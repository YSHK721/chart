"""基本設計書を読む**唯一のリーダー**（§7.1「期待値の出所が本書であること」の担保）。

本モジュールだけが .doc/PRICE_LEVEL_REACH_SHEET_BASIC_DESIGN.md を開く。検査側
（`test_design_doc_matches_compute.py`）は実装からも本書からも期待値を組み立てず、ここが
返した値だけを使う。読む先を 2 か所に増やした時点で「本書が唯一の機械可読源」（§7.1.1）が
崩れるため、パスの解決も抽出規則も本モジュールが所有する。

読む対象は 3 つある。

1. §7.1.1 の機械可読ブロック（```yaml で囲まれた `# CONTRACT: series-names`）。
   検査が突き合わせる期待値はすべてここから来る。
2. §3.1 の表と、その直後の「価格スケールに乗らない系列」の段落。人間向けの写しであり、
   1 と一致することを同じ検査で固定する（§7.1「複製を機械的に縛る」）。
3. §3.2 の表。列「水準系列」は完全な系列名ではなく**接尾辞のひな型**（`_q{pct}` など）で
   書かれているため、展開できるひな型だけを対象にする（§7.1「文章の言い換えは対象にしない」）。

YAML の `yes:` / `no:` について:
    PyYAML は YAML 1.1 の真偽値解決を行うため、引用符の無い `yes` / `no` は `True` / `False`
    という **bool のキー**になる。本書の見た目（`yes:` / `no:`）と検査側の語彙を一致させる
    ため、リーダーが文字列キーへ正規化する。正規化を検査側へ漏らすと、本書の表記を変えた
    ときに直す場所が 2 か所になる。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

import yaml

#: 本書の位置。`design_doc_contract.py` → contract → tests → dashboard_ui → リポジトリ根。
DESIGN_DOC = (
    Path(__file__).resolve().parents[3] / ".doc" / "PRICE_LEVEL_REACH_SHEET_BASIC_DESIGN.md"
)

#: §7.1.1 の機械可読ブロックの目印（本書がこの 1 行で自分を名乗っている）。
_CONTRACT_MARKER = "# CONTRACT: series-names"

#: 系列名のひな型に現れる置換（§7.1.1: instance の "q_low" / "q_high" を百分率の整数へ）。
_Q_LOW_TOKEN = "{q_lo}"
_Q_HIGH_TOKEN = "{q_hi}"

#: §3.2 の表が使う分位のひな型（`_q{pct}` は下側・上側の 2 本を表す）。
_PCT_TOKEN = "{pct}"

#: バッククォートで囲まれた語（表・段落から系列名を拾う）。
_BACKTICKED = re.compile(r"`([^`]+)`")

#: 全角括弧の注記（§3.1 の表の「（価格スケール上のバンド）」など）。表からは落とす。
_PARENTHETICAL = re.compile(r"（[^（）]*）")

#: ひな型の選択肢（`{med|ext}` → med / ext）。
_BRACED = re.compile(r"\{([^{}]*)\}")


# --------------------------------------------------------------------------- #
# 本文の切り出し
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _text() -> str:
    """本書の全文（1 プロセスで 1 回だけ読む）。

    抽出関数はそれぞれ独立に本文を必要とするため、素朴に書くと 1 run で百回以上
    同じファイルを読む。読んで捨てる I/O を作らない（§7 の規律と同じ形）。
    """
    return DESIGN_DOC.read_text(encoding="utf-8")


def _section(heading: str, next_heading: str) -> str:
    """`heading` の行から `next_heading` の行の直前までを返す。"""
    body = _text()
    start = body.index(heading)
    end = body.index(next_heading, start)
    return body[start:end]


def _table_rows(section: str) -> "list[list[str]]":
    """Markdown 表の本体行（見出し行・区切り行を除く）をセルの並びで返す。"""
    rows: "list[list[str]]" = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or not cells[0].startswith("`"):
            continue  # 見出し行（`indicatorId`）と区切り行（---）を落とす
        rows.append(cells)
    return rows


def _bare(cell: str) -> str:
    """先頭のバッククォート語をそのまま返す（バッククォートで囲んだ ma_marod → ma_marod）。"""
    found = _BACKTICKED.search(cell)
    if found is None:
        raise ValueError(f"バッククォート語が見つかりません: {cell!r}")
    return found.group(1)


# --------------------------------------------------------------------------- #
# 1. §7.1.1 機械可読ブロック
# --------------------------------------------------------------------------- #
def contract() -> "Mapping[str, object]":
    """§7.1.1 の YAML ブロックを読む（`yes` / `no` は文字列キーへ正規化する）。"""
    body = _text()
    marker = body.index(_CONTRACT_MARKER)
    opening = body.rindex("```", 0, marker)
    start = body.index("\n", opening) + 1
    end = body.index("```", start)
    parsed = yaml.safe_load(body[start:end])
    return _normalise(parsed)


def _normalise(node: object) -> object:
    """bool になったキー（YAML 1.1 の `yes` / `no`）を本書の表記へ戻す。"""
    if isinstance(node, dict):
        return {_key(key): _normalise(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_normalise(item) for item in node]
    return node


def _key(key: object) -> object:
    if key is True:
        return "yes"
    if key is False:
        return "no"
    return key


def expand(names: "Iterable[str]", params: "Mapping[str, object]") -> "frozenset[str]":
    """`{q_lo}` / `{q_hi}` を instance の設定から百分率の整数へ展開する（§7.1.1）。

    展開元は**リクエストの params**（＝ユーザー設定側）であり被検査コードではない。
    分位のひな型を持たない指標（moving_averages / cvfe）は "q_low" / "q_high" を
    設定に持たないため、ひな型が現れたときにだけ設定を引く（無い設定を要求しない）。
    """
    expanded: "set[str]" = set()
    for name in names:
        if _Q_LOW_TOKEN in name:
            name = name.replace(_Q_LOW_TOKEN, str(percent(params["q_low"])))
        if _Q_HIGH_TOKEN in name:
            name = name.replace(_Q_HIGH_TOKEN, str(percent(params["q_high"])))
        expanded.add(name)
    return frozenset(expanded)


def percent(quantile: object) -> int:
    """分位を系列名の百分率へ（`0.05` → `5`）。"""
    return int(round(float(quantile) * 100.0))  # type: ignore[arg-type]


def _flatten(entry: object) -> "list[str]":
    """契約ブロックの 1 指標ぶんの宣言を系列名の並びへ均す。

    btlm_trail のように "levels" / "not_levels" へ分けて宣言している指標は、その和が
    「その指標が出す系列名の集合」である（水準か否かの区別は §3.1 の役割であって、
    `/compute` が返す集合の区別ではない）。
    """
    if isinstance(entry, list):
        return [str(name) for name in entry]
    if isinstance(entry, dict):
        names: "list[str]" = []
        for value in entry.values():
            names.extend(str(name) for name in value)
        return names
    raise TypeError(f"契約ブロックの宣言が list でも dict でもありません: {entry!r}")


def contract_series_names(
    indicator_id: str, params: "Mapping[str, object]"
) -> "frozenset[str]":
    """本書が「この指標が出す」と宣言している系列名の集合（展開済み）。"""
    return expand(_flatten(_declaration(indicator_id)), params)


def contract_levels(indicator_id: str, params: "Mapping[str, object]") -> "frozenset[str]":
    """本書が**水準**（価格スケールに乗る）と宣言している系列名の集合。"""
    entry = _declaration(indicator_id)
    names = entry["levels"] if isinstance(entry, dict) else entry
    return expand([str(name) for name in names], params)


def contract_not_levels(
    indicator_id: str, params: "Mapping[str, object]"
) -> "frozenset[str]":
    """本書が**水準でない**と宣言している系列名の集合（宣言が無ければ空集合）。"""
    entry = _declaration(indicator_id)
    if not isinstance(entry, dict):
        return frozenset()
    return expand([str(name) for name in entry.get("not_levels", ())], params)


def _declaration(indicator_id: str) -> object:
    block = contract()
    for group in ("price_scale", "oscillator"):
        section = block[group]  # type: ignore[index]
        if indicator_id in section:
            return section[indicator_id]
    raise KeyError(f"契約ブロックに宣言がありません: {indicator_id!r}")


def price_scale_ids() -> "frozenset[str]":
    return frozenset(contract()["price_scale"])  # type: ignore[arg-type]


def oscillator_ids() -> "frozenset[str]":
    return frozenset(contract()["oscillator"])  # type: ignore[arg-type]


def value_series_of(indicator_id: str) -> str:
    """オシレータの「到達する量」の系列名（宣言の先頭＝§3.2 の並びの規約）。"""
    return _flatten(contract()["oscillator"][indicator_id])[0]  # type: ignore[index]


def declared_set(group: str, key: str) -> "frozenset[str]":
    """宣言 intrabar_update / price_invertible / cumulative の "yes" / "no" を読む。"""
    return frozenset(str(name) for name in contract()[group][key])  # type: ignore[index]


def constant(name: str) -> object:
    return contract()["constants"][name]  # type: ignore[index]


# --------------------------------------------------------------------------- #
# 2. §3.1 の人間向けの表・段落
# --------------------------------------------------------------------------- #
def prose_price_scale_levels() -> "Mapping[str, frozenset[str]]":
    """§3.1 の表の「水準系列」列（全角括弧の注記は落とす）。"""
    section = _section("### 3.1 ", "### 3.2 ")
    return {
        _bare(cells[0]): frozenset(
            _BACKTICKED.findall(_PARENTHETICAL.sub("", cells[1]))
        )
        for cells in _table_rows(section)
    }


def prose_price_scale_not_levels() -> "frozenset[str]":
    """§3.1 の「価格スケールに乗らない系列も返す」段落が挙げる系列名。"""
    section = _section("### 3.1 ", "### 3.2 ")
    paragraph = next(
        block
        for block in section.split("\n\n")
        if "価格スケールに乗らない系列" in block
    )
    ids = prose_price_scale_levels().keys()
    return frozenset(name for name in _BACKTICKED.findall(paragraph) if name not in ids)


# --------------------------------------------------------------------------- #
# 3. §3.2 の人間向けの表
# --------------------------------------------------------------------------- #
def prose_oscillator_ids() -> "frozenset[str]":
    section = _section("### 3.2 ", "### 3.3 ")
    return frozenset(_bare(cells[0]) for cells in _table_rows(section))


def prose_oscillator_patterns(
    indicator_id: str, params: "Mapping[str, object]"
) -> "frozenset[str]":
    """§3.2 の「水準系列」列のひな型を展開した系列名の集合。

    列には `_q{pct}` のような**接尾辞**と `rsi_evq_ext_{hi|lo}` のような**完全名**が混在し、
    さらに「GPD 外挿」のように機械化できない語も並ぶ。展開できるひな型だけを対象にする
    （§7.1「文章の言い換えは対象にしない」）。接尾辞には、その指標の水準系列の接頭辞
    （＝§7.1.1 が宣言する「到達する量」の系列名）を付ける。
    """
    section = _section("### 3.2 ", "### 3.3 ")
    rows = _table_rows(section)
    index = next(
        position for position, cells in enumerate(rows) if _bare(cells[0]) == indicator_id
    )
    cell = rows[index][2]
    if "同上" in cell and not _BACKTICKED.search(cell):
        cell = rows[index - 1][2]   # 「同上」＝直前行と同じひな型（本書の省略記法）
    prefix = value_series_of(indicator_id)
    names: "set[str]" = set()
    for token in _BACKTICKED.findall(cell):
        stem = f"{prefix}{token}" if token.startswith("_") else token
        names.update(_expand_braces(stem, params))
    return frozenset(names)


def _expand_braces(template: str, params: "Mapping[str, object]") -> "frozenset[str]":
    """`{med|ext}` / `{hi|lo}` / `{pct}` を展開する（`{pct}` は下側・上側の 2 通り）。"""
    found = _BRACED.search(template)
    if found is None:
        return frozenset({template})
    body = found.group(1)
    # 変数名を choices にしない: 宣言整合性検定の記号索引はリポジトリ全体で 1 つなので、
    # ここで束縛した名前は他モジュールの散文中の同名語を「実在する記号」に変えてしまう
    # （実測: simulator/usecase/optimize_strategies.py の docstring が新規違反になった）。
    alternatives = (
        [str(percent(params["q_low"])), str(percent(params["q_high"]))]
        if template[found.start():found.end()] == _PCT_TOKEN
        else body.split("|")
    )
    expanded: "set[str]" = set()
    for choice in alternatives:
        head = template[: found.start()] + choice + template[found.end():]
        expanded.update(_expand_braces(head, params))
    return frozenset(expanded)
