"""Implementación de OCRProvider para LightOnOCR mediante endpoint vLLM.

Responsabilidad única: comunicarse con el servidor vLLM que sirve el modelo
LightOnOCR y extraer texto de imágenes via API OpenAI-compatible (vision).
"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx
from loguru import logger

from refmate.config import OcrModelConfig


class LightOnOCRProvider:
    """Cliente HTTP para el endpoint vLLM de LightOnOCR.

    Implementa el protocolo OCRProvider convirtiendo imágenes a base64 y
    enviándolas al endpoint configurado en formato OpenAI vision.
    """

    def __init__(self, config: OcrModelConfig) -> None:
        """Inicializa el proveedor con la configuración del modelo OCR.

        Args:
            config: Configuración del modelo OCR (endpoint, name, temperature, etc.).
        """
        self._config = config
        logger.debug(f"LightOnOCRProvider inicializado → endpoint={config.endpoint}")

    async def extract_text(self, image_path: Path) -> str:
        """Extrae texto de una imagen PNG usando LightOnOCR via vLLM.

        Lee la imagen, la codifica en base64 y la envía al endpoint vLLM
        con formato OpenAI-compatible (vision message).

        Args:
            image_path: Ruta a la imagen PNG a procesar.

        Returns:
            Texto extraído de la imagen.

        Raises:
            FileNotFoundError: Si la imagen no existe.
            httpx.HTTPStatusError: Si el servidor devuelve un código de error HTTP.
            ValueError: Si la respuesta no contiene texto extraído.
        """
        if not image_path.exists():
            raise FileNotFoundError(f"Imagen no encontrada: {image_path}")

        image_bytes = image_path.read_bytes()
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        data_uri = f"data:image/png;base64,{b64_image}"

        payload = {
            "model": self._config.name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri},
                        }
                    ],
                }
            ],
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
            "top_p": self._config.top_p,
        }

        logger.debug(f"OCR request → {image_path.name}")

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(self._config.endpoint, json=payload)
            response.raise_for_status()

        data = response.json()

        try:
            text: str = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ValueError(
                f"Respuesta inesperada del endpoint OCR para {image_path.name}: {data}"
            ) from exc

        logger.debug(f"OCR completado → {image_path.name} ({len(text)} chars)")
        return text
