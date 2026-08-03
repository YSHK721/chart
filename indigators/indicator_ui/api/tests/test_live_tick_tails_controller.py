"""/live_ticks 末尾値同梱の薄殻テスト（ISSUE-250 Phase 1）。

後方互換（申告が無ければ従来応答）と、対象外の明示除外を固定する。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from adapter.controller import live_tick_tails_controller as ctl

_TICKS = [[1_785_757_500_100, 100.0], [1_785_757_500_300, 101.0]]


class _Port:
    def __init__(self, df=None, known=True, tf_known=True):
        self._df = df
        self._known, self._tf = known, tf_known

    def is_known(self, ref): return self._known
    def is_known_timeframe(self, tf): return self._tf
    def load_dataframe(self, ref, tf): return self._df


def _df(n=10):
    return pd.DataFrame({
        "time": [1_785_757_500 - (n - 1 - i) * 900 for i in range(n)],
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.5] * n, "volume": [1.0] * n,
    })


def _q(**over):
    q = {"specs": [json.dumps([{"instanceId": "i1", "indicatorId": "profit_rsi"}])],
         "datasetRef": ["jp225_tick"], "timeframe": ["15m"]}
    q.update({k: [v] for k, v in over.items()})
    return q


@pytest.fixture
def _port(monkeypatch):
    holder = {}

    def _install(port):
        holder["p"] = port
        monkeypatch.setattr(ctl, "_dataset_port", lambda: port)
    return _install


def test_returns_none_without_specs():
    assert ctl.handle_live_tick_tails({}, _TICKS) is None


def test_returns_none_without_ticks(_port):
    _port(_Port(_df()))
    assert ctl.handle_live_tick_tails(_q(), []) is None


@pytest.mark.parametrize("tf", ["1W", "1M", "unknown", None])
def test_returns_none_for_non_fixed_period_timeframe(tf, _port):
    _port(_Port(_df()))
    q = _q()
    q["timeframe"] = [tf] if tf is not None else []
    assert ctl.handle_live_tick_tails(q, _TICKS) is None


def test_returns_none_for_malformed_specs_json(_port):
    _port(_Port(_df()))
    assert ctl.handle_live_tick_tails(_q(specs="{not json"), _TICKS) is None


def test_returns_none_for_unknown_dataset(_port):
    _port(_Port(_df(), known=False))
    assert ctl.handle_live_tick_tails(_q(), _TICKS) is None


def test_returns_none_for_empty_window(_port):
    _port(_Port(_df(0)))
    assert ctl.handle_live_tick_tails(_q(), _TICKS) is None


def test_emits_one_entry_per_tick_and_drops_non_incremental(_port):
    _port(_Port(_df(400)))
    specs = json.dumps([
        {"instanceId": "rsi", "indicatorId": "profit_rsi",
         "params": {"rsi_period": 6, "apply": 5, "window_n": 500,
                    "q_low": 0.10, "q_high": 0.90, "q_out": 0.99, "k_events": 50}},
        {"instanceId": "stc", "indicatorId": "profit_stc", "params": {}},
    ])
    out = ctl.handle_live_tick_tails(_q(specs=specs), _TICKS)
    assert out is not None and len(out) == len(_TICKS)
    assert [e["tickMs"] for e in out] == [t[0] for t in _TICKS]
    assert "stc" not in out[0]["tails"]      # 増分器なし＝明示的に落とす


# =========================================================================== #
# ISSUE-251: 形成中バーは「周期の累積」で組む（増分だけで畳まない）
# =========================================================================== #

class _Buffer:
    """LiveTickBuffer の読取面だけを持つフェイク（(ms, mid) 昇順）。"""

    def __init__(self, ticks):
        self._ticks = list(ticks)

    def ticks_since(self, ms):
        return [t for t in self._ticks if t[0] > ms]


class _Forming:
    """rollup_forming_bar のフェイク（呼ばれた now_unix を記録し、固定の周期累積を返す）。"""

    def __init__(self, bar):
        self.bar, self.calls = bar, []

    def rollup_forming_bar(self, ref, tf, now_unix, *, buffer=None):
        self.calls.append(now_unix)
        return self.bar


def _states(_port, ticks, *, buffer=None, forming=None, tf="15m"):
    """tail_at をフェイク化して、各 tick 時点の FormingState をそのまま観測する。"""
    _port(_Port(_df(400)))
    seen = []
    import usecase.serve_live_tick_tails as usecase

    orig = usecase.tails_for_ticks

    def _spy(states, specs, tail_at):
        seen.extend(states)
        return orig(states, specs, lambda *_a: {"v": 1.0})

    ctl.tails_for_ticks = _spy
    try:
        ctl.handle_live_tick_tails(_q(timeframe=tf), ticks, buffer=buffer, forming=forming)
    finally:
        ctl.tails_for_ticks = orig
    return seen


# 周期始端 1_785_757_500 - ... ではなく、15m 周期の始端が丁度になる時刻で組む。
_P0_SEC = 1_785_757_500 // 900 * 900          # _TICKS が属する 15m 周期の始端
_P0_MS = _P0_SEC * 1000
_SEED = {"time": _P0_SEC, "open": 10.0, "high": 30.0, "low": 5.0, "close": 20.0, "volume": 40}


def test_seed_carries_the_period_accumulation_into_the_batch(_port):
    """seed（周期始端からの累積）が増分 tick に引き継がれる（open/high/low/volume が保たれる）。"""
    forming = _Forming(_SEED)
    states = _states(_port, _TICKS, buffer=_Buffer([]), forming=forming)
    assert len(states) == len(_TICKS)
    last = states[-1]
    assert last.open == 10.0                      # 周期の最初の tick（増分先頭ではない）
    assert last.high == 101.0 and last.low == 5.0  # 累積 max/min（増分だけなら 101/100）
    assert last.volume == 40 + len(_TICKS)         # 累積 tick 数（増分だけなら 2）
    assert last.close == 101.0                     # close は常に当該 tick


def test_without_seed_the_batch_restarts_the_bar(_port):
    """buffer 未注入（非 served / テスト既定）は従来どおり増分だけで畳む（後方互換）。"""
    states = _states(_port, _TICKS, buffer=None, forming=_Forming(_SEED))
    assert states[-1].volume == len(_TICKS) and states[-1].open == 100.0


def test_seed_of_another_period_is_dropped(_port):
    """周期が違う seed は引き継がない（誤った周期の値を持ち込まない）。"""
    stale = {**_SEED, "time": _P0_SEC - 900}
    states = _states(_port, _TICKS, buffer=_Buffer([]), forming=_Forming(stale))
    assert states[-1].volume == len(_TICKS) and states[-1].open == 100.0


def test_sub_second_prior_ticks_are_not_lost(_port):
    """seed は秒境界までしか畳めない。増分先頭と同一秒の既適用 tick は buffer から補う。"""
    first_ms = _TICKS[0][0]
    same_sec = [(first_ms - 90, 99.0), (first_ms - 50, 98.0)]   # 同一秒・増分より前
    forming = _Forming(_SEED)
    states = _states(_port, _TICKS, buffer=_Buffer(same_sec + [tuple(t) for t in _TICKS]),
                     forming=forming)
    assert forming.calls == [first_ms // 1000]                  # seed は floor 秒で評価する
    assert states[-1].volume == 40 + len(same_sec) + len(_TICKS)
    assert states[-1].low == 5.0
    assert len(states) == len(_TICKS)                           # 返すのは増分ぶんだけ


def test_seed_failure_degrades_to_increment_only(_port):
    """seed 取得が失敗しても tails 全体は落とさない（増分のみへ縮退）。"""
    class _Broken:
        def rollup_forming_bar(self, *a, **k):
            raise RuntimeError("rollup unavailable")

    states = _states(_port, _TICKS, buffer=_Buffer([]), forming=_Broken())
    assert len(states) == len(_TICKS) and states[-1].volume == len(_TICKS)


def test_1d_uses_the_session_day_period_rule(_port):
    """1D の周期キーはセッション日（ISSUE-078）＝フロント _periodOf と同一規則。"""
    from marketdata.session_day import session_bar_time

    ms = 1_785_794_400_000                                    # 22:00 UTC（NY17:00 ET 以降＝セッション日は翌日）
    ticks = [[ms, 100.0], [ms + 200, 101.0]]
    states = _states(_port, ticks, buffer=_Buffer([]), forming=_Forming(None), tf="1D")
    assert states[-1].time == session_bar_time(ms // 1000)
    assert states[-1].time != (ms // 1000 // 86400) * 86400    # UTC floor とは異なる


def test_falls_back_to_the_buffer_when_the_rollup_seed_is_a_stale_period(_port):
    """周期境界直後は M1/ロールアップの焼き込みが遅れ base が前周期のまま。

    そのときは seed を捨て、**周期始端以降のバッファ tick を全部畳んで**累積を復元する
    （前周期の値は持ち込まない／増分だけにも縮退しない）。
    """
    stale = {**_SEED, "time": _P0_SEC - 900}                 # 前周期の base（焼き込み遅れ）
    batch = [[_P0_MS + 300_000, 100.0], [_P0_MS + 300_200, 101.0]]  # 周期の 5 分後に届いた増分
    in_period = [(_P0_MS + 1_000, 90.0), (_P0_MS + 2_000, 110.0)]   # 周期始端以降・増分より前
    states = _states(_port, batch,
                     buffer=_Buffer(in_period + [tuple(t) for t in batch]),
                     forming=_Forming(stale))
    last = states[-1]
    assert last.open == 90.0                                  # 周期の最初の tick
    assert last.high == 110.0 and last.low == 90.0            # 周期内の max/min
    assert last.volume == len(in_period) + len(batch)         # 周期の tick 数（増分だけなら 2）
    assert len(states) == len(batch)
