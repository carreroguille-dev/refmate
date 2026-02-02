import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scraper.version_tracker import VersionTracker

tracker = VersionTracker()
status = tracker.get_status()

print("\nESTADO DEL SISTEMA DE VERSIONES")
print("=" * 50)
print(f"Última comprobación: {status['last_check'] or 'Nunca'}")
print(f"Próxima comprobación: {status['next_check']}")
print(f"Intervalo: {status['check_interval_days']} días (3 meses)")
print(f"Documentos: {status['total_documents']}\n")

for doc in status['documents']:
    print(f"📄 {doc['id']}: {doc['title']}")
    print(f"   Versión: {doc['version']}")
    print(f"   Archivo: {doc['filename']}")
    print(f"   Tamaño: {doc['file_size']/1024/1024:.2f} MB")
    print(f"   Descargado: {doc['download_date']}")
    print(f"   Checksum: {doc['checksum'][:16]}...")
    if doc.get('history'):
        print(f"   Historial: {len(doc['history'])} versiones anteriores")
    print()
