import requests
from bs4 import BeautifulSoup
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Optional
import re

from src.utils.logger import log
from config.settings import settings

class RFEBMScraper:
    """
    Scraper para obtener PDFs de las Reglas de Juego desde la web de la RFEBM.
    """
    
    BASE_URL = 'https://www.rfebm.com'
    NORMATIVA_URL = 'https://www.rfebm.com/transparencia/normativa-y-reglamentos/'
    
    def __init__(self, max_retries: int = 3, retry_delay: int = 2):
        """
        Inicializa el scraper.
        
        Args:
            max_retries: Número máximo de reintentos ante fallos
            retry_delay: Tiempo de espera entre reintentos (segundos)
        """
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
        })
        log.info(f'Scraper inicializado con max_retries={max_retries}, retry_delay={retry_delay}')
    
    def _make_request(self, url: str, method: str = 'GET', **kwargs) -> Optional[requests.Response]:
        """
        Realiza una petición HTTP con sistema de reintentos.
        
        Args:
            url: URL a consultar
            method: Método HTTP (GET, POST, etc.)
            **kwargs: Parámetros adicionales para requests
            
        Returns:
            Response object o None si falla tras los reintentos
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                log.debug(f'Intento {attempt}/{self.max_retries}: {method} {url}')
                response = self.session.request(method, url, timeout=30, **kwargs)
                response.raise_for_status()
                log.debug(f'Petición exitosa: {url} (status={response.status_code})')
                return response
            
            except requests.exceptions.Timeout:
                log.warning(f'Timeout en intento {attempt}/{self.max_retries}: {url}')
            except requests.exceptions.ConnectionError:
                log.warning(f'Error de conexión en intento {attempt}/{self.max_retries}: {url}')
            except requests.exceptions.HTTPError as e:
                log.warning(f'Error HTTP {e.response.status_code} en intento {attempt}/{self.max_retries}: {url}')
            except requests.exceptions.RequestException as e:
                log.warning(f'Error en petición (intento {attempt}/{self.max_retries}): {str(e)}')
            
            if attempt < self.max_retries:
                wait_time = self.retry_delay * attempt
                log.info(f'Esperando {wait_time}s antes del siguiente intento...')
                time.sleep(wait_time)
        
        log.error(f'Todos los intentos fallaron para: {url}')
        return None
    
    def _extract_metadata_from_link(self, link_element) -> Dict[str, str]:
        """
        Extrae metadatos de un elemento de enlace.
        
        Args:
            link_element: Elemento BeautifulSoup del enlace
            
        Returns:
            Dict con metadatos extraídos
        """
        metadata = {}
        
        # Extraer texto del enlace
        link_text = link_element.get_text(strip=True)
        metadata['link_text'] = link_text
        
        # Intentar extraer año/versión del texto
        year_match = re.search(r'20\d{2}', link_text)
        if year_match:
            metadata['version'] = year_match.group()
        
        # Buscar información adicional en elementos hermanos o padres
        parent = link_element.find_parent()
        if parent:
            parent_text = parent.get_text(strip=True)
            metadata['context'] = parent_text
            
            # Buscar fechas en el contexto
            date_patterns = [
                r'(\d{1,2}[-/]\d{1,2}[-/]\d{4})',  # DD-MM-YYYY o DD/MM/YYYY
                r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})',  # YYYY-MM-DD
            ]
            for pattern in date_patterns:
                date_match = re.search(pattern, parent_text)
                if date_match:
                    metadata['date_found'] = date_match.group()
                    break
        
        return metadata
    
    def _is_rules_pdf(self, url: str, metadata: Dict[str, str]) -> bool:
        """
        Determina si un PDF es de Reglas de Juego.
        
        Args:
            url: URL del PDF
            metadata: Metadatos extraídos
            
        Returns:
            True si es un PDF de Reglas de Juego
        """
        # Palabras clave que indican que es un PDF de Reglas de Juego
        keywords = [
            'regla',
            'juego',
            'rj',
            'rule',
            'game',
            'reglamento'
        ]
        
        # Buscar en URL
        url_lower = url.lower()
        if any(kw in url_lower for kw in keywords):
            return True
        
        # Buscar en texto del enlace
        link_text = metadata.get('link_text', '').lower()
        if any(kw in link_text for kw in keywords):
            return True
        
        # Buscar en contexto
        context = metadata.get('context', '').lower()
        if any(kw in context for kw in keywords):
            return True
        
        return False
    
    def scrape_pdf_links(self) -> List[Dict[str, any]]:
        """
        Extrae todos los enlaces a PDFs de Reglas de Juego de la página de normativa.
        
        Returns:
            Lista de diccionarios con información de cada PDF encontrado
        """
        log.info(f'Iniciando scraping de: {self.NORMATIVA_URL}')
        
        response = self._make_request(self.NORMATIVA_URL)
        if not response:
            log.error('No se pudo obtener la página de normativa')
            return []
        
        soup = BeautifulSoup(response.content, 'html.parser')
        pdf_links = []
        
        # Buscar todos los enlaces a PDFs
        all_links = soup.find_all('a', href=True)
        log.info(f'Encontrados {len(all_links)} enlaces en la página')
        
        for link in all_links:
            href = link['href']
            
            # Verificar si es un PDF
            if not href.lower().endswith('.pdf'):
                continue
            
            # Construir URL absoluta
            absolute_url = urljoin(self.BASE_URL, href)
            
            # Extraer metadatos
            metadata = self._extract_metadata_from_link(link)
            
            # Verificar si es un PDF de Reglas de Juego
            if not self._is_rules_pdf(absolute_url, metadata):
                log.debug(f'PDF descartado (no es de Reglas de Juego): {absolute_url}')
                continue
            
            # Extraer nombre del archivo de la URL
            parsed_url = urlparse(absolute_url)
            filename = Path(parsed_url.path).name
            
            # Intentar extraer versión si no se encontró antes
            if 'version' not in metadata:
                version_match = re.search(r'20\d{2}', filename)
                if version_match:
                    metadata['version'] = version_match.group()
                else:
                    metadata['version'] = 'unknown'
            
            pdf_info = {
                'url': absolute_url,
                'filename': filename,
                'title': metadata.get('link_text', filename),
                'version': metadata.get('version', 'unknown'),
                'date_found': metadata.get('date_found'),
                'scraped_at': datetime.now().isoformat(),
                'metadata': metadata
            }
            
            pdf_links.append(pdf_info)
            log.info(f'PDF encontrado: {filename} (versión: {pdf_info["version"]})')
        
        log.info(f'Scraping completado: {len(pdf_links)} PDFs de Reglas de Juego encontrados')
        return pdf_links
    
    def get_pdf_metadata(self, pdf_url: str) -> Optional[Dict[str, any]]:
        """
        Obtiene metadatos adicionales de un PDF sin descargarlo completamente.
        
        Args:
            pdf_url: URL del PDF
            
        Returns:
            Dict con metadatos o None si falla
        """
        log.debug(f'Obteniendo metadatos de: {pdf_url}')
        
        try:
            response = self._make_request(pdf_url, method='HEAD')
            if not response:
                return None
            
            metadata = {
                'content_type': response.headers.get('Content-Type'),
                'content_length': int(response.headers.get('Content-Length', 0)),
                'last_modified': response.headers.get('Last-Modified'),
                'etag': response.headers.get('ETag'),
            }
            
            log.debug(f'Metadatos obtenidos: tamaño={metadata["content_length"]} bytes')
            return metadata
            
        except Exception as e:
            log.error(f'Error obteniendo metadatos de {pdf_url}: {str(e)}')
            return None
    
    def close(self):
        """Cierra la sesión del scraper."""
        self.session.close()
        log.info('Sesión del scraper cerrada')
    
    def __enter__(self):
        """Context manager enter."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


def main():
    """Función de prueba del scraper."""
    log.info('=== Iniciando prueba del scraper RFEBM ===')
    
    with RFEBMScraper() as scraper:
        # Buscar PDFs
        pdfs = scraper.scrape_pdf_links()
        
        # Mostrar resultados
        print(f'\n📄 PDFs encontrados: {len(pdfs)}\n')
        for i, pdf in enumerate(pdfs, 1):
            print(f'{i}. {pdf["title"]}')
            print(f'   URL: {pdf["url"]}')
            print(f'   Versión: {pdf["version"]}')
            print(f'   Archivo: {pdf["filename"]}')
            
            # Obtener metadatos adicionales
            meta = scraper.get_pdf_metadata(pdf['url'])
            if meta:
                size_mb = meta['content_length'] / (1024 * 1024)
                print(f'   Tamaño: {size_mb:.2f} MB')
            print()
    
    log.info('=== Prueba completada ===')


if __name__ == '__main__':
    main()
