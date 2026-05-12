import os
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def send_email(subject: str, body: str) -> None:
    """Send a plain-text report email using SMTP.

    For QQ Mail, use an authorization code/app password, not the normal login password.
    Required env vars:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, MAIL_TO
    Optional:
      MAIL_FROM_NAME, SMTP_SSL
    """
    smtp_host = os.getenv("SMTP_HOST", "smtp.qq.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    mail_to = os.getenv("MAIL_TO")
    mail_from_name = os.getenv("MAIL_FROM_NAME", "MOS Radar")
    smtp_ssl = env_bool("SMTP_SSL", default=(smtp_port == 465))

    if not all([smtp_host, smtp_user, smtp_password, mail_to]):
        raise RuntimeError(
            "Missing email environment variables. Need SMTP_HOST, SMTP_PORT, "
            "SMTP_USER, SMTP_PASSWORD, MAIL_TO."
        )

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = formataddr((str(Header(mail_from_name, "utf-8")), smtp_user))
    msg["To"] = mail_to
    msg["Subject"] = Header(subject, "utf-8")

    recipients = [x.strip() for x in mail_to.split(",") if x.strip()]

    if smtp_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())
