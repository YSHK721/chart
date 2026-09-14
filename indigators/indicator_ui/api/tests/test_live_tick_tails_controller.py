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


@pytest.mark.parametrize("tf", ["unknown", "", None])
def test_returns_none_for_unknown_timeframe(tf, _port):
    _port(_Port(_df()))
    q = _q()
    q["timeframe"] = [tf] if tf is not None else []
    assert ctl.handle_live_tick_tails(q, _TICKS) is None


# 全時間足が同一経路（tf 分岐なし）。暦周期（1W/1M）も日中足と同じく末尾値を返す。
@pytest.mark.parametrize("tf", ["1m", "5m", "15m", "30m", "1h", "4h", "1D", "1W", "1M"])
def test_emits_tails_for_every_known_timeframe(tf, _port):
    _port(_Port(_df(400)))
    out = ctl.handle_live_tick_tails(_q(timeframe=tf), _TICKS)
    assert out is not None and len(out) == len(_TICKS)
    assert [e["tickMs"] for e in out] == [t[0] for t in _TICKS]


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

    def _spy(states, specs, tail_at, **kw):
        seen.extend(states)
        return orig(states, specs, lambda *_a: {"v": 1.0}, **kw)

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


# =========================================================================== #
# ISSUE-257: 末尾値は「個別に描かれる tick」だけ計算する（費用を tick 密度から切り離す）
# =========================================================================== #

def test_tails_within_ms_limits_computation_to_the_playback_horizon(_port):
    """地平（now - tailsWithinMs）より古い tick は末尾値を持たない（計算もしない）。

    費用は tick 数 × spec 数に比例するため、カーソルが古いと 1 応答が 30 分バッファ全件になる。
    実測密度（30 分あたり p90 2,056・max 10,886 tick）では 1 要求だけで poll 間隔 2.5 秒を
    超え、要求が重なり始める。地平で絞れば費用は tick 密度に依らない上限に固定される。
    """
    _port(_Port(_df(400)))
    now = _TICKS[-1][0]
    # 先頭 tick は地平より古く、末尾 tick は新しくなる幅を選ぶ。
    within = (_TICKS[-1][0] - _TICKS[0][0]) // 2
    out = ctl.handle_live_tick_tails(
        _q(tailsWithinMs=str(within)), _TICKS, now_ms=now)
    assert out is not None and len(out) == len(_TICKS)     # 契約（ticks と同数・同順）は不変
    assert out[0]["tails"] == {}                            # 地平より古い＝計算しない
    assert out[-1]["tails"] != {}                           # 地平より新しい＝従来どおり計算する


def test_tails_computed_for_every_tick_without_declaration(_port):
    """未申告（旧フロント）は全 tick で計算＝従来挙動（後方互換）。"""
    _port(_Port(_df(400)))
    out = ctl.handle_live_tick_tails(_q(), _TICKS, now_ms=_TICKS[-1][0])
    assert out is not None
    assert all(e["tails"] != {} for e in out)


def test_tails_within_ms_is_ignored_without_server_clock(_port):
    """``now_ms`` 未注入では地平を決められない＝絞らない（勝手に間引かない）。"""
    _port(_Port(_df(400)))
    out = ctl.handle_live_tick_tails(_q(tailsWithinMs="1"), _TICKS)
    assert out is not None
    assert all(e["tails"] != {} for e in out)


@pytest.mark.parametrize("bad", ["0", "-5", "abc", ""])
def test_malformed_tails_within_ms_falls_back_to_computing_all(bad, _port):
    """不正な申告は無視して従来挙動へ（黙って間引かない）。"""
    _port(_Port(_df(400)))
    out = ctl.handle_live_tick_tails(
        _q(tailsWithinMs=bad), _TICKS, now_ms=_TICKS[-1][0])
    assert out is not None
    assert all(e["tails"] != {} for e in out)


# --------------------------------------------------------------------------- #
# 計算.時間足（上位足）ごとの分離（ISSUE-274）
# --------------------------------------------------------------------------- #
class _RecordingPort(_Port):
    """``load_dataframe`` に渡された時間足を記録する Port。"""

    def __init__(self, df=None):
        super().__init__(df)
        self.loaded: "list[str]" = []

    def load_dataframe(self, ref, tf):
        self.loaded.append(tf)
        return self._df


def _specs_query(*specs):
    return _q(specs=json.dumps(list(specs)))


def test_each_compute_timeframe_loads_its_own_window(_port):
    """計算足ごとに窓を分けて読む（上位足指標へチャート足の窓を使わない）。"""
    port = _RecordingPort(_df(400))
    _port(port)
    out = ctl.handle_live_tick_tails(_specs_query(
        {"instanceId": "chart#1", "indicatorId": "profit_rsi", "params": {"timeframe": "chart"}},
        {"instanceId": "mtf#1", "indicatorId": "profit_rsi", "params": {"timeframe": "1h"}},
    ), _TICKS)
    assert out is not None
    # チャート足（15m）と計算足（1h）の 2 本。同じ足の spec は 1 回にまとまる。
    assert sorted(port.loaded) == ["15m", "1h"]


def test_specs_of_the_same_timeframe_share_one_window(_port):
    """同一計算足の spec は 1 グループ＝窓の読み込みは 1 回（仕事量を増やさない）。"""
    port = _RecordingPort(_df(400))
    _port(port)
    ctl.handle_live_tick_tails(_specs_query(
        {"instanceId": "a", "indicatorId": "profit_rsi", "params": {"timeframe": "1h"}},
        {"instanceId": "b", "indicatorId": "profit_rsi", "params": {"timeframe": "1h"}},
    ), _TICKS)
    assert port.loaded == ["1h"]


def test_forming_bar_is_folded_per_compute_timeframe(_port):
    """形成中バーは計算足の周期で畳む（チャート足の周期で畳んだ値を渡さない）。"""
    seen: "dict[str, list]" = {}

    def _fake_make_tail_at(*, df, adapter, latest_compute, set_last_bar, inject):
        def _tail_at(spec, state):
            seen.setdefault(spec.instance_id, []).append(state.time)
            return {"v": 1.0}
        return _tail_at

    _port(_Port(_df(400)))
    import adapter.controller.live_tick_tails_controller as mod
    original = mod.make_tail_at
    mod.make_tail_at = _fake_make_tail_at
    try:
        ctl.handle_live_tick_tails(_specs_query(
            {"instanceId": "chart#1", "indicatorId": "profit_rsi", "params": {"timeframe": "15m"}},
            {"instanceId": "mtf#1", "indicatorId": "profit_rsi", "params": {"timeframe": "1D"}},
        ), _TICKS)
    finally:
        mod.make_tail_at = original
    # 同じ tick でも、属するバーの time は計算足ごとに違う（15m の枠 ≠ 1D の枠）。
    assert seen["chart#1"] and seen["mtf#1"]
    assert seen["chart#1"][-1] != seen["mtf#1"][-1]


def test_unknown_timeframe_override_falls_back_to_the_chart_timeframe(_port):
    """未知の override はチャート足に追従する（勝手な足で計算しない）。"""
    port = _RecordingPort(_df(400))
    _port(port)
    ctl.handle_live_tick_tails(_specs_query(
        {"instanceId": "x", "indicatorId": "profit_rsi", "params": {"timeframe": "3y"}},
    ), _TICKS)
    assert port.loaded == ["15m"]


def test_both_timeframes_appear_in_the_merged_response(_port):
    """異なる計算足の末尾値が 1 つの tick エントリへまとまる。"""
    _port(_Port(_df(400)))
    out = ctl.handle_live_tick_tails(_specs_query(
        {"instanceId": "chart#1", "indicatorId": "profit_rsi", "params": {"timeframe": "chart"}},
        {"instanceId": "mtf#1", "indicatorId": "profit_rsi", "params": {"timeframe": "1h"}},
    ), _TICKS)
    assert out is not None
    assert [e["tickMs"] for e in out] == [t[0] for t in _TICKS]
    assert set(out[-1]["tails"]) == {"chart#1", "mtf#1"}


def test_forming_bar_accumulation_still_covers_every_tick(_port):
    """地平で絞るのは末尾値だけ。形成中バーの累積（volume）は 1 本も飛ばさない。"""
    seen = []

    def _tail_at(spec, state):
        seen.append(state)
        return {"v": 1.0}

    from usecase.serve_live_tick_tails import TailSpec, states_for_batch, tails_for_ticks
    states = states_for_batch([], _TICKS, lambda ms: 0)
    spec = TailSpec("i1", "profit_rsi", "default", {})
    out = tails_for_ticks(states, [spec], _tail_at, wanted=lambda st: st.tick_ms == _TICKS[-1][0])
    assert [e["tickMs"] for e in out] == [t[0] for t in _TICKS]   # 全 tick が応答に残る
    assert len(seen) == 1                                          # 計算は 1 本だけ
    assert states[-1].volume == len(_TICKS)                        # 累積は全 tick ぶん
