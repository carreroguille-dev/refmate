import torch
from datetime import date
from pathlib import Path
from PIL import Image
from tqdm import tqdm
from transformers import AutoProcessor, AutoModelForImageTextToText, AutoTokenizer

from config.settings import PATHS, OCR
from utils.logger import get_logger
from .pdf_processor import stream_pdf_pages

log = get_logger(__name__)

_tokenizer = None


def _get_tokenizer():
    """Lazily load the tokenizer for token counting."""
    global _tokenizer
    if _tokenizer is None:
        log.info(f'Loading tokenizer: {OCR["tokenizer_model"]}')
        _tokenizer = AutoTokenizer.from_pretrained(
            OCR["tokenizer_model"], trust_remote_code=True
        )
    return _tokenizer


def _build_front_matter(document_name, source_file, num_pages, text):
    """Build YAML front matter with metadata and token count."""
    tokenizer = _get_tokenizer()
    tokens = len(tokenizer.encode(text))
    return (
        f"---\n"
        f"document: {document_name}\n"
        f"source_file: {source_file}\n"
        f"date_processed: {date.today().isoformat()}\n"
        f"pages: {num_pages}\n"
        f"tokens: {tokens}\n"
        f"---\n\n"
    )


class OCRProcessor:
    """OCR processor using LightOnOCR model."""

    def __init__(self, device=None, load_in_8bit=False):
        """
        Initialize the OCR processor.

        Args:
            device: Device to use ('cuda', 'cpu', or None for auto-detect)
            load_in_8bit: Load model in 8-bit quantization (saves VRAM)
        """
        self.device = device or ('cuda' if torch.cuda.is_available() else 'cpu')
        self.load_in_8bit = load_in_8bit
        self.model = None
        self.processor = None

        log.info(f"OCR Processor initialized (device: {self.device})")

    def load_model(self):
        """Load the LightOnOCR model and processor."""
        if self.model is not None:
            log.debug("Model already loaded")
            return

        log.info(f'Loading model: {OCR["model_id"]}')

        self.processor = AutoProcessor.from_pretrained(OCR["model_id"])

        if self.load_in_8bit and self.device == 'cuda':
            self.model = AutoModelForImageTextToText.from_pretrained(
                OCR["model_id"],
                torch_dtype=torch.float16,
                load_in_8bit=True,
                device_map="auto"
            )
        else:
            self.model = AutoModelForImageTextToText.from_pretrained(
                OCR["model_id"],
                torch_dtype=torch.float16 if self.device == 'cuda' else torch.float32
            )
            self.model.to(self.device)

        log.info("Model loaded successfully")

    def unload_model(self):
        """Unload model to free memory."""
        if self.model is not None:
            del self.model
            del self.processor
            self.model = None
            self.processor = None
            if self.device == 'cuda':
                torch.cuda.empty_cache()
            log.info("Model unloaded")

    def process_image(self, image, prompt=None):
        """
        Process a single image and extract text.

        Args:
            image: PIL Image or path to image file
            prompt: Optional prompt for the model (default: Spanish OCR prompt)

        Returns:
            Extracted text string
        """
        if self.model is None:
            self.load_model()

        if prompt is None:
            prompt = OCR["default_prompt"]

        # Accept both PIL Image and path
        if isinstance(image, (str, Path)):
            image = Image.open(image).convert("RGB")
        elif not isinstance(image, Image.Image):
            raise ValueError("image must be PIL Image or path")
        else:
            image = image.convert("RGB")

        # LightOnOCR expects image-only input; adding text prompts causes hallucination
        content = [{"type": "image", "image": image}]
        if prompt:
            content.append({"type": "text", "text": prompt})

        messages = [{"role": "user", "content": content}]

        inputs = self.processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt"
        ).to(self.device)

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=OCR["max_new_tokens"],
                do_sample=False
            )

        generated_ids = outputs[0][inputs['input_ids'].shape[1]:]
        text = self.processor.decode(generated_ids, skip_special_tokens=True)

        return text.strip()

    def process_pdf(self, pdf_path, output_path=None, dpi=200):
        """
        Process a PDF directly without saving intermediate images.
        Streams pages from PDF → crop → OCR → text.

        Args:
            pdf_path: Path to the PDF file
            output_path: Path to save the combined text (optional)
            dpi: Resolution for PDF conversion

        Returns:
            Combined text from all pages
        """
        pdf_path = Path(pdf_path)

        log.info(f"Processing PDF directly: {pdf_path.name}")

        pages_text = []
        for page_num, cropped_image in tqdm(
            stream_pdf_pages(pdf_path, dpi=dpi),
            desc=f"OCR {pdf_path.stem}"
        ):
            try:
                text = self.process_image(cropped_image)
                pages_text.append(f"<!-- Page: {page_num} -->\n{text}")
            except Exception as e:
                log.error(f"Error processing page {page_num}: {e}")
                pages_text.append(f"<!-- Page: {page_num} - ERROR -->\n")

        combined_text = "\n\n---\n\n".join(pages_text)

        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            doc_name = pdf_path.stem.replace("-", " ").title()
            front_matter = _build_front_matter(
                doc_name, pdf_path.name, len(pages_text), combined_text
            )
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(front_matter + combined_text)
            log.info(f"Saved OCR output to {output_path}")

        return combined_text

    def process_document(self, image_dir, output_path=None):
        """
        Process all images in a directory (one document).

        Args:
            image_dir: Directory containing page images
            output_path: Path to save the combined text (optional)

        Returns:
            Combined text from all pages
        """
        image_dir = Path(image_dir)
        images = sorted(image_dir.glob("*.png"))

        if not images:
            log.warning(f"No images found in {image_dir}")
            return ""

        log.info(f"Processing {len(images)} pages from {image_dir.name}")

        pages_text = []
        for image_path in tqdm(images, desc=f"OCR {image_dir.name}"):
            try:
                text = self.process_image(image_path)
                pages_text.append(f"<!-- Page: {image_path.stem} -->\n{text}")
            except Exception as e:
                log.error(f"Error processing {image_path}: {e}")
                pages_text.append(f"<!-- Page: {image_path.stem} - ERROR -->\n")

        combined_text = "\n\n---\n\n".join(pages_text)

        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            doc_name = image_dir.name.replace("-", " ").title()
            source_file = f"{image_dir.name}.pdf"
            front_matter = _build_front_matter(
                doc_name, source_file, len(pages_text), combined_text
            )
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(front_matter + combined_text)
            log.info(f"Saved OCR output to {output_path}")

        return combined_text


def process_all_pdfs(input_dir=None, output_dir=None, dpi=200):
    """
    Process all PDFs directly (streaming, no intermediate images).

    Args:
        input_dir: Directory containing PDFs (default: data/raw/)
        output_dir: Directory to save text files (default: data/processed/)
        dpi: Resolution for PDF conversion

    Returns:
        Dict mapping PDF names to output paths
    """
    if input_dir is None:
        input_dir = PATHS['raw']
    else:
        input_dir = Path(input_dir)

    if output_dir is None:
        output_dir = PATHS['processed']
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = list(input_dir.glob("*.pdf"))

    if not pdf_files:
        log.warning(f"No PDFs found in {input_dir}")
        return {}

    log.info(f"Found {len(pdf_files)} PDFs to process")

    ocr = OCRProcessor()
    ocr.load_model()

    results = {}
    for pdf_path in pdf_files:
        output_path = output_dir / f"{pdf_path.stem}_ocr.md"
        try:
            ocr.process_pdf(pdf_path, output_path, dpi=dpi)
            results[pdf_path.stem] = output_path
        except Exception as e:
            log.error(f"Failed to process {pdf_path.name}: {e}")
            results[pdf_path.stem] = None

    ocr.unload_model()
    return results


def process_all_documents(image_base_dir=None, output_dir=None):
    """
    Process all documents from saved images (for testing).
    For production, use process_all_pdfs() instead.

    Args:
        image_base_dir: Base directory with document folders (default: data/temp/)
        output_dir: Directory to save text files (default: data/processed/)

    Returns:
        Dict mapping document names to output paths
    """
    if image_base_dir is None:
        image_base_dir = PATHS['temp']
    else:
        image_base_dir = Path(image_base_dir)

    if output_dir is None:
        output_dir = PATHS['processed']
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    doc_dirs = [d for d in image_base_dir.iterdir() if d.is_dir()]

    if not doc_dirs:
        log.warning(f"No document directories found in {image_base_dir}")
        return {}

    log.info(f"Found {len(doc_dirs)} documents to process")

    ocr = OCRProcessor()
    ocr.load_model()

    results = {}
    for doc_dir in doc_dirs:
        output_path = output_dir / f"{doc_dir.name}_ocr.md"
        try:
            ocr.process_document(doc_dir, output_path)
            results[doc_dir.name] = output_path
        except Exception as e:
            log.error(f"Failed to process {doc_dir.name}: {e}")
            results[doc_dir.name] = None

    ocr.unload_model()
    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # Process single PDF
        ocr = OCRProcessor()
        ocr.process_pdf(sys.argv[1], dpi=200)
    else:
        # Process all PDFs
        process_all_pdfs()
