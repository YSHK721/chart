"""module_loader — 指標 src（top-level 名 ``src`` 同名衝突）を一意名で読み込む共通機構。

指標パッケージはいずれも top-level パッケージ名 ``src`` を使うため ``import src`` では 1 つしか
読めない。本モジュールは「ファイルパスから一意なモジュール名で exec する」共通手順を 1 か所へ
集約する（各指標 core が個別に持っていた importlib 機構の重複を解消する・振る舞い不変）。

- ``load_package(name, pkg_dir)``  : ``__init__.py`` を持つパッケージを一意名で読み込む
  （相対 import ``from .trail import`` が解決するよう submodule_search_locations を設定）。
- ``load_module(name, file_path)`` : 単一の .py ファイルをモジュールとして読み込む。

いずれも ``sys.modules`` を唯一のキャッシュとし、同名の 2 回目以降は再 exec しない。
既存指標 src は read-only（改変しない）。描画ライブラリは import しない。

並列直列化（ISSUE-156 / ISSUE-176 / ISSUE-185）:
    ``sys.modules[name] = module`` は exec **前** に行う必要がある（src 内の相対 import が
    自モジュールを参照するため）。この間に他スレッドがキャッシュを引くと **半構築モジュール**
    （公開関数が未定義のモジュール）を掴む。実測（ISSUE-176・16 スレッド同時 / 既定
    switchinterval）で 15/16 スレッドがこれを観測した。

    ロックだけでは不足する（実測）: 「ロック前に ``sys.modules`` を素引きする二重チェック」
    では、exec 前に登録済みのエントリをロックを取らない高速経路が読んでしまう。旧
    ``indicator_ui/api/adapter/compute/module_loader.py``（ISSUE-185 で本モジュールへ委譲）は
    この形で、実測は 3 スレッドで 2/3・16 スレッドで 15/16 が半構築を観測した（重複 exec のみ
    が防がれる）。よって本実装は **exec 完了までキャッシュ命中と見なさない** ``_LOADING``
    ゲートを併用する（`_LOADING` は登録より先に立てるため、``sys.modules`` に見えるモジュール
    は必ず「exec 中」か「exec 済み」に判別できる）。

再入（同一スレッドからの入れ子ロード）:
    ロックは :class:`threading.RLock`。ロード中モジュールの本体が本ローダを再帰呼び出しても
    デッドロックしない。同名の自己再帰時は CPython の循環 import と同じく途中状態のモジュール
    を返す（ロック導入前の挙動と同一）。

出自: ``indicator_ui/api/adapter/compute/module_loader.py`` と同一関心の共有実装。indigators の
core 層（最内層）が indicator_ui の adapter 層（外層）へ依存すると Dependency Rule が逆流する
ため、共有プリミティブ層 ``common`` 側へ同型の実装を置く（``common/marod_bands.py`` が兄弟具象
への依存を common 抽出で対称化した手順と同型）。ISSUE-185 で重複を解消し、
``indicator_ui/api/adapter/compute/module_loader.py`` は本モジュールへの委譲（再エクスポート）
となった＝動的ロードの実装は本モジュールが唯一である。

依存: 標準ライブラリのみ（numpy・指標パッケージへ依存しない）。
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType

# ISSUE-156（A）/ ISSUE-176: 動的ロードの直列化ロック（計算プール並列時の初回競合防止）。
# RLock: ロード中モジュール本体からの入れ子ロードでデッドロックさせない。
_LOAD_LOCK = threading.RLock()

# exec 実行中の name → 実行スレッド ident。`sys.modules` への登録より **先** に立てる。
_LOADING: dict[str, int] = {}


def _cached_ready(name: str) -> "ModuleType | None":
    """exec 完了済みのキャッシュのみを返す（exec 中の半構築モジュールは None 扱い）。"""
    module = sys.modules.get(name)
    if module is None:
        return None
    if name in _LOADING:  # exec 途中（登録より先に立つ）＝キャッシュ命中と見なさない。
        return None
    return module


def _exec_into_sys_modules(name: str, module: ModuleType, loader) -> ModuleType:
    """`_LOADING` を立ててから `sys.modules` へ登録し exec する（順序が判別可能性の根拠）。

    exec が例外を送出した場合は `sys.modules` から**半構築モジュールを取り除いてから**
    再送出する（ISSUE-219）。取り除かないと `_cached_ready` が「exec 完了済み」と誤判定し、
    2 回目以降の呼び出しが**例外を出さずに壊れたモジュールを配布する**（実測: 1 回目
    RuntimeError → 2 回目は例外なしで途中まで定義された値を返す）。CPython 標準の import
    機構も失敗時に `sys.modules` から削除しており、本実装をそれに合わせる。
    """
    _LOADING[name] = threading.get_ident()
    # exec 前に登録する（src 内の相対 import が自モジュールを参照できるように）。
    sys.modules[name] = module
    try:
        loader.exec_module(module)
    except BaseException:
        # 自分が登録した実体のみ取り除く（循環 import で他が差し替えた場合は触らない）。
        if sys.modules.get(name) is module:
            del sys.modules[name]
        raise
    finally:
        _LOADING.pop(name, None)
    return module


def _load_guarded(name: str, build) -> ModuleType:
    """キャッシュ判定 → ロック → 再判定 → ``build()`` の共通ガード。"""
    cached = _cached_ready(name)
    if cached is not None:
        return cached

    # 計算プール並列時の初回同時ロード競合を防ぐ（exec 途中の半構築モジュールを他スレッドが
    #   観測しない）。ロック内で再チェックし、二重 exec も防止する。
    with _LOAD_LOCK:
        cached = _cached_ready(name)
        if cached is not None:
            return cached
        if _LOADING.get(name) == threading.get_ident():
            # 自スレッドの exec 中に同名を再入（循環参照）。CPython の循環 import と同じく
            #   途中状態のモジュールを返す（ロック導入前の挙動と同一）。
            return sys.modules[name]
        return build()


def load_package(name: str, pkg_dir: Path) -> ModuleType:
    """``pkg_dir/__init__.py`` を一意名 ``name`` で読み込む（相対 import を解決可能にする）。

    キャッシュ: ``sys.modules[name]`` に exec 完了済みのモジュールがあれば再 exec せず返す。
    """
    return _load_guarded(name, lambda: _load_package_locked(name, pkg_dir))


def _load_package_locked(name: str, pkg_dir: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        name,
        pkg_dir / "__init__.py",
        submodule_search_locations=[str(pkg_dir)],
    )
    if spec is None or spec.loader is None:  # pragma: no cover - 環境異常（spec 解決不能）
        raise ImportError(f"パッケージを読み込めません: {name} ({pkg_dir})")
    return _exec_into_sys_modules(name, importlib.util.module_from_spec(spec), spec.loader)


def load_module(name: str, file_path: Path) -> ModuleType:
    """単一の .py ファイルを一意名 ``name`` のモジュールとして読み込む。

    キャッシュ: ``sys.modules[name]`` に exec 完了済みのモジュールがあれば再 exec せず返す。
    """
    return _load_guarded(name, lambda: _load_module_locked(name, file_path))


def _load_module_locked(name: str, file_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, file_path)
    if spec is None or spec.loader is None:  # pragma: no cover - 環境異常（spec 解決不能）
        raise ImportError(f"モジュールを読み込めません: {name} ({file_path})")
    return _exec_into_sys_modules(name, importlib.util.module_from_spec(spec), spec.loader)
