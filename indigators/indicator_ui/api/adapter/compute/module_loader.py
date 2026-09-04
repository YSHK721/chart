"""module_loader — 指標 src（top-level 名 ``src`` 同名衝突）を一意名で読み込む共通機構。

3 指標はいずれも top-level パッケージ名 ``src`` を使うため ``import src`` では 1 つしか
読めない。「ファイルパスから一意なモジュール名で exec する」共通手順は 1 か所へ集約する。

本モジュールは **共有プリミティブ層 ``common.module_loader`` への委譲**（再エクスポート）
であり、独自の実装を持たない。``adapter.compute.module_loader`` という既存の import 面
（``call_binding._load_src_package``）を維持したまま、実装を 1 本化する。

- ``load_package(name, pkg_dir)``  : ``__init__.py`` を持つパッケージを一意名で読み込む
  （相対 import ``from .bands import`` が解決するよう submodule_search_locations を設定）。
- ``load_module(name, file_path)`` : 単一の .py ファイルをモジュールとして読み込む。

一本化の経緯（ISSUE-185）:
    旧実装は ISSUE-156 対策の ``threading.Lock`` と二重チェックを持っていたが、**高速経路が
    ロックを取らずに ``sys.modules`` を素引きする**欠陥があった。動的ロードは相対 import 解決
    のため ``sys.modules[name] = module`` を exec **前**に登録する必要があり、その未初期化
    オブジェクトを高速経路が読む（重複 exec は防げるが、公開関数が未定義のモジュールを掴み
    呼出時 ``AttributeError`` になり得る）。実測は 3 スレッドで 2/3、16 スレッドで 15/16 が
    半構築を観測。``common.module_loader`` は「exec 完了までキャッシュ命中と見なさない」
    ``_LOADING`` ゲート（+ 入れ子ロード用 ``RLock``）で本欠陥を持たないため、そちらを正として
    委譲する。半構築観測の是正以外の挙動（キャッシュ命中の返り値・例外の型とメッセージ・
    二重ロードの抑止・exec 失敗時に壊れたモジュールが ``sys.modules`` に残ること）は不変。

依存: ``common`` は venv の ``.pth``（tools/install_dev_paths.py）およびエントリポイントの
自己結線フォールバック（``framework/server.py``）でリポジトリ根が sys.path に載ることで解決する
（本モジュールは sys.path を改変しない・ISSUE-087 🟡-3 / ISSUE-174 の解決点一本化に従う）。
既存指標 src は read-only（改変しない）。描画ライブラリは import しない。
"""

from __future__ import annotations

from common.module_loader import load_module, load_package

__all__ = ["load_module", "load_package"]
