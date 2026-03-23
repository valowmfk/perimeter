"""Tests for sops_env._parse_env — pure KEY=VALUE parser."""

from utils.sops_env import _parse_env


class TestParseEnv:
    def test_basic_key_value(self):
        assert _parse_env("FOO=bar") == {"FOO": "bar"}

    def test_multiple_lines(self):
        text = "A=1\nB=2\nC=3"
        assert _parse_env(text) == {"A": "1", "B": "2", "C": "3"}

    def test_strips_double_quotes(self):
        assert _parse_env('KEY="value"') == {"KEY": "value"}

    def test_strips_single_quotes(self):
        assert _parse_env("KEY='value'") == {"KEY": "value"}

    def test_skips_comments(self):
        text = "# comment\nKEY=val\n# another"
        assert _parse_env(text) == {"KEY": "val"}

    def test_skips_blank_lines(self):
        text = "\n\nKEY=val\n\n"
        assert _parse_env(text) == {"KEY": "val"}

    def test_skips_lines_without_equals(self):
        text = "no_equals_here\nKEY=val"
        assert _parse_env(text) == {"KEY": "val"}

    def test_value_with_equals_sign(self):
        assert _parse_env("URL=http://host:8080/path?a=1") == {
            "URL": "http://host:8080/path?a=1"
        }

    def test_empty_value(self):
        assert _parse_env("KEY=") == {"KEY": ""}

    def test_whitespace_around_key_value(self):
        assert _parse_env("  KEY  =  value  ") == {"KEY": "value"}

    def test_empty_input(self):
        assert _parse_env("") == {}

    def test_only_comments(self):
        assert _parse_env("# just a comment\n# another") == {}

    def test_empty_key_skipped(self):
        assert _parse_env("=value") == {}

    def test_mixed_real_world(self):
        text = """# Q Branch secrets
PIHOLE_API_URL=http://10.1.55.9
PIHOLE_API_PASSWORD="s3cret"
VTH_ADMIN_PASS='hunter2'

# SSH config
LINUX_SSH_KEY_1="ssh-ed25519 AAAA..."
"""
        result = _parse_env(text)
        assert result["PIHOLE_API_URL"] == "http://10.1.55.9"
        assert result["PIHOLE_API_PASSWORD"] == "s3cret"
        assert result["VTH_ADMIN_PASS"] == "hunter2"
        assert result["LINUX_SSH_KEY_1"] == "ssh-ed25519 AAAA..."
        assert len(result) == 4
