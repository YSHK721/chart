"""CATALOG_SCHEMA（ISSUE-092 ③ / ISSUE-180）— 指標 param 既定値の配信面。

背景（ISSUE-091 A5）: 指標追加・param 変更時に既定値が 4 面（back ``call_binding._TABLE`` /
指標 src の ``add_*`` シグネチャ / front ``usecase/catalog.js`` 静的レジストリ / latest_meta）へ
分散し、front/back の乖離が構造的に検出できなかった。

param **既定値**（名前 → 既定値）の正は Python 側に単一定義する。この定義が「正」であり、front は
``GET /catalog`` 経由でこれを runtime overlay して既定値を解決する。フェッチ失敗時のみ front 静的値
（``catalog.js`` リテラル）へフォールバックする（オフライン耐性・後方互換）。front 静的値と本定義の
一致は back/front 双方のテスト（``catalog_defaults.json`` 契約）で固定する。

ISSUE-180（OCP）: その単一定義の**置き場所**を本モジュールから指標記述子
``call_binding._TABLE`` の ``params_defaults`` へ移した。指標 1 件の追加で改変するファイルを
減らすためであり、本モジュールは記述子からの**導出**（``indicator_param_defaults``）と配信のみを
担う。ここに既定値リテラルを再び置くことは二重定義であり禁止する（``PARAM_DEFAULTS`` は導出値）。

対象は ``call_binding._TABLE`` に登録された全 compute_id（tgp_btlm / btlm_trail /
btlm_trail_marod / ma_marod / profit_band / price_range_power / moving_averages ＋ profit_* 15
＝ 22 件）。``market_profile`` は独立アクター
（market_profile モジュール）が所有するため本 schema の対象外（front 側で静的定義を維持）。

表示ラベル・制約・UI メタ等の純 UI 情報は front（``catalog.js``）に残す（既定値のみ単一情報源化）。
"""

from __future__ import annotations

import copy
from typing import Any

from adapter.compute.call_binding import indicator_param_defaults

# compute_id → {param_name: default}。param **既定値**の正（single source）＝指標記述子
# ``call_binding._TABLE`` の ``params_defaults`` 宣言からの導出値（deep copy 済み）。
# 値は front ``usecase/catalog.js`` の現行既定値と完全一致させる（UI 実効値を不変に保つ）。
# 一致は ``catalog_defaults.json`` 契約経由で back（test_catalog_schema）/ front
# （catalog_schema_sync.test.js）双方のテストが固定し、乖離を検出する。
# dict の挿入順は _TABLE のエントリ順＝``GET /catalog`` 応答の key 順（従来配信順を維持）。
PARAM_DEFAULTS: dict[str, dict[str, Any]] = indicator_param_defaults()


def catalog_defaults() -> dict[str, dict[str, Any]]:
    """serving 用に ``PARAM_DEFAULTS`` の deep copy を返す（source を呼び出し側の変更から守る）。

    ``GET /catalog`` の controller（``handle_catalog``）がこれを JSON 応答へ載せる。
    """
    return copy.deepcopy(PARAM_DEFAULTS)
