"""Provider-neutral classification for LLM transport and API failures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ProviderErrorKind(str, Enum):
    PROVIDER_DOWN = "provider_down"
    BUDGET_EXCEEDED = "budget_exceeded"
    RATE_LIMITED = "rate_limited"
    CONTEXT_TOO_LARGE = "context_too_large"
    AUTHENTICATION_ERROR = "authentication_error"
    INVALID_REQUEST = "invalid_request"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderErrorInfo:
    kind: ProviderErrorKind
    status_code: Optional[int]
    retryable: bool
    shrink_output: bool = False


_CONTEXT_MARKERS = (
    "context_length_exceeded",
    "context length",
    "maximum context",
    "too many tokens",
    "prompt is too long",
    "context window",
    "input is too long",
)
_BUDGET_MARKERS = (
    "requires more credits",
    "can only afford",
    "insufficient credit",
    "insufficient_quota",
    "billing",
    "payment required",
)
_AUTH_MARKERS = (
    "invalid_api_key",
    "invalid api key",
    "incorrect api key",
    "unauthorized",
    "authentication",
)
_RATE_MARKERS = ("rate limit", "rate_limit", "too many requests", "tokens per minute", "tpm")
_DOWN_MARKERS = (
    "connection error",
    "connection refused",
    "connection reset",
    "read operation timed out",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
)


def _status_code(exc: BaseException, text: str) -> Optional[int]:
    direct = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    for value in (direct, response_status):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    match = re.search(r"(?:error code|status|http)[^0-9]{0,8}([1-5][0-9]{2})", text)
    return int(match.group(1)) if match else None


def classify_provider_error(exc: BaseException) -> ProviderErrorInfo:
    text = str(exc or "").lower()
    status = _status_code(exc, text)

    if status == 402 or any(marker in text for marker in _BUDGET_MARKERS):
        # The transport layer performs deterministic output shrinking first.
        # If that is exhausted, repeating the same outer request is wasteful;
        # orchestration should move to a cheaper fallback model.
        return ProviderErrorInfo(ProviderErrorKind.BUDGET_EXCEEDED, status, retryable=False, shrink_output=True)
    if status in {401, 403} or any(marker in text for marker in _AUTH_MARKERS):
        return ProviderErrorInfo(ProviderErrorKind.AUTHENTICATION_ERROR, status, retryable=False)
    if any(marker in text for marker in _CONTEXT_MARKERS):
        return ProviderErrorInfo(ProviderErrorKind.CONTEXT_TOO_LARGE, status, retryable=False)
    if status == 429 or any(marker in text for marker in _RATE_MARKERS):
        return ProviderErrorInfo(ProviderErrorKind.RATE_LIMITED, status, retryable=True)
    if status is not None and 500 <= status <= 599:
        return ProviderErrorInfo(ProviderErrorKind.PROVIDER_DOWN, status, retryable=True)
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(marker in text for marker in _DOWN_MARKERS):
        return ProviderErrorInfo(ProviderErrorKind.PROVIDER_DOWN, status, retryable=True)
    if status == 400 or (status is not None and 400 <= status <= 499):
        return ProviderErrorInfo(ProviderErrorKind.INVALID_REQUEST, status, retryable=False)
    return ProviderErrorInfo(ProviderErrorKind.UNKNOWN, status, retryable=True)


__all__ = ["ProviderErrorInfo", "ProviderErrorKind", "classify_provider_error"]
