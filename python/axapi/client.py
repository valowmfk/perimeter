"""
HTTP/JSON AXAPI v3 client for A10 ACOS devices.

Design goals:
- Usable from CLI, Flask, or the future Perimeter CLI.
- Handles auth, session token, and basic error mapping.
- Leaves higher-level workflow logic to python/workflows/*
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests
from requests import Session, Response
import urllib3

# ──────────────────────────────────────────────
# Disable HTTPS warnings for self-signed certs
# ──────────────────────────────────────────────
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from .errors import AxapiError, AxapiAuthError, AxapiTransportError
from .utils import build_base_url, qlog_warning, qlog_success

class AxapiClient:
    """
    Thin wrapper around AXAPI v3 HTTP interface.

    Typical usage:

        from axapi.client import AxapiClient

        with AxapiClient(host="10.1.55.50", username="admin", password="secret") as client:
            client.login()
            system_info = client.get("/axapi/v3/system")
    """

    def __init__(
        self,
        host: str,
        *,
        username: str,
        password: str,
        use_https: bool = True,
        port: Optional[int] = None,
        timeout: int = 10,
        verify_ssl: bool = False,
        component: str = "AXAPI",
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.use_https = use_https
        self.port = port
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.component = component

        self._session: Session = requests.Session()
        self._base_url = build_base_url(host, https=use_https, port=port)
        self._auth_token: Optional[str] = None

    # Expose a public read-only view of the auth token
    # Workflows expect to check client.session_id, so we map it here.
    @property
    def session_id(self) -> Optional[str]:
        """Return the AXAPI session_id token (or None if not authenticated)."""
        return self._auth_token

    def _auth_headers(self) -> Dict[str, str]:
        """
        Build the correct Authorization header.

        ACOS first-login signature tokens (32 hex chars) use:
            Authorization: A10 <signature>

        ACOS session_id tokens (after password rotation) also use:
            Authorization: A10 <session_id>
        """
        if not self._auth_token:
            return {}

        token = self._auth_token

        # First-login signature token (32 hex characters)
        if isinstance(token, str) and len(token) == 32:
            return {"Authorization": f"A10 {token}"}

        # Normal session_id token
        return {"Authorization": f"A10 {token}"}

    # ─────────────────────────────
    # Context manager
    # ─────────────────────────────

    def __enter__(self) -> "AxapiClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.logoff()
        except Exception:
            pass
        self._session.close()

    # ─────────────────────────────
    # Auth lifecycle
    # ─────────────────────────────

    def login(self, *, tolerate_no_token: bool = False) -> None:
        """
        Authenticate and store the session token.
        """
        url = f"{self._base_url}/axapi/v3/auth"
        payload = {
            "credentials": {
                "username": self.username,
                "password": self.password,
            }
        }

        try:
            resp = self._session.post(
                url,
                json=payload,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as exc:
            raise AxapiTransportError(f"HTTP transport error during login: {exc}") from exc

        data = self._parse_response(resp, expect_auth=True)

        # First try the standard session_id path
        token = data.get("auth", {}).get("session_id")
        signature = None

        # If no session_id, check for first-login signature token
        if not token:
            signature = data.get("authresponse", {}).get("signature")

        # ---------------------------------------------------------
        #  Dual-mode token handling:
        #  - Normal: auth.session_id
        #  - First-login: authresponse.signature (Authorization: A10 <sig>)
        # ---------------------------------------------------------
        if token:
            self._auth_token = token
            qlog_success(
                self.component,
                f"Authenticated to {self.host} (session_id token acquired)",
            )
            return

        if signature:
            # Treat the signature as our auth token; all subsequent calls
            # will send it via Authorization header (A10 <token>).
            self._auth_token = signature
            qlog_success(
                self.component,
                f"Authenticated to {self.host} (signature token acquired; default-password mode)",
            )
            return

        # If we reach here, neither session_id nor signature was found
        msg = "AXAPI auth succeeded but neither session_id nor signature token was returned"
        if tolerate_no_token:
            qlog_warning(self.component, msg)
            self._auth_token = None
            return

        # Strict mode failure
        raise AxapiAuthError(msg, response=data)


    def logoff(self) -> None:
        """Best-effort logoff. Safe to call even if login failed."""
        if not self._auth_token:
            return

        url = f"{self._base_url}/axapi/v3/logoff"
        headers = self._auth_headers()

        try:
            self._session.post(
                url,
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException:
            return
        finally:
            self._auth_token = None

    # ─────────────────────────────
    # Core request machinery
    # ─────────────────────────────

    def _parse_response(
        self,
        resp: Response,
        *,
        expect_auth: bool = False,
    ) -> Dict[str, Any]:
        """
        Common response parser:

        - Raises AxapiError/AxapiAuthError on failure.
        - Returns parsed JSON dict on success.
        """
        # HTTP 204 No Content — valid empty response (e.g. empty cert list)
        if resp.status_code == 204 or (resp.status_code < 400 and not resp.text.strip()):
            return {}

        try:
            payload = resp.json()
        except ValueError:
            raise AxapiError(
                f"Non-JSON response from AXAPI (status={resp.status_code})",
                response={"raw_text": resp.text},
            )

        if resp.status_code in (401, 403):
            raise AxapiAuthError(
                f"AXAPI authentication failed (HTTP {resp.status_code})",
                response=payload,
            )

        if resp.status_code >= 400:
            base_msg = f"AXAPI HTTP error {resp.status_code}"
            if isinstance(payload, dict):
                err = (payload.get("response") or {}).get("err")
            else:
                err = None
            if isinstance(err, dict):
                msg = err.get("msg") or base_msg
                code = err.get("code")
                src = err.get("from")
                raise AxapiError(
                    msg,
                    code=code,
                    source=src,
                    status="fail",
                    response=payload,
                )
            raise AxapiError(base_msg, response=payload)

        if expect_auth:
            return payload if isinstance(payload, dict) else {"raw": payload}

        if isinstance(payload, dict):
            resp_wrapper = payload.get("response")
            if isinstance(resp_wrapper, dict) and resp_wrapper.get("status") == "fail":
                err = resp_wrapper.get("err") or {}
                msg = err.get("msg") or "AXAPI response.status=fail"
                code = err.get("code")
                src = err.get("from")
                raise AxapiError(
                    msg,
                    code=code,
                    status="fail",
                    source=src,
                    response=payload,
                )

        return payload if isinstance(payload, dict) else {"raw": payload}

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Low-level generic request helper.

        `path` should start with '/axapi/...'.
        """
        if not path.startswith("/"):
            raise ValueError("AXAPI path must start with '/'")

        url = f"{self._base_url}{path}"
        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"
        timeout = timeout or self.timeout

        try:
            resp = self._session.request(
                method=method.upper(),
                url=url,
                headers=headers,
                json=json_body,
                timeout=timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as exc:
            raise AxapiTransportError(f"HTTP transport error: {exc}") from exc

        return self._parse_response(resp)

    def get(self, path: str, *, timeout: Optional[int] = None) -> Dict[str, Any]:
        return self.request("GET", path, timeout=timeout)

    def post(
        self,
        path: str,
        body: Dict[str, Any],
        *,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self.request("POST", path, json_body=body, timeout=timeout)

    def put(
        self,
        path: str,
        body: Dict[str, Any],
        *,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self.request("PUT", path, json_body=body, timeout=timeout)

    def delete(self, path: str, *, timeout: Optional[int] = None) -> Dict[str, Any]:
        return self.request("DELETE", path, timeout=timeout)

    # ─────────────────────────────
    # Optional high-level helpers
    # ─────────────────────────────

    def write_memory(self) -> Dict[str, Any]:
        """
        ACOS 'write memory' wrapper.

        Endpoint: /axapi/v3/write/memory
        """
        qlog_warning(self.component, "Issuing 'write memory' to device")
        return self.post("/axapi/v3/write/memory", body={"memory": {"partition": "shared"}})

    def reboot(self, warm: bool = True) -> Dict[str, Any]:
        """
        ACOS 'reboot' wrapper.

        Endpoint: /axapi/v3/reboot

        Note: ACOS requires at least one field in the reboot object.
        Using "all": 1 to reboot the device immediately (works for both VCS and standalone).
        """
        qlog_warning(self.component, f"Issuing reboot to device")
        body = {"reboot": {"all": 1}}
        return self.post("/axapi/v3/reboot", body=body)
