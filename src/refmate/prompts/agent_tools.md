# Herramientas del Agente RAG — RefMate

Descripción de las herramientas disponibles para el agente RAG. Los esquemas
JSON de function calling se definen en `src/refmate/retrieval/agent.py`.

## search_dense

**Búsqueda semántica** por similitud de significado (vector denso BGE-m3).

Usar cuando la pregunta es conceptual o usa sinónimos/lenguaje coloquial.
Ejemplo: "¿Cuándo se puede descalificar a un jugador?"

Parámetros:
- `query` (str, requerido): Texto de búsqueda.
- `top_k` (int, opcional, default=5): Número de resultados.
- `doc_filter` (str, opcional): Filtrar por documento (`reglas-de-juego`, `rgc-fabm`, `add-fabm`).

## search_sparse

**Búsqueda léxica** por términos exactos (vector sparse BGE-m3).

Usar cuando la pregunta menciona números de regla, artículos o términos normativos específicos.
Ejemplo: "Regla 8:5", "Artículo 36 RGC", "exclusión de 2 minutos"

Parámetros:
- `query` (str, requerido): Texto de búsqueda.
- `top_k` (int, opcional, default=5): Número de resultados.
- `doc_filter` (str, opcional): Filtrar por documento.

## search_hybrid

**Búsqueda híbrida** combinando dense y sparse con Reciprocal Rank Fusion (RRF).

Usar cuando no estés seguro del tipo de búsqueda o cuando la pregunta combine
términos exactos con contexto conceptual. Es la opción más robusta.

Parámetros:
- `query` (str, requerido): Texto de búsqueda.
- `top_k` (int, opcional, default=5): Número de resultados.
- `doc_filter` (str, opcional): Filtrar por documento.

## get_chunk_by_id

**Acceso directo** a un chunk por su ID exacto.

Usar cuando el índice jerárquico te indica exactamente qué chunk necesitas,
o cuando un resultado previo referencia un chunk_id concreto.
Ejemplo: `get_chunk_by_id("reglas-de-juego:regla-8:8-5")`

Parámetros:
- `chunk_id` (str, requerido): ID del chunk a recuperar.

## get_related_chunks

**Expansión de referencias cruzadas**: devuelve chunks relacionados mediante
referencias explícitas entre artículos.

Usar cuando un chunk menciona "ver también", "según la regla X", o cuando
necesitas el contexto completo de un artículo que referencia otros.

Parámetros:
- `chunk_id` (str, requerido): ID del chunk del que expandir referencias.
- `max_results` (int, opcional, default=3): Número máximo de chunks relacionados.
