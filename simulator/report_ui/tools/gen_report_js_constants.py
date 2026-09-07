"""report_ui front 定数の JS 生成物を書き出す（ISSUE-502 段階 2・D-2 / D-3）。

規則の権威は Python（``simulator.report_ui.usecase.derive``）。本スクリプトが
``web/js/derive_constants_generated.js`` を書き出し、front（heatmap.js / graphs.js）は
生成された値を **読むだけ** にする。

なぜ生成物なのか（実測 2026-09-06 の SOLID 精査 D-2 / D-3）:
    同じ 2 つの事実（曜日順・hold バケット境界）が Python と JS の両方に手書きで存在し、
    突合手段が 0 件だった。ずれても例外は出ない——front のフィルタが back の集計と
    **別の trade** を選ぶだけで、画面は正常に見える（`weekorder` は payload にも載るため
    表示は合い、クリック抽出だけが静かに割れる）。同型の壊れ方は ISSUE-253（floorable の
    写しがずれ、1W/1M だけ tick 再生から脱落）で実際に起きている。定義は Python ただ 1 つと
    し、再生成漏れは parity 検定（``tests/unit/test_js_constants_single_source.py``）が落とす。

    採用したのはリポジトリ実証済みの方式（``tools/gen_js_parity_golden.py`` →
    ``tf_ledger_generated.js`` → ``marketdata/tests/test_tf_ledger_parity.py``）と同一である。

実行: PYTHONPATH=. python3 simulator/report_ui/tools/gen_report_js_constants.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from simulator.report_ui.usecase import derive  # noqa: E402

#: 生成物の置き場（front から相対 import される葉モジュール）。
JS_OUT = _REPO / "simulator" / "report_ui" / "web" / "js" / "derive_constants_generated.js"


def render_derive_constants_js(week: "list[str]", holds: "list[tuple]") -> str:
    """曜日順と hold バケット境界の JS モジュール（データのみ・自動生成）を組み立てる。"""
    week_items = ", ".join(f'"{w}"' for w in week)
    hold_items = ",\n".join(
        '  {{ lo: {lo}, hi: {hi}, label: "{label}" }}'.format(lo=int(lo), hi=int(hi), label=lab)
        for lo, hi, lab in holds
    )
    return (
        "// derive_constants_generated.js — front 表示定数（**自動生成・手で編集しない**）。\n"
        "//\n"
        "// 生成元: simulator/report_ui/usecase/derive.py の WEEK / HOLD_BUCKET_BOUNDS。\n"
        "// 生成器: simulator/report_ui/tools/gen_report_js_constants.py（規則変更時に再実行する）。\n"
        "//\n"
        "// なぜ生成物なのか（ISSUE-502 D-2 / D-3）: 同じ事実を JS 側にも書くと第 2 定義になり、\n"
        "//   ずれても例外が出ない。曜日順がずれれば front のフィルタが back の集計と別の trade を\n"
        "//   選び、hold 境界がずれれば「保有時間別損益」の棒と抽出結果が食い違う——いずれも画面は\n"
        "//   正常に見えたまま静かに壊れる（ISSUE-253 と同型）。定義は Python ただ 1 つとし、JS は\n"
        "//   生成された値を読むだけにする。陳腐化は\n"
        "//   simulator/report_ui/tests/unit/test_js_constants_single_source.py が落とす。\n"
        "//\n"
        "//   WEEKORDER          : wday インデックス規約（Mon=0..Sun=6・UTC 基準）\n"
        "//   HOLD_BUCKET_BOUNDS : 保有時間バケット境界 [lo, hi) 半開区間とラベル\n"
        "export const WEEKORDER = Object.freeze([" + week_items + "]);\n"
        "\n"
        "export const HOLD_BUCKET_BOUNDS = Object.freeze([\n" + hold_items + ",\n"
        "].map(Object.freeze));\n"
    )


def main() -> None:
    js = render_derive_constants_js(list(derive.WEEK), list(derive.HOLD_BUCKET_BOUNDS))
    JS_OUT.write_text(js, encoding="utf-8")
    print(f"wrote {JS_OUT}")


if __name__ == "__main__":
    main()
