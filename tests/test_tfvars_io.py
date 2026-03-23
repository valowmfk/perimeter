"""Tests for python/utils/tfvars_io.py — atomic tfvars read/write with locking."""

import json

from utils.tfvars_io import read_tfvars, _atomic_write, locked_update


class TestReadTfvars:
    def test_missing_file_returns_skeleton(self, tmp_path):
        p = tmp_path / "missing.json"
        result = read_tfvars(p)
        assert result == {"vm_configs": {}, "vthunder_configs": {}}

    def test_reads_existing_file(self, tmp_path):
        p = tmp_path / "data.json"
        data = {"vm_configs": {"host1": {"cpu": 2}}}
        p.write_text(json.dumps(data))
        assert read_tfvars(p) == data


class TestAtomicWrite:
    def test_writes_valid_json(self, tmp_path):
        p = tmp_path / "out.json"
        data = {"key": "value"}
        _atomic_write(p, data)
        assert json.loads(p.read_text()) == data

    def test_trailing_newline(self, tmp_path):
        p = tmp_path / "out.json"
        _atomic_write(p, {"a": 1})
        assert p.read_text().endswith("\n")

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "sub" / "dir" / "out.json"
        _atomic_write(p, {"nested": True})
        assert json.loads(p.read_text()) == {"nested": True}


class TestLockedUpdate:
    def test_creates_file_if_missing(self, tmp_path):
        p = tmp_path / "tfvars.json"

        def add_vm(data):
            data["vm_configs"]["newhost"] = {"cpu": 4}

        locked_update(p, add_vm)

        result = json.loads(p.read_text())
        assert result["vm_configs"]["newhost"]["cpu"] == 4

    def test_preserves_existing_data(self, tmp_path):
        p = tmp_path / "tfvars.json"
        p.write_text(json.dumps({"vm_configs": {"old": {"cpu": 1}}, "vthunder_configs": {}}))

        def add_vm(data):
            data["vm_configs"]["new"] = {"cpu": 2}

        locked_update(p, add_vm)

        result = json.loads(p.read_text())
        assert "old" in result["vm_configs"]
        assert "new" in result["vm_configs"]

    def test_returns_updater_result(self, tmp_path):
        p = tmp_path / "tfvars.json"

        def updater(data):
            data["vm_configs"]["h"] = {}
            return "done"

        result = locked_update(p, updater)
        assert result == "done"

    def test_lock_file_created(self, tmp_path):
        p = tmp_path / "tfvars.json"
        locked_update(p, lambda d: None)
        assert (tmp_path / "tfvars.json.lock").exists()
