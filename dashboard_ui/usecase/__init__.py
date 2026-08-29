"""usecase 層。domain と自層のみに依存する（pandas / HTTP / 指標パッケージを知らない）。

外側（adapter）とは Output Boundary（`sheet_ports`）越しにしか話さない（DIP）。
"""
