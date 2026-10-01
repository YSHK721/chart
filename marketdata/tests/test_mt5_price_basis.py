"""jp225_mt5 の価格基準が bid であることの検定（ISSUE-447 / 依頼者裁定 2026-09-02）。

なぜ bid なのか（実測の出典）:
    MT5 端末のチャートは bid を描いている。ISSUE.md 段階 0 実測 T5 が、同日の Dukascopy M1
    （mid）と MT5 M1 を突き合わせて中央値 ``duka(mid) - mt5(bid) = +6.97`` を得ており
    （MT5 のスプレッド平均 11.41 のちょうど半分に相当・T7）、``chart_mode=0`` と整合する。
    同じティックから mid で M1 を作ると、端末表示に対して系統的に半スプレッドぶんずれる。

本ファイルが固定するもの:
    1. **両経路**（日中増分＝``m1_chain`` / 日次権威再構築＝``rebuild``）が bid で M1 を作る
    2. 基準の宣言は**台帳**（marketdata/dataset_registry.py）にただ 1 つあり、各経路は ref を
       渡すだけである（本パッケージは基準を宣言も送信もしない・段階 6・TBD-4）
    3. 増分と権威が**同じ基準**であること（片方だけが mid だと、日次再構築が表示中の系列を
       静かに mid へ書き戻す。出力は「それらしい」ので状態検証では気付けない）
    4. M-4 同値性（ジャーナル畳み == 確定 parquet 畳み）が bid でも成立し続けること
"""
from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import pandas as pd

from marketdata import dataset_registry, tick_m1
from marketdata.mt5_ticks import journal, m1_chain, rebuild

_PKG = Path(tick_m1.__file__).resolve().parent / "mt5_ticks"
_TOKEN = "JP225@OANDA-Japan-MT5-Live"
_REF = "jp225_mt5"
_DAY = dt.date(2026, 8, 25)
#: 気配幅（実測平均 11.41 と同オーダー）。bid と mid が必ず食い違う値にする。
_SPREAD = 10.0


def _label_ms(utc: dt.datetime) -> int:
    """UTC の壁時計をサーバ時刻ラベル（UTC+3）の epoch ms へ（既存検定と同じ変換）。"""
    return int(utc.replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + 3 * 3600 * 1000


def _day_rows(minutes: int = 5, per_minute: int = 20, *, phantom=()):
    """``minutes`` 分ぶんのティックと、その終端（形成中の分の境界）。"""
    start = dt.datetime(2026, 8, 25, 9, 0)
    rows = []
    for m in range(minutes):
        for i in range(per_minute):
            when = start + dt.timedelta(minutes=m, seconds=i * (60.0 / per_minute))
            bid = 15100.0 if m in phantom else 66000.0 + m * 2.0 + i * 0.1
            rows.append((_label_ms(when), bid, bid + _SPREAD))
    return rows, start + dt.timedelta(minutes=minutes)


def _bid_of(row) -> float:
    return row[1]


# =====================================================================
# 経路 1: 日中増分（m1_chain）
# =====================================================================

def test_the_intraday_fold_builds_the_m1_from_the_bid_series(tmp_path):
    """日中増分の M1 は bid から作られる（mid ではない）。"""
    # Arrange
    rows, until = _day_rows(minutes=3, per_minute=10)

    # Act
    m1_chain.append_m1_for_closed_minutes(
        rows, ref=_REF, data_dir=tmp_path, until=until.replace(tzinfo=dt.timezone.utc)
    )

    # Assert: 最初の分の open は最初のティックの bid（mid ならこれより半スプレッド大きい）。
    written = pd.read_csv(tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path))
    assert written["open"].iloc[0] == _bid_of(rows[0]), (
        f"日中増分の M1 が bid ではありません: {written['open'].iloc[0]}"
        f"（bid={_bid_of(rows[0])} / mid={_bid_of(rows[0]) + _SPREAD / 2}）"
    )
    # 最終バーの close は最後のティックの bid（mid なら半スプレッドぶん大きい）。
    assert written["close"].iloc[-1] == _bid_of(rows[-1])


# =====================================================================
# 経路 2: 日次権威再構築（rebuild）
# =====================================================================

def test_the_authoritative_day_m1_builds_from_the_bid_series(tmp_path):
    """確定 parquet からの権威 M1 も bid から作られる。"""
    # Arrange: 1 日ぶんを確定 parquet まで進める。
    rows, _ = _day_rows(minutes=3, per_minute=10)
    journal.append(_DAY, rows, symbol=_TOKEN, data_dir=tmp_path)
    assert journal.finalize(_DAY, symbol=_TOKEN, data_dir=tmp_path) == "written"

    # Act
    authoritative = rebuild.authoritative_day_m1(_DAY, symbol=_TOKEN, ref=_REF, data_dir=tmp_path)

    # Assert
    assert authoritative["open"].iloc[0] == _bid_of(rows[0]), (
        f"権威経路の M1 が bid ではありません: {authoritative['open'].iloc[0]}"
    )


def test_a_clean_day_needs_no_replacement_because_both_paths_share_the_basis(tmp_path):
    """清浄日の再構築は :data:`rebuild.UNCHANGED`（両経路が同じ基準である証拠）。

    片方だけが mid のままなら、ここは毎日 :data:`rebuild.REPLACED` になり、日次再構築が
    表示中の系列を静かに書き戻す。出力はどちらも「それらしい」ので、この検定が無いと気付けない。
    """
    # Arrange
    rows, until = _day_rows(minutes=4, per_minute=10)
    journal.append(_DAY, rows, symbol=_TOKEN, data_dir=tmp_path)
    journal.finalize(_DAY, symbol=_TOKEN, data_dir=tmp_path)
    m1_chain.append_m1_for_closed_minutes(
        rows, ref=_REF, data_dir=tmp_path, until=until.replace(tzinfo=dt.timezone.utc)
    )

    # Act
    outcome = rebuild.rebuild_day(
        _DAY, symbol=_TOKEN, ref=_REF, data_dir=tmp_path, update_rollups=False
    )

    # Assert
    assert outcome == rebuild.UNCHANGED, (
        "清浄日なのに再構築が置換を行いました（増分と権威で価格基準が食い違っています）。"
    )


# M-4 同値性（ジャーナル畳み == 確定 parquet 畳み・外れ値日の再構築後の記録 == 全量経路）は
#   ``marketdata/tests/test_mt5_equivalence.py`` が持つ。本裁定に合わせて、あちらは突合の
#   **両側**を台帳の基準で回すよう揃えた。ここで同じ突合を繰り返すと検定が 2 箇所に割れ、
#   片方だけが基準を追随して静かに食い違う。


# =====================================================================
# 単一宣言（台帳が唯一の源）
# =====================================================================

def test_the_ledger_declares_the_price_basis_as_bid():
    """基準の宣言は**台帳**にあり bid である（``tick_m1`` の識別子そのものを指す）。"""
    assert dataset_registry.tick_price_basis(_REF) == tick_m1.PRICE_BASIS_BID


def _modules_defining(name: str) -> "list[str]":
    """パッケージ内で ``name`` を代入定義しているモジュール名を集める。"""
    out: "list[str]" = []
    for path in sorted(_PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = (
                node.targets if isinstance(node, ast.Assign)
                else [node.target] if isinstance(node, ast.AnnAssign) else []
            )
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                out.append(path.name)
    return sorted(set(out))


def test_no_module_in_the_package_declares_a_price_basis():
    """本パッケージは基準を宣言しない（宣言が 2 箇所になった瞬間、片方だけ直って系列が割れる）。

    かつては ``ingest.PRICE_BASIS`` がこの宣言を持ち、台帳と合わせて 2 源になっていた。両者の
    一致は本ファイルが事後に確認していただけなので、台帳だけを切り替えれば日中経路が旧基準で
    走った（段階 6・TBD-4 の裁定 2026-09-17）。
    """
    assert _modules_defining("PRICE_BASIS") == []


def _call_sites_passing_a_basis() -> "list[str]":
    """``price_basis=`` を渡している呼出位置を集める（値が綴りでも参照でも数える）。

    直書きだけを数えていた頃より広い。段階 6 以降は「宣言を経由せずに基準を決めている呼出」
    だけでなく、**基準を渡すこと自体**が 2 源の入口だからである（渡す限り、渡し忘れた経路と
    渡した経路が別の基準で走り得る）。転送層が同じ綴りをフィールド名として持っていても、
    ここは ``price_basis`` という**キーワード引数**だけを見るので偽陽性にならない。
    """
    out: "list[str]" = []
    for path in sorted(_PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "price_basis":
                    out.append(f"{path.name}:{node.lineno}")
    return sorted(out)


def test_neither_path_passes_a_basis_because_the_authority_pulls_it():
    """両経路とも ``price_basis`` を渡さない（ref だけを渡し、素材化の権威が台帳から引く）。

    渡す形に戻すと同じ事実が台帳と引数の 2 源になり、渡し忘れた経路だけが既定（mid）で走る。
    その実例が V-6 で、CLI が台帳の bid を名乗る置き場へ mid の足を書いていた（2026-09-17 実測）。
    """
    assert _call_sites_passing_a_basis() == [], (
        "MT5 経路が価格基準を渡しています。ref だけを渡して台帳から引かせてください"
        "（2 源になると、台帳を切り替えたとき片方だけが旧基準で走ります）。"
    )
