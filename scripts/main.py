import json
import sys
import os
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

if len(sys.argv) != 4:
    print("Usage: python main.py <lnd_number> <lnd_pass> <apartment>")
    sys.exit(1)

lnd_number = sys.argv[1]
lnd_pass = sys.argv[2]
apartment = sys.argv[3]

acctId = "FBB" + lnd_number[1:]
PROJECT_ROOT = Path(__file__).resolve().parent.parent
output_file = PROJECT_ROOT / "output" / "dsl_data.json"
LOGIN_URL = "https://my.te.eg/echannel/#/"


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


captured = {}


def handle_response(response):
    if "userAuthenticate" not in response.url:
        return
    try:
        data = response.json()
    except Exception:
        return
    ret_code = data.get("header", {}).get("retCode")
    if ret_code == "0":
        captured["auth"] = data.get("body", {})
    else:
        captured["auth_error"] = data.get("header", {})
        print(f"  Auth failed: {data.get('header', {})}")


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
            print("  WARNING: No Service Type options found")

    except Exception as e:
        print(f"  WARNING: Service Type selection failed: {e}")


def do_auth_via_browser(lnd_number, lnd_pass):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        page.on("response", handle_response)

        print(f"Logging in for {lnd_number}...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90000)

        page.wait_for_selector(
            "input[placeholder='Service number']",
            state="visible",
            timeout=20000
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
            timeout=10000
        )
        password_input = page.locator("input[placeholder='Password']")
        password_input.click()
        password_input.fill(lnd_pass)
        page.wait_for_timeout(500)

        login_button = page.get_by_role("button", name="Login")
        login_button.click()

        deadline = 20000
        interval = 300
        elapsed = 0
        while "auth" not in captured and "auth_error" not in captured and elapsed < deadline:
            page.wait_for_timeout(interval)
            elapsed += interval

        browser_cookies = context.cookies()
        cookies_dict = {c["name"]: c["value"] for c in browser_cookies}

        browser.close()

    return cookies_dict


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


# ── Step 1: Browser-based auth ─────────────────────────────────────────────────

cookies_dict = do_auth_via_browser(lnd_number, lnd_pass)

if "auth_error" in captured:
    print(f"Authentication failed for {lnd_number}: {captured['auth_error']}")
    sys.exit(1)

if "auth" not in captured:
    print(f"No auth response captured for {lnd_number} within timeout")
    sys.exit(1)

auth_body = captured["auth"]
csrf_token = auth_body["token"]
sub_id = auth_body["subscriber"]["subscriberId"]
headers = build_headers(csrf_token)

# ── Step 2: getSubscribedOfferings via requests ────────────────────────────────

with requests.Session() as session:
    session.cookies.update(cookies_dict)

    offerings_resp = session.post(
        "https://my.te.eg/echannel/service/besapp/base/rest/busiservice/"
        "cz/v1/auth/getSubscribedOfferings",
        headers=headers,
        json={"msisdn": acctId, "numberServiceType": "FBB", "groupId": ""},
    )
    offerings_data = offerings_resp.json()

    if offerings_data["header"]["retCode"] != "0":
        print(f"getSubscribedOfferings failed: {offerings_data['header']}")
        sys.exit(1)

    offer_id = offerings_data["body"]["offeringList"][0]["mainOfferingId"]

    # ── Step 3: queryFreeUnit ──────────────────────────────────────────────────

    quota_resp = session.post(
        "https://my.te.eg/echannel/service/besapp/base/rest/busiservice/"
        "cz/cbs/bb/queryFreeUnit",
        headers=headers,
        json={"subscriberId": sub_id, "mainOfferId": offer_id},
    )
    quota_data = quota_resp.json()

    if quota_data["header"]["retCode"] != "0" or len(quota_data["body"]) == 0:
        print(f"queryFreeUnit failed: {quota_data['header']}")
        sys.exit(1)

    q = quota_data["body"][0]

# ── Step 4: Extract fields and write JSON ──────────────────────────────────────

offerName    = q["offerName"]
totalGB      = q["total"]
usedGB       = q["used"]
remainGB     = q["remain"]
usagePrc     = round((usedGB / totalGB) * 100, 2)
renewedDate  = tsConv(q["effectiveTime"])[0]
expiryDate   = tsConv(q["expireTime"], returnUntil=True)
expDate      = expiryDate[0]
daysUntilExp = expiryDate[1][3:-5]

if usagePrc <= 49:
    bar_color, back_color = "#6BA368", "#1a1a1a"
elif usagePrc <= 74:
    bar_color, back_color = "#D1B26F", "#1a1a1a"
else:
    bar_color, back_color = "#B35A5A", "#B35a5A"

dsl_data_entry = {
    "lnd_number":      str(lnd_number).strip(),
    "ApartmentNumber": apartment,
    "offerName":       str(offerName).strip(),
    "remainGB":        str(remainGB).strip(),
    "totalGB":         str(totalGB).strip(),
    "usedGB":          str(usedGB).strip(),
    "usagePrc":        str(usagePrc).strip(),
    "renewedDate":     str(renewedDate).strip(),
    "expDate":         str(expDate).strip(),
    "daysUntilExp":    str(daysUntilExp).strip(),
    "lastUpdated":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
        f" width: {usagePrc}%; height: 100%; position: absolute;'></div>"
        f"<span style='position: absolute; left: 50%; transform: translateX(-50%);"
        f" font-size: 11pt; line-height: 17px; white-space: nowrap; color: white;'>"
        f"{usagePrc}% - ({daysUntilExp} days left)"
        f"</span></div>"
    ),
}

if os.path.exists(output_file):
    with open(output_file, "r") as f:
        try:
            existing_data = json.load(f)
        except json.JSONDecodeError:
            existing_data = []
else:
    existing_data = []

existing_data.append(dsl_data_entry)

with open(output_file, "w") as f:
    json.dump(existing_data, f, indent=4)

print(f"Data for {lnd_number} saved to {output_file}")
