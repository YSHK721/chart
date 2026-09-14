"""moving_averages を名前付き共有ライブラリとして公開する再エクスポート層。

``indicators/`` を ``sys.path`` に追加すれば、他指標パッケージから
``from moving_averages import simple_ma`` のように `common` 同様の名前付き
import で再利用できる（実体は ``src/core.py``。本層は薄い再公開のみ）。

依存: なし（``src`` の公開 API を素通しするだけ。numpy 以外を引き込まない）。
"""

from __future__ import annotations

# 束縛と __all__ の唯一源は src.__all__（ISSUE-331）。名前を手書きで書き写すと、src へ
#   追加された公開名（stateful LWMA 等）がここで欠け、`from moving_averages import *` が
#   AttributeError になる（実際に 3 名欠けていた）。`import *` は src.__all__ の全名を
#   束縛するので、出所が構造的に 1 つになる。
from .src import *  # noqa: F401,F403
from .src import __all__  # noqa: F401 — 公開 API を src と一致させる
