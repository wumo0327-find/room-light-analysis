"""Runtime checks for optional, research-only Python dependencies.

The normal screening workflow deliberately does not import these packages.  This
keeps the main window usable when a research dependency was added after a user's
virtual environment had already been created.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass


RESEARCH_DEPENDENCIES = {
    "shapely": "Shapely>=2.0.0",
}


@dataclass(frozen=True)
class DependencyRepairResult:
    ok: bool
    missing_modules: tuple[str, ...] = ()
    detail: str = ""


def missing_research_dependencies() -> tuple[str, ...]:
    """Return packages that cannot actually be imported in this interpreter."""
    missing = []
    for module_name in RESEARCH_DEPENDENCIES:
        try:
            importlib.import_module(module_name)
        except (ImportError, OSError, AttributeError, ValueError):
            missing.append(module_name)
    return tuple(missing)


def repair_research_dependencies(*, timeout: int = 300) -> DependencyRepairResult:
    """Install only the fixed allow-list of missing research dependencies."""
    missing = missing_research_dependencies()
    if not missing:
        return DependencyRepairResult(True)
    packages = [RESEARCH_DEPENDENCIES[name] for name in missing]
    command = [sys.executable, "-m", "pip", "install", *packages]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return DependencyRepairResult(False, missing, str(exc))
    importlib.invalidate_caches()
    remaining = missing_research_dependencies()
    if completed.returncode == 0 and not remaining:
        return DependencyRepairResult(True)
    output = (completed.stderr or completed.stdout or "pip 未返回错误说明").strip()
    return DependencyRepairResult(False, remaining or missing, output[-2000:])
