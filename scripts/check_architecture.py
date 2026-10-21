"""Check static imports against docs/ARCHITECTURE.md without importing application code."""

import ast
import sys
from importlib.util import resolve_name
from pathlib import Path

INTERNAL_IMPORTS = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    "bot": {"domain", "application", "bot"},
    "admin": {"domain", "application", "admin"},
    "api": {"domain", "application", "api"},
    "workers": {"domain", "application", "workers"},
    "infrastructure": {"domain", "infrastructure"},
}
TRANSPORT_LIBRARIES = {
    "bot": {"aiogram", "pydantic"},
    "admin": {"aiogram", "pydantic"},
    "api": {"fastapi", "starlette", "pydantic"},
}


def import_allowed(module: str, target: str) -> bool:
    if module in {"telebotshop.bootstrap", "telebotshop.__main__"}:
        return True
    layer = module.split(".")[1] if "." in module else ""
    parts = target.split(".")
    if parts[0] in sys.stdlib_module_names:
        return True
    if parts[0] != "telebotshop":
        return layer == "infrastructure" or parts[0] in TRANSPORT_LIBRARIES.get(layer, set())
    if layer == "infrastructure" and parts[1:3] == ["application", "ports"]:
        return True
    return len(parts) > 1 and parts[1] in INTERNAL_IMPORTS.get(layer, set())


def check_source(module: str, source: str, *, is_package: bool = False) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [f"{module}:{error.lineno}: {error.msg}"]
    errors = []
    package = module if is_package else module.rpartition(".")[0]
    # shortcut: static imports only; review dynamic loading and runtime behavior separately.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                try:
                    base = resolve_name("." * node.level + base, package)
                except (ImportError, ValueError):
                    errors.append(f"{module}:{node.lineno}: relative import escapes package")
                    continue
            targets = [f"{base}.{alias.name}" for alias in node.names]
        else:
            continue
        for target in targets:
            if not import_allowed(module, target):
                errors.append(f"{module}:{node.lineno}: forbidden import {target}")
    return errors


def check_package(root: Path) -> list[str]:
    if not (root / "__init__.py").is_file():
        return [f"Missing package initializer: {root / '__init__.py'}"]
    errors = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).with_suffix("").parts
        is_package = parts[-1] == "__init__"
        module = ".".join(("telebotshop", *(parts[:-1] if is_package else parts)))
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            errors.append(f"{module}: cannot read source: {error}")
            continue
        errors.extend(check_source(module, source, is_package=is_package))
    return errors


def main() -> int:
    errors = check_package(Path(__file__).resolve().parents[1] / "src" / "telebotshop")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("PASS: application static imports follow docs/ARCHITECTURE.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
