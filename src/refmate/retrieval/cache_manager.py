"""Orquestador de la caché semántica con paso de embedding incluido.

Responsabilidad única: adaptar la API de texto (question: str) al protocolo
SemanticCache (embedding: list[float]), combinando EmbeddingProvider y
SemanticCache en una interfaz de alto nivel orientada a strings.
"""

from __future__ import annotations

from loguru import logger

from refmate.core.models import CacheLookupResult
from refmate.core.protocols import EmbeddingProvider, SemanticCache


class CacheManager:
    """Orquestador de caché semántica con codificación de preguntas integrada.

    Recibe preguntas como texto, las codifica con el EmbeddingProvider inyectado
    y delega en el SemanticCache para lookup y store. El llamador no necesita
    gestionar embeddings directamente.
    """

    def __init__(self, cache: SemanticCache, embedder: EmbeddingProvider) -> None:
        """Inicializa el gestor de caché.

        Args:
            cache: Implementación de SemanticCache (protocolo) inyectada.
            embedder: Implementación de EmbeddingProvider (protocolo) inyectada.
        """
        self._cache = cache
        self._embedder = embedder
        logger.debug("CacheManager inicializado")

    async def lookup(self, question: str) -> CacheLookupResult:
        """Busca en caché la respuesta más similar a la pregunta dada.

        Codifica la pregunta con el embedder y delega en SemanticCache.lookup.

        Args:
            question: Texto de la pregunta del usuario.

        Returns:
            CacheLookupResult con hit_type ('direct', 'context' o 'miss'),
            la respuesta cacheada (None si miss) y la similitud coseno.
        """
        embedding = self._embedder.encode(question)
        logger.debug(f"CacheManager.lookup → codificando pregunta ({len(question)} chars)")
        result = await self._cache.lookup(embedding.dense)
        logger.debug(
            f"CacheManager.lookup → {result.hit_type} (sim={result.similarity:.3f})"
        )
        return result

    async def store(self, question: str, response: str) -> None:
        """Almacena una nueva respuesta en la caché.

        Codifica la pregunta con el embedder y delega en SemanticCache.store.
        Debe llamarse tras una respuesta del agente (miss o context hit).

        Args:
            question: Texto original de la pregunta.
            response: Respuesta generada por el agente.
        """
        embedding = self._embedder.encode(question)
        await self._cache.store(embedding.dense, question, response)
        logger.debug(f"CacheManager.store → almacenada respuesta para: {question[:60]!r}")

    async def invalidate_all(self) -> None:
        """Invalida toda la caché semántica.

        Delega en SemanticCache.invalidate_all. Llamado automáticamente
        al finalizar una re-ingesta si cache.invalidate_on_ingest=true.
        """
        logger.info("CacheManager: invalidando toda la caché…")
        await self._cache.invalidate_all()
