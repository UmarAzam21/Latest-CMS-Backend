import os
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class Settings:
    # Database
    DATABASE_URL: str = _get(
        "DATABASE_URL",
        "mysql+pymysql://root:@localhost:3306/filernow_db",
    )

    # Domain used to build ticket+<id>@yourdomain.com addresses
    SUPPORT_DOMAIN: str = _get("SUPPORT_DOMAIN", "yourdomain.com")

    # SMTP (outgoing)
    SMTP_HOST: str = _get("SMTP_HOST", "localhost")
    SMTP_PORT: int = int(_get("SMTP_PORT", "587"))
    SMTP_USER: str = _get("SMTP_USER", "")
    SMTP_PASSWORD: str = _get("SMTP_PASSWORD", "")
    SMTP_USE_TLS: bool = _get("SMTP_USE_TLS", "true").lower() == "true"
    SMTP_FROM_NAME: str = _get("SMTP_FROM_NAME", "Support Team")

    # IMAP (incoming)
    IMAP_HOST: str = _get("IMAP_HOST", "localhost")
    IMAP_PORT: int = int(_get("IMAP_PORT", "993"))
    IMAP_USER: str = _get("IMAP_USER", "")
    IMAP_PASSWORD: str = _get("IMAP_PASSWORD", "")
    IMAP_MAILBOX: str = _get("IMAP_MAILBOX", "INBOX")
    IMAP_POLL_SECONDS: int = int(_get("IMAP_POLL_SECONDS", "30"))

    # Admin auth
    ADMIN_USERNAME: str = _get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = _get("ADMIN_PASSWORD", "change-me")


settings = Settings()
