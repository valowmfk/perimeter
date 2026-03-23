"""Tests for python/helpers/dns_manager.py — Pi-hole v6 DNS management."""

from unittest.mock import patch, MagicMock

import pytest

from helpers.dns_manager import (
    _pihole_auth,
    _pihole_logout,
    _pihole_add_host,
    _pihole_remove_host,
    _pihole_find_record_by_fqdn,
    _pihole_add_cname,
    _pihole_remove_cname,
    _pihole_find_cname_by_alias,
    dns_add_record,
    dns_remove_record,
    dns_add_cname,
    dns_remove_cname,
)


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Set required env vars for all tests."""
    monkeypatch.setattr("helpers.dns_manager.PIHOLE_API_URL", "http://pihole-test")
    monkeypatch.setattr("helpers.dns_manager.PIHOLE_API_PASSWORD", "testpass")
    monkeypatch.setattr("helpers.dns_manager.SSH_KEY", "/tmp/fake-key")


# ──────────────────────────────
# Auth / logout
# ──────────────────────────────

class TestPiholeAuth:
    @patch("helpers.dns_manager.requests.post")
    def test_auth_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"session": {"sid": "abc123", "csrf": "xyz789"}},
        )
        result = _pihole_auth()
        assert result == {"sid": "abc123", "csrf": "xyz789"}

    @patch("helpers.dns_manager.requests.post")
    def test_auth_no_session(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"session": {}},
        )
        assert _pihole_auth() is None

    @patch("helpers.dns_manager.requests.post", side_effect=Exception("timeout"))
    def test_auth_exception(self, mock_post):
        assert _pihole_auth() is None

    @patch("helpers.dns_manager.requests.delete")
    def test_logout(self, mock_delete):
        _pihole_logout({"sid": "abc", "csrf": "xyz"})
        mock_delete.assert_called_once()

    @patch("helpers.dns_manager.requests.delete", side_effect=Exception("err"))
    def test_logout_ignores_errors(self, mock_delete):
        _pihole_logout({"sid": "abc", "csrf": "xyz"})  # should not raise


# ──────────────────────────────
# Host record add / remove
# ──────────────────────────────

class TestPiholeAddHost:
    @patch("helpers.dns_manager._pihole_logout")
    @patch("helpers.dns_manager.requests.put")
    @patch("helpers.dns_manager._pihole_auth", return_value={"sid": "s", "csrf": "c"})
    def test_add_success(self, mock_auth, mock_put, mock_logout):
        mock_put.return_value = MagicMock(status_code=201)
        assert _pihole_add_host("10.1.55.10", "test.home.klouda.co") is True

    @patch("helpers.dns_manager._pihole_logout")
    @patch("helpers.dns_manager.requests.put")
    @patch("helpers.dns_manager._pihole_auth", return_value={"sid": "s", "csrf": "c"})
    def test_add_already_exists(self, mock_auth, mock_put, mock_logout):
        resp = MagicMock(status_code=400, content=b'{"error":{}}')
        resp.json.return_value = {
            "error": {"message": "Entry already present", "hint": ""}
        }
        mock_put.return_value = resp
        assert _pihole_add_host("10.1.55.10", "test.home.klouda.co") is True

    @patch("helpers.dns_manager._pihole_auth", return_value=None)
    def test_add_auth_failure(self, mock_auth):
        assert _pihole_add_host("10.1.55.10", "test.home.klouda.co") is False

    def test_add_no_password(self, monkeypatch):
        monkeypatch.setattr("helpers.dns_manager.PIHOLE_API_PASSWORD", "")
        assert _pihole_add_host("10.1.55.10", "test.home.klouda.co") is False


class TestPiholeRemoveHost:
    @patch("helpers.dns_manager._pihole_logout")
    @patch("helpers.dns_manager.requests.delete")
    @patch("helpers.dns_manager._pihole_auth", return_value={"sid": "s", "csrf": "c"})
    def test_remove_success(self, mock_auth, mock_delete, mock_logout):
        mock_delete.return_value = MagicMock(status_code=200)
        assert _pihole_remove_host("10.1.55.10", "test.home.klouda.co") is True

    @patch("helpers.dns_manager._pihole_logout")
    @patch("helpers.dns_manager.requests.delete")
    @patch("helpers.dns_manager._pihole_auth", return_value={"sid": "s", "csrf": "c"})
    def test_remove_not_found(self, mock_auth, mock_delete, mock_logout):
        mock_delete.return_value = MagicMock(status_code=404)
        assert _pihole_remove_host("10.1.55.10", "test.home.klouda.co") is True

    @patch("helpers.dns_manager._pihole_auth", return_value=None)
    def test_remove_auth_failure(self, mock_auth):
        assert _pihole_remove_host("10.1.55.10", "test.home.klouda.co") is False


# ──────────────────────────────
# Host record lookup
# ──────────────────────────────

class TestPiholeFindRecord:
    @patch("helpers.dns_manager._pihole_logout")
    @patch("helpers.dns_manager.requests.get")
    @patch("helpers.dns_manager._pihole_auth", return_value={"sid": "s", "csrf": "c"})
    def test_find_matching(self, mock_auth, mock_get, mock_logout):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "config": {"dns": {"hosts": ["10.1.55.10 web.home.klouda.co", "10.1.55.20 db.home.klouda.co"]}}
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()
        result = _pihole_find_record_by_fqdn("web.home.klouda.co")
        assert result == [("10.1.55.10", "web.home.klouda.co")]

    @patch("helpers.dns_manager._pihole_logout")
    @patch("helpers.dns_manager.requests.get")
    @patch("helpers.dns_manager._pihole_auth", return_value={"sid": "s", "csrf": "c"})
    def test_find_no_match(self, mock_auth, mock_get, mock_logout):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"config": {"dns": {"hosts": []}}},
        )
        mock_get.return_value.raise_for_status = MagicMock()
        assert _pihole_find_record_by_fqdn("missing.home.klouda.co") == []

    @patch("helpers.dns_manager._pihole_auth", return_value=None)
    def test_find_auth_failure(self, mock_auth):
        assert _pihole_find_record_by_fqdn("test.home.klouda.co") == []


# ──────────────────────────────
# CNAME add / remove
# ──────────────────────────────

class TestPiholeCname:
    @patch("helpers.dns_manager._pihole_logout")
    @patch("helpers.dns_manager.requests.put")
    @patch("helpers.dns_manager._pihole_auth", return_value={"sid": "s", "csrf": "c"})
    def test_add_cname_success(self, mock_auth, mock_put, mock_logout):
        mock_put.return_value = MagicMock(status_code=201)
        assert _pihole_add_cname("alias.co", "target.co") is True

    @patch("helpers.dns_manager._pihole_logout")
    @patch("helpers.dns_manager.requests.delete")
    @patch("helpers.dns_manager._pihole_auth", return_value={"sid": "s", "csrf": "c"})
    def test_remove_cname_success(self, mock_auth, mock_delete, mock_logout):
        mock_delete.return_value = MagicMock(status_code=200)
        assert _pihole_remove_cname("alias.co", "target.co") is True

    @patch("helpers.dns_manager._pihole_logout")
    @patch("helpers.dns_manager.requests.delete")
    @patch("helpers.dns_manager._pihole_auth", return_value={"sid": "s", "csrf": "c"})
    def test_remove_cname_not_found(self, mock_auth, mock_delete, mock_logout):
        mock_delete.return_value = MagicMock(status_code=404)
        assert _pihole_remove_cname("alias.co", "target.co") is True


# ──────────────────────────────
# Public API: dns_add_record / dns_remove_record
# ──────────────────────────────

class TestDnsAddRecord:
    @patch("helpers.dns_manager._trigger_nebula_sync", return_value=True)
    @patch("helpers.dns_manager._pihole_add_host", return_value=True)
    @patch("helpers.dns_manager._pihole_remove_host", return_value=True)
    @patch("helpers.dns_manager._pihole_find_record_by_fqdn", return_value=[])
    def test_add_new_record(self, mock_find, mock_remove, mock_add, mock_sync):
        assert dns_add_record("10.1.55.50", "web") is True
        mock_add.assert_called_once_with("10.1.55.50", "web.home.klouda.co")
        mock_sync.assert_called_once()

    @patch("helpers.dns_manager._trigger_nebula_sync", return_value=True)
    @patch("helpers.dns_manager._pihole_add_host", return_value=True)
    @patch("helpers.dns_manager._pihole_remove_host", return_value=True)
    @patch("helpers.dns_manager._pihole_find_record_by_fqdn")
    def test_add_replaces_stale(self, mock_find, mock_remove, mock_add, mock_sync):
        mock_find.return_value = [("10.1.55.99", "web.home.klouda.co")]
        assert dns_add_record("10.1.55.50", "web") is True
        mock_remove.assert_called_once_with("10.1.55.99", "web.home.klouda.co")

    @patch("helpers.dns_manager._trigger_nebula_sync")
    @patch("helpers.dns_manager._pihole_add_host", return_value=True)
    @patch("helpers.dns_manager._pihole_find_record_by_fqdn", return_value=[])
    def test_strips_domain_suffix(self, mock_find, mock_add, mock_sync):
        dns_add_record("10.1.55.50", "web.home.klouda.co")
        mock_add.assert_called_once_with("10.1.55.50", "web.home.klouda.co")

    @patch("helpers.dns_manager._pihole_add_host", return_value=False)
    @patch("helpers.dns_manager._pihole_find_record_by_fqdn", return_value=[])
    def test_add_failure(self, mock_find, mock_add):
        assert dns_add_record("10.1.55.50", "web") is False


class TestDnsRemoveRecord:
    @patch("helpers.dns_manager._trigger_nebula_sync", return_value=True)
    @patch("helpers.dns_manager._pihole_remove_host", return_value=True)
    def test_remove_success(self, mock_remove, mock_sync):
        assert dns_remove_record("10.1.55.50", "web") is True
        mock_remove.assert_called_once_with("10.1.55.50", "web.home.klouda.co")
        mock_sync.assert_called_once()

    @patch("helpers.dns_manager._pihole_remove_host", return_value=False)
    def test_remove_failure(self, mock_remove):
        assert dns_remove_record("10.1.55.50", "web") is False


class TestDnsCnamePublic:
    @patch("helpers.dns_manager._trigger_nebula_sync", return_value=True)
    @patch("helpers.dns_manager._pihole_add_cname", return_value=True)
    @patch("helpers.dns_manager._pihole_find_cname_by_alias", return_value=[])
    def test_add_cname(self, mock_find, mock_add, mock_sync):
        assert dns_add_cname("alias.co", "target.co") is True

    @patch("helpers.dns_manager._trigger_nebula_sync", return_value=True)
    @patch("helpers.dns_manager._pihole_remove_cname", return_value=True)
    def test_remove_cname(self, mock_remove, mock_sync):
        assert dns_remove_cname("alias.co", "target.co") is True
