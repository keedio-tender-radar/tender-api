"""Almacenamiento de binarios del expediente en S3/MinIO (opcional).

Si no hay bucket configurado, `is_configured()` es False y los endpoints degradan a 503 (modo
solo-lógico: las carpetas existen en BD pero no se suben ficheros). boto3 funciona con AWS S3 y
con MinIO vía `endpoint_url`. boto3 se importa de forma perezosa para no pesar si no se usa.
"""

from __future__ import annotations

from tender_api.config import settings


def is_configured() -> bool:
    return bool(settings.s3_bucket and settings.s3_access_key and settings.s3_secret_key)


def _client():
    import boto3  # import perezoso

    kwargs = {
        "aws_access_key_id": settings.s3_access_key,
        "aws_secret_access_key": settings.s3_secret_key,
        "region_name": settings.s3_region,
    }
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url  # MinIO u otro S3-compatible
    return boto3.client("s3", **kwargs)


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Sube bytes a {bucket}/{key} y devuelve la clave. Requiere is_configured()."""
    _client().put_object(
        Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type
    )
    return key


def presigned_get(key: str, expires: int = 3600) -> str:
    """URL prefirmada de descarga para la clave dada."""
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires,
    )
