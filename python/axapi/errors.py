"""
Dedicated exception types for the Perimeter AXAPI client.
"""

from __future__ import annotations
from typing import Any, Optional


class AxapiError(Exception):
    """
    Base exception for AXAPI-related failures.

    Attributes:
        message: Human readable error message.
        code: AXAPI error code (if any).
        status: AXAPI 'status' field (e.g. 'fail').
        source: AXAPI 'from' field (e.g. 'CM').
        response: Raw parsed JSON response (if available).
    """

    def __init__(
        self,
        message: str,
        *,
        code: Optional[int] = None,
        status: Optional[str] = None,
        source: Optional[str] = None,
        response: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.source = source
        self.response = response

    def __str__(self) -> str:
        parts = [self.message]
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.status:
            parts.append(f"status={self.status}")
        if self.source:
            parts.append(f"from={self.source}")
        return " | ".join(parts)


class AxapiAuthError(AxapiError):
    """Authentication / authorisation failures (wrong password, expired session, etc.)."""


class AxapiTimeoutError(AxapiError):
    """Raised when a wait/poll operation exceeds its timeout."""


class AxapiTransportError(AxapiError):
    """Raised when HTTP-level issues occur (connection errors, SSL issues, etc.)."""
