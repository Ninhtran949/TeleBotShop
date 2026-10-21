import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_architecture import check_package, check_source


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("telebotshop.domain.order", "from dataclasses import dataclass"),
        ("telebotshop.domain.order", "from .errors import InvalidOrder"),
        ("telebotshop.application.services.checkout", "from ...domain import order"),
        ("telebotshop.application.services.checkout", "from ..ports import Stock"),
        ("telebotshop.bot.routers.start", "from aiogram import Router"),
        ("telebotshop.admin.orders", "from telebotshop.application.services import refund"),
        ("telebotshop.api.webhook", "from fastapi import FastAPI"),
        ("telebotshop.api.webhook", "from pydantic import BaseModel"),
        ("telebotshop.workers.delivery", "from telebotshop.application.services import send"),
        (
            "telebotshop.infrastructure.database.uow",
            "from sqlalchemy.ext.asyncio import AsyncSession",
        ),
        ("telebotshop.infrastructure.database.uow", "from ...application.ports import UnitOfWork"),
        ("telebotshop.infrastructure.database.uow", "from telebotshop.application import ports"),
        ("telebotshop.bootstrap", "from telebotshop.infrastructure.database import uow"),
        ("telebotshop.__main__", "from .bootstrap import main"),
    ],
)
def test_allowed_imports(module: str, source: str) -> None:
    assert check_source(module, source) == []


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("telebotshop.domain.order", "import sqlalchemy as sa"),
        ("telebotshop.domain.order", "from pydantic import BaseModel"),
        ("telebotshop.domain.order", "from telebotshop.application.ports import Stock"),
        ("telebotshop.domain.order", "from .. import infrastructure"),
        ("telebotshop.domain.order", "from telebotshop import infrastructure as infra"),
        ("telebotshop.application.services.checkout", "import aiogram"),
        ("telebotshop.application.services.checkout", "from sqlalchemy import select"),
        ("telebotshop.application.services.checkout", "from ...infrastructure import database"),
        ("telebotshop.application.services.checkout", "from ...bot.routers import checkout"),
        ("telebotshop.bot.routers.start", "from ...infrastructure.database import models"),
        ("telebotshop.bot.routers.start", "import sqlalchemy"),
        ("telebotshop.bot.routers.start", "from telebotshop.admin import orders"),
        ("telebotshop.admin.orders", "from telebotshop.bot import routers"),
        ("telebotshop.api.webhook", "from telebotshop.bot import routers"),
        ("telebotshop.api.webhook", "import aiogram"),
        ("telebotshop.workers.delivery", "from telebotshop.api import webhook"),
        ("telebotshop.infrastructure.database.uow", "from ...application.services import checkout"),
        ("telebotshop.infrastructure.database.uow", "from telebotshop.application import services"),
        ("telebotshop.infrastructure.database.uow", "from telebotshop.bot import routers"),
        ("telebotshop.domain.order", "from telebotshop.bootstrap import build"),
        ("telebotshop.domain.order", "import telebotshop"),
        ("telebotshop", "from telebotshop.infrastructure import config"),
        ("telebotshop.application.services.checkout", "from ....outside import service"),
        ("telebotshop.application.checkout", "if True:\n    import asyncpg"),
        ("telebotshop.application.checkout", "import uuid, asyncpg"),
    ],
)
def test_forbidden_imports(module: str, source: str) -> None:
    errors = check_source(module, source)
    assert len(errors) == 1
    assert module in errors[0]


def test_package_relative_import_is_resolved_from_package() -> None:
    assert (
        check_source("telebotshop.application", "from .ports import Stock", is_package=True) == []
    )
    assert check_source("telebotshop.domain", "from .. import infrastructure", is_package=True)


def test_package_check_reports_file_and_line(tmp_path: Path) -> None:
    root = tmp_path / "telebotshop"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    domain = root / "domain"
    domain.mkdir()
    (domain / "__init__.py").write_text("from .. import infrastructure\n", encoding="utf-8")
    (domain / "order.py").write_text("# regression fixture\nimport sqlalchemy\n", encoding="utf-8")
    errors = check_package(root)
    assert len(errors) == 2
    assert any("telebotshop.domain:1" in error for error in errors)
    assert any("telebotshop.domain.order:2" in error for error in errors)


def test_missing_or_empty_package_fails(tmp_path: Path) -> None:
    assert check_package(tmp_path / "missing")
    assert check_package(tmp_path)


def test_syntax_error_fails_with_diagnostic() -> None:
    assert check_source("telebotshop.domain.order", "from (")


def test_invalid_source_encoding_fails(tmp_path: Path) -> None:
    (tmp_path / "__init__.py").write_bytes(b"\xff")
    errors = check_package(tmp_path)
    assert len(errors) == 1
    assert "cannot read source" in errors[0]


@pytest.mark.parametrize(
    ("source", "exit_code"), [("import dataclasses", 0), ("import sqlalchemy", 1)]
)
def test_cli_exit_reflects_import_violations(tmp_path: Path, source: str, exit_code: int) -> None:
    project = tmp_path / "project"
    script = project / "scripts" / "check_architecture.py"
    script.parent.mkdir(parents=True)
    original = Path(__file__).resolve().parents[2] / "scripts" / script.name
    script.write_bytes(original.read_bytes())
    package = project / "src" / "telebotshop"
    domain = package / "domain"
    domain.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (domain / "order.py").write_text(source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == exit_code, result.stderr
    if exit_code:
        assert "telebotshop.domain.order:1: forbidden import sqlalchemy" in result.stderr


def test_application_obeys_import_matrix() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "telebotshop"
    assert check_package(root) == []
