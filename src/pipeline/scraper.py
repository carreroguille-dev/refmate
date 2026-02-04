"""
Extrae enlaces a PDFs de páginas web.
"""

import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from config.settings import SOURCES, DOWNLOAD
from utils.logger import get_logger

log = get_logger(__name__)


def extract_pdfs():
    """Extrae todos los enlaces a PDFs de las fuentes configuradas."""
    pdfs = []
    
    log.info(f"Iniciando extracción de {len(SOURCES)} fuentes")
    
    for source in SOURCES:
        try:
            headers = {'User-Agent': DOWNLOAD['user_agent']}
            response = requests.get(source['url'], headers=headers, timeout=DOWNLOAD['timeout'])
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            count = 0
            
            for link in soup.find_all('a', href=True):
                href = link['href']
                if href.lower().endswith('.pdf'):
                    pdf_url = urljoin(source['url'], href)
                    if pdf_url not in pdfs:
                        pdfs.append(pdf_url)
                        count += 1
            
            log.info(f"{source['name']}: {count} PDFs encontrados")
                        
        except Exception as e:
            log.error(f"Error procesando {source['url']}: {e}")
    
    log.info(f"Total PDFs extraídos: {len(pdfs)}")
    return pdfs


def filter_pdfs(pdfs, patterns):
    """Filtra PDFs que coincidan con los patrones dados."""
    filtered = []
    
    for pdf in pdfs:
        filename = pdf.split('/')[-1].lower()
        for pattern in patterns:
            if re.search(pattern, filename, re.IGNORECASE):
                filtered.append(pdf)
                break
    
    log.info(f"PDFs filtrados: {len(filtered)} de {len(pdfs)} (patrones: {patterns})")
    return filtered