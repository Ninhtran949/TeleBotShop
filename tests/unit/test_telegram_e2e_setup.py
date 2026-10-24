import copy
import tomllib
from pathlib import Path

import pytest

from scripts.setup_telegram_e2e import sync_arguments


def test_setup_allows_source_build_only_for_reviewed_pyaes_and_own_project():
    lock = tomllib.loads(Path("uv.lock").read_text("utf-8"))
    args = sync_arguments(lock)
    blocked = {args[i + 1] for i, value in enumerate(args) if value == "--no-build-package"}
    assert blocked == {p["name"] for p in lock["package"]} - {"pyaes", "telebotshop"}
    assert "--locked" in args
    assert args[args.index("--group") + 1] == "telegram-e2e"
    changed = copy.deepcopy(lock)
    next(p for p in changed["package"] if p["name"] == "pyaes")["sdist"]["hash"] = "sha256:BAD"
    with pytest.raises(ValueError, match="review"):
        sync_arguments(changed)
