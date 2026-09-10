"""Memory-mapped window designs (Phase 3).

A full Phase 3 design is ~1.5M rows x ~580 features. Held as float64 that is
several GB — more than this repository's typical laptop can afford on top of
LightGBM's own float64 dataset. The design is therefore written once to disk as
a float32 memory map, row-aligned with a side-car .npz holding patient_id,
site, hour and SepsisLabel. Training maps the file back read-only, so the peak
resident memory is roughly LightGBM's dataset, not two copies of the design.

The numbers are byte-for-byte the same as `features.window_features_frame`
(both call `_feature_block` with the same column order).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from sepsis.features import _feature_block, _side_block, _sorted_by_hour, window_feature_columns


@dataclass(frozen=True)
class DesignFiles:
    """The three files that make up one split's design."""

    name: str
    npy: Path  # float32 memmap of the feature matrix
    side: Path  # .npz with patient_id, site, hour, SepsisLabel
    meta: Path  # JSON with columns, windows, n_rows, cohort_sha256


def _fixed_width_unicode(values: np.ndarray) -> np.ndarray:
    """Convert an object (string) array to fixed-width unicode.

    Object arrays are pickled inside .npz and cannot be loaded with
    ``allow_pickle=False``. Fixed-width unicode loads unconditionally, keeping
    the default (safer) loading path everywhere.
    """
    max_len = max(len(str(v)) for v in values)
    return np.asarray([str(v) for v in values], dtype=f"U{max(4, max_len)}")


def design_files(config, name: str) -> DesignFiles:
    """The on-disk location of a named design (e.g. 'a_train')."""
    directory = config.path("designs")
    return DesignFiles(
        name=name,
        npy=directory / f"{name}.npy",
        side=directory / f"{name}.npz",
        meta=directory / f"{name}.meta.json",
    )


def load_design(design: DesignFiles):
    """X (float32 read-only memmap), side dict, and the frozen column order."""
    x = np.load(design.npy, mmap_mode="r")  # type: ignore[attr-defined]
    side = np.load(design.side)
    meta = json.loads(design.meta.read_text(encoding="utf-8"))
    return x, side, list(meta["columns"])


def build_window_design(
    cohort: pl.DataFrame,
    columns: list[str],
    windows: tuple[int, ...],
    npy_path: Path,
    side_path: Path,
    meta_path: Path,
    cohort_sha256: str | None = None,
) -> None:
    """Write the design matrix of `cohort` to a float32 .npy memory map.

    Sequences patients by hour (as every downstream consumer must) and flushes
    per patient, so peak RAM stays small even for a full-site design.
    """
    frame = _sorted_by_hour(cohort)
    if frame.height == 0:
        raise ValueError("cannot build a design for an empty cohort")

    npy_path.parent.mkdir(parents=True, exist_ok=True)
    total = frame.height
    mm = np.lib.format.open_memmap(
        npy_path, mode="w+", dtype=np.float32, shape=(total, len(columns))
    )

    pids: list[np.ndarray] = []
    sites: list[np.ndarray] = []
    hours: list[np.ndarray] = []
    labels: list[np.ndarray] = []

    offset = 0
    for _, patient in frame.group_by("patient_id", maintain_order=True):
        block = _feature_block(patient, columns, windows)
        mm[offset : offset + block.shape[0], :] = block
        offset += block.shape[0]
        pid, site, hour, label = _side_block(patient)
        pids.append(pid)
        sites.append(site)
        hours.append(hour)
        labels.append(label)

    mm.flush()
    del mm

    npy_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        side_path,
        patient_id=_fixed_width_unicode(np.concatenate(pids)),
        site=_fixed_width_unicode(np.concatenate(sites)),
        hour=np.concatenate(hours),
        SepsisLabel=np.concatenate(labels),
    )
    meta_path.write_text(
        json.dumps(
            {
                "columns": columns,
                "windows": [int(w) for w in windows],
                "n_rows": int(total),
                "cohort_sha256": cohort_sha256,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def design_is_current(design: DesignFiles, columns: list[str], cohort_sha256: str) -> bool:
    """True when the on-disk design is complete and matches the inputs.

    Column order is part of the model contract, so a half-written or stale
    design must be rebuilt rather than silently reused.
    """
    if not (design.npy.exists() and design.side.exists() and design.meta.exists()):
        return False
    try:
        meta = json.loads(design.meta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    n_rows = int(meta.get("n_rows", -1))
    side_n_rows = int(np.load(design.side)["hour"].size)  # type: ignore[attr-defined]
    expected_bytes = 128 + n_rows * np.dtype(np.float32).itemsize * len(columns)
    return (
        meta.get("columns") == columns
        and meta.get("cohort_sha256") == cohort_sha256
        and n_rows == side_n_rows
        and design.npy.stat().st_size == expected_bytes
    )


def concatenate_designs(
    sources: list[DesignFiles],
    target: DesignFiles,
) -> None:
    """Concatenate several same-schema designs row-wise (e.g. a_train + b_train).

    Used for the A+B design: the per-patient blocks of each source are already
    exactly what a direct build would write, so concatenating the files gives
    byte-identical content without recomputing features. Wrap-in-X values never
    cross a patient boundary because a patient never straddles a source design.
    """
    if not sources:
        raise ValueError("nothing to concatenate")
    if not all(s.meta.exists() for s in sources):
        raise ValueError("all source designs must exist before concatenating")

    metas = [json.loads(s.meta.read_text(encoding="utf-8")) for s in sources]
    columns = metas[0]["columns"]
    if any(m["columns"] != columns for m in metas[1:]):
        raise ValueError("cannot concatenate designs with different column orders")

    n_rows = sum(int(m["n_rows"]) for m in metas)
    target.npy.parent.mkdir(parents=True, exist_ok=True)
    mm = np.lib.format.open_memmap(
        target.npy, mode="w+", dtype=np.float32, shape=(n_rows, len(columns))
    )

    offset = 0
    for source, meta in zip(sources, metas, strict=True):
        src = np.load(source.npy, mmap_mode="r")  # type: ignore[attr-defined]
        rows = int(meta["n_rows"])
        mm[offset : offset + rows, :] = src
        offset += rows
        del src

    mm.flush()
    del mm

    chunks = [np.load(s.side) for s in sources]
    np.savez(
        target.side,
        patient_id=_fixed_width_unicode(np.concatenate([c["patient_id"] for c in chunks])),
        site=_fixed_width_unicode(np.concatenate([c["site"] for c in chunks])),
        hour=np.concatenate([c["hour"] for c in chunks]),
        SepsisLabel=np.concatenate([c["SepsisLabel"] for c in chunks]),
    )
    target.meta.write_text(
        json.dumps(
            {
                "columns": columns,
                "windows": metas[0]["windows"],
                "n_rows": n_rows,
                "cohort_sha256": metas[0]["cohort_sha256"],
                "sources": [s.name for s in sources],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load_window_design(npy_path: Path, side_path: Path):
    """Read back the design as a read-only memory map plus its side arrays."""
    x = np.load(npy_path, mmap_mode="r")  # type: ignore[attr-defined]
    side = np.load(side_path)
    return x, side


def window_columns_from_config(config) -> list[str]:
    """The frozen column order for a config's windows."""
    return window_feature_columns(tuple(config.windows))
