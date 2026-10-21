"""Verify the repository's vendored engineering harness without dependencies."""

import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path

SKILLS = (
    "spec-driven-development",
    "planning-and-task-breakdown",
    "incremental-implementation",
    "test-driven-development",
    "code-review-and-quality",
    "security-and-hardening",
)
REPOSITORY = "https://github.com/addyosmani/agent-skills"


def verify(root: Path, archive: Path | None = None) -> list[str]:
    root = root.resolve()
    agents = root / ".agents"
    try:
        metadata = json.loads((agents / "upstream.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return [f"Cannot read upstream manifest: {error}"]
    if not isinstance(metadata, dict):
        return ["Upstream manifest must be a JSON object"]
    files = metadata.get("files")
    if (
        not isinstance(files, dict)
        or not files
        or not all(
            isinstance(path, str)
            and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest)
            for path, digest in files.items()
        )
    ):
        return ["Upstream manifest must contain file paths and SHA-256 values"]

    errors = []
    commit = metadata.get("commit", "")
    if (
        metadata.get("repository") != REPOSITORY
        or not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
    ):
        errors.append("Upstream repository or exact commit is invalid")
    archive_url = f"https://codeload.github.com/addyosmani/agent-skills/tar.gz/{commit}"
    archive_digest = metadata.get("archive_sha256", "")
    if (
        metadata.get("archive_url") != archive_url
        or not isinstance(archive_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", archive_digest)
    ):
        errors.append("Pinned archive URL or SHA-256 is invalid")
    if metadata.get("skills") != list(SKILLS):
        errors.append("Manifest skill list does not match the approved six skills")

    expected_skills = {f".agents/skills/{name}/SKILL.md" for name in SKILLS}
    discovered = {
        path.relative_to(root).as_posix() for path in (agents / "skills").rglob("SKILL.md")
    }
    if discovered != expected_skills:
        errors.append(
            f"Skill set mismatch: missing={sorted(expected_skills - discovered)}, "
            f"extra={sorted(discovered - expected_skills)}"
        )
    actual = {path.relative_to(root).as_posix() for path in agents.rglob("*") if path.is_file()}
    actual.discard(".agents/upstream.json")
    if actual != set(files):
        errors.append(
            f"Vendor file set mismatch: missing={sorted(set(files) - actual)}, "
            f"extra={sorted(actual - set(files))}"
        )
    required = {".agents/LICENSE", *expected_skills}
    if not required <= set(files) or not any(
        path.startswith(".agents/references/") for path in files
    ):
        errors.append("Manifest is missing required skills, shared references, or original license")

    for relative, digest in files.items():
        path = root / relative
        if not path.resolve().is_relative_to(agents):
            errors.append(f"Vendor path escapes .agents: {relative}")
            continue
        if not path.is_file():
            errors.append(f"Missing vendor file: {relative}")
            continue
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            errors.append(f"SHA-256 mismatch: {relative}")
        if path.suffix != ".md":
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeError:
            errors.append(f"Invalid UTF-8: {relative}")
            continue
        if path.name == "SKILL.md":
            header = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.DOTALL)
            if (
                not header
                or not re.search(rf"^name: {re.escape(path.parent.name)}$", header[1], re.MULTILINE)
                or not re.search(r"^description: \S.+$", header[1], re.MULTILINE)
            ):
                errors.append(f"Invalid skill name/description front matter: {relative}")
        references = re.findall(r"(?<![\w/])(?:\.\./)*references/[A-Za-z0-9_./-]+\.md", text)
        for reference in set(references):
            target = (path.parent / reference).resolve()
            if not target.is_relative_to(agents) or not target.is_file():
                errors.append(f"Missing reference: {relative} -> {reference}")

    for relative in (
        "AGENTS.md",
        ".gitattributes",
        ".gitignore",
        "docs/PROJECT.md",
        "docs/ARCHITECTURE.md",
    ):
        if not (root / relative).is_file():
            errors.append(f"Missing harness file: {relative}")
    for relative in ("docs/adr", "docs/plans"):
        if not (root / relative).is_dir():
            errors.append(f"Missing documentation directory: {relative}")

    if archive is not None:
        try:
            if hashlib.sha256(archive.read_bytes()).hexdigest() != archive_digest:
                errors.append("Archive SHA-256 mismatch")
            else:
                with tarfile.open(archive, "r:gz") as source:
                    prefix = f"agent-skills-{commit}/"
                    upstream = {}
                    for member in source.getmembers():
                        relative = member.name.removeprefix(prefix)
                        if member.isfile() and (
                            relative == "LICENSE"
                            or relative.startswith(
                                ("references/", *(f"skills/{name}/" for name in SKILLS))
                            )
                        ):
                            upstream[f".agents/{relative}"] = member
                    if set(upstream) != set(files):
                        errors.append("Archive subset differs from the installed manifest")
                    for relative in set(upstream) & set(files):
                        content = source.extractfile(upstream[relative]).read()
                        if hashlib.sha256(content).hexdigest() != files[relative]:
                            errors.append(f"Upstream archive content differs: {relative}")
        except (OSError, tarfile.TarError) as error:
            errors.append(f"Cannot verify upstream archive: {error}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Also compare with the pinned upstream tar.gz")
    arguments = parser.parse_args()
    errors = verify(Path(__file__).resolve().parents[1], arguments.archive)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: six skills, upstream file hashes, local reference paths, and harness layout")
    if arguments.archive:
        print("PASS: pinned archive checksum and complete selected upstream subset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
