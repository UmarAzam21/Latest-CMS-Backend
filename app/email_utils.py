import os
import smtplib
from email.mime.text import MIMEText

SMTP_EMAIL = os.getenv("SMTP_EMAIL") or os.getenv("EMAIL_ADDRESS") or ""
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")


def send_email(to_email: str, subject: str, body: str):
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_EMAIL
    msg["To"] = to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, to_email, msg.as_string())
        return True
    except Exception:
        return False