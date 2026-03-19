# RefMate — Diario de Sesiones

Registro de decisiones de diseño, cambios realizados y razonamiento detrás de cada implementación.

---

## 2026-03-19 — FASE 0: Scaffolding completo

**Fase(s):** Fase 0: Scaffolding
**Duración aproximada:** 30 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `pyproject.toml` | Creado | Proyecto uv con hatchling como build backend, ruff y mypy configurados |
| `uv.lock` | Creado | Lockfile con todas las dependencias resueltas |
| `config.yaml` | Creado | Configuración central v3.0 con documentos, modelos, crop masks, qdrant, redis, telegram |
| `.env.example` | Modificado | Plantilla con OPENROUTER_API_KEY y TELEGRAM_BOT_TOKEN |
| `.gitignore` | Modificado | Excluye `.env`, `data/raw/`, `data/images/`, `data/ocr/`, `data/structured/`, `data/qdrant/`, `data/redis/`, `logs/` |
| `Dockerfile` | Creado | Imagen del bot (python:3.12-slim + uv, sin dev deps) |
| `Dockerfile.ingest` | Creado | Imagen de ingesta (incluye FlagEmbedding/dev deps completos) |
| `docker-compose.yml` | Creado | Servicios qdrant, redis, bot, ingest (con profile `ingest`) |
| `src/refmate/__init__.py` | Creado | Paquete principal vacío |
| `src/refmate/config.py` | Creado | Singleton `get_config()` con Pydantic, resolución de `${VAR}`, búsqueda de config.yaml por directorios padres |
| `src/refmate/core/__init__.py` | Creado | Subpaquete core vacío |
| `src/refmate/core/protocols.py` | Creado | 7 protocolos `@runtime_checkable`: OCRProvider, TextGenerator, GuardModel, EmbeddingProvider, VectorStore, SemanticCache, Agent |
| `src/refmate/core/models.py` | Creado | 9 DTOs Pydantic `frozen=True`: Chunk, EmbeddingResult, SparseVector, SearchResult, GuardResult, CacheLookupResult, AgentResult, QueryResult |
| `src/refmate/infrastructure/*/__init__.py` | Creado | Subpaquetes vacíos: llm, embeddings, vectorstore, cache, ocr |
| `src/refmate/{ingest,retrieval,bot}/__init__.py` | Creado | Subpaquetes vacíos para fases posteriores |
| `src/refmate/prompts/*.md` | Creado | Placeholders para system_prompt, guard_prompt, agent_tools y prompts de estructuración |
| `scripts/run_ingest.sh` | Creado | Helper para lanzar ingesta dockerizada |
| `scripts/start_ocr_server.sh` | Creado | Helper para levantar vLLM con LightOnOCR |
| `data/{chunks,index}/.gitkeep` | Creado | Directorios commitados (índice jerárquico y chunks se commitean) |

### Decisiones tomadas

- **`uv init` + `hatchling` como build backend:** uv genera por defecto un proyecto sin `[build-system]`, lo que impide instalar el paquete en modo editable. Se añadió hatchling explícitamente para que `from refmate.x import Y` funcione correctamente con `uv run`.

- **Búsqueda de `config.yaml` subiendo por directorios:** En lugar de hardcodear la ruta, `_find_config_file()` sube desde `__file__` hasta encontrar `config.yaml`. Esto hace que el módulo funcione tanto localmente como dentro del contenedor Docker (donde el workdir es `/app`).

- **`@lru_cache(maxsize=1)` para el singleton:** Alternativa a un módulo-level `_config = None` con doble check. `lru_cache` es thread-safe por diseño en CPython y evita el boilerplate de gestión manual del singleton.

- **`data/index/` y `data/chunks/` sí se commitean:** El índice jerárquico y los chunks JSON son artefactos de la ingesta que deben estar disponibles en el contenedor del bot sin ejecutar la ingesta. El resto de `data/` (raw, images, ocr, structured, qdrant, redis) no se commitea por tamaño.

- **Separación de `Dockerfile` y `Dockerfile.ingest`:** FlagEmbedding arrastra PyTorch (~2GB). El contenedor del bot no necesita FlagEmbedding (usa los embeddings ya indexados en Qdrant). Separar imágenes reduce significativamente el tamaño del contenedor de producción.

- **`profiles: [ingest]` en docker-compose:** El servicio de ingesta es un job one-shot, no un servicio long-running. Con `profiles`, `docker compose up -d` solo levanta bot+qdrant+redis, sin intentar ejecutar la ingesta automáticamente.

### Problemas encontrados

- `uv` no estaba en el PATH de la shell del agente. Se resolvió usando la ruta completa `/home/carreroguille/.local/bin/uv` (instalado previamente por el usuario).

### Pendiente para la próxima sesión

- [ ] Fase 1: Scraper — implementar `src/refmate/ingest/scraper.py` con descarga de los 3 PDFs, SHA-256 deduplicación y `manifest.json`.

---

## 2026-03-19 — FASE 1: Scraper de descarga de PDFs

**Fase(s):** Fase 1: Scraper
**Duración aproximada:** 15 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `src/refmate/ingest/scraper.py` | Creado | Descarga los 3 PDFs con httpx, deduplicación SHA-256, verificación de integridad y manifest.json |

### Decisiones tomadas

- **Sin protocolo `Scraper` en `core/protocols.py`:** El scraper es un job offline one-shot que no tiene ningún consumidor que necesite abstraerlo. Añadir un protocolo solo tendría sentido si hubiera múltiples implementaciones (p.ej. `HttpScraper` vs `S3Scraper`). Por ahora es innecesario y violaría YAGNI.

- **Manifest atómico (carga al inicio, escritura al final):** Se carga todo el manifest al principio, se actualiza en memoria y se escribe una sola vez al terminar. Alternativa descartada: escribir por cada documento. La escritura atómica es más segura ante interrupciones parciales y genera menos I/O.

- **Deduplicación por SHA-256 (no solo existencia del fichero):** Comprobar únicamente `dest_path.exists()` no detecta descargas corruptas ni actualizaciones del documento en la URL remota. Con SHA-256 contra el manifest se garantiza que el contenido es idéntico al registrado. Si el hash no coincide, se re-descarga.

- **`status: "cached"` preserva `download_date` original:** Al no re-descargar, la `download_date` del manifest se mantiene tal cual estaba (la fecha de la descarga real), no se sobreescribe con la fecha actual. Esto refleja fielmente cuándo se obtuvo el fichero.

- **Backoff exponencial `2^intento` segundos:** Intento 1→2s, 2→4s, 3→8s. Suficiente para errores transitorios de red sin bloquear el pipeline demasiado tiempo. Los 3 PDFs se descargaron en el primer intento sin necesidad de reintentos.

### Problemas encontrados

- `get_config()` valida `TELEGRAM_BOT_TOKEN` y `OPENROUTER_API_KEY` aunque el scraper no las necesita. Se ejecutó con variables dummy para la prueba. En producción el `.env` tendrá las claves reales. Queda como deuda técnica para FASE 7: considerar si el pipeline de ingesta debería poder arrancar sin las keys del bot.

### Pendiente para la próxima sesión

- [ ] Fase 2: Cropper — implementar `src/refmate/ingest/cropper.py` con renderizado pypdfium2 + recorte de máscaras en porcentaje.

---
## 2026-03-19 — FASE 0: Correcciones post-revisión

**Fase(s):** Fase 0: Scaffolding (fix)
**Duración aproximada:** 5 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `src/refmate/core/protocols.py` | Modificado | `dict \| None` → `dict[str, Any] \| None` en los tres métodos de `VectorStore`; añadido `Any` al import de `typing` |
| `src/refmate/config.py` | Modificado | Ruta al `.env` calculada relativa al `config.yaml` encontrado en lugar de `parent.parent.parent` hardcodeado |
| `pyproject.toml` + `uv.lock` | Modificado | Añadido `types-PyYAML` como dev dependency para satisfacer mypy en modo strict |

### Decisiones tomadas

- **`dict[str, Any]` en lugar de `dict` sin parámetros:** mypy en modo `strict` exige type parameters en todos los genéricos. `dict[str, Any]` es la opción más permisiva que satisface el tipo correcto para filtros arbitrarios de metadatos (documento_id, fuente, nivel). Una alternativa habría sido un `TypedDict` específico, pero sería prematura: los filtros reales se definen en FASE 6 (Qdrant), no aquí.

- **Ruta `.env` anclada al `config.yaml`:** El `.env` siempre vive junto al `config.yaml` en la raíz del proyecto. Anclar su ruta al resultado de `_find_config_file()` es más robusto que contar niveles de directorio, y reutiliza la lógica ya existente de búsqueda de config.

### Problemas encontrados

- Ninguno — los tres cambios son quirúrgicos y el criterio `mypy: no issues found` se cumple tras aplicarlos.

### Pendiente para la próxima sesión

- [ ] Fase 1: Scraper — implementar `src/refmate/ingest/scraper.py` con descarga de los 3 PDFs, SHA-256 deduplicación y `manifest.json`.

---

## 2026-03-19 — FASE 1: Correcciones post-revisión (fix)

**Fase(s):** Fase 1: Scraper (fix)
**Duración aproximada:** 10 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `config.yaml` | Modificado | Añadida sección `scraper` con `timeout_seconds`, `max_retries`, `backoff_base` |
| `src/refmate/config.py` | Modificado | Añadidos `ScraperConfig`, campo `scraper` en `RefMateConfig`, y función pública `get_project_root()` |
| `src/refmate/ingest/scraper.py` | Modificado | 3 correcciones: ruta `data/raw` anclada al proyecto, parámetros operacionales desde `config.scraper`, `dict[str, Any]` sin `type: ignore` |

### Decisiones tomadas

- **`get_project_root()` en `config.py` (no en `scraper.py`):** Se consideró calcular la raíz dentro del propio scraper contando niveles de `__file__`, pero eso sería frágil y duplicaría lógica ya existente en `config.py`. Centralizar en `config.py` hace que todos los módulos de ingesta futuros (cropper, ocr_runner, etc.) tengan un único punto de referencia para la raíz del proyecto.

- **`ScraperConfig` en `config.py` + sección en `config.yaml`:** Los valores `timeout_seconds`, `max_retries` y `backoff_base` son operacionales y podrían necesitar ajuste en entornos con red lenta o inestable sin tocar código. El ROADMAP exige zero hardcoding. Alternativa descartada: dejarlos como constantes de módulo con comentario — viola el principio explícitamente.

- **Pasar `max_retries` y `backoff_base` como argumentos a `_download_document`:** La función privada recibe los parámetros en lugar de leerlos del singleton de config directamente. Esto mantiene las funciones helper testables de forma aislada sin necesidad de mockear `get_config()`.

### Problemas encontrados

- Ninguno — los cambios son quirúrgicos. El scraper sigue pasando los 3 criterios de aceptación y la ruta del manifest ahora es absoluta, confirmando la corrección.

### Pendiente para la próxima sesión

- [ ] Fase 2: Cropper — implementar `src/refmate/ingest/cropper.py` con renderizado pypdfium2 + recorte de máscaras en porcentaje.

---

## 2026-03-19 — FASE 2: Cropper de renderizado y recorte de PDFs

**Fase(s):** Fase 2: Cropper
**Duración aproximada:** 15 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `src/refmate/ingest/cropper.py` | Creado | Renderiza PDFs con pypdfium2 (scale adaptativo) y recorta en porcentaje con PIL. 254 páginas procesadas. |

### Decisiones tomadas

- **Sin protocolo `Cropper` en `core/protocols.py`:** El cropper es un módulo de procesamiento interno de la pipeline offline. No hay variantes intercambiables previstas. Añadir un protocolo sin consumidores que lo abstraigan violaría tanto YAGNI como ISP.

- **Scale calculado para `max_dimension` (no DPI fijo):** pypdfium2 trabaja con un `scale` factor relativo a los puntos del PDF (1 pt = 1/72 in). En lugar de usar `dpi/72` directamente (que daría tamaños variables según el formato de página), se calcula `scale = max_dimension / max(width_pts, height_pts)`. Esto garantiza que todas las páginas tienen el mismo lado largo sin importar el DPI nominal del PDF. Alternativa descartada: renderizar a DPI fijo y luego redimensionar con PIL — dos pasos innecesarios cuando pypdfium2 puede hacer todo en uno.

- **`alternate_sides`: impar→derecha, par→izquierda:** Las reglas de juego siguen el convenio editorial estándar donde las páginas impares tienen el margen exterior a la derecha y las pares a la izquierda. El header lateral (número de regla) aparece en el lado exterior: derecha para impares, izquierda para pares. Este convenio coincide con la numeración real del documento.

- **`asyncio.to_thread()` para el renderizado síncrono:** pypdfium2 y PIL son síncronos. Para no bloquear el event loop del pipeline, el renderizado completo de cada documento se delega a un thread. Alternativa descartada: hacer `run_cropper` puramente síncrono — rompería la convención async del proyecto y dificultaría la integración con el orquestador de Fase 7.

- **Idempotencia por fichero:** Si `page_{n:04d}_crop.png` ya existe, se salta sin re-renderizar. Permite reanudar tras interrupciones sin rehacer trabajo. Las imágenes son deterministas dado el mismo PDF.

- **Liberación explícita de memoria:** Se llama a `.close()` sobre `bitmap`, `pil_image` y `cropped` tras guardar cada página. pypdfium2 gestiona recursos nativos (libpdfium) que no siempre libera el GC de Python. Con 100+ páginas a 1540px, el consumo sin liberar podría superar los límites del contenedor.

### Problemas encontrados

- Ninguno — las 254 páginas (113+114+27) se procesaron correctamente con las máscaras configuradas.

### Pendiente para la próxima sesión

- [ ] Fase 3: OCR — implementar `src/refmate/infrastructure/ocr/lighton.py` (implementa `OCRProvider`) y `src/refmate/ingest/ocr_runner.py`.

---

## 2026-03-19 — FASE 2: Correcciones post-revisión (fix)

**Fase(s):** Fase 2: Cropper (fix)
**Duración aproximada:** 10 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `src/refmate/ingest/cropper.py` | Modificado | Corregido orden `.close()` y eliminado import inline duplicado |
| `config.yaml` | Modificado | Calibrados valores `crop_masks` para eliminar headers/footers visibles |

### Decisiones tomadas

- **Orden de `.close()`: `cropped → pil_image → bitmap`:** En pypdfium2, `bitmap.to_pil()` puede compartir buffer de memoria con el bitmap subyacente. Cerrar `pil_image` antes que `bitmap` podría provocar acceso a memoria ya liberada en versiones futuras de la librería. El orden correcto es hijo→padre (la imagen PIL derivada se cierra antes que el bitmap del que procede, y éste antes que el recurso nativo).

- **Eliminación del import inline de `CropMaskConfig`:** El import en línea 192 era redundante — `CropMaskConfig` ya estaba importado en la línea 16 del módulo. Un import duplicado dentro de un bloque condicional sugiere que fue añadido por error durante la implementación inicial. No hay ninguna razón para reimportarlo: la clase es exactamente la misma.

- **Calibración de `crop_masks` por inspección visual directa:** Las máscaras originales (3.0% laterales, 4.5% top en rgc-fabm, 4.0% top en add-fabm) resultaron insuficientes. Se midió visualmente el tamaño de los elementos a eliminar en cada documento y se ajustaron a: `rgc-fabm.top_pct 4.5→8.0` (logo FABM+título: ~90px/1418px), `reglas-de-juego.right/left_pct 3.0→6.0` (banda coloreada lateral: ~50px/1057px), `add-fabm.top_pct 4.0→6.5` (banner de cabecera: ~75px/1433px). Alternativa descartada: análisis automático de píxeles para detectar bandas de color uniforme — correcto pero sobreingeniería para tres documentos con estructura visual estable.

### Problemas encontrados

- Los valores iniciales de `crop_masks` en `config.yaml` se establecieron en el ROADMAP sin medición real sobre los PDFs renderizados. La revisión visual tras la primera ejecución reveló que los headers persistían. Se corrigió aumentando los porcentajes basándose en las dimensiones reales de los elementos decorativos medidos en las imágenes renderizadas.

### Pendiente para la próxima sesión

- [ ] Fase 3: OCR — implementar `src/refmate/infrastructure/ocr/lighton.py` (implementa `OCRProvider`) y `src/refmate/ingest/ocr_runner.py`.

---

## 2026-03-19 — FASE 2: Segunda ronda de calibración de crop_masks

**Fase(s):** Fase 2: Cropper (fix)
**Duración aproximada:** 10 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `config.yaml` | Modificado | Segunda ronda de ajuste: footers residuales y header de add-fabm aún visibles |

### Decisiones tomadas

- **Segunda ronda de calibración necesaria:** La primera ronda corrigió los headers, pero los footers seguían visibles en los tres documentos y el header de add-fabm persistía parcialmente. Los footers son más difíciles de estimar en la primera pasada porque el espacio entre el último elemento de contenido y el borde varía página a página.

- **Valores finales adoptados:** `reglas-de-juego.bottom_pct 4.0→7.5` (elimina "Reglas de Juego Balonmano Pista" + número de página); `rgc-fabm.bottom_pct 3.5→6.0` (elimina número de página en círculo coloreado); `add-fabm.top_pct 6.5→9.5` (elimina completamente el banner "Anexo Disciplina Deportiva"); `add-fabm.bottom_pct 3.0→6.0` (aumentado a 6.0 por el usuario tras verificación visual propia).

- **Idempotencia requiere borrado manual al cambiar config:** La lógica de skip-si-existe es correcta para reanudar interrupciones, pero implica que un cambio en `config.yaml` no se aplica a imágenes ya generadas. Para re-calibrar hay que borrar `data/images/{doc_id}/` antes de volver a ejecutar.

### Problemas encontrados

- Ninguno — los ajustes son de configuración pura. La revisión final confirmó que las 254 imágenes de los tres documentos pasan el criterio de aceptación visual.

### Pendiente para la próxima sesión

- [ ] Fase 3: OCR — implementar `src/refmate/infrastructure/ocr/lighton.py` (implementa `OCRProvider`) y `src/refmate/ingest/ocr_runner.py`.

---
