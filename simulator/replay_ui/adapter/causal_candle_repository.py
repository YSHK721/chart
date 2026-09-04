"""CausalCandleRepository — /candles の CausalCandlePort 実装（dataset 完全委譲・ISSUE-131）。

配信路の単一権威化（依頼者承認 2026-07-18）: リプレイ側の自前足生成（tick M1 CSV の実行時
リサンプル＋独自外れ値補正）を全廃し、全 ref・全時間足を **ライブと同一の配信路**
``dataset.load_dataframe``（事前生成ロールアップ／1m 末尾 tail・clamp 外れ値補正・mtime
キャッシュ）へ委譲する。これにより足の集合・値・補正・鮮度管理が全時間足で構造的に
ライブと同一になる（実測一致でなく設計一致）。

リプレイ固有の追加はただ 1 点: tick 源の各足に optional ``tickvol``（足内実 tick 数＝M1
volume の集約値・ISSUE-044 real_ticks ETA 用）を additive に付与する。dataset の DataFrame が
持つ volume 列から写すだけで、集計・補正には一切関与しない（candles JSON の追加キーのみ）。

技術隔離（CLEAN_ARCH §6）: pandas / indicator_ui bridge は本ファイル内に閉じる。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from indigators.indicator_ui import api_loader
from simulator.replay_ui.adapter.dataset_ports import OhlcSupplyPort, RefValidationPort

class CausalCandleRepository:
    """CausalCandlePort / WindowedCandlePort / AvailableDaysPort 実装。

    untilTime 切断はしない（proto /candles と同一）。3 Port とも同一の ``_frame``（＝ライブと
    同一の配信路）と ``_bars`` を通すため、窓の取り方（tail / start 起点）だけが違う。
    """

    def __init__(
        self,
        api_path: Any = None,
        repo_root: Any = None,
        bridge_loader: "Callable[..., Any] | None" = None,
    ) -> None:
        self._api_path = api_path
        self._repo_root = repo_root
        # 既定は dataset のみのアクセサ（ISSUE-136 ISP: MP controller を eager import しない）。
        # テストは fake loader を注入して indicator_ui 実体に依存しない（MarketProfileGateway と同型）。
        self._loader = (
            bridge_loader if bridge_loader is not None else api_loader.load_dataset
        )

    # ---- 供給路（全 Port 共通の単一入口） ----

    def _frame(self, ref: str, timeframe: "str | None"):
        """ライブと同一の単一配信路（rollup 正典・clamp 補正込み）の DataFrame を返す。

        volume 列を tickvol へ写すため candles JSON でなく DataFrame を受ける。
        """
        bridge = self._loader(self._api_path, self._repo_root)
        # ISSUE-136 ISP: dataset 具象を役割別の狭いポート型で受ける（検証／供給の 2 面のみに依存）。
        refs: RefValidationPort = bridge.dataset
        ohlc: OhlcSupplyPort = bridge.dataset
        if not refs.is_known(ref):
            raise ValueError(f"unknown {ref}")
        return ohlc.load_dataframe(ref, timeframe)

    @staticmethod
    def _index_secs(df):
        """DataFrame の index（UTC 時刻）を UNIX 秒の ndarray で返す。"""
        return df.index.values.astype("datetime64[s]").astype("int64")

    # ---- CausalCandlePort ----

    def load_candles(
        self, ref: str, timeframe: "str | None", limit: "int | None"
    ) -> "list[dict]":
        # tail(limit) は dataset.load_candles と同一規則（末尾 N 本）。
        df = self._frame(ref, timeframe)
        if isinstance(limit, int) and limit > 0:
            df = df.tail(limit)
        return self._bars(df)

    # ---- WindowedCandlePort（カレンダー選択＝再生開始日を起点にした窓） ----

    def load_candles_from(
        self,
        ref: str,
        timeframe: "str | None",
        start: int,
        pre: int,
        limit: "int | None",
    ) -> "list[dict]":
        """``time >= start`` の最初の足の ``pre`` 本手前から ``limit`` 本を返す。

        末尾側は素材の終端で自然に打ち切られる（開始日が新しいほど短い窓になる）。足の形・値・
        補正は ``load_candles`` と完全に同一（同じ ``_frame``／``_bars`` を通す）。
        """
        df = self._frame(ref, timeframe)
        secs = self._index_secs(df)
        begin = max(0, int(secs.searchsorted(int(start), side="left")) - max(0, int(pre or 0)))
        if isinstance(limit, int) and limit > 0:
            df = df.iloc[begin: begin + limit]
        else:
            df = df.iloc[begin:]
        return self._bars(df)

    # ---- AvailableDaysPort（カレンダーのグレーアウト判定） ----

    def load_days(self, ref: str, timeframe: "str | None") -> "list[str]":
        """足が 1 本以上存在する UTC 日を ``"YYYY-MM-DD"`` 昇順で返す。"""
        df = self._frame(ref, timeframe)
        secs = self._index_secs(df)
        days = sorted({int(s) // 86400 for s in secs.tolist()})
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return [(epoch + timedelta(days=d)).strftime("%Y-%m-%d") for d in days]

    # ---- 足列の組み立て（全 Port 共通・出力 byte 不変） ----

    def _bars(self, df) -> "list[dict]":
        lower = {str(c).lower(): c for c in df.columns}
        secs = self._index_secs(df)
        col_o, col_h, col_l, col_c = (lower["open"], lower["high"], lower["low"], lower["close"])
        col_v = lower.get("volume")
        # ISSUE-158 ①: 列単位ベクトル化（旧: df.iterrows 行ループ）。出力は旧実装と完全同一
        #   （tickvol の isfinite 規則込み。等価性は tests/unit/test_plain_bars_vectorized.py が固定）。
        times = secs.tolist()
        o = df[col_o].to_numpy(dtype="float64").tolist()
        h = df[col_h].to_numpy(dtype="float64").tolist()
        lo = df[col_l].to_numpy(dtype="float64").tolist()
        c = df[col_c].to_numpy(dtype="float64").tolist()
        v = df[col_v].to_numpy(dtype="float64").tolist() if col_v is not None else None
        out: "list[dict]" = []
        for i in range(len(times)):
            d = {"time": times[i], "open": o[i], "high": h[i], "low": lo[i], "close": c[i]}
            if v is not None:
                vv = v[i]
                if math.isfinite(vv):  # NaN/Inf 行は載せない（int(NaN)→ValueError の 500 を防ぐ・JS はフォールバック）
                    d["tickvol"] = int(vv)
            out.append(d)
        return out
