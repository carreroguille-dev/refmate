from src.pipeline.scraper import extract_pdfs, filter_pdfs
from src.pipeline.downloader import (
    load_metadata, 
    filter_new_urls, 
    download_pdfs, 
    extract_metadata, 
    save_metadata
)
from utils.logger import get_logger

log = get_logger(__name__)


def test_flow():
    """Ejecuta el flujo completo de scraping y descarga."""
    
    log.info("=" * 50)
    log.info("INICIANDO TEST DEL FLUJO")
    log.info("=" * 50)
    
    log.info("PASO 1: Extrayendo PDFs...")
    pdfs = extract_pdfs()
    
    if not pdfs:
        log.error("No se encontraron PDFs")
        return False
    
    log.info("PASO 2: Filtrando PDFs...")
    patterns = ["reglas", "reglamento", "disciplina", "rgc"]
    pdfs = filter_pdfs(pdfs, patterns)
    
    if not pdfs:
        log.error("Ningún PDF coincide con los patrones")
        return False
        
    
    log.info("PASO 3: Cargando metadatos existentes...")
    existing = load_metadata()
    
    log.info("PASO 4: Filtrando duplicados...")
    new_urls = filter_new_urls(pdfs, existing)
    
    if not new_urls:
        log.info("No hay PDFs nuevos para descargar")
        return True
    
    log.info("PASO 5: Descargando PDFs...")
    downloaded = download_pdfs(new_urls)
    
    if not downloaded:
        log.error("No se pudo descargar ningún PDF")
        return False
    
    log.info("PASO 6: Extrayendo metadatos...")
    new_metadata = extract_metadata(downloaded)
    
    log.info("PASO 7: Guardando metadatos...")
    save_metadata(existing + new_metadata)
    
    log.info("=" * 50)
    log.info("TEST COMPLETADO EXITOSAMENTE")
    log.info("=" * 50)
    
    return True


if __name__ == "__main__":
    success = test_flow()
    exit(0 if success else 1)