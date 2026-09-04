"""adapter 層（内部設計書 §3.3）。

既存 add_* 隔離点（compute）・upstream 隔離点（front）を収める。本パッケージは
domain / usecase / 既存指標 src（read-only import）に依存し、描画ライブラリには
依存しない（FakeChart 注入で描画ライブラリ非依存・PORTING_GUIDE §2）。
"""
