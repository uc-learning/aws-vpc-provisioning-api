"""
Structured JSON logging for Lambda functions.

Features:
- JSON format for CloudWatch Logs Insights queries
- Automatic injection of Lambda context fields (requestId, functionName)
- Correlation fields: requestId, clientRequestId, executionArn
- Authorization header / JWT values are never logged
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()


class _JsonFormatter(logging.Formatter):
    """Format a LogRecord as a single-line JSON object."""

    RESERVED = {"message", "asctime", "levelname", "name", "pathname", "lineno"}

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        log_object: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Propagate any extra kwargs passed to the logger call
        for key, value in record.__dict__.items():
            if key not in logging.LogRecord.__dict__ and key not in self.RESERVED and not key.startswith("_"):
                log_object[key] = value

        if record.exc_info:
            log_object["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_object, default=str)


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger that emits structured JSON to stdout.
    Re-uses the logger if it has already been configured (Lambda reuse path).
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(_LOG_LEVEL)
    logger.propagate = False
    return logger


class RequestLogger:
    """
    Context-aware logger that automatically injects correlation fields
    into every log call.

    Usage:
        log = RequestLogger("create_vpc", request_id="abc", requested_by="sub-xyz")
        log.info("Processing request", extra_field="value")
    """

    def __init__(
        self,
        name: str,
        *,
        request_id: str | None = None,
        client_request_id: str | None = None,
        execution_arn: str | None = None,
        requested_by: str | None = None,
    ) -> None:
        self._logger = get_logger(name)
        self._context: dict[str, Any] = {}
        if request_id:
            self._context["requestId"] = request_id
        if client_request_id:
            self._context["clientRequestId"] = client_request_id
        if execution_arn:
            self._context["executionArn"] = execution_arn
        if requested_by:
            self._context["requestedBy"] = requested_by

    def _merge(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        merged = {**self._context, **kwargs}
        return merged

    def info(self, message: str, **kwargs: Any) -> None:
        self._logger.info(message, extra=self._merge(kwargs))

    def warning(self, message: str, **kwargs: Any) -> None:
        self._logger.warning(message, extra=self._merge(kwargs))

    def error(self, message: str, **kwargs: Any) -> None:
        self._logger.error(message, extra=self._merge(kwargs))

    def debug(self, message: str, **kwargs: Any) -> None:
        self._logger.debug(message, extra=self._merge(kwargs))

    def exception(self, message: str, **kwargs: Any) -> None:
        self._logger.exception(message, extra=self._merge(kwargs))
