"""Image contents rule: the files the Dockerfile copies are enough to import and start the hub."""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"  # the build context (compose.yml: context ..)
DOCKERFILE = INFRA / "event-hub" / "Dockerfile"
DOCKERIGNORE = INFRA / "event-hub" / "Dockerfile.dockerignore"
# No PYTHON* variables and no user site: only the copied files and the installed packages, as in
# the image. (Not -I: the image's CMD relies on the script directory being on sys.path.)
ISOLATED = ("-E", "-s")


def copies() -> list[tuple[list[str], str]]:
    """(sources, destination) of every COPY from the build context."""
    pairs = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "COPY" and not parts[1].startswith("--"):
            *sources, destination = parts[1:]
            pairs.append((sources, destination))
    return pairs


def build_app(tmp_path: Path) -> Path:
    """Lay out /app the way Docker COPY does, from the same build context."""
    app = tmp_path / "app"
    for sources, destination in copies():
        if not destination.startswith("/app/"):
            continue  # e.g. the requirements lock under /tmp
        target = app / destination.removeprefix("/app/")
        for source in sources:
            path = INFRA / source
            if path.is_dir():
                shutil.copytree(path, target, dirs_exist_ok=True)
            elif destination.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target / path.name)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    return app


def test_every_copied_file_is_allowed_by_the_dockerignore():
    allowed = [
        line[1:].rstrip("/")
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.startswith("!")
    ]
    for sources, _ in copies():
        for source in sources:
            files = (
                [
                    p.relative_to(INFRA).as_posix()
                    for p in (INFRA / source).rglob("*.py")
                ]
                if (INFRA / source).is_dir()
                else [source]
            )
            for name in files:
                assert any(fnmatch.fnmatch(name, pattern) for pattern in allowed), name


def test_the_copied_files_import_the_hub_and_its_transport(tmp_path):
    app = build_app(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            *ISOLATED,
            "-c",
            (
                "import sys; sys.path.insert(0, 'infra/scripts/services'); "
                "import event_hub, event_hub_transport; print('ok')"
            ),
        ],
        cwd=app,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,  # the assertion below reports the import error
    )
    assert result.returncode == 0, result.stderr.strip().splitlines()[-1:]
    assert result.stdout.strip() == "ok"


def test_the_image_command_parses_its_arguments(tmp_path):
    app = build_app(tmp_path)
    result = subprocess.run(
        [sys.executable, *ISOLATED, "infra/scripts/services/event_hub.py", "--help"],
        cwd=app,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.strip().splitlines()[-1:]
    assert "serve" in result.stdout
