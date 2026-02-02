import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict
import requests

from src.utils.logger import log
from config.settings import settings


class PDFDownloader:
    """
    Descargador de PDFs con nomenclatura consistente y verificación de duplicados.
    """
    
    def __init__(self, output_dir: Optional[Path] = None):
        """
        Inicializa el descargador.
        
        Args:
            output_dir: Directorio de salida para los PDFs. Si es None, usa settings.raw_path
        """
        self.output_dir = output_dir or settings.raw_path
        self.output_dir.mkdir(parents=True, exist_ok=True)
        log.info(f'PDFDownloader inicializado. Directorio de salida: {self.output_dir}')
    
    def _calculate_checksum(self, file_path: Path) -> str:
        """
        Calcula el checksum SHA256 de un archivo.
        
        Args:
            file_path: Ruta al archivo
            
        Returns:
            Checksum en formato hexadecimal
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for byte_block in iter(lambda: f.read(4096), b''):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def _calculate_checksum_from_content(self, content: bytes) -> str:
        """
        Calcula el checksum SHA256 de contenido en memoria.
        
        Args:
            content: Contenido en bytes
            
        Returns:
            Checksum en formato hexadecimal
        """
        return hashlib.sha256(content).hexdigest()
    
    def _generate_filename(self, pdf_info: Dict) -> str:
        """
        Genera un nombre de archivo consistente.
        
        Formato: {tipo}_{version}_{fecha}.pdf
        Ejemplo: reglas-juego_2025_20260202.pdf
        
        Args:
            pdf_info: Información del PDF del scraper
            
        Returns:
            Nombre de archivo generado
        """
        # Extraer tipo del título o filename
        title = pdf_info.get('title', '').lower()
        filename = pdf_info.get('filename', '').lower()
        
        # Determinar el tipo
        if 'reglas' in title or 'reglas' in filename:
            tipo = 'reglas-juego'
        else:
            tipo = 'reglamento'
        
        # Extraer versión
        version = pdf_info.get('version', 'unknown')
        
        # Fecha actual en formato YYYYMMDD
        fecha = datetime.now().strftime('%Y%m%d')
        
        return f"{tipo}_{version}_{fecha}.pdf"
    
    def _file_exists_with_same_content(self, file_path: Path, content: bytes) -> bool:
        """
        Verifica si un archivo ya existe con el mismo contenido.
        
        Args:
            file_path: Ruta del archivo a verificar
            content: Contenido a comparar
            
        Returns:
            True si el archivo existe y tiene el mismo contenido
        """
        if not file_path.exists():
            return False
        
        # Calcular checksums
        existing_checksum = self._calculate_checksum(file_path)
        new_checksum = self._calculate_checksum_from_content(content)
        
        if existing_checksum == new_checksum:
            log.info(f'Archivo duplicado detectado: {file_path.name} (checksum: {existing_checksum[:8]}...)')
            return True
        
        log.warning(f'Archivo existente con contenido diferente: {file_path.name}')
        return False
    
    def download_pdf(self, pdf_info: Dict, force: bool = False) -> Optional[Dict]:
        """
        Descarga un PDF y lo guarda con nomenclatura consistente.
        
        Args:
            pdf_info: Información del PDF (del scraper)
            force: Si True, descarga incluso si ya existe
            
        Returns:
            Dict con información de la descarga o None si falla
        """
        url = pdf_info.get('url')
        if not url:
            log.error('No se proporcionó URL para descargar')
            return None
        
        # Generar nombre de archivo
        filename = self._generate_filename(pdf_info)
        output_path = self.output_dir / filename
        
        log.info(f'Descargando PDF: {pdf_info.get("title", "Sin título")}')
        log.info(f'URL: {url}')
        log.info(f'Destino: {output_path}')
        
        try:
            # Descargar contenido
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            
            content = response.content
            file_size = len(content)
            
            log.info(f'Descarga completada: {file_size / 1024 / 1024:.2f} MB')
            
            # Verificar duplicados si no es forzado
            if not force and self._file_exists_with_same_content(output_path, content):
                log.info(f'Archivo ya existe y es idéntico, omitiendo escritura')
                return {
                    'filename': filename,
                    'path': str(output_path),
                    'size': file_size,
                    'checksum': self._calculate_checksum_from_content(content),
                    'url': url,
                    'downloaded': False,
                    'reason': 'duplicate'
                }
            
            # Guardar archivo
            with open(output_path, 'wb') as f:
                f.write(content)
            
            # Calcular checksum
            checksum = self._calculate_checksum(output_path)
            
            log.info(f'PDF guardado exitosamente: {output_path}')
            log.info(f'Checksum: {checksum[:16]}...')
            
            return {
                'filename': filename,
                'path': str(output_path),
                'size': file_size,
                'checksum': checksum,
                'url': url,
                'downloaded': True,
                'download_date': datetime.now().isoformat()
            }
            
        except requests.exceptions.Timeout:
            log.error(f'Timeout al descargar {url}')
        except requests.exceptions.HTTPError as e:
            log.error(f'Error HTTP al descargar {url}: {e}')
        except requests.exceptions.RequestException as e:
            log.error(f'Error de red al descargar {url}: {e}')
        except IOError as e:
            log.error(f'Error al guardar archivo {output_path}: {e}')
        except Exception as e:
            log.error(f'Error inesperado al descargar PDF: {e}')
        
        return None
    
    def download_reglas_juego(self, pdf_links: list, force: bool = False) -> Optional[Dict]:
        """
        Descarga específicamente el PDF de Reglas de Juego más reciente.
        
        Args:
            pdf_links: Lista de PDFs del scraper
            force: Si True, descarga incluso si ya existe
            
        Returns:
            Dict con información de la descarga o None si no se encuentra
        """
        # Filtrar solo Reglas de Juego (no playa, no erratas)
        reglas_juego = []
        for pdf in pdf_links:
            title = pdf.get('title', '').lower()
            filename = pdf.get('filename', '').lower()
            
            # Verificar que es Reglas de Juego
            is_rules = ('reglas' in title or 'reglas' in filename) and \
                       ('juego' in title or 'juego' in filename)
            
            # Excluir playa y erratas
            is_excluded = 'playa' in title or 'playa' in filename or \
                         'erratas' in title or 'erratas' in filename or \
                         'beach' in title or 'beach' in filename
            
            if is_rules and not is_excluded:
                reglas_juego.append(pdf)
        
        if not reglas_juego:
            log.error('No se encontró ningún PDF de Reglas de Juego')
            return None
        
        # Ordenar por versión (más reciente primero)
        reglas_juego.sort(
            key=lambda x: int(x.get('version', '0')) if x.get('version', '').isdigit() else 0,
            reverse=True
        )
        
        # Tomar el más reciente
        reglas_mas_reciente = reglas_juego[0]
        
        log.info(f'PDF de Reglas de Juego identificado: {reglas_mas_reciente.get("title")} (versión {reglas_mas_reciente.get("version")})')
        
        # Descargar
        return self.download_pdf(reglas_mas_reciente, force=force)


def main():
    """Función de prueba del descargador."""
    from src.scraper.rfebm_scraper import RFEBMScraper
    
    log.info('=== Iniciando prueba del descargador de PDFs ===')
    
    # Obtener lista de PDFs
    with RFEBMScraper() as scraper:
        pdf_links = scraper.scrape_pdf_links()
    
    if not pdf_links:
        log.error('No se encontraron PDFs')
        return
    
    # Descargar Reglas de Juego
    downloader = PDFDownloader()
    result = downloader.download_reglas_juego(pdf_links)
    
    if result:
        print(f'\n✅ Descarga exitosa:')
        print(f'   Archivo: {result["filename"]}')
        print(f'   Ruta: {result["path"]}')
        print(f'   Tamaño: {result["size"] / 1024 / 1024:.2f} MB')
        print(f'   Checksum: {result["checksum"][:16]}...')
        print(f'   Descargado: {"Sí" if result["downloaded"] else "No (duplicado)"}')
    else:
        print('\n❌ Error en la descarga')
    
    log.info('=== Prueba completada ===')


if __name__ == '__main__':
    main()
