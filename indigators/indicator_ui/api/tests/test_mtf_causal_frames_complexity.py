"""MTF の DataFrame 境界の**計算量テスト**（ISSUE-450 E/F/G・CLAUDE.md 絶対命令）。

固定するのは出力の正しさではなく **無駄の不在**。ここで見るのは「変換した行数」と
「指紋を作った回数」で、いずれも出力が正しいままいくらでも増えうる量である。

実測（是正前・datasetRef=jp225_tick・limit=500）:
    E: C 足全体 50,000 行を dict 化して head 3 行しか使わない（破棄率 100.0%）
    F: 接頭辞の指紋 50,001 個を作って 10 個しか読まない（破棄率 100.0%）

回数そのものは期待値に焼き込まない（焼き込むと浪費が仕様へ昇格する）。固定するのは
「**入力の履歴を伸ばしても、変換量・指紋生成量が増えないこと**」＝オーダーの表明である。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pandas as pd
import pytest

import adapter.compute.mtf_causal as causal_mod
import adapter.compute.mtf_causal_frames as frames_mod
from adapter.compute.mtf_causal_frames import causal_mtf_frames

HOUR = 3600
DAY = 86400


def _label(tf: str, unix_sec: int) -> int:
    """1D セッション足と同型のラベル（期間の右端の深夜）。fake だが実物と同じ形。"""
    return ((int(unix_sec) + 3 * HOUR) // DAY) * DAY


def _chart_frame(n_days: int, per_day: int) -> "pd.DataFrame":
    """``n_days`` 日 × ``per_day`` 本の C 足（時刻昇順の DatetimeIndex）。"""
    times = []
    for d in range(n_days):
        base = (9 + d) * DAY - 3 * HOUR
        step = (3 * HOUR) // max(per_day, 1)
        times.extend(base + i * step for i in range(per_day))
    return pd.DataFrame(
        {"open": [1.0] * len(times), "high": [2.0] * len(times),
         "low": [0.5] * len(times), "close": [1.5] * len(times),
         "volume": [1.0] * len(times)},
        index=pd.to_datetime(times, unit="s"))


def _source_frame(n: int) -> "pd.DataFrame":
    """H 足（確定プレフィクス用）。C 足の期間ラベルと同じ座標に置く。"""
    times = [(9 + i) * DAY for i in range(n)]
    return pd.DataFrame(
        {"open": [1.0] * n, "high": [2.0] * n, "low": [0.5] * n,
         "close": [1.5] * n, "volume": [10.0] * n},
        index=pd.to_datetime(times, unit="s"))


class _Meter:
    """変換した行数と指紋の生成回数を数える（Test Spy）。"""

    def __init__(self, monkeypatch) -> None:
        self.converted_rows = 0
        self.fingerprints = 0
        real_bars_from_frame = frames_mod.bars_from_frame
        real_signature = causal_mod._bar_signature

        def counting_bars_from_frame(df):
            out = real_bars_from_frame(df)
            self.converted_rows += len(out)
            return out

        def counting_signature(bar):
            self.fingerprints += 1
            return real_signature(bar)

        monkeypatch.setattr(frames_mod, "bars_from_frame", counting_bars_from_frame)
        monkeypatch.setattr(causal_mod, "_bar_signature", counting_signature)


class _Memo:
    """常に未記憶を返す記憶（指紋の生成経路を通すためだけの最小実装）。"""

    def get(self, _t, _fp):
        return None

    def put(self, _t, _fp, _series):
        return None


def _run(chart_all, source, *, limit, memo=None):
    """``causal_mtf_frames`` を出力窓 ``limit`` 本で回す。"""
    return causal_mtf_frames(
        df_chart=chart_all.tail(limit), df_source=source, compute_tf="1D",
        bar_time_unix=_label,
        compute_latest=lambda df: [{"name": "MA", "kind": "line",
                                    "data": [{"time": 0, "value": float(len(df))}]}],
        fold_from=chart_all, memo=memo)


@pytest.mark.parametrize("history_days", [2, 8, 32])
def test_converted_rows_do_not_grow_with_the_chart_history(monkeypatch, history_days: int) -> None:
    """C 足の履歴をいくら伸ばしても、dict へ変換する行数は増えない（ISSUE-450 E）。

    畳みに要るのは「窓の直前に連なる同一期間の C 足」だけである。C 足全体を変換すると、
    要らない行まで作って捨てることになる（実測 50,000 行を変換して 3 行しか使わない）。
    """
    per_day = 24
    chart = _chart_frame(n_days=history_days, per_day=per_day)
    source = _source_frame(history_days + 1)
    meter = _Meter(monkeypatch)

    _run(chart, source, limit=4)

    # 上限: 出力窓 4 本 ＋ 同一期間の先頭側（最大 per_day 本）＋ H 足 ＝ 履歴日数に依らない。
    budget = 4 + per_day + len(source)
    assert meter.converted_rows <= budget, (
        f"履歴 {history_days} 日で {meter.converted_rows} 行を変換した（要るのは高々 {budget} 行）。"
        "C 足全体を変換して捨てている")


def test_fingerprints_do_not_grow_with_the_unused_source_tail(monkeypatch) -> None:
    """読まない位置の指紋は作らない（ISSUE-450 F）。

    接頭辞の指紋が読まれるのは**期間の切れ目**だけである。H 足の末尾側を伸ばしても、
    切れ目より後ろの指紋は誰も読まないので、作れば作っただけ捨てることになる。
    """
    chart = _chart_frame(n_days=3, per_day=8)
    short_source = _source_frame(4)
    long_source = _source_frame(400)          # 窓より後ろへ大量に伸ばす（誰も読まない）

    meter_short = _Meter(monkeypatch)
    _run(chart, short_source, limit=8, memo=_Memo())
    short = meter_short.fingerprints

    meter_long = _Meter(monkeypatch)
    _run(chart, long_source, limit=8, memo=_Memo())
    long = meter_long.fingerprints

    assert long <= short + len(short_source), (
        f"読まれない H 足を 4 → 400 本へ伸ばしたら指紋生成が {short} → {long} に増えた。"
        "読まない位置の指紋を作って捨てている")


# --------------------------------------------------------------------------- #
# 採らなかった案の記録（ISSUE-450 H）
#   「窓を 1 回組み、時点ごとに末尾行を差し替える」形（live_tick_tails.make_tail_at と同型）を
#   試作し、結合 500 回を消した。発行回数は確かに減ったが **時間は縮まなかった**:
#   24 ケース合計 6,157ms → 6,324ms（0.97 倍）で悪化ケースもあった（2026-08-28 実測）。
#   よって採用せず、その形を強制する計算量テストも置かない。回数の削減が時間の削減を
#   含意しない実例であり、**計算量テストは時間の代替ではない**ことをここに記録する。
#   （時間の主張には別途ベンチマークが要る。逆に、時間が縮まないからといって「作って捨てる」
#     浪費を放置してよいわけではない — E/F は回数削減がそのまま時間短縮になった。）
# --------------------------------------------------------------------------- #
