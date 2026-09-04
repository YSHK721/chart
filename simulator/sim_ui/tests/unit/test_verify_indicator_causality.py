"""UC-S3 因果性検定（案 i と案 ii の全バー突合・束契約）の単体検定。

基本設計書 §3.5.4 の裁定: 供給は案 ii（供給窓を 1 回計算）を既定とし、**案 i（バーごとに
until_time で truncate して逐次計算）を照合基準（golden）**とする。全バーで一致した系列
だけを sim モードで選択可能にし、不一致は選択不可として明示する（無音で誤った値を使わない）。

固定する判定規則（2026-08-11 契約改訂裁定 A/C）:
    1. 系列名の集合が案 i と案 ii で違えば**比較不能**（値のずれではない）。
    2. 片側にのみ点が存在する時刻・時刻のずれた点も比較不能。
    3. 値は既定で厳密一致（tolerance=0.0）。tolerance は測定条件として明示的に渡す。
    4. ``None``（未定義値）同士は一致・片側だけ ``None`` は不一致。
    5. 案 i は各バー時刻を until_time として問い合わせる（因果順守）。
    6. 比較したバーが 0 本のとき一致を主張しない（fail-closed）。
    7. 判定は**系列ごと**（1 系列の不一致で他系列を巻き添えにしない）。
    8. **1 バーにつき案 i の計算は 1 回**（束契約。系列ごとに呼ぶと同じ計算を重複して払う）。
    9. 予算（timeout）超過は「一致」とも「不一致」とも書かず verification_incomplete。
   10. reason は 3 値固定（自由文は detail）。

方式: 合成データ（`FakeCausalSeriesProbe`）のみ。実データ・indicator_ui へは触れない。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.tests.integration._fake_indicator_ports import (
    FakeCausalSeriesProbe,
)
from simulator.sim_ui.usecase.indicator_models import (
    REASON_MISMATCH,
    REASON_VERIFICATION_INCOMPLETE,
    CausalityComparisonError,
    IndicatorSpec,
)
from simulator.sim_ui.usecase.verify_indicator_causality import (
    measure_supply_cost,
    verify_indicator_causality,
)

_BARS = [100, 160, 220]
_SPEC = IndicatorSpec(indicator="moving_averages", variant="default", params={})


def _probe(full, upto, **kwargs) -> FakeCausalSeriesProbe:
    return FakeCausalSeriesProbe(full=full, upto=upto, **kwargs)


def _single(full_points, upto_table, **kwargs) -> FakeCausalSeriesProbe:
    return _probe({"MA": full_points}, {"MA": upto_table}, **kwargs)


def _verify(probe, bar_times=_BARS, **kwargs):
    return verify_indicator_causality(
        spec=_SPEC, ref="jp225", timeframe="5m", bar_times=bar_times,
        probe=probe, **kwargs,
    )


def _by_name(findings) -> dict:
    return {f.series_name: f for f in findings}


# --- 1. 一致（正常系）-----------------------------------------------------

def test_全バー一致なら選択可能になる() -> None:
    # Arrange
    probe = _single([(100, 1.0), (160, 2.0), (220, 3.0)], {100: 1.0, 160: 2.0, 220: 3.0})
    # Act
    finding = _by_name(_verify(probe))["MA"]
    # Assert
    assert finding.selectable is True
    assert finding.reason is None
    assert finding.bars_compared == 3
    assert finding.max_abs_diff == 0.0
    assert finding.first_mismatch_time is None


def test_単一バーでも突合できる() -> None:
    """境界値: バー 1 本。"""
    # Arrange
    probe = _single([(100, 1.0)], {100: 1.0})
    # Act
    finding = _by_name(_verify(probe, bar_times=[100]))["MA"]
    # Assert
    assert finding.selectable is True
    assert finding.bars_compared == 1


def test_案iは各バー時刻をuntil_timeとして問い合わせる() -> None:
    """規則 5。未来バーが窓に入らないのは until_time で切るから。"""
    # Arrange
    probe = _single([(100, 1.0), (160, 2.0), (220, 3.0)], {100: 1.0, 160: 2.0, 220: 3.0})
    # Act
    _verify(probe)
    # Assert
    assert probe.upto_calls == _BARS


def test_案iの計算は1バーにつき1回だけ() -> None:
    """規則 8（束契約）。系列ごとに呼ぶと同じ計算を系列数ぶん重複して払う。"""
    # Arrange（3 系列を持つ指標）
    probe = _probe(
        {name: [(t, float(t)) for t in _BARS] for name in ("A", "B", "C")},
        {name: {t: float(t) for t in _BARS} for name in ("A", "B", "C")},
    )
    # Act
    findings = _verify(probe)
    # Assert
    assert len(findings) == 3
    assert len(probe.upto_calls) == len(_BARS)   # 系列数（3）倍にならない
    assert probe.full_calls == [_BARS[-1]]       # 案 ii も 1 回


def test_案iiは案iの最終窓と同じ時刻で計算される() -> None:
    """prefix 関係。窓の左端を動かさないため、案 ii の until は案 i の最終 until と同じ。"""
    # Arrange
    probe = _single([(100, 1.0), (160, 2.0), (220, 3.0)], {100: 1.0, 160: 2.0, 220: 3.0})
    # Act
    _verify(probe, bar_times=[100, 160])
    # Assert
    assert probe.full_calls == [160]


# --- 2. 不一致（規則 3・10）-----------------------------------------------

def test_値が異なるバーがあれば選択不可になる() -> None:
    # Arrange（2 本目が 0.5 ずれる）
    probe = _single([(100, 1.0), (160, 2.0), (220, 3.0)], {100: 1.0, 160: 2.5, 220: 3.0})
    # Act
    finding = _by_name(_verify(probe))["MA"]
    # Assert
    assert finding.selectable is False
    assert finding.reason == REASON_MISMATCH
    assert finding.first_mismatch_time == 160
    assert finding.max_abs_diff == pytest.approx(0.5)
    assert "160" in finding.detail


def test_既定toleranceは0で微小差も不一致にする() -> None:
    """規則 3。厳密一致を既定にする（tolerance は測定条件として台帳に残す）。"""
    # Arrange
    probe = _single([(100, 1.0)], {100: 1.0 + 1e-12})
    # Act
    finding = _by_name(_verify(probe, bar_times=[100]))["MA"]
    # Assert
    assert finding.selectable is False
    assert finding.reason == REASON_MISMATCH


def test_toleranceを与えればその範囲の差は一致扱い() -> None:
    # Arrange
    probe = _single([(100, 1.0)], {100: 1.0 + 1e-12})
    # Act
    finding = _by_name(_verify(probe, bar_times=[100], tolerance=1e-9))["MA"]
    # Assert
    assert finding.selectable is True


# --- 3. 未定義値の扱い（規則 4）--------------------------------------------

def test_未定義値同士は一致とみなす() -> None:
    # Arrange
    probe = _single([(100, None), (160, 2.0)], {100: None, 160: 2.0})
    # Act
    finding = _by_name(_verify(probe, bar_times=[100, 160]))["MA"]
    # Assert
    assert finding.selectable is True
    assert finding.bars_compared == 2


def test_片側だけ未定義値なら不一致() -> None:
    # Arrange
    probe = _single([(100, None)], {100: 1.0})
    # Act
    finding = _by_name(_verify(probe, bar_times=[100]))["MA"]
    # Assert
    assert finding.selectable is False
    assert finding.reason == REASON_MISMATCH
    assert finding.first_mismatch_time == 100


def test_値がNaNの点は一致を主張しない() -> None:
    """NaN が突合へ到達しても fail-open にしない（比較できない＝不一致側へ倒す）。

    現行 probe は NaN を ``None`` へ正規化するため到達しないが、正規化は adapter の
    実装詳細であり、突合規則の側で機械的に塞ぐ。塞がないと ``nan > tolerance`` も
    ``nan > max_abs_diff`` も False になり、**全バー乖離していても
    selectable=True / max_abs_diff=0.0** と記録される（2026-08-11 実測で再現）。
    """
    # Arrange（案 ii は実数・案 i は全バー NaN）
    nan = float("nan")
    probe = _single([(100, 110.0), (160, 120.0), (220, 130.0)],
                    {100: nan, 160: nan, 220: nan})
    # Act
    finding = _by_name(_verify(probe))["MA"]
    # Assert
    assert finding.selectable is False
    assert finding.reason == REASON_MISMATCH
    assert finding.first_mismatch_time == 100


def test_案iiの値がNaNでも一致を主張しない() -> None:
    """境界値: 逆向き（案 ii 側が NaN）。どちらから来ても比較不能は不一致側。"""
    # Arrange
    nan = float("nan")
    probe = _single([(100, nan)], {100: 110.0})
    # Act
    finding = _by_name(_verify(probe, bar_times=[100]))["MA"]
    # Assert
    assert finding.selectable is False
    assert finding.reason == REASON_MISMATCH


def test_両側NaNでも一致を主張しない() -> None:
    """境界値: 両側 NaN。``None``（未定義点）と違い NaN は「比較できない」。"""
    # Arrange
    nan = float("nan")
    probe = _single([(100, nan)], {100: nan})
    # Act
    finding = _by_name(_verify(probe, bar_times=[100]))["MA"]
    # Assert
    assert finding.selectable is False
    assert finding.reason == REASON_MISMATCH


def test_未定義値同士の差はmax_abs_diffを汚さない() -> None:
    # Arrange
    probe = _single([(100, None), (160, 2.0)], {100: None, 160: 2.0})
    # Act
    finding = _by_name(_verify(probe, bar_times=[100, 160]))["MA"]
    # Assert
    assert finding.max_abs_diff == 0.0


# --- 4. 比較不能（規則 1・2）----------------------------------------------

def test_案iiにだけ点がある時刻は比較不能エラー() -> None:
    """規則を緩めるのは先頭 warmup prefix だけ（出し始めたあとの欠落は通さない）。"""
    # Arrange（100 で案 i は点を出しており、160 の点だけが無い）
    probe = _single([(100, 1.0), (160, 2.0)], {100: 1.0})
    # Act / Assert
    with pytest.raises(CausalityComparisonError) as exc:
        _verify(probe, bar_times=[100, 160])
    assert "160" in str(exc.value)


def test_案iにだけ点がある時刻は比較不能エラー() -> None:
    # Arrange（案 ii は 160 の点を持たない）
    probe = _single([(100, 1.0)], {100: 1.0, 160: 2.0})
    # Act / Assert
    with pytest.raises(CausalityComparisonError):
        _verify(probe, bar_times=[100, 160])


def test_案iの点の時刻が対象バーと違えば比較不能エラー() -> None:
    """時間軸不一致（規則 2）。ずれた点を突合すると無音で誤判定になる。"""
    # Arrange（160 を問うと 100 の点が返る）
    probe = _single([(100, 1.0), (160, 2.0)], {100: 1.0, 160: (100, 1.0)})
    # Act / Assert
    with pytest.raises(CausalityComparisonError):
        _verify(probe, bar_times=[100, 160])


def test_案iにしかない系列があれば比較不能エラー() -> None:
    """規則 1。同じものを比べていない状態を「不一致」と記録しない。"""
    # Arrange（案 ii は A だけ、案 i は A・B）
    probe = _probe(
        {"A": [(100, 1.0)]},
        {"A": {100: 1.0}, "B": {100: 2.0}},
        upto_names=["A", "B"],
    )
    # Act / Assert
    with pytest.raises(CausalityComparisonError) as exc:
        _verify(probe, bar_times=[100])
    assert "B" in str(exc.value)


def test_案iに現れない系列はその時刻の点なしとして扱う() -> None:
    """規則 1。窓が短いうちは compute が系列そのものを返さない（2026-08-11 実測）。

    ここを比較不能にすると検定は必ず先頭バーで倒れ、全系列が選択不可になる。
    """
    # Arrange（220 までは案 i に系列が現れない＝案 ii の点も 220 から）
    probe = _single([(220, 3.0)], {220: 3.0}, appear_at=220)
    # Act
    finding = _by_name(_verify(probe))["MA"]
    # Assert
    assert finding.selectable is True
    assert finding.bars_compared == 1


def test_先頭warmupは比較対象外として数える() -> None:
    """規則 2。案 i が値を出せる本数に達するまでの先頭区間（2026-08-11 実測の形）。

    案 ii は先頭バーから点を持つが、案 i は窓が短いうちは pane すら返さない。
    ここを比較不能にすると検定は必ずデータセット先頭で倒れる。隠さずに数えて残す。
    """
    # Arrange（案 i は 220 から点を出す・案 ii は 100 から点を持つ）
    probe = _single([(100, 1.0), (160, 2.0), (220, 3.0)], {220: 3.0}, appear_at=220)
    # Act
    finding = _by_name(_verify(probe))["MA"]
    # Assert
    assert finding.selectable is True
    assert finding.warmup_bars == 2
    assert finding.bars_compared == 1




def test_両側とも点が無いバーは比較対象外() -> None:
    """warmup 区間。どちらも値を出さないのは不一致ではない。"""
    # Arrange
    probe = _single([(160, 2.0), (220, 3.0)], {160: 2.0, 220: 3.0})
    # Act
    finding = _by_name(_verify(probe))["MA"]
    # Assert
    assert finding.selectable is True
    assert finding.bars_compared == 2


# --- 5. fail-closed（規則 6）-----------------------------------------------

def test_バー列が空なら結果を返さない() -> None:
    """境界値: 空窓。測っていないものを台帳に載せない。"""
    # Arrange
    probe = _single([], {})
    # Act
    findings = _verify(probe, bar_times=[])
    # Assert
    assert findings == []
    assert probe.full_calls == []   # 束すら取りに行かない


def test_系列が1つも無い指標は結果を持たない() -> None:
    """境界値: 供給できる系列 0 件（レジストリへ入れるものが無い）。"""
    # Arrange
    probe = _probe({}, {})
    # Act
    findings = _verify(probe)
    # Assert
    assert findings == []


def test_点が1つも無い系列は選択可能を主張しない() -> None:
    """境界値: 系列はあるが点が 0（比較 0 本）。"""
    # Arrange
    probe = _probe({"MA": []}, {"MA": {}})
    # Act
    finding = _by_name(_verify(probe))["MA"]
    # Assert
    assert finding.selectable is False
    assert finding.reason == REASON_VERIFICATION_INCOMPLETE
    assert finding.bars_compared == 0


# --- 6. 系列ごとの独立判定（規則 7）----------------------------------------

def _multi_probe() -> FakeCausalSeriesProbe:
    return FakeCausalSeriesProbe(
        full={
            "UPPER": [(100, 1.0), (160, 2.0)],
            "LOWER": [(100, -1.0), (160, -2.0)],
        },
        upto={
            "UPPER": {100: 1.0, 160: 2.0},
            "LOWER": {100: -1.0, 160: -2.0},
        },
    )


def test_全系列が一致すれば全系列が選択可能() -> None:
    # Arrange
    probe = _multi_probe()
    # Act
    findings = _by_name(_verify(probe, bar_times=[100, 160]))
    # Assert
    assert {name: f.selectable for name, f in findings.items()} == {
        "UPPER": True, "LOWER": True
    }


def test_不一致の系列だけが選択不可になる() -> None:
    """規則 7。使える系列を巻き添えで落とさない（戦略は系列名で参照する）。"""
    # Arrange
    probe = _multi_probe()
    probe.upto["LOWER"][160] = -2.5
    # Act
    findings = _by_name(_verify(probe, bar_times=[100, 160]))
    # Assert
    assert findings["UPPER"].selectable is True
    assert findings["LOWER"].selectable is False
    assert findings["LOWER"].reason == REASON_MISMATCH
    assert findings["LOWER"].max_abs_diff == pytest.approx(0.5)
    assert findings["LOWER"].first_mismatch_time == 160


# --- 7. 予算超過（規則 9）--------------------------------------------------

def test_予算超過は一致とも不一致とも書かない() -> None:
    # Arrange
    probe = _multi_probe()
    # Act（予算 0 秒＝1 本目を測った時点で打ち切り）
    findings = _by_name(_verify(probe, bar_times=[100, 160], timeout=0.0))
    # Assert
    assert all(f.selectable is False for f in findings.values())
    assert {f.reason for f in findings.values()} == {REASON_VERIFICATION_INCOMPLETE}
    assert all("予算" in f.detail for f in findings.values())
    assert all(f.bars_compared == 1 for f in findings.values())  # 測れたぶんは残す


def test_予算超過でも既に確定した不一致はmismatchのまま() -> None:
    """原因の語彙をすり替えない（値がずれたことは測れている）。"""
    # Arrange（1 本目で不一致）
    probe = _probe({"MA": [(100, 1.0), (160, 2.0)]}, {"MA": {100: 9.0, 160: 2.0}})
    # Act（1 本目の比較後に予算切れ）
    findings = _by_name(_verify(probe, bar_times=[100, 160], timeout=0.0))
    # Assert
    assert findings["MA"].reason == REASON_MISMATCH


def test_最終バーまで測り切れば予算超過でも打ち切りにしない() -> None:
    """境界値: 残り 0 本での超過。測り切ったものを「測り切れなかった」と書かない。"""
    # Arrange
    probe = _single([(100, 1.0)], {100: 1.0})
    # Act
    finding = _by_name(_verify(probe, bar_times=[100], timeout=0.0))["MA"]
    # Assert
    assert finding.selectable is True


# --- 8. 供給コストの実測（段 0）-------------------------------------------

def test_供給コストは案iiの1回計算を測る() -> None:
    # Arrange
    probe = _single([(100, 1.0), (160, 2.0)], {})
    # Act
    cost = measure_supply_cost(
        spec=_SPEC, ref="jp225", timeframe="5m", until_time=160, probe=probe
    )
    # Assert
    assert cost.seconds >= 0.0
    assert probe.full_calls == [160]
    assert sorted(cost.bundle) == ["MA"]
    assert [p.time for p in cost.bundle["MA"]] == [100, 160]


def test_供給コストの束は除外系列名を持つ() -> None:
    """裁定 A: 時系列でない kind は供給しない。除外したことを台帳へ残す。"""
    # Arrange
    probe = _single([(100, 1.0)], {}, excluded=["LEVEL"])
    # Act
    cost = measure_supply_cost(
        spec=_SPEC, ref="jp225", timeframe="5m", until_time=100, probe=probe
    )
    # Assert
    assert cost.bundle.excluded == ("LEVEL",)
