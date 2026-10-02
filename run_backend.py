"""Start the v3 API with ``backend/.venv`` (Windows-safe, including ``--reload``).

The root ``.venv`` has no FastAPI. Uvicorn's reloader spawns a second process, so the
child must use the same backend interpreter and ``PYTHONPATH`` (``backend/`` + repo root
for ``core/``). There is no module-level ``app`` object; the factory is ``create_app``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"


def venv_python() -> Path:
    """The Python that has FastAPI: ``backend/.venv``."""
    scripts = "Scripts" if os.name == "nt" else "bin"
    name = "python.exe" if os.name == "nt" else "python"
    path = BACKEND / ".venv" / scripts / name
    if not path.is_file():
        raise SystemExit(
            f"Missing {path}. Create it with:\n"
            "  py -3.12 -m venv backend\\.venv\n"
            "  backend\\.venv\\Scripts\\python.exe -m pip install -r backend\\requirements.txt"
        )
    return path.resolve()


def child_env() -> dict[str, str]:
    """Paths the reload child must inherit (Windows spawn does not keep sys.path)."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), str(ROOT)])
    return env


def uvicorn_command(python: Path, extra: list[str]) -> list[str]:
    """``python -m uvicorn app.main:create_app --factory`` with reload dirs."""
    reload = "--no-reload" not in extra
    args = [a for a in extra if a != "--no-reload"]
    cmd = [
        str(python),
        "-m",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--app-dir",
        str(BACKEND),
    ]
    if reload:
        cmd += ["--reload", "--reload-dir", str(BACKEND), "--reload-dir", str(ROOT / "core")]
    return cmd + args


def main(argv: list[str] | None = None) -> int:
    """Re-run Uvicorn under ``backend/.venv`` from the backend directory."""
    python = venv_python()
    cmd = uvicorn_command(python, list(argv if argv is not None else sys.argv[1:]))
    return subprocess.call(cmd, cwd=str(BACKEND), env=child_env())


if __name__ == "__main__":
    raise SystemExit(main())
