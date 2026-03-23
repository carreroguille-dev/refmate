"""Implementación de SemanticCache usando Redis como backend.

Responsabilidad única: almacenar y recuperar respuestas cacheadas en Redis,
calcular similitud coseno entre embeddings y aplicar los umbrales de hit.
"""

from __future__ import annotations

import json
import uuid

import numpy as np
import redis.asyncio as aioredis
from loguru import logger

from refmate.config import CacheConfig
from refmate.core.models import CacheLookupResult

# Prefijos de claves Redis
_KEY_PREFIX = "refmate:cache:"
_INDEX_KEY = "refmate:cache:keys"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calcula la similitud coseno entre dos vectores densos.

    Args:
        a: Vector de consulta.
        b: Vector almacenado en caché.

    Returns:
        Similitud coseno en el rango [−1, 1]. Devuelve 0.0 si algún vector es nulo.
    """
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    norm_a = float(np.linalg.norm(va))
    norm_b = float(np.linalg.norm(vb))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


class RedisSemanticCache:
    """Caché semántica respaldada por Redis.

    Almacena pares (embedding, pregunta, respuesta) con TTL configurable.
    En el lookup calcula similitud coseno contra todos los entries y aplica
    los umbrales de hit definidos en la configuración.

    Data model:
        - ``refmate:cache:keys``   → SET con todos los UUIDs de entradas activas.
        - ``refmate:cache:{uuid}`` → HASH con campos:
            - ``embedding``: JSON (list[float])
            - ``question``:  texto original de la pregunta
            - ``response``:  respuesta generada por el agente
            - ``hit_count``: entero — número de veces accedida con alta similitud
    """

    def __init__(self, config: CacheConfig) -> None:
        """Inicializa la conexión a Redis.

        Args:
            config: Configuración de la caché (host, port, db, umbrales, TTL, max_entries).
        """
        self._config = config
        self._ttl = config.ttl_days * 24 * 3600
        self._client: aioredis.Redis = aioredis.Redis(  # type: ignore[type-arg]
            host=config.host,
            port=config.port,
            db=config.db,
            decode_responses=False,
        )
        logger.debug(
            f"RedisSemanticCache inicializado → {config.host}:{config.port} "
            f"db={config.db} max_entries={config.max_entries}"
        )

    async def lookup(self, embedding: list[float]) -> CacheLookupResult:
        """Busca en caché la respuesta más similar al embedding dado.

        Escanea todas las entradas activas, calcula similitud coseno y aplica
        umbrales:
        - ``≥ similarity_direct_hit``: incrementa hit_count + renueva TTL.
          Si hit_count resultante ``≥ min_hits_for_direct`` → ``"direct"``.
          Si no → ``"context"`` (la entrada está en camino de graduarse).
        - ``[similarity_context_hit, similarity_direct_hit)`` → ``"context"``.
        - ``< similarity_context_hit`` → ``"miss"``.

        Las entradas cuyo hash ha expirado se eliminan automáticamente del índice.

        Args:
            embedding: Vector denso de la consulta actual.

        Returns:
            CacheLookupResult con hit_type, response y similarity.
        """
        raw_ids: set[bytes] = await self._client.smembers(_INDEX_KEY)  # type: ignore[assignment]

        best_sim = 0.0
        best_id: str | None = None
        best_response: str | None = None
        best_hit_count = 0

        for raw_id in raw_ids:
            entry_id = raw_id.decode()
            key = _KEY_PREFIX + entry_id
            data: dict[bytes, bytes] = await self._client.hgetall(key)  # type: ignore[assignment]

            if not data:
                # Hash expirado: limpiar del índice
                await self._client.srem(_INDEX_KEY, raw_id)
                logger.debug(f"Cache: entrada expirada eliminada del índice → {entry_id}")
                continue

            cached_emb: list[float] = json.loads(data[b"embedding"])
            sim = _cosine_similarity(embedding, cached_emb)

            if sim > best_sim:
                best_sim = sim
                best_id = entry_id
                best_response = data[b"response"].decode()
                best_hit_count = int(data[b"hit_count"])

        if best_id is None:
            logger.debug("Cache lookup → miss (caché vacía)")
            return CacheLookupResult(hit_type="miss", response=None, similarity=0.0)

        if best_sim >= self._config.similarity_direct_hit:
            # Incrementar hit_count y renovar TTL
            key = _KEY_PREFIX + best_id
            new_count = await self._client.hincrby(key, "hit_count", 1)
            await self._client.expire(key, self._ttl)

            if new_count >= self._config.min_hits_for_direct:
                logger.debug(
                    f"Cache lookup → direct (sim={best_sim:.3f}, hits={new_count}) "
                    f"id={best_id}"
                )
                return CacheLookupResult(
                    hit_type="direct", response=best_response, similarity=best_sim
                )
            else:
                logger.debug(
                    f"Cache lookup → context/graduating (sim={best_sim:.3f}, "
                    f"hits={new_count}/{self._config.min_hits_for_direct}) id={best_id}"
                )
                return CacheLookupResult(
                    hit_type="context", response=best_response, similarity=best_sim
                )

        if best_sim >= self._config.similarity_context_hit:
            logger.debug(
                f"Cache lookup → context (sim={best_sim:.3f}, hits={best_hit_count}) "
                f"id={best_id}"
            )
            return CacheLookupResult(
                hit_type="context", response=best_response, similarity=best_sim
            )

        logger.debug(f"Cache lookup → miss (best_sim={best_sim:.3f})")
        return CacheLookupResult(hit_type="miss", response=None, similarity=best_sim)

    async def store(self, embedding: list[float], question: str, response: str) -> None:
        """Almacena un nuevo par (pregunta, respuesta) en la caché.

        Si el número de entradas supera ``max_entries``, evicta la entrada
        con menor ``hit_count`` antes de almacenar la nueva.

        Args:
            embedding: Vector denso de la pregunta.
            question: Texto original de la pregunta.
            response: Respuesta generada por el agente.
        """
        await self._evict_if_needed()

        entry_id = str(uuid.uuid4())
        key = _KEY_PREFIX + entry_id
        await self._client.hset(  # type: ignore[misc]
            key,
            mapping={
                "embedding": json.dumps(embedding),
                "question": question,
                "response": response,
                "hit_count": "1",
            },
        )
        await self._client.expire(key, self._ttl)
        await self._client.sadd(_INDEX_KEY, entry_id)
        logger.debug(f"Cache store → {entry_id} | pregunta: {question[:60]!r}")

    async def invalidate_all(self) -> None:
        """Elimina todas las entradas de la caché y el índice.

        Usa SCAN para no bloquear Redis en cachés grandes.
        """
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = await self._client.scan(  # type: ignore[misc]
                cursor, match=f"{_KEY_PREFIX}*", count=100
            )
            if keys:
                await self._client.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        await self._client.delete(_INDEX_KEY)
        logger.info(f"Cache invalidada: {deleted} entradas eliminadas")

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    async def _evict_if_needed(self) -> None:
        """Evicta la entrada con menor hit_count si se supera max_entries.

        Carga los hit_counts de todas las entradas activas y elimina la que
        tenga menor valor. En caso de empate, elimina la primera encontrada.
        """
        count = await self._client.scard(_INDEX_KEY)
        if count < self._config.max_entries:
            return

        logger.debug(
            f"Cache llena ({count}/{self._config.max_entries}). Evictando entrada con menor hit_count…"
        )

        raw_ids: set[bytes] = await self._client.smembers(_INDEX_KEY)  # type: ignore[assignment]

        min_hits = None
        min_id: str | None = None

        for raw_id in raw_ids:
            entry_id = raw_id.decode()
            key = _KEY_PREFIX + entry_id
            raw_hits = await self._client.hget(key, "hit_count")

            if raw_hits is None:
                # Hash expirado: eliminarlo del índice y continuar
                await self._client.srem(_INDEX_KEY, raw_id)
                continue

            hits = int(raw_hits)
            if min_hits is None or hits < min_hits:
                min_hits = hits
                min_id = entry_id

        if min_id is not None:
            await self._client.delete(_KEY_PREFIX + min_id)
            await self._client.srem(_INDEX_KEY, min_id)
            logger.debug(f"Cache evicción → eliminada entrada {min_id} (hit_count={min_hits})")
