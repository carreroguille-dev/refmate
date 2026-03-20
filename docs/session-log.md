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

## 2026-03-19 — FASE 3: OCR con LightOnOCR y orquestador

**Fase(s):** Fase 3: OCR
**Duración aproximada:** 10 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `src/refmate/infrastructure/ocr/lighton.py` | Creado | `LightOnOCRProvider` implementa `OCRProvider`: PNG → base64 → POST OpenAI-compatible al endpoint vLLM |
| `src/refmate/ingest/ocr_runner.py` | Creado | Orquestador: health check del servidor, procesamiento secuencial página a página, retry/backoff, persistencia en `data/ocr/{doc_id}_raw.txt` |
| `data/ocr/` | Creado | Directorio de salida para los textos OCR (no commitado, en .gitignore) |

### Decisiones tomadas

- **Procesamiento secuencial (no concurrente):** El ROADMAP especifica explícitamente procesamiento secuencial por la limitación de VRAM del servidor vLLM (8GB). Un semáforo con concurrencia=1 habría sido equivalente, pero la forma idiomática y legible es simplemente un bucle `for` sin `asyncio.gather`. Menos complejidad, mismo resultado.

- **Health check parseando la base URL del endpoint:** El endpoint configurado es la URL completa del chat completions (`/v1/chat/completions`). El health endpoint de vLLM está en `/health` sobre la misma base. Se extrae la base con una regex sencilla `(https?://[^/]+)` en lugar de usar `urllib.parse` para evitar una importación adicional innecesaria para un caso tan simple.

- **Marcador de página insertado antes del texto (excepto página 1):** El marcador `<!-- PAGE {n} -->` se inserta **antes** del texto de cada página (excepto la primera), no después. Esto facilita el splitting posterior en la fase de estructuración: un `split('<!-- PAGE')` da directamente el texto limpio de cada página sin prefijos vacíos.

- **Retry backoff 1s/2s/4s (base=1.0, factor=2^(intento-1)):** Tres intentos con esperas cortas son suficientes para errores transitorios del servidor local. Un backoff más agresivo sería excesivo para un servidor en la misma máquina. Si falla en los tres intentos, el error se propaga con contexto claro de qué página y documento fallaron.

- **`LightOnOCRProvider` sin prompt de sistema:** La API de LightOnOCR (modelo OCR puro) no necesita system prompt — el modelo está entrenado específicamente para transcribir imágenes. El mensaje del usuario contiene solo el `image_url`. Alternativa descartada: añadir un system prompt descriptivo, que podría interferir con el comportamiento entrenado del modelo.

### Problemas encontrados

- Ninguno — la implementación es directa. La verificación del criterio "texto fiel al original" solo puede hacerse en runtime con el servidor vLLM activo.

### Pendiente para la próxima sesión

- [ ] Fase 4: Structurer — implementar `src/refmate/ingest/structurer.py` usando `TextGenerator` de OpenRouter para convertir texto plano en Markdown jerárquico.

---

## 2026-03-19 — FASE 4: Structurer con OpenRouterTextGenerator

**Fase(s):** Fase 4: Structurer
**Duración aproximada:** 15 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `src/refmate/infrastructure/llm/openrouter.py` | Creado | `OpenRouterTextGenerator` implementa `TextGenerator`: POST a OpenRouter con retry exponencial (2s/4s/8s), headers obligatorios y soporte no-thinking mode |
| `src/refmate/ingest/structurer.py` | Creado | Pipeline completo: carga OCR → ventaneo por páginas con overlap → llamadas LLM → combinación de ventanas → validación → extracción de refs → escritura de `.md` y `_refs.json` |
| `src/refmate/prompts/structuring/reglas-de-juego.md` | Modificado | Prompt con jerarquía `# Regla N` / `## N:M`, reglas de no-modificación y marcas `[REF:reglas-de-juego:regla-N-M]` |
| `src/refmate/prompts/structuring/rgc-fabm.md` | Modificado | Prompt con jerarquía H1/H2(Capítulo)/H3(Sección)/H4 y marcas `[REF:rgc-fabm:art-N]` |
| `src/refmate/prompts/structuring/add-fabm.md` | Modificado | Prompt con jerarquía H1/H2(Capítulo)/H3(Artículo) y marcas `[REF:add-fabm:art-N]` |

### Decisiones tomadas

- **`OPENROUTER_REFERER` como constante de módulo (no en `config.yaml`):** Es un identificador fijo del proyecto que no varía entre entornos ni usuarios. Ponerlo en config añadiría una llave que nadie necesita cambiar. Las constantes de módulo son el lugar correcto para valores literales que no son operacionales.

- **Retry de 3 intentos con delays `(2, 4, 8)` como tupla constante:** Los delays son una decisión de diseño fija del módulo (no configurable). Una tupla inmutable de tres elementos es más expresiva que un bucle con `2 ** intento`. Si en el futuro se necesita configurabilidad, se añadirá a `LlmModelConfig`.

- **Ventaneo por páginas completas (no por offset de caracteres):** Cortar por offset de caracteres puede dividir el texto en mitad de una regla o un artículo, lo que degradaría la calidad del output estructurado. Agrupando páginas completas hasta llenar la ventana, el LLM siempre recibe unidades semánticas completas. El coste es que alguna ventana puede quedar ligeramente por debajo del límite si la última página que cabe es muy pequeña.

- **Overlap como páginas completas (no texto arbitrario):** Al mantener el overlap a nivel de páginas completas, se garantiza que el LLM siempre tiene contexto de continuación coherente. La longitud del overlap es variable (depende de cuántas páginas completas caben en `overlap_chars`), pero nunca parte una página por la mitad.

- **Combinación de ventanas buscando el primer párrafo en el output previo:** Un offset fijo de caracteres para el join fallaría si el LLM reorganiza ligeramente el texto (añade saltos de línea, cambia espaciado). Buscar el primer párrafo del window N en el acumulado es más robusto ante esas variaciones menores. El fallback es concatenar con doble salto de línea si no se encuentra coincidencia.

- **Validación como warning (no excepción) para el ratio de longitud:** El LLM puede añadir encabezados y marcas de referencia que aumenten el tamaño del output, o puede comprimir ligeramente el espaciado. Un ratio 0.85–1.15 es suficientemente permisivo para ambas variaciones. Elevar a excepción detendría el pipeline por variaciones legítimas; un warning informa sin bloquear.

- **`FileNotFoundError` con mensaje de instrucción explícita:** El error indica qué módulo hay que ejecutar antes (`refmate.ingest.ocr_runner`). Un mensaje genérico "fichero no encontrado" obliga al usuario a buscar en la documentación qué hacer. El mensaje contextualizado acelera el debugging.

- **Stats como `dict` simple (no DTO):** `Structurer.run()` es invocado por el orquestador del pipeline (Fase 7), que solo necesita los números para logging. No hay otros consumidores. Un `TypedDict` o `@dataclass` sería over-engineering para un dict de cuatro enteros.

### Problemas encontrados

- Ninguno — los imports y la lógica de ventaneo/extracción de refs se verificaron con tests inline antes del commit.

### Pendiente para la próxima sesión

- [x] Fase 5: Chunker — implementar `src/refmate/ingest/chunker.py` para dividir el Markdown estructurado en `Chunk` DTOs con metadatos jerárquicos.

---

## 2026-03-19 — FASE 3: Correcciones post-revisión (fix)

**Fase(s):** Fase 3: OCR (fix)
**Duración aproximada:** 5 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `config.yaml` | Modificado | Añadidos `timeout_seconds: 120` y `health_timeout_seconds: 10` a `models.ocr` |
| `src/refmate/config.py` | Modificado | Añadidos campos `timeout_seconds: int` y `health_timeout_seconds: int` a `OcrModelConfig` |
| `src/refmate/infrastructure/ocr/lighton.py` | Modificado | `timeout=120.0` hardcodeado → `self._config.timeout_seconds` |
| `src/refmate/ingest/ocr_runner.py` | Modificado | Tres correcciones: tipo `OCRProvider` en `_extract_with_retry`, `_check_endpoint_health` recibe `model_name` y `timeout` como parámetros, nombre de modelo en error message leído de config |

### Decisiones tomadas

- **`_extract_with_retry` tipa `OCRProvider` (protocolo), no `LightOnOCRProvider`:** La función privada no necesita saber qué implementación concreta recibe — solo necesita que tenga `extract_text`. Usar el tipo concreto obligaba a importar la implementación desde `infrastructure/` en el módulo de orquestación, acoplando los dos niveles. Con el protocolo, `_extract_with_retry` es agnóstica a la implementación. El import de `LightOnOCRProvider` se mantiene en `run_ocr_runner` para la instanciación hasta que `pipeline.py` (Fase 7) asuma ese rol como composition root.

- **`_check_endpoint_health` recibe `model_name` y `timeout` como parámetros:** Alternativa descartada: hacer que la función lea `get_config()` internamente. Eso habría añadido un efecto secundario oculto y dificultado el testing. Recibir los valores como parámetros es coherente con el principio de que las funciones privadas son helpers puros que reciben lo que necesitan.

- **`timeout_seconds` e `health_timeout_seconds` en `OcrModelConfig`:** Los dos timeouts tienen naturalezas distintas: uno es el tiempo máximo de inferencia del modelo (depende del tamaño de la imagen y la GPU), el otro es un ping de disponibilidad. Tenerlos separados permite ajustar uno sin afectar al otro. Alternativa descartada: un único campo `timeout_seconds` para ambos — demasiado genérico y podría dar falsos positivos en el health check si se usa el valor de inferencia (120s).

### Problemas encontrados

- Ninguno — todos los cambios son puntuales y quirúrgicos.

### Pendiente para la próxima sesión

- [ ] Fase 4: Structurer — implementar `src/refmate/ingest/structurer.py` usando `TextGenerator` de OpenRouter para convertir texto plano en Markdown jerárquico.

---

## 2026-03-19 — FASE 4: Correcciones post-revisión (fix)

**Fase(s):** Fase 4: Structurer (fix)
**Duración aproximada:** 5 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `config.yaml` | Modificado | `timeout_seconds` añadido a `models.structuring` (120s), `models.guard` (30s) y `models.agent` (120s) |
| `src/refmate/config.py` | Modificado | Campo `timeout_seconds: int` añadido a `LlmModelConfig` |
| `src/refmate/infrastructure/llm/openrouter.py` | Modificado | `timeout=120` hardcodeado → `self._config.timeout_seconds`; `payload: dict` → `dict[str, object]` |
| `src/refmate/ingest/structurer.py` | Modificado | Prompt cargado una vez en `run()` y pasado como argumento a `_generate_windows`; eliminado parámetro muerto `ocr_text` de `_combine_windows`; `re.Pattern` → `re.Pattern[str]` |

### Decisiones tomadas

- **`timeout_seconds` en `LlmModelConfig` (igual que en `OcrModelConfig`):** La revisión detectó que `openrouter.py` tenía `timeout=120` hardcodeado, incoherente con el tratamiento del OCR donde el timeout ya era configurable. Se añadió el campo al modelo Pydantic para mantener coherencia. Los valores elegidos reflejan las necesidades de cada modelo: structuring y agent necesitan 120s (modelos grandes, salidas largas), guard solo 30s (modelo pequeño, salida de 50 tokens).

- **`prompt` pasado como argumento a `_generate_windows` (no recargado):** La versión original cargaba el prompt en `run()` (variable que quedaba sin usar) y lo volvía a cargar dentro de `_generate_windows`. La solución correcta es cargarlo una sola vez en `run()` y pasarlo como parámetro. Alternativa descartada: hacer `_load_prompt` un método de caché interna (`@lru_cache`) — sobreingeniería para un fichero que se lee una vez por ejecución.

- **Eliminación de `ocr_text` de `_combine_windows`:** El parámetro estaba marcado como "reservado" en el docstring original pero nunca se usó. Mantenerlo hubiera sido una API mentirosa: la firma prometía recibir el OCR pero lo ignoraba. Se eliminó para que la firma refleje exactamente lo que la función necesita.

### Problemas encontrados

- Ninguno — los cinco cambios son puntuales y el test de imports + firma de métodos confirmó la corrección antes del commit.

### Pendiente para la próxima sesión

- [x] Fase 5: Chunker — implementar `src/refmate/ingest/chunker.py` para dividir el Markdown estructurado en `Chunk` DTOs con metadatos jerárquicos.

---

## 2026-03-20 — FASE 5: Chunker semántico con grafo de referencias cruzadas

**Fase(s):** Fase 5: Chunker
**Duración aproximada:** 45 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `src/refmate/ingest/chunker.py` | Creado | Chunker completo: 3 parsers específicos por tipo de jerarquía, resolución bidireccional de refs cruzadas, generación de grafo e índice jerárquico |

### Decisiones tomadas

- **3 parsers separados por `tipo_jerarquia` (no uno genérico paramétrico):** Cada documento tiene reglas de chunking distintas (nivel de heading, lógica de subdivisión, formato de lookup para refs). Un parser genérico con flags tendría demasiados condicionales anidados. Tres métodos `_parse_regla_subregla`, `_parse_titulo_capitulo_articulo` y `_parse_titulo_capitulo_seccion` son más legibles, independientes y fáciles de ajustar por separado cuando el LLM del structurer produce variaciones menores.

- **chunk_ids concisos extrayendo solo el número/numeral romano:** La primera implementación usaba el slug completo del título (`capitulo-i-disposiciones-generales:articulo-1-objeto-y-ambito-de-aplicacion`). Se sustituyó por extracción de número mediante regex específica por tipo de heading (`_heading_slug`), produciendo IDs del estilo `add-fabm:capitulo-i:articulo-1`. Motivo: los IDs aparecerán en logs, en el grafo de refs y en las respuestas del agente al usuario — la legibilidad importa.

- **Chunk por capítulo (`nivel=capitulo`) cuando un H2 de rgc-fabm carece de secciones H3:** El ROADMAP especifica "chunk por sección" para rgc-fabm, pero algunos capítulos del RGC no tienen secciones (el contenido normativo está directamente bajo el capítulo). Alternativa descartada: ignorar ese contenido (se perdería normativa relevante). Se crea un chunk de nivel `capitulo` para no perder texto normativo.

- **Contenido pre-H3 en un capítulo que sí tiene secciones → chunk `capitulo` separado:** Cuando un capítulo de rgc-fabm tiene texto introductorio antes de la primera sección, ese texto se flushea como chunk de nivel `capitulo` al encontrar el primer H3. Alternativa descartada: añadirlo al primer H3 (mezclaría contextos distintos).

- **Auto-referencias filtradas del grafo:** La resolución de `[REF:rgc-fabm:art-N]` busca artículos en el body text de los chunks. Si el artículo referenciado está en el mismo chunk (self-loop), la ref se filtra con `if target_id != chunk_id`. Los self-loops no añaden información al grafo y generarían ruido en la expansión de refs del agente.

- **Lookup de refs para rgc-fabm basado en escaneo del body text:** Los refs en rgc-fabm son `[REF:rgc-fabm:art-N]` pero los chunks son por sección. Para resolver `art-15`, `_register_lookup_rgc` escanea el texto del chunk con regex `Art[íi]culo\s+(\d+)` y registra la clave. Primer-come-first-served: no sobreescribe si ya existe una entrada, para que la mención definitoria prevalezca sobre menciones incidentales.

- **Implementación sin ejecutar structurer previo:** El usuario decidió implementar el chunker directamente basándose en los prompts de estructuración (formato de headings conocido y determinista) sin necesidad de correr la FASE 4 primero. El chunker se validó con Markdown sintético representativo de los tres tipos de documento.

### Problemas encontrados

- **chunk_ids verbosos en primera implementación:** Primera versión usaba `_slugify(titulo[:60])` produciendo IDs muy largos. Detectado al inspeccionar el output y corregido con `_heading_slug()` que extrae solo el número/numeral.

- **Auto-referencia en rgc-fabm:** La resolución de `[REF:rgc-fabm:art-12]` dentro del chunk `capitulo-ii` (que define ese artículo en su body) creaba un edge self-loop. Corregido filtrando `target_id == chunk_id` en `_resolve_references`.

### Pendiente para la próxima sesión

- [ ] Fase 6: Indexer — implementar `src/refmate/infrastructure/embeddings/bge_m3.py` (EmbeddingProvider) y `src/refmate/infrastructure/vectorstore/qdrant.py` (VectorStore) + `src/refmate/ingest/indexer.py`.

---

## 2026-03-20 — Fix FASE 5: correcciones de review (hardcoding, async, dedup, splitting)

**Fase(s):** Fase 5: Chunker
**Duración aproximada:** 20 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `config.yaml` | Modificado | Añadida sección `chunker:` con `max_section_tokens: 2000` y `max_chunk_tokens: 3000` |
| `src/refmate/config.py` | Modificado | Añadida clase `ChunkerConfig` (Pydantic frozen) y campo `chunker: ChunkerConfig` en `RefMateConfig` |
| `src/refmate/ingest/chunker.py` | Modificado | 5 correcciones: eliminar constantes hardcodeadas, `run_all()` async, dedup de edges, quitar nonlocal innecesario, splitting universal |

### Decisiones tomadas

- **Eliminar `_MAX_SECTION_TOKENS` y `_MAX_CHUNK_TOKENS` como constantes de módulo:** Los umbrales de tokens son parámetros operacionales que el usuario podría querer ajustar sin tocar código. Se movieron a `config.yaml` y se acceden via `self._config.chunker.max_section_tokens/max_chunk_tokens`. No se pasaron como argumentos al constructor de `Chunker` (eso requeriría cambiar la interfaz del composition root); leerlos directamente de config es más simple y consistente con el resto del código.

- **`run_all()` → `async def`:** Toda la pipeline es async por convención del proyecto (CLAUDE.md). Aunque `run_all()` actualmente no hace I/O asíncrono (los ficheros los lee de forma síncrona con Pydantic/pathlib), el método está destinado a integrarse en una pipeline async en FASE 8. Hacer la signatura async ahora evita un cambio de interfaz posterior. El `__main__` se actualizó para usar `asyncio.run()`.

- **Dedup de edges con comparación por valor de dict:** La lista `edges` acumulaba un edge por cada ocurrencia del mismo `[REF:...]` en el texto (si la misma referencia aparece dos veces en un chunk, se registraba dos veces). Se añadió `if edge not in edges` (O(n) por edge, pero el grafo es pequeño). Alternativa descartada: usar un set de tuplas (requeriría conversión y es menos legible).

- **Splitting en los tres parsers (no solo rgc-fabm):** El review señaló que los parsers de reglas-de-juego y add-fabm no tenían garantía de chunk < 3000 tokens. Se añadió el mismo patrón de splitting en `flush()` de los tres parsers, usando `max_chunk_tokens` del config. `_split_large_section` se parametrizó con `max_tokens` en lugar de usar la constante eliminada.

- **Quitar `h2_has_sections` del nonlocal:** La variable `h2_has_sections` se modificaba en el loop principal (no dentro de `flush()`), por lo que declararlo `nonlocal` dentro de `flush()` era incorrecto y confuso. Se eliminó del `nonlocal` sin impacto funcional.

### Problemas encontrados

- Ninguno — todas las correcciones se aplicaron directamente sin errores. La verificación con `uv run python -m refmate.ingest.chunker` confirmó que la config carga y valida correctamente con el nuevo campo `chunker`.

### Pendiente para la próxima sesión

- [ ] Fase 6: Indexer — implementar `src/refmate/infrastructure/embeddings/bge_m3.py` (EmbeddingProvider) y `src/refmate/infrastructure/vectorstore/qdrant.py` (VectorStore) + `src/refmate/ingest/indexer.py`.

---

## 2026-03-20 — FASE 6: Indexer con BGE-m3 y QdrantVectorStore

**Fase(s):** Fase 6: Indexer
**Duración aproximada:** 20 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `src/refmate/infrastructure/embeddings/bge_m3.py` | Creado | `BGEM3EmbeddingProvider` implementa `EmbeddingProvider`: carga BGE-m3 en CPU, encode/encode_batch con lexical_weights → sparse indices+values |
| `src/refmate/infrastructure/vectorstore/qdrant.py` | Creado | `QdrantVectorStore` implementa `VectorStore`: colección con named vectors dense+sparse, payload indexes, upsert en batches de 50, búsqueda dense/sparse/hybrid (RRF), get_by_ids |
| `src/refmate/ingest/indexer.py` | Creado | `Indexer` orquesta embedding + indexación; `run_indexer()` es el composition root de ingesta (instancia concretos y prepara colección) |
| `pyproject.toml` + `uv.lock` | Modificado | Añadida restricción `transformers<5.0` por incompatibilidad de FlagEmbedding 1.3.5 con transformers 5.x |

### Decisiones tomadas

- **`ensure_collection()` fuera del protocolo `VectorStore`, llamado desde `run_indexer`:** La preparación de la colección es una operación de inicialización específica de Qdrant, no parte del contrato genérico de `VectorStore`. Incluirla en el protocolo habría contaminado la interfaz con un detalle de infraestructura. El `run_indexer` (composition root) conoce la implementación concreta y puede llamar `store.ensure_collection()` antes de delegar en `Indexer`. Alternativa descartada: añadirla al protocolo — violaría ISP (un `FaissVectorStore` futuro no necesitaría colecciones Qdrant).

- **chunk_id → UUID determinista con MD5:** Qdrant requiere `int` o UUID como punto ID. Los chunk_ids son strings (`reglas-de-juego:regla-8:8-5`). Se usa MD5 del string para obtener un UUID estable y reproducible: el mismo chunk_id siempre genera el mismo UUID, lo que hace los upserts idempotentes. Alternativa descartada: un contador entero secuencial — no sería reproducible entre ejecuciones con distintos subconjuntos de documentos.

- **Chunk payload completo en Qdrant (todos los campos del DTO):** Se almacena `chunk.model_dump()` como payload. Esto permite reconstruir el `Chunk` completo en `get_by_ids` y en los resultados de búsqueda sin queries adicionales. Alternativa descartada: almacenar solo el `chunk_id` y hacer lookup en los ficheros JSON — añadiría I/O extra y acoplaría el retrieval a los ficheros de ingesta.

- **`encode_batch` síncrono (no `asyncio.to_thread`):** El protocolo `EmbeddingProvider` define `encode`/`encode_batch` como síncronos (FlagEmbedding es CPU-bound, no I/O). En el contexto del pipeline de ingesta (offline, no web server), bloquear el event loop durante la embedificación es aceptable. Añadir `to_thread` habría complicado el código sin beneficio real en este contexto.

- **`transformers<5.0` como restricción explícita:** FlagEmbedding 1.3.5 importa `is_torch_fx_available` de `transformers.utils.import_utils`, que se eliminó en transformers 5.0. Se añadió la restricción al resolver el error de importación. Alternativa descartada: actualizar a una versión de FlagEmbedding compatible con transformers 5.x — no existe en PyPI a fecha de esta sesión.

### Problemas encontrados

- **Incompatibilidad FlagEmbedding 1.3.5 + transformers 5.3.0:** Al intentar importar `BGEM3FlagModel`, el entorno lanzaba `ImportError: cannot import name 'is_torch_fx_available'`. Se resolvió añadiendo `transformers<5.0` al `pyproject.toml`, que instaló `transformers==4.57.6`. La restricción queda documentada en el lock file para evitar que futuras actualizaciones reactiven el problema.

### Pendiente para la próxima sesión

- [ ] Fase 7: Pipeline Orchestrator — implementar `src/refmate/ingest/pipeline.py` que encadene las fases 1-6 con flags `--from` y `--only`.

---

## 2026-03-20 — Fix FASE 6: correcciones de review (device, batch_size, tipos, recreate)

**Fase(s):** Fase 6: Indexer (fix)
**Duración aproximada:** 10 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `src/refmate/infrastructure/embeddings/bge_m3.py` | Modificado | Pasar `device=config.device` a `BGEM3FlagModel`; `raw: dict` → `dict[str, Any]`; añadido import `Any` |
| `src/refmate/infrastructure/vectorstore/qdrant.py` | Modificado | Eliminado `_UPSERT_BATCH_SIZE = 50` hardcodeado; `upsert()` usa ahora `self._config.upsert_batch_size` |
| `config.yaml` | Modificado | Añadido `upsert_batch_size: 50` bajo la sección `qdrant:` |
| `src/refmate/config.py` | Modificado | Añadido campo `upsert_batch_size: int` a `QdrantConfig` |
| `src/refmate/ingest/indexer.py` | Modificado | `list[dict]` → `list[dict[str, Any]]`; `run_indexer` recibe `recreate: bool = True` como parámetro; añadido import `Any` |

### Decisiones tomadas

- **`device` no pasado al modelo (bug):** `BGEM3FlagModel` acepta un parámetro `device` explícito. La implementación original lo logueaba pero lo ignoraba, lo que significaba que en un entorno con GPU (`device: cuda` en config) el modelo correría igualmente en CPU. Corrección directa: `BGEM3FlagModel(config.name, use_fp16=False, device=config.device)`. El default del config sigue siendo `"cpu"` según el ROADMAP.

- **`upsert_batch_size` a `config.yaml`:** El valor `50` aparecía como constante de módulo `_UPSERT_BATCH_SIZE = 50`, violando el principio de zero-hardcoding. Aunque el ROADMAP lo menciona como "batches de 50", el tamaño de batch es un parámetro operacional (afecta a latencia y uso de memoria del cliente Qdrant). Se movió a `config.yaml` bajo `qdrant:` para que sea ajustable sin tocar código. El valor por defecto se mantuvo en 50.

- **`recreate: bool = True` en `run_indexer`:** Hardcodear `recreate=True` impedía que el pipeline (FASE 7) pudiera invocar indexación incremental mediante `--from indexer` sin destruir la colección existente. Recibir el flag como parámetro con default `True` mantiene el comportamiento habitual (ingesta completa siempre recrea) pero permite al pipeline pasar `recreate=False` cuando sea apropiado.

- **Tipos sin parámetros (`dict`, `list[dict]`):** En modo `strict` de mypy, los genéricos sin parámetros de tipo (`dict` en lugar de `dict[str, Any]`, `list[dict]` en lugar de `list[dict[str, Any]]`) son errores. Las correcciones son mecánicas y no afectan al comportamiento en runtime.

### Problemas encontrados

- La verificación con `get_config()` falló por falta de `.env` en el entorno del agente (`TELEGRAM_BOT_TOKEN` no definida). Se verificaron todas las correcciones mediante inspección de código fuente con `inspect.getsource()` y lectura directa de ficheros, evitando la necesidad de cargar la config completa.

### Pendiente para la próxima sesión

- [ ] Fase 7: Pipeline Orchestrator — implementar `src/refmate/ingest/pipeline.py` con flags `--from` y `--only`, verificación de artefactos y FLUSHDB Redis al finalizar.

---

## 2026-03-20 — FASE 7: Pipeline Orchestrator con CLI y verificación de artefactos

**Fase(s):** Fase 7: Pipeline Orchestrator
**Duración aproximada:** 15 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `src/refmate/ingest/pipeline.py` | Creado | Orquestador completo: `PHASES`, verificación de artefactos por fase, dispatcher `_run_phase`, invalidación Redis, `run_pipeline` async, CLI con `--from`/`--only` |

### Decisiones tomadas

- **Lazy imports dentro de `_run_phase`:** Los módulos pesados (FlagEmbedding, OpenRouter, Qdrant) se importan dentro del bloque `if phase == ...` correspondiente, no en el top-level del módulo. Esto evita que el simple `import refmate.ingest.pipeline` cargue el modelo BGE-m3 en memoria. Alternativa descartada: imports en el top-level — penalizaría el arranque y el `--help` con varios segundos de carga del modelo.

- **Wrappers de Structurer y Chunker en `pipeline.py` (composition root de ingesta):** Structurer necesita un `TextGenerator` inyectado y Chunker necesita config. No tienen un `run_*` standalone equivalente a `run_scraper`. En lugar de añadir `run_structurer(config)` a cada módulo (cambio de interfaz no pedido), se instancian directamente en `pipeline.py`, que es el composition root legítimo para la ingesta. Esta es la misma pauta que ya seguía `run_indexer` y `run_ocr_runner`.

- **Redis solo se invalida si `indexer` estuvo en las fases ejecutadas:** El ROADMAP dice "FLUSHDB Redis al finalizar" pero no especifica la condición. Si alguien ejecuta `--only structurer`, el índice vectorial en Qdrant no cambia, por lo que las respuestas cacheadas siguen siendo válidas. Invalidar siempre sería incorrecto y frustrante (el usuario perdería la caché por ejecutar solo el paso de estructuración). La condición `"indexer" in phases_to_run and config.cache.invalidate_on_ingest` es la más precisa.

- **Fallo de Redis no es bloqueante:** Si Qdrant está indexado pero Redis no está levantado, el pipeline ya completó su trabajo principal. Un `logger.warning` sin relanzar la excepción permite que el pipeline termine con éxito aunque Redis no sea accesible. Alternativa descartada: relanzar — haría que una caché inaccesible rompiera una indexación exitosa.

- **`--from` y `--only` mutuamente excluyentes vía `add_mutually_exclusive_group`:** argparse maneja la exclusividad automáticamente con mensaje de error claro. Alternativa descartada: validación manual post-parse — más código para el mismo resultado.

- **Abort inmediato si cualquier fase falla:** El pipeline re-lanza la excepción en cuanto una fase falla. Continuar con la siguiente fase tras un error produciría datos inconsistentes (ej: indexar chunks basados en un structurer fallido). El resumen parcial se loguea antes de re-lanzar para que el usuario vea qué había completado.

### Problemas encontrados

- Ninguno.

### Pendiente para la próxima sesión

- [ ] Fase 8: Guard Model — implementar `src/refmate/retrieval/guard.py` con Qwen3-4B vía OpenRouter y `src/refmate/prompts/guard_prompt.md`.

---

## 2026-03-20 — Fix FASE 7: tipos Callable, _check_scraper, stats en resumen, validación

**Fase(s):** Fase 7: Pipeline Orchestrator (fix)
**Duración aproximada:** 10 minutos

### Ficheros tocados
| Fichero | Acción | Descripción |
|---------|--------|-------------|
| `src/refmate/ingest/pipeline.py` | Modificado | 5 correcciones: tipo `Callable` en `_ARTIFACT_CHECKS`, `_check_scraper` vacío, eliminado `type: ignore`, stats en resumen final, validación de fases al inicio de `run_pipeline` |

### Decisiones tomadas

- **`_check_scraper` vacío en lugar de `None` en el dict:** El valor `None` para la fase scraper requería una guarda `if check_fn is not None` en `_check_artifacts`, añadiendo complejidad innecesaria y un camino de código que no se testea. Una función vacía `_check_scraper` que simplemente retorna sin hacer nada es más uniforme: todas las fases siguen el mismo patrón, `_check_artifacts` se simplifica a una línea, y el tipo del dict puede ser homogéneo (`Callable` en lugar de `Callable | None`).

- **`dict[str, Callable[[Path, RefMateConfig], None]]` en lugar de `dict[str, Any]`:** El tipo `Any` en `_ARTIFACT_CHECKS` silenciaba potenciales errores de tipo en mypy strict. El tipo preciso documenta el contrato del dict y permite que mypy verifique que todos los valores son funciones con la firma correcta. El único coste es la verbosidad del tipo, que se compensa con el beneficio de seguridad.

- **Resumen final con stats:** `summary[phase]["stats"]` se capturaba por fase pero nunca se emitía en el log de resumen. El ROADMAP pide "resumen final con estadísticas". Se añadió `stats_str` que loguea el dict de stats si no está vacío (el indexer devuelve `{}`, los demás devuelven métricas útiles como conteo de páginas/chunks). El formato `— {stats}` es compacto y no requiere formato especial por fase.

- **Validación de `phases_to_run` al inicio de `run_pipeline`:** Sin validación, una fase inválida pasaba por `_check_artifacts` (que hace `_ARTIFACT_CHECKS[fase]` con KeyError tardío) o llegaba al `raise ValueError` al final de `_run_phase`. Validar al inicio con un mensaje claro es más conforme al principio de fail-fast del ROADMAP.

### Problemas encontrados

- Ninguno — todas las correcciones son mecánicas y se verificaron con inspección de código fuente.

### Pendiente para la próxima sesión

- [ ] Fase 8: Guard Model — implementar `src/refmate/retrieval/guard.py` con Qwen3-4B vía OpenRouter y `src/refmate/prompts/guard_prompt.md`.

---
