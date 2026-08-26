# DSL Dashboard

A lightweight dashboard and data collector for monitoring multiple WE/TE
Data DSL accounts.

The collector reuses authenticated WE/TE Data sessions when possible and
retrieves current package and usage information through the portal's API
endpoints. If a saved session is rejected, the collector automatically
performs a fresh Playwright login, saves the replacement session, and
retries the API collection once.

Accounts can be collected individually or in batches. Existing data is
retained when an individual account fails, allowing the dashboard to
continue displaying the last successful result for that account.

## Features

-   Collect usage data from multiple WE/TE Data accounts
-   Reuse saved authenticated sessions to avoid unnecessary logins
-   Automatically refresh authentication when WE/TE Data rejects a saved
    session
-   Use Playwright for authentication and direct HTTP requests for usage
    collection
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
-   `requests`
-   Playwright
-   Chromium for Playwright
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

Each account contains:

-   `lnd_number` - WE/TE Data DSL service number
-   `lnd_pass` - account password
-   `apartments` - one or more apartments/areas served by the DSL
    account
-   `location` - physical location of the DSL modem

``` json
[
    {
        "lnd_number": "0123456789",
        "lnd_pass": "password-for-account-1",
        "apartments": [
            "Office"
        ],
        "location": "17 Closet"
    },
    {
        "lnd_number": "0123456790",
        "lnd_pass": "password-for-account-2",
        "apartments": [
            "Apt 2"
        ],
        "location": "17 Closet"
    },
    {
        "lnd_number": "0123456791",
        "lnd_pass": "password-for-account-3",
        "apartments": [
            "Apt 8",
            "Apt 4"
        ],
        "location": "17a Closet"
    },
    {
        "lnd_number": "0123456792",
        "lnd_pass": "password-for-account-4",
        "apartments": [
            "Apt 3"
        ],
        "location": "Apt 3"
    }
]
```

An account may serve more than one apartment. The collector builds the
human-friendly dashboard assignment from the `apartments` array. For
example, an account assigned to `Apt 8` and `Apt 4` is displayed as
`Apt 8 & 4`.

`location` describes the physical location of the DSL modem and is
independent of the apartment assignment.

In this example:

-   `1` selects `Office`
-   `2` selects `Apt 2`
-   `3` selects the account serving `Apt 8` and `Apt 4`
-   `1-3` runs the first three accounts in that order
-   `3-1` runs the first three accounts in reverse order
-   `Apt 4` selects the account whose assignment includes `Apt 4`

Keep `accounts.json` private because it contains account credentials.

## Authentication and Session Reuse

The collector maintains a separate reusable authentication session for
each DSL account.

Session files are stored in:

``` text
scripts/session_cache/
```

Each account's session file contains the authentication material needed
to make subsequent API requests, including the token, subscriber ID,
cookies, and the time the session was captured.

The normal collection flow is:

``` text
Load saved session
      |
      +-- no saved session --> Playwright login
      |
      +-- saved session --> try WE/TE Data API
                               |
                               +-- accepted --> collect usage
                               |
                               +-- rejected --> remove saved session
                                                    |
                                                    v
                                              Playwright login
                                                    |
                                                    v
                                             save new session
                                                    |
                                                    v
                                              collect usage
```

Playwright is therefore used primarily to establish authentication. Once
authenticated, the collector retrieves the subscribed offering and quota
data directly with HTTP requests.

Saved sessions are treated as an optimization, not a requirement. They
may expire or be invalidated by WE/TE Data. The collector does not
assume that a session will remain valid for any particular length of
time.

When WE/TE Data explicitly rejects a saved session, the collector:

1.  Removes the rejected cached session.
2.  Performs one fresh Playwright login.
3.  Saves the new authentication session.
4.  Retries the API collection once.

A freshly authenticated session that is immediately rejected is treated
as a collection failure. The collector does not repeatedly log in, which
helps avoid unnecessary authentication attempts.

Network errors, malformed responses, or other collection failures do not
automatically cause repeated browser logins.

No separate session keepalive process is required. Sessions are tested
naturally when each account is collected and refreshed when necessary.

### Session Security

Both of the following contain sensitive authentication information and
must remain private:

``` text
scripts/accounts.json
scripts/session_cache/
```

`accounts.json` contains account passwords. The session cache contains
reusable authentication material.

Neither should be committed to Git.

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

# Apartment assignment
python3 scripts/caller_script.py "Apt 2"
```

The default delay between accounts is 10 seconds. It can be changed with
`--delay`:

``` bash
python3 scripts/caller_script.py 1-5 --delay 20
```

For testing, a shorter delay can also be supplied explicitly:

``` bash
python3 scripts/caller_script.py 1-5 --delay 1
```

## Collection Behavior

Each selected account is processed independently.

For each account, `main.py` first attempts to use the account's cached
authentication session. If the session is accepted, quota collection
proceeds without a browser login. If WE/TE Data rejects the saved
session, the collector refreshes authentication once with Playwright and
retries using the new session.

When an account succeeds, its existing entry in the combined dataset is
replaced with newly collected data.

If an account fails:

-   its previous successful data is retained;
-   the failure is logged;
-   collection continues with the next selected account.

The global `Last Run` timestamp is updated when at least one account in
the batch succeeds. If every account in a batch fails, the previous
`Last Run` timestamp is retained.

## WE/TE Data Authentication Pacing

Repeated fresh authentication attempts against the WE/TE Data portal
appear to be subject to rate limiting or other request restrictions.

Testing has shown an apparent limit around 10 fresh logins within an
hour. This is observed portal behavior, not a documented WE/TE Data API
limit.

With session reuse, an account collection does not necessarily require a
login. Accounts with valid cached sessions can be collected directly
through the API. The pacing concern is therefore primarily the number of
**fresh Playwright authentication attempts**, rather than simply the
number of accounts collected.

For unattended operation, larger account sets can still be divided
around an hour boundary so that the system remains safe even if every
cached session has expired.

For example:

``` text
04:45    accounts 1-10
05:05    accounts 11-19
```

A later collection can reverse the order:

``` text
accounts 10-1
accounts 19-11
```

This prevents the same accounts from always being collected first.

The first run on a new installation, a cleared session cache, or a run
after many sessions have expired may require fresh authentication for
every selected account. Those situations should be scheduled with the
observed login restriction in mind.

Because the restriction is based on observed portal behavior rather than
a published limit, pacing may need adjustment if WE/TE Data changes its
authentication behavior.

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

Typical session-related messages include:

``` text
Trying saved session for 0123456789
Saved session accepted for 0123456789
```

or, when a cached session has expired:

``` text
Trying saved session for 0123456789
getSubscribedOfferings rejected saved session
Refreshing authentication for 0123456789
No usable saved session for 0123456789; logging in with Playwright
Saved reusable session for 0123456789
Fresh session accepted for 0123456789
```

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
temporary authentication, API, network, or portal failure does not
remove that account from the dashboard.
