import os
import logging
from typing import Iterable, Optional


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
    log = logger or logging.getLogger(__name__)

    selected_key = None
    raw = None
    for key in keys:
        raw = os.getenv(key)
        if raw not in (None, ""):
            selected_key = key
            break

    if raw in (None, ""):
        return default

    try:
        val = float(raw)
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
