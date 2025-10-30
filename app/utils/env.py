import os
import logging
from typing import Iterable, Optional
from app.logging_config import get_logger, bind_logger, configure_logging
configure_logging()



def get_float_from_env(
    keys: Iterable[str],
    default: float = 0.0,
    min_value: Optional[float] = None,
    logger: Optional[logging.Logger] = None,
) -> float:
    """Return a float from the first present env var in keys, else default.

    - keys: priority-ordered env var names to check
    - default: value to use if none present/valid
    - min_value: if provided, value must be strictly greater than this
    - logger: optional logger for warnings; falls back to module logger

    Examples:
        get_float_from_env(["FOO", "BAR"], default=10.0, min_value=0.0)
    """
    logger = get_logger(__name__)
    log = bind_logger(logger, {"function": "get_float_from_env"})
    log.info("Fetching float from env vars: %s", keys)

    selected_key = None
    raw = None
    for key in keys:
        raw = os.getenv(key)
        if raw not in (None, ""):
            selected_key = key
            break

    if raw in (None, ""):
        log.info("No env var set from %s; defaulting to %s", keys, default)
        return default

    try:
        
        val = float(raw)
        log.info("Using env var %s=%s", selected_key, val)
    except Exception:
        log.warning("Invalid float for %s='%s'; defaulting to %s", selected_key, raw, default)
        return default

    if min_value is not None and val <= min_value:
        log.warning(
            "Value for %s=%s is <= min_value(%s); defaulting to %s",
            selected_key,
            val,
            min_value,
            default,
        )
        return default

    return val
