"""DTOs inmutables del sistema RefMate.

Todos los modelos de datos compartidos entre módulos. Usar Pydantic
BaseModel con frozen=True para garantizar inmutabilidad.
"""

from pydantic import BaseModel, ConfigDict


class Chunk(BaseModel):
    """Unidad normativa indexada con metadatos completos."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    """Identificador único. Formato: '{doc_id}:{seccion}:{subseccion}'."""
    documento_id: str
    """ID del documento fuente. Ej: 'reglas-de-juego'."""
    documento_nombre: str
    """Nombre legible del documento. Ej: 'Reglas de Juego (Julio 2025)'."""
    fuente: str
    """Organismo emisor. Ej: 'RFEBM/IHF', 'FABM'."""
    jerarquia: list[str]
    """Cadena jerárquica. Ej: ['Regla 8: Faltas...', '8:5 Descalificación']."""
    nivel: str
    """Nivel de profundidad: 'regla', 'subregla', 'articulo', etc."""
    titulo_seccion: str
    """Título de la sección. Ej: '8:5 Descalificación'."""
    texto: str
    """Texto íntegro de la unidad normativa."""
    texto_con_contexto: str
    """Breadcrumb de jerarquía + texto (usado para embeddings)."""
    referencias_salientes: list[str]
    """chunk_ids que este chunk referencia."""
    referencias_entrantes: list[str]
    """chunk_ids que referencian a este chunk."""
    num_tokens_approx: int
    """Estimación del número de tokens."""


class EmbeddingResult(BaseModel):
    """Resultado de encodificación con vectores dense y sparse."""

    model_config = ConfigDict(frozen=True)

    dense: list[float]
    """Vector denso (1024 dimensiones para BGE-m3)."""
    sparse_indices: list[int]
    """Índices del vector sparse."""
    sparse_values: list[float]
    """Valores del vector sparse."""


class SparseVector(BaseModel):
    """Vector sparse para búsqueda por términos."""

    model_config = ConfigDict(frozen=True)

    indices: list[int]
    values: list[float]


class SearchResult(BaseModel):
    """Resultado de una búsqueda en el vector store."""

    model_config = ConfigDict(frozen=True)

    chunk: Chunk
    score: float
    match_type: str
    """Tipo de búsqueda: 'dense', 'sparse' o 'hybrid'."""


class GuardResult(BaseModel):
    """Resultado de clasificación del guard model."""

    model_config = ConfigDict(frozen=True)

    classification: str
    """Clasificación: 'normal', 'out_of_scope' o 'injection'."""
    confidence: float


class CacheLookupResult(BaseModel):
    """Resultado de consulta en la caché semántica."""

    model_config = ConfigDict(frozen=True)

    hit_type: str
    """Tipo de hit: 'direct', 'context' o 'miss'."""
    response: str | None
    """Respuesta cacheada (None si miss)."""
    similarity: float
    """Similitud coseno con la consulta más cercana."""


class AgentResult(BaseModel):
    """Resultado devuelto por el agente RAG."""

    model_config = ConfigDict(frozen=True)

    response: str
    chunks_used: list[str]
    """chunk_ids utilizados para generar la respuesta."""
    search_strategy: str
    """Estrategia empleada: 'dense', 'sparse', 'hybrid', 'multi_doc'."""
    tools_called: list[str]
    """Nombres de las tools invocadas por el agente."""


class QueryResult(BaseModel):
    """Resultado completo de una consulta al sistema."""

    model_config = ConfigDict(frozen=True)

    response: str
    chunks_used: list[str]
    search_strategy: str
    cache_hit: str
    """Tipo de cache hit: 'direct', 'context' o 'miss'."""
    guard_result: str
    """Clasificación del guard: 'normal', 'out_of_scope' o 'injection'."""
    latency_ms: int
    cost_tokens_approx: int
