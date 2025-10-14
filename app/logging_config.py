import logging
import os
import json
from datetime import datetime, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo



class JsonFormatter(logging.Formatter):
    """Simple JSON formatter for structured logs.

    It serializes the LogRecord into a JSON object containing timestamp, level,
    logger name, message and any extra fields passed via the ``extra`` argument.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Base payload
        now = datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()
        # now = datetime.now(timezone.utc) --- IGNORE ---
        payload: Dict[str, Any] = {
            "timestamp": now,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include exception info if present
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        # Include any extra keys that were passed via the 'extra' parameter
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
        }

        for key, value in record.__dict__.items():
            if key in standard_attrs:
                continue
            try:
                json.dumps(value)  # ensure serializable
                payload[key] = value
            except Exception:
                payload[key] = str(value)

        return json.dumps(payload, ensure_ascii=False)



def configure_logging(level: int = logging.INFO, log_file: str | None = None) -> None:
    """Configure the root logger to use JSON formatter. Logs to terminal and file in local/dev."""
    root = logging.getLogger()
    # Avoid adding multiple handlers during module reloads
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers if h.formatter):
        return

    env = os.environ.get("ENV", "production").lower()
    handlers = []

    if env in ("local", "development", "dev"):
        # Log to both terminal and file
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(JsonFormatter())
        handlers.append(stream_handler)

        # Default log file path
        log_file_path = log_file or os.path.join(os.path.dirname(__file__), "app-local.log")
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(log_file_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        handlers.append(file_handler)
    else:
        # Production/staging: log to stream (or file if specified)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(JsonFormatter())
        handlers.append(stream_handler)
        if log_file:
            from logging.handlers import RotatingFileHandler
            file_handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
            file_handler.setFormatter(JsonFormatter())
            handlers.append(file_handler)

    root.handlers = handlers
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def bind_logger(logger: logging.Logger, extra: Dict[str, Any] | None = None) -> logging.LoggerAdapter:
    """Return a LoggerAdapter with provided extra context dict attached.

    Usage:
        from app.logging_config import get_logger, bind_logger
        from zoneinfo import ZoneInfo
        logger = get_logger(__name__)
        log = bind_logger(logger, {"user_id": user_id, "trace_id": trace_id})
        log.info("started")
    """
    return logging.LoggerAdapter(logger, extra or {})
