"""Implementación de TextGenerator para OpenRouter.

Responsabilidad única: comunicarse con la API de OpenRouter y devolver
el texto generado por el modelo configurado.
"""

from __future__ import annotations

import asyncio

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
            payload["thinking"] = {"type": "disabled"}
            logger.debug("Modo no-thinking activado en payload")

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
