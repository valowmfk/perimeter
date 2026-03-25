"""Tests for python/workflows/destroy_vm.py — VM destruction orchestration."""

import json
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest

from workflows.destroy_vm import (
    remove_ssh_host_keys,
    destroy_vm,
)
from utils.vm_types import find_vm_in_tfvars


@pytest.fixture
def linux_tfvars(tmp_path):
    """Create a fake Linux tfvars file."""
    data = {
        "vm_configs": {
            "web-01": {"vm_id": 8001, "ipv4_address": "10.1.55.50/24"},
            "db-01": {"vm_id": 8002, "ipv4_address": "10.1.55.51/24"},
        }
    }
    p = tmp_path / "linux.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def vthunder_tfvars(tmp_path):
    """Create a fake vThunder tfvars file."""
    data = {
        "vthunder_configs": {
            "vth-01": {"vm_id": 9001, "ipv4_address": "10.1.55.60/24"},
        }
    }
    p = tmp_path / "vthunder.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def empty_tfvars(tmp_path):
    """Create empty tfvars files."""
    linux = tmp_path / "linux.json"
    linux.write_text(json.dumps({"vm_configs": {}}))
    vthunder = tmp_path / "vthunder.json"
    vthunder.write_text(json.dumps({"vthunder_configs": {}}))
    return linux, vthunder


# ──────────────────────────────
# find_vm_in_tfvars
# ──────────────────────────────

class TestFindVmInTfvars:
    def _mock_registry(self, monkeypatch, linux_path, vthunder_path, vyos_path=None):
        """Patch the VM type registry to use test-specific tfvars paths."""
        from utils.vm_types import VMTypeInfo
        fake_registry = [
            VMTypeInfo("linux", "vm_configs", "linux_vm", linux_path, linux_path.parent),
            VMTypeInfo("vthunder", "vthunder_configs", "vthunder_vm", vthunder_path, vthunder_path.parent),
        ]
        if vyos_path:
            fake_registry.append(VMTypeInfo("vyos", "vyos_configs", "vyos_vm", vyos_path, vyos_path.parent))
        monkeypatch.setattr("utils.vm_types.all_vm_types", lambda: fake_registry)

    def test_find_linux(self, linux_tfvars, vthunder_tfvars, monkeypatch):
        self._mock_registry(monkeypatch, linux_tfvars, vthunder_tfvars)
        result = find_vm_in_tfvars(8001)
        assert result == ("linux", "web-01", "10.1.55.50")

    def test_find_vthunder(self, linux_tfvars, vthunder_tfvars, monkeypatch):
        self._mock_registry(monkeypatch, linux_tfvars, vthunder_tfvars)
        result = find_vm_in_tfvars(9001)
        assert result == ("vthunder", "vth-01", "10.1.55.60")

    def test_not_found(self, empty_tfvars, monkeypatch):
        linux, vthunder = empty_tfvars
        self._mock_registry(monkeypatch, linux, vthunder)
        result = find_vm_in_tfvars(9999)
        assert result is None

    def test_ip_without_cidr(self, tmp_path, monkeypatch):
        data = {"vm_configs": {"host": {"vm_id": 100, "ipv4_address": "10.1.55.5"}}}
        linux = tmp_path / "linux.json"
        linux.write_text(json.dumps(data))
        vthunder = tmp_path / "vthunder.json"
        vthunder.write_text(json.dumps({"vthunder_configs": {}}))
        self._mock_registry(monkeypatch, linux, vthunder)
        result = find_vm_in_tfvars(100)
        assert result[2] == "10.1.55.5"


# ──────────────────────────────
# remove_ssh_host_keys
# ──────────────────────────────

class TestRemoveSshHostKeys:
    @patch("workflows.destroy_vm.subprocess.run")
    def test_calls_ssh_keygen(self, mock_run, tmp_path, monkeypatch):
        known = tmp_path / "known_hosts"
        known.write_text("some host key\n")
        monkeypatch.setattr("workflows.destroy_vm.KNOWN_HOSTS", known)
        remove_ssh_host_keys("web-01", "10.1.55.50")
        assert mock_run.call_count == 2  # once for hostname, once for IP

    def test_missing_known_hosts(self, tmp_path, monkeypatch):
        monkeypatch.setattr("workflows.destroy_vm.KNOWN_HOSTS", tmp_path / "missing")
        remove_ssh_host_keys("web-01", "10.1.55.50")  # should not raise


# ──────────────────────────────
# destroy_vm (orchestration)
# ──────────────────────────────

class TestDestroyVm:
    @patch("workflows.destroy_vm.remove_ssh_host_keys")
    @patch("workflows.destroy_vm.remove_host_from_yaml", return_value="staging_linux")
    @patch("workflows.destroy_vm.locked_update")
    @patch("workflows.destroy_vm.run_terraform_destroy", return_value=0)
    @patch("workflows.destroy_vm.netbox_delete_ip", return_value=True)
    @patch("workflows.destroy_vm.dns_remove_record", return_value=True)
    @patch("workflows.destroy_vm.find_vm_by_id", return_value=("linux", "web-01", "10.1.55.50"))
    def test_destroy_linux(self, mock_find, mock_dns, mock_netbox, mock_tf,
                           mock_tfvars, mock_inv_yaml, mock_ssh):
        assert destroy_vm(8001) == 0
        mock_dns.assert_called_once_with("10.1.55.50", "web-01")
        mock_netbox.assert_called_once_with("10.1.55.50")
        mock_tf.assert_called_once_with("linux", "web-01")
        mock_inv_yaml.assert_called_once_with("web-01")
        mock_ssh.assert_called_once_with("web-01", "10.1.55.50")

    @patch("workflows.destroy_vm.remove_ssh_host_keys")
    @patch("workflows.destroy_vm.remove_host_from_yaml", return_value="staging_vthunder")
    @patch("workflows.destroy_vm.locked_update")
    @patch("workflows.destroy_vm.run_terraform_destroy", return_value=0)
    @patch("workflows.destroy_vm.netbox_delete_ip", return_value=True)
    @patch("workflows.destroy_vm.dns_remove_record", return_value=True)
    @patch("workflows.destroy_vm.find_vm_by_id", return_value=("vthunder", "vth-01", "10.1.55.60"))
    def test_destroy_vthunder(self, mock_find, mock_dns, mock_netbox,
                              mock_tf, mock_tfvars, mock_inv_yaml, mock_ssh):
        assert destroy_vm(9001) == 0
        mock_inv_yaml.assert_called_once_with("vth-01")

    @patch("workflows.destroy_vm.find_vm_by_id", return_value=None)
    def test_destroy_not_found(self, mock_find):
        assert destroy_vm(9999) == 1

    @patch("workflows.destroy_vm.run_terraform_destroy", return_value=1)
    @patch("workflows.destroy_vm.netbox_delete_ip", return_value=True)
    @patch("workflows.destroy_vm.dns_remove_record", return_value=True)
    @patch("workflows.destroy_vm.find_vm_by_id", return_value=("linux", "web-01", "10.1.55.50"))
    def test_destroy_tf_failure(self, mock_find, mock_dns, mock_netbox, mock_tf):
        assert destroy_vm(8001) == 1

    @patch("workflows.destroy_vm.remove_ssh_host_keys")
    @patch("workflows.destroy_vm.remove_host_from_yaml", return_value=None)
    @patch("workflows.destroy_vm.locked_update")
    @patch("workflows.destroy_vm.run_terraform_destroy", return_value=0)
    @patch("workflows.destroy_vm.netbox_delete_ip", return_value=False)
    @patch("workflows.destroy_vm.dns_remove_record", return_value=False)
    @patch("workflows.destroy_vm.find_vm_by_id", return_value=("linux", "web-01", "10.1.55.50"))
    def test_destroy_continues_on_dns_netbox_failure(self, mock_find, mock_dns,
                                                     mock_netbox, mock_tf,
                                                     mock_tfvars, mock_inv_yaml, mock_ssh):
        assert destroy_vm(8001) == 0  # DNS/Netbox failures are non-fatal
