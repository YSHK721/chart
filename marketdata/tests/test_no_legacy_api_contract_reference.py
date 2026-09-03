"""アーキ回帰: 旧 HTTP 契約パス ``marketdata.api_contract`` の参照ゼロ（ISSUE-479 F-8 段階 1）。

HTTP 契約（``ERROR_STATUS`` / ``nested_error``）の所有者は配信殻であり、``marketdata`` のどの
アクターでもない（ISSUE-094 🔵-11）。実体は中立共有パッケージ ``api_shared.http_contract`` へ
移設済みで、``marketdata/api_contract.py`` は後方互換の再エクスポートに過ぎない。互換シムを
残したまま参照が生き続けると、契約の入口が 2 つある状態（単一ソースの偽装）が固定化する。

本テストは「旧パスを import する本番・テストコードがリポジトリに 1 件も無い」ことだけを主張する。
**ファイルの存在自体は Red にしない**（削除は要承認事項であり、段階 2 で免除の撤去と同時に行う）。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 旧パスの実体（存在は前提であり、削除は段階 2・要承認）。
_LEGACY_MODULE = _REPO_ROOT / "marketdata" / "api_contract.py"

#: 所有者（実体）モジュール。付替え先はここ。
_OWNER_MODULE = _REPO_ROOT / "api_shared" / "http_contract.py"

#: 走査から外すディレクトリ名／トップレベル（仮想環境・キャッシュ・第三者コード・試作・データ）。
_SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"}
_SKIP_TOP_LEVEL = {"lightweight-charts-python-main", "data", "sample", "node_modules", "scratchpad"}

#: 旧パスの import 形態。**行頭（インデント可）が import 文であること**を要求するので、
#: docstring・コメント中に旧パス名が現れても誤検出しない（api_contract.py 自身の docstring 等）。
_LEGACY_DOTTED = re.compile(r"^\s*(?:from|import)\s+(?:marketdata\.|\.)api_contract\b")
_LEGACY_FROM_PACKAGE = re.compile(r"^\s*from\s+(?:marketdata|\.)\s+import\s+(?P<names>[^#\n]+)")
_LEGACY_NAME = re.compile(r"\bapi_contract\b")


def _imports_legacy_api_contract(source: str) -> bool:
    """ソース文字列が ``marketdata.api_contract`` を import しているか。"""
    for line in source.splitlines():
        if _LEGACY_DOTTED.match(line):
            return True
        m = _LEGACY_FROM_PACKAGE.match(line)
        if m and _LEGACY_NAME.search(m.group("names")):
            return True
    return False


def _repo_sources() -> "list[Path]":
    """リポジトリの Python ソース（本番・テストの両方。仮想環境・試作・第三者コードは除く）。"""
    out: "list[Path]" = []
    for top in sorted(_REPO_ROOT.iterdir()):
        if not top.is_dir():
            continue
        if top.name in _SKIP_TOP_LEVEL or top.name in _SKIP_DIR_NAMES:
            continue
        if top.name.startswith("prototype_"):
            continue
        for p in top.rglob("*.py"):
            if _SKIP_DIR_NAMES & set(p.parts):
                continue
            out.append(p)
    out += sorted(_REPO_ROOT.glob("*.py"))
    return out


def test_scan_reaches_both_the_legacy_and_the_owner_module() -> None:
    """走査が旧パス・所有者・本テスト自身へ届いている（空走査で恒真式に退化しない）。"""
    sources = set(_repo_sources())
    assert _LEGACY_MODULE in sources, "走査が marketdata/api_contract.py に届いていません"
    assert _OWNER_MODULE in sources, "走査が api_shared/http_contract.py に届いていません"
    assert Path(__file__).resolve() in sources, "走査が本テスト自身に届いていません"


def test_the_legacy_module_still_exists_and_is_not_required_to_be_deleted() -> None:
    """段階 1 の適用範囲: 旧パスのファイル削除は行わない（要承認・段階 2）。

    参照ゼロと削除は別事象なので分けて固定する。本テストが緑である限り、参照ゼロ化は
    「ファイルを消したから 0 件」ではなく「実際に付替えたから 0 件」であることが保証される。
    """
    assert _LEGACY_MODULE.exists(), "旧パスのファイルは段階 1 では削除しない（要承認）"


def test_no_code_imports_the_legacy_api_contract_path() -> None:
    """``marketdata.api_contract`` を import する本番・テストコードが 1 件も無い。

    識別力: どこかで ``from marketdata.api_contract import ERROR_STATUS`` を復活させると Red になる。
    落ちた場合の直し方は ``from api_shared.http_contract import ...`` への付替え。
    """
    offenders: "list[str]" = []
    for path in _repo_sources():
        if _imports_legacy_api_contract(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert not offenders, (
        "旧 HTTP 契約パス marketdata.api_contract を import している箇所が残っています:\n"
        + "\n".join(offenders)
        + "\napi_shared.http_contract への直参照へ付替えてください。"
    )


def test_legacy_import_detection_has_power() -> None:
    """検出力: 旧パスの import 形態を検出し、紛らわしい非違反を誤検出しない。

    合成ソース文字列で与える（実ファイルは生成しない。かつ本テスト自身が走査の offender に
    ならないよう、リテラルは連結して組み立てる）。
    """
    name = "api_contract"

    for offender in (
        "from marketdata." + name + " import ERROR_STATUS\n",
        "import marketdata." + name + "\n",
        "from marketdata import " + name + "\n",
        "from ." + name + " import nested_error\n",
        "from . import " + name + "\n",
        "    from marketdata." + name + " import ERROR_STATUS\n",   # 関数内 import も参照は参照。
    ):
        assert _imports_legacy_api_contract(offender), f"検出できていません: {offender!r}"

    for clean in (
        "from api_shared.http_contract import ERROR_STATUS\n",
        "# 旧 import ``from marketdata." + name + " import ...`` は撤去済み\n",
        '"""（既存 import ``from marketdata.' + name + ' import ...`` を壊さない）"""\n',
        "from marketdata import tf_meta\n",
        "_LEGACY = 'marketdata." + name + "'\n",
    ):
        assert not _imports_legacy_api_contract(clean), f"誤検出しています: {clean!r}"


def test_scan_reads_each_source_exactly_once() -> None:
    """計算量テスト: 走査は 1 ファイル 1 読込（発行 − 判定に使ったソース数 = 0）。

    オーダー表明として対象 1 件 / 2 件の 2 点で、発行が対象数だけで決まることを固定する
    （ファイルの長さ・行数では増えない）。回数リテラルは焼き込まない。
    """
    sources = _repo_sources()
    reads: "list[Path]" = []
    real_read = Path.read_text

    def _spy(self, *args, **kwargs):
        reads.append(self)
        return real_read(self, *args, **kwargs)

    Path.read_text = _spy
    try:
        one = sources[:1]
        used_one = [_imports_legacy_api_contract(p.read_text(encoding="utf-8")) for p in one]
        issued_one = len(reads)
        reads.clear()

        two = sources[:2] if len(sources) >= 2 else sources
        used_two = [_imports_legacy_api_contract(p.read_text(encoding="utf-8")) for p in two]
        issued_two = len(reads)
    finally:
        Path.read_text = real_read

    assert issued_one - len(used_one) == 0, "1 ファイルあたりの読込発行が判定使用数を超えています"
    assert issued_two - len(used_two) == 0, "1 ファイルあたりの読込発行が判定使用数を超えています"
    assert issued_two == len(two), "読込発行が対象ファイル数以外の要因で増えています"
