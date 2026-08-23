import argparse
import datetime
import json
import os
import signal
import subprocess
import time

import pytz

local_timezone = pytz.timezone("Africa/Cairo")

# Paths (portable between the local checkout and /opt/dsl_dashboard)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
ACCOUNTS_FILE_PATH = os.path.join(SCRIPT_DIR, "accounts.json")
MAIN_SCRIPT_PATH = os.path.join(SCRIPT_DIR, "main.py")
JSON_FILE_PATH = os.path.join(PROJECT_ROOT, "output", "dsl_data.json")
TEMP_JSON_FILE_PATH = os.path.join(
    PROJECT_ROOT, "output", f".dsl_account_{os.getpid()}.json"
)

DEFAULT_DELAY = 10
ACCOUNT_TIMEOUT = 120  # Hard limit for one main.py scrape, in seconds.


def format_assignment(account):
    """Build the existing human-friendly dashboard label from structured assignments."""
    apartments = account.get("apartments", [])
    if not apartments:
        return account.get("lnd_number", "Unknown")

    if len(apartments) == 1:
        label = apartments[0]
    else:
        first = apartments[0]
        rest = [
            item[4:] if item.startswith("Apt ") else item
            for item in apartments[1:]
        ]
        label = first + " & " + " & ".join(rest)

    # Preserve the useful "(in apt)" dashboard cue when the modem is physically
    # located in one of the apartments it serves.
    if account.get("location") in apartments and account.get("location", "").startswith("Apt "):
        label += " (in apt)"

    return label


def validate_account_config(account, index):
    """Validate the structured accounts.json schema."""
    if not isinstance(account, dict):
        raise SystemExit(f"Account entry {index} is not a JSON object.")

    for key in ("lnd_number", "lnd_pass", "location"):
        value = account.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SystemExit(f"Account entry {index} has invalid or missing {key!r}.")

    apartments = account.get("apartments")
    if (
        not isinstance(apartments, list)
        or not apartments
        or any(not isinstance(item, str) or not item.strip() for item in apartments)
    ):
        raise SystemExit(
            f"Account {account.get('lnd_number', index)} must have a non-empty "
            "'apartments' list."
        )

def log(message, blank_before=False):
    timestamp = datetime.datetime.now(local_timezone).strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )
    if blank_before:
        print("", flush=True)
    print(f"{timestamp} - {message}", flush=True)

def load_accounts():
    """Load private account configuration from accounts.json."""
    try:
        with open(ACCOUNTS_FILE_PATH, "r") as file:
            accounts = json.load(file)
    except FileNotFoundError:
        raise SystemExit(f"Account file not found: {ACCOUNTS_FILE_PATH}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {ACCOUNTS_FILE_PATH}: {exc}")

    if not isinstance(accounts, list) or not accounts:
        raise SystemExit(f"No accounts found in {ACCOUNTS_FILE_PATH}")

    for index, account in enumerate(accounts, start=1):
        validate_account_config(account, index)

    return accounts


def select_account(accounts, selector):
    """Select one account by 1-based list number, DSL number, or apartment name."""
    selector = selector.strip()

    if selector.isdigit():
        index = int(selector)
        if 1 <= index <= len(accounts):
            return accounts[index - 1]

    for account in accounts:
        if account.get("lnd_number") == selector:
            return account

    matches = [
        account
        for account in accounts
        if (
            format_assignment(account).casefold() == selector.casefold()
            or any(
                apartment.casefold() == selector.casefold()
                for apartment in account.get("apartments", [])
            )
        )
    ]
    if len(matches) == 1:
        return matches[0]

    raise ValueError(
        f"No unique account matched {selector!r}. "
        f"Use an account number 1-{len(accounts)}, the DSL number, "
        "or an apartment/assignment name."
    )


def parse_selector(accounts, selector):
    """
    Expand a selector into an ordered list of accounts.

    Supported examples:
      5
      1-10
      10-1
      1,3,7,12
      1-5,9,12-10
      all
      <DSL number>
      <exact apartment name>
    """
    selector = selector.strip()

    if not selector:
        raise SystemExit("Account selector cannot be empty.")

    if selector.casefold() == "all":
        return list(accounts)

    selected = []

    # Commas are intended for combining account numbers/ranges. A single
    # non-comma selector can still be a DSL number or exact apartment name.
    parts = [part.strip() for part in selector.split(",")]

    for part in parts:
        if not part:
            raise SystemExit(f"Invalid empty selector in {selector!r}")

        if part.casefold() == "all":
            selected.extend(accounts)
            continue

        # Numeric range, including reverse ranges such as 19-11.
        if "-" in part:
            pieces = part.split("-")
            if (
                len(pieces) == 2
                and pieces[0].strip().isdigit()
                and pieces[1].strip().isdigit()
            ):
                start = int(pieces[0])
                end = int(pieces[1])

                if not (1 <= start <= len(accounts)):
                    raise SystemExit(
                        f"Range start {start} is outside 1-{len(accounts)}."
                    )
                if not (1 <= end <= len(accounts)):
                    raise SystemExit(
                        f"Range end {end} is outside 1-{len(accounts)}."
                    )

                step = 1 if end >= start else -1
                selected.extend(
                    accounts[index - 1]
                    for index in range(start, end + step, step)
                )
                continue

        try:
            selected.append(select_account(accounts, part))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    # Remove accidental duplicates while preserving requested order.
    unique = []
    seen = set()
    for account in selected:
        key = account.get("lnd_number")
        if key not in seen:
            unique.append(account)
            seen.add(key)

    if not unique:
        raise SystemExit("No accounts selected.")

    return unique


def load_existing_data():
    """Load the current dashboard dataset, accepting old wrapped/unwrapped formats."""
    if not os.path.exists(JSON_FILE_PATH):
        return {"accounts": []}

    try:
        with open(JSON_FILE_PATH, "r") as file:
            loaded = json.load(file)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Cannot merge into invalid {JSON_FILE_PATH}: {exc}")

    if isinstance(loaded, dict) and isinstance(loaded.get("accounts"), list):
        # Ignore the legacy top-level "timestamp" field. Account-level
        # lastUpdated values are the authoritative freshness indicators.
        return {"accounts": loaded["accounts"]}

    if isinstance(loaded, list):
        # Backward compatibility with an unwrapped scraper result.
        return {"accounts": loaded}

    raise SystemExit(f"Unexpected structure in {JSON_FILE_PATH}")


def write_dataset(data):
    """Atomically publish the dashboard dataset."""
    output_dir = os.path.dirname(JSON_FILE_PATH)
    os.makedirs(output_dir, exist_ok=True)

    temp_path = f"{JSON_FILE_PATH}.tmp"
    with open(temp_path, "w") as file:
        json.dump(data, file, indent=4)
        file.flush()
        os.fsync(file.fileno())

    os.replace(temp_path, JSON_FILE_PATH)


def merge_account(existing_data, fresh_account):
    """Replace one account in the current dataset while retaining all others."""
    lnd_number = fresh_account.get("lnd_number")

    merged_accounts = []
    replaced = False

    for item in existing_data.get("accounts", []):
        if item.get("lnd_number") == lnd_number:
            merged_accounts.append(fresh_account)
            replaced = True
        else:
            merged_accounts.append(item)

    if not replaced:
        merged_accounts.append(fresh_account)

    return {
        "accounts": merged_accounts,
    }


def run_account(account, current_data):
    """
    Run main.py for one account.

    main.py writes the fresh one-account scrape to a scratch file. The live
    dsl_data.json is left untouched until a successful scrape has been
    validated, merged, and atomically published.

    On success, return (updated_data, True).
    On failure, leave the live dataset untouched and return
    (current_data, False).
    """
    lnd_number = account["lnd_number"]
    lnd_pass = account["lnd_pass"]
    apartment = format_assignment(account)

    log(f"Running account: {lnd_number} ({apartment})")

    os.makedirs(os.path.dirname(TEMP_JSON_FILE_PATH), exist_ok=True)

    # Remove only a stale scratch file from this caller process. Never remove
    # the live dsl_data.json before a scrape.
    if os.path.exists(TEMP_JSON_FILE_PATH):
        os.remove(TEMP_JSON_FILE_PATH)

    env = os.environ.copy()
    env["DSL_OUTPUT_FILE"] = TEMP_JSON_FILE_PATH

    process = None
    try:
        # Start each scrape in its own process group. If it hangs, this lets us
        # terminate main.py together with any Playwright/browser child processes.
        process = subprocess.Popen(
            ["python3", "-u", MAIN_SCRIPT_PATH, lnd_number, lnd_pass, apartment],
            env=env,
            start_new_session=True,
        )
        return_code = process.wait(timeout=ACCOUNT_TIMEOUT)

        if return_code != 0:
            log(
                f"FAILED {lnd_number} ({apartment}): exit code {return_code}. "
                "Previous account data retained."
            )
            return current_data, False

    except subprocess.TimeoutExpired:
        log(
            f"FAILED {lnd_number} ({apartment}): timed out after "
            f"{ACCOUNT_TIMEOUT} seconds. Terminating scrape; previous account "
            "data retained."
        )

        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

        return current_data, False

    except OSError as exc:
        log(
            f"FAILED {lnd_number} ({apartment}): could not run main.py: {exc}. "
            "Previous account data retained."
        )
        return current_data, False

    if not os.path.exists(TEMP_JSON_FILE_PATH):
        log(
            f"FAILED {lnd_number} ({apartment}): main.py did not create "
            f"{TEMP_JSON_FILE_PATH}. Previous account data retained."
        )
        return current_data, False

    try:
        with open(TEMP_JSON_FILE_PATH, "r") as file:
            fresh_result = json.load(file)
    except (json.JSONDecodeError, OSError) as exc:
        log(
            f"FAILED {lnd_number} ({apartment}): could not read valid JSON "
            f"from main.py: {exc}. Previous account data retained."
        )
        return current_data, False
    finally:
        try:
            os.remove(TEMP_JSON_FILE_PATH)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log(f"WARNING: could not remove scratch file {TEMP_JSON_FILE_PATH}: {exc}")

    if isinstance(fresh_result, dict) and isinstance(fresh_result.get("accounts"), list):
        fresh_accounts = fresh_result["accounts"]
    elif isinstance(fresh_result, list):
        fresh_accounts = fresh_result
    else:
        log(
            f"FAILED {lnd_number} ({apartment}): unexpected JSON structure. "
            "Previous account data retained."
        )
        return current_data, False

    fresh_account = next(
        (item for item in fresh_accounts if item.get("lnd_number") == lnd_number),
        None,
    )

    if fresh_account is None:
        log(
            f"FAILED {lnd_number} ({apartment}): fresh result did not contain "
            "the requested account. Previous account data retained."
        )
        return current_data, False

    updated_data = merge_account(current_data, fresh_account)
    write_dataset(updated_data)

    log(
        f"SUCCESS {lnd_number} ({apartment}); "
        f"{len(updated_data['accounts'])} account(s) retained."
    )

    return updated_data, True

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Run the DSL collector for one or more accounts from accounts.json."
        ),
        epilog=(
            "Examples: 5 | 1-10 | 10-1 | 1,3,7,12 | 1-5,9,12-10 | all"
        ),
    )
    parser.add_argument(
        "account",
        help=(
            "Account selector: number, range, reverse range, comma-separated "
            "selection, 'all', DSL number, or exact apartment name"
        ),
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=DEFAULT_DELAY,
        help=(
            f"Seconds to wait between selected accounts "
            f"(default: {DEFAULT_DELAY})"
        ),
    )
    args = parser.parse_args()

    if args.delay < 0:
        raise SystemExit("--delay cannot be negative.")

    accounts = load_accounts()
    selected_accounts = parse_selector(accounts, args.account)
    current_data = load_existing_data()

    log("*" * 80, blank_before=True)
    log(
        f"Starting DSL dashboard collection for {len(selected_accounts)} "
        f"account(s): {args.account}"
    )

    successful = 0
    failed = 0

    for position, account in enumerate(selected_accounts, start=1):
        log(
            f"\nStarting account {position}/{len(selected_accounts)}: "
            f"{account['lnd_number']} ({format_assignment(account)})"
        )

        current_data, ok = run_account(account, current_data)

        if ok:
            successful += 1
        else:
            failed += 1

        if position < len(selected_accounts) and args.delay:
            log(
                f"Waiting {args.delay} seconds before the next account..."
            )
            time.sleep(args.delay)

    # Publish the merged account dataset. Freshness/status is derived by the
    # webpage from each account's lastUpdated value rather than a batch timestamp.
    write_dataset(current_data)

    log(
        f"\nBatch complete: {successful} successful, {failed} failed."
    )

    # Return a failure exit code if any selected account failed. This is useful
    # to systemd/log monitoring while still allowing the entire batch to run.
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
