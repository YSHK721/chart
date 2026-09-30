"""JSON API の出口の共有部品（adapter 層・ISSUE-552/554 段階 2-1）。

責務（SRP）: **HTTP の JSON 応答へ出す直前の翻訳**のうち、API をまたいで同じ規則であるもの。
    - 「`json_safe`」: payload 木の非有限値を null にする。
    - 「`guarded`」: UC の失敗を HTTP の状態へ翻訳する。

なぜ共有するか: 分析 API（`/trace`）と足の API（`/chart-bars`）は、公開可否の関門
（`usecase/fetch_job_result.py`）も非有限値の出方（維持率は建玉 0 の足で無限大）も同じである。
controller ごとに写しを持つと、同じ問いに 2 つの答えができ、片方だけ直る形で必ず食い違う。
本体は `simulator/sim_ui/adapter/trace_api_controller.py` に在ったものを**挙動を変えずに**
移した（翻訳表の順序・文言・戻り値の形は同じ）。

「`guarded`」 が API ごとの例外型を引数で受ける理由: 「成果物が無い」「量が上限を超える」は
各 API の usecase が自分の型で表す（足の API が分析 API の例外型を借りると、別のアクターの
語彙へ依存する）。状態の割り当て（404 / 413）だけが共通である。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Mapping

from simulator.sim_ui.usecase.job_models import (
    JobNotFoundError,
    ResultNotAvailableError,
)


def guarded(
    call: "Callable[[], tuple[int, dict]]",
    *,
    missing: "tuple[type[BaseException], ...]",
    too_wide: "tuple[type[BaseException], ...]",
) -> "tuple[int, dict]":
    """UC の失敗を HTTP の状態へ翻訳する。表に無い例外は握らない。

    状態の割り当ては既存の `/data/{job_id}/{filename}` と揃える（同じ問いに
    2 つの答えを作らない）:
        404 ジョブが無い / 成果物が無い（``missing``）/ 識別子が受理形でない
        409 ジョブが完了していない（部分結果の非公開）
        413 返す量が上限を超える（``too_wide``・間引かずに断る）
        400 範囲の指定そのものが不正
    """
    try:
        return call()
    except JobNotFoundError as exc:
        return 404, {"error": str(exc)}
    except missing as exc:
        return 404, {"error": str(exc)}
    except ResultNotAvailableError as exc:
        return 409, {"error": str(exc)}
    except too_wide as exc:
        return 413, {"error": str(exc)}
    except ValueError as exc:
        # 台帳が受理しない識別子・ファイル名（CWE-22 防御）もここへ来る。
        return 400, {"error": str(exc)}


def json_safe(value: Any) -> Any:
    """payload 木を再帰的に通し、**JSON に存在しない綴りになる値を `None` にする**。

    なぜ木を通すか（拡張点の確保・工程 5 レビュー 🔴-4）:
        工程 3 はフィールドごとに変換を手書きし、`_points` の 4 群へは撒いたが
        `/extent` に忘れた。front が最初に叩くのは `/extent` なので、分析タブは実 run で
        全面が掲示のみになる。**手書き適用は「足したフィールドを通し忘れる」という
        欠陥を構造的に許す**——出口で木ごと通せば、忘れる場所が存在しない。

    何を `None` にするか（実測・2026-09-10／09-11）:
        非有限の実数（`inf` / `-inf` / nan）。`Account.margin_level()` は建玉 0 の点で
        無限大を返し、実ティック 1 ヶ月 run の 1,036,394 点のうち **405,941 点（39.2%）**
        がそれに当たる。`initial_deposit` / `margin_level_floor` も run 設定次第で
        非有限になり得る（実測で `/extent` の直列化が壊れることを確認）。
        Python の `json.dumps` は既定でこれを `Infinity` / `NaN` と書くが、**どちらも JSON の
        文法に無い**——ブラウザの JSON.parse は `SyntaxError` で落ちる（node 実測）。
        Python の json.loads は非標準拡張として受理するため、Python 側だけで測ると
        欠陥が緑のまま通る（検定は `parse_constant` で厳格に読む）。

    なぜ `None` か:
        JSON は無限大を表現できない。値を発明する（極大の数で代用する）と front が
        それを実在の維持率として描き、軸が壊れる。「有限の数値ではない」ことをそのまま
        運ぶ唯一の綴りが null である。front の系列描画は `Number.isFinite` で弾く。

    なぜ `ApiResponse.to_bytes` 側を直さないか:
        同型は既存の全 API 応答が共有しており、そこへ手を入れると既存応答の byte が
        変わり得る（本段階の「既存面は 1 バイトも変えない」制約に反する）。非有限値が
        出るのは足ごと・評価点ごとの列（維持率・DD・run 設定）に固有の事実なので、
        それを配る API の controller が自分の出口で本関数を通す。

    `bool` を数値として扱わない: `isinstance(True, float)` は偽なので素通りするが、
    halted 列が壊れると事象導出の突合ができなくなるため検定で固定している。
    """
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, Mapping):
        return {key: json_safe(item) for key, item in value.items()}
    # `str` / `bytes` は列ではない（1 文字ずつ分解すると payload が壊れる）。
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value
