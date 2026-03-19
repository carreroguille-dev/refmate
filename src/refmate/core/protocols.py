"""Protocolos (interfaces) del sistema RefMate.

Define los contratos abstractos que separan módulos de alto nivel de sus
implementaciones concretas. Ningún módulo de alto nivel importa clases de
infrastructure/ — solo estos protocolos.
"""

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from refmate.core.models import (
    AgentResult,
    CacheLookupResult,
    Chunk,
    EmbeddingResult,
    GuardResult,
    SearchResult,
    SparseVector,
)


@runtime_checkable
class OCRProvider(Protocol):
    """Extrae texto de una imagen mediante OCR."""

    async def extract_text(self, image_path: Path) -> str:
        """Extrae y devuelve el texto contenido en la imagen.

        Args:
            image_path: Ruta absoluta a la imagen PNG/JPG.

        Returns:
            Texto extraído como string.
        """
        ...


@runtime_checkable
class TextGenerator(Protocol):
    """Genera texto a partir de un prompt usando un LLM."""

    async def generate(self, system_prompt: str, user_prompt: str, **kwargs: object) -> str:
        """Genera una respuesta dado un system prompt y un user prompt.

        Args:
            system_prompt: Instrucciones del sistema para el modelo.
            user_prompt: Mensaje del usuario o input a procesar.
            **kwargs: Parámetros adicionales (temperature, max_tokens, etc.).

        Returns:
            Texto generado por el modelo.
        """
        ...


@runtime_checkable
class GuardModel(Protocol):
    """Filtra consultas antes de que lleguen al agente RAG."""

    async def classify(self, query: str) -> GuardResult:
        """Clasifica una consulta de usuario.

        Args:
            query: Texto de la consulta del usuario.

        Returns:
            GuardResult con clasificación 'normal', 'out_of_scope' o 'injection'.
        """
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Genera embeddings dense y sparse para búsqueda híbrida."""

    def encode(self, text: str) -> EmbeddingResult:
        """Genera embedding para un único texto.

        Args:
            text: Texto a encodificar.

        Returns:
            EmbeddingResult con vectores dense y sparse.
        """
        ...

    def encode_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Genera embeddings para un lote de textos.

        Args:
            texts: Lista de textos a encodificar.

        Returns:
            Lista de EmbeddingResult en el mismo orden que la entrada.
        """
        ...


@runtime_checkable
class VectorStore(Protocol):
    """Almacena y recupera chunks mediante búsqueda vectorial."""

    async def upsert(self, chunks: list[Chunk], embeddings: list[EmbeddingResult]) -> int:
        """Inserta o actualiza chunks con sus embeddings.

        Args:
            chunks: Lista de chunks a indexar.
            embeddings: Lista de embeddings en el mismo orden que chunks.

        Returns:
            Número de puntos insertados/actualizados.
        """
        ...

    async def search_dense(
        self,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[SearchResult]:
        """Búsqueda por similitud semántica (vector denso).

        Args:
            vector: Vector de consulta (dense).
            top_k: Número máximo de resultados.
            filters: Filtros opcionales por metadatos (documento_id, fuente, nivel).

        Returns:
            Lista de SearchResult ordenada por score descendente.
        """
        ...

    async def search_sparse(
        self,
        sparse_vector: SparseVector,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[SearchResult]:
        """Búsqueda por términos exactos (vector sparse).

        Args:
            sparse_vector: Vector sparse de consulta.
            top_k: Número máximo de resultados.
            filters: Filtros opcionales por metadatos.

        Returns:
            Lista de SearchResult ordenada por score descendente.
        """
        ...

    async def search_hybrid(
        self,
        dense: list[float],
        sparse: SparseVector,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[SearchResult]:
        """Búsqueda híbrida combinando dense y sparse con RRF.

        Args:
            dense: Vector denso de consulta.
            sparse: Vector sparse de consulta.
            top_k: Número máximo de resultados.
            filters: Filtros opcionales por metadatos.

        Returns:
            Lista de SearchResult ordenada por score RRF descendente.
        """
        ...

    async def get_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        """Recupera chunks por sus IDs para expansión de referencias.

        Args:
            chunk_ids: Lista de chunk_ids a recuperar.

        Returns:
            Lista de Chunk encontrados (puede ser menor si alguno no existe).
        """
        ...


@runtime_checkable
class SemanticCache(Protocol):
    """Caché semántica para respuestas frecuentes basada en similitud."""

    async def lookup(self, embedding: list[float]) -> CacheLookupResult:
        """Busca en caché por similitud semántica.

        Args:
            embedding: Vector denso de la consulta actual.

        Returns:
            CacheLookupResult con hit_type 'direct', 'context' o 'miss'.
        """
        ...

    async def store(self, embedding: list[float], question: str, response: str) -> None:
        """Almacena una respuesta en la caché.

        Args:
            embedding: Vector denso de la pregunta.
            question: Texto original de la pregunta.
            response: Respuesta generada por el agente.
        """
        ...

    async def invalidate_all(self) -> None:
        """Invalida toda la caché (usado al re-indexar documentos)."""
        ...


@runtime_checkable
class Agent(Protocol):
    """Agente RAG que decide estrategia de búsqueda y genera respuesta."""

    async def run(self, query: str, context: str | None) -> AgentResult:
        """Procesa una consulta con tool calling y genera respuesta.

        Args:
            query: Pregunta del usuario (ya validada por el guard).
            context: Contexto adicional de caché (opcional, hit de 0.85-0.95).

        Returns:
            AgentResult con la respuesta y metadata de la búsqueda.
        """
        ...
