# Scripts CLI - Refmate Handball

Este directorio contiene los scripts de línea de comandos para gestionar el proyecto.

## Scripts Disponibles

### 1. run_scraper.py

Script CLI principal para gestionar el scraping y descarga de Reglas de Juego.

#### Comandos:

**scrape** - Solo scrapear sin descargar
```bash
python scripts/run_scraper.py scrape
```
Busca PDFs disponibles en la web de la RFEBM y muestra la lista sin descargar.

**download** - Scrapear y descargar
```bash
python scripts/run_scraper.py download
python scripts/run_scraper.py download --force  # Forzar descarga
```
Scrapea y descarga el PDF más reciente de Reglas de Juego.

**check-updates** - Comprobar actualizaciones
```bash
python scripts/run_scraper.py check-updates
python scripts/run_scraper.py check-updates --auto       # Descargar automáticamente
python scripts/run_scraper.py check-updates --auto --force  # Forzar descarga
```
Comprueba si hay nuevas versiones disponibles. Pregunta antes de descargar (usa `--auto` para descargar automáticamente).

**auto-update** - Actualización automática
```bash
python scripts/run_scraper.py auto-update
python scripts/run_scraper.py auto-update --force  # Ignorar intervalo de 3 meses
```
Ejecuta el proceso completo de auto-actualización respetando el intervalo de 90 días.

**status** - Ver estado del sistema
```bash
python scripts/run_scraper.py status
```
Muestra información detallada del sistema de versiones.

#### Opciones Globales:

- `--force` o `--force-download`: Fuerza la descarga aunque el archivo ya exista
- `--auto` o `--auto-download`: (solo check-updates) Descarga automáticamente sin preguntar

#### Ejemplos de Uso:

```bash
# Ver ayuda general
python scripts/run_scraper.py --help

# Ver ayuda de un comando específico
python scripts/run_scraper.py check-updates --help

# Flujo típico inicial
python scripts/run_scraper.py download

# Comprobar actualizaciones manualmente
python scripts/run_scraper.py check-updates

# Ver estado actual
python scripts/run_scraper.py status
```

---

### 2. auto_update.py

Script para ejecución en tareas programadas. Ejecuta auto-actualización respetando el intervalo de 90 días.

```bash
python scripts/auto_update.py
```

**Uso recomendado:** Configurar como tarea programada (Task Scheduler en Windows, cron en Linux/Mac).

**Windows Task Scheduler:**
```powershell
# Crear tarea que ejecuta diariamente a las 3 AM
$action = New-ScheduledTaskAction -Execute "python" -Argument "C:\path\to\scripts\auto_update.py"
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
Register-ScheduledTask -Action $action -Trigger $trigger -TaskName "RefmateAutoUpdate"
```

**Linux/Mac cron:**
```bash
# Agregar a crontab (crontab -e)
0 3 * * * cd /path/to/project && python scripts/auto_update.py
```

---

### 3. check_status.py

Script simple para verificar el estado del sistema.

```bash
python scripts/check_status.py
```

Muestra:
- Última comprobación
- Próxima comprobación
- Documentos registrados
- Información detallada de cada documento

---

## Flujo de Trabajo Recomendado

### Setup Inicial

1. **Primera descarga:**
```bash
python scripts/run_scraper.py download
```

2. **Verificar que se descargó correctamente:**
```bash
python scripts/run_scraper.py status
```

### Actualizaciones Manuales

**Comprobar si hay actualizaciones:**
```bash
python scripts/run_scraper.py check-updates
```

**Descargar actualizaciones automáticamente:**
```bash
python scripts/run_scraper.py check-updates --auto
```

### Automatización

**Configurar tarea programada:**
- Usar `auto_update.py` en Task Scheduler (Windows) o cron (Linux/Mac)
- El sistema comprobará automáticamente cada 90 días
- Si hay actualizaciones, las descargará automáticamente

**Verificar estado periódicamente:**
```bash
python scripts/check_status.py
```

---

## Estructura de Archivos Generados

```
data/
├── raw/
│   ├── versions.json                    # Base de datos de versiones
│   └── reglas-juego_2025_20260202.pdf  # PDF descargado
├── processed/                           # Futuros archivos procesados (OCR)
├── chunks/                              # Futuros chunks de texto
└── index/                               # Futuros índices de búsqueda
```

---

## Solución de Problemas

### El script dice "No es necesario realizar comprobación todavía"

El sistema respeta un intervalo de 90 días entre comprobaciones. Para forzar una comprobación:
```bash
python scripts/run_scraper.py auto-update --force
```

### Error de conexión al scrapear

Verifica tu conexión a internet y que la URL de la RFEBM esté accesible:
- https://www.rfebm.com/transparencia/normativa-y-reglamentos/

### El PDF no se descarga

1. Verifica que haya espacio en disco
2. Comprueba que tengas permisos de escritura en `data/raw/`
3. Usa `--force` para forzar la descarga:
```bash
python scripts/run_scraper.py download --force
```

---

## Logs

Todos los scripts generan logs en:
- **Consola:** Output en tiempo real
- **Archivo:** `logs/refmate.log` (rotación automática cada 10 MB)

Para ver solo errores, filtra el log:
```bash
# Windows PowerShell
Get-Content logs/refmate.log | Select-String "ERROR"

# Linux/Mac
grep "ERROR" logs/refmate.log
```
