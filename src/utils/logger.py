import sys
from loguru import logger
from config.settings import settings

def setup_logger():
    """
    Configures the application logger.
    """
    logger.remove()  # Remove default handler

    # Add console handler
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=settings.LOG_LEVEL,
    )

    # Add file handler
    log_file = settings.BASE_DIR / "logs" / "refmate.log"
    logger.add(
        log_file,
        rotation="10 MB",
        retention="1 month",
        level=settings.LOG_LEVEL,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    )

    return logger

# Initialize logger
log = setup_logger()
