import smtplib
from email.message import EmailMessage

from app.support_system.config import settings


def send_email(
    to_address: str,
    subject: str,
    body: str,
    reply_to: str | None = None,
    from_address: str | None = None,
) -> bool:
    """Send a plain-text email via SMTP.

    Returns False when the SMTP configuration is not available, without raising
    an exception so the admin UI can still save the message/reply.
    """
    if not settings.SMTP_HOST or settings.SMTP_HOST == "localhost":
        return False
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        return False

    msg = EmailMessage()
    from_addr = from_address or settings.SMTP_USER
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{from_addr}>"
    msg["To"] = to_address
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            if settings.SMTP_USE_TLS:
                server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception:
        return False
