"""テストが「本番が作らないオブジェクト」を検証していないことを強制する（ISSUE-255 追補）。

## 何を防ぐか（実際に起きた事故）

ISSUE-275: ライブ追従トグルが実 UI で押せなくなっていたのに、単体テストは緑だった。
    - 本番の合成根は `LiveFollowController` を **`mode` を渡さずに**構築する（ISSUE-269 で
      A方式を撤去した際に引数が消えた）。
    - しかしテストは全構築で `mode: 'b'` を**明示注入**していた。`mode` が在る世界でだけ
      機能が配線される実装だったため、テストは「本番が作らないオブジェクト」を検証し続けた。
    - 結果、機能が完全に失われても検定は最後まで緑。

同型は ISSUE-277（本番の配信ページに無い DOM をテストが自分で用意）でも起きている。
共通するのは「テストが前提を自分で満たしていた」こと。テストは正しく通っていたが、
**通っていた対象が本番ではなかった**。

## 施行する不変条件

合成根が options オブジェクトで構築するクラス C について、
「本番は渡さないが、テストは渡すキー K」があるなら、
**K を渡さずに C を構築するテストが少なくとも 1 つ存在すること**。

つまり seam（`now` などの決定論クロック注入）は許す。許さないのは
「本番の形（K 不在）を一度も通さないまま、注入済みの形だけで緑にすること」。

## 限界（明示）

`tools/js_ctor_scan` は構文解析器ではない（位置引数・spread は対象外）。したがって本テストは
「検出できたものは確実に問題」側へ倒してある。網羅性は主張しない。実 UI 検証の代替ではない。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.js_ctor_scan import collect

_ROOT = Path(__file__).resolve().parents[2]

#: 合成根（本番配線）のソース。ここで組まれた形が「本番が実際に作るオブジェクト」。
_COMPOSITION_ROOTS = [
    "indigators/indicator_ui/web/js/adapter/front/composition_root_front.js",
    "indigators/indicator_ui/web/js/adapter/front/chart_app_wiring.js",
    "simulator/replay_ui/web/js/adapter/front/composition_root_front.js",
    "unified_ui/web/js/unified_root.js",
]


def _test_sources() -> "list[Path]":
    """フロント全スイートのテストソース（対象は tools/web_suites.txt が唯一源・ISSUE-280）。

    スイートを跨いで構築されるクラス（例: MP クライアントは market_profile 側で構築テストがある）を
    取りこぼさないよう、テスト側コーパスは**全スイートの合併**にする。
    """
    ledger = (_ROOT / "tools" / "web_suites.txt").read_text(encoding="utf-8")
    dirs = [ln.strip() for ln in ledger.splitlines() if ln.strip() and not ln.startswith("#")]
    out: "list[Path]" = []
    for d in dirs:
        out.extend(sorted((_ROOT / d / "tests").glob("*.test.js")))
    return out


def _root_sources() -> "list[Path]":
    paths = [_ROOT / rel for rel in _COMPOSITION_ROOTS]
    missing = [str(p) for p in paths if not p.exists()]
    assert not missing, f"合成根が見つかりません（移動したら本表を更新）: {missing}"
    return paths


def test_composition_roots_exist():
    """合成根の一覧が実在する（파일移動で走査が空振りしていないこと）。"""
    assert len(_root_sources()) == len(_COMPOSITION_ROOTS)


def test_scan_finds_constructions():
    """走査が空振りしていない（0 件で緑になる無意味なテストにしない）。"""
    prod = collect(_root_sources())
    tests = collect(_test_sources())
    assert len(prod) >= 10, f"合成根の構築が拾えていません: {sorted(prod)}"
    assert len(tests) >= 10, f"テストの構築が拾えていません: {sorted(tests)}"


def test_no_test_only_precondition_without_production_form():
    """本番の形（キー不在）を一度も通さないテストが無い（ISSUE-275 型の検出）。"""
    prod = collect(_root_sources())
    tests = collect(_test_sources())

    violations: "list[str]" = []
    for cls, prod_key_sets in prod.items():
        test_key_sets = tests.get(cls)
        if not test_key_sets:
            continue  # 構築テストが無いクラスは本テストの対象外（別の観点）。
        prod_keys: "set[str]" = set().union(*prod_key_sets)
        test_keys: "set[str]" = set().union(*test_key_sets)
        for key in sorted(test_keys - prod_keys):
            without = [ks for ks in test_key_sets if key not in ks]
            if not without:
                violations.append(
                    f"{cls}.{key}: 本番の合成根は渡さないのに、全テストが渡しています"
                    "（本番が作る形＝当該キー不在の構築を検証していない）"
                )

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize("rel", _COMPOSITION_ROOTS)
def test_each_composition_root_is_executed_by_some_test(rel: str):
    """各合成根が、少なくとも 1 つのテストから**実際に import されて実行**される。

    「本番と同じものを組ませる」ための最低条件。スタブを手で組み立てるテストだけになると、
    合成根の変更（引数の削除など）が誰にも検出されない（ISSUE-275 の入口）。
    """
    module = Path(rel).name
    hits = [p.name for p in _test_sources() if module in p.read_text(encoding="utf-8")]
    assert hits, f"{rel} を import して実行するテストがありません"
