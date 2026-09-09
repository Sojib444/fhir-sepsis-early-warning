"""Tier 1 — parsing. Pure functions over small files, no real data."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from sepsis.config import PSV_COLUMNS
from sepsis.io import PsvFormatError, parse_psv, patient_id_from_path

HEADER = "|".join(PSV_COLUMNS)


def _row(iculos: float, hr: float, label: int = 0) -> str:
    values = ["NaN"] * len(PSV_COLUMNS)
    values[PSV_COLUMNS.index("HR")] = f"{hr:g}"
    values[PSV_COLUMNS.index("ICULOS")] = f"{iculos:g}"
    values[PSV_COLUMNS.index("Age")] = "60"
    values[PSV_COLUMNS.index("Gender")] = "1"
    values[PSV_COLUMNS.index("HospAdmTime")] = "-5"
    values[PSV_COLUMNS.index("SepsisLabel")] = str(label)
    return "|".join(values)


def _write(tmp_path: Path, name: str, rows: list[str]) -> Path:
    path = tmp_path / name
    path.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    return path


def test_patient_id_comes_from_the_filename():
    assert patient_id_from_path("/x/y/p012345.psv") == "p012345"


def test_patient_id_rejects_an_unexpected_name(tmp_path: Path):
    with pytest.raises(PsvFormatError):
        patient_id_from_path(tmp_path / "patient_1.psv")


def test_parse_produces_the_documented_columns(tmp_path: Path):
    path = _write(tmp_path, "p000001.psv", [_row(1, 80), _row(2, 82)])
    frame = parse_psv(path, "A")

    assert frame.columns == ["patient_id", "site", "hour", *PSV_COLUMNS]
    assert frame.height == 2
    assert frame.get_column("patient_id").to_list() == ["p000001", "p000001"]
    assert frame.get_column("site").to_list() == ["A", "A"]
    assert frame.get_column("hour").to_list() == [0, 1]


def test_nan_becomes_null_not_a_float(tmp_path: Path):
    path = _write(tmp_path, "p000001.psv", [_row(1, 80), _row(2, 82)])
    frame = parse_psv(path, "A")

    # Every lab in these rows is "NaN" in the file and must arrive as null, so
    # that missingness is representable rather than silently numeric.
    assert frame.get_column("Lactate").null_count() == 2
    assert frame.get_column("HR").null_count() == 0


def test_rows_out_of_order_are_sorted_by_iculos(tmp_path: Path):
    """§19 Tier 3: rows out of order — decided policy is *sort*, not reject."""
    path = _write(tmp_path, "p000001.psv", [_row(3, 90), _row(1, 70), _row(2, 80)])
    frame = parse_psv(path, "A")

    assert frame.get_column("ICULOS").to_list() == [1.0, 2.0, 3.0]
    assert frame.get_column("HR").to_list() == [70.0, 80.0, 90.0]
    assert frame.get_column("hour").to_list() == [0, 1, 2]


def test_duplicate_iculos_is_rejected(tmp_path: Path):
    path = _write(tmp_path, "p000001.psv", [_row(1, 70), _row(1, 80)])
    with pytest.raises(PsvFormatError, match="duplicate ICULOS"):
        parse_psv(path, "A")


def test_empty_file_is_rejected(tmp_path: Path):
    path = tmp_path / "p000001.psv"
    path.write_text(HEADER + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(PsvFormatError, match="no rows"):
        parse_psv(path, "A")


def test_wrong_columns_are_rejected(tmp_path: Path):
    path = tmp_path / "p000001.psv"
    path.write_text("HR|ICULOS|SepsisLabel\n80|1|0\n", encoding="utf-8", newline="\n")
    with pytest.raises(PsvFormatError, match="column mismatch"):
        parse_psv(path, "A")


def test_a_null_label_is_rejected(tmp_path: Path):
    """A null label would silently become a null prediction target."""
    row = _row(1, 70)
    fields = row.split("|")
    fields[PSV_COLUMNS.index("SepsisLabel")] = "NaN"
    path = _write(tmp_path, "p000001.psv", ["|".join(fields)])

    with pytest.raises(PsvFormatError, match="contains nulls"):
        parse_psv(path, "A")


def test_a_non_binary_label_is_rejected(tmp_path: Path):
    row = _row(1, 70)
    fields = row.split("|")
    fields[PSV_COLUMNS.index("SepsisLabel")] = "2"
    path = _write(tmp_path, "p000001.psv", ["|".join(fields)])

    with pytest.raises(PsvFormatError, match="not binary"):
        parse_psv(path, "A")


def test_label_is_an_integer_column(tmp_path: Path):
    path = _write(tmp_path, "p000001.psv", [_row(1, 70, label=0), _row(2, 80, label=1)])
    frame = parse_psv(path, "A")

    assert frame.schema["SepsisLabel"] == pl.Int8
    assert frame.get_column("SepsisLabel").to_list() == [0, 1]
