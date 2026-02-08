import json
import re
from pathlib import Path
from pdf2image import convert_from_path
from PIL import Image

from config.settings import PATHS, ROOT
from utils.logger import get_logger

log = get_logger(__name__)

TEMPLATES_DIR = ROOT / "config" / "image_templates"


def load_templates():
    """Load all crop templates from config/image_templates/."""
    templates = []

    for template_file in TEMPLATES_DIR.glob("*.json"):
        try:
            with open(template_file, 'r', encoding='utf-8') as f:
                template = json.load(f)
                template['_file'] = template_file.name
                templates.append(template)
                log.debug(f"Template loaded: {template_file.name}")
        except Exception as e:
            log.error(f"Error loading template {template_file}: {e}")

    log.info(f"Loaded {len(templates)} crop templates")
    return templates


def match_template(pdf_path, templates):
    """Find the matching template for a PDF based on file_pattern."""
    filename = Path(pdf_path).stem.lower()

    for template in templates:
        pattern = template.get('file_pattern', '')
        if re.search(pattern, filename, re.IGNORECASE):
            log.info(f"Matched '{filename}' to template '{template['name']}'")
            return template

    log.warning(f"No template matched for '{filename}', using no crop")
    return None


def calculate_crop_box(image, crop_config, page_num):
    """
    Calculate the crop box (left, top, right, bottom) for an image.

    Args:
        image: PIL Image object
        crop_config: Crop configuration from template
        page_num: Page number (1-indexed)

    Returns:
        Tuple (left, top, right, bottom) in pixels
    """
    width, height = image.size

    if 'odd_pages' in crop_config and 'even_pages' in crop_config:
        if page_num % 2 == 1:
            settings = crop_config['odd_pages']
        else:
            settings = crop_config['even_pages']
    else:
        settings = crop_config.get('all_pages', {})

    top_pct = settings.get('top', 0)
    bottom_pct = settings.get('bottom', 0)
    left_pct = settings.get('left', 0)
    right_pct = settings.get('right', 0)

    left = int(width * left_pct / 100)
    top = int(height * top_pct / 100)
    right = int(width - (width * right_pct / 100))
    bottom = int(height - (height * bottom_pct / 100))

    return (left, top, right, bottom)


def crop_image(image, crop_box):
    """Crop an image using the given box."""
    return image.crop(crop_box)


def stream_pdf_pages(pdf_path, dpi=200):
    """
    Stream cropped pages from a PDF (memory efficient).

    Args:
        pdf_path: Path to the PDF file
        dpi: Resolution for PDF conversion

    Yields:
        Tuple (page_num, cropped_image) for each page
    """
    pdf_path = Path(pdf_path)

    log.info(f"Streaming PDF: {pdf_path.name}")

    templates = load_templates()
    template = match_template(pdf_path, templates)

    skip_first = template.get('skip_first_page', False) if template else False
    crop_config = template.get('crop', {}) if template else {}

    log.info(f"Converting PDF to images at {dpi} DPI...")
    try:
        images = convert_from_path(pdf_path, dpi=dpi)
    except Exception as e:
        log.error(f"Error converting PDF: {e}")
        raise

    log.info(f"PDF has {len(images)} pages")

    start_page = 2 if skip_first else 1

    for page_num, image in enumerate(images, start=1):
        if page_num < start_page:
            log.debug(f"Skipping page {page_num} (first page)")
            continue

        if crop_config:
            crop_box = calculate_crop_box(image, crop_config, page_num)
            cropped = crop_image(image, crop_box)
            log.debug(f"Page {page_num}: cropped from {image.size} to {cropped.size}")
        else:
            cropped = image

        yield page_num, cropped


def process_pdf(pdf_path, output_dir=None, dpi=200):
    """
    Convert a PDF to cropped images and save to disk.
    Use this for testing/debugging. For production, use stream_pdf_pages().

    Args:
        pdf_path: Path to the PDF file
        output_dir: Directory to save images (default: data/temp/{pdf_name}/)
        dpi: Resolution for PDF conversion

    Returns:
        List of paths to the cropped images
    """
    pdf_path = Path(pdf_path)
    pdf_name = pdf_path.stem

    if output_dir is None:
        output_dir = PATHS['temp'] / pdf_name
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Processing PDF: {pdf_path.name}")

    templates = load_templates()
    template = match_template(pdf_path, templates)

    skip_first = template.get('skip_first_page', False) if template else False
    crop_config = template.get('crop', {}) if template else {}

    log.info(f"Converting PDF to images at {dpi} DPI...")
    try:
        images = convert_from_path(pdf_path, dpi=dpi)
    except Exception as e:
        log.error(f"Error converting PDF: {e}")
        raise

    log.info(f"PDF has {len(images)} pages")

    output_paths = []
    start_page = 2 if skip_first else 1

    for page_num, image in enumerate(images, start=1):
        if page_num < start_page:
            log.debug(f"Skipping page {page_num} (first page)")
            continue

        if crop_config:
            crop_box = calculate_crop_box(image, crop_config, page_num)
            cropped = crop_image(image, crop_box)
            log.debug(f"Page {page_num}: cropped from {image.size} to {cropped.size}")
        else:
            cropped = image

        output_path = output_dir / f"page_{page_num:04d}.png"
        cropped.save(output_path, 'PNG')
        output_paths.append(output_path)

    log.info(f"Saved {len(output_paths)} cropped images to {output_dir}")
    return output_paths


def process_all_pdfs(input_dir=None, dpi=200):
    """
    Process all PDFs in a directory.

    Args:
        input_dir: Directory containing PDFs (default: data/raw/)
        dpi: Resolution for PDF conversion

    Returns:
        Dict mapping PDF names to lists of image paths
    """
    if input_dir is None:
        input_dir = PATHS['raw']
    else:
        input_dir = Path(input_dir)

    results = {}
    pdf_files = list(input_dir.glob("*.pdf"))

    log.info(f"Found {len(pdf_files)} PDFs in {input_dir}")

    for pdf_path in pdf_files:
        try:
            output_paths = process_pdf(pdf_path, dpi=dpi)
            results[pdf_path.stem] = output_paths
        except Exception as e:
            log.error(f"Failed to process {pdf_path.name}: {e}")
            results[pdf_path.stem] = []

    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        pdf_file = sys.argv[1]
        process_pdf(pdf_file)
    else:
        process_all_pdfs()
