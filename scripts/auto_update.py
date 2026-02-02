"""
Script para ejecutar la auto-actualización del sistema.
Diseñado para ser ejecutado como tarea programada (cron/Task Scheduler).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scraper.version_tracker import VersionTracker
from src.utils.logger import log


def main():
    """Ejecuta el proceso de auto-actualización."""
    try:
        log.info('=' * 60)
        log.info('INICIO DE TAREA PROGRAMADA: Auto-actualización')
        log.info('=' * 60)
        
        # Crear tracker y ejecutar auto-actualización
        tracker = VersionTracker()
        result = tracker.auto_update(force=False)  # No forzar, respetar intervalo
        
        # Reportar resultado
        action = result.get('action')
        
        if action == 'no_check_needed':
            log.info('✅ No es necesario realizar comprobación todavía')
            log.info(f"   Próximo check: {result.get('next_check')}")
            return 0
        
        elif action == 'no_updates':
            log.info('✅ Comprobación realizada: No hay actualizaciones')
            log.info(f"   Próximo check: {result.get('next_check')}")
            return 0
        
        elif action == 'updated':
            log.info('✅ Actualizaciones procesadas exitosamente')
            log.info(f"   Actualizaciones encontradas: {result.get('updates_found')}")
            log.info(f"   Descargas exitosas: {result.get('downloads_successful')}")
            if result.get('downloads_failed', 0) > 0:
                log.warning(f"   Descargas fallidas: {result.get('downloads_failed')}")
            log.info(f"   Próximo check: {result.get('next_check')}")
            return 0
        
        else:
            log.error(f'❌ Resultado inesperado: {action}')
            return 1
    
    except Exception as e:
        log.error(f'❌ Error durante la auto-actualización: {e}', exc_info=True)
        return 1
    
    finally:
        log.info('=' * 60)
        log.info('FIN DE TAREA PROGRAMADA')
        log.info('=' * 60)


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
