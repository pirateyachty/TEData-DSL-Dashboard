import os
import json
import smtplib
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from dotenv import load_dotenv
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / "scripts" / ".env")

# Email configuration
SMTP_SERVER = os.getenv("EMAIL_SERVER") # Or your email server (e.g., smtp.mail.yahoo.com)
SMTP_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL = os.getenv("EMAIL_USER")  # Your email address
PASSWORD = os.getenv("EMAIL_PASS")  # Your email password
TO_EMAIL = os.getenv("EMAIL_DEST")  # Where to send the notification

# JSON file path
JSON_FILE = PROJECT_ROOT / "output" / "dsl_data.json"

# Function to send email
def send_email(subject, message):
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL, PASSWORD)
        
        msg = MIMEMultipart()
        msg['From'] = EMAIL
        msg['To'] = TO_EMAIL
        msg['Subject'] = subject
        
        msg.attach(MIMEText(message, 'plain'))
        server.sendmail(EMAIL, TO_EMAIL, msg.as_string())
        
        server.quit()
        print(f"Email sent: {subject}")
    except Exception as e:
        print(f"Failed to send email: {e}")

# Function to check the accounts
def check_accounts():
    try:
        with open(JSON_FILE, 'r') as file:
            data = json.load(file)

        accounts = data.get("accounts", [])  # Extract accounts from the JSON structure
        warnings = []

        for account in accounts:
            usagePrc = float(account['usagePrc'])
            days_until_exp = int(account['daysUntilExp'])  # Now it's a number as a string

            # Check usage percentage
            if usagePrc >= 90:
                warnings.append(f"Account {account['lnd_number']} is at {usagePrc}% usage.")

            # Check days left until expiration
            if days_until_exp <= 2:
                warnings.append(f"Account {account['lnd_number']} is expiring in {days_until_exp} days.")

        # Only send an email if there are warnings
        if warnings:
            subject = "DSL Account Warning Notification"
            message = "\n".join(warnings)
            send_email(subject, message)
        else:
            print("All accounts are within safe limits. No email sent.")
        
    except Exception as e:
        print(f"Failed to check accounts: {e}")

if __name__ == "__main__":
    check_accounts()  # Run the check once when called
