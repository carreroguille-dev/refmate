"""Proveedor de embeddings BGE-m3 local.

Implementa EmbeddingProvider usando FlagEmbedding (BAAI/bge-m3).
Genera vectores dense (1024 dims) y sparse (lexical weights) sin llamadas externas.

Nota: se bypasea el método `encode()` de FlagEmbedding porque tiene un bug de
compatibilidad con transformers>=4.44 (convert_to_numpy acaba en los kwargs del
tokenizer, que ya no acepta kwargs desconocidos). Se usa directamente el tokenizer
y el modelo subyacente, replicando la misma lógica de post-procesado.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import torch
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
        """Carga el modelo BGE-m3 en memoria y precalcula tokens especiales.

        Args:
            config: Configuración del modelo de embeddings (nombre, device, batch_size).
        """
        logger.info(f"Cargando modelo de embeddings '{config.name}' en {config.device}...")
        self._batch_size = config.batch_size
        self._device = config.device
        self._model: BGEM3FlagModel = BGEM3FlagModel(config.name, use_fp16=False, device=config.device)
        self._model.model.eval()

        # Precalcular IDs de tokens especiales que se excluyen del sparse vector
        special_token_names = ["cls_token", "eos_token", "pad_token", "unk_token"]
        self._unused_token_ids: set[int] = set()
        for name in special_token_names:
            if name in self._model.tokenizer.special_tokens_map:
                token_id = self._model.tokenizer.convert_tokens_to_ids(
                    self._model.tokenizer.special_tokens_map[name]
                )
                self._unused_token_ids.add(token_id)

        logger.info("Modelo BGE-m3 cargado correctamente")

    def encode(self, text: str) -> EmbeddingResult:
        """Genera embedding para un único texto.

        Args:
            text: Texto a encodificar.

        Returns:
            EmbeddingResult con vector dense (1024 dims) y sparse (lexical weights).
        """
        return self.encode_batch([text])[0]

    def encode_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Genera embeddings para un lote de textos.

        Procesa en sublotes de config.batch_size para controlar uso de memoria.
        Bypasea FlagEmbedding.encode() por incompatibilidad con transformers>=4.44;
        usa directamente el tokenizer y el modelo subyacente.

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

            inputs = self._model.tokenizer(
                batch,
                truncation=True,
                max_length=512,
                padding=True,
                return_tensors="pt",
            ).to(self._device)

            with torch.no_grad():
                outputs = self._model.model(
                    inputs,
                    return_dense=True,
                    return_sparse=True,
                    return_colbert_vecs=False,
                )

            dense_vecs = outputs["dense_vecs"].cpu().numpy()
            sparse_vecs = outputs["sparse_vecs"].squeeze(-1).cpu().numpy()
            input_ids = inputs["input_ids"].cpu().numpy().tolist()

            for j in range(len(batch)):
                results.append(
                    self._build_result(dense_vecs[j], sparse_vecs[j], input_ids[j])
                )

        return results

    def _build_result(
        self,
        dense: Any,
        token_weights: Any,
        input_ids: list[int],
    ) -> EmbeddingResult:
        """Construye un EmbeddingResult a partir de los outputs crudos del modelo.

        Args:
            dense: Vector denso numpy (1024 dims).
            token_weights: Pesos sparse por token (numpy array).
            input_ids: IDs de tokens del input (para filtrar especiales).

        Returns:
            EmbeddingResult con dense list[float] y sparse (indices + values).
        """
        # Replicar _process_token_weights de FlagEmbedding:
        # construir dict {token_id_str: max_weight} excluyendo tokens especiales
        lexical: dict[str, float] = defaultdict(float)
        for w, idx in zip(token_weights, input_ids):
            if idx not in self._unused_token_ids and w > 0:
                key = str(idx)
                if float(w) > lexical[key]:
                    lexical[key] = float(w)

        return EmbeddingResult(
            dense=dense.tolist(),
            sparse_indices=[int(k) for k in lexical.keys()],
            sparse_values=list(lexical.values()),
        )
