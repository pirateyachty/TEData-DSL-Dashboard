import json
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / "scripts" / ".env")

CAIRO_TZ = ZoneInfo("Africa/Cairo")
USAGE_WARNING_PERCENT = 90
EXPIRY_WARNING_DAYS = 2

# Email configuration
SMTP_SERVER = os.getenv("EMAIL_SERVER")
SMTP_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL = os.getenv("EMAIL_USER")
PASSWORD = os.getenv("EMAIL_PASS")
TO_EMAIL = os.getenv("EMAIL_DEST")

# JSON file path
JSON_FILE = PROJECT_ROOT / "output" / "dsl_data.json"


def log(message):
    timestamp = datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"{timestamp} - {message}")


def send_email(subject, message):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL
        msg["To"] = TO_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(message, "plain"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL, PASSWORD)
            server.sendmail(EMAIL, TO_EMAIL, msg.as_string())

        log(f"Email sent: {subject}")
    except Exception as exc:
        log(f"Failed to send email: {exc}")


def check_accounts():
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        accounts = data.get("accounts", [])
        warnings = []

        for account in accounts:
            usage_prc = float(account["usagePrc"])
            days_until_exp = int(account["daysUntilExp"])

            if usage_prc >= USAGE_WARNING_PERCENT:
                warnings.append(
                    f"Account {account['lnd_number']} is at {usage_prc}% usage."
                )

            if days_until_exp <= EXPIRY_WARNING_DAYS:
                warnings.append(
                    f"Account {account['lnd_number']} is expiring in "
                    f"{days_until_exp} days."
                )

        if warnings:
            subject = "DSL Account Warning Notification"
            message = "\n".join(warnings)
            send_email(subject, message)
        else:
            log("All accounts are within safe limits. No email sent.")

    except Exception as exc:
        log(f"Failed to check accounts: {exc}")


if __name__ == "__main__":
    check_accounts()
