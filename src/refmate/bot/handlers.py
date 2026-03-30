"""Bot Telegram — Manejadores de mensajes y comandos.

Responsabilidad única: manejar los eventos de Telegram (comandos /start,
/help y mensajes de texto) y formatear las respuestas al usuario.
"""

from __future__ import annotations

import collections
import time

from loguru import logger
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from refmate.core.models import QueryResult

# ---------------------------------------------------------------------------
# Constantes de mensajes
# ---------------------------------------------------------------------------

MSG_WELCOME = (
    "👋 ¡Hola! Soy *RefMate*, tu asistente de normativa de balonmano pista.\n\n"
    "Puedo responder preguntas sobre:\n"
    "• 📖 *Reglas de Juego* (RFEBM/IHF, Julio 2025)\n"
    "• 📋 *Reglamento General de Competiciones* (FABM, 2025)\n"
    "• ⚖️ *Acuerdo Disciplinario Deportivo* (FABM, 2024)\n\n"
    "Escríbeme tu pregunta directamente o usa /help para ver ejemplos."
)

MSG_HELP = (
    "ℹ️ *Cómo usar RefMate*\n\n"
    "Escríbeme cualquier pregunta sobre normativa de balonmano. Ejemplos:\n\n"
    "• _¿Qué dice la regla 8:5?_\n"
    "• _¿Cuándo se puede descalificar a un jugador?_\n"
    "• _¿Cuál es el tiempo de juego reglamentario?_\n"
    "• _¿Qué sanciones contempla el ADD por agresión?_\n"
    "• _¿Qué es una exclusión temporal?_\n\n"
    "Las respuestas incluyen referencias a los artículos y reglas concretas."
)

MSG_ERROR = (
    "⚠️ Ha ocurrido un error al procesar tu consulta. Por favor, inténtalo de nuevo."
)

MSG_RATE_LIMIT = (
    "⏳ Has alcanzado el límite de consultas por hoy. Inténtalo mañana."
)

# ---------------------------------------------------------------------------
# Rate limiter en memoria (ventana deslizante de 24h)
# ---------------------------------------------------------------------------

_RATE_WINDOW_SECONDS = 86400  # 24 horas

_user_requests: dict[int, collections.deque[float]] = collections.defaultdict(
    collections.deque
)


def _is_rate_limited(user_id: int, limit: int) -> bool:
    """Comprueba si el usuario ha superado el límite de peticiones en 24h.

    Usa una ventana deslizante: descarta timestamps anteriores a 24h y
    comprueba si se supera el límite. Si no está limitado, registra la petición.

    Args:
        user_id: ID del usuario de Telegram.
        limit: Número máximo de peticiones permitidas en la ventana.

    Returns:
        True si el usuario ha superado el límite; False si puede continuar.
    """
    now = time.time()
    dq = _user_requests[user_id]
    cutoff = now - _RATE_WINDOW_SECONDS

    # Eliminar timestamps fuera de la ventana
    while dq and dq[0] < cutoff:
        dq.popleft()

    if len(dq) >= limit:
        return True

    dq.append(now)
    return False


# ---------------------------------------------------------------------------
# Helpers de formato
# ---------------------------------------------------------------------------


def _short_doc_name(nombre: str) -> str:
    """Extrae el nombre corto del documento (antes del primer paréntesis).

    Args:
        nombre: Nombre completo del documento. Ej: 'Reglas de Juego (Julio 2025)'.

    Returns:
        Nombre corto. Ej: 'Reglas de Juego'.
    """
    idx = nombre.find("(")
    if idx > 0:
        return nombre[:idx].strip()
    return nombre.strip()


def _format_sources(chunks_used: list[str], doc_names: dict[str, str]) -> str:
    """Formatea los chunk_ids en una línea de fuentes legible.

    Extrae el doc_id del chunk_id, lo mapea al nombre corto del documento
    y añade la sección (última parte del chunk_id) como referencia.
    Limita a 5 fuentes distintas para no sobrecargar el pie.

    Args:
        chunks_used: Lista de chunk_ids usados por el agente.
        doc_names: Mapa doc_id → nombre corto del documento.

    Returns:
        Cadena con las fuentes formateadas, o cadena vacía si no hay fuentes.
    """
    if not chunks_used:
        return ""

    seen: set[str] = set()
    sources: list[str] = []

    for chunk_id in chunks_used:
        parts = chunk_id.split(":")
        if len(parts) >= 2:
            doc_id = parts[0]
            # La última parte del chunk_id identifica la sección concreta
            section = parts[-1].replace("-", ":")
            doc_short = doc_names.get(doc_id, doc_id)
            label = f"{section} ({doc_short})"
        else:
            label = doc_names.get(chunk_id, chunk_id)

        if label not in seen:
            seen.add(label)
            sources.append(label)

        if len(sources) >= 5:
            break

    return ", ".join(sources)


def _format_response(result: QueryResult, doc_names: dict[str, str]) -> str:
    """Ensambla el mensaje Telegram a partir de un QueryResult.

    Formato:
        📖 [respuesta con citaciones]

        📎 Fuentes: sección (Documento), …
        ⚡ 1.2s | 🔍 hybrid | 💾 miss

    Las líneas de metadatos se omiten cuando no aportan información
    (ej: sin fuentes en cache hit directo o rechazo de guard).

    Args:
        result: QueryResult devuelto por QueryEngine.query().
        doc_names: Mapa doc_id → nombre corto del documento.

    Returns:
        Texto Markdown listo para enviarse por Telegram.
    """
    lines: list[str] = [f"📖 {result.response}"]

    sources = _format_sources(result.chunks_used, doc_names)
    if sources:
        lines.append(f"\n📎 *Fuentes:* {sources}")

    latency_s = result.latency_ms / 1000
    meta = f"⚡ {latency_s:.1f}s | 🔍 {result.search_strategy} | 💾 {result.cache_hit}"
    lines.append(meta)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde al comando /start con el mensaje de bienvenida.

    Args:
        update: Objeto Update de Telegram.
        context: Contexto del handler.
    """
    user = update.effective_user
    logger.info(f"Bot: /start de user_id={user.id if user else 'desconocido'}")
    await update.message.reply_text(MSG_WELCOME, parse_mode="Markdown")


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde al comando /help con ejemplos de uso.

    Args:
        update: Objeto Update de Telegram.
        context: Contexto del handler.
    """
    user = update.effective_user
    logger.info(f"Bot: /help de user_id={user.id if user else 'desconocido'}")
    await update.message.reply_text(MSG_HELP, parse_mode="Markdown")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Procesa mensajes de texto: aplica rate limit, consulta QueryEngine y responde.

    Flujo:
    1. Verificar rate limit del usuario.
    2. Enviar indicador de escritura (typing).
    3. Llamar a QueryEngine.query() con el texto del mensaje.
    4. Formatear y enviar la respuesta.

    Args:
        update: Objeto Update de Telegram.
        context: Contexto del handler con bot_data inyectado desde main.py.
    """
    if update.message is None or update.message.text is None:
        return

    user = update.effective_user
    user_id: int = user.id if user else 0
    question = update.message.text.strip()

    if not question:
        return


    logger.info(f"Bot: mensaje de user_id={user_id} ({len(question)} chars)")

    # --- Typing indicator ---
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    # --- Consulta al QueryEngine ---
    query_engine = context.bot_data.get("query_engine")
    doc_names: dict[str, str] = context.bot_data.get("doc_names", {})

    if query_engine is None:
        logger.error("Bot: query_engine no disponible en bot_data")
        await update.message.reply_text(MSG_ERROR)
        return

    try:
        result: QueryResult = await query_engine.query(question)
        response_text = _format_response(result, doc_names)
        logger.info(
            f"Bot: respuesta enviada a user_id={user_id} "
            f"(guard={result.guard_result}, cache={result.cache_hit}, "
            f"strategy={result.search_strategy}, {result.latency_ms}ms)"
        )
    except Exception as exc:
        logger.exception(f"Bot: error al procesar consulta de user_id={user_id}: {exc}")
        response_text = MSG_ERROR

    await update.message.reply_text(response_text, parse_mode="Markdown")
