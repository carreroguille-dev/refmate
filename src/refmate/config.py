"""Módulo de configuración de RefMate.

Responsabilidad única: cargar config.yaml, resolver variables de entorno,
validar con Pydantic y exponer un singleton thread-safe.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel, ConfigDict, field_validator


# ---------------------------------------------------------------------------
# Modelos Pydantic para validación de la configuración
# ---------------------------------------------------------------------------


class DocumentConfig(BaseModel):
    """Configuración de un documento normativo."""

    model_config = ConfigDict(frozen=True)

    url: str
    nombre: str
    fuente: str
    tipo_jerarquia: str


class OcrModelConfig(BaseModel):
    """Configuración del modelo OCR."""

    model_config = ConfigDict(frozen=True)

    name: str
    endpoint: str
    temperature: float
    max_tokens: int
    top_p: float
    timeout_seconds: int
    health_timeout_seconds: int


class LlmModelConfig(BaseModel):
    """Configuración de un modelo LLM (structuring, guard, agent)."""

    model_config = ConfigDict(frozen=True)

    name: str
    provider: str
    endpoint: str
    temperature: float
    max_tokens: int
    timeout_seconds: int
    mode: str | None = None
    overlap_tokens: int | None = None


class EmbeddingsConfig(BaseModel):
    """Configuración del modelo de embeddings local."""

    model_config = ConfigDict(frozen=True)

    name: str
    provider: str
    device: str
    batch_size: int


class ModelsConfig(BaseModel):
    """Agrupación de todos los modelos del sistema."""

    model_config = ConfigDict(frozen=True)

    ocr: OcrModelConfig
    structuring: LlmModelConfig
    guard: LlmModelConfig
    agent: LlmModelConfig
    embeddings: EmbeddingsConfig


class CropMaskConfig(BaseModel):
    """Máscara de recorte en porcentaje para un documento."""

    model_config = ConfigDict(frozen=True)

    top_pct: float
    bottom_pct: float
    right_pct: float | None = None
    left_pct: float | None = None
    alternate_sides: bool = False


class RenderingConfig(BaseModel):
    """Parámetros de renderizado de PDFs a imagen."""

    model_config = ConfigDict(frozen=True)

    dpi: int
    format: str
    max_dimension: int


class RetrievalConfig(BaseModel):
    """Parámetros del sistema de recuperación."""

    model_config = ConfigDict(frozen=True)

    top_k: int
    max_cross_ref_expansion: int
    hybrid_fusion: str
    rrf_k: int


class ChunkerConfig(BaseModel):
    """Parámetros del chunker semántico."""

    model_config = ConfigDict(frozen=True)

    max_section_tokens: int
    max_chunk_tokens: int


class QdrantVectorsConfig(BaseModel):
    """Configuración de los vectores de la colección Qdrant."""

    model_config = ConfigDict(frozen=True)

    dense: dict[str, Any]
    sparse: dict[str, Any]


class QdrantConfig(BaseModel):
    """Configuración de la conexión a Qdrant."""

    model_config = ConfigDict(frozen=True)

    host: str
    port: int
    collection: str
    vectors: QdrantVectorsConfig


class CacheConfig(BaseModel):
    """Configuración de la caché semántica Redis."""

    model_config = ConfigDict(frozen=True)

    host: str
    port: int
    db: int
    ttl_days: int
    similarity_direct_hit: float
    similarity_context_hit: float
    min_hits_for_direct: int
    invalidate_on_ingest: bool


class TelegramConfig(BaseModel):
    """Configuración del bot de Telegram."""

    model_config = ConfigDict(frozen=True)

    token: str
    rate_limit_per_user: int

    @field_validator("token")
    @classmethod
    def token_not_empty(cls, v: str) -> str:
        """Valida que el token no esté vacío."""
        if not v:
            raise ValueError("TELEGRAM_BOT_TOKEN no puede estar vacío")
        return v


class ScraperConfig(BaseModel):
    """Parámetros operacionales del scraper de descarga."""

    model_config = ConfigDict(frozen=True)

    timeout_seconds: int
    max_retries: int
    backoff_base: int


class ApiKeysConfig(BaseModel):
    """API keys externas."""

    model_config = ConfigDict(frozen=True)

    openrouter: str

    @field_validator("openrouter")
    @classmethod
    def openrouter_not_empty(cls, v: str) -> str:
        """Valida que la API key de OpenRouter no esté vacía."""
        if not v:
            raise ValueError("OPENROUTER_API_KEY no puede estar vacío")
        return v


class RefMateConfig(BaseModel):
    """Configuración completa del sistema RefMate."""

    model_config = ConfigDict(frozen=True)

    documents: dict[str, DocumentConfig]
    models: ModelsConfig
    crop_masks: dict[str, CropMaskConfig]
    rendering: RenderingConfig
    retrieval: RetrievalConfig
    chunker: ChunkerConfig
    qdrant: QdrantConfig
    cache: CacheConfig
    scraper: ScraperConfig
    telegram: TelegramConfig
    api_keys: ApiKeysConfig


# ---------------------------------------------------------------------------
# Resolución de variables de entorno
# ---------------------------------------------------------------------------

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(obj: Any) -> Any:
    """Resuelve recursivamente las cadenas '${VAR}' con valores de entorno.

    Args:
        obj: Cualquier valor del YAML (dict, list, str, int...).

    Returns:
        El mismo objeto con las cadenas resueltas.

    Raises:
        ValueError: Si una variable de entorno requerida no está definida.
    """
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(item) for item in obj]
    if isinstance(obj, str):
        def replace_match(match: re.Match) -> str:  # type: ignore[type-arg]
            var_name = match.group(1)
            value = os.environ.get(var_name)
            if value is None:
                raise ValueError(
                    f"Variable de entorno requerida '{var_name}' no definida. "
                    f"Comprueba tu fichero .env o las variables de entorno del sistema."
                )
            return value

        return _ENV_VAR_PATTERN.sub(replace_match, obj)
    return obj


# ---------------------------------------------------------------------------
# Carga del fichero config.yaml
# ---------------------------------------------------------------------------

def _find_config_file() -> Path:
    """Busca config.yaml subiendo desde este fichero hasta encontrarlo.

    Returns:
        Path al fichero config.yaml.

    Raises:
        FileNotFoundError: Si no se encuentra config.yaml en ningún directorio padre.
    """
    current = Path(__file__).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / "config.yaml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "No se encontró config.yaml. Asegúrate de que existe en la raíz del proyecto."
    )


def get_project_root() -> Path:
    """Devuelve la raíz del proyecto (directorio que contiene config.yaml).

    Returns:
        Path al directorio raíz del proyecto.
    """
    return _find_config_file().parent


@lru_cache(maxsize=1)
def get_config() -> RefMateConfig:
    """Carga, valida y devuelve la configuración del sistema (singleton).

    Carga el fichero .env antes de resolver variables de entorno.
    La configuración se cachea después de la primera carga.

    Returns:
        RefMateConfig validado y completo.

    Raises:
        FileNotFoundError: Si no se encuentra config.yaml.
        ValueError: Si falta alguna variable de entorno requerida.
        ValidationError: Si la configuración no cumple el esquema Pydantic.
    """
    config_path = _find_config_file()

    # Cargar .env junto al config.yaml si existe (no sobreescribe vars ya definidas)
    env_path = config_path.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)
        logger.debug(f"Variables de entorno cargadas desde {env_path}")
    logger.info(f"Cargando configuración desde {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    resolved = _resolve_env_vars(raw)
    config = RefMateConfig.model_validate(resolved)
    logger.info("Configuración cargada y validada correctamente")
    return config
