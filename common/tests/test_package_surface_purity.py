"""アーキ回帰: ``common`` パッケージ表面の純度と遅延化契約（ISSUE-479 F-2 / F-7d）。

``common`` は「計算・本質・安定層」であり、**import しただけ**では偶有的技術（numpy / pandas）を
プロセスへ持ち込まない。従来 ``common/__init__.py`` は ``.applied_price`` を eager import して
おり、``common.forming_window``（stdlib のみ）を使うだけの純層（``simulator.replay_ui.domain`` /
``usecase``）にも numpy が推移的に流入していた。本モジュールはその遅延化を 3 段で固定する:

  1. **実行**（Red-1）: 新しいインタプリタで ``import common`` し ``sys.modules`` に numpy / pandas が
     現れないこと。AST 走査では推移的流入を検出できない（``test_contact_scan_usecase_purity`` 流儀）。
  2. **順序独立**（Red-2）: 公開名 ``applied_price`` はサブモジュール名と同名である。CPython の
     import 機構はサブモジュール読込後に親属性へ無条件 setattr するため、素朴な PEP 562 遅延化では
     ``from common.applied_price import ...`` が先行したときに ``from common import applied_price``
     が**モジュール**を掴む。どの import 順でも関数が得られることを固定する。
  3. **同一性**: 公開 11 名が ``common.applied_price`` の同名オブジェクトそのものであること。

加えて計算量テスト（絶対命令）を置く。遅延化は「アクセスのたびに解決し直す」実装でも状態検証は
緑のままになるため、Test Spy で「発行した解決 − 出力が必要とした解決 = 0」を表明する。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(code: str) -> str:
    """リポジトリ根を cwd に、新しいインタプリタで ``code`` を実行し stdout を返す。"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"子プロセスが失敗しました（returncode={proc.returncode}）:\n"
        f"{proc.stderr.strip()[-1500:]}"
    )
    return proc.stdout.strip()


# --------------------------------------------------------------------------------------
# 1. 実行検定（推移的流入の遮断）
# --------------------------------------------------------------------------------------


def test_importing_common_does_not_load_numpy_or_pandas() -> None:
    """``import common`` だけでは numpy / pandas がロードされない（推移的流入の遮断）。

    識別力: ``common/__init__.py`` が ``.applied_price`` を eager import へ戻すと Red になる。
    """
    leaked = _run(
        "import sys, common;"
        "print(','.join(sorted({'numpy','pandas'} & set(sys.modules))))"
    )
    assert not leaked, (
        f"import common だけで {leaked} がロードされます（推移的流入）。"
        " common は numpy を必要とする実装を遅延解決すること。"
    )


# --------------------------------------------------------------------------------------
# 2. 順序独立性（サブモジュール名との衝突ガード）
# --------------------------------------------------------------------------------------

#: ``from common import applied_price`` の**前**に走らせる import 文（実コードに現れる 4 形態）。
#: - ``import common.applied_price``            : 素の submodule import
#: - ``from common.applied_price import ...``   : 実コード（call_binding.py）の形
#: - ``from common import applied_price as ...``: 実コード（incremental/profit_rsi.py）の形が先行
#: - ``import common``                          : 親のみ先行
_IMPORT_ORDER_PROLOGUES = [
    "import common.applied_price",
    "from common.applied_price import AppliedPrice",
    "from common import applied_price as _first; import common.applied_price",
    "import common",
]


@pytest.mark.parametrize(
    "prologue", _IMPORT_ORDER_PROLOGUES, ids=lambda s: s.split(";")[0].replace(" ", "_")
)
def test_applied_price_attribute_is_the_function_for_any_import_order(prologue: str) -> None:
    """``from common import applied_price`` は import 順に関わらず**関数**を返す。

    識別力: 名前衝突ガードを外した素朴な PEP 562 遅延化では、submodule 先行の 2 形態で
    ``module False`` になり Red になる（import 機構の親属性 setattr がガードを素通りする）。
    """
    out = _run(
        f"{prologue}\n"
        "from common import applied_price\n"
        "print(type(applied_price).__name__, callable(applied_price))"
    )
    assert out == "function True", (
        f"import 順 `{prologue}` のあと common.applied_price が関数ではありません: {out!r}"
    )


# --------------------------------------------------------------------------------------
# 3. 公開表面の同一性
# --------------------------------------------------------------------------------------

#: 公開表面（``common.__all__``）の固定値。表面の増減は破壊的変更なので明示的に pin する。
_EXPECTED_ALL = [
    "AppliedPrice",
    "SOURCE_TO_APPLIED",
    "applied_price",
    "close_price",
    "open_price",
    "high_price",
    "low_price",
    "median_price",
    "typical_price",
    "weighted_price",
    "ohlc4_price",
]


def test_public_surface_is_unchanged() -> None:
    import common

    assert common.__all__ == _EXPECTED_ALL


def test_public_names_are_the_objects_of_the_applied_price_module() -> None:
    """公開 11 名は ``common.applied_price`` の同名オブジェクトそのもの（写しではない）。"""
    import importlib

    import common

    impl = importlib.import_module("common.applied_price")
    for name in common.__all__:
        assert getattr(common, name) is getattr(impl, name), (
            f"common.{name} が common.applied_price.{name} と別オブジェクトです"
        )


def test_dir_exposes_the_public_surface() -> None:
    import common

    assert set(common.__all__) <= set(dir(common))


def test_unknown_attribute_raises_attribute_error() -> None:
    import common

    with pytest.raises(AttributeError):
        common.no_such_public_name  # noqa: B018


# --------------------------------------------------------------------------------------
# 4. 計算量テスト（絶対命令）: 発行した解決 − 出力が必要とした解決 = 0
# --------------------------------------------------------------------------------------

_SPY_CODE = """
import sys
from importlib.abc import MetaPathFinder


class _LoadCounter(MetaPathFinder):
    # Test Spy: 発行された「モジュール探索」を名前ごとに数える（計数のみ・探索は後続へ委譲）。
    def __init__(self):
        self.counts = {}

    def find_spec(self, fullname, path=None, target=None):
        self.counts[fullname] = self.counts.get(fullname, 0) + 1
        return None


spy = _LoadCounter()
sys.meta_path.insert(0, spy)

import common

# Test Spy: 遅延解決（PEP 562 __getattr__）の発行回数を数える。
resolved = []
_orig = common.__getattr__


def _counting(name):
    resolved.append(name)
    return _orig(name)


common.__getattr__ = _counting

values = []
for _ in range(%d):
    for name in common.__all__:
        values.append(getattr(common, name))

owners = {getattr(v, "__module__", None) for v in values}
owners.discard(None)                      # 値オブジェクト（定数 dict）は所有モジュールを持たない
loads_issued = sum(spy.counts.get(m, 0) for m in owners)
loads_used = len(owners)                  # 出力が必要とした実装モジュール数（出力から導出）
print(len(resolved), len(set(resolved)), loads_issued, loads_used, len(values))
"""


class _SpyResult:
    __slots__ = ("resolutions_issued", "resolutions_used", "loads_issued", "loads_used", "produced")

    def __init__(self, raw: str) -> None:
        parts = [int(x) for x in raw.split()]
        (
            self.resolutions_issued,
            self.resolutions_used,
            self.loads_issued,
            self.loads_used,
            self.produced,
        ) = parts


def _spy(accesses: int) -> _SpyResult:
    return _SpyResult(_run(_SPY_CODE % accesses))


def test_lazy_surface_issues_no_resolution_beyond_what_the_output_uses() -> None:
    """発行した遅延解決・モジュールロードが、出力が使った分をちょうど 1 つも超えない。

    「作ってから捨てる」型の浪費（アクセスのたびに再解決・再ロードする実装）は出力が正しいままなので
    状態検証では原理的に落ちない。Test Spy で発行数を数え、出力から導出した使用数との差 0 を表明する。
    回数リテラルは焼き込まない（浪費の仕様化を避けるため）。
    """
    r = _spy(3)
    assert r.resolutions_issued - r.resolutions_used == 0, (
        "同じ公開名を繰り返し解決しています（キャッシュされていない）:"
        f" 発行={r.resolutions_issued} 使用={r.resolutions_used}"
    )
    assert r.loads_issued - r.loads_used == 0, (
        "実装モジュールを繰り返しロードしています:"
        f" 発行={r.loads_issued} 使用={r.loads_used}"
    )


def test_lazy_surface_resolution_count_does_not_grow_with_access_count() -> None:
    """オーダー表明: アクセス回数を 2 倍にしても、発行される解決・ロードは増えない。

    出力量（生成した値の個数）は 2 倍になるが、発行は「異なる公開名の数」だけで決まる。
    """
    few, many = _spy(3), _spy(6)

    assert many.produced == few.produced * 2, "測定の前提（アクセス 2 倍で出力 2 倍）が崩れています"
    assert many.resolutions_issued == few.resolutions_issued, (
        "遅延解決の発行がアクセス回数に比例しています（キャッシュ欠落）:"
        f" {few.resolutions_issued} → {many.resolutions_issued}"
    )
    assert many.loads_issued == few.loads_issued, (
        "実装モジュールのロード発行がアクセス回数に比例しています:"
        f" {few.loads_issued} → {many.loads_issued}"
    )
