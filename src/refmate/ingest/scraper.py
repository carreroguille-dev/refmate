"""Fase 1 del pipeline de ingesta: descarga de PDFs.

Responsabilidad única: descargar los documentos normativos desde sus URLs,
verificar integridad, deduplicar mediante SHA-256 y registrar metadatos en
manifest.json.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger

from refmate.config import get_config

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_TIMEOUT_SECONDS = 60
_MAX_RETRIES = 3
_BACKOFF_BASE = 2  # segundos: 2^1, 2^2, 2^3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_sha256(path: Path) -> str:
    """Calcula el hash SHA-256 de un fichero.

    Args:
        path: Ruta al fichero.

    Returns:
        Hash SHA-256 en hexadecimal.
    """
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _load_manifest(manifest_path: Path) -> dict[str, dict]:  # type: ignore[type-arg]
    """Carga el manifest.json existente o devuelve un dict vacío.

    Args:
        manifest_path: Ruta al fichero manifest.json.

    Returns:
        Diccionario con los metadatos registrados.
    """
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)  # type: ignore[no-any-return]
    return {}


def _save_manifest(manifest_path: Path, data: dict[str, dict]) -> None:  # type: ignore[type-arg]
    """Guarda el manifest.json con formato legible.

    Args:
        manifest_path: Ruta al fichero manifest.json.
        data: Datos a persistir.
    """
    with open(manifest_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _verify_pdf(path: Path) -> None:
    """Verifica que el fichero es un PDF válido y no vacío.

    Args:
        path: Ruta al fichero descargado.

    Raises:
        ValueError: Si el fichero está vacío o no empieza con '%PDF'.
    """
    if path.stat().st_size == 0:
        raise ValueError(f"El fichero descargado está vacío: {path}")
    with open(path, "rb") as f:
        header = f.read(4)
    if header != b"%PDF":
        raise ValueError(
            f"El fichero no parece un PDF válido (cabecera: {header!r}): {path}"
        )


# ---------------------------------------------------------------------------
# Descarga individual con reintentos
# ---------------------------------------------------------------------------


async def _download_document(
    client: httpx.AsyncClient,
    doc_id: str,
    url: str,
    dest_path: Path,
) -> None:
    """Descarga un documento con reintentos y backoff exponencial.

    Args:
        client: Cliente HTTP reutilizable.
        doc_id: Identificador del documento (para logging).
        url: URL de descarga.
        dest_path: Ruta de destino donde guardar el fichero.

    Raises:
        httpx.HTTPError: Si agotan los reintentos sin éxito.
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.info(f"[{doc_id}] Descargando (intento {attempt}/{_MAX_RETRIES}): {url}")
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(dest_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
            logger.info(f"[{doc_id}] Descarga completada → {dest_path}")
            return
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.warning(f"[{doc_id}] Error en intento {attempt}: {exc}")
            if attempt < _MAX_RETRIES:
                wait = _BACKOFF_BASE**attempt
                logger.info(f"[{doc_id}] Reintentando en {wait}s...")
                await asyncio.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------


async def run_scraper() -> dict[str, dict]:  # type: ignore[type-arg]
    """Descarga todos los documentos normativos configurados.

    Lee las URLs de config.documents, aplica deduplicación por SHA-256 contra
    manifest.json, descarga los que no estén cacheados y actualiza el manifest.

    Returns:
        Diccionario doc_id → entrada del manifest con los resultados.
    """
    config = get_config()
    raw_dir = Path("data/raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_dir / "manifest.json"

    manifest = _load_manifest(manifest_path)

    timeout = httpx.Timeout(timeout=_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for doc_id, doc_cfg in config.documents.items():
            dest_path = raw_dir / f"{doc_id}.pdf"
            entry: dict[str, object] = {
                "url": doc_cfg.url,
                "filename": f"{doc_id}.pdf",
            }

            # Deduplicación: comprobar si ya existe con hash coincidente
            if dest_path.exists():
                existing_sha = _compute_sha256(dest_path)
                cached_sha = manifest.get(doc_id, {}).get("sha256")
                if cached_sha and existing_sha == cached_sha:
                    logger.info(f"[{doc_id}] Fichero en caché (SHA-256 coincide). Saltando descarga.")
                    entry.update({
                        "download_date": manifest[doc_id].get("download_date"),
                        "sha256": existing_sha,
                        "size_bytes": dest_path.stat().st_size,
                        "status": "cached",
                    })
                    manifest[doc_id] = entry
                    continue
                else:
                    logger.info(f"[{doc_id}] SHA-256 no coincide o no registrado. Re-descargando.")

            # Descarga
            try:
                await _download_document(client, doc_id, doc_cfg.url, dest_path)
                _verify_pdf(dest_path)
                sha256 = _compute_sha256(dest_path)
                entry.update({
                    "download_date": datetime.now(timezone.utc).isoformat(),
                    "sha256": sha256,
                    "size_bytes": dest_path.stat().st_size,
                    "status": "ok",
                })
                logger.info(
                    f"[{doc_id}] OK — {entry['size_bytes']:,} bytes, SHA-256: {sha256[:16]}..."
                )
            except Exception as exc:
                logger.error(f"[{doc_id}] Error durante la descarga: {exc}")
                entry.update({
                    "download_date": datetime.now(timezone.utc).isoformat(),
                    "sha256": None,
                    "size_bytes": None,
                    "status": "error",
                    "error": str(exc),
                })

            manifest[doc_id] = entry

    _save_manifest(manifest_path, manifest)
    logger.info(f"Manifest guardado en {manifest_path}")

    # Resumen
    ok = sum(1 for e in manifest.values() if e.get("status") in ("ok", "cached"))
    errors = sum(1 for e in manifest.values() if e.get("status") == "error")
    logger.info(f"Scraper completado: {ok} OK/cached, {errors} errores")

    return manifest


# ---------------------------------------------------------------------------
# Punto de entrada standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_scraper())
