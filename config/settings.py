# =====================================
# FUENTES DE NORMATIVA
# =====================================

from pathlib import Path


SOURCES = [
    {
        "name": "RFEBM - Normativa y Reglamentos",
        "url": "https://www.rfebm.com/transparencia/normativa-y-reglamentos/",
    },
    {
        "name": "FABM - Reglamentos", 
        "url": "https://fandaluzabm.org/normativa/reglamentos/",
    },
]

# =====================================
# DESCARGA
# =====================================

DOWNLOAD = {
    "timeout": 30,
    "retries": 3,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# =====================================
# RUTAS
# =====================================

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

PATHS = {
    "raw": DATA_DIR / "raw",           
    "temp": DATA_DIR / "temp",         
    "processed": DATA_DIR / "processed", 
    "indices": DATA_DIR / "indices",   
    "logs": ROOT / "logs",            
}