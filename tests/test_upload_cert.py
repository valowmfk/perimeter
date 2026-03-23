"""Tests for python/helpers/upload_cert_to_vthunder.py — SSL cert upload to ACOS."""

from unittest.mock import patch, MagicMock

import pytest

from helpers.upload_cert_to_vthunder import (
    authenticate,
    switch_partition,
    upload_certificate,
    upload_key,
    delete_certificate,
)


# ──────────────────────────────
# authenticate
# ──────────────────────────────

class TestAuthenticate:
    @patch("helpers.upload_cert_to_vthunder.requests.post")
    def test_auth_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"authresponse": {"signature": "tok123"}},
        )
        mock_post.return_value.raise_for_status = MagicMock()
        assert authenticate("10.1.55.1", 443, "admin", "pass") == "tok123"

    @patch("helpers.upload_cert_to_vthunder.requests.post")
    def test_auth_http_error(self, mock_post):
        mock_post.return_value.raise_for_status.side_effect = Exception("401")
        with pytest.raises(Exception):
            authenticate("10.1.55.1", 443, "admin", "wrong")


# ──────────────────────────────
# switch_partition
# ──────────────────────────────

class TestSwitchPartition:
    @patch("helpers.upload_cert_to_vthunder.requests.post")
    def test_switch_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()
        mock_post.return_value.json.return_value = {"active-partition": {}}
        result = switch_partition("10.1.55.1", 443, "tok", "shared")
        assert result == {"active-partition": {}}


# ──────────────────────────────
# upload_certificate
# ──────────────────────────────

class TestUploadCertificate:
    @patch("helpers.upload_cert_to_vthunder.requests.post")
    def test_upload_cert(self, mock_post, tmp_path):
        cert_file = tmp_path / "cert.pem"
        cert_file.write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n")

        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()
        mock_post.return_value.json.return_value = {"response": {"status": "OK"}}

        result = upload_certificate("10.1.55.1", 443, "tok", "mycert", str(cert_file))
        assert result == {"response": {"status": "OK"}}
        mock_post.assert_called_once()
        # Verify multipart upload includes cert name
        call_kwargs = mock_post.call_args
        assert "files" in call_kwargs[1]


# ──────────────────────────────
# upload_key
# ──────────────────────────────

class TestUploadKey:
    @patch("helpers.upload_cert_to_vthunder.requests.post")
    def test_upload_key(self, mock_post, tmp_path):
        key_file = tmp_path / "key.pem"
        key_file.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")

        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()
        mock_post.return_value.json.return_value = {"response": {"status": "OK"}}

        result = upload_key("10.1.55.1", 443, "tok", "mycert", str(key_file))
        assert result == {"response": {"status": "OK"}}


# ──────────────────────────────
# delete_certificate (cleanup)
# ──────────────────────────────

class TestDeleteCertificate:
    @patch("helpers.upload_cert_to_vthunder.requests.delete")
    def test_delete_success(self, mock_delete):
        mock_delete.return_value = MagicMock(status_code=200)
        mock_delete.return_value.raise_for_status = MagicMock()
        mock_delete.return_value.json.return_value = {"response": {"status": "OK"}}

        result = delete_certificate("10.1.55.1", 443, "tok", "mycert")
        assert result == {"response": {"status": "OK"}}
        mock_delete.assert_called_once()
        assert "mycert" in mock_delete.call_args[0][0]

    @patch("helpers.upload_cert_to_vthunder.requests.delete")
    def test_delete_http_error(self, mock_delete):
        mock_delete.return_value.raise_for_status.side_effect = Exception("404")
        with pytest.raises(Exception):
            delete_certificate("10.1.55.1", 443, "tok", "missing")
