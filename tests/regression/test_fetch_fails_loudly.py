"""Regression: the fetch script must never fail silently.

The bug (fixed in "Fix silent download failure from untranslated paths in the
curl config"): under Git Bash, MSYS translates POSIX paths in command-line
arguments but not inside a curl `--config` file. Absolute `output` paths
reached the native curl binary unconverted, curl wrote nothing, and the script
reported progress and exited 0 with an empty data directory.

Nothing downstream would have caught that — `make data` would simply have
parsed whatever happened to be on disk. So the property under test is not "the
download works" but "a download that achieves nothing exits non-zero and says
what to do", which is also what §18 requires.

These tests never touch the real data directory and never reach PhysioNet.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FETCH_SCRIPT = REPO_ROOT / "scripts" / "fetch_data.sh"

#: Reserved by RFC 6761 to never resolve. No packet leaves the machine.
UNREACHABLE = "https://fetch-test.invalid/training"


def _bash() -> str:
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if Path(candidate).exists():
            return candidate
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    pytest.skip("no POSIX bash available")


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A throwaway repo layout, so the real data/raw/ is never touched.

    The script resolves its own root from its location, so copying it into
    <tmp>/scripts/ makes <tmp>/data/raw/ the target.
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "src" / "sepsis" / "vendor").mkdir(parents=True)
    shutil.copy(FETCH_SCRIPT, tmp_path / "scripts" / "fetch_data.sh")
    # Pretend the scorer is already vendored: this test is about the data path.
    (tmp_path / "src" / "sepsis" / "vendor" / "evaluate_sepsis_score.py").write_text(
        "# placeholder\n", encoding="utf-8"
    )
    return tmp_path


def _run(sandbox: Path) -> subprocess.CompletedProcess[str]:
    # One attempt, no backoff: the host cannot resolve, so retrying only makes
    # the fast-test gate slower (§21 budgets it at two minutes).
    environment = dict(os.environ, FETCH_BASE_URL=UNREACHABLE, FETCH_RETRIES="0")
    return subprocess.run(
        [_bash(), (sandbox / "scripts" / "fetch_data.sh").as_posix()],
        capture_output=True,
        text=True,
        timeout=300,
        env=environment,
        cwd=sandbox,
    )


def test_an_unreachable_source_exits_non_zero(sandbox: Path):
    """The original bug's signature was exit 0 with nothing downloaded."""
    result = _run(sandbox)
    assert result.returncode != 0, (
        "the fetch script reported success while downloading nothing:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_failure_prints_the_manual_download_instructions(sandbox: Path):
    """§18: on any failure, print the manual URL and the expected paths."""
    result = _run(sandbox)
    combined = result.stdout + result.stderr

    assert "Manual download instructions" in combined
    assert "physionet.org/content/challenge-2019" in combined
    assert "data/raw/training_setA" in combined
    assert "data/raw/training_setB" in combined


def test_failure_leaves_no_partial_data_claimed_as_complete(sandbox: Path):
    _run(sandbox)
    downloaded = list((sandbox / "data").rglob("p*.psv"))
    assert downloaded == []
