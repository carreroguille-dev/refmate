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
