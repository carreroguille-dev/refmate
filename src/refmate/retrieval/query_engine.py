"""Query Engine — orquestador del flujo online completo.

Responsabilidad única: coordinar guard → cache → agente → cache store y
devolver un QueryResult con la respuesta y toda la metadata de la consulta.
"""

from __future__ import annotations

import time

from loguru import logger

from refmate.config import RefMateConfig
from refmate.core.models import QueryResult
from refmate.core.protocols import Agent, EmbeddingProvider, GuardModel, SemanticCache
from refmate.retrieval.cache_manager import CacheManager
from refmate.retrieval.guard import RESPONSE_INJECTION, RESPONSE_OUT_OF_SCOPE


class QueryEngine:
    """Orquestador del flujo online de consulta.

    Coordina las dependencias inyectadas siguiendo el flujo:
    guard → cache lookup → agente RAG → cache store → QueryResult.

    Las consultas fuera de scope o con inyección de prompt se cortan antes
    de llegar al agente. Los direct cache hits se resuelven sin llamar al LLM.
    """

    def __init__(
        self,
        guard: GuardModel,
        cache: SemanticCache,
        agent: Agent,
        embedder: EmbeddingProvider,
        config: RefMateConfig,
    ) -> None:
        """Inicializa el query engine con todas sus dependencias.

        Args:
            guard: Implementación de GuardModel (protocolo) inyectada.
            cache: Implementación de SemanticCache (protocolo) inyectada.
            agent: Implementación de Agent (protocolo) inyectada.
            embedder: Implementación de EmbeddingProvider (protocolo) inyectada.
            config: Configuración validada del sistema.
        """
        self._guard = guard
        self._agent = agent
        self._config = config
        # CacheManager combina SemanticCache + EmbeddingProvider en una API de texto
        self._cache_manager = CacheManager(cache, embedder)
        logger.info("QueryEngine inicializado")

    async def query(self, question: str) -> QueryResult:
        """Procesa una consulta de usuario de extremo a extremo.

        Flujo:
        1. Guard Model filtra la consulta.
        2. Cache lookup decide si se necesita llamar al agente.
        3. Agente RAG genera la respuesta si la caché no la cubre.
        4. La respuesta se almacena en caché para consultas futuras.

        Args:
            question: Texto de la pregunta del usuario.

        Returns:
            QueryResult con respuesta, metadata de búsqueda y latencia.
        """
        t0 = time.monotonic()

        # ------------------------------------------------------------------ #
        # Paso 1: Guard Model                                                  #
        # ------------------------------------------------------------------ #
        logger.info(f"QueryEngine: procesando consulta ({len(question)} chars)")
        guard_result = await self._guard.classify(question)
        logger.debug(f"QueryEngine: guard → {guard_result.classification}")

        if guard_result.classification == "out_of_scope":
            return QueryResult(
                response=RESPONSE_OUT_OF_SCOPE,
                chunks_used=[],
                search_strategy="none",
                cache_hit="miss",
                guard_result="out_of_scope",
                latency_ms=_elapsed_ms(t0),
                cost_tokens_approx=0,
            )

        if guard_result.classification == "injection":
            return QueryResult(
                response=RESPONSE_INJECTION,
                chunks_used=[],
                search_strategy="none",
                cache_hit="miss",
                guard_result="injection",
                latency_ms=_elapsed_ms(t0),
                cost_tokens_approx=0,
            )

        # ------------------------------------------------------------------ #
        # Paso 2: Cache lookup                                                 #
        # ------------------------------------------------------------------ #
        cache_result = await self._cache_manager.lookup(question)
        logger.debug(
            f"QueryEngine: cache → {cache_result.hit_type} "
            f"(sim={cache_result.similarity:.3f})"
        )

        if cache_result.hit_type == "direct":
            logger.info("QueryEngine: direct cache hit — respondiendo sin LLM")
            return QueryResult(
                response=cache_result.response,  # type: ignore[arg-type]
                chunks_used=[],
                search_strategy="cached",
                cache_hit="direct",
                guard_result="normal",
                latency_ms=_elapsed_ms(t0),
                cost_tokens_approx=0,
            )

        # context hit → pasar como contexto al agente; miss → context=None
        context = cache_result.response if cache_result.hit_type == "context" else None

        # ------------------------------------------------------------------ #
        # Paso 3: Agente RAG                                                   #
        # ------------------------------------------------------------------ #
        logger.info(
            f"QueryEngine: llamando al agente "
            f"(cache={cache_result.hit_type}, context={'sí' if context else 'no'})"
        )
        agent_result = await self._agent.run(question, context)
        logger.debug(
            f"QueryEngine: agente completado — strategy={agent_result.search_strategy}, "
            f"chunks={len(agent_result.chunks_used)}, tools={agent_result.tools_called}"
        )

        # ------------------------------------------------------------------ #
        # Paso 4: Cache store                                                  #
        # ------------------------------------------------------------------ #
        await self._cache_manager.store(question, agent_result.response)
        logger.debug("QueryEngine: respuesta almacenada en caché")

        latency = _elapsed_ms(t0)
        logger.info(
            f"QueryEngine: consulta completada en {latency}ms — "
            f"guard=normal, cache={cache_result.hit_type}, "
            f"strategy={agent_result.search_strategy}"
        )

        return QueryResult(
            response=agent_result.response,
            chunks_used=agent_result.chunks_used,
            search_strategy=agent_result.search_strategy,
            cache_hit=cache_result.hit_type,
            guard_result="normal",
            latency_ms=latency,
            cost_tokens_approx=0,
        )


def _elapsed_ms(t0: float) -> int:
    """Calcula los milisegundos transcurridos desde ``t0``.

    Args:
        t0: Timestamp de inicio obtenido con ``time.monotonic()``.

    Returns:
        Milisegundos enteros transcurridos.
    """
    return int((time.monotonic() - t0) * 1000)
