"""ティック木トークンの **発行と使用が 1 対 1** であることを固定する（ISSUE-512 段階 1）。

計算量テスト（絶対命令）。固定するのは出力の正しさではなく **無駄の不在** である。
ここで数えるのは「どの木を読むかを解決した回数」と「解決した答えを実際に読取へ渡した回数」で、
出力が正しいままいくらでもずれうる量である。

なぜ状態検証では落ちないか:
    トークンを解決しておきながら読取へ渡し忘れると、読取は木の側が持つ既定引数
    （marketdata/tick_tree.py:30）の枝を黙って読む。現状は両者が偶然一致しているため、
    **出力は完全に正しい**。
    ずれが表に出るのは木が 2 本になった段階（ISSUE-512 段階 3）であり、そのときには
    「指紋は MT5 の木・実データは Dukascopy の木」＝値は正しいのに毎回読み直す状態
    （ISSUE-450 と同型）になる。回数を数える検査だけがこれを今のうちに落とせる。

固定する不変量（いずれも **無駄の不在** であって回数ではない）:
    1. 誰にも読まれなかった解決 = 0（作って捨てない）。
    2. 木を名指しされなかった読取 = 0（既定の木へ落ちない）。
    3. 窓の暦日数を変えても発行回数は増えない（ref あたり O(1) の表明）。

1 を「発行 == 使用」の等式では書かない。解決をループの外へ括り出す最適化は
**発行 < 使用** になるが、捨てている解決は 1 つも無く、禁じる理由が無い。等式で書くと
この最適化を検定が塞ぐ（実装詳細の仕様への昇格＝ここで避けたいことそのもの）。
禁じるのは「解決したのに誰も読まない」側だけである。

**回数そのものは期待値へ焼き込まない**（N 回という実装詳細を仕様へ昇格させない）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from adapter.compute import forming_bar as forming_bar_mod

_REF = "jp225_tick"
_NOW = 1_787_887_980          # 2026-08-28 ごろの日中（1D 窓が前暦日へ跨ぐ時刻）

_BAR = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 3}


class _TokenLedger:
    """トークンの **発行** と **使用** を数える（Test Spy）。

    発行: 枝名を解決する窓口が呼ばれた回数。
    使用: ティック読取（下記 _TICK_READERS の 2 関数）が、枝名を名指しで渡された回数。
    無名: 同じ読取が枝名を渡されなかった回数（＝既定の木へ落ちた読取）。
    """

    def __init__(self, monkeypatch) -> None:
        self.issued = 0
        self.used = 0
        self.unnamed = 0
        self.windows: "list[tuple]" = []
        real_token = forming_bar_mod.tick_tree_token

        def issuing(ref):
            self.issued += 1
            return real_token(ref)

        def count_read(kwargs):
            if "symbol" in kwargs:
                self.used += 1
            else:
                self.unnamed += 1

        def files(start, end, **kwargs):
            count_read(kwargs)
            self.windows.append((pd.Timestamp(start), pd.Timestamp(end)))
            return []

        def bars(start_unix, end_unix, **kwargs):
            count_read(kwargs)
            return {**_BAR, "time": int(start_unix)}

        monkeypatch.setattr(forming_bar_mod, "tick_tree_token", issuing)
        monkeypatch.setattr(forming_bar_mod, "day_parquet_files", files)
        monkeypatch.setattr(forming_bar_mod, "forming_bar_from_ticks", bars)

    @property
    def discarded(self) -> int:
        """誰にも読まれなかった解決の数（負にはしない＝括り出しは無駄ではない）。"""
        return max(0, self.issued - self.used)

    def reset(self) -> None:
        self.issued = 0
        self.used = 0
        self.unnamed = 0
        self.windows.clear()

    def days(self) -> int:
        """読取へ渡された窓が覆う暦日数（最後の 1 件）。"""
        s, e = self.windows[-1]
        return int((e.normalize() - s.normalize()).days) + 1


@pytest.fixture(autouse=True)
def _clear():
    forming_bar_mod.clear_forming_cache()
    yield
    forming_bar_mod.clear_forming_cache()


def test_every_resolved_token_reaches_the_tick_read(monkeypatch):
    """捨てられた解決 = 0 かつ 木を名指ししない読取 = 0。"""
    # Arrange
    ledger = _TokenLedger(monkeypatch)

    # Act
    forming_bar_mod.forming_bar(_REF, "5m", _NOW)

    # Assert
    assert ledger.issued > 0, "どの木を読むかを一度も解決していない（既定引数に頼っている）"
    assert ledger.unnamed == 0, (
        f"{ledger.unnamed} 件の読取が木を名指しされず既定木へ落ちた。"
        " 指紋と実データが別の木を見る状態（ISSUE-450 と同型）になる。"
    )
    assert ledger.discarded == 0, (
        f"解決 {ledger.issued} 回のうち {ledger.discarded} 回は誰にも読まれていない"
    )


def test_resolution_does_not_grow_with_the_window(monkeypatch):
    """窓の暦日数を変えても発行回数は増えない（ref あたり O(1)）。

    窓幅は 2 点（同一暦日に収まる 5m 窓 / 前暦日へ跨ぐ 1D 窓）で固定する。
    """
    # Arrange
    ledger = _TokenLedger(monkeypatch)

    # Act — 点 1: 狭い窓
    forming_bar_mod.forming_bar(_REF, "5m", _NOW)
    narrow_issued, narrow_days = ledger.issued, ledger.days()

    # Act — 点 2: 広い窓
    ledger.reset()
    forming_bar_mod.clear_forming_cache()
    forming_bar_mod.forming_bar(_REF, "1D", _NOW)
    wide_issued, wide_days = ledger.issued, ledger.days()

    # Assert
    assert wide_days > narrow_days, (
        f"2 点の窓が同じ暦日数（{narrow_days} / {wide_days}）＝オーダーを測れていない"
    )
    assert narrow_issued > 0
    assert wide_issued == narrow_issued, (
        f"窓を {narrow_days}→{wide_days} 日に広げたら解決が"
        f" {narrow_issued}→{wide_issued} 回に増えた（窓幅に比例させてはならない）"
    )
    assert ledger.unnamed == 0
    assert ledger.discarded == 0


def test_gap_synthesis_resolves_only_what_it_reads(monkeypatch):
    """欠落閉周期の合成（3 つ目のティック読取点）でも、捨てられた解決 = 0。

    とくに **合成すべき周期が 0 本** のときが要点である。解決をループの外へ先に括り出すと、
    このとき「解決したのに誰も読まない」1 件が生まれる（作って捨てる＝固定したい無駄そのもの）。
    括り出しそのものは穴がある限り無駄にならないので、ここでも等式では書かない。
    """
    # Arrange
    ledger = _TokenLedger(monkeypatch)
    period = 60                                # 1m
    forming_start = _NOW - _NOW % period

    # Act — 穴あり（複数周期）
    bars = forming_bar_mod.closed_gap_bars(_REF, "1m", forming_start - 4 * period, forming_start)
    gap = (ledger.issued, ledger.used, ledger.unnamed, ledger.discarded)

    # Act — 穴なし（合成 0 本）
    ledger.reset()
    empty_bars = forming_bar_mod.closed_gap_bars(
        _REF, "1m", forming_start - period, forming_start
    )

    # Assert
    assert len(bars) > 0, "合成が 1 本も出ていない（検定が空振りしている）"
    assert not empty_bars, "穴が無いのに合成している"
    assert gap[2] == 0, f"合成 {len(bars)} 本のうち {gap[2]} 件が木を名指しせず読んだ"
    assert gap[3] == 0, f"解決 {gap[0]} 回のうち {gap[3]} 回は誰にも読まれていない"
    assert ledger.discarded == 0, (
        f"合成 0 本なのに解決を {ledger.issued} 回発行している（作って捨てている）"
    )
