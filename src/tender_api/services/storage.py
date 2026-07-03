"""Almacenamiento de ficheros del expediente en InsForge Storage (bucket privado).

Acceso server-side con la key admin (`x-api-key`). Si no está configurado, `is_configured()` es
False y los endpoints degradan a 503 (modo solo-lógico). REST:
- PUT    /api/storage/buckets/{bucket}/objects/{key}   → subir con key exacto
- GET    /api/storage/buckets/{bucket}/objects?prefix= → listar
- GET    /api/storage/buckets/{bucket}/objects/{key}   → descargar (302 → CDN)
- DELETE /api/storage/buckets/{bucket}/objects/{key}   → borrar
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from tender_api.config import settings


def is_configured() -> bool:
    return bool(
        settings.insforge_api_url and settings.insforge_api_key and settings.expedient_bucket
    )


def _objects_url() -> str:
    base = settings.insforge_api_url.rstrip("/")
    return f"{base}/api/storage/buckets/{settings.expedient_bucket}/objects"


def _obj_url(key: str) -> str:
    return f"{_objects_url()}/{quote(key, safe='/')}"


def _headers() -> dict:
    return {"x-api-key": settings.insforge_api_key}


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Sube bytes bajo el key exacto (`{tender_id}/{carpeta}/{fichero}`) y devuelve la clave."""
    filename = key.rsplit("/", 1)[-1] or "fichero"
    with httpx.Client(timeout=90) as client:
        resp = client.put(
            _obj_url(key),
            headers=_headers(),
            files={"file": (filename, data, content_type or "application/octet-stream")},
        )
        resp.raise_for_status()
    return key


def fetch(key: str) -> tuple[bytes, str]:
    """Descarga los bytes (sigue el redirect a la CDN). Devuelve (contenido, content_type)."""
    with httpx.Client(timeout=90, follow_redirects=True) as client:
        resp = client.get(_obj_url(key), headers=_headers())
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "application/octet-stream")


def delete(key: str) -> None:
    with httpx.Client(timeout=30) as client:
        resp = client.delete(_obj_url(key), headers=_headers())
        resp.raise_for_status()


def list_objects(prefix: str) -> list[dict]:
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            _objects_url(), headers=_headers(), params={"prefix": prefix, "limit": 200}
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
