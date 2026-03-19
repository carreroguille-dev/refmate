"""Fase 3 del pipeline de ingesta: extracción de texto OCR.

Responsabilidad única: orquestar el procesamiento OCR secuencial de todas las
imágenes recortadas, concatenar el texto con marcadores de página y persistir
el resultado en data/ocr/{doc_id}_raw.txt.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx
from loguru import logger

from refmate.config import get_config, get_project_root
from refmate.core.protocols import OCRProvider
from refmate.infrastructure.ocr.lighton import LightOnOCRProvider


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_RETRY_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0
_PAGE_MARKER = "\n\n<!-- PAGE {n} -->\n\n"


# ---------------------------------------------------------------------------
# Health check del endpoint vLLM
# ---------------------------------------------------------------------------


def _parse_base_url(endpoint: str) -> str:
    """Extrae la URL base del servidor a partir del endpoint completo.

    Toma el endpoint (ej. 'http://host:8000/v1/chat/completions') y devuelve
    la parte hasta el prefijo '/v1' (ej. 'http://host:8000').

    Args:
        endpoint: URL completa del endpoint OCR.

    Returns:
        URL base del servidor (sin path).
    """
    match = re.match(r"(https?://[^/]+)", endpoint)
    if match:
        return match.group(1)
    return endpoint.rstrip("/")


async def _check_endpoint_health(base_url: str, model_name: str, timeout: int) -> None:
    """Comprueba que el servidor vLLM está activo consultando /health.

    Args:
        base_url: URL base del servidor (ej. 'http://host.docker.internal:8000').
        model_name: Nombre del modelo OCR configurado (para el mensaje de error).
        timeout: Segundos de espera máxima para la petición de salud.

    Raises:
        RuntimeError: Si el endpoint no responde o devuelve error HTTP.
    """
    health_url = f"{base_url}/health"
    logger.info(f"Comprobando disponibilidad del endpoint OCR → {health_url}")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(health_url)
            response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise RuntimeError(
            f"El servidor vLLM no está disponible en {health_url}.\n"
            f"Para levantarlo, ejecuta en el host:\n\n"
            f"  vllm serve {model_name} \\\n"
            f"    --limit-mm-per-prompt '{{\"image\": 1}}' \\\n"
            f"    --mm-processor-cache-gb 0 \\\n"
            f"    --no-enable-prefix-caching \\\n"
            f"    --gpu-memory-utilization 0.85 \\\n"
            f"    --max-model-len 4096\n"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"El endpoint de salud {health_url} devolvió error HTTP {exc.response.status_code}."
        ) from exc

    logger.info("Endpoint OCR disponible.")


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


async def _extract_with_retry(
    provider: OCRProvider,
    image_path: Path,
    page_number: int,
    doc_id: str,
) -> str:
    """Llama a extract_text con reintentos y backoff exponencial.

    Args:
        provider: Instancia del proveedor OCR.
        image_path: Ruta a la imagen a procesar.
        page_number: Número de página (para logging).
        doc_id: ID del documento (para logging).

    Returns:
        Texto extraído de la imagen.

    Raises:
        Exception: Si se agotan todos los intentos.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return await provider.extract_text(image_path)
        except Exception as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS:
                wait = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    f"[{doc_id}] Página {page_number}: intento {attempt}/{_RETRY_ATTEMPTS} "
                    f"fallido ({exc}). Reintentando en {wait:.0f}s..."
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    f"[{doc_id}] Página {page_number}: todos los intentos agotados. Error: {exc}"
                )

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Función principal async
# ---------------------------------------------------------------------------


async def run_ocr_runner(doc_ids: list[str] | None = None) -> dict[str, int]:
    """Ejecuta OCR sobre todas las imágenes recortadas de los documentos.

    Lee las imágenes de `data/images/{doc_id}/`, las procesa secuencialmente
    con LightOnOCR y guarda el texto completo en `data/ocr/{doc_id}_raw.txt`.

    Args:
        doc_ids: Lista de IDs de documentos a procesar. Si es None, procesa
                 todos los documentos configurados en config.documents.

    Returns:
        Diccionario doc_id → número de páginas procesadas.

    Raises:
        RuntimeError: Si el endpoint vLLM no está disponible.
        FileNotFoundError: Si no hay imágenes para un documento.
    """
    config = get_config()
    root = get_project_root()
    images_dir = root / "data" / "images"
    ocr_dir = root / "data" / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)

    targets = doc_ids if doc_ids is not None else list(config.documents.keys())

    # Comprobar salud del endpoint antes de empezar
    base_url = _parse_base_url(config.models.ocr.endpoint)
    await _check_endpoint_health(
        base_url,
        config.models.ocr.name,
        config.models.ocr.health_timeout_seconds,
    )

    provider = LightOnOCRProvider(config.models.ocr)
    results: dict[str, int] = {}

    for doc_id in targets:
        if doc_id not in config.documents:
            logger.warning(f"[{doc_id}] No está en config.documents, saltando.")
            continue

        doc_images_dir = images_dir / doc_id
        if not doc_images_dir.exists():
            raise FileNotFoundError(
                f"Directorio de imágenes no encontrado para '{doc_id}': {doc_images_dir}. "
                f"Ejecuta primero el cropper (Fase 2)."
            )

        image_paths = sorted(doc_images_dir.glob("page_*_crop.png"))
        if not image_paths:
            raise FileNotFoundError(
                f"No hay imágenes en {doc_images_dir}. Ejecuta primero el cropper (Fase 2)."
            )

        n_pages = len(image_paths)
        logger.info(f"[{doc_id}] Iniciando OCR: {n_pages} páginas")

        page_texts: list[str] = []

        for idx, image_path in enumerate(image_paths):
            page_number = idx + 1
            logger.debug(f"[{doc_id}] Procesando página {page_number}/{n_pages}: {image_path.name}")

            text = await _extract_with_retry(provider, image_path, page_number, doc_id)
            page_texts.append(text)

            if page_number % 10 == 0 or page_number == n_pages:
                logger.info(f"[{doc_id}] {page_number}/{n_pages} páginas procesadas con OCR")

        # Concatenar páginas con marcadores
        parts: list[str] = []
        for page_number, text in enumerate(page_texts, start=1):
            if page_number > 1:
                parts.append(_PAGE_MARKER.format(n=page_number))
            parts.append(text)

        full_text = "".join(parts)

        out_path = ocr_dir / f"{doc_id}_raw.txt"
        out_path.write_text(full_text, encoding="utf-8")
        logger.info(
            f"[{doc_id}] OCR completado: {n_pages} páginas → {out_path} "
            f"({len(full_text):,} caracteres)"
        )

        results[doc_id] = n_pages

    total_pages = sum(results.values())
    logger.info(f"OCR Runner completado: {total_pages} páginas totales en {len(results)} documentos")
    return results


# ---------------------------------------------------------------------------
# Punto de entrada standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_ocr_runner())
