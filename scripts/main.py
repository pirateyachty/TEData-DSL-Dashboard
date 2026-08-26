import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright


if len(sys.argv) != 4:
    print("Usage: python main.py <lnd_number> <lnd_pass> <apartment>")
    sys.exit(1)

lnd_number = sys.argv[1]
lnd_pass = sys.argv[2]
apartment = sys.argv[3]

acct_id = "FBB" + lnd_number[1:]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
SESSION_DIR = SCRIPT_DIR / "session_cache"
SESSION_FILE = SESSION_DIR / f"{lnd_number}.json"

output_file = Path(
    os.environ.get(
        "DSL_OUTPUT_FILE",
        PROJECT_ROOT / "output" / "dsl_data.json",
    )
)

LOGIN_URL = "https://my.te.eg/echannel/#/"
OFFERINGS_URL = (
    "https://my.te.eg/echannel/service/besapp/base/rest/busiservice/"
    "cz/v1/auth/getSubscribedOfferings"
)
QUOTA_URL = (
    "https://my.te.eg/echannel/service/besapp/base/rest/busiservice/"
    "cz/cbs/bb/queryFreeUnit"
)

REQUEST_TIMEOUT = 30


class SessionRejected(Exception):
    """The saved WE session was rejected and should be refreshed."""


class CollectionError(Exception):
    """The quota collection failed for a reason other than an expired session."""


def tsConv(unix_timestamp, returnUntil=False):
    dt_utc = datetime.fromtimestamp(unix_timestamp / 1000.0, tz=timezone.utc)
    local_tz = datetime.now().astimezone().tzinfo
    dt_local = dt_utc.astimezone(local_tz)
    formatted_date = dt_local.strftime("%d/%m/%Y at %I:%M %p")
    dates = [formatted_date]

    if returnUntil:
        now_local = datetime.now().astimezone(local_tz)
        time_difference = dt_local - now_local

        if time_difference <= timedelta(days=1):
            hours_left = int(time_difference.total_seconds() // 3600)
            dates.append(f"in {hours_left} hours")
        else:
            days_left = time_difference.days
            dates.append(f"in {days_left} days")

    return dates


def build_headers(csrf_token):
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Origin": "https://my.te.eg",
        "Referer": "https://my.te.eg/echannel/",
        "channelId": "702",
        "csrftoken": csrf_token,
        "delegatorSubsId": "",
        "isCoporate": "false",
        "isMobile": "false",
        "isSelfcare": "true",
        "languageCode": "en-US",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    }


def load_saved_session():
    """Return this account's saved auth material, or None if unavailable/corrupt."""
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Saved session for {lnd_number} is unreadable; re-authenticating: {exc}")
        return None

    if not isinstance(saved, dict):
        return None

    if not saved.get("token") or not saved.get("subscriberId"):
        return None

    if not isinstance(saved.get("cookies"), dict):
        return None

    return saved


def save_session(auth_body, cookies_dict):
    """
    Atomically save one account's reusable authentication material.

    A separate file per DSL account avoids multiple collector processes having
    to rewrite one shared sessions.json file.
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    data = {
        "token": auth_body["token"],
        "subscriberId": auth_body["subscriber"]["subscriberId"],
        "cookies": cookies_dict,
        "capturedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    temp_file = SESSION_FILE.with_suffix(".json.tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    os.replace(temp_file, SESSION_FILE)
    print(f"Saved reusable session for {lnd_number}")


def delete_saved_session():
    try:
        SESSION_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"WARNING: could not remove rejected session for {lnd_number}: {exc}")


def select_service_type(page):
    try:
        selector = page.locator(".ant-select-selector").first
        selector.wait_for(state="visible", timeout=5000)
        selector.click()
        page.wait_for_timeout(800)

        for label in ["Internet", "FBB", "Landline Internet", "ADSL", "Fiber"]:
            option = page.locator(".ant-select-item-option").filter(has_text=label)
            if option.count() > 0 and option.first.is_visible():
                option.first.click()
                return

        options = page.locator(".ant-select-item-option")
        if options.count() > 0:
            options.first.click()
        else:
            print("WARNING: No Service Type options found")

    except Exception as exc:
        print(f"WARNING: Service Type selection failed: {exc}")


def authenticate_via_browser():
    """
    Use the current working WE web flow only to obtain authentication material.

    The browser is closed immediately after successful authentication; quota
    collection itself is performed with requests.
    """
    captured = {}

    def handle_response(response):
        if "userAuthenticate" not in response.url:
            return

        try:
            data = response.json()
        except Exception:
            return

        ret_code = str(data.get("header", {}).get("retCode", ""))

        if ret_code == "0":
            captured["auth"] = data.get("body", {})
        else:
            captured["auth_error"] = data.get("header", {})

    print(f"No usable saved session for {lnd_number}; logging in with Playwright...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.on("response", handle_response)

        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90000)

            page.wait_for_selector(
                "input[placeholder='Service number']",
                state="visible",
                timeout=20000,
            )
            page.wait_for_timeout(1000)

            number_input = page.locator("input[placeholder='Service number']")
            number_input.click()
            number_input.fill(lnd_number)

            page.wait_for_timeout(1200)
            select_service_type(page)
            page.wait_for_timeout(500)

            page.wait_for_selector(
                "input[placeholder='Password']",
                state="visible",
                timeout=10000,
            )
            password_input = page.locator("input[placeholder='Password']")
            password_input.click()
            password_input.fill(lnd_pass)
            page.wait_for_timeout(500)

            page.get_by_role("button", name="Login").click()

            deadline_ms = 20000
            interval_ms = 300
            elapsed_ms = 0

            while (
                "auth" not in captured
                and "auth_error" not in captured
                and elapsed_ms < deadline_ms
            ):
                page.wait_for_timeout(interval_ms)
                elapsed_ms += interval_ms

            cookies_dict = {
                cookie["name"]: cookie["value"]
                for cookie in context.cookies()
            }

        finally:
            browser.close()

    if "auth_error" in captured:
        raise CollectionError(
            f"Authentication failed for {lnd_number}: {captured['auth_error']}"
        )

    if "auth" not in captured:
        raise CollectionError(
            f"No auth response captured for {lnd_number} within timeout"
        )

    auth_body = captured["auth"]

    if (
        not auth_body.get("token")
        or not auth_body.get("subscriber", {}).get("subscriberId")
    ):
        raise CollectionError(
            f"Authentication response for {lnd_number} was missing token/subscriberId"
        )

    save_session(auth_body, cookies_dict)

    return {
        "token": auth_body["token"],
        "subscriberId": auth_body["subscriber"]["subscriberId"],
        "cookies": cookies_dict,
        "capturedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def response_json(response, step):
    try:
        return response.json()
    except ValueError as exc:
        preview = response.text[:500]
        raise CollectionError(
            f"{step} returned non-JSON HTTP {response.status_code}: {preview!r}"
        ) from exc


def pull_quota(saved):
    """
    Pull fresh usage data using an already authenticated WE session.

    A non-zero WE retCode is treated as a rejected/invalid session so the caller
    can refresh authentication once and retry. Network/JSON/shape errors remain
    normal collection failures rather than forcing repeated browser logins.
    """
    headers = build_headers(saved["token"])

    with requests.Session() as session:
        session.cookies.update(saved["cookies"])

        try:
            offerings_resp = session.post(
                OFFERINGS_URL,
                headers=headers,
                json={
                    "msisdn": acct_id,
                    "numberServiceType": "FBB",
                    "groupId": "",
                },
                timeout=REQUEST_TIMEOUT,
            )
            offerings_resp.raise_for_status()
        except requests.RequestException as exc:
            raise CollectionError(
                f"getSubscribedOfferings network/HTTP failure: {exc}"
            ) from exc

        offerings_data = response_json(
            offerings_resp,
            "getSubscribedOfferings",
        )
        offerings_header = offerings_data.get("header", {})
        offerings_ret = str(offerings_header.get("retCode", ""))

        if offerings_ret != "0":
            raise SessionRejected(
                f"getSubscribedOfferings rejected saved session: {offerings_header}"
            )

        offering_list = offerings_data.get("body", {}).get("offeringList") or []
        if not offering_list:
            raise CollectionError("getSubscribedOfferings returned no offerings")

        offer_id = offering_list[0].get("mainOfferingId")
        if not offer_id:
            raise CollectionError("Subscribed offering had no mainOfferingId")

        try:
            quota_resp = session.post(
                QUOTA_URL,
                headers=headers,
                json={
                    "subscriberId": saved["subscriberId"],
                    "mainOfferId": offer_id,
                },
                timeout=REQUEST_TIMEOUT,
            )
            quota_resp.raise_for_status()
        except requests.RequestException as exc:
            raise CollectionError(
                f"queryFreeUnit network/HTTP failure: {exc}"
            ) from exc

        quota_data = response_json(quota_resp, "queryFreeUnit")
        quota_header = quota_data.get("header", {})
        quota_ret = str(quota_header.get("retCode", ""))

        if quota_ret != "0":
            raise SessionRejected(
                f"queryFreeUnit rejected saved session: {quota_header}"
            )

        quota_rows = quota_data.get("body") or []
        if not quota_rows:
            raise CollectionError("queryFreeUnit returned no quota rows")

        return quota_rows[0]


def collect_quota():
    """
    API first:
      1. Try the account's saved session.
      2. If WE rejects it, remove it and authenticate once with Playwright.
      3. Retry the API pull once with the new session.
    """
    saved = load_saved_session()

    if saved is not None:
        captured_at = saved.get("capturedAt", "unknown")
        print(f"Trying saved session for {lnd_number} (captured {captured_at})")

        try:
            quota = pull_quota(saved)
            print(f"Saved session accepted for {lnd_number}")
            return quota
        except SessionRejected as exc:
            print(f"{exc}")
            print(f"Refreshing authentication for {lnd_number}...")
            delete_saved_session()

    fresh = authenticate_via_browser()

    try:
        quota = pull_quota(fresh)
    except SessionRejected as exc:
        # A freshly authenticated session should not immediately be rejected.
        # Do not loop and generate repeated authentication attempts.
        delete_saved_session()
        raise CollectionError(
            f"Fresh session for {lnd_number} was immediately rejected: {exc}"
        ) from exc

    print(f"Fresh session accepted for {lnd_number}")
    return quota


def make_dsl_data_entry(q):
    offer_name = q["offerName"]
    total_gb = q["total"]
    used_gb = q["used"]
    remain_gb = q["remain"]

    usage_prc = round((used_gb / total_gb) * 100, 2)

    renewed_date = tsConv(q["effectiveTime"])[0]
    expiry_date = tsConv(q["expireTime"], returnUntil=True)
    exp_date = expiry_date[0]
    days_until_exp = expiry_date[1][3:-5]

    if usage_prc <= 49:
        bar_color, back_color = "#6BA368", "#1a1a1a"
    elif usage_prc <= 74:
        bar_color, back_color = "#D1B26F", "#1a1a1a"
    else:
        bar_color, back_color = "#B35A5A", "#B35a5A"

    return {
        "lnd_number": str(lnd_number).strip(),
        "ApartmentNumber": apartment,
        "offerName": str(offer_name).strip(),
        "remainGB": str(remain_gb).strip(),
        "totalGB": str(total_gb).strip(),
        "usedGB": str(used_gb).strip(),
        "usagePrc": str(usage_prc).strip(),
        "renewedDate": str(renewed_date).strip(),
        "expDate": str(exp_date).strip(),
        "daysUntilExp": str(days_until_exp).strip(),
        "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "display_text": (
            f"<div style='border-radius: 6px 6px 0 0; width: 100%; height: 27px;"
            f" background-color: {back_color}; position: relative;'>"
            f"<span style='position: absolute; top: 0; left: 50%;"
            f" transform: translateX(-50%); font-size: 13pt; line-height: 27px; color: white;'>"
            f"{lnd_number[3:]}"
            f"</span></div>"
            f"<div style='border-radius: 0 0 6px 6px; width: 100%; height: 17px;"
            f" background-color: {back_color}; position: relative;'>"
            f"<div style='border-radius: 3px; background-color: {bar_color};"
            f" width: {usage_prc}%; height: 100%; position: absolute;'></div>"
            f"<span style='position: absolute; left: 50%; transform: translateX(-50%);"
            f" font-size: 11pt; line-height: 17px; white-space: nowrap; color: white;'>"
            f"{usage_prc}% - ({days_until_exp} days left)"
            f"</span></div>"
        ),
    }


def write_worker_output(entry):
    """
    Preserve the existing main.py contract.

    caller_script.py points DSL_OUTPUT_FILE at a per-run scratch file, so this
    worker writes only the fresh one-account result there. caller_script.py
    validates/merges it and atomically publishes the live dsl_data.json.
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump([entry], f, indent=4)
        f.flush()
        os.fsync(f.fileno())


def main():
    try:
        quota = collect_quota()
        entry = make_dsl_data_entry(quota)
        write_worker_output(entry)
    except (CollectionError, KeyError, TypeError, ZeroDivisionError) as exc:
        print(f"FAILED {lnd_number}: {exc}")
        raise SystemExit(1)

    print(f"Data for {lnd_number} saved to {output_file}")


if __name__ == "__main__":
    main()
