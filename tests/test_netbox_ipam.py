"""Tests for python/helpers/netbox_ipam.py — Netbox IPAM management."""

from unittest.mock import patch, MagicMock

import pytest

from helpers.netbox_ipam import (
    netbox_create_ip,
    netbox_delete_ip,
    _find_ip_id,
    _netbox_update_existing,
)


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    monkeypatch.setattr("helpers.netbox_ipam.NETBOX_URL", "https://netbox-test")
    monkeypatch.setattr("helpers.netbox_ipam.NETBOX_API_TOKEN", "test-token")


# ──────────────────────────────
# netbox_create_ip
# ──────────────────────────────

class TestNetboxCreateIp:
    @patch("helpers.netbox_ipam.requests.post")
    def test_create_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=201)
        assert netbox_create_ip("10.1.55.50", "web-01") is True
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["json"]["address"] == "10.1.55.50/24"
        assert call_kwargs[1]["json"]["dns_name"] == "web-01"

    @patch("helpers.netbox_ipam._netbox_update_existing", return_value=True)
    @patch("helpers.netbox_ipam.requests.post")
    def test_create_duplicate_updates(self, mock_post, mock_update):
        resp = MagicMock(status_code=400, content=b'{}')
        resp.json.return_value = {"address": ["IP already exists in Netbox"]}
        mock_post.return_value = resp
        assert netbox_create_ip("10.1.55.50", "web-01") is True
        mock_update.assert_called_once_with("10.1.55.50", "web-01")

    @patch("helpers.netbox_ipam.requests.post")
    def test_create_error(self, mock_post):
        resp = MagicMock(status_code=500, content=b'err', text="Internal error")
        resp.json.return_value = {}
        mock_post.return_value = resp
        assert netbox_create_ip("10.1.55.50", "web-01") is False

    @patch("helpers.netbox_ipam.requests.post", side_effect=Exception("timeout"))
    def test_create_exception(self, mock_post):
        assert netbox_create_ip("10.1.55.50", "web-01") is False

    def test_create_no_token(self, monkeypatch):
        monkeypatch.setattr("helpers.netbox_ipam.NETBOX_API_TOKEN", "")
        assert netbox_create_ip("10.1.55.50", "web-01") is True  # non-fatal skip


# ──────────────────────────────
# _find_ip_id
# ──────────────────────────────

class TestFindIpId:
    @patch("helpers.netbox_ipam.requests.get")
    def test_found(self, mock_get):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"results": [{"id": 42}]}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        assert _find_ip_id("10.1.55.50") == 42

    @patch("helpers.netbox_ipam.requests.get")
    def test_not_found(self, mock_get):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"results": []}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        assert _find_ip_id("10.1.55.50") is None

    @patch("helpers.netbox_ipam.requests.get", side_effect=Exception("err"))
    def test_exception(self, mock_get):
        assert _find_ip_id("10.1.55.50") is None


# ──────────────────────────────
# _netbox_update_existing
# ──────────────────────────────

class TestNetboxUpdateExisting:
    @patch("helpers.netbox_ipam.requests.patch")
    @patch("helpers.netbox_ipam._find_ip_id", return_value=42)
    def test_update_success(self, mock_find, mock_patch):
        mock_patch.return_value = MagicMock(status_code=200)
        assert _netbox_update_existing("10.1.55.50", "web-01") is True

    @patch("helpers.netbox_ipam._find_ip_id", return_value=None)
    def test_update_not_found(self, mock_find):
        assert _netbox_update_existing("10.1.55.50", "web-01") is True  # non-fatal

    @patch("helpers.netbox_ipam.requests.patch")
    @patch("helpers.netbox_ipam._find_ip_id", return_value=42)
    def test_update_error(self, mock_find, mock_patch):
        mock_patch.return_value = MagicMock(status_code=500, text="err")
        assert _netbox_update_existing("10.1.55.50", "web-01") is False


# ──────────────────────────────
# netbox_delete_ip
# ──────────────────────────────

class TestNetboxDeleteIp:
    @patch("helpers.netbox_ipam.requests.delete")
    @patch("helpers.netbox_ipam._find_ip_id", return_value=42)
    def test_delete_success(self, mock_find, mock_delete):
        mock_delete.return_value = MagicMock(status_code=204)
        assert netbox_delete_ip("10.1.55.50") is True

    @patch("helpers.netbox_ipam._find_ip_id", return_value=None)
    def test_delete_not_found(self, mock_find):
        assert netbox_delete_ip("10.1.55.50") is True  # idempotent

    @patch("helpers.netbox_ipam.requests.delete")
    @patch("helpers.netbox_ipam._find_ip_id", return_value=42)
    def test_delete_404(self, mock_find, mock_delete):
        mock_delete.return_value = MagicMock(status_code=404)
        assert netbox_delete_ip("10.1.55.50") is True

    @patch("helpers.netbox_ipam.requests.delete")
    @patch("helpers.netbox_ipam._find_ip_id", return_value=42)
    def test_delete_error(self, mock_find, mock_delete):
        mock_delete.return_value = MagicMock(status_code=500, text="err")
        assert netbox_delete_ip("10.1.55.50") is False

    @patch("helpers.netbox_ipam.requests.delete", side_effect=Exception("err"))
    @patch("helpers.netbox_ipam._find_ip_id", return_value=42)
    def test_delete_exception(self, mock_find, mock_delete):
        assert netbox_delete_ip("10.1.55.50") is False

    def test_delete_no_token(self, monkeypatch):
        monkeypatch.setattr("helpers.netbox_ipam.NETBOX_API_TOKEN", "")
        assert netbox_delete_ip("10.1.55.50") is True  # non-fatal skip
