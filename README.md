# DSL Dashboard

A lightweight dashboard and data collector for monitoring multiple WE/TE
Data DSL accounts.

The collector logs into each configured account, retrieves current
package and usage information, and maintains a combined JSON dataset for
the web dashboard.

Accounts can be collected individually or in batches. Existing data is
retained when an individual account fails, allowing the dashboard to
continue displaying the last successful result for that account.

## Features

-   Collect usage data from multiple WE/TE Data accounts
-   Select individual accounts, ranges, reverse ranges, or arbitrary
    groups
-   Preserve the last successful data when an account fails
-   Continue processing the remaining accounts after an individual
    failure
-   Per-account `lastUpdated` timestamps
-   Global `Last Run` timestamp
-   Simple HTML dashboard
-   Optional email monitoring for high usage or accounts approaching
    renewal
-   Designed for unattended operation with systemd

## Requirements

-   Python 3
-   Playwright / Chromium
-   Internet access to the WE/TE Data customer portal

Install the Python dependencies:

``` bash
pip3 install -r requirements.txt
```

Install the Chromium browser used by Playwright:

``` bash
python3 -m playwright install chromium
```

## Account Configuration

Create `scripts/accounts.json`.

The accounts are stored as a JSON array. Their order determines the
numeric selectors used by `caller_script.py`.

``` json
[
    {
        "lnd_number": "0123456789",
        "lnd_pass": "password-for-account-1",
        "apartment": "Office"
    },
    {
        "lnd_number": "0123456790",
        "lnd_pass": "password-for-account-2",
        "apartment": "Apartment 1"
    },
    {
        "lnd_number": "0123456791",
        "lnd_pass": "password-for-account-3",
        "apartment": "Apartment 2"
    }
]
```

In this example:

-   `1` selects `Office`
-   `2` selects `Apartment 1`
-   `3` selects `Apartment 2`
-   `1-3` runs all three in that order
-   `3-1` runs all three in reverse order

Keep `accounts.json` private because it contains account credentials.

## Running the Collector

General syntax:

``` bash
python3 scripts/caller_script.py SELECTOR
```

Examples:

``` bash
# Single account
python3 scripts/caller_script.py 5

# Forward range
python3 scripts/caller_script.py 1-10

# Reverse range
python3 scripts/caller_script.py 10-1

# Multiple accounts
python3 scripts/caller_script.py 1,3,7

# Mixed ranges
python3 scripts/caller_script.py 1-5,8-10

# All accounts
python3 scripts/caller_script.py all

# WE/TE Data service number
python3 scripts/caller_script.py 0123456789

# Exact apartment name
python3 scripts/caller_script.py "Apartment 1"
```

The default delay between account requests is 10 seconds. It can be
changed with:

``` bash
python3 scripts/caller_script.py 1-5 --delay 20
```

## Collection Behavior

Each selected account is processed independently.

When an account succeeds, its existing entry in the combined dataset is
replaced with newly collected data.

If an account fails:

-   its previous successful data is retained;
-   the failure is logged;
-   collection continues with the next selected account.

The global `Last Run` timestamp is updated when at least one account in
the batch succeeds. If every account in a batch fails, the previous
`Last Run` timestamp is retained.

## WE/TE Data Request Pacing

Repeated authentication attempts against the WE/TE Data portal appear to
be subject to rate limiting or other request restrictions.

In testing, reliable operation was achieved by limiting collection to
**10 or fewer accounts in a batch** and splitting larger collections
across an hour boundary.

For example:

``` text
04:50    accounts 1-10
05:10    accounts 11-19
```

The exact schedule is not important. The intent is to avoid sending all
account authentication requests in one continuous burst.

For a second daily collection, the order can also be reversed:

``` text
accounts 10-1
accounts 19-11
```

This prevents the same accounts from always being collected first.

These limits are based on observed portal behavior rather than a
documented WE/TE Data API limit, so they may need adjustment for
different deployments.

## systemd

For unattended operation, a templated systemd service can pass the
account selector to `caller_script.py`.

``` ini
[Unit]
Description=DSL Dashboard Collector (%i)
Wants=network-online.target
After=network-online.target

[Service]
User=YOUR_USER
Type=oneshot
ExecStart=/usr/bin/python3 /opt/dsl_dashboard/scripts/caller_script.py %i
WorkingDirectory=/opt/dsl_dashboard
StandardOutput=append:/opt/dsl_dashboard/logs/caller_script.log
StandardError=append:/opt/dsl_dashboard/logs/caller_script.log
```

Save it as `/etc/systemd/system/caller_script@.service`, then reload
systemd:

``` bash
sudo systemctl daemon-reload
```

The selector becomes the systemd instance name:

``` bash
sudo systemctl start caller_script@1.service
sudo systemctl start caller_script@1-10.service
```

Different systemd timers can therefore invoke different account groups
without requiring separate collector scripts.

## Logging

When run manually, the collector prints timestamped status information
directly to the terminal.

When using the example systemd service, output is appended to:

``` text
/opt/dsl_dashboard/logs/caller_script.log
```

Watch it live with:

``` bash
tail -f /opt/dsl_dashboard/logs/caller_script.log
```

## Usage Monitoring

`monitor.py` can optionally check the collected account data and send an
email notification when an account enters a warning condition.

The monitor is independent of the collector and can be scheduled
separately---for example, once per day after the morning collection has
completed.

## Web Dashboard

The web interface reads the generated DSL account data and displays
current package usage, remaining data, renewal information, and the last
successful update time for each account.

Because individual account data is preserved when a collection fails, a
temporary login or portal failure does not remove that account from the
dashboard.
