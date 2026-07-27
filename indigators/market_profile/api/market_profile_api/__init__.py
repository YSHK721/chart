"""market_profile_api — MP backend（計算＋HTTPハンドラ）の固有名トップパッケージ。

indicator_ui の adapter パッケージとの sys.path 衝突を避けるため固有名を用いる。
共有インフラ（adapter.compute の dataset/forming_bar/ERROR_STATUS）は indicator_ui
api/ を sys.path 経由で参照する（server/bridge/conftest が結線）。

ISSUE-183（DIP）: 本モジュールがパッケージの **Composition Root**。compute が所有する
Output Boundary（``tick_store_port`` / ``store_port``）へ、gateway 側の既定 factory を
起動時に 1 回登録する。Python は submodule を import する前に必ず親パッケージの ``__init__``
を実行するため、ポートが呼ばれる時点での登録済みが構造的に保証される（エントリポイント
＝server / 各 conftest / analysis スクリプト / tools を個別に列挙する必要がなく、列挙漏れが
原理的に起こらない）。これによりポート側から gateway を pull する遅延 import（内側 → 外側の
逆流かつ ``store_port → composition → market_profile_zp → store_port`` の循環）を撤去できる。

登録するのは factory（関数オブジェクト）のみで、具象 gateway の import は既定 Store が実際に
必要になった時点まで遅延する（本パッケージ import のコストは従来どおり）。
"""

from market_profile_api.gateway.composition import install_default_stores as _install_default_stores

_install_default_stores()
