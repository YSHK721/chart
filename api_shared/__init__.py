"""api_shared — 配信殻が同格に参照する中立共有パッケージ（ISSUE-094 🔵-11）。

HTTP 契約（error.type→ステータス表・nested エラー整形）など「どの配信殻のアクターにも
属さないが 3 殻（indicator_ui api・market_profile api・replay backend）が共有する純粋物」を
最下層 marketdata でも各殻でもない中立パッケージへ集約する。リポジトリ根に置き、venv の
.pth（tools/install_dev_paths.py が登録する ROOT）で全プロセスから解決可能。
"""
