"""CausalComputeGateway — /compute の CausalComputePort 実装（proto do_compute 忠実）。

indicator_ui の実アダプタ ``full_compute`` / ``latest_compute`` を read-only 再利用して計算する
（proto_server:171-177 と同一・偽装なし＝出力はプロトと bit 同一）。usecase から渡る plain バー列
（truncate/tail/forming 適用済）を DataFrame（DatetimeIndex・UTC）へ復元して計算へ渡す。

バー時刻の符号化は candle.time と同一（``index → datetime64[s] → int64``）＝フロントの untilTime と
同基準。DataFrame 復元は ``pd.to_datetime(sec, unit="s")`` で完全逆変換（UTC・秒境界で bit 一致）。

技術隔離（CLEAN_ARCH §6）: pandas / indicator_ui は本ファイル内に閉じる。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from indigators.indicator_ui import api_loader
from simulator.replay_ui.adapter.dataset_ports import OhlcSupplyPort, RefValidationPort


class CausalComputeGateway:
    """CausalComputePort 実装。load_source（dataset）+ compute（full/latest）。"""

    def __init__(self, api_path: Any = None, repo_root: Any = None) -> None:
        self._api_path = api_path
        self._repo_root = repo_root

    def _bridge(self):
        # ISSUE-136 ISP: /compute は dataset ＋ 計算 Facade のみを要する（MP controller を import しない）。
        return api_loader.load_compute(self._api_path, self._repo_root)

    # ---- CausalComputePort ----

    def load_source(self, ref: str, timeframe: "str | None") -> "list[dict]":
        bridge = self._bridge()
        # ISSUE-136 ISP: dataset 具象を役割別の狭いポート型で受ける（検証 2 面／供給 1 面のみに依存）。
        refs: RefValidationPort = bridge.dataset
        ohlc: OhlcSupplyPort = bridge.dataset
        if not refs.is_known(ref):
            raise ValueError(f"unknown datasetRef {ref!r}")
        if timeframe is not None and not refs.is_known_timeframe(timeframe):
            raise ValueError(f"unknown timeframe {timeframe!r}")
        df = ohlc.load_dataframe(ref, timeframe)
        return self._df_to_bars(df)

    def bar_time(self, timeframe: str, unix_sec: int) -> int:
        """ISSUE-290: ライブと同一のラベル規約（marketdata.tf_meta.bar_time_unix）を使う。"""
        from marketdata.tf_meta import bar_time_unix  # 遅延: 技術隔離を本ファイルに閉じる

        return int(bar_time_unix(timeframe, int(unix_sec)))

    def period_start(self, timeframe: str, unix_sec: int) -> int:
        """ISSUE-292: 期間の始端は ``marketdata.tf_meta.period_start_unix`` が唯一源。

        ラベル（``bar_time``）とは別物である。実測（1D）: t=2026-08-06 22:20 UTC の
        ラベルは 08-07 00:00 UTC・始端は 08-06 21:00 UTC。属する足の判定は始端で行う。
        """
        from marketdata.tf_meta import period_start_unix  # 遅延: 技術隔離を本ファイルに閉じる

        return int(period_start_unix(int(unix_sec), timeframe))

    def causal_series(
        self, indicator: str, variant: str, chart_bars: "list[dict]",
        source_bars: "list[dict]", compute_tf: str, window_bars: "list[dict]", params: dict,
    ) -> "list[dict]":
        """上位足の因果系列（ISSUE-295）。規約の実体は**ライブと同一**の唯一源を呼ぶ。

        ``adapter.compute.mtf_causal.causal_mtf_series``（indicator_ui）を read-only 再利用し、
        期間ラベルは ``marketdata.tf_meta.bar_time_unix``、各時点の計算は本ゲートウェイの
        ``compute_latest_seq``（確定プレフィクスの DataFrame 化を 1 回に畳む経路）を渡す。
        """
        from marketdata.tf_meta import bar_time_unix  # 遅延: 技術隔離を本ファイルに閉じる

        bridge = self._bridge()
        return bridge.causal_mtf_series(
            chart_bars=chart_bars,
            source_bars=source_bars,
            compute_tf=compute_tf,
            bar_time_unix=bar_time_unix,
            latest_seq=lambda prefix, tails: self.compute_latest_seq(
                indicator, variant, prefix, tails, params),
            window_bars=window_bars,
            # ISSUE-297: バー単位の記憶もライブ core と同一実装を共有する（記憶はプロセス内）。
            #   正しさを担保するのは鍵ではなく指紋（value(τ) を決める入力そのもの）＝本 Port の面が
            #   持たない datasetRef / チャート足を鍵に含めなくても取り違えは起こらない。
            memo=bridge.causal_mtf_memo_for(
                compute_tf=compute_tf, indicator=indicator, variant=variant, params=params),
        )

    def compute(
        self, indicator: str, variant: str, mode: str, bars: "list[dict]", params: dict
    ) -> "list[dict]":
        bridge = self._bridge()
        df = self._bars_to_df(bars)
        p = dict(params or {})
        if mode == "latest":
            return bridge.latest_compute(bridge.adapter, indicator, variant, df, p)
        return bridge.full_compute(bridge.adapter, indicator, variant, df, p)

    def compute_latest_seq(
        self, indicator: str, variant: str, prefix_bars: "list[dict]",
        tails: "list[list[dict]]", params: dict,
    ) -> "list[list[dict]]":
        """足内推移の各時点を計算する（共通の窓は 1 回だけ DataFrame へ変換する）。

        ISSUE-233: 時点ごとに窓全体（実測 1492 本）を plain dict から DataFrame へ組み直すと
        1 ステップ 2.1ms を要し、指標計算そのもの（0.36ms）を上回る。確定プレフィクスの
        変換を 1 回に畳み、時点ごとには末尾差分（1〜2 本）だけを結合する。出力は
        ``compute(..., "latest", prefix_bars + tails[i], ...)`` と同値。
        """
        bridge = self._bridge()
        p = dict(params or {})
        prefix_df = self._bars_to_df(prefix_bars)
        out: "list[list[dict]]" = []
        for tail in tails:
            tail_df = self._bars_to_df(tail)
            if len(prefix_df) == 0:
                df = tail_df
            else:
                # 列は確定プレフィクス側に合わせる（_bars_to_df の列順契約を保つ）。
                df = pd.concat([prefix_df, tail_df.reindex(columns=prefix_df.columns)])
            out.append(bridge.latest_compute(bridge.adapter, indicator, variant, df, p))
        return out

    # ---- internal (pandas ↔ plain) ----

    @staticmethod
    def _df_to_bars(df: "pd.DataFrame") -> "list[dict]":
        """DataFrame → plain bars。**全列が数値（float 変換可能）であることが前提**。

        契約（ISSUE-034 の暗黙契約を明示化）:
            - 列名は ``str(c).lower()`` へ正規化する。大文字を保持したい列があっても失われる。
            - 値は ``float64`` へ強制する。非数値列（文字列・カテゴリ等）が入ると
              ``ValueError`` / ``TypeError`` になる。
            - すなわち本ゲートウェイは **OHLCV 相当の数値列のみ**を運ぶ経路である。

        現状これが安全な理由: 源データの CSV 列は小文字（open/high/low/close/volume）で、
        compute 側は列名を case-insensitive に解決する。非数値列を持つ指標・大文字前提の
        指標を通す必要が生じた場合は、本メソッドで非対象列を保存扱いにするガードが要る。
        """
        # candle.time と同一符号化（untilTime と同基準・tz 非依存 UTC epoch）。
        # ISSUE-158 ①: 列単位ベクトル化（旧: 行ループ df.iloc＝50k 行で ~1.2s・compute 1 回の 69%）。
        #   出力は旧実装と完全同一（キー順 time→列順・time は int・値は float。等価性は
        #   tests/unit/test_plain_bars_vectorized.py が参照実装との一致で固定）。
        secs = df.index.values.astype("datetime64[s]").astype("int64")
        keys = ["time"] + [str(c).lower() for c in df.columns]
        columns = [secs.tolist()] + [
            df[c].to_numpy(dtype="float64").tolist() for c in df.columns
        ]
        return [dict(zip(keys, row)) for row in zip(*columns)]

    @staticmethod
    def _bars_to_df(bars: "list[dict]") -> "pd.DataFrame":
        # time → DatetimeIndex（UTC・秒境界で df_to_bars の完全逆変換）。他列はそのまま復元。
        times = [int(b["time"]) for b in bars]
        index = pd.to_datetime(times, unit="s")
        cols: "list[str]" = []
        for b in bars:
            for k in b:
                if k != "time" and k not in cols:
                    cols.append(k)
        data = {c: [b.get(c) for b in bars] for c in cols}
        return pd.DataFrame(data, index=index)
