"""Vector store Qdrant con soporte de búsqueda híbrida dense+sparse.

Implementa VectorStore usando qdrant-client async.
Soporta búsqueda dense, sparse y hybrid (RRF nativo de Qdrant).
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from loguru import logger
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    NamedSparseVector,
    NamedVector,
    PayloadSchemaType,
    PointStruct,
    Prefetch,
    SparseIndexParams,
    SparseVector as QdrantSparseVector,
    SparseVectorParams,
    VectorParams,
)

from refmate.config import QdrantConfig
from refmate.core.models import Chunk, EmbeddingResult, SearchResult, SparseVector

_UPSERT_BATCH_SIZE = 50

_DISTANCE_MAP = {
    "Cosine": Distance.COSINE,
    "Dot": Distance.DOT,
    "Euclid": Distance.EUCLID,
}


def _chunk_id_to_uuid(chunk_id: str) -> str:
    """Convierte un chunk_id a UUID determinista para usar como ID en Qdrant.

    Args:
        chunk_id: Identificador del chunk (ej: 'reglas-de-juego:regla-8:8-5').

    Returns:
        UUID como string.
    """
    hex_digest = hashlib.md5(chunk_id.encode()).hexdigest()  # noqa: S324
    return str(uuid.UUID(hex_digest))


def _build_filter(filters: dict[str, Any] | None) -> Filter | None:
    """Construye un Filter de Qdrant a partir de un dict campo→valor.

    Args:
        filters: Dict de campo → valor (ej: {'documento_id': 'reglas-de-juego'}).

    Returns:
        Filter de Qdrant o None si no hay filtros.
    """
    if not filters:
        return None
    conditions = [
        FieldCondition(key=field, match=MatchValue(value=value))
        for field, value in filters.items()
    ]
    return Filter(must=conditions)


def _point_to_search_result(point: Any, match_type: str) -> SearchResult:
    """Convierte un ScoredPoint de Qdrant a SearchResult.

    Args:
        point: ScoredPoint con payload y score.
        match_type: Tipo de búsqueda ('dense', 'sparse', 'hybrid').

    Returns:
        SearchResult con Chunk reconstruido desde el payload.
    """
    chunk = Chunk.model_validate(point.payload or {})
    return SearchResult(chunk=chunk, score=float(point.score), match_type=match_type)


class QdrantVectorStore:
    """Vector store Qdrant con named vectors dense+sparse y búsqueda híbrida RRF."""

    def __init__(self, config: QdrantConfig) -> None:
        """Inicializa el cliente Qdrant async.

        Args:
            config: Configuración de conexión y colección Qdrant.
        """
        self._config = config
        self._client = AsyncQdrantClient(host=config.host, port=config.port)
        logger.info(f"QdrantVectorStore conectado a {config.host}:{config.port}")

    async def ensure_collection(self, recreate: bool = False) -> None:
        """Crea o recrea la colección con named vectors dense y sparse.

        Crea además payload indexes en documento_id, fuente y nivel
        para filtrado eficiente en búsquedas.

        Args:
            recreate: Si True, elimina la colección existente antes de recrearla.
        """
        collection = self._config.collection
        exists = await self._client.collection_exists(collection)

        if exists and recreate:
            logger.info(f"Eliminando colección existente '{collection}' para recrear...")
            await self._client.delete_collection(collection)
            exists = False

        if not exists:
            dense_cfg = self._config.vectors.dense
            sparse_cfg = self._config.vectors.sparse
            distance = _DISTANCE_MAP.get(dense_cfg.get("distance", "Cosine"), Distance.COSINE)
            on_disk = sparse_cfg.get("index", {}).get("on_disk", False)

            await self._client.create_collection(
                collection_name=collection,
                vectors_config={
                    "dense": VectorParams(size=dense_cfg["size"], distance=distance)
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(index=SparseIndexParams(on_disk=on_disk))
                },
            )
            logger.info(f"Colección '{collection}' creada")

            for field_name in ("documento_id", "fuente", "nivel"):
                await self._client.create_payload_index(
                    collection_name=collection,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
                logger.debug(f"Payload index creado: {field_name}")
        else:
            logger.info(f"Colección '{collection}' ya existe, reutilizando")

    async def upsert(self, chunks: list[Chunk], embeddings: list[EmbeddingResult]) -> int:
        """Inserta o actualiza chunks con sus embeddings en batches de 50.

        Args:
            chunks: Lista de chunks a indexar.
            embeddings: Lista de embeddings en el mismo orden que chunks.

        Returns:
            Número total de puntos insertados/actualizados.

        Raises:
            ValueError: Si chunks y embeddings tienen longitudes distintas.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks ({len(chunks)}) y embeddings ({len(embeddings)}) "
                "deben tener el mismo tamaño"
            )

        points = [
            PointStruct(
                id=_chunk_id_to_uuid(chunk.chunk_id),
                vector={
                    "dense": emb.dense,
                    "sparse": QdrantSparseVector(
                        indices=emb.sparse_indices,
                        values=emb.sparse_values,
                    ),
                },
                payload=chunk.model_dump(),
            )
            for chunk, emb in zip(chunks, embeddings)
        ]

        total = 0
        for i in range(0, len(points), _UPSERT_BATCH_SIZE):
            batch = points[i : i + _UPSERT_BATCH_SIZE]
            await self._client.upsert(
                collection_name=self._config.collection,
                points=batch,
            )
            total += len(batch)
            logger.debug(
                f"Upserted batch {i // _UPSERT_BATCH_SIZE + 1}: {len(batch)} puntos"
            )

        return total

    async def search_dense(
        self,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[SearchResult]:
        """Búsqueda por similitud semántica (vector denso).

        Args:
            vector: Vector de consulta dense (1024 dims).
            top_k: Número máximo de resultados.
            filters: Filtros opcionales por metadatos (documento_id, fuente, nivel).

        Returns:
            Lista de SearchResult ordenada por score descendente.
        """
        results = await self._client.search(
            collection_name=self._config.collection,
            query_vector=NamedVector(name="dense", vector=vector),
            limit=top_k,
            query_filter=_build_filter(filters),
            with_payload=True,
        )
        return [_point_to_search_result(p, "dense") for p in results]

    async def search_sparse(
        self,
        sparse_vector: SparseVector,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[SearchResult]:
        """Búsqueda por términos exactos (vector sparse / lexical).

        Args:
            sparse_vector: Vector sparse de consulta.
            top_k: Número máximo de resultados.
            filters: Filtros opcionales por metadatos.

        Returns:
            Lista de SearchResult ordenada por score descendente.
        """
        results = await self._client.search(
            collection_name=self._config.collection,
            query_vector=NamedSparseVector(
                name="sparse",
                vector=QdrantSparseVector(
                    indices=sparse_vector.indices,
                    values=sparse_vector.values,
                ),
            ),
            limit=top_k,
            query_filter=_build_filter(filters),
            with_payload=True,
        )
        return [_point_to_search_result(p, "sparse") for p in results]

    async def search_hybrid(
        self,
        dense: list[float],
        sparse: SparseVector,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[SearchResult]:
        """Búsqueda híbrida combinando dense y sparse con RRF nativo de Qdrant.

        Args:
            dense: Vector denso de consulta.
            sparse: Vector sparse de consulta.
            top_k: Número máximo de resultados.
            filters: Filtros opcionales por metadatos.

        Returns:
            Lista de SearchResult ordenada por score RRF descendente.
        """
        filter_obj = _build_filter(filters)
        response = await self._client.query_points(
            collection_name=self._config.collection,
            prefetch=[
                Prefetch(query=dense, using="dense", limit=top_k, filter=filter_obj),
                Prefetch(
                    query=QdrantSparseVector(
                        indices=sparse.indices,
                        values=sparse.values,
                    ),
                    using="sparse",
                    limit=top_k,
                    filter=filter_obj,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )
        return [_point_to_search_result(p, "hybrid") for p in response.points]

    async def get_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        """Recupera chunks por sus IDs para expansión de referencias cruzadas.

        Args:
            chunk_ids: Lista de chunk_ids a recuperar.

        Returns:
            Lista de Chunk encontrados (puede ser menor si alguno no existe).
        """
        if not chunk_ids:
            return []

        uuids = [_chunk_id_to_uuid(cid) for cid in chunk_ids]
        records = await self._client.retrieve(
            collection_name=self._config.collection,
            ids=uuids,
            with_payload=True,
        )
        chunks: list[Chunk] = []
        for record in records:
            if record.payload:
                try:
                    chunks.append(Chunk.model_validate(record.payload))
                except Exception as exc:
                    logger.warning(f"Error al reconstruir Chunk desde payload: {exc}")
        return chunks
