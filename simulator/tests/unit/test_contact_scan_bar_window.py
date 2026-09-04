"""bit 一致回帰: bar_window のラベル規約（左/右・末足/先頭 fallback）（参照実装）。"""
from __future__ import annotations

from simulator.usecase.contact_scan.bar_window import DAY, TF_SECS, bar_window


def test_left_label_interior_uses_next_bar_boundary():
    # 1m（左ラベル）の内部足: [t[i], t[i+1])
    t = [0, 60, 120, 180]
    assert bar_window(t, 1, "1m") == (60, 120)


def test_left_label_last_bar_falls_back_to_duration():
    # 末足は次足なし → [t[i], t[i]+dur)
    t = [0, 60, 120]
    assert bar_window(t, 2, "1m") == (120, 120 + TF_SECS["1m"])


def test_left_label_4h_interior():
    t = [0, 14400, 28800]
    assert bar_window(t, 0, "4h") == (0, 14400)


def test_right_label_week_interior_uses_prev_plus_day():
    # 1W（右ラベル・time=期間終端）の内部足: [t[i-1]+DAY, t[i]+DAY)
    t = [1000, 1000 + 604800, 1000 + 2 * 604800]
    s, e = bar_window(t, 1, "1W")
    assert s == t[0] + DAY
    assert e == t[1] + DAY


def test_right_label_first_bar_falls_back_to_minus_duration():
    # 先頭足（右ラベル）は前足なし → [t[i]-dur+DAY, t[i]+DAY)
    t = [5_000_000, 5_000_000 + 604800]
    s, e = bar_window(t, 0, "1W")
    assert s == t[0] - TF_SECS["1W"] + DAY
    assert e == t[0] + DAY


def test_right_label_month():
    t = [0, 2_592_000, 5_184_000]
    s, e = bar_window(t, 2, "1M")
    assert s == t[1] + DAY
    assert e == t[2] + DAY


def test_returns_ints():
    s, e = bar_window([0, 60], 0, "1m")
    assert isinstance(s, int) and isinstance(e, int)


# =========================================================================== #
# ISSUE-261: 台帳との一致を検定で拘束する（写しの黙ったずれを防ぐ）
# =========================================================================== #

def test_tf_secs_matches_the_marketdata_ledger():
    """``bar_window.TF_SECS`` が時間足台帳（唯一源）と一致する。

    なぜ import ではなく検定で縛るか: 本モジュールは「純・stdlib のみ」を宣言しており
    （usecase 層の純度＝偶有的技術を持ち込まない）、``marketdata.tf_meta`` を import すると
    pandas が推移的に入って宣言が壊れる。よって写しは残したまま、**ずれたら落ちる**状態に
    する（台帳側の JS 生成物を parity 検定で拘束しているのと同じ考え方）。

    台帳へ時間足を足したら本表にも足す。足し忘れは本検定が落として知らせる。
    """
    from marketdata.tf_meta import TF_BAR_SEC

    from simulator.usecase.contact_scan.bar_window import TF_SECS

    assert TF_SECS == dict(TF_BAR_SEC)
