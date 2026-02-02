import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import hashlib

from src.utils.logger import log
from config.settings import settings
from src.scraper.rfebm_scraper import RFEBMScraper
from src.scraper.pdf_downloader import PDFDownloader


class VersionTracker:
    """
    Sistema de control de versiones para documentos de normativa.
    Mantiene historial, detecta cambios y actualiza automáticamente.
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        """
        Inicializa el tracker de versiones.
        
        Args:
            db_path: Ruta al archivo JSON de versiones. Si es None, usa default
        """
        self.db_path = db_path or (settings.raw_path / "versions.json")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.versions_db = self._load_database()
        log.info(f'VersionTracker inicializado. Base de datos: {self.db_path}')
    
    def _load_database(self) -> Dict:
        """
        Carga la base de datos de versiones desde JSON.
        
        Returns:
            Dict con la estructura de la base de datos
        """
        if not self.db_path.exists():
            log.info('Base de datos de versiones no existe, creando nueva')
            return {
                "version": "1.0.0",
                "last_check": None,
                "check_interval_days": 90,  # 3 meses
                "documents": []
            }
        
        try:
            with open(self.db_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                log.info(f'Base de datos cargada: {len(data.get("documents", []))} documentos')
                return data
        except Exception as e:
            log.error(f'Error cargando base de datos: {e}')
            return {
                "version": "1.0.0",
                "last_check": None,
                "check_interval_days": 90,
                "documents": []
            }
    
    def _save_database(self):
        """Guarda la base de datos de versiones en JSON."""
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(self.versions_db, f, indent=2, ensure_ascii=False)
            log.info(f'Base de datos guardada: {self.db_path}')
        except Exception as e:
            log.error(f'Error guardando base de datos: {e}')
    
    def _find_document(self, doc_id: str) -> Optional[Dict]:
        """
        Busca un documento en la base de datos por ID.
        
        Args:
            doc_id: ID del documento
            
        Returns:
            Dict con información del documento o None
        """
        for doc in self.versions_db.get("documents", []):
            if doc.get("id") == doc_id:
                return doc
        return None
    
    def _generate_doc_id(self, pdf_info: Dict) -> str:
        """
        Genera un ID único para un documento.
        
        Args:
            pdf_info: Información del PDF
            
        Returns:
            ID del documento (ej: "rj-2025")
        """
        version = pdf_info.get('version', 'unknown')
        
        title = pdf_info.get('title', '').lower()
        filename = pdf_info.get('filename', '').lower()
        
        if 'reglas' in title or 'reglas' in filename:
            tipo = 'rj'  # Reglas de Juego
        else:
            tipo = 'doc'
        
        return f"{tipo}-{version}"
    
    def needs_check(self) -> bool:
        """
        Determina si es necesario realizar una comprobación de actualizaciones.
        
        Returns:
            True si han pasado más de check_interval_days desde la última comprobación
        """
        last_check = self.versions_db.get('last_check')
        if not last_check:
            log.info('No hay registro de última comprobación, se requiere check')
            return True
        
        try:
            last_check_date = datetime.fromisoformat(last_check)
            interval_days = self.versions_db.get('check_interval_days', 90)
            next_check_date = last_check_date + timedelta(days=interval_days)
            now = datetime.now()
            
            if now >= next_check_date:
                days_since = (now - last_check_date).days
                log.info(f'Han pasado {days_since} días desde el último check (intervalo: {interval_days})')
                return True
            else:
                days_until = (next_check_date - now).days
                log.info(f'Próximo check en {days_until} días')
                return False
        except Exception as e:
            log.error(f'Error verificando fecha de último check: {e}')
            return True
    
    def check_for_updates(self, force: bool = False) -> Dict:
        """
        Comprueba si hay actualizaciones disponibles en la web.
        
        Args:
            force: Si True, fuerza la comprobación sin importar el intervalo
            
        Returns:
            Dict con información de actualizaciones encontradas
        """
        if not force and not self.needs_check():
            log.info('No es necesario realizar comprobación todavía')
            return {
                'check_performed': False,
                'reason': 'interval_not_reached',
                'updates_available': []
            }
        
        log.info('=== Iniciando comprobación de actualizaciones ===')
        
        self.versions_db['last_check'] = datetime.now().isoformat()
        self._save_database()
        
        with RFEBMScraper() as scraper:
            available_pdfs = scraper.scrape_pdf_links()
        
        reglas_juego = []
        for pdf in available_pdfs:
            title = pdf.get('title', '').lower()
            filename = pdf.get('filename', '').lower()
            
            is_rules = ('reglas' in title or 'reglas' in filename) and \
                       ('juego' in title or 'juego' in filename)
            is_excluded = 'playa' in title or 'playa' in filename or \
                         'erratas' in title or 'erratas' in filename
            
            if is_rules and not is_excluded:
                reglas_juego.append(pdf)
        
        if not reglas_juego:
            log.warning('No se encontraron PDFs de Reglas de Juego')
            return {
                'check_performed': True,
                'updates_available': [],
                'error': 'no_pdfs_found'
            }
        
        reglas_juego.sort(
            key=lambda x: int(x.get('version', '0')) if x.get('version', '').isdigit() else 0,
            reverse=True
        )
        latest_pdf = reglas_juego[0]
        
        doc_id = self._generate_doc_id(latest_pdf)
        existing_doc = self._find_document(doc_id)
        
        updates = []
        
        if not existing_doc:
            log.info(f'Nuevo documento detectado: {latest_pdf["title"]} (versión {latest_pdf["version"]})')
            updates.append({
                'type': 'new',
                'document': latest_pdf,
                'doc_id': doc_id
            })
        else:
            if existing_doc.get('url') != latest_pdf.get('url'):
                log.info(f'URL actualizada para {doc_id}')
                updates.append({
                    'type': 'url_changed',
                    'document': latest_pdf,
                    'doc_id': doc_id,
                    'old_url': existing_doc.get('url')
                })
            else:
                log.info(f'No hay cambios para {doc_id}')
        
        result = {
            'check_performed': True,
            'check_date': self.versions_db['last_check'],
            'updates_available': updates,
            'total_updates': len(updates)
        }
        
        log.info(f'=== Comprobación completada: {len(updates)} actualizaciones encontradas ===')
        return result
    
    def download_updates(self, updates: List[Dict], force: bool = False) -> List[Dict]:
        """
        Descarga actualizaciones disponibles.
        
        Args:
            updates: Lista de actualizaciones (del check_for_updates)
            force: Si True, fuerza la descarga incluso si ya existe
            
        Returns:
            Lista de resultados de descarga
        """
        if not updates:
            log.info('No hay actualizaciones para descargar')
            return []
        
        log.info(f'=== Descargando {len(updates)} actualizaciones ===')
        
        downloader = PDFDownloader()
        results = []
        
        for update in updates:
            pdf_info = update['document']
            doc_id = update['doc_id']
            
            log.info(f'Descargando {doc_id}: {pdf_info["title"]}')
            
            result = downloader.download_pdf(pdf_info, force=force)
            
            if result:
                self._update_document_record(doc_id, pdf_info, result)
                results.append({
                    'doc_id': doc_id,
                    'success': True,
                    'result': result
                })
            else:
                log.error(f'Error descargando {doc_id}')
                results.append({
                    'doc_id': doc_id,
                    'success': False
                })
        
        self._save_database()
        log.info(f'=== Descargas completadas: {len([r for r in results if r["success"]])} exitosas ===')
        return results
    
    def _update_document_record(self, doc_id: str, pdf_info: Dict, download_result: Dict):
        """
        Actualiza el registro de un documento en la base de datos.
        
        Args:
            doc_id: ID del documento
            pdf_info: Información del PDF (del scraper)
            download_result: Resultado de la descarga
        """
        existing_doc = self._find_document(doc_id)
        
        new_record = {
            "id": doc_id,
            "title": pdf_info.get('title'),
            "url": pdf_info.get('url'),
            "filename": download_result.get('filename'),
            "version": pdf_info.get('version'),
            "download_date": download_result.get('download_date', datetime.now().isoformat()),
            "checksum": download_result.get('checksum'),
            "file_size": download_result.get('size'),
            "source_pdf": pdf_info.get('filename')
        }
        
        if existing_doc:
            if 'history' not in existing_doc:
                existing_doc['history'] = []
            
            existing_doc['history'].append({
                "download_date": existing_doc.get('download_date'),
                "checksum": existing_doc.get('checksum'),
                "url": existing_doc.get('url')
            })
            
            existing_doc.update(new_record)
            log.info(f'Documento {doc_id} actualizado (historial: {len(existing_doc["history"])} versiones)')
        else:
            new_record['history'] = []
            self.versions_db['documents'].append(new_record)
            log.info(f'Nuevo documento {doc_id} agregado a la base de datos')
    
    def auto_update(self, force: bool = False) -> Dict:
        """
        Proceso completo de auto-actualización: comprueba y descarga si es necesario.
        
        Args:
            force: Si True, fuerza comprobación y descarga
            
        Returns:
            Dict con resumen del proceso
        """
        log.info('=== Iniciando proceso de auto-actualización ===')
        
        check_result = self.check_for_updates(force=force)
        
        if not check_result.get('check_performed'):
            return {
                'success': True,
                'action': 'no_check_needed',
                'next_check': self._calculate_next_check()
            }
        
        updates = check_result.get('updates_available', [])
        
        if not updates:
            log.info('No hay actualizaciones disponibles')
            return {
                'success': True,
                'action': 'no_updates',
                'check_date': check_result.get('check_date'),
                'next_check': self._calculate_next_check()
            }
        
        download_results = self.download_updates(updates, force=force)
        
        successful = len([r for r in download_results if r['success']])
        
        return {
            'success': True,
            'action': 'updated',
            'check_date': check_result.get('check_date'),
            'updates_found': len(updates),
            'downloads_successful': successful,
            'downloads_failed': len(download_results) - successful,
            'next_check': self._calculate_next_check()
        }
    
    def _calculate_next_check(self) -> str:
        """
        Calcula la fecha del próximo check.
        
        Returns:
            Fecha en formato ISO del próximo check
        """
        last_check = self.versions_db.get('last_check')
        if not last_check:
            return 'unknown'
        
        try:
            last_check_date = datetime.fromisoformat(last_check)
            interval_days = self.versions_db.get('check_interval_days', 90)
            next_check = last_check_date + timedelta(days=interval_days)
            return next_check.isoformat()
        except:
            return 'unknown'
    
    def get_status(self) -> Dict:
        """
        Obtiene el estado actual del sistema de versiones.
        
        Returns:
            Dict con información de estado
        """
        return {
            'database_path': str(self.db_path),
            'last_check': self.versions_db.get('last_check'),
            'next_check': self._calculate_next_check(),
            'check_interval_days': self.versions_db.get('check_interval_days'),
            'total_documents': len(self.versions_db.get('documents', [])),
            'documents': self.versions_db.get('documents', [])
        }


def main():
    """Función de prueba del tracker de versiones."""
    log.info('=== Iniciando prueba del tracker de versiones ===')
    
    tracker = VersionTracker()
    
    status = tracker.get_status()
    print(f'\n📊 Estado del sistema:')
    print(f'   Última comprobación: {status["last_check"] or "Nunca"}')
    print(f'   Próxima comprobación: {status["next_check"]}')
    print(f'   Intervalo: {status["check_interval_days"]} días (3 meses)')
    print(f'   Documentos registrados: {status["total_documents"]}')
    
    if status['documents']:
        print(f'\n📄 Documentos en base de datos:')
        for doc in status['documents']:
            print(f'   • {doc["id"]}: {doc["title"]} (versión {doc["version"]})')
            print(f'     Descargado: {doc["download_date"]}')
            if doc.get('history'):
                print(f'     Historial: {len(doc["history"])} versiones anteriores')
    
    print(f'\n🔄 Ejecutando auto-actualización...')
    result = tracker.auto_update(force=True)  # Force para testing
    
    print(f'\n✅ Resultado:')
    print(f'   Acción: {result["action"]}')
    if result['action'] == 'updated':
        print(f'   Actualizaciones encontradas: {result["updates_found"]}')
        print(f'   Descargas exitosas: {result["downloads_successful"]}')
    print(f'   Próximo check: {result.get("next_check", "N/A")}')
    
    log.info('=== Prueba completada ===')


if __name__ == '__main__':
    main()
