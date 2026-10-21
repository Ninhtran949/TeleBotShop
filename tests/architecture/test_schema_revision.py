from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from telebotshop.infrastructure.database.schema import SCHEMA_HEAD


def test_readiness_revision_matches_migration_head() -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_current_head() == SCHEMA_HEAD
