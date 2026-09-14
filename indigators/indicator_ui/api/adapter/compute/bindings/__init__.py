"""指標ごとの呼出規約フック（CALL_BINDING の協働子）。

``call_binding._TABLE`` の各エントリが宣言する preprocess / latest_meta / value_error_types /
共有 params_defaults / 引数渡し規約（Invoker）などの **指標固有の知識** をここへ置く。
call_binding 本体は表を引くだけで指標名を知らない（SRP: 「1 指標の都合が変わったとき改変する
ファイル」を指標ごとに 1 本へ分ける）。

**協働子の追加は「本ディレクトリに ``<compute_id>.py`` を 1 本置く」だけで完了する**
（ISSUE-502 段階 4B）。登録行は存在しない: ``bindings.<compute_id>`` は下の ``__getattr__``
（PEP 562）が遅延 import で解決する。これにより「hook を要する指標を 1 件足す」ときの編集点は
*bindings/ に 1 ファイル追加* ＋ *call_binding._TABLE に 1 行* の 2 点に固定される
（共有ファイルへ import 行や再エクスポート別名を書き足さない）。

遅延であることは性能上も必要である: 本パッケージの import が全協働子を読み込むと、指標 src の
ロードとは無関係な import を起動時に発行することになる。実際に参照された協働子だけを読む
（``api/tests/test_call_binding_complexity.py`` が「協働子の import が指標 src の exec を
1 件も発行しない」ことを Test Spy で固定する）。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

_HERE = Path(__file__).resolve().parent


def __getattr__(name: str) -> ModuleType:
    """``bindings.<compute_id>`` を遅延 import で解決する（PEP 562・登録行を作らない）。"""
    if name.startswith("_"):
        raise AttributeError(name)
    full = f"{__name__}.{name}"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as exc:
        if exc.name != full:
            raise
        raise AttributeError(
            f"指標協働子 {name} がありません（{_HERE / (name + '.py')} を置いてください）"
        ) from None


def __dir__() -> "list[str]":
    """協働子として置かれている .py の一覧（登録表ではなくディレクトリ実体が唯一源）。"""
    return sorted(p.stem for p in _HERE.glob("*.py") if not p.stem.startswith("_"))
