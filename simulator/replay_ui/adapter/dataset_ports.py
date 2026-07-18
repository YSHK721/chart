"""dataset 具象（``marketdata.dataset``）の役割別 狭いポート（ISSUE-136 ISP）。

replay の adapter 群は ``_indicator_ui_bridge`` 経由で取得した dataset 具象（module）の **一部の面**
だけを使う。実測（ISSUE-136）では:

- ``is_known`` / ``is_known_timeframe`` … ref・timeframe のホワイトリスト検証
  （causal_candle / causal_compute / composition_root）
- ``load_dataframe`` / ``load_atom_window`` … OHLC 供給（causal_candle / causal_compute / intrabar）

各クライアントが dataset 具象の全面（``load_candles`` 等を含む約 5 面）へ広く依存するのを避け、
用途別に :class:`RefValidationPort`（検証）と :class:`OhlcSupplyPort`（供給）へ狭める。dataset 具象は
両ポートを構造的に満たす（``runtime_checkable`` で実測可能）。過剰分割は避け、実在クライアントが使う
2 役割のみを切る（YAGNI）。挙動は不変（クライアントは同じ dataset 具象を狭い型で受けるだけ）。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RefValidationPort(Protocol):
    """datasetRef / timeframe のホワイトリスト検証（§7.3 パストラバーサル対策）の抽象（read-only）。"""

    def is_known(self, ref: Any) -> bool:
        """``ref`` が既知の datasetRef キーかを返す。"""
        ...

    def is_known_timeframe(self, timeframe: Any) -> bool:
        """``timeframe`` が既知の時間足コードかを返す。"""
        ...


@runtime_checkable
class OhlcSupplyPort(Protocol):
    """解決済み datasetRef の OHLC 供給（DataFrame / 原子窓）の抽象（read-only）。"""

    def load_dataframe(self, ref: str, timeframe: "str | None") -> Any:
        """解決済み datasetRef を OHLC DataFrame 互換で返す（timeframe で resample）。"""
        ...

    def load_atom_window(self, ref: str, start: int, end: int) -> Any:
        """``[start, end)`` の 1 分足原子窓を DataFrame 互換で返す。"""
        ...
