"""
The config file holds the daemon API key plus the Plex token and the
Jackett/Jellyfin keys, and certs/privkey.pem is the TLS private key. They used
to be created with the default umask (world-readable), so any other local user
on a shared box could read them. These tests pin the owner-only (0600) modes.

POSIX only: Windows file modes don't work this way.
"""
import os
import stat

import pytest

import certs
import config

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_restrict_to_owner_makes_file_private(tmp_path):
    p = tmp_path / "secret.json"
    p.write_text("{}")
    os.chmod(p, 0o644)
    config.restrict_to_owner(str(p))
    assert _mode(p) == 0o600


def test_restrict_to_owner_ignores_missing_file(tmp_path):
    config.restrict_to_owner(str(tmp_path / "does-not-exist"))  # must not raise


def test_save_config_writes_private_file(tmp_path, monkeypatch):
    cfg = tmp_path / "hydra_config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", str(cfg))
    config.save_config("daemon_api_key", "not-a-real-key")
    assert cfg.exists()
    assert _mode(cfg) == 0o600


def test_generated_tls_private_key_is_private(tmp_path, monkeypatch):
    monkeypatch.setattr(certs, "CERTS_DIR", str(tmp_path))
    monkeypatch.setattr(certs, "PRIVKEY_PATH", str(tmp_path / "privkey.pem"))
    monkeypatch.setattr(certs, "FULLCHAIN_PATH", str(tmp_path / "fullchain.pem"))
    monkeypatch.setattr(certs, "CHAIN_PATH", str(tmp_path / "chain.pem"))
    certs.ensure_certificates()
    assert _mode(tmp_path / "privkey.pem") == 0o600


def test_existing_world_readable_private_key_is_tightened(tmp_path, monkeypatch):
    monkeypatch.setattr(certs, "CERTS_DIR", str(tmp_path))
    key = tmp_path / "privkey.pem"
    chain = tmp_path / "fullchain.pem"
    key.write_text("x")
    chain.write_text("x")
    os.chmod(key, 0o644)
    monkeypatch.setattr(certs, "PRIVKEY_PATH", str(key))
    monkeypatch.setattr(certs, "FULLCHAIN_PATH", str(chain))
    certs.ensure_certificates()  # early-return path for an existing cert
    assert _mode(key) == 0o600
