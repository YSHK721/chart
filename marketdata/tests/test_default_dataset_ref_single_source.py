"""既定 ref（クエリ無しで表示する datasetRef）を台帳 1 箇所に置く（ISSUE-512 段階 0）。

用語（初出定義）:
    既定 ref
        ＝ URL に ``?dataset=`` が無いとき各画面が表示する datasetRef。

なぜ必要か（依頼者要件 2026-09-11）:
    「MT5 のデータ取得に問題が発生した場合、1 行書き換えるだけで Dukascopy へ切り替えられる
    ようにしたい」。実測では既定 ref が 6 ファイル 7 箇所に手書きされていた（ライブ入口・
    リプレイ入口 2 箇所・ダッシュボードの Python と JS・統合ページ・リプレイの特別扱い）。
    1 箇所でも取り残すと **画面ごとにベンダが食い違う**（どの系列を見ているか分からない）。

構成（新しい仕組みを作らない）:
    権威は Python の台帳（``DEFAULT_DATASET_REF``）。JS へは生成物で配る
    （``symbol_spec_generated.js`` と同じ既存規約・``tools/gen_js_parity_golden.py``）。
    生成物の実体は中立核（chart_kernel）に 1 つ置き、live・replay・dashboard は既存の共有 JS と
    同じ **相対** symlink で読む。統合ページはライブの公開面（``/live/js/public/``）から読む
    （統合層の配信は自分の web_root 外を realpath で拒否するため、symlink では届かない）。

本検定が固定するもの:
  1. 値ピン: 既定は ``jp225_tick`` のまま（表示は 1 バイトも変わらない）。
  2. 生成物が台帳と一致する（再生成漏れを落とす）。
  3. 共有の link は相対で、実体を指す（絶対パス・リポジトリ外を指す link を作らない）。
  4. 手書きの不在: 既定を持っていた 6 ファイルに、台帳の ref の文字列リテラルが 1 つも無い。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from marketdata import dataset_registry, tf_meta

ROOT = Path(__file__).resolve().parents[2]

#: 生成物の実体（検定は実体を読む。consumer 側の link 越しに読むと分岐を見逃す）。
JS_DEFAULT = (
    ROOT / "indigators" / "chart_kernel" / "web" / "js" / "domain" / "dataset_default_generated.js"
)

#: 実体を相対 symlink で読む画面（既存の共有 JS と同じ置き場）。
CONSUMER_LINKS = {
    "live": ROOT / "indigators" / "indicator_ui" / "web" / "js" / "domain"
    / "dataset_default_generated.js",
    "replay": ROOT / "simulator" / "replay_ui" / "web" / "js" / "domain"
    / "dataset_default_generated.js",
    "dashboard": ROOT / "dashboard_ui" / "web" / "js" / "domain" / "dataset_default_generated.js",
}

#: 既定 ref を手書きしていた 6 ファイル（ISSUE-512 実測 2026-09-11）。
HAND_WRITTEN_BEFORE = {
    "live_entry": ROOT / "indigators" / "indicator_ui" / "web" / "index.html",
    "replay_entry": ROOT / "simulator" / "replay_ui" / "web" / "index.html",
    "dashboard_front": ROOT / "dashboard_ui" / "web" / "js" / "adapter" / "front"
    / "composition_root_front.js",
    "dashboard_root": ROOT / "dashboard_ui" / "main" / "composition_root.py",
    "unified_root": ROOT / "unified_ui" / "web" / "js" / "unified_root.js",
    "replay_server": ROOT / "simulator" / "replay_ui" / "framework" / "serve_replay.py",
}

#: うち、既定を台帳から受け取るべきもの。replay_server は既定ではなく ref 検証の特別扱い
#: （ティック ref は既知判定を免除）で、台帳のティック判定の注入へ置き換えた。dashboard_root は
#: 本番の参照者 0 件の手書き定数だったため撤去した（既定は front が生成物から読む）。
READS_THE_DEFAULT = (
    "live_entry", "replay_entry", "dashboard_front", "unified_root",
)


# --------------------------------------------------------------------------- #
# 1. 値ピン
# --------------------------------------------------------------------------- #
def test_the_default_is_unchanged():
    """既定は ``jp225_tick`` のまま（段階 0 は置き場所の変更であり、表示は変えない）。"""
    # Arrange / Act / Assert
    assert dataset_registry.DEFAULT_DATASET_REF == "jp225_tick"


def test_the_default_is_a_registered_tick_ref():
    """既定は台帳にあるティック ref（ライブの足内更新が動く ref でなければ既定にできない）。"""
    # Arrange
    ref = dataset_registry.DEFAULT_DATASET_REF

    # Act / Assert
    assert ref in dataset_registry.REGISTRY
    assert tf_meta.is_tick_ref(ref)


# --------------------------------------------------------------------------- #
# 2. 生成物
# --------------------------------------------------------------------------- #
def test_the_generated_js_matches_the_ledger():
    """生成物は台帳の既定から描いたものとバイト一致する（再生成漏れを落とす）。"""
    from tools.gen_js_parity_golden import render_dataset_default_js

    # Arrange
    assert JS_DEFAULT.exists(), (
        f"生成物が無い: {JS_DEFAULT}。PYTHONPATH=. python3 tools/gen_js_parity_golden.py を実行すること"
    )

    # Act
    rendered = render_dataset_default_js(dataset_registry.DEFAULT_DATASET_REF)

    # Assert
    assert rendered == JS_DEFAULT.read_text(encoding="utf-8"), (
        "台帳の既定を変えたら tools/gen_js_parity_golden.py を再実行すること"
    )


def test_the_generated_js_is_marked_and_has_no_import():
    """生成物であることを冒頭で明示し、import を持たない（依存 0 のデータ）。"""
    # Arrange
    lines = JS_DEFAULT.read_text(encoding="utf-8").splitlines()

    # Act
    imports = [ln for ln in lines if ln.startswith("import") or re.match(r"^export .* from", ln)]
    marked = "自動生成" in lines[0] and "編集しない" in lines[0]

    # Assert
    assert marked, f"冒頭行に生成物の明示が無い: {lines[0]!r}"
    assert imports == []


# --------------------------------------------------------------------------- #
# 3. 共有の link（相対・実体を指す）
# --------------------------------------------------------------------------- #
def test_each_consumer_link_is_relative_and_points_at_the_kernel_file():
    """live・replay・dashboard の link は相対で、中立核の実体を指す。

    絶対パスの link は別チェックアウトへ展開されると自己参照になり実体を失う（ISSUE-363）。
    ここで許すのはリポジトリ内を相対で指す既存の共有 JS と同じ形だけである。
    """
    # Arrange / Act
    wrong = {
        name: (os.readlink(p) if p.is_symlink() else "（link ではない）")
        for name, p in CONSUMER_LINKS.items()
        if not p.is_symlink()
        or os.path.isabs(os.readlink(p))
        or p.resolve() != JS_DEFAULT.resolve()
    }

    # Assert
    assert wrong == {}, f"共有の link が相対で実体を指していない: {wrong}"


# --------------------------------------------------------------------------- #
# 4. 手書きの不在（1 行切替の前提）
# --------------------------------------------------------------------------- #
def _quoted_refs(text: str) -> "list[str]":
    """``text`` に文字列リテラルとして現れる台帳の ref（'jp225_tick' / "jp225_tick" 等）。"""
    return sorted(
        ref for ref in dataset_registry.REGISTRY
        if re.search(rf"""['"]{re.escape(ref)}['"]""", text)
    )


def test_no_file_that_held_the_default_hand_writes_a_ref():
    """既定を手書きしていた 6 ファイルに、台帳の ref の文字列リテラルが無い。

    1 つでも残ると、台帳を 1 行変えてもその画面だけ旧ベンダを表示し続ける。
    """
    # Arrange / Act
    hits = {
        name: _quoted_refs(path.read_text(encoding="utf-8"))
        for name, path in HAND_WRITTEN_BEFORE.items()
    }
    left = {name: refs for name, refs in hits.items() if refs}

    # Assert
    assert left == {}, f"ref を手書きしているファイルがある: {left}"


def test_every_screen_takes_the_default_from_the_ledger():
    """既定を使う 4 ファイルは、台帳由来の名前（DEFAULT_DATASET_REF）を読む（空振り防止）。"""
    # Arrange / Act
    missing = [
        name for name in READS_THE_DEFAULT
        if "DEFAULT_DATASET_REF" not in HAND_WRITTEN_BEFORE[name].read_text(encoding="utf-8")
    ]

    # Assert
    assert missing == [], f"台帳の既定を読んでいない: {missing}"
