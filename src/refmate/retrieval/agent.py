"""Agente RAG con tool calling para consultas de normativa de balonmano.

Responsabilidad única: decidir la estrategia de búsqueda mediante tool calling
nativo (Qwen3-235B vía OpenRouter), ejecutar las herramientas de búsqueda
sobre Qdrant y generar una respuesta final citando las fuentes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from refmate.config import RetrievalConfig
from refmate.core.models import AgentResult, SparseVector
from refmate.core.protocols import EmbeddingProvider, ToolCallingLLM, VectorStore
from refmate.retrieval.cross_refs import CrossRefExpander

# ---------------------------------------------------------------------------
# Esquemas de tools en formato OpenAI function calling
# ---------------------------------------------------------------------------

_DOC_FILTER_ENUM = ["reglas-de-juego", "rgc-fabm", "add-fabm"]

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_dense",
            "description": (
                "Búsqueda semántica por similitud de significado (vector denso). "
                "Usar para preguntas conceptuales o con sinónimos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Texto de búsqueda semántica.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Número de resultados (por defecto 5).",
                        "default": 5,
                    },
                    "doc_filter": {
                        "type": "string",
                        "enum": _DOC_FILTER_ENUM,
                        "description": "Filtrar por documento específico (opcional).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_sparse",
            "description": (
                "Búsqueda léxica por términos exactos (vector sparse). "
                "Usar cuando la pregunta menciona números de regla o artículos concretos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Texto de búsqueda léxica.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Número de resultados (por defecto 5).",
                        "default": 5,
                    },
                    "doc_filter": {
                        "type": "string",
                        "enum": _DOC_FILTER_ENUM,
                        "description": "Filtrar por documento específico (opcional).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hybrid",
            "description": (
                "Búsqueda híbrida combinando semántica y léxica con RRF. "
                "Opción más robusta cuando no estés seguro del tipo de búsqueda."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Texto de búsqueda híbrida.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Número de resultados (por defecto 5).",
                        "default": 5,
                    },
                    "doc_filter": {
                        "type": "string",
                        "enum": _DOC_FILTER_ENUM,
                        "description": "Filtrar por documento específico (opcional).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chunk_by_id",
            "description": (
                "Acceso directo a un chunk por su ID exacto. "
                "Usar cuando el índice jerárquico indica el chunk concreto necesario."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chunk_id": {
                        "type": "string",
                        "description": "ID del chunk (ej: 'reglas-de-juego:regla-8:8-5').",
                    },
                },
                "required": ["chunk_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_related_chunks",
            "description": (
                "Devuelve chunks relacionados mediante referencias cruzadas. "
                "Usar para expandir el contexto cuando un artículo referencia otros."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chunk_id": {
                        "type": "string",
                        "description": "ID del chunk del que expandir referencias.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Número máximo de chunks relacionados (por defecto 3).",
                        "default": 3,
                    },
                },
                "required": ["chunk_id"],
            },
        },
    },
]

# Máximo de iteraciones del loop de tool calling para evitar bucles infinitos
_MAX_TOOL_ITERATIONS = 10


def _format_index(index_data: dict[str, Any]) -> str:
    """Convierte el índice jerárquico JSON a texto compacto para el system prompt.

    Agrupa los chunks bajo su sección principal (primer elemento de jerarquía)
    para que el LLM pueda navegar el índice sin consumir demasiados tokens.

    Args:
        index_data: Contenido del hierarchical_index.json.

    Returns:
        Texto formateado con la estructura jerárquica de los tres documentos.
    """
    lines: list[str] = []
    for doc_id, doc_info in index_data.items():
        nombre = doc_info.get("nombre", doc_id)
        lines.append(f"\n### {nombre} (id: `{doc_id}`)")

        # Agrupar chunks por sección principal (jerarquia[0])
        sections: dict[str, list[str]] = {}
        for chunk in doc_info.get("chunks", []):
            jerarquia = chunk.get("jerarquia", [])
            titulo = chunk.get("titulo_seccion", chunk.get("chunk_id", ""))
            section_key = jerarquia[0] if jerarquia else "General"
            sections.setdefault(section_key, []).append(titulo)

        for section, titulos in sections.items():
            # Listar hasta 15 sub-títulos en línea para mantener compacidad
            sample = ", ".join(titulos[:15])
            suffix = f" … (+{len(titulos) - 15} más)" if len(titulos) > 15 else ""
            lines.append(f"- **{section}**: {sample}{suffix}")

    return "\n".join(lines)


def _format_search_results(results: list[Any]) -> str:
    """Formatea SearchResults para que el LLM pueda leerlos y citarlos.

    Args:
        results: Lista de SearchResult.

    Returns:
        Texto con los chunks formateados, separados por líneas.
    """
    if not results:
        return "No se encontraron resultados."

    parts: list[str] = []
    for i, r in enumerate(results, 1):
        chunk = r.chunk
        breadcrumb = " > ".join(chunk.jerarquia)
        parts.append(
            f"[Resultado {i}]\n"
            f"ID: {chunk.chunk_id}\n"
            f"Fuente: {chunk.documento_nombre} ({chunk.fuente})\n"
            f"Sección: {breadcrumb}\n"
            f"Texto:\n{chunk.texto}\n"
        )
    return "\n---\n".join(parts)


def _format_chunks(chunks: list[Any]) -> str:
    """Formatea una lista de Chunk para que el LLM pueda leerlos.

    Args:
        chunks: Lista de Chunk.

    Returns:
        Texto con los chunks formateados, separados por líneas.
    """
    if not chunks:
        return "No se encontraron chunks."

    parts: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        breadcrumb = " > ".join(chunk.jerarquia)
        parts.append(
            f"[Chunk {i}]\n"
            f"ID: {chunk.chunk_id}\n"
            f"Fuente: {chunk.documento_nombre} ({chunk.fuente})\n"
            f"Sección: {breadcrumb}\n"
            f"Texto:\n{chunk.texto}\n"
        )
    return "\n---\n".join(parts)


def _determine_strategy(tools_called: list[str], chunks_used: list[str]) -> str:
    """Determina la estrategia de búsqueda usada basándose en las tools invocadas.

    Args:
        tools_called: Nombres de las tools invocadas durante la sesión.
        chunks_used: chunk_ids recopilados de los resultados.

    Returns:
        Estrategia: 'dense', 'sparse', 'hybrid' o 'multi_doc'.
    """
    # Detectar si se usaron múltiples documentos
    if chunks_used:
        docs = {cid.split(":")[0] for cid in chunks_used}
        if len(docs) > 1:
            return "multi_doc"

    search_tools = [t for t in tools_called if t.startswith("search_")]
    if not search_tools:
        return "dense"  # fallback

    if all(t == "search_sparse" for t in search_tools):
        return "sparse"
    if all(t == "search_dense" for t in search_tools):
        return "dense"
    return "hybrid"


class RAGAgent:
    """Agente RAG con tool calling nativo sobre Qdrant.

    Implementa el protocolo Agent. Recibe una consulta validada por el guard,
    decide la estrategia de búsqueda mediante tool calling (Qwen3-235B),
    ejecuta las herramientas sobre el vector store y genera una respuesta
    final citando los artículos consultados.
    """

    def __init__(
        self,
        llm: ToolCallingLLM,
        vector_store: VectorStore,
        embedder: EmbeddingProvider,
        cross_ref_expander: CrossRefExpander,
        retrieval_config: RetrievalConfig,
        system_prompt_path: Path,
        index_path: Path,
    ) -> None:
        """Inicializa el agente cargando el system prompt y el índice jerárquico.

        Args:
            llm: Implementación de ToolCallingLLM inyectada (protocolo).
            vector_store: Implementación de VectorStore inyectada (protocolo).
            embedder: Implementación de EmbeddingProvider inyectada (protocolo).
            cross_ref_expander: Expansor de referencias cruzadas.
            retrieval_config: Parámetros de recuperación (top_k, etc.).
            system_prompt_path: Ruta al fichero system_prompt.md (template).
            index_path: Ruta al fichero hierarchical_index.json.

        Raises:
            FileNotFoundError: Si algún fichero de prompt o índice no existe.
        """
        self._llm = llm
        self._vector_store = vector_store
        self._embedder = embedder
        self._cross_ref_expander = cross_ref_expander
        self._retrieval_config = retrieval_config

        # Cargar y preparar el template del system prompt
        if not system_prompt_path.exists():
            raise FileNotFoundError(f"System prompt no encontrado: {system_prompt_path}")
        self._prompt_template = system_prompt_path.read_text(encoding="utf-8")

        # Cargar y formatear el índice jerárquico (se inyecta en el prompt)
        if not index_path.exists():
            raise FileNotFoundError(f"Índice jerárquico no encontrado: {index_path}")
        with open(index_path, encoding="utf-8") as f:
            index_data = json.load(f)
        self._formatted_index = _format_index(index_data)

        logger.info("RAGAgent inicializado con índice jerárquico cargado")

    async def run(self, query: str, context: str | None) -> AgentResult:
        """Procesa una consulta con tool calling y genera una respuesta final.

        Construye el system prompt, lanza el loop de tool calling (hasta
        _MAX_TOOL_ITERATIONS iteraciones) y devuelve la respuesta con metadata.

        Args:
            query: Pregunta del usuario, ya validada por el guard.
            context: Respuesta cacheada adicional (context hit, similitud 0.85-0.95).
                     Se incluye en el system prompt como contexto previo.

        Returns:
            AgentResult con la respuesta, chunks_used, search_strategy y tools_called.
        """
        system_prompt = self._build_system_prompt(context)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]

        tools_called: list[str] = []
        chunks_used: list[str] = []
        final_response = ""

        for iteration in range(_MAX_TOOL_ITERATIONS):
            content, tool_calls = await self._llm.chat(messages, _TOOLS)

            if tool_calls:
                # El modelo quiere invocar herramientas: ejecutarlas y continuar
                messages.append(
                    {"role": "assistant", "content": None, "tool_calls": tool_calls}
                )

                for tc in tool_calls:
                    call_id: str = tc["id"]
                    fn = tc["function"]
                    tool_name: str = fn["name"]
                    try:
                        arguments: dict[str, Any] = json.loads(fn["arguments"])
                    except json.JSONDecodeError:
                        arguments = {}

                    tools_called.append(tool_name)
                    logger.debug(
                        f"RAGAgent iter={iteration + 1}: ejecutando tool '{tool_name}' "
                        f"args={arguments}"
                    )

                    tool_result, new_chunk_ids = await self._execute_tool(
                        tool_name, arguments
                    )
                    chunks_used.extend(new_chunk_ids)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": tool_result,
                        }
                    )

            elif content:
                # El modelo devuelve la respuesta final
                final_response = content
                logger.debug(
                    f"RAGAgent: respuesta final obtenida en iteración {iteration + 1}, "
                    f"{len(final_response)} chars"
                )
                break

            else:
                logger.warning(
                    f"RAGAgent iter={iteration + 1}: respuesta vacía del LLM, abortando"
                )
                break

        else:
            logger.warning(
                f"RAGAgent: límite de {_MAX_TOOL_ITERATIONS} iteraciones alcanzado"
            )
            # Si el loop se agota sin respuesta, usar el último contenido disponible
            if not final_response:
                final_response = (
                    "No he podido generar una respuesta completa. "
                    "Por favor, reformula tu pregunta."
                )

        # Deduplicar chunks_used manteniendo orden
        seen: set[str] = set()
        unique_chunks: list[str] = []
        for cid in chunks_used:
            if cid not in seen:
                unique_chunks.append(cid)
                seen.add(cid)

        strategy = _determine_strategy(tools_called, unique_chunks)

        logger.info(
            f"RAGAgent completado: strategy={strategy}, "
            f"tools={tools_called}, chunks={len(unique_chunks)}"
        )

        return AgentResult(
            response=final_response,
            chunks_used=unique_chunks,
            search_strategy=strategy,
            tools_called=tools_called,
        )

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _build_system_prompt(self, context: str | None) -> str:
        """Construye el system prompt rellenando los placeholders del template.

        Args:
            context: Respuesta cacheada adicional (None si no hay context hit).

        Returns:
            System prompt completo listo para enviarse al LLM.
        """
        context_section = (
            f"**Respuesta previa relevante (similitud alta):** {context}"
            if context
            else "*(Sin contexto adicional de caché)*"
        )
        return self._prompt_template.format(
            hierarchical_index=self._formatted_index,
            context=context_section,
        )

    async def _execute_tool(
        self, tool_name: str, args: dict[str, Any]
    ) -> tuple[str, list[str]]:
        """Ejecuta una herramienta del agente y devuelve el resultado formateado.

        Args:
            tool_name: Nombre de la herramienta a ejecutar.
            args: Argumentos de la herramienta (ya deserializados).

        Returns:
            Tuple ``(resultado_texto, chunk_ids_usados)``.
        """
        top_k: int = int(args.get("top_k", self._retrieval_config.top_k))
        doc_filter_val: str | None = args.get("doc_filter")
        filters = {"documento_id": doc_filter_val} if doc_filter_val else None

        if tool_name == "search_dense":
            query: str = args["query"]
            emb = self._embedder.encode(query)
            results = await self._vector_store.search_dense(emb.dense, top_k, filters)
            chunk_ids = [r.chunk.chunk_id for r in results]
            return _format_search_results(results), chunk_ids

        if tool_name == "search_sparse":
            query = args["query"]
            emb = self._embedder.encode(query)
            sparse = SparseVector(
                indices=emb.sparse_indices, values=emb.sparse_values
            )
            results = await self._vector_store.search_sparse(sparse, top_k, filters)
            chunk_ids = [r.chunk.chunk_id for r in results]
            return _format_search_results(results), chunk_ids

        if tool_name == "search_hybrid":
            query = args["query"]
            emb = self._embedder.encode(query)
            sparse = SparseVector(
                indices=emb.sparse_indices, values=emb.sparse_values
            )
            results = await self._vector_store.search_hybrid(
                emb.dense, sparse, top_k, filters
            )
            chunk_ids = [r.chunk.chunk_id for r in results]
            return _format_search_results(results), chunk_ids

        if tool_name == "get_chunk_by_id":
            chunk_id: str = args["chunk_id"]
            chunks = await self._vector_store.get_by_ids([chunk_id])
            return _format_chunks(chunks), [c.chunk_id for c in chunks]

        if tool_name == "get_related_chunks":
            chunk_id = args["chunk_id"]
            max_results: int = int(
                args.get("max_results", self._retrieval_config.max_cross_ref_expansion)
            )
            related_ids = self._cross_ref_expander.expand([chunk_id])
            related_ids = related_ids[:max_results]
            chunks = await self._vector_store.get_by_ids(related_ids)
            return _format_chunks(chunks), [c.chunk_id for c in chunks]

        logger.warning(f"RAGAgent: tool desconocida '{tool_name}'")
        return f"Herramienta '{tool_name}' no reconocida.", []
