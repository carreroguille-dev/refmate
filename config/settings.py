import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # APIs
    OPENAI_API_KEY: str | None = None
    TELEGRAM_BOT_TOKEN: str | None = None

    # App Config
    LOG_LEVEL: str = "INFO"
    MAX_TOKENS_PER_CHUNK: int = 14000

    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent
    
    # Data Paths (relative to BASE_DIR if not absolute)
    DATA_RAW_PATH: Path = Path("data/raw")
    DATA_PROCESSED_PATH: Path = Path("data/processed")
    DATA_CHUNKS_PATH: Path = Path("data/chunks")
    DATA_INDEX_PATH: Path = Path("data/index")
    DATA_CACHE_PATH: Path = Path("data/cache")

    def get_absolute_path(self, path: Path) -> Path:
        """Returns the absolute path, resolving relative paths against BASE_DIR."""
        if path.is_absolute():
            return path
        return self.BASE_DIR / path

    @property
    def raw_path(self) -> Path:
        return self.get_absolute_path(self.DATA_RAW_PATH)
    
    @property
    def processed_path(self) -> Path:
        return self.get_absolute_path(self.DATA_PROCESSED_PATH)

    @property
    def chunks_path(self) -> Path:
        return self.get_absolute_path(self.DATA_CHUNKS_PATH)

    @property
    def index_path(self) -> Path:
        return self.get_absolute_path(self.DATA_INDEX_PATH)

    @property
    def cache_path(self) -> Path:
        return self.get_absolute_path(self.DATA_CACHE_PATH)


settings = Settings()
