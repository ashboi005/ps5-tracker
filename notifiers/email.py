"""SMTP email notifier."""

import os
import smtplib
from email.message import EmailMessage

TIMEOUT = 20.0


def configured() -> bool:
    return bool(
        os.getenv("SMTP_HOST")
        and os.getenv("SMTP_USER")
        and os.getenv("SMTP_PASSWORD")
        and os.getenv("EMAIL_TO")
    )


def send(message: str) -> None:
    """Send a plain-text email over SMTP with STARTTLS."""
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    recipient = os.getenv("EMAIL_TO")
    sender = os.getenv("EMAIL_FROM") or user

    if not (host and user and password and recipient):
        raise RuntimeError("SMTP_HOST, SMTP_USER, SMTP_PASSWORD or EMAIL_TO not set")

    email = EmailMessage()
    email["Subject"] = "PS5 stock alert"
    email["From"] = sender
    email["To"] = recipient
    email.set_content(message)

    with smtplib.SMTP(host, port, timeout=TIMEOUT) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(email)
