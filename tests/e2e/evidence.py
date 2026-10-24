"""Store bounded results, never Telegram bodies, file IDs, charge IDs or user identities."""

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from tests.e2e.safety import E2EBlocked


class Evidence:
    def __init__(self, root: Path, actor: str):
        if actor not in {"owner", "buyer2"}:
            raise E2EBlocked("Unknown evidence actor")
        self.root = root
        self.actor = actor
        digest = hashlib.sha256()
        files = [
            p
            for folder in ("src", "tests", "scripts", "migrations")
            for p in (root / folder).rglob("*.py")
        ]
        files += [root / "pyproject.toml", root / "uv.lock"]
        for path in sorted(files):
            if path.is_file():
                digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        self.source_sha256 = digest.hexdigest()

    def passed(self, case: str, *, order_id: UUID | None = None):
        if re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", case) is None:
            raise E2EBlocked("Invalid evidence case name")
        if order_id is not None and not isinstance(order_id, UUID):
            raise E2EBlocked("Only internal order UUIDs may be attached to evidence")
        record = dict(
            case=case,
            actor=self.actor,
            status="PASS",
            at=datetime.now(UTC).isoformat(),
            source_sha256=self.source_sha256,
        )
        if order_id is not None:
            record["order_id"] = str(order_id)
        directory = self.root / ".cache/t14-e2e/evidence"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f"{uuid4().hex}.tmp"
        temporary.write_text(json.dumps(record, indent=2) + "\n", "utf-8")
        os.replace(temporary, directory / f"{self.actor}.{case}.json")
        print(json.dumps(record), flush=True)
