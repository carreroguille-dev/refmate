"""
Configuración de logging para el pipeline.
"""

import logging
from datetime import datetime
from config.settings import PATHS

# Crear directorio de logs
LOG_DIR = PATHS.get('logs', PATHS['raw'].parent / 'logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Fichero con fecha
LOG_FILE = LOG_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"

# Configurar logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()  # También muestra en consola
    ]
)

def get_logger(name):
    """Obtiene un logger con el nombre especificado."""
    return logging.getLogger(name)