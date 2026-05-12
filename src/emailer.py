from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def send_email(subject: str, body: str):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    mail_to = os.getenv("MAIL_TO")
    smtp_ssl = env_bool("SMTP_SSL", default=False)
    from_name = os.getenv("MAIL_FROM_NAME", "MOS Radar")

    if not all([smtp_host, smtp_user, smtp_password, mail_to]):
        raise RuntimeError("Missing email environment variables.")

    is_html = "<html" in body.lower() or "<table" in body.lower()

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{Header(from_name, 'utf-8')} <{smtp_user}>"
    msg["To"] = mail_to
    msg["Subject"] = Header(subject, "utf-8")

    if is_html:
        plain = "MOS Radar 安全边际报告。请使用支持 HTML 的邮件客户端查看表格版报告。"
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(body, "html", "utf-8"))
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))

    if smtp_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [mail_to], msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [mail_to], msg.as_string())
