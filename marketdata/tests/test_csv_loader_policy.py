"""ISSUE-306: :func:`make_csv_loader` が各スライスの loader の写しを置き換えることの検定。

なぜ必要か:
    ``indigators/*/src/loader.py`` は 17 スライスで同一の委譲関数を手書き複製していた
    （codescan 実測: 削減見込み 1 位 394 行・2 位 346 行・3 位 322 行がこの群）。差は
    「必須列」「``cast_column_names``」「``require_non_empty``」の 3 方針だけである。
    本ビルダはその 3 方針を引数として受け、委譲の実体を 1 箇所へ集める。

固定する不変条件:
    - 生成された関数は :func:`read_ohlc_csv_with_policy` へ方針をそのまま渡す（挙動不変）。
    - 公開シグネチャ ``(path, *, time_column=None, require=<既定>, **read_csv_kwargs)`` を保つ
      （複製されていた 17 関数と同一。呼出側は無改変で動く）。
"""
from __future__ import annotations

import pytest

from marketdata.ohlc_csv_loader import make_csv_loader

_OHLCV = ("open", "high", "low", "close", "volume")


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_required_columns_are_matched_case_insensitively_without_renaming(tmp_path):
    """必須列の照合は大小不問。ただし列名は改名しない（既存 17 loader の実挙動）。"""
    csv = _write(tmp_path / "a.csv", "Open,High,Low,Close,Volume\n1,2,0,1,10\n")
    load = make_csv_loader(_OHLCV, cast_column_names=True)

    df = load(csv)

    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_cast_column_names_false_rejects_non_string_column_names(tmp_path):
    """``cast_column_names`` の有無が実際に方針として伝わることを固定する。"""
    csv = _write(tmp_path / "a.csv", "open,high,low,close,1\n1,2,0,1,9\n")
    lenient = make_csv_loader(("open", "high", "low", "close"), cast_column_names=True)
    strict = make_csv_loader(("open", "high", "low", "close"))

    assert len(lenient(csv, header=0)) == 1
    with pytest.raises(AttributeError):
        strict(csv, header=0, dtype=None, names=["open", "high", "low", "close", 1], skiprows=1)


def test_generated_loader_raises_when_a_required_column_is_missing(tmp_path):
    csv = _write(tmp_path / "a.csv", "open,high,low,close\n1,2,0,1\n")
    load = make_csv_loader(_OHLCV, cast_column_names=True)

    with pytest.raises(KeyError):
        load(csv)


def test_require_non_empty_policy_is_forwarded(tmp_path):
    csv = _write(tmp_path / "a.csv", "open,high,low,close\n")
    strict = make_csv_loader(("open", "high", "low", "close"), require_non_empty=True)
    lenient = make_csv_loader(("open", "high", "low", "close"))

    with pytest.raises(ValueError):
        strict(csv)
    assert len(lenient(csv)) == 0


def test_time_column_and_read_csv_kwargs_pass_through(tmp_path):
    csv = _write(tmp_path / "a.csv", "date;open;high;low;close\n2024-01-01;1;2;0;1\n")
    load = make_csv_loader(("open", "high", "low", "close"))

    df = load(csv, time_column="date", sep=";")

    assert df.index.name == "date" and len(df) == 1


def test_caller_can_still_override_require(tmp_path):
    """複製されていた 17 関数は ``require=`` を受け取れた。その面を保つ。"""
    csv = _write(tmp_path / "a.csv", "open,close\n1,1\n")
    load = make_csv_loader(("open", "high", "low", "close"))

    df = load(csv, require=("open", "close"))

    assert list(df.columns) == ["open", "close"]
