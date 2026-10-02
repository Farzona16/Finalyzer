"""The Windows run helper points at backend/.venv, not the root venv."""

from __future__ import annotations

import os
from pathlib import Path

import run_backend


def test_venv_python_is_the_backend_interpreter() -> None:
    path = run_backend.venv_python()
    assert path.is_file()
    assert path.parts[-3:] == (".venv", "Scripts", "python.exe") or path.parts[-3:] == (
        ".venv",
        "bin",
        "python",
    )
    assert "backend" in path.parts


def test_uvicorn_command_uses_the_factory_and_app_dir() -> None:
    cmd = run_backend.uvicorn_command(Path("python"), [])
    assert "app.main:create_app" in cmd
    assert "--factory" in cmd
    assert "--reload" in cmd
    assert str(run_backend.BACKEND) in cmd


def test_reload_child_env_puts_backend_and_core_on_the_path() -> None:
    env = run_backend.child_env()
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert str(run_backend.BACKEND) in parts
    assert str(run_backend.ROOT) in parts


def test_no_reload_flag_drops_reload() -> None:
    cmd = run_backend.uvicorn_command(Path("python"), ["--no-reload"])
    assert "--reload" not in cmd
