"""日中の畳み口 ``tick_m1.fold_ticks_for`` の検証順序と point 解決の計算量（ISSUE-511 段階 3 の段階 5・工程 6）。

用語（初出定義）:
    継ぎ目（スナップショット読込）
        ＝ ``marketdata.symbol_spec_snapshot.load_snapshot``。宣言（台帳記述子の
          ``spread_point_snapshot``）の point を解決するとき**だけ**通る唯一の読込口である
          （``marketdata.spread_point._point_size_of_snapshot`` →
          ``marketdata.symbol_spec_snapshot.load_symbol_field`` → ここ）。
    発行 / 使用
        発行 ＝ 上記継ぎ目の呼出。使用 ＝ 読んだ組の point が、出力の spread 列を数える唯一の口
          （``marketdata.quote_spread.minute_spread_points``）へ渡ったこと。数え方は
          ``spread_series_fixture.used_reads``（同じ組を 2 回読んでも使用は 1）。

本検定が固定するもの:
  R-16 **検証が point の解決より先に走る**。必須列を欠く非空フレームでは ``ValueError`` で止まり、
       スナップショットを 1 回も読まない。素材化の唯一源 ``tick_m1.materialize_m1_day`` と**同じ
       順序**であることを、同じ入力を両口へ通して突き合わせる。
       なぜ型ではなく順序を固定するか: 読んでから落ちても出力は同じ（どちらも ``ValueError``）なので、
       状態検証では順序の非対称を検出できない。段階 7 で台帳に宣言が入ると、この 1 回は実在の
       スナップショット読込（IO）になる。
  CX-I-1 空入力（境界値）では発行 0。出力が 0 行なら使う point も 0 であり、
       発行 − 使用 = 0 が成り立つ唯一の形が発行 0 である。
  CX-I-2 非空では発行 − 使用 = 0、かつ**入力を増やしても発行が増えない**（閉じた分 2 と 20 の 2 点）。
       回数そのものは期待値に焼き込まない（期待値は観測した使用から導く）。

本検定が固定しないもの（射程の明示）:
  - 列形の照合そのもの（``tick_m1._checked_series``）。それは
    ``marketdata/tests/test_mt5_m1_chain_ledger_series.py`` の R-15 / R-15b が持つ。
  - 台帳への宣言の投入（段階 7）。本ファイルは合成 ref を一時登録して測る。

書込はすべて ``tmp_path``（data_dir を必ず渡す）。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from marketdata import spread_point as sp
from marketdata import tick_m1
from spread_series_fixture import (
    SNAPSHOT_PAIR,
    cleared_point_cache,  # noqa: F401  （import した先で autouse になる共有 fixture）
    register_tick_ref,
    spy_snapshot_reads,
    spy_spent_points,
    used_reads,
)

#: 合成 ref（宣言あり / 宣言なし）。台帳へ一時登録する。
_DECLARED = "zz_fold_declared"
_UNDECLARED = "zz_fold_undeclared"

#: 価格基準は渡さない（合成 ref は 2 つとも台帳へ一時登録する＝台帳が唯一の源・段階 6・V-3）。
_PER_MINUTE = 4
_T0 = pd.Timestamp("2026-09-01 09:00:00")


def _frame(minutes: int, per_minute: int = _PER_MINUTE) -> pd.DataFrame:
    """``minutes`` 分ぶんのティック（気配幅は分ごとに違う＝定数列では空振りする）。"""
    rows = []
    for m in range(minutes):
        for i in range(per_minute):
            bid = 66000.0 + m * 2.0 + i * 0.1
            rows.append((
                _T0 + pd.Timedelta(minutes=m, seconds=i * (60 // per_minute)),
                bid,
                bid + 7.0 + (m % 3) * 0.5 + i * 0.1,
            ))
    return pd.DataFrame(rows, columns=["timestamp", "bidPrice", "askPrice"])


# =====================================================================
# R-16 検証が point の解決より先（順序は materialize_m1_day と同じ）
# =====================================================================
@pytest.mark.parametrize("dropped", ["bidPrice", "askPrice", "timestamp"])
def test_r16_an_invalid_non_empty_frame_is_refused_before_the_point_is_resolved(
    tmp_path, monkeypatch, dropped
):
    """R-16: 必須列を欠く非空フレームは、スナップショットを読む前に ``ValueError`` で止まる。

    畳む分が 1 つも出ない入力で point を解決すると、使わない読込を発行することになる
    （ISSUE-450 と同型・出力は同じ ``ValueError`` なので状態検証では落ちない）。
    """
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _DECLARED, SNAPSHOT_PAIR)
    ticks = _frame(minutes=3).drop(columns=[dropped])
    reads = spy_snapshot_reads(monkeypatch)
    spent = spy_spent_points(monkeypatch)

    # Act
    with pytest.raises(ValueError):
        tick_m1.fold_ticks_for(ticks, ref=_DECLARED, data_dir=tmp_path)

    # Assert: 出力が無い＝使用 0。発行 − 使用 = 0 が成り立つのは発行 0 のときだけ。
    used = used_reads(reads, spent)
    assert (used, len(spent)) == (0, 0)          # 観測（出力に使った point は無い）
    assert len(reads) - used == 0, (
        f"必須列 {dropped} を欠く非空フレームでスナップショットを {len(reads)} 回読みました"
        "（検証より先に point を解決しています）。"
    )


@pytest.mark.parametrize("dropped", ["bidPrice", "timestamp"])
def test_r16_the_two_public_entries_refuse_in_the_same_order(tmp_path, monkeypatch, dropped):
    """R-16 の対称性: 同じ不正入力で、日中の畳み口と素材化の唯一源の発行数が等しい。

    片方だけが読んでから落ちる状態は「どちらも止まった」ので状態検証では区別できない。
    順序が揃っていることは、2 つの口の発行数を同じ入力で突き合わせてのみ言える。
    """
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _DECLARED, SNAPSHOT_PAIR)
    ticks = _frame(minutes=2).drop(columns=[dropped])

    # Act
    reads_fold = spy_snapshot_reads(monkeypatch)
    with pytest.raises(ValueError):
        tick_m1.fold_ticks_for(ticks, ref=_DECLARED, data_dir=tmp_path)
    issued_fold = len(reads_fold)

    sp.forget_resolved_points()
    reads_day = spy_snapshot_reads(monkeypatch)
    with pytest.raises(ValueError):
        tick_m1.materialize_m1_day(ticks, ref=_DECLARED)
    issued_day = len(reads_day)

    # Assert
    assert issued_fold == issued_day, (
        f"同じ不正入力で発行が食い違いました: fold_ticks_for {issued_fold} 回 /"
        f" materialize_m1_day {issued_day} 回（検証と point 解決の順序が非対称です）。"
    )


# =====================================================================
# CX-I-1 空入力（境界値）では発行 0
# =====================================================================
@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
def test_cxi1_an_empty_frame_resolves_no_point(tmp_path, monkeypatch, declared):
    """CX-I-1: 空入力では point を解決しない（使わない読込を発行しない）。列形は宣言どおりのまま。"""
    # Arrange
    ref = _DECLARED if declared else _UNDECLARED
    register_tick_ref(monkeypatch, tmp_path, ref, SNAPSHOT_PAIR if declared else None)
    reads = spy_snapshot_reads(monkeypatch)
    spent = spy_spent_points(monkeypatch)

    # Act
    out = tick_m1.fold_ticks_for(
        _frame(minutes=0), ref=ref, data_dir=tmp_path
    )

    # Assert: 出力 0 行＝使用 0。列形は宣言どおり（空でも列は宣言に従う）。
    used = used_reads(reads, spent)
    assert (len(out), used, len(spent)) == (0, 0, 0)
    assert ("spread" in out.columns) is declared      # 空振り防止（宣言どおりの列形）
    assert len(reads) - used == 0, (
        f"空入力でスナップショットを {len(reads)} 回読みました（使わない point を読んでいます）。"
    )


# =====================================================================
# CX-I-2 非空: 発行 − 使用 = 0・入力を増やしても発行は増えない（2 点）
# =====================================================================
def test_cxi2_snapshot_reads_do_not_grow_with_the_number_of_folded_minutes(tmp_path, monkeypatch):
    """CX-I-2: 閉じた分 2 と 20 で、発行 − 使用 = 0 かつ発行が等しい（オーダーの表明）。"""
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _DECLARED, SNAPSHOT_PAIR)
    reads = spy_snapshot_reads(monkeypatch)
    spent = spy_spent_points(monkeypatch)
    issued_by_scale: "list[int]" = []
    folded_by_scale: "list[int]" = []

    for minutes in (2, 20):
        # Arrange: 記憶と記録を空にして規模ごとに測り直す。
        sp.forget_resolved_points()
        reads.clear()
        spent.clear()

        # Act
        out = tick_m1.fold_ticks_for(
            _frame(minutes=minutes), ref=_DECLARED, data_dir=tmp_path
        )

        # Assert
        used = used_reads(reads, spent)
        assert len(out) == minutes                       # 空振り防止（実際に畳んだ）
        assert out["spread"].nunique() > 1                # 空振り防止（定数列ではない）
        assert used > 0                                   # 空振り防止（point を実際に使った）
        assert len(reads) - used == 0, (
            f"閉じた分 {minutes}: 読込 {len(reads)} − 使用 {used} ≠ 0"
        )
        issued_by_scale.append(len(reads))
        folded_by_scale.append(len(out))

    assert folded_by_scale == [2, 20]                     # 空振り防止（規模が実際に違う）
    assert issued_by_scale[0] == issued_by_scale[1], (
        f"畳む分を 2 → 20 にしたらスナップショット読込が {issued_by_scale[0]} →"
        f" {issued_by_scale[1]} へ増えました（分ごとに point を読み直しています）。"
    )
