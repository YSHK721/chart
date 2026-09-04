"""ミラー／単一権威の検定（ISSUE-447 段階 1 / 検定 M-1・M-4）。

本 repo の是正は繰り返し「コメントに正しいことを書く」で終わってきた。宣言は施行されている
ように読めるが、施行する仕組みが無ければ次の編集で静かに破れる（ISSUE-262）。ここでは
「規則の第 2 実装が無いこと」と「増分経路と全量経路の結果が一致すること」を実物で固定する。
"""
from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import ingest, journal, m1_chain, rebuild

_PKG = Path(tick_m1.__file__).resolve().parent / "mt5_ticks"
_TOKEN = "JP225@OANDA-Japan-MT5-Live"
_DAY = dt.date(2026, 8, 25)


def _label_ms(utc: dt.datetime) -> int:
    return int(utc.replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + 3 * 3600 * 1000


# =====================================================================
# M-1 sanitize の第 2 実装が無い
# =====================================================================

def test_sanitize_is_imported_from_the_existing_authority():
    """M-1: 呼んでいるのは ``capture_mt5_symbol_spec.sanitize_path_component`` 自身である。"""
    from tools.capture_mt5_symbol_spec import sanitize_path_component

    assert ingest.sanitize_path_component is sanitize_path_component


def test_the_package_declares_the_sanitizer_as_an_import_not_a_definition():
    """M-1: AST 上、``sanitize_path_component`` は import であって定義ではない。"""
    tree = ast.parse((_PKG / "ingest.py").read_text(encoding="utf-8"))
    defined = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imported = {
        alias.name for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) for alias in n.names
    }
    assert "sanitize_path_component" in imported
    assert "sanitize_path_component" not in defined


def _lines_defining_a_character_set() -> "list[str]":
    """許可文字集合を書き下しているコード行を集める（走査は純関数・検定本体は 1 主張）。"""
    out: "list[str]" = []
    for path in sorted(_PKG.glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.strip()
            if code.startswith("#"):
                continue
            if "abcdefghijklmnopqrstuvwxyz" in code or "SAFE_CHARS" in code:
                out.append(f"{path.name}:{i}")
    return out


def test_no_module_in_the_package_redefines_the_safe_character_set():
    """M-1: 許可文字集合の相当物を本パッケージが持たない（ミラー実装禁止）。"""
    offenders = _lines_defining_a_character_set()
    assert offenders == [], (
        f"パス成分の文字集合を再定義しています: {offenders}。"
        " tools.capture_mt5_symbol_spec.sanitize_path_component を import してください。"
    )


# =====================================================================
# M-4 増分経路 == 全量経路
# =====================================================================

def _day_rows(minutes: int, per_minute: int):
    """UTC 09:00 から ``minutes`` 分ぶんのティック（外れ値なし）。"""
    start = dt.datetime(2026, 8, 25, 9, 0)
    rows = []
    for m in range(minutes):
        for i in range(per_minute):
            when = start + dt.timedelta(minutes=m, seconds=i * (60.0 / per_minute))
            price = 66000.0 + m * 2.0 + i * 0.1
            rows.append((_label_ms(when), price, price + 10.0))
    return rows, start + dt.timedelta(minutes=minutes)


def test_the_intraday_fold_equals_the_m1_derived_from_the_finalized_parquet(tmp_path):
    """M-4: ジャーナル畳み（intraday）の M1 == 確定 parquet からの M1。"""
    # Arrange: 同じティックを 2 経路へ通す。
    rows, until = _day_rows(minutes=5, per_minute=20)
    journal.append(_DAY, rows, symbol=_TOKEN, data_dir=tmp_path)
    assert journal.finalize(_DAY, symbol=_TOKEN, data_dir=tmp_path) == "written"

    # Act: 経路 A = 増分（閉じた分のみ畳む）。
    m1_chain.append_m1_for_closed_minutes(
        rows, ref="incremental", data_dir=tmp_path, until=until.replace(tzinfo=dt.timezone.utc)
    )
    incremental = pd.read_csv(tick_m1.m1_csv_path(ref="incremental", data_dir=tmp_path))

    # 経路 B = 確定 parquet を権威の集計器へ通す。
    parquet = pd.read_parquet(
        tick_m1.day_parquet_path(_DAY, symbol=_TOKEN, data_dir=tmp_path),
        columns=tick_m1._TICK_COLUMNS,
    )
    # 突合相手も **同じ価格基準**で回す（依頼者裁定 2026-09-02 で MT5 系列は bid）。基準を
    #   揃えずに比べると、測っているのは「経路の一致」ではなく「基準の食い違い」になる。
    whole = tick_m1._format_m1_for_csv(
        tick_m1.ticks_to_m1(parquet, price_basis=ingest.PRICE_BASIS)
    ).reset_index()

    # Assert
    pd.testing.assert_frame_equal(incremental, whole, check_dtype=False)


def test_the_intraday_csv_is_byte_identical_to_the_whole_day_builder(tmp_path):
    """M-4: 全量経路（``build_m1_from_ticks``）の出力と **1 バイト一致**する。

    書式・列順・端数・改行のいずれかがずれれば、同じデータが 2 通りの CSV になる。
    """
    # Arrange
    rows, until = _day_rows(minutes=6, per_minute=15)
    journal.append(_DAY, rows, symbol=_TOKEN, data_dir=tmp_path)
    journal.finalize(_DAY, symbol=_TOKEN, data_dir=tmp_path)

    # Act
    m1_chain.append_m1_for_closed_minutes(
        rows, ref="incremental", data_dir=tmp_path, until=until.replace(tzinfo=dt.timezone.utc)
    )
    tick_m1.build_m1_from_ticks(
        _DAY, _DAY, symbol=_TOKEN, ref="whole", data_dir=tmp_path, until=until,
        price_basis=ingest.PRICE_BASIS,  # 突合相手も MT5 系列と同じ基準（bid）で回す。
    )

    # Assert
    a = tick_m1.m1_csv_path(ref="incremental", data_dir=tmp_path).read_bytes()
    b = tick_m1.m1_csv_path(ref="whole", data_dir=tmp_path).read_bytes()
    assert a == b


def _phantom_day_rows(minutes: int = 10, phantom=(4, 5)):
    """分 ``phantom`` を ~15,100 帯のファントム run にした 1 日（ISSUE-107 と同型）。"""
    start = dt.datetime(2026, 8, 25, 9, 0)
    rows = [
        (_label_ms(start + dt.timedelta(minutes=m, seconds=i * 3)),
         15100.0 if m in phantom else 66000.0 + m * 2.0 + i * 0.1,
         (15100.0 if m in phantom else 66000.0 + m * 2.0 + i * 0.1) + 10.0)
        for m in range(minutes) for i in range(20)
    ]
    return rows, start + dt.timedelta(minutes=minutes)


def test_the_intraday_fold_alone_still_carries_the_phantom_bars(tmp_path):
    """M-4 の**射程**: 畳みだけでは外れ分バーは落ちない（日次統計を持てないため）。

    全量経路（:func:`tick_m1.build_m1_from_ticks`）は日別 M1 へ日次クリーニング（日内 close
    中央値から ±30% 乖離するバーの除去・ISSUE-107）を適用する。これは**日単位の統計**を要する
    ため、分単位の増分では同じ判断ができない。この非対称は消えたのではなく、
    「日中は暫定値・日次確定後に再構築で解消する」（設計 §10 の裁定＝案 b）という形で
    引き受けている。ここは裁定の**前半**（日中は食い違う）を固定する。
    """
    # Arrange
    rows, until = _phantom_day_rows()
    journal.append(_DAY, rows, symbol=_TOKEN, data_dir=tmp_path)
    journal.finalize(_DAY, symbol=_TOKEN, data_dir=tmp_path)

    # Act
    m1_chain.append_m1_for_closed_minutes(
        rows, ref="incremental", data_dir=tmp_path, until=until.replace(tzinfo=dt.timezone.utc)
    )
    tick_m1.build_m1_from_ticks(
        _DAY, _DAY, symbol=_TOKEN, ref="whole", data_dir=tmp_path, until=until,
        price_basis=ingest.PRICE_BASIS,  # 突合相手も MT5 系列と同じ基準（bid）で回す。
    )
    incremental = pd.read_csv(tick_m1.m1_csv_path(ref="incremental", data_dir=tmp_path))
    whole = pd.read_csv(tick_m1.m1_csv_path(ref="whole", data_dir=tmp_path))

    # Assert: 差は「日次クリーニングが落とした外れ分バー」ちょうどである。
    only_in_incremental = set(incremental["date"]) - set(whole["date"])
    assert only_in_incremental == {"2026-08-25 09:04:00", "2026-08-25 09:05:00"}, (
        "増分経路と全量経路の差が、日次クリーニングが除去した外れ分バー以外に広がっています。"
    )


def test_the_finalized_record_matches_the_authority_even_on_an_outlier_day(tmp_path):
    """裁定の**後半**: 日次確定後の再構築で、外れ値日も権威と完全一致する（設計 §10）。

    裁定前はここが「食い違ったままである」ことを特性検定として固定していた。裁定
    （日次確定時に再構築・案 b）が済んだので、固定すべき仕様は
    「**確定記録は既存権威と完全一致する**」へ変わる。
    """
    # Arrange
    rows, until = _phantom_day_rows()
    journal.append(_DAY, rows, symbol=_TOKEN, data_dir=tmp_path)
    journal.finalize(_DAY, symbol=_TOKEN, data_dir=tmp_path)
    m1_chain.append_m1_for_closed_minutes(
        rows, ref="incremental", data_dir=tmp_path, until=until.replace(tzinfo=dt.timezone.utc)
    )

    # Act: UTC 日が閉じた後の再構築。
    outcome = rebuild.rebuild_day(
        _DAY, symbol=_TOKEN, ref="incremental", data_dir=tmp_path, update_rollups=False
    )

    # Assert
    tick_m1.build_m1_from_ticks(
        _DAY, _DAY, symbol=_TOKEN, ref="whole", data_dir=tmp_path,
        price_basis=ingest.PRICE_BASIS,  # 突合相手も MT5 系列と同じ基準（bid）で回す。
    )
    incremental = tick_m1.m1_csv_path(ref="incremental", data_dir=tmp_path).read_bytes()
    whole = tick_m1.m1_csv_path(ref="whole", data_dir=tmp_path).read_bytes()
    assert outcome == rebuild.REPLACED
    assert incremental == whole


def test_folding_in_several_cycles_equals_folding_in_one(tmp_path):
    """M-4: 何回に分けて受信しても結果が変わらない（周期の切れ目に依存しない）。"""
    rows, until = _day_rows(minutes=4, per_minute=20)

    # 一括で畳む。
    m1_chain.append_m1_for_closed_minutes(
        rows, ref="onego", data_dir=tmp_path, until=until.replace(tzinfo=dt.timezone.utc)
    )

    # 30 行ずつ、形成中の分を持ち越しながら畳む。
    pending: list = []
    chunk = 30
    for offset in range(0, len(rows), chunk):
        batch = rows[offset:offset + chunk]
        last_utc = pd.Timestamp(
            m1_chain.ingest.server_clock.to_utc_ms(batch[-1][0]), unit="ms", tz="UTC"
        )
        result = m1_chain.append_m1_for_closed_minutes(
            pending + batch, ref="stepwise", data_dir=tmp_path,
            until=last_utc.floor("min"),
        )
        pending = list(result.pending_rows)
    m1_chain.append_m1_for_closed_minutes(
        pending, ref="stepwise", data_dir=tmp_path,
        until=until.replace(tzinfo=dt.timezone.utc),
    )

    onego = tick_m1.m1_csv_path(ref="onego", data_dir=tmp_path).read_bytes()
    stepwise = tick_m1.m1_csv_path(ref="stepwise", data_dir=tmp_path).read_bytes()
    assert onego == stepwise
