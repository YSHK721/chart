"""spread の point を検定する 2 つの検定群が共有する合成系列の組み立て（テストヘルパ・非テストモジュール）。

なぜ在るか（実測に基づく欠陥）:
    同じ合成ティック木・同じ書き手呼出・同じ Test Spy を
    `marketdata/tests/test_tick_m1_spread_schema_guard.py` と
    `marketdata/tests/test_spread_point_ownership.py` が**手書きで複製**していた（13 ヘルパ・
    うち 6 件は完全一致、7 件は docstring だけ差し替え）。複製は必ず取り残しを生む（片方だけ直すと
    同じ名前の別物が 2 つ残り、どちらが正かを検定が答えられなくなる）。本モジュールが唯一源であり、
    両検定はここから import する。

含む構造:
    TICK_TREE / SNAPSHOT_PAIR / DAY0
        合成系列の定数（tick 木の枝名・宣言する実在スナップショットの組・起点の日）。
    cleared_point_cache
        解決済み point の記憶を試験ごとに捨てる autouse fixture。捨てる口は被検査モジュールの
        公開面 `marketdata.spread_point.forget_resolved_points` であり、private 属性へは触らない
        （触ると、記憶の持ち方を変えただけで検定が「失敗」ではなく「エラー」になる）。
    register_tick_ref / snapshot_point
        合成 ref の台帳への一時登録と、宣言した組の point（既存の公開経路で引く期待値）。
    day / put_day / put_days / put_day_of_spread_points
        合成ティックを tick 木へ置く（すべて呼出側が渡す tmp_path の下）。
        put_day は 1 分内の幅が一定、put_day_of_spread_points は**分内で幅が変わる**
        （分内 min を first / last / max / mean へ変える変異を素通ししないための素材）。
    run_writer / header_of
        build / append を同じ引数で呼ぶ口と、出力 CSV の先頭行。
    spy / spy_snapshot_reads / spy_spent_points / used_reads
        計算量検定の Test Spy（モジュール属性の継ぎ目）と「出力に使った読込」の数え方。

本モジュールは既定の DATA_DIR を読み書きしない（書込先は呼出側の tmp_path のみ）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pandas as pd
import pytest

from marketdata import quote_spread
from marketdata import spread_point as sp
from marketdata import symbol_spec_snapshot as sss
from marketdata import tick_m1
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor

#: tick 木の枝（テスト専用・tmp_path の中だけ）。
TICK_TREE = "SPY225"
#: 宣言する組（実在するスナップショット）。
SNAPSHOT_PAIR = (sss.OANDA_JAPAN_MT5_LIVE, "JP225")
#: 合成 ref の台帳が名乗る価格基準。台帳外 ref へ同じ基準を明示したい呼出もここを使う
#: （登録済み側と台帳外側で綴りが割れると、byte 一致の突合が「基準の食い違い」を測ってしまう）。
LEDGER_BASIS = "bid"
#: 合成系列の起点の日。
DAY0 = pd.Timestamp("2026-09-01")


@pytest.fixture(autouse=True)
def cleared_point_cache():
    """point の記憶をテスト間で持ち越さない（F.I.R.S.T の Independent）。

    import した検定モジュールの中でだけ autouse になる（他の marketdata 検定には及ばない）。
    """
    sp.forget_resolved_points()
    yield
    sp.forget_resolved_points()


def register_tick_ref(
    monkeypatch, tmp_path: Path, ref: str, declared, basis: str = LEDGER_BASIS
) -> None:
    """合成のティック ref を台帳へ一時登録する（``declared`` が None なら宣言欄を渡さない）。

    ``basis`` は台帳が名乗る価格基準で、既定は :data:`LEDGER_BASIS`（既存の呼出は 1 つも変わらない）。
    明示できるのは、同じティック木から導く系列の**基準の食い違い**を Fail-Stop する検定
    （``marketdata/tests/test_tick_m1_series_plan.py`` の F-1）が、違う基準を名乗る ref を要るため。
    """
    extra = {} if declared is None else {"spread_point_snapshot": declared}
    monkeypatch.setitem(REGISTRY, ref, DatasetDescriptor(
        path=tmp_path / f"{ref}_m1.csv", symbol="JP225", tick=True,
        price_basis=basis, vendor="dukascopy", **extra,
    ))


def snapshot_point() -> float:
    """宣言した組の point（既存の公開経路 load_spec_fields で引く＝被検査の新しい面を通さない）。"""
    return sss.load_spec_fields(*SNAPSHOT_PAIR)["point_size"]


def day(k: int) -> pd.Timestamp:
    """起点から ``k`` 日後。"""
    return DAY0 + pd.Timedelta(days=k)


def put_day(data_dir: Path, when: pd.Timestamp, n_minutes: int = 2) -> None:
    """``when`` の先頭 ``n_minutes`` 分 × 3 本のティック（bid < ask・幅 7.x）を tick 木へ置く。"""
    rows = [
        (when + pd.Timedelta(minutes=m, seconds=s), 66000.0 + s * 0.1, 66007.0 + s * 0.1 + m * 0.1)
        for m in range(n_minutes) for s in (5, 30, 55)
    ]
    _write_tick_day(data_dir, when, rows)


def _write_tick_day(
    data_dir: Path, when: pd.Timestamp, rows: "Sequence[tuple[pd.Timestamp, float, float]]"
) -> None:
    """``(時刻, bid, ask)`` の並びを ``when`` の日別 parquet へ書く（合成ティック木の書込点）。

    合成ティックを置く関数が 2 つ（:func:`put_day` / :func:`put_day_of_spread_points`）あるので、
    列名・tz・置き場の解決はここ 1 箇所に置く（書き写すと片方だけ直った瞬間に別の木を作る）。
    """
    frame = pd.DataFrame({
        "timestamp": pd.to_datetime([r[0] for r in rows]).tz_localize("UTC"),
        "bidPrice": [r[1] for r in rows],
        "askPrice": [r[2] for r in rows],
    })
    p = tick_m1.day_parquet_path(when, symbol=TICK_TREE, data_dir=data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(p)


def put_day_of_spread_points(
    data_dir: Path, when: pd.Timestamp, widths_by_minute: "Sequence[Sequence[int]]"
) -> None:
    """``widths_by_minute[m]`` の各整数 points を気配幅に持つティックを ``when`` の m 分へ置く。

    :func:`put_day` との違いは、**分内で気配幅が変わる**ことである。put_day は 1 分内の 3 本の
    幅が等しいので、分内 min を first / last / max / mean へ変える変異を素通しする。

    価格は ``bidPrice`` がティックごとに 0.05 ずつ動き、``askPrice`` は
    ``bidPrice + points × point``（point は :func:`snapshot_point` の値）である。呼出側は割り算を
    1 度もせずに期待値（その分の spread ＝ ``min(widths_by_minute[m])``）を持てる——検定が丸めの
    規則を書き写すと、規則を変えた変異を検定も一緒に追随してしまう。
    """
    point = snapshot_point()
    rows = [
        (when + pd.Timedelta(minutes=m, seconds=5 + 25 * k),
         66000.0 + 0.1 * m + 0.05 * k,
         66000.0 + 0.1 * m + 0.05 * k + point * width)
        for m, widths in enumerate(widths_by_minute)
        for k, width in enumerate(widths)
    ]
    _write_tick_day(data_dir, when, rows)


def put_days(data_dir: Path, n_days: int) -> "list[pd.Timestamp]":
    """起点から ``n_days`` 日分を tick 木へ置き、その日付を返す。"""
    days = [day(k) for k in range(n_days)]
    for when in days:
        put_day(data_dir, when)
    return days


def run_writer(entry, ref: str, data_dir: Path, start, end, **kw) -> Path:
    """build / append を同じ引数で呼ぶ（tick 木は TICK_TREE・data_dir は必ず tmp）。

    価格基準は**渡さない**。登録済み ref では台帳が唯一の源であり、明示は拒否される
    （ISSUE-511 段階 3 の段階 6・V-3）。かつてここが全呼出へ無条件に ``price_basis="bid"`` を
    渡していたため、基準の拒否を入れると point ではなく基準で ``ValueError`` が上がり、point の
    明示拒否を測る検定が ``match="台帳"`` に一致したまま緑で通って主張が空洞化した（2026-09-17 実測）。
    台帳外 ref へ基準が要る呼出は、呼出側が ``price_basis=LEDGER_BASIS`` を ``kw`` で渡す。
    """
    return entry(start, end, symbol=TICK_TREE, ref=ref, data_dir=data_dir, **kw)


def header_of(path: Path) -> str:
    """出力 CSV の先頭行。"""
    return path.read_text(encoding="utf-8").splitlines()[0]


def spy(monkeypatch, module, name: str) -> "list[tuple]":
    """``module.name`` を包み、発行ごとに引数を記録する Test Spy（モジュール属性の継ぎ目）。

    属性が無ければ AttributeError（継ぎ目の不在がそのまま失敗として見える）。
    """
    real = getattr(module, name)
    calls: "list[tuple]" = []

    def recorded(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, recorded)
    return calls


def spy_snapshot_reads(monkeypatch) -> "list[tuple[tuple, float]]":
    """``sss.load_snapshot`` を包み、発行ごとに（読んだ組, その読込で得た point_size）を記録する。

    point_size は既存の公開経路 ``sss.spec_fields`` で読込の戻り値そのものから引く
    （被検査の新しい面を通さない）。
    """
    real = sss.load_snapshot
    reads: "list[tuple[tuple, float]]" = []

    def recorded(*args, **kwargs):
        snapshot = real(*args, **kwargs)
        reads.append((args, sss.spec_fields(snapshot)["point_size"]))
        return snapshot

    monkeypatch.setattr(sss, "load_snapshot", recorded)
    return reads


def spy_spent_points(monkeypatch) -> "list[float]":
    """出力の spread 列を数える唯一の口（``quote_spread.minute_spread_points``）へ渡った point を記録する。"""
    real = quote_spread.minute_spread_points
    spent: "list[float]" = []

    def recorded(*args, **kwargs):
        spent.append(kwargs["point"])
        return real(*args, **kwargs)

    monkeypatch.setattr(quote_spread, "minute_spread_points", recorded)
    return spent


def used_reads(reads: "list[tuple[tuple, float]]", spent: "list[float]") -> int:
    """出力の spread に使われた読込の数＝読んだ組のうち point が spread の計算へ渡った組の数。

    同じ組を 2 回読んでも使用は 1（2 回目の読込は出力に何も足さない）。読んだ point と別の point で
    spread を数えたら、その読込の使用は 0。
    """
    return len({pair for pair, point in reads if point in spent})
