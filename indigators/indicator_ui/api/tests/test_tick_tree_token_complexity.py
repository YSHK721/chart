"""ティック木トークンの **発行と使用が食い違わない** ことを固定する（ISSUE-512 段階 1）。

計算量テスト（絶対命令）。固定するのは出力の正しさではなく **無駄の不在** である。
ここで数えるのは「どの木を読むかを解決して得た答え」と「読取が実際に受け取った枝名」で、
出力が正しいままいくらでもずれうる量である。

なぜ状態検証では落ちないか:
    トークンを解決しておきながら読取へ渡し忘れると、読取は木の側が持つ既定引数
    （marketdata/tick_tree.py:30）の枝を黙って読む。現状は両者が偶然一致しているため、
    **出力は完全に正しい**。
    ずれが表に出るのは木が 2 本になった段階（ISSUE-512 段階 3）であり、そのときには
    「指紋は MT5 の木・実データは Dukascopy の木」＝値は正しいのに毎回読み直す状態
    （ISSUE-450 と同型）になる。回数を数える検査だけがこれを今のうちに落とせる。

固定する不変量（いずれも **無駄の不在** であって回数ではない）:
    1. 読取が受け取った枝名の集合 == 解決して得た答えの集合。
       左辺にしか無い枝＝誰も解決していない木を読んでいる（指紋と実データの食い違い）。
       右辺にしか無い枝＝解決したのに誰も読まない（作って捨てる）。
    2. 木を名指しされなかった読取 = 0（既定の木へ落ちない）。
    3. 入力を増やしても発行は増えない（オーダーの表明）。2 点で測る対象は 2 つある:
       窓の暦日数（forming_bar）と、埋める穴の本数（closed_gap_bars）。

**集合で書く理由**（回数で書かない理由）: 解決をループの外へ括り出す最適化は
発行回数 < 読取回数になるが、捨てている解決は 1 つも無く、禁じる理由が無い。回数の等式で
書くとこの最適化を検定が塞ぐ（実装詳細の仕様への昇格＝ここで避けたいことそのもの）。
集合の一致は括り出しを許したまま、食い違いと作り捨ての両方を落とす。

**回数そのものは期待値へ焼き込まない**（N 回という実装詳細を仕様へ昇格させない）。
オーダーの表明も「2 点で不変」であって「N 回」ではない。

検出力の担保: 本ファイルには陽性対照（末尾）を置く。上の不変量が実際に欠陥を落とすことを
**実行して**示すためのもので、宣言に留めない（規約は機械的検査で強制する）。

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
    """トークンの **発行** と **使用** を、回数ではなく **値** で控える（Test Spy）。

    値を控えるのが要点である。回数だけ見ると「指紋は別の木・実データは正しい木」という
    食い違いが両方とも 1 件として数えられ、検定を素通りする（ISSUE-512 レビュー実測）。

    issued: 枝名を解決する窓口が返した答えの列。
    read:   ティック読取（下記 2 関数）が枝名として受け取った値の列。
    unnamed: 同じ読取が枝名を渡されなかった回数（＝既定の木へ落ちた読取）。
    """

    def __init__(self, monkeypatch) -> None:
        self.issued: "list[str | None]" = []
        self.read: "list[str | None]" = []
        self.unnamed = 0
        self.windows: "list[tuple]" = []
        real_token = forming_bar_mod.tick_tree_token

        def issuing(ref):
            answer = real_token(ref)
            self.issued.append(answer)
            return answer

        def count_read(kwargs):
            if "symbol" in kwargs:
                self.read.append(kwargs["symbol"])   # 鍵の有無ではなく **値** を控える
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
        monkeypatch.setattr(forming_bar_mod, "day_tick_files", files)
        monkeypatch.setattr(forming_bar_mod, "forming_bar_from_ticks", bars)

    @property
    def stray_reads(self) -> "set":
        """誰も解決していない枝を読んだぶん（指紋と実データが別の木を見ている）。"""
        return set(self.read) - set(self.issued)

    @property
    def discarded(self) -> "set":
        """解決したのに誰も読まなかった答え（作って捨てている）。"""
        return set(self.issued) - set(self.read)

    def assert_issue_matches_use(self) -> None:
        """不変量 1・2 をまとめて表明する（この 3 件は常に同時に成り立つ）。"""
        assert self.unnamed == 0, (
            f"{self.unnamed} 件の読取が木を名指しされず既定木へ落ちた。"
            " 指紋と実データが別の木を見る状態（ISSUE-450 と同型）になる。"
        )
        assert not self.stray_reads, (
            f"誰も解決していない枝 {sorted(map(str, self.stray_reads))} を読んでいる"
            f"（解決した答えは {sorted(map(str, set(self.issued)))}）。"
            " 指紋と実データが別の木を見る＝値は正しいまま毎回読み直す。"
        )
        assert not self.discarded, (
            f"解決した答え {sorted(map(str, self.discarded))} を誰も読んでいない（作って捨てている）"
        )
        # 集合だけでは「同じ答えを何度も解決して余らせる」浪費が見えない（集合は一致したまま）。
        #   読取より多く解決していれば、超過分は必ずどこにも渡っていない。等式ではなく
        #   **不等式** で書くのは、ループの外への括り出し（解決 < 読取）を塞がないためである。
        assert len(self.issued) <= len(self.read), (
            f"解決 {len(self.issued)} 回に対し読取は {len(self.read)} 回しかない"
            "＝超過した解決は誰にも渡っていない（作って捨てている）"
        )

    def reset(self) -> None:
        self.issued.clear()
        self.read.clear()
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


def _gap_call(ledger, holes: int) -> "tuple[int, int]":
    """``closed_gap_bars`` を穴 ``holes`` 本で 1 回呼び、``(発行数, 読取数)`` を返す。"""
    period = 60                                    # 1m
    forming_start = _NOW - _NOW % period
    ledger.reset()
    forming_bar_mod.clear_forming_cache()
    bars = forming_bar_mod.closed_gap_bars(
        _REF, "1m", forming_start - (holes + 1) * period, forming_start
    )
    assert len(bars) == holes, f"穴 {holes} 本を想定したが {len(bars)} 本しか合成していない"
    return len(ledger.issued), len(ledger.read)


# --------------------------------------------------------------------------- #
# 1. 発行と使用の一致（値の集合で見る）
# --------------------------------------------------------------------------- #
def test_every_resolved_token_reaches_the_tick_read(monkeypatch):
    """読取が受け取った枝名の集合 == 解決して得た答えの集合。"""
    # Arrange
    ledger = _TokenLedger(monkeypatch)

    # Act
    forming_bar_mod.forming_bar(_REF, "5m", _NOW)

    # Assert
    assert ledger.issued, "どの木を読むかを一度も解決していない（既定引数に頼っている）"
    assert ledger.read, "ティック読取が 1 度も走っていない（検定が空振りしている）"
    ledger.assert_issue_matches_use()


def test_the_fingerprint_and_the_data_read_the_same_tree(monkeypatch):
    """指紋と実データ読取は **同一の** 枝を見る（読取側に 2 種類の枝が現れない）。

    :func:`forming_bar` は指紋（``forming_bar_mod.day_tick_files``）と実データ（``forming_bar_from_ticks``）
    の 2 点でティックへ触る。両者が別の木を指しても OHLCV は揃うため出力は正しいままで、
    記憶は毎回外れる（ISSUE-450 と同型）。読取が受け取った枝名が 1 種類であることで固定する。
    """
    # Arrange
    ledger = _TokenLedger(monkeypatch)

    # Act
    forming_bar_mod.forming_bar(_REF, "5m", _NOW)

    # Assert
    assert len(ledger.read) >= 2, "ティックへ触る点が 2 つ揃っていない（検定が空振りしている）"
    assert len(set(ledger.read)) == 1, (
        f"読取が {sorted(map(str, set(ledger.read)))} と複数の木を見ている"
    )


def test_gap_synthesis_resolves_only_what_it_reads(monkeypatch):
    """欠落閉周期の合成（3 つ目のティック読取点）でも、発行と使用が食い違わない。

    とくに **合成すべき周期が 0 本** のときが要点である。解決をループの外へ先に括り出すと、
    このとき「解決したのに誰も読まない」1 件が生まれる（作って捨てる＝固定したい無駄そのもの）。
    """
    # Arrange
    ledger = _TokenLedger(monkeypatch)
    period = 60                                # 1m
    forming_start = _NOW - _NOW % period

    # Act — 穴あり（複数周期）
    bars = forming_bar_mod.closed_gap_bars(_REF, "1m", forming_start - 4 * period, forming_start)
    with_gap = (list(ledger.issued), list(ledger.read), ledger.unnamed)

    # Act — 穴なし（合成 0 本）
    ledger.reset()
    empty_bars = forming_bar_mod.closed_gap_bars(
        _REF, "1m", forming_start - period, forming_start
    )

    # Assert — 穴なし側（現在の ledger の中身がこちら）
    assert not empty_bars, "穴が無いのに合成している"
    ledger.assert_issue_matches_use()
    assert not ledger.issued, (
        f"合成 0 本なのに解決を {len(ledger.issued)} 回発行している（作って捨てている）"
    )

    # Assert — 穴あり側
    assert len(bars) > 0, "合成が 1 本も出ていない（検定が空振りしている）"
    assert with_gap[2] == 0, f"合成 {len(bars)} 本のうち {with_gap[2]} 件が木を名指しせず読んだ"
    assert set(with_gap[1]) == set(with_gap[0]), (
        f"読取が見た木 {sorted(map(str, set(with_gap[1])))} と"
        f" 解決した答え {sorted(map(str, set(with_gap[0])))} が食い違う"
    )


# --------------------------------------------------------------------------- #
# 2. オーダーの表明（2 点で不変・回数は焼き込まない）
# --------------------------------------------------------------------------- #
def test_resolution_does_not_grow_with_the_window(monkeypatch):
    """窓の暦日数を変えても発行は増えない（ref あたり O(1)）。

    窓幅は 2 点（同一暦日に収まる 5m 窓 / 前暦日へ跨ぐ 1D 窓）で固定する。
    """
    # Arrange
    ledger = _TokenLedger(monkeypatch)

    # Act — 点 1: 狭い窓
    forming_bar_mod.forming_bar(_REF, "5m", _NOW)
    narrow_issued, narrow_days = len(ledger.issued), ledger.days()

    # Act — 点 2: 広い窓
    ledger.reset()
    forming_bar_mod.clear_forming_cache()
    forming_bar_mod.forming_bar(_REF, "1D", _NOW)
    wide_issued, wide_days = len(ledger.issued), ledger.days()

    # Assert
    assert wide_days > narrow_days, (
        f"2 点の窓が同じ暦日数（{narrow_days} / {wide_days}）＝オーダーを測れていない"
    )
    assert narrow_issued > 0
    assert wide_issued == narrow_issued, (
        f"窓を {narrow_days}→{wide_days} 日に広げたら解決が"
        f" {narrow_issued}→{wide_issued} 回に増えた（窓幅に比例させてはならない）"
    )
    ledger.assert_issue_matches_use()


def test_resolution_does_not_grow_with_the_number_of_holes(monkeypatch):
    """埋める穴の本数を変えても発行は増えない（穴あたりではなく呼び出しあたり）。

    :func:`forming_bar` は構造上つねに 1 回しか解決しないので、そこでオーダーを測っても
    括り出しの有無を区別できない。括り出しが効く経路は ``closed_gap_bars`` の穴のループ
    ただ 1 つであり、オーダーはそこで測らなければ意味がない（ISSUE-512 レビュー実測: 解決を
    ループ内へ戻す変異が、窓幅 2 点の検定を素通りした）。

    2 点はいずれも充填上限（adapter/compute/forming_bar.py の充填本数の上限）より少ない
    本数にする。上限を跨ぐと
    読取数が上限で頭打ちになり、「仕事量が違う 2 点」ではなくなる。
    """
    # Arrange
    ledger = _TokenLedger(monkeypatch)

    # Act — 2 点（穴 1 本 / 穴 4 本）
    few_issued, few_reads = _gap_call(ledger, holes=1)
    many_issued, many_reads = _gap_call(ledger, holes=4)

    # Assert
    assert many_reads > few_reads, (
        f"2 点の読取数が {few_reads} / {many_reads} で差が無い＝オーダーを測れていない"
    )
    assert few_issued > 0, "穴があるのに一度も解決していない（既定引数に頼っている）"
    assert many_issued == few_issued, (
        f"穴を {few_reads}→{many_reads} 本に増やしたら解決が"
        f" {few_issued}→{many_issued} 回に増えた（穴の本数に比例させてはならない）"
    )
    ledger.assert_issue_matches_use()


# --------------------------------------------------------------------------- #
# 3. 陽性対照（この検定が実際に欠陥を落とすことを実行して示す）
# --------------------------------------------------------------------------- #
def test_the_invariant_catches_a_fingerprint_that_reads_another_tree(monkeypatch):
    """陽性対照: 指紋だけ別の木を読む実装を、不変量 1 が落とす。

    この欠陥は OHLCV を正しいまま保つ（実データ側の木は正しい）ので、状態検証では原理的に
    検出できない。鍵の有無しか見ない Spy でも検出できない（どちらも「名指しされた読取」1 件）。
    値を控えて集合で突き合わせて初めて落ちる。
    """
    # Arrange
    ledger = _TokenLedger(monkeypatch)
    other = "OTHER_TREE"

    def _fingerprint_of_another_tree(start_unix, end_unix, tree):  # noqa: ANN001, ARG001
        s = pd.Timestamp(int(start_unix), unit="s")
        e = pd.Timestamp(int(end_unix), unit="s")
        forming_bar_mod.day_tick_files(s.normalize(), e.normalize(), symbol=other)
        return None

    monkeypatch.setattr(
        forming_bar_mod, "_tick_source_fingerprint", _fingerprint_of_another_tree
    )

    # Act
    forming_bar_mod.forming_bar(_REF, "5m", _NOW)

    # Assert — 不変量 1 が破れていること（＝検出力がある）
    assert other in ledger.stray_reads, (
        "指紋が別の木を読んでも不変量が破れない＝この検定は当該欠陥を落とせない"
    )
    with pytest.raises(AssertionError):
        ledger.assert_issue_matches_use()


def test_the_order_assertion_catches_a_resolution_moved_into_the_read(monkeypatch):
    """陽性対照: 読取のたびに解決する実装を、穴の本数 2 点のオーダー表明が落とす。

    窓幅 2 点（``forming_bar``）ではこの欠陥は落ちない。``forming_bar`` の読取点数は窓幅に
    依らず一定だからである。落とせるのは穴の本数で測る 2 点だけである。
    """
    # Arrange
    ledger = _TokenLedger(monkeypatch)
    spy_read = forming_bar_mod.forming_bar_from_ticks

    def _read_that_resolves_each_time(start_unix, end_unix, **kwargs):
        forming_bar_mod.tick_tree_token(_REF)      # 読取のたびに 1 回解決する実装の模倣
        return spy_read(start_unix, end_unix, **kwargs)

    monkeypatch.setattr(
        forming_bar_mod, "forming_bar_from_ticks", _read_that_resolves_each_time
    )

    # Act — 同じ 2 点（穴 1 本 / 穴 4 本）
    few_issued, _ = _gap_call(ledger, holes=1)
    many_issued, _ = _gap_call(ledger, holes=4)

    # Assert — 発行が穴の本数に追随していること（＝検出力がある）
    assert many_issued > few_issued, (
        f"読取ごとに解決しても発行が {few_issued}→{many_issued} と増えない"
        "＝この検定は当該欠陥を落とせない"
    )
