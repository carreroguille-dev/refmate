"""Indexer: orquesta embedding + indexación de chunks en Qdrant.

Responsabilidad única: leer los chunks de data/chunks/{doc_id}_chunks.json,
generar embeddings con EmbeddingProvider y almacenarlos en VectorStore.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from refmate.config import RefMateConfig, get_project_root
from refmate.core.models import Chunk
from refmate.core.protocols import EmbeddingProvider, VectorStore


class Indexer:
    """Orquestador de embedding + indexación de chunks en Qdrant.

    Recibe las dependencias inyectadas como protocolos. No instancia
    implementaciones concretas — eso ocurre en el composition root (pipeline.py).
    """

    def __init__(
        self,
        embedder: EmbeddingProvider,
        store: VectorStore,
        config: RefMateConfig,
    ) -> None:
        """Inicializa el Indexer con las dependencias inyectadas.

        Args:
            embedder: Proveedor de embeddings (implementa EmbeddingProvider).
            store: Vector store donde se indexarán los chunks (implementa VectorStore).
            config: Configuración del sistema.
        """
        self._embedder = embedder
        self._store = store
        self._config = config
        self._root: Path = get_project_root()

    async def index_document(self, doc_id: str) -> int:
        """Lee, embebe e indexa todos los chunks de un documento.

        Args:
            doc_id: ID del documento (ej: 'reglas-de-juego').

        Returns:
            Número de chunks indexados.

        Raises:
            FileNotFoundError: Si no existe el fichero de chunks del documento.
        """
        chunks_path = self._root / "data" / "chunks" / f"{doc_id}_chunks.json"
        if not chunks_path.exists():
            raise FileNotFoundError(
                f"No se encontró el fichero de chunks: {chunks_path}. "
                "Ejecuta el Chunker (Fase 5) antes de indexar."
            )

        logger.info(f"[{doc_id}] Cargando chunks desde {chunks_path}...")
        raw_chunks: list[dict[str, Any]] = json.loads(chunks_path.read_text(encoding="utf-8"))
        chunks = [Chunk.model_validate(c) for c in raw_chunks]
        logger.info(f"[{doc_id}] {len(chunks)} chunks cargados")

        t0 = time.monotonic()
        texts = [c.texto_con_contexto for c in chunks]
        logger.info(f"[{doc_id}] Generando embeddings para {len(texts)} textos...")
        embeddings = self._embedder.encode_batch(texts)
        embed_secs = time.monotonic() - t0
        logger.info(f"[{doc_id}] Embeddings generados en {embed_secs:.1f}s")

        t1 = time.monotonic()
        total = await self._store.upsert(chunks, embeddings)
        upsert_secs = time.monotonic() - t1
        logger.info(f"[{doc_id}] {total} chunks indexados en Qdrant en {upsert_secs:.1f}s")

        return total

    async def index_all(self) -> dict[str, int]:
        """Indexa todos los documentos definidos en la configuración.

        La colección debe crearse/recrearse antes de llamar a este método
        (responsabilidad del composition root, que conoce la implementación concreta).

        Returns:
            Dict {doc_id: n_chunks_indexados} para cada documento.
        """
        logger.info("Iniciando indexación de todos los documentos...")
        stats: dict[str, int] = {}
        t0 = time.monotonic()

        for doc_id in self._config.documents:
            try:
                n = await self.index_document(doc_id)
                stats[doc_id] = n
            except FileNotFoundError as exc:
                logger.warning(str(exc))
                stats[doc_id] = 0

        total_secs = time.monotonic() - t0
        total_chunks = sum(stats.values())
        logger.info(
            f"Indexación completada: {total_chunks} chunks en {total_secs:.1f}s — "
            + ", ".join(f"{k}: {v}" for k, v in stats.items())
        )
        return stats


async def run_indexer(config: RefMateConfig, recreate: bool = True) -> None:
    """Punto de entrada para la fase de indexación desde pipeline.py.

    Actúa como composition root para la ingesta: instancia las implementaciones
    concretas, prepara la colección y delega en Indexer.

    Args:
        config: Configuración del sistema ya cargada.
        recreate: Si True, elimina y recrea la colección antes de indexar.
                  Pasar False para indexación incremental (ej: --from indexer sin --only).
    """
    from refmate.infrastructure.embeddings.bge_m3 import BGEM3EmbeddingProvider
    from refmate.infrastructure.vectorstore.qdrant import QdrantVectorStore

    embedder = BGEM3EmbeddingProvider(config.models.embeddings)
    store = QdrantVectorStore(config.qdrant)

    logger.info(f"Preparando colección Qdrant (recreate={recreate})...")
    await store.ensure_collection(recreate=recreate)

    indexer = Indexer(embedder=embedder, store=store, config=config)
    await indexer.index_all()
