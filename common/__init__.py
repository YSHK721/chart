"""lib — MQL 移植で横断的に再利用する共有プリミティブ層。

特定の指標に属さず、複数の指標から再利用される純粋ロジック（numpy のみ）を置く。

公開 API:
    AppliedPrice                       : 適用価格の種別（MQL ENUM_APPLIED_PRICE 互換 ＋ OHLC4 拡張）。
    applied_price                      : 種別で 8 種を切り替えるディスパッチャ。
    close_price / open_price / high_price / low_price : 単純な列選択。
    SOURCE_TO_APPLIED                  : UI ソース値（catalog の source enum 8 択）→ 種別の写像。
    median_price / typical_price / weighted_price / ohlc4_price : 算術合成。

表示系（level_colors / LEVEL_LINE_WIDTH 等）は common_view へ分離した（ISSUE-092 ⑥）。本モジュール
（計算・本質・安定層＝numpy のみ依存）から common_view（表示・偶有・可変層）への再エクスポートは
安定度逆転（安定→不安定の依存）を生むため撤去した（ISSUE-104 🟡-1）。表示定数は common_view から
直接 import すること（`from common_view import level_colors, LEVEL_LINE_WIDTH`）。

stdlib のみで書かれた中立核（どのアクターにも属さない汎用抽象）も本パッケージが所有する。
パッケージ表面には出さず、サブモジュールを直接 import して使う:
    forming_window : 未確定足（forming）の差し替え規則。
    watch_loop     : 汎用ポーリングループ run_watch（ISSUE-479 F-3 で運用スクリプト層から移設）。

上記の公開 API は **遅延解決**する（PEP 562・ISSUE-479 F-2）。実装 applied_price.py は numpy を
必要とするが、本パッケージには上記のとおり numpy を使わない中立核も同居しており、
そちらだけを使う純層（リプレイ UI の domain / usecase）まで `import common` 経由で numpy を
背負っていた。初回アクセス時にだけ実装を解決することで推移的流入を断つ（表面・同一性は不変）。

典型的な使い方:
    >>> import numpy as np
    >>> from lib import applied_price, AppliedPrice
    >>> high = np.array([10.0, 20.0]); low = np.array([2.0, 4.0])
    >>> close = np.array([8.0, 16.0]); open_ = np.array([5.0, 12.0])
    >>> applied_price(AppliedPrice.TYPICAL, open_, high, low, close)
    array([ 6.66666667, 13.33333333])
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

__all__ = [
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

#: 遅延解決する公開名（実体は :mod:`common.applied_price`）。
_LAZY = frozenset(__all__)


def __getattr__(name: str):
    """PEP 562: 公開名の初回アクセス時にだけ実装モジュールを解決する。"""
    if name not in _LAZY:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    impl = importlib.import_module(f"{__name__}.applied_price")
    value = getattr(impl, name)
    globals()[name] = value          # 2 回目以降は通常の属性探索で解決する（再解決を発行しない）
    return value


def __dir__():
    return sorted(set(globals()) | _LAZY)


class _CommonPackage(ModuleType):
    """公開名とサブモジュール名の衝突ガード。

    公開名 applied_price はサブモジュール common.applied_price と同名である。CPython の
    import 機構はサブモジュール読込後に親パッケージの属性へ**無条件に** setattr する
    （標準ライブラリ importlib の _bootstrap が行う）ため、``from common.applied_price import ...`` が
    先行すると common.applied_price はモジュールに差し替わり、以後
    ``from common import applied_price`` が関数ではなくモジュールを掴む（呼び出し側で TypeError）。
    ``__getattr__`` は「属性が無いとき」しか呼ばれないので PEP 562 だけでは防げない。

    そこで属性取得の経路で「公開名なのにモジュールが入っている」状態を検出し、実体（同名の関数・
    クラス・定数）へ差し替えて確定させる。差し替えは 1 度だけで、以後は通常の属性探索で解決する。
    """

    def __getattribute__(self, name):
        value = ModuleType.__getattribute__(self, name)
        if name in _LAZY and isinstance(value, ModuleType):
            value = ModuleType.__getattribute__(value, name)
            ModuleType.__setattr__(self, name, value)
        return value


sys.modules[__name__].__class__ = _CommonPackage
