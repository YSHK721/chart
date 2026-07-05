"""loader — :mod:`marketdata.ohlc_csv_loader` への再エクスポート shim（実体は marketdata へ移設）。

``load_ohlc_csv`` の実体は最下層共有パッケージ ``marketdata/ohlc_csv_loader.py`` へ **byte 一致**で
移設した（dataset→marketdata 移設に伴い、marketdata→indigators の逆依存を断つため）。profit_band
自身の利用・テスト（``from src import load_ohlc_csv``）を byte 不変で維持するため、ここでは同名を
そのまま再エクスポートする。marketdata は最下層＝indigators を一切 import しない（逆依存ゼロ）。
"""

from __future__ import annotations

from marketdata.ohlc_csv_loader import load_ohlc_csv

__all__ = ["load_ohlc_csv"]
