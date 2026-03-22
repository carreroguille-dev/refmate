"""Guard Model para filtrado de consultas.

Responsabilidad única: clasificar una consulta de usuario como 'normal',
'out_of_scope' o 'injection' antes de que llegue al agente RAG.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from refmate.core.models import GuardResult
from refmate.core.protocols import TextGenerator

# Respuestas fijas para consultas bloqueadas
RESPONSE_OUT_OF_SCOPE = "Lo siento, solo puedo responder preguntas sobre normativa de balonmano."
RESPONSE_INJECTION = "No puedo procesar esta consulta."

# Mapa de tokens válidos que puede devolver el LLM → clasificación canónica
_VALID_CLASSIFICATIONS: dict[str, str] = {
    "NORMAL": "normal",
    "OUT_OF_SCOPE": "out_of_scope",
    "INJECTION": "injection",
}


class OpenRouterGuardModel:
    """Implementa el protocolo GuardModel usando un TextGenerator de OpenRouter.

    Clasifica consultas de usuario en tres categorías mediante un LLM pequeño
    (Qwen3-4B) con temperature=0.0 para máxima determinismo. En caso de
    respuesta inesperada del modelo, el fallback seguro es 'out_of_scope'.
    """

    def __init__(self, generator: TextGenerator, prompt_path: Path) -> None:
        """Inicializa el guard model.

        Args:
            generator: Implementación de TextGenerator inyectada (protocolo).
            prompt_path: Ruta al fichero guard_prompt.md con el system prompt.

        Raises:
            FileNotFoundError: Si prompt_path no existe.
        """
        self._generator = generator
        self._system_prompt = prompt_path.read_text(encoding="utf-8").strip()
        logger.debug(f"OpenRouterGuardModel inicializado → prompt={prompt_path.name}")

    async def classify(self, query: str) -> GuardResult:
        """Clasifica una consulta de usuario.

        Llama al LLM con el guard prompt y parsea su respuesta (NORMAL,
        OUT_OF_SCOPE o INJECTION). Si la respuesta no es reconocida, aplica
        fallback seguro a 'out_of_scope'.

        Args:
            query: Texto de la consulta del usuario.

        Returns:
            GuardResult con la clasificación canónica y confidence=1.0.
        """
        raw = await self._generator.generate(
            system_prompt=self._system_prompt,
            user_prompt=query,
        )

        token = raw.strip().upper()
        classification = _VALID_CLASSIFICATIONS.get(token)

        if classification is None:
            logger.warning(
                f"Guard: respuesta inesperada del LLM '{raw!r}' para query '{query[:80]}'. "
                "Aplicando fallback → out_of_scope"
            )
            classification = "out_of_scope"

        logger.debug(f"Guard: '{query[:80]}' → {classification}")
        return GuardResult(classification=classification, confidence=1.0)
