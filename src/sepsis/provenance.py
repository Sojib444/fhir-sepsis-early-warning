"""Provenance stamping (AGENTS-ENGINEERING.md §23).

Every artefact that carries a number — results/metrics.json, docs/data_notes.md,
results/cross_site_matrix.md — embeds one of these blocks. It is what lets
someone check, a year from now, that a figure in the report matches the code
that produced it.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sepsis.config import repo_root

#: Packages whose version can move a number. Recorded with every artefact.
TRACKED_PACKAGES: tuple[str, ...] = (
    "polars",
    "numpy",
    "pandas",
    "scikit-learn",
    "lightgbm",
    "shap",
    "pyarrow",
)


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return result.stdout.strip()


def git_commit() -> str:
    return _git("rev-parse", "HEAD") or "unknown"


def git_tree_is_dirty() -> bool | None:
    """True if there are uncommitted changes. None if git is unavailable.

    A number produced from a dirty tree cannot be traced to a commit, so this
    flag travels with it rather than being quietly dropped.
    """
    status = _git("status", "--porcelain")
    if status is None:
        return None
    return bool(status)


def package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in TRACKED_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "not installed"
    return versions


def sha256_of_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def input_data_digest(cohort_path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Digest of the inputs a number was computed from.

    Prefers the checked-in data/CHECKSUMS.sha256 entries, which cover the raw
    files themselves; falls back to hashing the parquet cache.
    """
    digests: dict[str, str] = {}

    checksums = repo_root() / "data" / "CHECKSUMS.sha256"
    if checksums.exists():
        for line in checksums.read_text(encoding="utf-8").splitlines():
            parts = line.split("  ", 1)
            if len(parts) == 2:
                digests[parts[1].strip()] = parts[0].strip()

    if cohort_path is not None and Path(cohort_path).exists():
        digests[str(Path(cohort_path).as_posix())] = sha256_of_file(cohort_path)

    return digests


def provenance(seed: int, cohort_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """The provenance block embedded alongside every reported number.

    `timestamp_utc` is the only field expected to differ between two runs of
    the same commit on the same data — the determinism test excludes it.
    """
    return {
        "git_commit": git_commit(),
        "git_tree_dirty": git_tree_is_dirty(),
        "seed": seed,
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "packages": package_versions(),
        "input_data_sha256": input_data_digest(cohort_path),
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def provenance_markdown(block: dict[str, Any]) -> str:
    """Render a provenance block as a markdown footer for a generated document."""
    dirty = block["git_tree_dirty"]
    dirty_text = "unknown" if dirty is None else ("yes" if dirty else "no")
    packages = ", ".join(f"{name} {version}" for name, version in block["packages"].items())

    lines = [
        "## Provenance",
        "",
        "This file is generated. Do not edit it by hand; edit the code that writes it.",
        "",
        f"- Generated (UTC): `{block['timestamp_utc']}`",
        f"- Git commit: `{block['git_commit']}` (working tree dirty: {dirty_text})",
        f"- Seed: `{block['seed']}`  ·  `PYTHONHASHSEED={block['pythonhashseed']}`",
        f"- Python: {block['python_version']} on {block['platform']}",
        f"- Packages: {packages}",
    ]
    if block["input_data_sha256"]:
        lines.append("- Input digests:")
        for key, value in sorted(block["input_data_sha256"].items()):
            lines.append(f"  - `{key}` → `{value}`")
    return "\n".join(lines) + "\n"
