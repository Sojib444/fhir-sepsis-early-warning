"""Tier 3 — the download validation in scripts/fetch_data.sh.

An interrupted transfer leaves a file that is non-empty but short. If the fetch
script skipped it next time because it "exists", silently truncated patients
would enter the cohort and every number downstream would be wrong in a way no
other test would catch. These tests exercise the real shell function rather
than a Python re-implementation of it, so the two cannot drift apart.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FETCH_SCRIPT = REPO_ROOT / "scripts" / "fetch_data.sh"


def _bash() -> str:
    """Locate a POSIX bash.

    On Windows, `which("bash")` usually finds the WSL launcher in System32,
    which cannot execute a script on a Windows path. Git Bash is checked first
    and a System32 hit is rejected.
    """
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


def _run_validate(directory: Path) -> int:
    """Source fetch_data.sh and call validate_set. Returns files removed."""
    posix_script = FETCH_SCRIPT.as_posix()
    posix_dir = directory.as_posix()
    result = subprocess.run(
        [
            _bash(),
            "-c",
            f'source "{posix_script}"; validate_set "{posix_dir}"',
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip().splitlines()[-1])


@pytest.fixture
def site_copy(fixture_dir: Path, tmp_path: Path) -> Path:
    destination = tmp_path / "training_setA"
    shutil.copytree(fixture_dir / "training_setA", destination)
    return destination


def test_well_formed_files_are_all_kept(site_copy: Path):
    before = sorted(p.name for p in site_copy.glob("p*.psv"))
    assert _run_validate(site_copy) == 0
    assert sorted(p.name for p in site_copy.glob("p*.psv")) == before


def test_a_file_truncated_mid_row_is_removed(site_copy: Path):
    """The realistic failure: a dropped connection part-way through a line."""
    victim = site_copy / "p000001.psv"
    text = victim.read_text(encoding="utf-8")
    victim.write_text(text[: len(text) // 2], encoding="utf-8", newline="\n")

    assert _run_validate(site_copy) == 1
    assert not victim.exists()


def test_a_file_with_only_a_header_is_removed(site_copy: Path):
    victim = site_copy / "p000002.psv"
    header = victim.read_text(encoding="utf-8").splitlines()[0]
    victim.write_text(header + "\n", encoding="utf-8", newline="\n")

    assert _run_validate(site_copy) == 1
    assert not victim.exists()


def test_a_file_with_the_wrong_header_is_removed(site_copy: Path):
    """An HTML error page saved under a .psv name looks like this."""
    victim = site_copy / "p000003.psv"
    victim.write_text("<html>404 Not Found</html>\n", encoding="utf-8", newline="\n")

    assert _run_validate(site_copy) == 1
    assert not victim.exists()


def test_several_bad_files_are_all_removed_and_good_ones_survive(site_copy: Path):
    total_before = len(list(site_copy.glob("p*.psv")))
    for name in ("p000004.psv", "p000005.psv", "p000006.psv"):
        victim = site_copy / name
        text = victim.read_text(encoding="utf-8")
        victim.write_text(text[: len(text) // 3], encoding="utf-8", newline="\n")

    assert _run_validate(site_copy) == 3
    assert len(list(site_copy.glob("p*.psv"))) == total_before - 3


def test_validation_is_idempotent_on_a_clean_directory(site_copy: Path):
    """Running the fetch twice must not re-download; validation must agree."""
    assert _run_validate(site_copy) == 0
    assert _run_validate(site_copy) == 0
