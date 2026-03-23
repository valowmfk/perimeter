"""Tests for routes/shared.py validation functions.

These are imported directly to test the pure logic without Flask context.
"""

import sys
from pathlib import Path

import pytest

# shared.py imports `from config import cfg` which triggers SOPS decryption.
# Mock the config module before importing shared.
from unittest.mock import MagicMock
from types import SimpleNamespace

# Create a minimal mock config so shared.py can import without SOPS/Flask
_mock_cfg = SimpleNamespace(
    ROOT_DIR=Path("/tmp/fake"),
    SCRIPTS_DIR=Path("/tmp/fake/scripts"),
    LINUX_SCRIPT_DIR=Path("/tmp/fake/python/workflows"),
    VTHUNDER_SCRIPT_DIR=Path("/tmp/fake/python/workflows"),
    HELPERS_SCRIPT_DIR=Path("/tmp/fake/python/helpers"),
    PLAYBOOK_DIR=Path("/tmp/fake/playbooks"),
    PLAYBOOK_TEMPLATE_DIR=Path("/tmp/fake/playbook_templates"),
    INVENTORY_DIR=Path("/tmp/fake/inventories"),
    TERRAFORM_DIR=Path("/tmp/fake/terraform"),
    CERTIFICATE_DIR=Path("/tmp/fake/certificates"),
    TF_LINUX_STATE_PATH=Path("/tmp/fake/tf/linux.tfstate"),
    TF_VTHUNDER_STATE_PATH=Path("/tmp/fake/tf/vthunder.tfstate"),
    TF_LINUX_TFVARS_PATH=Path("/tmp/fake/tf/linux.tfvars.json"),
    TF_VTHUNDER_TFVARS_PATH=Path("/tmp/fake/tf/vthunder.tfvars.json"),
    VM_TRACK_FILE=Path("/tmp/fake/vm_track.json"),
    cert_domains={"home.klouda.co": "/tmp/fake/certs"},
    ALLOWED_CERT_FILES={"cert.pem", "chain.pem", "fullchain.pem"},
    PM_API_URL="https://fake:8006/api2/json",
    PM_API_TOKEN_ID="",
    PM_API_TOKEN_SECRET="",
    NETBOX_URL="https://fake",
    NETBOX_API_TOKEN="",
    NETBOX_CACHE_TTL=300,
    pm_headers=lambda: {},
    netbox_headers=lambda: {},
)

# Inject mock config before importing shared
_mock_config_module = MagicMock()
_mock_config_module.cfg = _mock_cfg
sys.modules["config"] = _mock_config_module

from routes.shared import _valid_hostname, validate_vm_params, is_safe_filename


class TestValidHostname:
    def test_simple_hostname(self):
        assert _valid_hostname("qrocky101") is True

    def test_fqdn(self):
        assert _valid_hostname("snmp.home.klouda.co") is True

    def test_hyphenated(self):
        assert _valid_hostname("my-server-01") is True

    def test_single_char(self):
        assert _valid_hostname("a") is True

    def test_empty_string(self):
        assert _valid_hostname("") is False

    def test_leading_hyphen(self):
        assert _valid_hostname("-badhost") is False

    def test_trailing_hyphen(self):
        assert _valid_hostname("badhost-") is False

    def test_uppercase_rejected(self):
        assert _valid_hostname("MyServer") is False

    def test_underscore_rejected(self):
        assert _valid_hostname("my_server") is False

    def test_too_long_label(self):
        assert _valid_hostname("a" * 64) is False

    def test_max_length_label(self):
        assert _valid_hostname("a" * 63) is True

    def test_total_length_over_253(self):
        # 4 labels of 63 chars + 3 dots = 255
        assert _valid_hostname(".".join(["a" * 63] * 4)) is False

    def test_numeric_only(self):
        assert _valid_hostname("123") is True

    def test_empty_label_in_fqdn(self):
        assert _valid_hostname("host..klouda.co") is False


class TestValidateVmParams:
    def _params(self, **overrides):
        base = {
            "hostname": "qtest01",
            "ip": "10.1.55.100",
            "vm_id": 200,
            "cpu": 2,
            "ram": 4096,
            "disk": 32,
        }
        base.update(overrides)
        return base

    def test_valid_params(self):
        assert validate_vm_params(self._params()) is None

    def test_invalid_hostname(self):
        err = validate_vm_params(self._params(hostname="BAD_HOST"))
        assert err is not None
        assert "hostname" in err.lower() or "Invalid" in err

    def test_missing_ip(self):
        err = validate_vm_params(self._params(ip=""))
        assert "required" in err.lower()

    def test_invalid_ip(self):
        err = validate_vm_params(self._params(ip="not.an.ip.address"))
        assert "Invalid" in err or "invalid" in err.lower()

    def test_ip_outside_subnet(self):
        err = validate_vm_params(self._params(ip="192.168.1.1"))
        assert "outside" in err.lower() or "subnet" in err.lower()

    def test_ip_at_subnet_boundary(self):
        assert validate_vm_params(self._params(ip="10.1.55.1")) is None
        assert validate_vm_params(self._params(ip="10.1.55.254")) is None

    def test_vmid_too_low(self):
        err = validate_vm_params(self._params(vm_id=50))
        assert "out of range" in err.lower()

    def test_vmid_too_high(self):
        err = validate_vm_params(self._params(vm_id=10000))
        assert "out of range" in err.lower()

    def test_vmid_boundaries(self):
        assert validate_vm_params(self._params(vm_id=100)) is None
        assert validate_vm_params(self._params(vm_id=9999)) is None

    def test_vmid_non_integer(self):
        err = validate_vm_params(self._params(vm_id="abc"))
        assert "integer" in err.lower()

    def test_cpu_out_of_range(self):
        assert validate_vm_params(self._params(cpu=0)) is not None
        assert validate_vm_params(self._params(cpu=129)) is not None

    def test_ram_out_of_range(self):
        assert validate_vm_params(self._params(ram=256)) is not None
        assert validate_vm_params(self._params(ram=300000)) is not None

    def test_disk_out_of_range(self):
        assert validate_vm_params(self._params(disk=5)) is not None
        assert validate_vm_params(self._params(disk=6000)) is not None

    def test_optional_fields_can_be_none(self):
        """vm_id, cpu, ram, disk are optional — None should pass."""
        params = {"hostname": "qtest01", "ip": "10.1.55.100"}
        assert validate_vm_params(params) is None


class TestIsSafeFilename:
    def test_normal_filename(self):
        assert is_safe_filename("playbook.yml") is True

    def test_with_hyphens_underscores(self):
        assert is_safe_filename("my-playbook_v2.yaml") is True

    def test_path_traversal(self):
        assert is_safe_filename("../etc/passwd") is False

    def test_absolute_path(self):
        assert is_safe_filename("/etc/passwd") is False

    def test_backslash(self):
        assert is_safe_filename("foo\\bar") is False

    def test_null_byte(self):
        assert is_safe_filename("file\0name") is False

    def test_empty(self):
        assert is_safe_filename("") is False

    def test_none(self):
        assert is_safe_filename(None) is False

    def test_spaces_rejected(self):
        assert is_safe_filename("my file.yml") is False

    def test_special_chars_rejected(self):
        assert is_safe_filename("file;rm -rf /") is False
