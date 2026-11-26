"""An externally owned tree's gateway service runs the environment that owns it, never PM's store
Python found beside it (which holds no dependency generation of that tree)."""
import json
import shlex
import sys
from pathlib import Path

import pytest


def _exec(unit, key):
    line = next(line for line in unit.splitlines() if line.startswith(key + "="))
    return shlex.split(line.split("=", 1)[1].lstrip("-"))


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("profile", [None, "mymel"])
def test_external_tree_service_unit_execs_the_running_venv(tmp_path, monkeypatch, profile):
    from hermes_cli import gateway
    from hermes_cli.gateway_runtime_service_identity import verify_gateway_argv

    repo, root = tmp_path / "hermes-agent", tmp_path / ".hermes"
    repo.mkdir()
    (repo / "install-stamp.json").write_text(json.dumps({"updateMechanism": "external"}), encoding="utf-8")
    store_python = root / "tools" / "python-fixture" / "bin" / "python3"
    store_python.parent.mkdir(parents=True)
    store_python.symlink_to(Path(sys._base_executable).resolve())
    (root / "tools" / "facts.json").write_text(
        json.dumps({"packages": {"python": {"entry": "python-fixture"}}}), encoding="utf-8")
    home = root / "profiles" / profile if profile else root
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(gateway, "PROJECT_ROOT", repo)
    assert sys.prefix != sys.base_prefix  # the suite runs from a venv: that is the running environment

    gateway._prepare_service_launcher()
    unit = gateway.generate_systemd_unit()

    profile_args = ["--profile", profile] if profile else []
    assert _exec(unit, "ExecStart") == [sys.executable, "-m", "hermes_cli.main", *profile_args, "gateway", "run"]
    assert _exec(unit, "ExecStop") == [sys.executable, "-m", "gateway.systemd_stop_mark"]
    assert _exec(unit, "ExecStopPost") == [sys.executable, "-m", "gateway.cgroup_cleanup"]
    path = next(line for line in unit.splitlines() if line.startswith('Environment="PATH='))
    assert path.split("=", 2)[2].rstrip('"').split(":")[0] == str(Path(sys.prefix) / "bin")
    assert f'Environment="VIRTUAL_ENV={sys.prefix}"' in unit.splitlines()
    assert str(store_python.parent.parent) not in unit
    assert not (repo / ".hermes" / "bin").exists()
    verify_gateway_argv(_exec(unit, "ExecStart"), home)  # status/stop still recognise it as this home's
