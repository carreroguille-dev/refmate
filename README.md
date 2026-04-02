<div align="center">

  <img src="docs/refmate.png" alt="Logo RefMate" width="300"/>

  <h1>RefMate</h1>
  <h3>Asistente Arbitral de Balonmano</h3>
  
  <p>Bot de Telegram con RAG agéntico para consulta de normativa oficial de balonmano pista en el ámbito andaluz.</p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12" />
    <img src="https://img.shields.io/badge/Telegram-Bot-2CA5E0?style=flat-square&logo=telegram&logoColor=white" alt="Telegram Bot" />
    <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker" />
    <img src="https://img.shields.io/badge/Qdrant-Vector%20Store-C5314C?style=flat-square" alt="Qdrant" />
    <img src="https://img.shields.io/badge/Redis-Cache-DC382D?style=flat-square&logo=redis&logoColor=white" alt="Redis" />
  </p>

</div>

---

## ¿Qué hace?

RefMate permite a árbitros y entrenadores consultar en lenguaje natural tres documentos normativos:
---

## ¿Qué hace?

RefMate permite a árbitros y entrenadores consultar en lenguaje natural tres documentos normativos:

- **Reglas de Juego** (RFEBM/IHF, Julio 2025)
- **Reglamento General de Competiciones** — RGC (FABM, 2025)
- **Acuerdo Disciplinario Deportivo** — ADD (FABM, 2024)

Una pregunta como _"¿Cuándo se puede descalificar a un jugador?"_ desencadena un agente que decide qué estrategia de búsqueda usar (semántica, léxica o híbrida), recupera los chunks relevantes, expande referencias cruzadas entre documentos y genera una respuesta citando los artículos concretos.

---

## Stack

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.12, uv |
| OCR | LightOnOCR-1B (vLLM local, endpoint OpenAI-compatible) |
| LLM estructuración/agente | Qwen3-235B-A22B via OpenRouter |
| Guard model | Qwen3-8B via OpenRouter (no-thinking) |
| Embeddings | BGE-m3 local (FlagEmbedding), dense + sparse |
| Vector store | Qdrant (Docker, búsqueda híbrida RRF nativa) |
| Caché semántica | Redis |
| Interfaz | python-telegram-bot (polling) |
| Infraestructura | Docker Compose (despliegue en EC2) |

---

## Arquitectura

El sistema tiene dos partes claramente separadas: un **pipeline de ingesta offline** y un **flujo de consulta online**.

### Pipeline de ingesta (offline)

```
PDF remoto
  │
  ▼  Fase 1: Scraper
data/raw/{doc}.pdf     ← SHA-256 + manifest.json (deduplicación)
  │
  ▼  Fase 2: Cropper
data/images/{doc}/page_NNNN_crop.png   ← pypdfium2 + máscaras en % por documento
  │
  ▼  Fase 3: OCR
data/ocr/{doc}_raw.txt                 ← LightOnOCR vía vLLM local
  │
  ▼  Fase 4: Structurer
data/structured/{doc}.md               ← Qwen3-235B convierte a Markdown jerárquico
data/structured/{doc}_refs.json        ← referencias cruzadas detectadas [REF:doc:sec]
  │
  ▼  Fase 5: Chunker
data/chunks/{doc}_chunks.json          ← chunk por unidad normativa (regla/artículo)
data/chunks/cross_references_graph.json ← grafo bidireccional de referencias
data/index/hierarchical_index.json     ← índice de títulos sin texto (solo para el agente)
  │
  ▼  Fase 6: Indexer
Qdrant: colección handball_normativa   ← dense (1024 dims) + sparse, payload indexes
```

El pipeline está orquestado por `src/refmate/ingest/pipeline.py` con flags `--from` y `--only` para reejecutar fases parcialmente.

### Flujo de consulta (online)

```
Telegram message
      │
      ▼
  QueryEngine.query()
      │
      ├── 1. Guard (Qwen3-8B)
      │       "normal" → continuar
      │       "out_of_scope" / "injection" → respuesta predefinida, FIN
      │
      ├── 2. Caché semántica (BGE-m3 + Redis)
      │       direct hit (≥0.95, ≥3 hits) → respuesta cacheada, FIN
      │       context hit (0.85-0.95) → se pasa como contexto al agente
      │       miss → continuar
      │
      ├── 3. RAG Agent (Qwen3-235B, tool calling nativo)
      │       ├─ search_dense(query, top_k, doc_filter?)
      │       ├─ search_sparse(query, top_k, doc_filter?)
      │       ├─ search_hybrid(query, top_k, doc_filter?)
      │       ├─ get_chunk_by_id(chunk_id)
      │       └─ get_related_chunks(chunk_id, max_results)
      │       → expande referencias cruzadas → genera respuesta citando artículos
      │
      └── 4. Cache store (nueva entrada para futuras consultas similares)
```

### Principios de diseño

El código sigue **SOLID estricto**:

- **Protocolos** en `src/refmate/core/protocols.py`: definen los contratos del sistema (`OCRProvider`, `TextGenerator`, `GuardModel`, `EmbeddingProvider`, `VectorStore`, `SemanticCache`, `Agent`). Ningún módulo de alto nivel importa implementaciones concretas.
- **Implementaciones** únicamente en `src/refmate/infrastructure/`: cada clase implementa exactamente un protocolo.
- **Dependency Injection**: los constructores reciben protocolos. El único lugar donde se instancian implementaciones concretas es el composition root (`src/refmate/bot/main.py`).
- **DTOs inmutables**: todos los modelos de datos en `src/refmate/core/models.py` con Pydantic `frozen=True`.
- **Zero hardcoding**: todo valor configurable en `config.yaml`. Prompts del LLM en `src/refmate/prompts/*.md`. Mensajes estáticos del bot como constantes en el módulo correspondiente.

---

## Estructura del proyecto

```
refmate/
├── config.yaml                    # Única fuente de verdad de configuración
├── docker-compose.yml
├── Dockerfile                     # Imagen del bot (sin FlagEmbedding)
├── Dockerfile.ingest              # Imagen de ingesta (con FlagEmbedding)
├── .env.example
│
├── src/refmate/
│   ├── config.py                  # Carga config.yaml, resuelve ${ENV_VAR}, singleton
│   ├── core/
│   │   ├── protocols.py           # Protocolos (interfaces abstractas)
│   │   └── models.py              # DTOs: Chunk, QueryResult, AgentResult…
│   ├── infrastructure/
│   │   ├── llm/openrouter.py      # OpenRouterTextGenerator + OpenRouterToolCallingLLM
│   │   ├── embeddings/bge_m3.py   # BGEM3EmbeddingProvider (dense + sparse local)
│   │   ├── vectorstore/qdrant.py  # QdrantVectorStore
│   │   ├── cache/redis_cache.py   # RedisSemanticCache
│   │   └── ocr/lighton.py         # LightOnOCROCRProvider
│   ├── ingest/
│   │   ├── scraper.py             # Fase 1: descarga PDFs con deduplicación SHA-256
│   │   ├── cropper.py             # Fase 2: renderizado + recorte por máscara
│   │   ├── ocr_runner.py          # Fase 3: orquesta OCR página a página
│   │   ├── structurer.py          # Fase 4: texto plano → Markdown jerárquico
│   │   ├── chunker.py             # Fase 5: Markdown → chunks + grafo de refs
│   │   ├── indexer.py             # Fase 6: embeddings → Qdrant
│   │   └── pipeline.py            # Fase 7: orquestador con flags --from/--only
│   ├── retrieval/
│   │   ├── guard.py               # Filtro de scope e inyecciones
│   │   ├── cache_manager.py       # Lógica de umbrales de caché
│   │   ├── agent.py               # RAGAgent con tool calling y loop multi-turn
│   │   ├── cross_refs.py          # Expansión de referencias cruzadas
│   │   └── query_engine.py        # Orquestador online: guard → cache → agent
│   ├── bot/
│   │   ├── main.py                # Composition root: DI + polling
│   │   └── handlers.py            # /start, /help, message_handler, rate limit
│   └── prompts/
│       ├── system_prompt.md
│       ├── guard_prompt.md
│       ├── agent_tools.md
│       └── structuring/           # Un prompt por documento
│
├── data/
│   ├── index/                     # hierarchical_index.json (commitear)
│   ├── chunks/                    # Chunks JSON + grafo de refs (commitear)
│   └── …                          # raw/, images/, ocr/, structured/ (no commitear)
│
└── docs/
    └── test-queries.md            # Batería de 30+ queries de prueba
```

---

## Instalación y configuración

### Requisitos

- Python 3.12, `uv`
- Docker y Docker Compose
- GPU con ~8 GB VRAM para vLLM local (OCR durante la ingesta)
- Clave de API de OpenRouter

### Variables de entorno

Copia `.env.example` a `.env` y rellena:

```bash
OPENROUTER_API_KEY=sk-or-...
TELEGRAM_BOT_TOKEN=...

# Solo para desarrollo local (fuera de Docker)
REDIS_HOST=localhost
QDRANT_HOST=localhost
```

### Levantar servicios base

```bash
docker compose up qdrant redis -d
```

---

## Ingesta de documentos

La ingesta descarga los PDFs, los procesa con OCR, los estructura con un LLM y los indexa en Qdrant. Es un proceso offline que solo necesita ejecutarse una vez (o al actualizar los documentos).

### Paso 1: Levantar el servidor vLLM (OCR)

```bash
vllm serve lightonai/LightOnOCR-1B-1025 \
    --limit-mm-per-prompt '{"image": 1}' \
    --mm-processor-cache-gb 0 \
    --no-enable-prefix-caching \
    --gpu-memory-utilization 0.85 \
    --max-model-len 4096
```

### Paso 2: Ejecutar el pipeline

```bash
# Pipeline completo (Dockerizado)
docker compose --profile ingest run ingest

# Pipeline completo (local, para desarrollo)
uv run python -m refmate.ingest.pipeline

# Reejecutar desde una fase concreta
docker compose --profile ingest run ingest --from structurer

# Ejecutar solo una fase
docker compose --profile ingest run ingest --only indexer
```

---

## Ejecutar el bot

```bash
# Bot dockerizado (producción)
docker compose up bot -d
docker compose logs -f bot

# Bot local (desarrollo)
uv run python -m refmate.bot.main
```

El bot responde a:
- `/start` — mensaje de bienvenida con documentos disponibles
- `/help` — ejemplos de preguntas
- Cualquier texto — consulta al sistema RAG

Formato de respuesta:
```
📖 [Respuesta con citaciones de artículos]

📎 Fuentes: 8:5 (Reglas de Juego), Art. 36 (ADD)
⚡ 1.2s | 🔍 hybrid | 💾 miss
```

---

## Decisiones técnicas destacadas

### FlagEmbedding: bypass del API de alto nivel

La integración oficial de FlagEmbedding con `transformers>=4.44` produce un `IndexError` al hacer `.encode()` porque el tokenizer rechaza el kwarg `convert_to_numpy=True`. La solución es llamar directamente al tokenizer y al modelo interno, replicando la lógica de `_process_token_weights`. Se añadió `transformers<5.0` en `pyproject.toml`.

### Qwen3 no-thinking vía `/no_think`

Para el guard model y el structurer se desactiva el chain-of-thought de Qwen3 añadiendo `/no_think` al inicio del system prompt antes de construir el payload HTTP. Con max_tokens=50 en el guard, el modo thinking agotaba el presupuesto de tokens sin llegar a la respuesta categórica.

### Agente: single-turn infra / multi-turn loop

`OpenRouterToolCallingLLM.chat()` hace un único turno HTTP y devuelve `(content, [])` o `(None, tool_calls)`. El loop multi-turn (acumulación de mensajes, ejecución de tools, re-llamada) es responsabilidad exclusiva de `RAGAgent.run()`. Esto mantiene la infra stateless y testeable.

### Hostname de servicios en Docker vs local

Los hostnames `qdrant` y `redis` solo resuelven dentro de la red Docker Compose. `config.yaml` usa `${QDRANT_HOST:-qdrant}` y `${REDIS_HOST:-redis}`, de modo que en desarrollo local basta con `QDRANT_HOST=localhost` en `.env`.

### Chunking por unidad normativa

En lugar de chunking por tamaño fijo, cada chunk corresponde a una unidad semántica del documento: subregla en las Reglas de Juego (`## N:M`), sección en el RGC, artículo en el ADD. El campo `texto_con_contexto` incluye el breadcrumb jerárquico completo para mejorar la calidad de los embeddings.

### Caché semántica con umbrales

- `similarity ≥ 0.95` **y** `hit_count ≥ 3` → respuesta directa sin llamar al LLM.
- `0.85 ≤ similarity < 0.95` → se pasa como contexto adicional al agente.
- `similarity < 0.85` → miss. La nueva respuesta se almacena con `hit_count=1`.

Esto evita que una pregunta ligeramente reformulada cortocircuite el agente hasta que se haya confirmado que la respuesta cacheada es fiable (3 hits).

---

## Desarrollo

```bash
# Instalar dependencias
uv sync

# Añadir dependencia
uv add <pkg>

# Verificar configuración
uv run python -c "from refmate.config import get_config; print(get_config())"
```

Los artefactos de `data/index/` y `data/chunks/` se commitean porque son el resultado del pipeline de ingesta. El resto de `data/` está en `.gitignore`.

---

## Deuda técnica

- Tests de integración pendientes para todas las fases
- El structurer tiene un problema de solapamiento entre bloques en textos largos (workaround aplicado con `find` limitado al último tramo, no resuelto en origen)
- Verificar rendimiento de FlagEmbedding en CPU en producción (EC2 sin GPU)
- Verificación end-to-end pendiente con Qdrant indexado + flujo completo de caché
