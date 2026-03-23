"""Tests for python/utils/vm_track.py — VM cleanup state tracking."""

import json
from unittest.mock import patch

from utils.vm_track import (
    mark_needs_cleanup,
    clear_cleanup_status,
    remove_vm_tracking,
    VM_TRACK_FILE,
    _load,
    _save,
)


class TestVmTrack:
    """Test vm_track against a temp file to avoid touching production state."""

    def test_load_missing_file(self, tmp_path, monkeypatch):
        fake = tmp_path / "vm_track.json"
        monkeypatch.setattr("utils.vm_track.VM_TRACK_FILE", fake)
        assert _load() == {}

    def test_load_valid_file(self, tmp_path, monkeypatch):
        fake = tmp_path / "vm_track.json"
        fake.write_text('{"100": {"first_seen": 1}}')
        monkeypatch.setattr("utils.vm_track.VM_TRACK_FILE", fake)
        assert _load() == {"100": {"first_seen": 1}}

    def test_load_corrupt_file(self, tmp_path, monkeypatch):
        fake = tmp_path / "vm_track.json"
        fake.write_text("not json")
        monkeypatch.setattr("utils.vm_track.VM_TRACK_FILE", fake)
        assert _load() == {}

    def test_save_and_load(self, tmp_path, monkeypatch):
        fake = tmp_path / "vm_track.json"
        monkeypatch.setattr("utils.vm_track.VM_TRACK_FILE", fake)
        _save({"200": {"first_seen": 99}})
        assert _load() == {"200": {"first_seen": 99}}

    def test_mark_needs_cleanup(self, tmp_path, monkeypatch):
        fake = tmp_path / "vm_track.json"
        monkeypatch.setattr("utils.vm_track.VM_TRACK_FILE", fake)

        mark_needs_cleanup(300, "Bootstrap failed", hostname="qtest01", ip="10.1.55.50", vm_type="linux")

        data = json.loads(fake.read_text())
        entry = data["300"]
        assert entry["status"] == "needs_cleanup"
        assert entry["cleanup_reason"] == "Bootstrap failed"
        assert entry["hostname"] == "qtest01"
        assert entry["ip"] == "10.1.55.50"
        assert entry["vm_type"] == "linux"
        assert "first_seen" in entry
        assert "last_seen" in entry

    def test_mark_preserves_existing_entries(self, tmp_path, monkeypatch):
        fake = tmp_path / "vm_track.json"
        fake.write_text('{"100": {"first_seen": 1, "protected": true}}')
        monkeypatch.setattr("utils.vm_track.VM_TRACK_FILE", fake)

        mark_needs_cleanup(200, "DNS failed")

        data = json.loads(fake.read_text())
        assert "100" in data
        assert data["100"]["protected"] is True
        assert "200" in data

    def test_clear_cleanup_status(self, tmp_path, monkeypatch):
        fake = tmp_path / "vm_track.json"
        monkeypatch.setattr("utils.vm_track.VM_TRACK_FILE", fake)

        mark_needs_cleanup(300, "Test reason", hostname="h", ip="1.2.3.4", vm_type="linux")
        clear_cleanup_status(300)

        data = json.loads(fake.read_text())
        entry = data["300"]
        assert "status" not in entry
        assert "cleanup_reason" not in entry
        assert "hostname" not in entry

    def test_remove_vm_tracking(self, tmp_path, monkeypatch):
        fake = tmp_path / "vm_track.json"
        fake.write_text('{"300": {"first_seen": 1}, "400": {"first_seen": 2}}')
        monkeypatch.setattr("utils.vm_track.VM_TRACK_FILE", fake)

        remove_vm_tracking(300)

        data = json.loads(fake.read_text())
        assert "300" not in data
        assert "400" in data

    def test_remove_nonexistent_vmid(self, tmp_path, monkeypatch):
        fake = tmp_path / "vm_track.json"
        fake.write_text('{"100": {"first_seen": 1}}')
        monkeypatch.setattr("utils.vm_track.VM_TRACK_FILE", fake)

        remove_vm_tracking(999)  # should not raise

        data = json.loads(fake.read_text())
        assert "100" in data
