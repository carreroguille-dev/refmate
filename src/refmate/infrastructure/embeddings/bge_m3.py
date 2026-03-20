"""Proveedor de embeddings BGE-m3 local.

Implementa EmbeddingProvider usando FlagEmbedding (BAAI/bge-m3).
Genera vectores dense (1024 dims) y sparse (lexical weights) sin llamadas externas.
"""

from __future__ import annotations

from typing import Any

from FlagEmbedding import BGEM3FlagModel  # type: ignore[import-untyped]
from loguru import logger

from refmate.config import EmbeddingsConfig
from refmate.core.models import EmbeddingResult


class BGEM3EmbeddingProvider:
    """Proveedor de embeddings BGE-m3 usando FlagEmbedding (CPU).

    Genera vectores dense (1024 dims) y sparse (lexical weights) a partir de texto.
    El modelo se carga una sola vez en el constructor.
    """

    def __init__(self, config: EmbeddingsConfig) -> None:
        """Carga el modelo BGE-m3 en memoria.

        Args:
            config: Configuración del modelo de embeddings (nombre, device, batch_size).
        """
        logger.info(f"Cargando modelo de embeddings '{config.name}' en {config.device}...")
        self._batch_size = config.batch_size
        self._model: BGEM3FlagModel = BGEM3FlagModel(config.name, use_fp16=False, device=config.device)
        logger.info("Modelo BGE-m3 cargado correctamente")

    def encode(self, text: str) -> EmbeddingResult:
        """Genera embedding para un único texto.

        Args:
            text: Texto a encodificar.

        Returns:
            EmbeddingResult con vector dense (1024 dims) y sparse (lexical weights).
        """
        raw = self._model.encode(
            [text],
            batch_size=1,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        return self._to_embedding_result(raw, 0)

    def encode_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Genera embeddings para un lote de textos.

        Procesa en sublotes de config.batch_size para controlar uso de memoria.

        Args:
            texts: Lista de textos a encodificar.

        Returns:
            Lista de EmbeddingResult en el mismo orden que la entrada.
        """
        if not texts:
            return []

        results: list[EmbeddingResult] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            batch_num = i // self._batch_size + 1
            logger.debug(f"Encodificando batch {batch_num}: {len(batch)} textos")
            raw = self._model.encode(
                batch,
                batch_size=self._batch_size,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
            )
            for j in range(len(batch)):
                results.append(self._to_embedding_result(raw, j))

        return results

    def _to_embedding_result(self, raw: dict[str, Any], idx: int) -> EmbeddingResult:
        """Convierte la salida raw de FlagEmbedding a EmbeddingResult.

        Args:
            raw: Diccionario con 'dense_vecs' y 'lexical_weights'.
            idx: Índice del elemento en el batch actual.

        Returns:
            EmbeddingResult con dense list[float] y sparse (indices + values).
        """
        dense: list[float] = raw["dense_vecs"][idx].tolist()
        lexical: dict[str, float] = raw["lexical_weights"][idx]
        indices = [int(k) for k in lexical.keys()]
        values = [float(v) for v in lexical.values()]
        return EmbeddingResult(dense=dense, sparse_indices=indices, sparse_values=values)
