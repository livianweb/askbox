"""Envío de email opcional por SMTP (stdlib). Sin SMTP configurado, send_mail devuelve False."""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger(__name__)


def smtp_configured() -> bool:
    """Host + From obligatorios; Brevo y la mayoría de relays exigen login (USER + PASSWORD)."""
    if not (settings.smtp_host and settings.smtp_from):
        return False
    if settings.smtp_password and not settings.smtp_user:
        return False
    return True


def _send_sync(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if settings.smtp_tls == "ssl":
        client = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=ctx)
    else:
        client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
    with client as s:
        if settings.smtp_tls == "starttls":
            s.starttls(context=ctx)
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        refused = s.send_message(msg)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
        log.info(
            "email SMTP aceptado (250/queued) to=%s subject=%s from=%s",
            to,
            subject,
            settings.smtp_from,
        )


async def send_mail(to: str, subject: str, body: str) -> bool:
    if not smtp_configured():
        return False
    try:
        await asyncio.to_thread(_send_sync, to, subject, body)
        return True
    except Exception as exc:  # noqa: BLE001 - nunca volcar credenciales ni cuerpo
        detail = str(exc).replace(settings.smtp_password, "***") if settings.smtp_password else str(exc)
        if len(detail) > 300:
            detail = detail[:300] + "…"
        log.error(
            "fallo enviando email de recuperación: %s — %s",
            type(exc).__name__,
            detail,
        )
        return False
