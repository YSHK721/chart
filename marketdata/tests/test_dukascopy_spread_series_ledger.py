"""Dukascopy のティックから気配幅つきの新系列を導く台帳宣言（ISSUE-533 段階 3 の前提工事）。

用語（初出定義）:
    対（pair）
        ＝ 台帳の 2 つの記述子 ``<base>`` と ``<base>_spread`` の組。前者は spread 列を持たず、
          後者は同じティック木・同じ価格基準・同じベンダのまま spread 列を持つ。先例は
          ``jp225_mt5`` ↔ ``jp225_mt5_spread``（ISSUE-511 段階 3 の段階 7a）。
    宣言
        ＝ 記述子の ``spread_point_snapshot``（銘柄仕様スナップショットの**所在**であって point の
          値ではない）。読み口は ``marketdata.spread_point``。
    置き場（series）
        ＝ ref の保存物の名前（``<series>_m1.csv`` と ``rollups/<series>/``）。解決は
          ``dataset_registry.series_of``。
    気配幅（spread）
        ＝ M1 の spread 列。その 1 分内の各ティックの ``(ask − bid) / point`` の**最小値**を
          整数 points へ丸めた値（規則の唯一源は ``marketdata.quote_spread``）。上位足は M1 の最小値。

本検定が固定するもの:
  D-1 Dukascopy のティック木を読む ref のうち spread を宣言するのは 1 件で、宣言の無い側と
      ``_spread`` の命名関係で対になる（**本ファイルの Red の担い手**。綴りは書き写さず、
      ベンダ属性と宣言の有無から導く）。
  D-2 対の記述子は、**置き場（path・series）と宣言以外のすべての欄が一致**する。比較は
      ``dataclasses.asdict`` の差分であり、リテラルの対応表を書かない（欄が 1 つ増えても
      比較対象に自動で入る）。台帳に在る対すべてに掛かる＝先例（MT5）と新系列が同じ関係を
      満たすことを 1 つの主張で固定する。
  D-3 置き場の関係: ``series_of(<base>_spread) == series_of(<base>) + "_spread"``。実 CSV の
      綴りも置き場から導かれる（``<series>_m1.csv``）。
  D-4 対のうち宣言を持つのは spread 側だけで、宣言した組のスナップショットは実在する。
  D-5 台帳の中で置き場と実 CSV パスが一意（既存の置き場へ書き込まない）。
  D-6 新系列の M1 の spread が、その分の**最小**気配幅と一致する。期待値は検定が生成した
      整数 points の ``min`` であり、検定は割り算も丸めも 1 度もしない（丸めの規則を書き写すと、
      規則を変える変異に検定も追随してしまう）。
  D-7 上位足（5m・1h の 2 段）の spread が、その period に属する M1 の最小値と一致する。
  D-8 新しい記述子を台帳へ足しても、既存系列（対の base 側）の出力は **byte 一致**であり
      spread 列を持たない（記述子を外した世界の出力と突き合わせる）。

本検定が固定しないもの（射程の明示）:
  - 履歴の実データ生成（``data/marketdata/**`` への書込）。本ファイルの書込はすべて ``tmp_path``。
  - 常駐（``tools/live_tick_watch.py``）の結線。それは
    ``tools/tests/test_live_tick_watch_series_set.py`` が持つ。
  - 一度畳んで複数系列へ配る口の計算量。それは
    ``marketdata/tests/test_tick_m1_series_fan_out_build.py`` が持つ。

合成系列の組み立ては ``marketdata/tests/spread_series_fixture.py`` が唯一源。構造:
Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from marketdata import csv_schema, dataset_registry, rollup, tick_m1
from marketdata import symbol_spec_snapshot as sss
from marketdata.dataset_registry import REGISTRY
from spread_series_fixture import (
    cleared_point_cache,  # noqa: F401  （import した先で autouse になる共有 fixture）
    day,
    put_day_of_spread_points,
    run_writer,
)

#: 対の spread 側の名前に付く接尾辞（先例 ``jp225_mt5`` ↔ ``jp225_mt5_spread`` の関係）。
_SPREAD_SUFFIX = "_spread"

#: Dukascopy 配信のティックであることを示すベンダ欄の値（台帳の語彙）。
_DUKASCOPY = "dukascopy"

#: 合成ティックの分ごとの気配幅（整数 points）。分内で幅が変わり、**最小は先頭でも末尾でもない**
#: （min を first / last / max へ変える変異がここで落ちる）。分によって最小値が変わるので、
#: 上位足の min が「どれか 1 本の写し」になる変異も落ちる。
_WIDTHS = tuple(
    (60 + (m % 7) + 3, 60 + (m % 7), 60 + (m % 7) + 5) for m in range(130)
)

#: 上位足を突き合わせる 2 段（1 段だけでは「M1 をそのまま運んだ」実装と区別できない）。
_HIGHER_TIMEFRAMES = [pytest.param("5m", 5, id="5m"), pytest.param("1h", 60, id="1h")]


def _pairs() -> "list[pytest.param]":
    """台帳に在る対（base, base+'_spread'）を宣言順に列挙する（綴りを書き写さない）。"""
    return [
        pytest.param(base, base + _SPREAD_SUFFIX, id=base)
        for base in REGISTRY
        if base + _SPREAD_SUFFIX in REGISTRY
    ]


def _dukascopy_tick_refs() -> "tuple[str, ...]":
    """Dukascopy のティックを読む ref を台帳の宣言順で導く。"""
    return tuple(
        ref for ref, d in REGISTRY.items() if d.tick and d.vendor == _DUKASCOPY
    )


def _dukascopy_spread_ref() -> str:
    """Dukascopy のティックから spread 列を作る ref（D-1 が 1 件であることを固定する）。"""
    declared = [
        ref for ref in _dukascopy_tick_refs()
        if dataset_registry.spread_point_snapshot_of(ref) is not None
    ]
    return declared[0]


def _dukascopy_plain_ref() -> str:
    """Dukascopy のティックから spread 列を作らない既存 ref（byte 一致の壁）。"""
    plain = [
        ref for ref in _dukascopy_tick_refs()
        if dataset_registry.spread_point_snapshot_of(ref) is None
    ]
    return plain[0]


def _columns(path: Path) -> "list[str]":
    """出力 CSV の先頭行を列名へ割る。"""
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")


def _spread_by_minute(path: Path) -> "dict[pd.Timestamp, int]":
    """M1 / 上位足 CSV の 「`date`」 列 → spread（int）。"""
    frame = pd.read_csv(path, parse_dates=["date"], index_col="date")
    return {ts: int(v) for ts, v in frame[csv_schema.SPREAD_COLUMN].items()}


def _narrowest_of_period(start: pd.Timestamp, minutes: int) -> int:
    """``start`` から ``minutes`` 分に属する合成ティックの気配幅の最小値（期待値の素）。"""
    first = int((start - day(0)) / pd.Timedelta(minutes=1))
    return min(
        min(_WIDTHS[m]) for m in range(first, min(first + minutes, len(_WIDTHS)))
    )


# =====================================================================
# D-1. Dukascopy 側に spread 列の系列が 1 件在り、既存 ref と対になる
# =====================================================================
def test_the_dukascopy_tick_tree_has_one_spread_bearing_sibling():
    """D-1: Dukascopy のティック ref は「宣言の無い 1 件」と「宣言の在る 1 件」の対である。

    綴りは書き写さない（ベンダ欄と宣言の有無から導く）。対の名前の関係だけを固定する。
    """
    # Arrange
    refs = _dukascopy_tick_refs()

    # Act
    declared = tuple(
        r for r in refs if dataset_registry.spread_point_snapshot_of(r) is not None
    )
    plain = tuple(
        r for r in refs if dataset_registry.spread_point_snapshot_of(r) is None
    )

    # Assert
    assert len(declared) == 1, f"spread を宣言する Dukascopy ref が {len(declared)} 件: {refs}"
    assert len(plain) == 1, f"宣言の無い Dukascopy ref が {len(plain)} 件: {refs}"
    assert declared[0] == plain[0] + _SPREAD_SUFFIX


# =====================================================================
# D-2 / D-3 / D-4. 対の記述子の関係（台帳に在る対すべてに掛かる）
# =====================================================================
@pytest.mark.parametrize(("base", "spread"), _pairs())
def test_a_spread_series_differs_from_its_base_only_in_storage_and_declaration(base, spread):
    """D-2: 対の記述子は置き場（path・series）と宣言以外のすべての欄が一致する。

    比較は記述子の全欄の差分なので、欄が 1 つ増えても比較対象に自動で入る（リテラルの対応表を
    書かない）。片方の木・基準・ベンダだけを動かす変異はここで落ちる。
    """
    # Arrange
    differ = {"path", "series", "spread_point_snapshot"}
    fields = [f.name for f in dataclasses.fields(dataset_registry.DatasetDescriptor)]
    shared = [name for name in fields if name not in differ]

    # Act
    got = {name: getattr(REGISTRY[spread], name) for name in shared}
    expected = {name: getattr(REGISTRY[base], name) for name in shared}

    # Assert
    assert set(differ) < set(fields)  # 空振り防止（除外名が実在する欄である）
    assert got == expected


@pytest.mark.parametrize(("base", "spread"), _pairs())
def test_a_spread_series_is_stored_beside_its_base(base, spread):
    """D-3: 置き場の名前は base の置き場 ＋ ``_spread``、実 CSV は ``<series>_m1.csv``。"""
    # Arrange / Act
    base_series = dataset_registry.series_of(base)
    spread_series = dataset_registry.series_of(spread)

    # Assert
    assert spread_series == base_series + _SPREAD_SUFFIX
    assert REGISTRY[spread].path.name == f"{spread_series}_m1.csv"
    assert REGISTRY[base].path.name == f"{base_series}_m1.csv"


@pytest.mark.parametrize(("base", "spread"), _pairs())
def test_only_the_spread_side_declares_where_its_point_comes_from(base, spread):
    """D-4: 宣言を持つのは spread 側だけで、宣言した組のスナップショットは実在する。"""
    # Arrange / Act
    declared = dataset_registry.spread_point_snapshot_of(spread)

    # Assert
    assert dataset_registry.spread_point_snapshot_of(base) is None
    assert sss.snapshot_path(*declared).is_file(), f"宣言した組のスナップショットが無い: {declared}"


def test_no_two_series_share_a_storage_name_or_a_csv_path():
    """D-5: 置き場の名前も実 CSV パスも台帳の中で一意（既存の置き場へ書き込まない）。"""
    # Arrange / Act
    series = [dataset_registry.series_of(ref) for ref in REGISTRY]
    paths = [d.path for d in REGISTRY.values()]

    # Assert
    assert len(series) == len(set(series)), f"置き場の名前が重複: {sorted(series)}"
    assert len(paths) == len(set(paths)), "実 CSV パスが重複している"


# =====================================================================
# D-6. M1 の spread はその分の最小気配幅
# =====================================================================
def test_the_new_series_writes_the_narrowest_quote_of_each_minute(tmp_path):
    """D-6: M1 の spread が分内**最小**の気配幅（整数 points）と一致する。

    期待値は検定が生成した整数の ``min`` である（割り算も丸めもしない）。first / last / max /
    mean へ変える変異は、分内で幅が変わる素材のもとでここに現れる。
    """
    # Arrange
    ref = _dukascopy_spread_ref()
    put_day_of_spread_points(tmp_path, day(0), _WIDTHS)

    # Act
    out = run_writer(tick_m1.build_m1_from_ticks, ref, tmp_path, day(0), day(0))

    # Assert
    expected = {
        day(0) + pd.Timedelta(minutes=m): min(widths)
        for m, widths in enumerate(_WIDTHS)
    }
    assert _columns(out)[-1] == csv_schema.SPREAD_COLUMN  # 空振り防止（列が在る）
    assert _spread_by_minute(out) == expected


# =====================================================================
# D-7. 上位足の spread は M1 の最小値（2 段）
# =====================================================================
@pytest.mark.parametrize(("timeframe", "minutes"), _HIGHER_TIMEFRAMES)
def test_a_higher_timeframe_of_the_new_series_carries_the_narrowest_minute(
    tmp_path, timeframe, minutes
):
    """D-7: 上位足の spread は、その period に属する M1 の spread の最小値と一致する。"""
    # Arrange
    ref = _dukascopy_spread_ref()
    put_day_of_spread_points(tmp_path, day(0), _WIDTHS)
    m1_path = run_writer(tick_m1.build_m1_from_ticks, ref, tmp_path, day(0), day(0))
    out_dir = tmp_path / "rollups"

    # Act
    rollup.stream_build(
        m1_path, [timeframe], out_dir,
        ref_prefix=dataset_registry.series_of(ref), save_state=False,
    )

    # Assert
    got = _spread_by_minute(out_dir / f"{dataset_registry.series_of(ref)}_{timeframe}.csv")
    expected = {ts: _narrowest_of_period(ts, minutes) for ts in got}
    assert len(got) >= 2, f"{timeframe} の確定 period が {len(got)} 本（突合できていない）"
    assert got == expected


# =====================================================================
# D-8. 既存系列の出力は 1 バイトも変わらない
# =====================================================================
def test_adding_the_new_descriptor_leaves_the_existing_series_byte_identical(
    tmp_path, monkeypatch
):
    """D-8: 記述子を足した世界と外した世界で、既存系列の M1 は byte 一致である。

    同じ合成ティックから同じ既存 ref を書き、台帳から新記述子を外しただけの世界と突き合わせる。
    新しい宣言が既存系列の列形・値・書式のどれかへ漏れれば、ここで byte が割れる。
    """
    # Arrange
    existing = _dukascopy_plain_ref()
    new_ref = _dukascopy_spread_ref()
    with_entry_dir = tmp_path / "with"
    without_entry_dir = tmp_path / "without"
    put_day_of_spread_points(with_entry_dir, day(0), _WIDTHS)
    put_day_of_spread_points(without_entry_dir, day(0), _WIDTHS)

    # Act
    with_entry = run_writer(
        tick_m1.build_m1_from_ticks, existing, with_entry_dir, day(0), day(0)
    )
    # 記述子を外した世界は**台帳を差し替えて**作る（実物の dict から消して戻すと、戻った鍵が
    #   末尾へ回って宣言順が変わる＝順序を写す生成物の検定が後続で落ちる。2026-09-26 実測）。
    monkeypatch.setattr(
        dataset_registry, "REGISTRY",
        {ref: d for ref, d in REGISTRY.items() if ref != new_ref},
    )
    without_entry = run_writer(
        tick_m1.build_m1_from_ticks, existing, without_entry_dir, day(0), day(0)
    )

    # Assert
    assert csv_schema.SPREAD_COLUMN not in _columns(with_entry)
    assert with_entry.read_bytes() == without_entry.read_bytes()
