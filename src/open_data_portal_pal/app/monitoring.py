import datetime as dt
import json
import logging
import time
from functools import wraps
from typing import Any, Callable


class JSONFormatter(logging.Formatter):
    """Format log records as JSON for log aggregation (ELK, Datadoh, etc...)."""

    def format(self, record):
        log_obj = {
            "timestamp": dt.datetime.now(tz=dt.UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
        }

        # merge any extra data attached to the record
        if hasattr(record, "extra_data"):
            log_obj.update(record.extra_data)
        return json.dumps(log_obj)


def get_logger(name: str = "open-data-portal-pal") -> logging.Logger:
    """Create a structed JSON logger."""
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger


class MetricCollector:
    """
    Collects and aggregates applications metrics.

    In production, replace with Prometheus client:
        from prometheus_client import Counter, Histogram
    """

    def __init__(self):
        self._requests_total = 0
        self._errors_total = 0
        self._latency_sum = 0
        self._latency_count = 0
        self._tokens_input = 0
        self._tokens_output = 0
        self._cache_hits = 0
        self._cache_misses = 0

    def record_request(
        self,
        latency_ms: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        error: bool = False,
        cache_hit: bool = False,
    ):
        self._requests_total += 1
        self._latency_sum += latency_ms
        self._latency_count += 1
        self._tokens_input += input_tokens
        self._tokens_output += output_tokens
        if error:
            self._errors_total += 1

        if cache_hit:
            self._cache_hits += 1

        else:
            self._cache_misses += 1
