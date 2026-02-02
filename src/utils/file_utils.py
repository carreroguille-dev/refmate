import hashlib
import json
import re
from pathlib import Path
from typing import Any, Union
from src.utils.logger import logger

def ensure_directory(path: Path) -> Path:
    """Creates the directory if it does not exist."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception as e:
        logger.error(f"Error creating directory {path}: {e}")
        raise

def calculate_checksum(file_path: Path) -> str:
    """Calculates the SHA256 checksum of a file."""
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception as e:
        logger.error(f"Error calculating checksum for {file_path}: {e}")
        return ""

def load_json(file_path: Path) -> Any:
    """Loads a JSON file. Returns None if file doesn't exist or is invalid."""
    if not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {file_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return None

def save_json(file_path: Path, data: Any, indent: int = 2) -> bool:
    """Saves data to a JSON file."""
    try:
        ensure_directory(file_path.parent)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error saving JSON to {file_path}: {e}")
        return False

def safe_filename(filename: str) -> str:
    """Removes potentially dangerous characters from a filename."""
    cleaned = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    cleaned = cleaned.strip(".-_")
    return cleaned or "unnamed_file"
