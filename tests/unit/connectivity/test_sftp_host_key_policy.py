"""Unit tests for SFTP host-key policy selection."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]


class _RejectPolicy:
    pass


class _AutoAddPolicy:
    pass


# Host unit tests mock paramiko (not always installed in the host venv).
_paramiko = types.ModuleType("paramiko")
_paramiko.RejectPolicy = _RejectPolicy  # type: ignore[attr-defined]
_paramiko.AutoAddPolicy = _AutoAddPolicy  # type: ignore[attr-defined]
_paramiko.SSHClient = MagicMock  # type: ignore[attr-defined]
sys.modules["paramiko"] = _paramiko
sys.modules.setdefault("asyncpg", MagicMock())
sys.modules.setdefault("pyarrow", MagicMock())
sys.modules.setdefault("pyarrow.csv", MagicMock())
sys.modules.setdefault("pyarrow.json", MagicMock())
sys.modules.setdefault("pyarrow.parquet", MagicMock())
sys.modules.setdefault("pyarrow.lib", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.sftp_source_registry import _configure_host_key_policy  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_sftp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOLON_SFTP_KNOWN_HOSTS", raising=False)
    monkeypatch.delenv("HOLON_SFTP_INSECURE_AUTO_ADD_HOSTKEY", raising=False)
    monkeypatch.delenv("HOLON_ENV", raising=False)


def test_host_key_rejects_by_default() -> None:
    client = MagicMock()
    _configure_host_key_policy(client)
    policy = client.set_missing_host_key_policy.call_args[0][0]
    assert isinstance(policy, _RejectPolicy)


def test_host_key_auto_add_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLON_SFTP_INSECURE_AUTO_ADD_HOSTKEY", "1")
    client = MagicMock()
    _configure_host_key_policy(client)
    policy = client.set_missing_host_key_policy.call_args[0][0]
    assert isinstance(policy, _AutoAddPolicy)


def test_host_key_production_ignores_auto_add(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLON_ENV", "production")
    monkeypatch.setenv("HOLON_SFTP_INSECURE_AUTO_ADD_HOSTKEY", "1")
    client = MagicMock()
    _configure_host_key_policy(client)
    policy = client.set_missing_host_key_policy.call_args[0][0]
    assert isinstance(policy, _RejectPolicy)


def test_host_key_known_hosts_loads_and_rejects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    known = tmp_path / "known_hosts"
    known.write_text("")
    monkeypatch.setenv("HOLON_SFTP_KNOWN_HOSTS", str(known))
    monkeypatch.setenv("HOLON_SFTP_INSECURE_AUTO_ADD_HOSTKEY", "1")
    client = MagicMock()
    _configure_host_key_policy(client)
    client.load_system_host_keys.assert_called_once()
    client.load_host_keys.assert_called_once_with(str(known))
    policy = client.set_missing_host_key_policy.call_args[0][0]
    assert isinstance(policy, _RejectPolicy)
