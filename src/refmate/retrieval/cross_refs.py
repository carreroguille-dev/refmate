"""Expansor de referencias cruzadas entre chunks.

Responsabilidad única: dado un conjunto de chunk_ids, devolver chunk_ids
adicionales relacionados siguiendo el grafo de referencias cruzadas generado
en la fase de chunking.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from loguru import logger


class CrossRefExpander:
    """Expande chunk_ids siguiendo el grafo bidireccional de referencias cruzadas.

    El grafo se carga una sola vez en el constructor desde el fichero JSON
    generado por el chunker. La expansión es síncrona (sin I/O).

    El grafo tiene edges de la forma ``{"from": chunk_id_A, "to": chunk_id_B}``.
    La adyacencia se construye bidireccional: A→B implica también B→A.
    """

    def __init__(self, graph_path: Path, max_expansion: int) -> None:
        """Carga el grafo de referencias y construye el índice de adyacencia.

        Args:
            graph_path: Ruta al fichero cross_references_graph.json.
            max_expansion: Número máximo de chunk_ids adicionales a devolver
                           en cada llamada a expand().

        Raises:
            FileNotFoundError: Si graph_path no existe.
            ValueError: Si el fichero no contiene el campo 'edges'.
        """
        if not graph_path.exists():
            raise FileNotFoundError(f"Grafo de referencias no encontrado: {graph_path}")

        with open(graph_path, encoding="utf-8") as f:
            data = json.load(f)

        if "edges" not in data:
            raise ValueError(f"El fichero {graph_path} no contiene el campo 'edges'")

        self._max_expansion = max_expansion
        self._adjacency: dict[str, list[str]] = defaultdict(list)

        for edge in data["edges"]:
            src = edge.get("from", "")
            dst = edge.get("to", "")
            if src and dst:
                # Bidireccional: A referencia B y B puede ser encontrado desde A
                if dst not in self._adjacency[src]:
                    self._adjacency[src].append(dst)
                if src not in self._adjacency[dst]:
                    self._adjacency[dst].append(src)

        total_nodes = len(self._adjacency)
        total_edges = sum(len(v) for v in self._adjacency.values()) // 2
        logger.info(
            f"CrossRefExpander cargado: {total_nodes} nodos, "
            f"~{total_edges} edges, max_expansion={max_expansion}"
        )

    def expand(self, chunk_ids: list[str]) -> list[str]:
        """Devuelve chunk_ids adicionales relacionados con los dados.

        Busca todos los vecinos directos de cada chunk_id en el grafo y
        devuelve los que no estaban ya en la lista original, hasta
        ``max_expansion`` resultados.

        Args:
            chunk_ids: Lista de chunk_ids de partida (ya recuperados).

        Returns:
            Lista de chunk_ids adicionales relacionados (sin duplicados
            y sin incluir los chunk_ids originales). Puede estar vacía.
        """
        if not chunk_ids:
            return []

        seen = set(chunk_ids)
        related: list[str] = []

        for cid in chunk_ids:
            for neighbor in self._adjacency.get(cid, []):
                if neighbor not in seen and len(related) < self._max_expansion:
                    related.append(neighbor)
                    seen.add(neighbor)

        if related:
            logger.debug(
                f"CrossRef expand: {len(chunk_ids)} chunks → {len(related)} relacionados"
            )
        return related
