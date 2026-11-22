"""Every local module the daemon/app imports must be bundled into the exe.

hydra_daemon.py is loaded inside the PyInstaller onefile bundle from a
--add-data copy, so PyInstaller's import analysis never sees what it imports.
v1.4.5 shipped without ssrf_guard.py in that list and the exe could not start
at all. This test fails if a new local module is imported but not listed.
"""

import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Only the daemon's import tree: hydra_app.py is PyInstaller's entry point, so
# its own imports (cert_pinning, hydra_tray, ...) are found by the analysis.
ENTRY_FILES = ["hydra_daemon.py"]


def _local_imports(path):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return {n for n in names if os.path.isfile(os.path.join(ROOT, n + ".py"))}


def _bundled_modules():
    with open(os.path.join(ROOT, "build_installer.py"), encoding="utf-8") as f:
        text = f.read()
    return set(re.findall(r'"--add-data=(\w+)\.py;\."', text))


def test_every_local_import_is_bundled():
    bundled = _bundled_modules()
    # Walk imports transitively starting from the entry points.
    seen, todo = set(), list(ENTRY_FILES)
    while todo:
        fname = todo.pop()
        for mod in _local_imports(os.path.join(ROOT, fname)):
            if mod not in seen:
                seen.add(mod)
                todo.append(mod + ".py")
    missing = sorted(seen - bundled)
    assert not missing, f"Add these to build_installer.py --add-data: {missing}"
