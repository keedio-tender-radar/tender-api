"""Envío de email vía InsForge (REST send-raw).

InsForge gestiona el remitente (SES); solo necesitamos la URL del proyecto y la ANON_KEY. El email
custom requiere plan de pago InsForge — en free/OSS devuelve error, que tratamos con elegancia.
"""

from __future__ import annotations

import httpx

from tender_api.config import settings


def is_configured() -> bool:
    return bool(
        settings.insforge_api_url and settings.insforge_anon_key and settings.digest_email_to
    )


def recipients() -> list[str]:
    return [e.strip() for e in settings.digest_email_to.split(",") if e.strip()]


def send(subject: str, html: str, to: list[str] | None = None) -> dict:
    """POST {insforge_api_url}/api/email/send-raw. Lanza httpx.HTTPStatusError si falla."""
    url = settings.insforge_api_url.rstrip("/") + "/api/email/send-raw"
    payload = {
        "to": to or recipients(),
        "subject": subject,
        "html": html,
        "from": "Keedio Tender Radar",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            url, json=payload, headers={"Authorization": f"Bearer {settings.insforge_anon_key}"}
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}
