"""Bot Telegram — Composition root.

Responsabilidad única: instanciar todas las implementaciones concretas,
inyectarlas en QueryEngine, configurar el bot de Telegram y lanzar el polling.

Este módulo es el único lugar del sistema donde se instancian clases concretas
de infrastructure/. El resto del sistema trabaja con protocolos.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from refmate.bot.handlers import (
    _short_doc_name,
    help_handler,
    message_handler,
    start_handler,
)
from refmate.config import RefMateConfig, get_config, get_project_root
from refmate.infrastructure.cache.redis_cache import RedisSemanticCache
from refmate.infrastructure.embeddings.bge_m3 import BGEM3EmbeddingProvider
from refmate.infrastructure.llm.openrouter import (
    OpenRouterTextGenerator,
    OpenRouterToolCallingLLM,
)
from refmate.infrastructure.vectorstore.qdrant import QdrantVectorStore
from refmate.retrieval.agent import RAGAgent
from refmate.retrieval.cross_refs import CrossRefExpander
from refmate.retrieval.guard import OpenRouterGuardModel
from refmate.retrieval.query_engine import QueryEngine

# Directorio de prompts relativo al paquete (no depende del cwd)
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _configure_logging(log_dir: Path) -> None:
    """Añade un sink de loguru al fichero de log del bot.

    Escribe en ``{log_dir}/bot.log`` con rotación a 10 MB y retención de 30 días.
    El sink de stderr ya está activo por defecto en loguru.

    Args:
        log_dir: Directorio donde se creará el fichero bot.log.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "bot.log",
        rotation="10 MB",
        retention="30 days",
        level="INFO",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{line} — {message}",
    )
    logger.info(f"Logging configurado → {log_dir / 'bot.log'}")


def _build_query_engine(config: RefMateConfig, root: Path) -> QueryEngine:
    """Instancia todas las dependencias y construye el QueryEngine.

    Orden de construcción:
    1. EmbeddingProvider (carga modelo BGE-m3, operación pesada)
    2. SemanticCache (conexión Redis)
    3. VectorStore (conexión Qdrant)
    4. CrossRefExpander (carga grafo desde disco)
    5. GuardModel (TextGenerator + prompt)
    6. RAGAgent (ToolCallingLLM + VectorStore + Embedder + CrossRefs)
    7. QueryEngine (orquesta todo lo anterior)

    Args:
        config: Configuración validada del sistema.
        root: Directorio raíz del proyecto (contiene config.yaml).

    Returns:
        QueryEngine listo para recibir consultas.
    """
    api_key = config.api_keys.openrouter

    # 1. Embeddings (carga el modelo; puede tardar varios segundos)
    logger.info("Cargando modelo de embeddings BGE-m3...")
    embedder = BGEM3EmbeddingProvider(config.models.embeddings)

    # 2. Caché semántica Redis
    cache = RedisSemanticCache(config.cache)

    # 3. Vector store Qdrant
    vector_store = QdrantVectorStore(config.qdrant)

    # 4. Expansor de referencias cruzadas
    graph_path = root / "data" / "chunks" / "cross_references_graph.json"
    cross_ref_expander = CrossRefExpander(
        graph_path=graph_path,
        max_expansion=config.retrieval.max_cross_ref_expansion,
    )

    # 5. Guard model (Qwen3-4B vía OpenRouter)
    guard_generator = OpenRouterTextGenerator(config.models.guard, api_key)
    guard = OpenRouterGuardModel(
        generator=guard_generator,
        prompt_path=_PROMPTS_DIR / "guard_prompt.md",
    )

    # 6. Agente RAG (Qwen3-235B con tool calling)
    agent_llm = OpenRouterToolCallingLLM(config.models.agent, api_key)
    index_path = root / "data" / "index" / "hierarchical_index.json"
    agent = RAGAgent(
        llm=agent_llm,
        vector_store=vector_store,
        embedder=embedder,
        cross_ref_expander=cross_ref_expander,
        retrieval_config=config.retrieval,
        system_prompt_path=_PROMPTS_DIR / "system_prompt.md",
        index_path=index_path,
        doc_ids=list(config.documents.keys()),
    )

    # 7. Query engine (orquestador online)
    return QueryEngine(
        guard=guard,
        cache=cache,
        agent=agent,
        embedder=embedder,
        config=config,
    )


def main() -> None:
    """Punto de entrada principal del bot.

    Carga la configuración, instancia todas las dependencias, registra los
    handlers de Telegram y lanza el polling.
    """
    config = get_config()
    root = get_project_root()

    _configure_logging(root / "logs")
    logger.info("Iniciando RefMate Bot...")

    # Construir QueryEngine con todas sus dependencias
    query_engine = _build_query_engine(config, root)

    # Mapa doc_id → nombre corto para formatear las fuentes en las respuestas
    doc_names: dict[str, str] = {
        doc_id: _short_doc_name(doc_cfg.nombre)
        for doc_id, doc_cfg in config.documents.items()
    }

    # Construir aplicación Telegram
    application = ApplicationBuilder().token(config.telegram.token).build()

    # Inyectar dependencias compartidas en bot_data (accesibles desde handlers)
    application.bot_data["query_engine"] = query_engine
    application.bot_data["doc_names"] = doc_names
    application.bot_data["rate_limit"] = config.telegram.rate_limit_per_user

    # Registrar handlers
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
    )

    logger.info(
        f"Bot configurado con {len(config.documents)} documentos. "
        "Iniciando polling..."
    )
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
