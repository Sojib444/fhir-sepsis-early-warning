"""Parsing of the Challenge 2019 .psv files into one long-format parquet table.

One row per patient-hour. The cache at data/interim/cohort.parquet holds
*every* patient, including those the D3 inclusion rule will later drop, so that
docs/data_notes.md can report the effect of that rule from the same file the
model is trained from.

Nothing in this module filters, imputes, or fills. It parses, validates, and
concatenates — no more than that.
"""

from __future__ import annotations

import io as _io
import os
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import polars as pl

from sepsis.config import LABEL, PSV_COLUMNS

#: Files are read in batches of this many per worker task. Large enough that
#: process hand-off is not the bottleneck, small enough to keep memory flat.
_BATCH_SIZE = 256


class PsvFormatError(ValueError):
    """A .psv file does not match the documented Challenge 2019 format."""


def patient_id_from_path(path: str | os.PathLike[str]) -> str:
    """`.../p012345.psv` -> `p012345`. The filename is the only patient ID."""
    stem = Path(path).stem
    if not stem.startswith("p") or not stem[1:].isdigit():
        raise PsvFormatError(f"unexpected patient file name: {Path(path).name}")
    return stem


def parse_psv(
    path: str | os.PathLike[str],
    site: str,
    *,
    patient_id: str | None = None,
) -> pl.DataFrame:
    """Parse one patient file into a long-format frame.

    Returns columns: patient_id, site, hour, the 40 features, SepsisLabel.

    `hour` is a 0-based row index assigned *after* sorting by ICULOS. Rows in
    these files are already in order, but we sort rather than trust: an
    out-of-order file would otherwise silently corrupt every backward-looking
    feature. Duplicate ICULOS values are rejected, because there is no
    defensible way to decide which of two rows for the same hour is real.

    `hour` is a position in the record, **not** the ICU hour. Checked against
    the data and reported in docs/data_notes.md: ICULOS is gap-free for every
    patient, so consecutive rows really are consecutive hours and a window of
    `k` rows is a window of `k` hours — but a substantial share of records
    begin at ICULOS > 1, so `hour = 0` is not the same clock reading for every
    patient. Use `ICULOS` wherever the ICU hour itself is what matters.
    """
    path = Path(path)
    pid = patient_id if patient_id is not None else patient_id_from_path(path)

    frame = pl.read_csv(
        path,
        separator="|",
        null_values=["NaN", "nan", "NA", ""],
        schema_overrides={col: pl.Float64 for col in PSV_COLUMNS},
    )

    if tuple(frame.columns) != PSV_COLUMNS:
        missing = set(PSV_COLUMNS) - set(frame.columns)
        extra = set(frame.columns) - set(PSV_COLUMNS)
        raise PsvFormatError(
            f"{path.name}: column mismatch (missing={sorted(missing)}, extra={sorted(extra)})"
        )
    if frame.height == 0:
        raise PsvFormatError(f"{path.name}: file has a header but no rows")

    iculos = frame.get_column("ICULOS")
    if iculos.null_count() > 0:
        raise PsvFormatError(f"{path.name}: ICULOS contains nulls")
    if iculos.n_unique() != frame.height:
        raise PsvFormatError(f"{path.name}: duplicate ICULOS values")
    if not iculos.is_sorted():
        frame = frame.sort("ICULOS")

    return frame.with_columns(
        pl.lit(pid, dtype=pl.Utf8).alias("patient_id"),
        pl.lit(site, dtype=pl.Utf8).alias("site"),
        pl.int_range(0, frame.height, dtype=pl.Int32).alias("hour"),
        pl.col(LABEL).cast(pl.Int8),
    ).select(["patient_id", "site", "hour", *PSV_COLUMNS])


def _parse_batch(args: tuple[Sequence[str], str]) -> bytes:
    """Worker entry point: parse a batch of files, return Arrow IPC bytes.

    Returning serialised bytes rather than a DataFrame keeps the amount of
    pickling between processes small and predictable.
    """
    paths, site = args
    frames = [parse_psv(p, site) for p in paths]
    buf = _io.BytesIO()
    pl.concat(frames, how="vertical").write_ipc(buf)
    return buf.getvalue()


def _batched(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def list_patient_files(site_dir: str | os.PathLike[str]) -> list[str]:
    """Sorted list of patient .psv files in a site directory."""
    site_dir = Path(site_dir)
    if not site_dir.is_dir():
        raise FileNotFoundError(f"site directory not found: {site_dir}")
    files = sorted(str(p) for p in site_dir.glob("p*.psv"))
    if not files:
        raise FileNotFoundError(f"no p*.psv files in {site_dir}")
    return files


def load_site(
    site_dir: str | os.PathLike[str],
    site: str,
    *,
    max_workers: int | None = None,
) -> pl.DataFrame:
    """Parse every patient file for one site, in parallel.

    On Windows and macOS, worker processes are *spawned*, which re-imports the
    calling module. A caller that runs this at import time must therefore guard
    its entry point with `if __name__ == "__main__":`, or pass `max_workers=1`.
    """
    files = list_patient_files(site_dir)
    batches = [(batch, site) for batch in _batched(files, _BATCH_SIZE)]

    if max_workers == 1:  # serial path, used by tests and small fixtures
        payloads = [_parse_batch(b) for b in batches]
    else:
        try:
            with ProcessPoolExecutor(max_workers=max_workers) as pool:
                payloads = list(pool.map(_parse_batch, batches))
        except BrokenProcessPool as error:
            # Almost always the missing __main__ guard above. The raw error is
            # a page of multiprocessing internals that says nothing useful.
            raise RuntimeError(
                "the worker pool died while parsing. On Windows and macOS this "
                'is usually a caller without an `if __name__ == "__main__":` '
                "guard, because spawning a worker re-imports the calling "
                "module. Add the guard, or call load_site(..., max_workers=1)."
            ) from error

    return pl.concat(
        [pl.read_ipc(_io.BytesIO(payload)) for payload in payloads],
        how="vertical",
    )


def build_cohort(
    raw_dir: str | os.PathLike[str],
    sites: dict[str, str],
    *,
    max_workers: int | None = None,
) -> pl.DataFrame:
    """Parse all sites into a single long-format cohort table.

    No filtering is applied here — see `sepsis.splits.apply_inclusion` for D3.
    """
    raw_dir = Path(raw_dir)
    frames = [
        load_site(raw_dir / dirname, site, max_workers=max_workers)
        for site, dirname in sorted(sites.items())
    ]
    cohort = pl.concat(frames, how="vertical")
    return cohort.sort(["site", "patient_id", "hour"])


def write_cohort(cohort: pl.DataFrame, path: str | os.PathLike[str]) -> Path:
    """Write the cohort cache, creating the parent directory if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cohort.write_parquet(path, compression="zstd")
    return path


def read_cohort(path: str | os.PathLike[str]) -> pl.DataFrame:
    """Read the cohort cache written by `write_cohort`."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `make data` (or scripts/fetch_data.sh then make data)"
        )
    return pl.read_parquet(path)
