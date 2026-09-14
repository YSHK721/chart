"""adapter/gateway（ISSUE-092 ①）— Output Boundary の具象 gateway 群。

usecase が所有する境界ポート（DatasetPort 等）の marketdata 結線実装を置く。usecase は
本パッケージを module-level import せず、遅延既定（dataset_port() 内）または呼出時注入で
のみ受け取る（依存方向は外側 → 内側）。
"""
