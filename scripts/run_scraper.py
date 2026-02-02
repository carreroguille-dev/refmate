"""
Script CLI para ejecutar el scraper de RFEBM.
Permite scrapear, descargar y gestionar versiones de Reglas de Juego.
"""
import sys
import argparse
from pathlib import Path

# Agregar el directorio raíz al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scraper.rfebm_scraper import RFEBMScraper
from src.scraper.pdf_downloader import PDFDownloader
from src.scraper.version_tracker import VersionTracker
from src.utils.logger import log


def scrape_only(args):
    """Ejecuta solo el scraping sin descargar."""
    log.info('=== Modo: Solo Scraping ===')
    
    with RFEBMScraper() as scraper:
        pdfs = scraper.scrape_pdf_links()
    
    if not pdfs:
        print('\n❌ No se encontraron PDFs')
        return 1
    
    # Filtrar solo Reglas de Juego
    reglas_juego = []
    for pdf in pdfs:
        title = pdf.get('title', '').lower()
        filename = pdf.get('filename', '').lower()
        
        is_rules = ('reglas' in title or 'reglas' in filename) and \
                   ('juego' in title or 'juego' in filename)
        is_excluded = 'playa' in title or 'playa' in filename or \
                     'erratas' in title or 'erratas' in filename
        
        if is_rules and not is_excluded:
            reglas_juego.append(pdf)
    
    print(f'\n📄 PDFs de Reglas de Juego encontrados: {len(reglas_juego)}\n')
    
    for i, pdf in enumerate(reglas_juego, 1):
        print(f'{i}. {pdf["title"]}')
        print(f'   URL: {pdf["url"]}')
        print(f'   Versión: {pdf["version"]}')
        print(f'   Archivo: {pdf["filename"]}')
        print()
    
    return 0


def download_pdf(args):
    """Descarga el PDF de Reglas de Juego."""
    log.info('=== Modo: Descargar PDF ===')
    
    # Scraping
    with RFEBMScraper() as scraper:
        pdfs = scraper.scrape_pdf_links()
    
    if not pdfs:
        print('\n❌ No se encontraron PDFs')
        return 1
    
    # Descargar
    downloader = PDFDownloader()
    result = downloader.download_reglas_juego(pdfs, force=args.force_download)
    
    if result:
        print(f'\n✅ Descarga completada:')
        print(f'   Archivo: {result["filename"]}')
        print(f'   Ruta: {result["path"]}')
        print(f'   Tamaño: {result["size"] / 1024 / 1024:.2f} MB')
        print(f'   Checksum: {result["checksum"][:16]}...')
        
        if result['downloaded']:
            print(f'   Estado: ✓ Descargado')
        else:
            print(f'   Estado: ℹ️ Ya existía (duplicado)')
        
        return 0
    else:
        print('\n❌ Error en la descarga')
        return 1


def check_updates(args):
    """Comprueba si hay actualizaciones disponibles."""
    log.info('=== Modo: Comprobar Actualizaciones ===')
    
    tracker = VersionTracker()
    
    # Mostrar estado actual
    status = tracker.get_status()
    print(f'\n📊 Estado actual:')
    print(f'   Última comprobación: {status["last_check"] or "Nunca"}')
    print(f'   Próxima comprobación: {status["next_check"]}')
    print(f'   Documentos registrados: {status["total_documents"]}')
    
    if status['documents']:
        print(f'\n📄 Documentos actuales:')
        for doc in status['documents']:
            print(f'   • {doc["id"]}: {doc["title"]} (versión {doc["version"]})')
    
    # Comprobar actualizaciones
    print(f'\n🔍 Comprobando actualizaciones...')
    result = tracker.check_for_updates(force=True)
    
    if not result['check_performed']:
        print('\n⚠️ No se pudo realizar la comprobación')
        return 1
    
    updates = result.get('updates_available', [])
    
    if not updates:
        print('\n✅ No hay actualizaciones disponibles')
        print('   El sistema está actualizado')
        return 0
    
    print(f'\n🆕 Se encontraron {len(updates)} actualizaciones:')
    for update in updates:
        pdf = update['document']
        print(f'\n   • {pdf["title"]}')
        print(f'     Versión: {pdf["version"]}')
        print(f'     Tipo: {update["type"]}')
        
        if update['type'] == 'new':
            print(f'     Estado: Nuevo documento')
        elif update['type'] == 'url_changed':
            print(f'     Estado: URL actualizada')
    
    # Preguntar si descargar
    if not args.auto_download:
        response = input('\n¿Descargar actualizaciones? (s/n): ')
        if response.lower() != 's':
            print('Descarga cancelada')
            return 0
    
    # Descargar actualizaciones
    print('\n📥 Descargando actualizaciones...')
    download_results = tracker.download_updates(updates, force=args.force_download)
    
    successful = len([r for r in download_results if r['success']])
    print(f'\n✅ Descargas completadas: {successful}/{len(updates)}')
    
    return 0


def auto_update_mode(args):
    """Ejecuta el proceso completo de auto-actualización."""
    log.info('=== Modo: Auto-actualización ===')
    
    tracker = VersionTracker()
    result = tracker.auto_update(force=args.force_download)
    
    action = result.get('action')
    
    print(f'\n📋 Resultado de auto-actualización:')
    
    if action == 'no_check_needed':
        print('   ℹ️ No es necesario realizar comprobación todavía')
        print(f'   Próximo check: {result.get("next_check")}')
        return 0
    
    elif action == 'no_updates':
        print('   ✅ Comprobación realizada: No hay actualizaciones')
        print(f'   Próximo check: {result.get("next_check")}')
        return 0
    
    elif action == 'updated':
        print('   ✅ Actualizaciones procesadas exitosamente')
        print(f'   Actualizaciones encontradas: {result.get("updates_found")}')
        print(f'   Descargas exitosas: {result.get("downloads_successful")}')
        if result.get('downloads_failed', 0) > 0:
            print(f'   ⚠️ Descargas fallidas: {result.get("downloads_failed")}')
        print(f'   Próximo check: {result.get("next_check")}')
        return 0
    
    else:
        print(f'   ❌ Resultado inesperado: {action}')
        return 1


def status_mode(args):
    """Muestra el estado del sistema."""
    log.info('=== Modo: Estado del Sistema ===')
    
    tracker = VersionTracker()
    status = tracker.get_status()
    
    print('\n' + '=' * 60)
    print('ESTADO DEL SISTEMA DE VERSIONES')
    print('=' * 60)
    print(f'Última comprobación: {status["last_check"] or "Nunca"}')
    print(f'Próxima comprobación: {status["next_check"]}')
    print(f'Intervalo: {status["check_interval_days"]} días (3 meses)')
    print(f'Documentos registrados: {status["total_documents"]}')
    print()
    
    if status['documents']:
        print('DOCUMENTOS REGISTRADOS:')
        print('-' * 60)
        for doc in status['documents']:
            print(f'\n📄 {doc["id"]}: {doc["title"]}')
            print(f'   Versión: {doc["version"]}')
            print(f'   Archivo: {doc["filename"]}')
            print(f'   Tamaño: {doc["file_size"]/1024/1024:.2f} MB')
            print(f'   Descargado: {doc["download_date"]}')
            print(f'   Checksum: {doc["checksum"][:16]}...')
            if doc.get('history'):
                print(f'   Historial: {len(doc["history"])} versiones anteriores')
    else:
        print('No hay documentos registrados todavía')
    
    print()
    return 0


def main():
    """Función principal del CLI."""
    parser = argparse.ArgumentParser(
        description='CLI para gestionar el scraping y descarga de Reglas de Juego de la RFEBM',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:
  %(prog)s scrape                    # Solo scrapear sin descargar
  %(prog)s download                  # Scrapear y descargar
  %(prog)s download --force          # Forzar descarga aunque exista
  %(prog)s check-updates             # Comprobar actualizaciones
  %(prog)s check-updates --auto      # Descargar automáticamente
  %(prog)s auto-update               # Proceso completo (respeta intervalo)
  %(prog)s auto-update --force       # Forzar actualización
  %(prog)s status                    # Ver estado del sistema
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Comandos disponibles')
    
    # Comando: scrape
    scrape_parser = subparsers.add_parser(
        'scrape',
        help='Solo scrapear la web sin descargar PDFs'
    )
    
    # Comando: download
    download_parser = subparsers.add_parser(
        'download',
        help='Scrapear y descargar el PDF de Reglas de Juego'
    )
    download_parser.add_argument(
        '--force', '--force-download',
        dest='force_download',
        action='store_true',
        help='Forzar descarga aunque el archivo ya exista'
    )
    
    # Comando: check-updates
    check_parser = subparsers.add_parser(
        'check-updates',
        help='Comprobar si hay actualizaciones disponibles'
    )
    check_parser.add_argument(
        '--auto', '--auto-download',
        dest='auto_download',
        action='store_true',
        help='Descargar actualizaciones automáticamente sin preguntar'
    )
    check_parser.add_argument(
        '--force', '--force-download',
        dest='force_download',
        action='store_true',
        help='Forzar descarga aunque el archivo ya exista'
    )
    
    # Comando: auto-update
    auto_parser = subparsers.add_parser(
        'auto-update',
        help='Ejecutar proceso completo de auto-actualización'
    )
    auto_parser.add_argument(
        '--force',
        dest='force_download',
        action='store_true',
        help='Forzar comprobación y descarga (ignorar intervalo de 3 meses)'
    )
    
    # Comando: status
    status_parser = subparsers.add_parser(
        'status',
        help='Mostrar estado del sistema de versiones'
    )
    
    # Parse argumentos
    args = parser.parse_args()
    
    # Si no se proporciona comando, mostrar ayuda
    if not args.command:
        parser.print_help()
        return 0
    
    # Ejecutar comando
    try:
        if args.command == 'scrape':
            return scrape_only(args)
        elif args.command == 'download':
            return download_pdf(args)
        elif args.command == 'check-updates':
            return check_updates(args)
        elif args.command == 'auto-update':
            return auto_update_mode(args)
        elif args.command == 'status':
            return status_mode(args)
        else:
            parser.print_help()
            return 1
    
    except KeyboardInterrupt:
        print('\n\n⚠️ Operación cancelada por el usuario')
        return 130
    except Exception as e:
        log.error(f'Error inesperado: {e}', exc_info=True)
        print(f'\n❌ Error: {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
