"""Pipeline Orchestrator: ejecuta las fases de ingesta en secuencia.

Responsabilidad única: coordinar la ejecución ordenada de las fases 1-6,
verificar artefactos previos, reportar estadísticas e invalidar la caché
semántica de Redis al finalizar la indexación.

Uso:
    uv run python -m refmate.ingest.pipeline              # todas las fases
    uv run python -m refmate.ingest.pipeline --from ocr   # desde OCR en adelante
    uv run python -m refmate.ingest.pipeline --only indexer  # solo indexer
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from refmate.config import CacheConfig, RefMateConfig, get_config, get_project_root

# Orden canónico de las fases de ingesta
PHASES: list[str] = ["scraper", "cropper", "ocr", "structurer", "chunker", "indexer"]


# ---------------------------------------------------------------------------
# Verificación de artefactos previos
# ---------------------------------------------------------------------------


def _check_scraper(root: Path, config: RefMateConfig) -> None:
    """Sin precondiciones: el scraper descarga desde URLs externas."""


def _check_cropper(root: Path, config: RefMateConfig) -> None:
    """Verifica que existen los PDFs descargados (precondición del Cropper)."""
    for doc_id in config.documents:
        pdf = root / "data" / "raw" / f"{doc_id}.pdf"
        if not pdf.exists():
            raise FileNotFoundError(
                f"PDF no encontrado: {pdf}. Ejecuta la fase 'scraper' primero."
            )


def _check_ocr(root: Path, config: RefMateConfig) -> None:
    """Verifica que existen imágenes recortadas (precondición del OCR)."""
    for doc_id in config.documents:
        images_dir = root / "data" / "images" / doc_id
        if not images_dir.exists() or not any(images_dir.glob("*.png")):
            raise FileNotFoundError(
                f"Imágenes no encontradas para '{doc_id}': {images_dir}. "
                "Ejecuta la fase 'cropper' primero."
            )


def _check_structurer(root: Path, config: RefMateConfig) -> None:
    """Verifica que existe el texto OCR (precondición del Structurer)."""
    for doc_id in config.documents:
        ocr_file = root / "data" / "ocr" / f"{doc_id}_raw.txt"
        if not ocr_file.exists():
            raise FileNotFoundError(
                f"Texto OCR no encontrado: {ocr_file}. Ejecuta la fase 'ocr' primero."
            )


def _check_chunker(root: Path, config: RefMateConfig) -> None:
    """Verifica que existe el Markdown estructurado (precondición del Chunker)."""
    for doc_id in config.documents:
        md_file = root / "data" / "structured" / f"{doc_id}.md"
        if not md_file.exists():
            raise FileNotFoundError(
                f"Markdown estructurado no encontrado: {md_file}. "
                "Ejecuta la fase 'structurer' primero."
            )


def _check_indexer(root: Path, config: RefMateConfig) -> None:
    """Verifica que existen los chunks JSON (precondición del Indexer)."""
    for doc_id in config.documents:
        chunks_file = root / "data" / "chunks" / f"{doc_id}_chunks.json"
        if not chunks_file.exists():
            raise FileNotFoundError(
                f"Chunks no encontrados: {chunks_file}. "
                "Ejecuta la fase 'chunker' primero."
            )


_ARTIFACT_CHECKS: dict[str, Callable[[Path, RefMateConfig], None]] = {
    "scraper": _check_scraper,
    "cropper": _check_cropper,
    "ocr": _check_ocr,
    "structurer": _check_structurer,
    "chunker": _check_chunker,
    "indexer": _check_indexer,
}


def _check_artifacts(phase: str, root: Path, config: RefMateConfig) -> None:
    """Ejecuta la verificación de artefactos previos para la fase dada.

    Args:
        phase: Nombre de la fase a verificar.
        root: Directorio raíz del proyecto.
        config: Configuración del sistema.

    Raises:
        FileNotFoundError: Si algún artefacto requerido no existe.
    """
    _ARTIFACT_CHECKS[phase](root, config)


# ---------------------------------------------------------------------------
# Ejecución individual de cada fase
# ---------------------------------------------------------------------------


async def _run_phase(phase: str, config: RefMateConfig, root: Path) -> dict[str, Any]:
    """Verifica artefactos y ejecuta una fase individual.

    Las importaciones pesadas (FlagEmbedding, OpenRouter, etc.) son lazy
    para no penalizar el arranque del pipeline.

    Args:
        phase: Nombre de la fase a ejecutar.
        config: Configuración del sistema.
        root: Directorio raíz del proyecto (para verificación de artefactos).

    Returns:
        Dict con estadísticas de la ejecución (específico por fase).

    Raises:
        FileNotFoundError: Si faltan artefactos previos.
        ValueError: Si el nombre de fase es desconocido.
    """
    _check_artifacts(phase, root, config)

    if phase == "scraper":
        from refmate.ingest.scraper import run_scraper

        return await run_scraper()

    if phase == "cropper":
        from refmate.ingest.cropper import run_cropper

        result = await run_cropper()
        return {"pages_per_doc": result}

    if phase == "ocr":
        from refmate.ingest.ocr_runner import run_ocr_runner

        result = await run_ocr_runner()
        return {"pages_per_doc": result}

    if phase == "structurer":
        from refmate.infrastructure.llm.openrouter import OpenRouterTextGenerator
        from refmate.ingest.structurer import Structurer

        generator = OpenRouterTextGenerator(config)
        structurer = Structurer(generator, config)
        stats: dict[str, Any] = {}
        for doc_id in config.documents:
            logger.info(f"[structurer] Procesando {doc_id}...")
            stats[doc_id] = await structurer.run(doc_id)
        return stats

    if phase == "chunker":
        from refmate.ingest.chunker import Chunker

        chunker = Chunker(config)
        return await chunker.run_all()

    if phase == "indexer":
        from refmate.ingest.indexer import run_indexer

        await run_indexer(config, recreate=True)
        return {}

    raise ValueError(f"Fase desconocida: '{phase}'. Fases válidas: {PHASES}")


# ---------------------------------------------------------------------------
# Invalidación de caché Redis
# ---------------------------------------------------------------------------


async def _invalidate_redis(cache_config: CacheConfig) -> None:
    """Invalida toda la caché semántica en Redis (FLUSHDB).

    Args:
        cache_config: Configuración de conexión a Redis.
    """
    import redis.asyncio as aioredis

    client: aioredis.Redis = aioredis.Redis(
        host=cache_config.host,
        port=cache_config.port,
        db=cache_config.db,
    )
    try:
        await client.flushdb()
        logger.info("Caché Redis invalidada (FLUSHDB)")
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Orquestador principal
# ---------------------------------------------------------------------------


async def run_pipeline(phases_to_run: list[str], config: RefMateConfig) -> None:
    """Ejecuta las fases indicadas en orden, loguea estadísticas y finaliza.

    Si el indexer estaba entre las fases y `config.cache.invalidate_on_ingest`
    es True, invalida la caché Redis al terminar.

    Args:
        phases_to_run: Lista ordenada de nombres de fase a ejecutar.
        config: Configuración del sistema.

    Raises:
        ValueError: Si alguna fase de `phases_to_run` no es una fase válida.
        Exception: Si cualquier fase falla, el error se propaga y el pipeline para.
    """
    invalid = [p for p in phases_to_run if p not in PHASES]
    if invalid:
        raise ValueError(f"Fases desconocidas: {invalid}. Fases válidas: {PHASES}")

    root = get_project_root()
    summary: dict[str, dict[str, Any]] = {}
    pipeline_t0 = time.monotonic()

    logger.info(f"Pipeline iniciado — fases: {', '.join(phases_to_run)}")
    logger.info("=" * 60)

    for phase in phases_to_run:
        logger.info(f"▶ Fase: {phase.upper()}")
        phase_t0 = time.monotonic()
        try:
            stats = await _run_phase(phase, config, root)
            elapsed = time.monotonic() - phase_t0
            summary[phase] = {"status": "ok", "elapsed_s": round(elapsed, 1), "stats": stats}
            logger.info(f"✓ {phase} completado en {elapsed:.1f}s")
        except Exception as exc:
            elapsed = time.monotonic() - phase_t0
            summary[phase] = {"status": "error", "elapsed_s": round(elapsed, 1), "error": str(exc)}
            logger.error(f"✗ {phase} falló en {elapsed:.1f}s: {exc}")
            raise
        logger.info("-" * 60)

    # Invalidar Redis si el índice vectorial fue actualizado
    if "indexer" in phases_to_run and config.cache.invalidate_on_ingest:
        logger.info("Invalidando caché semántica Redis...")
        try:
            await _invalidate_redis(config.cache)
        except Exception as exc:
            logger.warning(f"No se pudo invalidar Redis (¿está levantado?): {exc}")

    # Resumen final
    total_elapsed = time.monotonic() - pipeline_t0
    logger.info("=" * 60)
    logger.info(f"Pipeline completada en {total_elapsed:.1f}s")
    for phase, data in summary.items():
        mark = "✓" if data["status"] == "ok" else "✗"
        stats = data.get("stats") or {}
        stats_str = f" — {stats}" if stats else ""
        logger.info(f"  {mark} {phase:<12} {data['elapsed_s']:>6.1f}s{stats_str}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Punto de entrada CLI del pipeline de ingesta.

    Parsea los argumentos --from y --only, calcula las fases a ejecutar
    y lanza el pipeline asíncrono.
    """
    parser = argparse.ArgumentParser(
        description="RefMate — Pipeline de ingesta de documentos normativos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Fases disponibles (en orden): {', '.join(PHASES)}",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--from",
        dest="from_phase",
        metavar="FASE",
        choices=PHASES,
        help="Ejecutar desde esta fase en adelante (inclusive)",
    )
    group.add_argument(
        "--only",
        dest="only_phase",
        metavar="FASE",
        choices=PHASES,
        help="Ejecutar solo esta fase",
    )
    args = parser.parse_args()

    if args.only_phase:
        phases_to_run = [args.only_phase]
    elif args.from_phase:
        idx = PHASES.index(args.from_phase)
        phases_to_run = PHASES[idx:]
    else:
        phases_to_run = list(PHASES)

    config = get_config()
    asyncio.run(run_pipeline(phases_to_run, config))


if __name__ == "__main__":
    main()
