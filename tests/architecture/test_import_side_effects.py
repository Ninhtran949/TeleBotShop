import os
import subprocess
import sys


def test_importing_application_does_not_load_settings_or_connect_to_network() -> None:
    source = """
import importlib
import pkgutil
import socket

def refuse_network(*args, **kwargs):
    raise AssertionError('network access during import')

socket.socket.connect = refuse_network
import telebotshop
for module in pkgutil.walk_packages(telebotshop.__path__, telebotshop.__name__ + '.'):
    importlib.import_module(module.name)
print('import-ok')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", source],
        env={**os.environ, "APP_ENV": "invalid", "DATABASE_URL": "invalid"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "import-ok"
