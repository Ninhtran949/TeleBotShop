"""Install optional E2E dependencies, permitting only the reviewed pyaes source build."""

import os
import shutil
import subprocess
import tomllib
from pathlib import Path

PYAES_HASH = "sha256:02c1b1405c38d3c370b085fb952dd8bea3fadcee6411ad99f312cc129c536d8f"


def sync_arguments(lock: dict) -> list[str]:
    pyaes = next((p for p in lock["package"] if p["name"] == "pyaes"), {})
    if (
        pyaes.get("version") != "1.6.1"
        or pyaes.get("source") != {"registry": "https://pypi.org/simple"}
        or pyaes.get("sdist", {}).get("hash") != PYAES_HASH
    ):
        raise ValueError("pyaes source changed; review its build script/hash before installing")
    args = ["sync", "--locked", "--group", "telegram-e2e"]
    for name in sorted({p["name"] for p in lock["package"]} - {"pyaes", "telebotshop"}):
        args.extend(("--no-build-package", name))
    return args


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    local = root / ".tools/uv/Scripts/uv.exe"
    executable = str(local) if local.is_file() else shutil.which("uv")
    if executable is None:
        raise SystemExit("Install the pinned uv toolchain from README first")
    args = sync_arguments(tomllib.loads((root / "uv.lock").read_text("utf-8")))
    env = {**os.environ, "UV_CACHE_DIR": str(root / ".cache/uv")}
    return subprocess.run([executable, *args], cwd=root, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
