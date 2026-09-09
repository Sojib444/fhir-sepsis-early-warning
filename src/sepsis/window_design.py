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
from pathlib import Path

import numpy as np
import polars as pl

from sepsis.features import _feature_block, _side_block, _sorted_by_hour, window_feature_columns


def build_window_design(
    cohort: pl.DataFrame,
    columns: list[str],
    windows: tuple[int, ...],
    npy_path: Path,
    side_path: Path,
    meta_path: Path,
) -> None:
    """Write the design matrix of `cohort` to a float32 .npy memory map.

    Sequences patients by hour (as every downstream consumer must) and flushes
    per patient, so peak RAM stays small even for a full-site design.
    """
    frame = _sorted_by_hour(cohort)
    if frame.height == 0:
        raise ValueError("cannot build a design for an empty cohort")

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
        patient_id=np.concatenate(pids),
        site=np.concatenate(sites),
        hour=np.concatenate(hours),
        SepsisLabel=np.concatenate(labels),
    )
    meta_path.write_text(
        json.dumps(
            {"columns": columns, "windows": [int(w) for w in windows], "n_rows": int(total)},
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