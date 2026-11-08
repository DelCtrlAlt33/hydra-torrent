"""Dependencies must be pinned to exact versions, and the few the CI workflow
installs inline must match requirements.txt.

Unpinned, a fresh install or rebuild takes whatever is newest that day, and a
new release of any dependency can change behaviour with no change to Hydra
(pywebview in particular drives the desktop window). Dependabot bumps the pins
deliberately instead.
"""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# name[extras]==version, optionally followed by an environment marker
_PIN = re.compile(r"^[A-Za-z0-9_.\-]+(\[[A-Za-z0-9_,\-]+\])?==[A-Za-z0-9_.\-]+\s*(;.*)?$")


def _requirement_lines(path):
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.split(" #")[0].strip()
            if not line or line.startswith("#") or line.startswith("-r ") or line.startswith("-c "):
                continue
            yield line


def test_every_requirement_is_pinned_to_an_exact_version():
    files = sorted(glob.glob(os.path.join(ROOT, "requirements*.txt")))
    assert files, "no requirements files found"
    loose = [
        f"{os.path.basename(p)}: {line}"
        for p in files for line in _requirement_lines(p) if not _PIN.match(line)
    ]
    assert not loose, "unpinned requirements (use ==):\n  " + "\n  ".join(loose)


def _pins(path):
    out = {}
    for line in _requirement_lines(path):
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;]+)", line)
        if m:
            out.setdefault(m.group(1).lower().replace("_", "-"), set()).add(m.group(2))
    return out


def test_ci_inline_pins_match_requirements_txt():
    with open(os.path.join(ROOT, ".github", "workflows", "tests.yml"), encoding="utf-8") as f:
        workflow = f.read()
    inline = re.findall(r"pip install ((?:[A-Za-z0-9_.\-]+==[A-Za-z0-9_.\-]+\s*)+)", workflow)
    assert inline, "expected the CI workflow to install its few dependencies with exact pins"
    wanted = _pins(os.path.join(ROOT, "requirements.txt"))
    for group in inline:
        for item in group.split():
            name, version = item.split("==")
            name = name.lower().replace("_", "-")
            assert name in wanted, f"{name} pinned in CI but not in requirements.txt"
            assert version in wanted[name], (
                f"CI pins {name}=={version} but requirements.txt has {sorted(wanted[name])}"
            )
