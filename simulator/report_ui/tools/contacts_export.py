"""contacts_export — 接点スキャン結果を report payload の agg.contacts へ載せる結線（tools 層）。

責務（Composition Root 側の偶有的処理）:
  - scan_contacts usecase（挙動の正解＝プロト bit 一致）を再利用して接点を算出する
    （接点算出ロジックは一切再実装しない）。
  - usecase が返す contact.v1 event（bar_time/price/direction ほか）を、フロント（chart.js）が
    そのまま使う agg.contacts 形状 [{time, price, dir}] へ純変換する。

本モジュールは numpy/pandas を import しない。ma_values（bar_index→MA 値）は上流
（export_report_payload.py・pandas 許容）が _ema_series から構築して注入する（DIP: 指標計算 IO は
外側に閉じ、本結線は plain 値のみ受ける）。ticks_fn も同様に注入する（full_scan 時のみ usecase が
呼ぶ）。preview（full_scan=False）は確定足 close クロスのみで tick を一切読まない安全経路。
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from simulator.usecase.scan_contacts import ScanContactsRequest, scan_contacts


def events_to_contacts(events: "Sequence[dict]") -> "list[dict]":
    """contact.v1 event 列を agg.contacts 形状 [{time, price, dir}] へ射影する（純変換）。

    time=bar_time（足ラベル＝マーカーの time）, dir=direction。price は接点 mid 価格。
    他キー（tick_time/bar_index/level/level_id/prev_price/mode/schema 等）は捨てる。
    """
    return [
        {"time": int(e["bar_time"]), "price": float(e["price"]), "dir": e["direction"]}
        for e in events
    ]


def compute_segment_contacts(
    *,
    bars: "Sequence[Any]",
    ma_values: "dict[int, float]",
    ref: str,
    timeframe: str,
    indicator: str = "ema",
    variant: str = "",
    params: "dict | None" = None,
    ticks_fn: "Callable[[int, int], list] | None" = None,
    full_scan: bool = False,
) -> "list[dict]":
    """1 セグメントの足＋MA から接点（agg.contacts 形状）を算出する。

    bars: .time/.high/.low/.close を持つ read-only バー列（昇順・ma_values と位置対応）。
    ma_values: bar_index→MA 値（前足 MA 参照は usecase/spec が bar_time 経由で行う）。
    ticks_fn: full_scan 時のみ usecase が呼ぶ tick 窓源。preview では未指定でよい
      （呼ばれないが、防御的に空列を返す no-op を既定注入する）。
    """
    request = ScanContactsRequest(
        ref=ref,
        timeframe=timeframe,
        indicator=indicator,
        variant=variant,
        params=params or {},
        bar_times=[int(b.time) for b in bars],
        highs=[float(b.high) for b in bars],
        lows=[float(b.low) for b in bars],
        closes=[float(b.close) for b in bars],
        full_scan=full_scan,
    )
    tf: "Callable[[int, int], list]" = ticks_fn if ticks_fn is not None else (lambda s, e: [])
    result = scan_contacts(request=request, ticks_fn=tf, ma_values=ma_values)
    return events_to_contacts(result.events)
