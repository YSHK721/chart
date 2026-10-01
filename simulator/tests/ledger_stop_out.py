"""検定が 「`build_interactor`」 へ渡すストップアウト水準（ISSUE-546）。

「`build_interactor`」 の 「`stop_out_level`」 は既定値を持たない（出所は台帳の口座 ``margin_so_so``
ただ 1 つ）。合成データで経路を確かめる検定も、値を書き写さず台帳から引いて渡す。
"""
from __future__ import annotations

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields

#: 台帳を引く銘柄（同一性の指定であって値ではない）。
_SYMBOL = "JP225"


def ledger_stop_out_level() -> float:
    """台帳の JP225 口座のストップアウト水準。"""
    return load_spec_fields(OANDA_JAPAN_MT5_LIVE, _SYMBOL)["stop_out_level"]
