"""Implementaciones de TextGenerator y ToolCallingLLM para OpenRouter.

Responsabilidad única: comunicarse con la API de OpenRouter y devolver
el texto generado por el modelo configurado. Incluye soporte para
tool calling (function calling) nativo.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from loguru import logger

from refmate.config import LlmModelConfig

OPENROUTER_REFERER = "https://github.com/refmate"
_RETRY_DELAYS = (2, 4, 8)


class OpenRouterTextGenerator:
    """Cliente HTTP para la API de OpenRouter.

    Implementa el protocolo TextGenerator con retry exponencial y soporte
    para el modo no-thinking de Qwen3.
    """

    def __init__(self, config: LlmModelConfig, api_key: str) -> None:
        """Inicializa el generador con la configuración del modelo y la API key.

        Args:
            config: Configuración del modelo LLM (endpoint, name, temperature, etc.).
            api_key: API key de OpenRouter.
        """
        self._config = config
        self._api_key = api_key
        logger.debug(
            f"OpenRouterTextGenerator inicializado → model={config.name} mode={config.mode}"
        )

    async def generate(self, system_prompt: str, user_prompt: str, **kwargs: object) -> str:
        """Genera una respuesta dado un system prompt y un user prompt.

        Reintenta hasta 3 veces con backoff exponencial (2s, 4s, 8s) ante
        errores HTTP o de red.

        Args:
            system_prompt: Instrucciones del sistema para el modelo.
            user_prompt: Mensaje del usuario o texto a procesar.
            **kwargs: Ignorados (compatibilidad con el protocolo TextGenerator).

        Returns:
            Texto generado por el modelo.

        Raises:
            RuntimeError: Si todos los reintentos fallan.
        """
        payload: dict[str, object] = {
            "model": self._config.name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
        }

        if self._config.mode == "no-thinking":
            # Qwen3 usa /no_think en el system prompt para desactivar el razonamiento.
            # El campo "thinking" de OpenRouter solo aplica a modelos Anthropic.
            system_prompt = "/no_think\n" + system_prompt
            logger.debug("Modo no-thinking: /no_think antepuesto al system prompt")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": OPENROUTER_REFERER,
            "X-Title": "RefMate",
            "Content-Type": "application/json",
        }

        last_exc: Exception | None = None
        for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
            try:
                async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                    response = await client.post(
                        self._config.endpoint, json=payload, headers=headers
                    )
                    response.raise_for_status()

                data = response.json()
                text: str = data["choices"][0]["message"]["content"]
                logger.debug(
                    f"OpenRouter respuesta recibida → {len(text)} chars "
                    f"(intento {attempt})"
                )
                return text

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                body = exc.response.text[:500] if exc.response else ""
                logger.warning(
                    f"OpenRouter intento {attempt}/3 fallido: {exc}. "
                    f"Body: {body}. Reintentando en {delay}s…"
                )
            except (httpx.HTTPError, KeyError, IndexError) as exc:
                last_exc = exc
                logger.warning(
                    f"OpenRouter intento {attempt}/3 fallido: {exc}. "
                    f"Reintentando en {delay}s…"
                )
                if attempt < len(_RETRY_DELAYS):
                    await asyncio.sleep(delay)

        raise RuntimeError(
            f"OpenRouter falló tras 3 intentos para el modelo '{self._config.name}'. "
            f"Último error: {last_exc}"
        )


class OpenRouterToolCallingLLM:
    """Cliente HTTP para tool calling (function calling) en OpenRouter.

    Implementa el protocolo ToolCallingLLM: realiza un único turno de
    conversación con tools disponibles y devuelve la respuesta del modelo
    (texto final O lista de tool calls, nunca ambos a la vez).

    El loop multi-turn es responsabilidad del llamador (RAGAgent).
    """

    def __init__(self, config: LlmModelConfig, api_key: str) -> None:
        """Inicializa el cliente con la configuración del modelo y la API key.

        Args:
            config: Configuración del modelo LLM (endpoint, name, temperature, etc.).
            api_key: API key de OpenRouter.
        """
        self._config = config
        self._api_key = api_key
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": OPENROUTER_REFERER,
            "X-Title": "RefMate",
            "Content-Type": "application/json",
        }
        logger.debug(
            f"OpenRouterToolCallingLLM inicializado → model={config.name} mode={config.mode}"
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Realiza un turno de conversación con soporte de tool calling.

        Reintenta hasta 3 veces con backoff exponencial ante errores de red.
        Aplica ``/no_think`` si el modelo está en modo no-thinking (Qwen3).

        Args:
            messages: Lista de mensajes en formato OpenAI
                      (role: system/user/assistant/tool).
            tools: Lista de esquemas de herramientas en formato OpenAI
                   function calling.

        Returns:
            ``(content, [])`` si el modelo devuelve texto final.
            ``(None, tool_calls)`` si el modelo invoca herramientas.

        Raises:
            RuntimeError: Si todos los reintentos fallan.
        """
        # Aplicar /no_think al primer mensaje de sistema si corresponde
        processed_messages = list(messages)
        if self._config.mode == "no-thinking":
            for i, msg in enumerate(processed_messages):
                if msg.get("role") == "system":
                    processed_messages[i] = {
                        **msg,
                        "content": "/no_think\n" + msg["content"],
                    }
                    break

        payload: dict[str, Any] = {
            "model": self._config.name,
            "messages": processed_messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
        }

        last_exc: Exception | None = None
        for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
            try:
                async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                    response = await client.post(
                        self._config.endpoint, json=payload, headers=self._headers
                    )
                    response.raise_for_status()

                data = response.json()
                message = data["choices"][0]["message"]
                raw_tool_calls = message.get("tool_calls")

                if raw_tool_calls:
                    logger.debug(
                        f"ToolCallingLLM → {len(raw_tool_calls)} tool call(s) "
                        f"(intento {attempt})"
                    )
                    return None, raw_tool_calls

                content: str = message.get("content") or ""
                logger.debug(
                    f"ToolCallingLLM → respuesta final {len(content)} chars "
                    f"(intento {attempt})"
                )
                return content, []

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                body = exc.response.text[:500] if exc.response else ""
                logger.warning(
                    f"ToolCallingLLM intento {attempt}/3 fallido: {exc}. "
                    f"Body: {body}. Reintentando en {delay}s…"
                )
                if attempt < len(_RETRY_DELAYS):
                    await asyncio.sleep(delay)
            except (httpx.HTTPError, KeyError, IndexError) as exc:
                last_exc = exc
                logger.warning(
                    f"ToolCallingLLM intento {attempt}/3 fallido: {exc}. "
                    f"Reintentando en {delay}s…"
                )
                if attempt < len(_RETRY_DELAYS):
                    await asyncio.sleep(delay)

        raise RuntimeError(
            f"OpenRouter ToolCallingLLM falló tras 3 intentos para el modelo "
            f"'{self._config.name}'. Último error: {last_exc}"
        )

    def build_tool_message(self, tool_call_id: str, content: str) -> dict[str, Any]:
        """Construye un mensaje de resultado de tool en formato OpenAI.

        Args:
            tool_call_id: ID del tool call devuelto por el modelo.
            content: Resultado de la ejecución de la herramienta.

        Returns:
            Dict con role='tool' listo para añadir a la lista de mensajes.
        """
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}

    def build_assistant_tool_call_message(
        self, tool_calls: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Construye el mensaje del asistente que contiene los tool calls.

        Necesario para mantener el historial de mensajes correcto antes de
        añadir los resultados de las tools.

        Args:
            tool_calls: Lista de tool calls en formato OpenAI.

        Returns:
            Dict con role='assistant' y tool_calls listo para el historial.
        """
        return {"role": "assistant", "content": None, "tool_calls": tool_calls}

    @staticmethod
    def parse_tool_call(tool_call: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        """Extrae id, nombre y argumentos de un tool call en formato OpenAI.

        Args:
            tool_call: Dict con campos id y function (name + arguments).

        Returns:
            Tuple ``(call_id, tool_name, arguments_dict)``.
        """
        call_id: str = tool_call["id"]
        fn = tool_call["function"]
        name: str = fn["name"]
        arguments: dict[str, Any] = json.loads(fn["arguments"])
        return call_id, name, arguments
