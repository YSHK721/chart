"""ISSUE-502 D-9: 表示時間足の並びが Python ただ 1 つで定義されていることを固定する。

方向: `dashboard_ui/domain/horizon.py` の ``TIMEFRAME_ORDER`` が唯一の定義で、
front はその射影（`web/js/domain/dashboard_timeframes_generated.js`）を読むだけ。
生成器は `tools/gen_js_parity_golden.py`（時間足台帳・銘柄仕様と同じ入口）。

なぜ必要か（精査台帳 .doc/solid_audit_20260906.md D-9 の実測）:
    かつて front 側にも同じ 8 本が手書きで並んでおり、Python と突き合わせる検定が
    **1 本も無かった**。この並びは第 2 表の列（`oscillator_sheet_view`）と
    テンプレートの束（`template_binding_reader`）の両方を決めるため、片側だけ足すと
    列と束が食い違う。食い違っても表は表示され続ける（例外も空表示も出ない）ので、
    出力を見る検査では原理的に落ちない — ISSUE-253（floorable の写しのずれ）・
    ISSUE-254（時間足台帳）と同型の「静かなずれ」である。

本検定が落とすもの:
    1. 生成器を再実行し忘れた（Python を変えたのに JS が古い）
    2. 生成物を手で書き換えた（生成元と食い違う）
    3. front 側に第 2 の並びを書き戻した（再発）

対称形: 時間足台帳の同型検定は `marketdata/tests/test_tf_ledger_parity.py`。
"""
from __future__ import annotations

import re
from pathlib import Path

from dashboard_ui.domain.horizon import TIMEFRAME_ORDER

#: `test_front_timeframes_parity.py` → contract → tests → dashboard_ui。
_DASHBOARD_ROOT = Path(__file__).resolve().parents[2]

#: 並びの射影（生成物）。
_GENERATED = (
    _DASHBOARD_ROOT / "web" / "js" / "domain" / "dashboard_timeframes_generated.js"
)

#: 生成物を再輸出する front の入口（ここに並びを書き戻していないことも見る）。
_FRONT_ENTRY = _DASHBOARD_ROOT / "web" / "js" / "adapter" / "front" / "timeframes.js"

#: 生成物の宣言（形が変わったら一致ではなく**不在**で落とす）。
_DECL = re.compile(
    r"export\s+const\s+DASHBOARD_TIMEFRAMES\s*=\s*Object\.freeze\(\[([^\]]*)\]\)"
)

#: 並びの手書き復活を見つける印（時間足コードが 2 つ以上並ぶ配列リテラル）。印そのものを
#:   唯一定義（TIMEFRAME_ORDER）から組み立てるので、コードをここへ書き写さない。
#:   `TIMEFRAME_REFRESH_MS` のようなオブジェクトのキー（`'1m': 60_000`）は対象外。
_TF_ALTERNATION = "|".join(re.escape(tf) for tf in TIMEFRAME_ORDER)
_HANDWRITTEN_ARRAY = re.compile(
    rf"\[\s*['\"](?:{_TF_ALTERNATION})['\"]\s*,\s*['\"](?:{_TF_ALTERNATION})['\"]"
)


def _codes_in_generated_js() -> "list[str]":
    match = _DECL.search(_GENERATED.read_text(encoding="utf-8"))
    assert match is not None, (
        f"{_GENERATED.name} に DASHBOARD_TIMEFRAMES の宣言が見つかりません"
        "（生成器 tools/gen_js_parity_golden.py と本検定の読み取り規則が食い違っています）"
    )
    return re.findall(r"'([^']+)'", match.group(1))


def _code_lines(path: Path) -> str:
    """行コメント（`//`）を除いた実コード。説明文中の引用を違反と数えないため。"""
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("//")
    )


def test_the_generated_projection_matches_the_domain_order() -> None:
    """生成物の並び（順序込み）が domain の唯一定義と一致する。"""
    assert _codes_in_generated_js() == list(TIMEFRAME_ORDER), (
        "TIMEFRAME_ORDER を変えたら tools/gen_js_parity_golden.py を再実行すること"
    )


def test_the_generated_projection_is_byte_identical_to_the_generator_output() -> None:
    """生成物＝生成器の再レンダリングと byte 一致（手編集・陳腐化の両方を落とす）。

    マーカー文言の有無を文字列で見る形は「被検査ソースへの grep」（品質検定 C2 の禁止形）
    なので、生成器そのものを唯一の正として全文一致で突き合わせる。マーカーは生成器が
    書くため、一致していれば必ず付いている。
    """
    from importlib import util as _importlib_util

    gen_path = _DASHBOARD_ROOT.parent / "tools" / "gen_js_parity_golden.py"
    spec = _importlib_util.spec_from_file_location("gen_js_parity_golden", gen_path)
    assert spec is not None and spec.loader is not None
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rendered = module.render_dashboard_timeframes_js(TIMEFRAME_ORDER)
    assert rendered == _GENERATED.read_text(encoding="utf-8"), (
        "生成物が生成器の出力と一致しません。tools/gen_js_parity_golden.py を再実行すること"
    )


def test_the_front_entry_re_exports_instead_of_declaring_the_order() -> None:
    """front の入口は生成物を再輸出するだけで、自分では並びを宣言しない。"""
    code = _code_lines(_FRONT_ENTRY)
    assert "dashboard_timeframes_generated.js" in code, (
        f"{_FRONT_ENTRY.name} が生成物を読んでいません（第 2 定義の復活）"
    )
    assert not _HANDWRITTEN_ARRAY.search(code), (
        f"{_FRONT_ENTRY.name} に時間足の並びが手書きで復活しています"
    )


def test_the_reader_is_not_vacuous() -> None:
    """読み取り規則の生存確認（空配列や 0 件一致で恒真化していない）。"""
    codes = _codes_in_generated_js()
    #: 本数そのものは焼き込まない（設計の並びを固定するのは tests/unit/test_horizon.py）。
    #: ここで見るのは「読み取りが 0 件一致で恒真化していない」ことだけ。
    assert len(codes) == len(TIMEFRAME_ORDER) > 1
    reintroduced = "const x = [%s];" % ", ".join(f"'{tf}'" for tf in TIMEFRAME_ORDER)
    assert _HANDWRITTEN_ARRAY.search(reintroduced), (
        "手書き復活の検出規則が何も見つけられない（検定が空振りする）"
    )
