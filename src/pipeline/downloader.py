import json
import requests
import hashlib
from datetime import datetime

from config.settings import PATHS, DOWNLOAD
from utils.logger import get_logger

log = get_logger(__name__)


def load_metadata(filename='downloads_metadata.json'):
    """
    Carga metadatos existentes.
    
    Returns:
        list: Metadatos guardados o lista vacía si no existe.
    """
    metadata_path = PATHS['raw'] / filename
    
    if metadata_path.exists():
        with open(metadata_path, 'r', encoding='utf-8') as f:
            log.info(f"Metadatos cargados: {metadata_path}")
            return json.load(f)
    
    log.info("No existen metadatos previos")
    return []


def filter_new_urls(urls, existing_metadata):
    """
    Filtra URLs que ya fueron descargadas.
    
    Args:
        urls: Lista de URLs a descargar.
        existing_metadata: Metadatos de descargas anteriores.
    
    Returns:
        list: URLs que no están en los metadatos.
    """
    existing_urls = {item['url'] for item in existing_metadata}
    new_urls = [url for url in urls if url not in existing_urls]
    log.info(f"URLs nuevas: {len(new_urls)} de {len(urls)} totales")

    return new_urls


def download_pdfs(urls):
    """
    Descarga una lista de PDFs.
    
    Returns:
        list: Dicts con 'path' y 'url' de cada PDF descargado.
    """
    output_dir = PATHS['raw']
    output_dir.mkdir(parents=True, exist_ok=True)
    
    downloaded = []
    
    for url in urls:
        try:
            headers = {'User-Agent': DOWNLOAD['user_agent']}
            response = requests.get(url, headers=headers, timeout=DOWNLOAD['timeout'], stream=True)
            response.raise_for_status()
            
            filename = url.split('/')[-1].split('?')[0]
            if not filename.endswith('.pdf'):
                filename += '.pdf'
            
            output_path = output_dir / filename
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            downloaded.append({
                'path': output_path,
                'url': url
            })
            log.info(f"Descargado: {filename}")
            
        except Exception as e:
            log.error(f"Error descargando {url}: {e}")
    
    return downloaded


def extract_metadata(downloaded):
    """
    Extrae metadatos de los PDFs descargados.
    
    Args:
        downloaded: Lista de dicts con 'path' y 'url'
    
    Returns:
        list: Metadatos de cada PDF.
    """
    metadata = []
    
    for item in downloaded:
        path = item['path']
        
        sha256 = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256.update(chunk)
        
        metadata.append({
            'filename': path.name,
            'path': str(path),
            'url': item['url'],
            'sha256': sha256.hexdigest(),
            'download_date': datetime.now().isoformat(),
            'size_kb': path.stat().st_size // 1024
        })
        log.info(f"Metadatos extraídos: {path.name} ({path.stat().st_size // 1024} KB)")
    
    return metadata


def save_metadata(metadata, filename='downloads_metadata.json'):
    """
    Guarda los metadatos en un fichero JSON.
    
    Args:
        metadata: Lista de metadatos.
        filename: Nombre del fichero JSON.
    
    Returns:
        Path del fichero guardado.
    """
    output_path = PATHS['raw'] / filename
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    log.info(f"Metadatos guardados: {output_path} ({len(metadata)} documentos)")
    return output_path