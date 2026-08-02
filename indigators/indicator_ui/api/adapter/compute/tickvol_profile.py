"""tickvol_profile — :mod:`marketdata.tickvol_profile` への shim（実体は marketdata 側）。

``adapter.compute.dataset`` と同規律で ``sys.modules[__name__]`` を marketdata 本体へ差し替える。
``from adapter.compute import tickvol_profile`` が marketdata.tickvol_profile と**同一モジュール
オブジェクト**へ解決するため、定数（BIN_SEC 等）や monkeypatch 対象が単一真実源で一致する。
"""

from __future__ import annotations

import sys as _sys

from marketdata import tickvol_profile as _real

_sys.modules[__name__] = _real
