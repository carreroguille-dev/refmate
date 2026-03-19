"""Fase 2 del pipeline de ingesta: renderizado PDF a imagen y recorte.

Responsabilidad única: convertir cada página de los PDFs normativos en
imágenes PNG recortadas (sin headers/footers), listas para el OCR.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pypdfium2 as pdfium
from loguru import logger
from PIL import Image

from refmate.config import CropMaskConfig, RenderingConfig, get_config, get_project_root


# ---------------------------------------------------------------------------
# Helpers de cálculo y recorte
# ---------------------------------------------------------------------------


def _compute_scale(page_width_pts: float, page_height_pts: float, max_dimension: int) -> float:
    """Calcula el factor de escala para que el lado largo sea max_dimension.

    Args:
        page_width_pts: Ancho de la página en puntos PDF (1 pt = 1/72 in).
        page_height_pts: Alto de la página en puntos PDF.
        max_dimension: Tamaño máximo del lado largo en píxeles.

    Returns:
        Factor de escala a pasar a pypdfium2.
    """
    long_side = max(page_width_pts, page_height_pts)
    return max_dimension / long_side


def _apply_crop_mask(image: Image.Image, mask: CropMaskConfig, page_number: int) -> Image.Image:
    """Aplica la máscara de recorte a una imagen PIL.

    Los valores de la máscara son porcentajes del tamaño de la imagen.
    Para documentos con `alternate_sides=True`, las páginas impares recortan
    por la derecha (`right_pct`) y las pares por la izquierda (`left_pct`).

    Args:
        image: Imagen original a recortar.
        mask: Configuración de la máscara en porcentaje.
        page_number: Número de página 1-indexed (para alternate_sides).

    Returns:
        Nueva imagen PIL recortada (la original no se modifica en memoria).
    """
    width, height = image.size

    top = int(height * mask.top_pct / 100)
    bottom = int(height * mask.bottom_pct / 100)
    left = 0
    right = 0

    if mask.alternate_sides and mask.right_pct is not None and mask.left_pct is not None:
        # Impar → recorta derecha; par → recorta izquierda
        if page_number % 2 != 0:
            right = int(width * mask.right_pct / 100)
        else:
            left = int(width * mask.left_pct / 100)
    else:
        if mask.right_pct is not None:
            right = int(width * mask.right_pct / 100)
        if mask.left_pct is not None:
            left = int(width * mask.left_pct / 100)

    box = (left, top, width - right, height - bottom)
    return image.crop(box)


# ---------------------------------------------------------------------------
# Renderizado de un documento completo (síncrono, pensado para to_thread)
# ---------------------------------------------------------------------------


def _render_document(
    doc_id: str,
    pdf_path: Path,
    out_dir: Path,
    mask: CropMaskConfig,
    rendering: RenderingConfig,
) -> int:
    """Renderiza y recorta todas las páginas de un PDF.

    Libera la memoria de cada imagen original tras recortarla para
    mantener bajo el uso de RAM con documentos grandes.

    Args:
        doc_id: Identificador del documento (para logging).
        pdf_path: Ruta al fichero PDF de entrada.
        out_dir: Directorio donde guardar las imágenes recortadas.
        mask: Máscara de recorte en porcentaje para este documento.
        rendering: Parámetros de renderizado (max_dimension, format).

    Returns:
        Número de páginas procesadas.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf = pdfium.PdfDocument(str(pdf_path))
    n_pages = len(pdf)
    logger.info(f"[{doc_id}] Abriendo PDF: {n_pages} páginas → {out_dir}")

    processed = 0
    for page_idx in range(n_pages):
        page_number = page_idx + 1  # 1-indexed para alternate_sides y nombre de fichero
        out_path = out_dir / f"page_{page_number:04d}_crop.png"

        if out_path.exists():
            logger.debug(f"[{doc_id}] Página {page_number} ya existe, saltando.")
            processed += 1
            continue

        page = pdf[page_idx]
        width_pts = page.get_width()
        height_pts = page.get_height()

        scale = _compute_scale(width_pts, height_pts, rendering.max_dimension)
        bitmap = page.render(scale=scale, rotation=0)
        pil_image: Image.Image = bitmap.to_pil()

        # Recorte
        cropped = _apply_crop_mask(pil_image, mask, page_number)

        # Guardar y liberar memoria de la imagen original (orden: hijo → padre)
        cropped.save(str(out_path), format=rendering.format)
        cropped.close()
        pil_image.close()
        bitmap.close()

        processed += 1
        if page_number % 10 == 0 or page_number == n_pages:
            logger.info(f"[{doc_id}] {page_number}/{n_pages} páginas procesadas")

    pdf.close()
    logger.info(f"[{doc_id}] Renderizado completo: {processed} páginas en {out_dir}")
    return processed


# ---------------------------------------------------------------------------
# Función principal async
# ---------------------------------------------------------------------------


async def run_cropper(doc_ids: list[str] | None = None) -> dict[str, int]:
    """Renderiza y recorta los PDFs de los documentos configurados.

    Lee los PDFs de `data/raw/`, aplica las máscaras de recorte de
    `config.crop_masks` y guarda las imágenes en `data/images/{doc_id}/`.

    Args:
        doc_ids: Lista de IDs de documentos a procesar. Si es None, procesa
                 todos los documentos configurados en config.documents.

    Returns:
        Diccionario doc_id → número de páginas procesadas.

    Raises:
        FileNotFoundError: Si el PDF de algún documento no existe en data/raw/.
    """
    config = get_config()
    root = get_project_root()
    raw_dir = root / "data" / "raw"
    images_dir = root / "data" / "images"

    targets = doc_ids if doc_ids is not None else list(config.documents.keys())
    results: dict[str, int] = {}

    for doc_id in targets:
        if doc_id not in config.documents:
            logger.warning(f"[{doc_id}] No está en config.documents, saltando.")
            continue

        pdf_path = raw_dir / f"{doc_id}.pdf"
        if not pdf_path.exists():
            raise FileNotFoundError(
                f"PDF no encontrado para '{doc_id}': {pdf_path}. "
                f"Ejecuta primero el scraper (Fase 1)."
            )

        if doc_id not in config.crop_masks:
            logger.warning(
                f"[{doc_id}] Sin máscara de recorte en config.crop_masks. "
                f"Se renderizará sin recorte (top/bottom=0)."
            )
            mask = CropMaskConfig(top_pct=0.0, bottom_pct=0.0)
        else:
            mask = config.crop_masks[doc_id]

        out_dir = images_dir / doc_id

        logger.info(f"[{doc_id}] Iniciando renderizado+recorte → {out_dir}")
        n_pages = await asyncio.to_thread(
            _render_document,
            doc_id,
            pdf_path,
            out_dir,
            mask,
            config.rendering,
        )
        results[doc_id] = n_pages

    total = sum(results.values())
    logger.info(f"Cropper completado: {total} páginas totales en {len(results)} documentos")
    return results


# ---------------------------------------------------------------------------
# Punto de entrada standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_cropper())
