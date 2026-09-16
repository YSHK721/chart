"""spread_point — 系列の spread 列を数える point の読み口（ISSUE-511 段階 3 前提 (a)・段階 1）。

point を呼出ごとの引数ではなく**系列ごとの台帳属性**にする。どのスナップショットの point で数えるかは
台帳（``marketdata.dataset_registry`` の記述子欄 ``spread_point_snapshot``）が、値はスナップショット
（``marketdata.symbol_spec_snapshot``）が持つ。本モジュールは両者をつなぐだけで値を持たない。

書き手（M1 の spread 列）と読み手（simulator の約定 ask = bid + spread × point_size）の point は
同じ源でなければならない。読み手の point_size はスナップショット由来なので、書き手も同じ表の同じ
項目（``marketdata.symbol_spec_snapshot.SYMBOL_FIELD_SOURCES`` の point_size）を読む。

台帳の**持ち方**（記述子の表に在るか・宣言欄をどう引くか）は本モジュールの変更理由であり、素材化側
（``marketdata.tick_m1``）は表そのものを見ない。素材化側が見るのは本モジュールの公開面 3 つ
:func:`ledger_owns_point`（誰が point を持つか）・:func:`declared_point_resolver`（どう解決するか）・
:func:`declared_snapshot_of`（何が宣言されているか＝食い違いを報せる文面のため）である
（ISSUE-511 段階 3 の段階 1・V-1）。

機械的に強制している範囲（``marketdata/tests/test_spread_point_ownership.py`` の AST 走査）:
    ``marketdata.tick_m1`` の構文木に ``REGISTRY``（台帳の内部表）と ``spread_point_snapshot_of``
    （宣言欄の照会）のどちらも現れないこと。import の形を変えた迂回（属性参照・直 import・
    モジュール別名・名前の別名の 4 形式）も数える（負の対照が 2 記号 × 4 形式で実証している）。
    **強制していない範囲**: ``getattr`` のように文字列で綴った到達は構文木に名前として現れないため
    検出しない。走査が言えるのは「構文木に現れる到達が 0 件」までである。

依存方向: ``marketdata.dataset_registry`` と ``marketdata.symbol_spec_snapshot`` のみ（pandas も
tick_m1 も知らない）。宣言は marketdata/tests/test_module_dependency_declarations.py が AST 走査で
強制する。スナップショットの読込は ``marketdata.symbol_spec_snapshot`` の関数をモジュール属性経由で
呼ぶ（計算量検定の Test Spy の継ぎ目）。

point の記憶: 同じ組（サーバ名, 銘柄名）は 1 回だけ読む（:func:`_point_size_of_snapshot` のメモ化が
遅延解決の「1 回性」を担う唯一の実体）。捨てる口は :func:`forget_resolved_points`。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Callable

from marketdata import dataset_registry as _dataset_registry
from marketdata import symbol_spec_snapshot as _symbol_spec_snapshot

#: 銘柄仕様側の項目名（MT5 のフィールド名ではない。MT5 名は SYMBOL_FIELD_SOURCES の中にだけ在る）。
_POINT_FIELD = "point_size"


def ledger_owns_point(ref: "str | None") -> bool:
    """台帳がこの ``ref`` の point を持つ（＝呼出側から point を受けない ref）か。

    台帳に記述子がある ref が該当する（spread 列を持たない＝宣言 ``None`` の ref も含む。point の
    持ち主が台帳であることと、その系列が spread 列を持つかは別の問いである）。IO なし。

    台帳の記述子の表を引くのは本モジュールであり、素材化側はこの真偽だけを見る。
    """
    return ref in _dataset_registry.REGISTRY


def declared_snapshot_of(ref: "str | None") -> "tuple[str, str] | None":
    """``ref`` の宣言（どのスナップショットの point で数えるか＝所在）を返す。宣言・登録が無ければ ``None``。

    返すのは point の値ではなく宣言そのものである。スナップショットは読まない（IO なし）。

    用途は、既存 CSV の spread 列の有無が宣言と食い違ったときに「何が宣言されているか」を文面へ
    載せること。宣言欄をどう引くかは本モジュールの変更理由であり、文面を組む素材化側の変更理由では
    ないため、素材化側は台帳の口を直に呼ばない（ISSUE-511 段階 3 の段階 1・V-1）。
    """
    return _dataset_registry.spread_point_snapshot_of(ref)


def declared_point_resolver(ref: "str | None") -> "Callable[[], float] | None":
    """宣言のある ``ref`` の point を遅延解決する呼び口を返す。宣言・登録の無い ref は ``None``。

    取得しただけではスナップショットを読まない（呼ばれて初めて読む）。台帳の照会は取得時の 1 回で
    済ませ、解決の時に引き直さない（照会の答え＝スナップショットの所在を呼び口が持つ）。同じ組を
    何度解決しても読込は増えない（:func:`_point_size_of_snapshot` のメモ化。呼び口を取り直しても
    同じ）。

    Raises:
        marketdata.symbol_spec_snapshot.SnapshotError: 呼び出した時に、宣言したスナップショットが
            読めない・point が引けない。**既定値へ落とさない**（落とすと別銘柄の point で数えた
            spread が形式上正しい値として CSV に残り、状態検証では検出できない）。
    """
    pair = declared_snapshot_of(ref)
    if pair is None:
        return None
    return lambda: _point_size_of_snapshot(*pair)


def spread_point_of(ref: "str | None") -> "float | None":
    """``ref`` の spread 列を数える point を返す。宣言の無い ref・台帳に無い ref は ``None``。

    宣言の無い ref ではスナップショットを読まない（使わない point を読まない）。

    Raises:
        marketdata.symbol_spec_snapshot.SnapshotError: 宣言したスナップショットが読めない・point が
            引けない（:func:`declared_point_resolver` と同じ）。
    """
    resolver = declared_point_resolver(ref)
    return None if resolver is None else resolver()


def forget_resolved_points() -> None:
    """解決済み point の記憶を捨てる（検定が試験どうしを独立させるための口）。

    記憶の持ち方（どこに・どう覚えるか）は本モジュールの変更理由なので、外から private 属性へ直に
    触らせない。直に触らせると、記憶の持ち方を変えただけで呼出側が「失敗」ではなく「エラー」で
    倒れ、何が壊れたのか読めなくなる。
    """
    _point_size_of_snapshot.cache_clear()


@lru_cache(maxsize=None)
def _point_size_of_snapshot(server: str, symbol: str) -> float:
    """スナップショット ``(server, symbol)`` の point_size（同じ組は 1 回だけ読む）。

    読込に失敗した組はキャッシュされない（lru_cache は例外を記憶しない）。
    """
    return _symbol_spec_snapshot.load_symbol_field(server, symbol, _POINT_FIELD)


__all__ = [
    "spread_point_of",
    "ledger_owns_point",
    "declared_snapshot_of",
    "declared_point_resolver",
    "forget_resolved_points",
]
