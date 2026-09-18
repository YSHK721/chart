"""spread 依存 EA 3 本の構築入力が、EA 名ではなく**データ実体の形式**から解決されること
（ISSUE-511 段階 8-B）。

固定する不変条件:

    1. 3 本とも spread 付き marketdata 形式から `(strategy, registry, market_data)` を
       組める。`market_data` は `MarketdataCsvOHLCRepository`（形式の権威はヘッダ）。
    2. 気配幅の系列の値は MT5 TAB 形式で組んでも marketdata 形式で組んでも**同じ意味**
       （整数 points）であり、値が一致する。
    3. MT5 TAB 形式は従来どおり `Mt5CsvOHLCRepository` へ解決する（fixture 経路無改変）。
    4. 形式不明・必要列欠落は `DataError` で Fail-Stop する（既定へ沈黙縮退しない）。
       是正前は pandas の KeyError が翻訳されずに裸で抜けていた。
    5. **保証境界は気配幅の供給で決まる**（段階 8-C で述語を置換）: N-17 の発火表は
       marketdata 6 列で発火・marketdata 9 列で非発火・MT5 TAB で非発火。段階 8-B の
       時点では述語が「形式 == marketdata」で代理していたため 9 列でも発火していた
       ——その 1 マスだけが段階 8-C で動く。

なぜ 5 を本ファイルで測るか: 構築側（形式非依存）と保証境界（気配幅の供給）は別の関心
であり、片方を変えたときにもう片方が黙って動いていないことを同じ段で機械的に示す。
"""
from __future__ import annotations

import pytest

from simulator.adapter.repository.ohlc_marketdata_csv import MarketdataCsvOHLCRepository
from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from simulator.domain.exceptions import DataError
from simulator.main.ea_bindings import build_ea_components
from simulator.main.tester_settings.unsupported import NOT_VIOLATED
from simulator.tests.unit.test_unsupported_spread_dependency import _detect

#: 3 本が読む系列（各 EA モジュールの registry ビルダの実測。MA_Slope は ema のみ）。
_EA_SERIES = {
    "MA_Slope_EA": ("ema",),
    "MA_Slope_Pending_EA": ("ema", "open", "spread"),
    "StopEntryProbe_EA": ("ema", "open", "spread"),
}

#: registry に気配幅の系列を載せる EA（データの当該列を必要とする 2 本）。
_SPREAD_SERIES_EAS = ("MA_Slope_Pending_EA", "StopEntryProbe_EA")

#: 3 形式で同一の値を表す合成 3 行（気配幅は整数 points）。
_SPREADS = (71, 72, 70)
_OHLC = ((100.0, 101.0, 99.0, 100.5), (100.5, 102.0, 100.0, 101.0), (101.0, 103.0, 101.0, 102.0))

_MT5_HEADER = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


def _mt5_csv(tmp_path):
    """MT5 エクスポート形式（タブ区切り・気配幅列つき）。"""
    rows = [
        f"2024.01.08\t00:{i:02d}:00\t{o}\t{h}\t{low}\t{c}\t1\t0\t{spread}"
        for i, ((o, h, low, c), spread) in enumerate(zip(_OHLC, _SPREADS))
    ]
    return _write(tmp_path / "mt5.csv", "\n".join([_MT5_HEADER, *rows]) + "\n")


def _marketdata_csv(tmp_path):
    """気配幅の列を持つ marketdata 形式（ISSUE-511 段階 3 の新系列と同じ 9 列）。"""
    rows = [
        f"2024-01-08 00:{i:02d}:00,{o},{h},{low},{c},1.0,0.0,0.0,{spread}"
        for i, ((o, h, low, c), spread) in enumerate(zip(_OHLC, _SPREADS))
    ]
    header = "date,open,high,low,close,volume,up,dn,spread"
    return _write(tmp_path / "md_spread.csv", "\n".join([header, *rows]) + "\n")


def _marketdata_csv_without_spread(tmp_path):
    """気配幅の列を持たない marketdata 形式（現行の実行データセットと同じ 6 列）。"""
    rows = [
        f"2024-01-08 00:{i:02d}:00,{o},{h},{low},{c},1.0"
        for i, (o, h, low, c) in enumerate(_OHLC)
    ]
    header = "date,open,high,low,close,volume"
    return _write(tmp_path / "md_plain.csv", "\n".join([header, *rows]) + "\n")


def _unknown_form_csv(tmp_path):
    """どの形式でもないヘッダ（形式判定が "" を返す実体）。"""
    return _write(tmp_path / "unknown.csv", "alpha,beta\n1,2\n")


def _build(ea_name, csv_path):
    """EA 束縛を実際に呼んで `(strategy, registry, market_data)` を得る。"""
    return build_ea_components(
        ea_name, tick_model="open_only", data_path=csv_path, params={"ma_period": 2}
    )


def _series_values(registry, name):
    return [float(v) for v in registry.get(name)]


# --- 1. 気配幅つき marketdata 形式から 3 本が組める（R-3）--------------------------


@pytest.mark.parametrize("ea_name", sorted(_EA_SERIES))
def test_each_spread_dependent_ea_builds_from_the_marketdata_form(ea_name, tmp_path):
    # Arrange
    csv_path = _marketdata_csv(tmp_path)
    # Act
    strategy, registry, market_data = _build(ea_name, csv_path)
    # Assert: 読み手は形式から解決される（EA 名で固定しない）。
    assert isinstance(market_data, MarketdataCsvOHLCRepository)
    assert strategy is not None
    # Assert: その EA が読む系列が全行ぶん揃う（欠けたら実行時に KeyError になる）。
    assert [len(_series_values(registry, n)) for n in _EA_SERIES[ea_name]] == [
        len(_OHLC)
    ] * len(_EA_SERIES[ea_name])


@pytest.mark.parametrize("ea_name", sorted(_EA_SERIES))
def test_each_spread_dependent_ea_still_builds_from_the_mt5_form(ea_name, tmp_path):
    """MT5 TAB 形式は従来どおり MT5 リーダへ解決する（突合 fixture 経路の無改変）。"""
    # Arrange
    csv_path = _mt5_csv(tmp_path)
    # Act
    _strategy, registry, market_data = _build(ea_name, csv_path)
    # Assert
    assert isinstance(market_data, Mt5CsvOHLCRepository)
    assert [len(_series_values(registry, n)) for n in _EA_SERIES[ea_name]] == [
        len(_OHLC)
    ] * len(_EA_SERIES[ea_name])


# --- 2. 気配幅の意味が形式に依らない（R-4）----------------------------------------


@pytest.mark.parametrize("ea_name", _SPREAD_SERIES_EAS)
def test_the_spread_series_has_the_same_meaning_in_both_forms(ea_name, tmp_path):
    """registry の気配幅の系列は両形式で同じ整数 points を載せる。

    値がずれると約定価格式 ask = open + spread×point が形式ごとに別物になる。
    """
    # Arrange / Act
    _s1, mt5_registry, _m1 = _build(ea_name, _mt5_csv(tmp_path))
    _s2, md_registry, _m2 = _build(ea_name, _marketdata_csv(tmp_path))
    # Assert: 宣言した合成値（整数 points）と一致し、かつ両形式で一致する。
    assert _series_values(mt5_registry, "spread") == [float(s) for s in _SPREADS]
    assert _series_values(md_registry, "spread") == [float(s) for s in _SPREADS]


@pytest.mark.parametrize("ea_name", _SPREAD_SERIES_EAS)
def test_the_open_series_has_the_same_values_in_both_forms(ea_name, tmp_path):
    """ペンディング価格の基準（始値の系列）も形式で変わらない。"""
    # Arrange / Act
    _s1, mt5_registry, _m1 = _build(ea_name, _mt5_csv(tmp_path))
    _s2, md_registry, _m2 = _build(ea_name, _marketdata_csv(tmp_path))
    # Assert
    expected = [o for (o, _h, _l, _c) in _OHLC]
    assert _series_values(mt5_registry, "open") == expected
    assert _series_values(md_registry, "open") == expected


# --- 3. Fail-Stop（R-6）-----------------------------------------------------------


@pytest.mark.parametrize("ea_name", sorted(_EA_SERIES))
def test_an_unknown_form_is_refused_instead_of_falling_back(ea_name, tmp_path):
    """形式不明の実体は既定形式へ黙って倒さず `DataError` で止まる。"""
    # Arrange
    csv_path = _unknown_form_csv(tmp_path)
    # Act / Assert
    with pytest.raises(DataError):
        _build(ea_name, csv_path)


@pytest.mark.parametrize("ea_name", _SPREAD_SERIES_EAS)
def test_a_missing_spread_column_is_refused_instead_of_defaulting_to_zero(
    ea_name, tmp_path
):
    """気配幅の系列を要る EA に当該列の無いデータを渡したら `DataError`。

    既定 0 で補うと「気配幅 0 の約定」が正常値として出力され、状態検証では落ちない。
    """
    # Arrange
    csv_path = _marketdata_csv_without_spread(tmp_path)
    # Act / Assert
    with pytest.raises(DataError):
        _build(ea_name, csv_path)


def test_ma_slope_builds_on_spreadless_marketdata_because_it_reads_no_spread_series(
    tmp_path,
):
    """MA_Slope は registry に気配幅を持たないため、構築側では止まらない。

    止めるのは構築側ではなく保証境界（N-17）である、という役割分担を固定する
    （下の発火表がその境界を測る）。
    """
    # Arrange
    csv_path = _marketdata_csv_without_spread(tmp_path)
    # Act
    _strategy, registry, market_data = _build("MA_Slope_EA", csv_path)
    # Assert
    assert isinstance(market_data, MarketdataCsvOHLCRepository)
    assert len(_series_values(registry, "ema")) == len(_OHLC)


# --- 4. 保証境界（N-17）は閉じたまま ---------------------------------------------


def _fires(ea_name, csv_path):
    """N-17 の判定結果を「発火したか」へ畳む（判定式そのものは宣言側が持つ）。"""
    return _detect(f"{ea_name}.ex5", csv_path) is not NOT_VIOLATED


@pytest.mark.parametrize("ea_name", sorted(_EA_SERIES))
def test_the_guarantee_boundary_follows_whether_the_entity_supplies_spread(
    ea_name, tmp_path
):
    """3 本 × 3 形式の発火表（段階 8-C の述語）。

    気配幅を供給する実体（MT5 TAB・marketdata 9 列）では非発火、供給しない実体
    （marketdata 6 列）では発火する。同じ形式で答えが割れる組（marketdata 9 列 /
    6 列）が、判定が形式ではなく**気配幅の供給**で決まっていることを示す。
    段階 8-B までは述語が形式の代理だったため 9 列でも発火していた。
    """
    # Arrange
    forms = {
        "mt5_tab": _mt5_csv(tmp_path),
        "marketdata_with_spread": _marketdata_csv(tmp_path),
        "marketdata_without_spread": _marketdata_csv_without_spread(tmp_path),
    }
    # Act
    measured = {name: _fires(ea_name, path) for name, path in forms.items()}
    # Assert
    assert measured == {
        "mt5_tab": False,
        "marketdata_with_spread": False,
        "marketdata_without_spread": True,
    }
