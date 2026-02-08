"""Extract all cropped images from PDFs to temp folder."""
import sys
sys.path.insert(0, '.')

from src.pipeline.pdf_processor import process_all_pdfs

print("Extracting cropped images from all PDFs...")
results = process_all_pdfs(dpi=200)

print("\nResults:")
for pdf_name, images in results.items():
    print(f"  {pdf_name}: {len(images)} images")
print(f"\nImages saved to data/temp/")
