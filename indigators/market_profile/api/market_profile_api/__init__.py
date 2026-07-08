"""market_profile_api — MP backend（計算＋HTTPハンドラ）の固有名トップパッケージ。

indicator_ui の adapter パッケージとの sys.path 衝突を避けるため固有名を用いる。
共有インフラ（adapter.compute の dataset/forming_bar/ERROR_STATUS）は indicator_ui
api/ を sys.path 経由で参照する（server/bridge/conftest が結線）。
"""
