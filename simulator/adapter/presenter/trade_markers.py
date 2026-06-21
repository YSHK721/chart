"""TradeMarkersPresenter（TradeMarkerPresenterPort 実装）。

設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §2.2〜§2.4、
  CHART_TRADE_MARKERS_BASIC_DESIGN.md §12（H-1 配色/position、H-2 exit_reason、H-3 text）。

BacktestResult.trades（TradeRecord 列）を Marker DTO 列へ純変換し JSON を書き出す。
trades は読み取り専用で消費する（domain へ書き戻さない）。時刻は candles 生成
（dataset.py の _to_unix_seconds）と同一式 int(pd.Timestamp(t).timestamp()) で UNIX 秒化する。

adapter 層は usecase + domain + 技術ドライバ（pandas/stdlib json）のみに依存する。
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from simulator.usecase.marker_ports import TradeMarkerPresenterPort

# H-1 確定配色（presenter 内定数）。
_C_BUY = "#26a69a"
_C_SELL = "#ef5350"


def _unix(value: Any) -> int:
    """candles 生成（dataset.py の _to_unix_seconds）と同一式で UNIX 秒へ変換する。"""
    return int(pd.Timestamp(value).timestamp())


def _marker(time: Any, position: str, shape: str, color: str, text: str,
            *, kind: str, side: str, pair: int, marker_id: str) -> dict:
    """lwc 純フィールド（v4: id 追加）とメタ（kind/side/pair）を別階層に分離した Marker DTO（M-2・§10.3）。"""
    return {
        "lwc": {
            "time": _unix(time),
            "position": position,
            "shape": shape,
            "color": color,
            "text": text,
            "id": marker_id,  # v4: createSeriesMarkers が受理する hover 用 id（"t{i}:entry"/"t{i}:exit"）
        },
        "meta": {"kind": kind, "side": side, "pair": pair},  # v4: pair=トレード通番
    }


def _entry_marker(tr: Any, digits: int, i: int) -> dict:
    """建てマーカー（H-1: buy→belowBar/arrowUp/buy色、sell→aboveBar/arrowDown/sell色）。"""
    if tr.side == "buy":
        position, shape, color = "belowBar", "arrowUp", _C_BUY
    else:
        position, shape, color = "aboveBar", "arrowDown", _C_SELL
    text = f"{tr.side.upper()} {tr.entry_price:.{digits}f}"
    return _marker(tr.entry_time, position, shape, color, text,
                   kind="entry", side=tr.side, pair=i, marker_id=f"t{i}:entry")


def _exit_marker(tr: Any, digits: int, i: int) -> dict:
    """決済マーカー（H-1: 反対側/circle、色は勝敗、H-3: REASON price (pnl)）。"""
    # position は side から一意（buy 玉→aboveBar / sell 玉→belowBar）。
    position = "aboveBar" if tr.side == "buy" else "belowBar"
    pnl = tr.pnl()
    color = _C_BUY if pnl > 0 else _C_SELL  # 勝=buy色 / 非勝（<=0）=sell色
    # H-3: pnl の桁は profit_round_digits（None 時は 0 桁表示）。
    pd_digits = getattr(tr, "profit_round_digits", None)
    pnl_fmt = 0 if pd_digits is None else pd_digits
    reason = str(tr.exit_reason).upper()  # 未知 reason はそのまま大文字化（H-2 フォールバック）
    text = f"{reason} {tr.exit_price:.{digits}f} ({pnl:+.{pnl_fmt}f})"
    return _marker(tr.exit_time, position, "circle", color, text,
                   kind="exit", side=tr.side, pair=i, marker_id=f"t{i}:exit")


def _pair_record(tr: Any, i: int) -> dict:
    """売買ペア（建て→決済の線分結合用）DTO（§10.3）。win は pnl>0。時刻は既存 UNIX 秒式。"""
    return {
        "i": i,
        "side": tr.side,
        "win": tr.pnl() > 0,
        "entry": {"time": _unix(tr.entry_time), "price": tr.entry_price},
        "exit": {"time": _unix(tr.exit_time), "price": tr.exit_price},
    }


class TradeMarkersPresenter(TradeMarkerPresenterPort):
    """確定トレード列を Marker DTO 列（lwc/meta 分離）へ変換し JSON を書き出す。"""

    def present_markers(
        self, result: Any, path: Any, *, symbol: Any, ea_name: Any, timeframe: Any = None
    ) -> None:
        digits = symbol.digits
        markers: list[dict] = []
        pairs: list[dict] = []  # v4: 売買ペア（線分結合用・§10.3）
        for i, tr in enumerate(result.trades):
            markers.append(_entry_marker(tr, digits, i))
            markers.append(_exit_marker(tr, digits, i))
            pairs.append(_pair_record(tr, i))
        markers.sort(key=lambda m: m["lwc"]["time"])  # time 昇順（lwc setMarkers 要求）
        payload = {
            "ok": True,
            "symbol": symbol.name,
            "ea_name": ea_name,
            "timeframe": timeframe,  # 該当時間足＝建玉の時間足（フロントはこれ以外で売買マーク非表示）
            "count": len(markers),  # 全件数（無音切り捨て禁止＝H-4）
            "markers": markers,
            "pairs": pairs,  # v4: トレード通番順（線分結合・hover 用）
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
