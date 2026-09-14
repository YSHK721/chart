"""`simulator/tests` 配下の全起動形に共通するレポートヘッダ（内部設計 §9.3）。

置き場所をここにする理由（実測 2026-08-17）:
    pytest は起動時に「引数として与えられたディレクトリとその祖先」の conftest しか
    読み込まない。降下先の conftest は収集中に遅延ロードされるため
    `pytest_report_header` の呼び出しには間に合わない。実測では
    `pytest simulator/tests/regression` ではヘッダが出る一方、
    `pytest simulator/tests` では出なかった。`simulator/tests` はすべての起動形
    （`simulator/tests` / `simulator/tests/unit` / `simulator/tests/regression` …）の
    祖先であるため、ここに置くことで表示が起動形に依存しなくなる。

表示内容の実装は `simulator/tests/regression/corpus_cases.corpus_report_lines()` の
1 箇所だけにある。本ファイルはフックの受け口であり、文言を持たない（同じ文言を
複数の conftest へ書き写さない）。判定機構そのものは
`simulator/tests/unit/tester_settings_corpus.py`（内部設計 §9.3 D-06 の単一ソース）
が持ち、本ファイルからも間接的にそれだけを参照する。

import を関数内で行う理由:
    本ファイルは `simulator/tests` 配下の**全テスト**（2700 件超）の収集前に読まれる。
    モジュール先頭で設定モジュール群を import すると、それらが壊れている間は
    無関係なテストまで収集不能になる（既存テストへの副作用）。遅延 import にし、
    失敗時は収集を止めずヘッダへ理由を出す。ヘッダを黙らせないため例外は握り潰さず
    型と文言を表示する。
"""
from __future__ import annotations


def pytest_report_header(config) -> list[str]:
    """corpus の有無・件数・必須化フラグを毎回表示する（沈黙スキップを作らない）。"""
    del config  # 未使用（フックのシグネチャ要件）
    try:
        from simulator.tests.regression.corpus_cases import corpus_report_lines
    except Exception as exc:  # noqa: BLE001 - 収集を止めず理由を可視化するための捕捉
        return [
            "tester-settings corpus: UNKNOWN"
            f"（状態を判定できない: {type(exc).__name__}: {exc}）"
        ]
    return corpus_report_lines()
