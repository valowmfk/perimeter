"""
axapi
=====

Perimeter AXAPI utility package for talking to A10 ACOS devices.

Provides:
- AxapiClient: HTTP/JSON client wrapper around AXAPI v3
- AxapiError / AxapiAuthError: structured exceptions
- waiters: polling helpers
- utils: logging, URL helpers, environment helpers
"""

from .errors import AxapiError, AxapiAuthError, AxapiTimeoutError, AxapiTransportError
from .client import AxapiClient
from . import waiters
from . import utils

__all__ = [
    "AxapiClient",
    "AxapiError",
    "AxapiAuthError",
    "AxapiTimeoutError",
    "AxapiTransportError",
    "waiters",
    "utils",
]
