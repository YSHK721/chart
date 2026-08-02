"""serve_tickvol_profile — GET /tickvol_profile の業務手順（純関数）。

取引密度（ティック数）の「セッション日内・時刻帯」プロファイルと、そこから決まる HIGH 帯を返す。
1 時間足以下のチャートで背景色を変える帯の唯一源（依頼者確定 2026-08-01）。

責務境界（:mod:`usecase.serve_candles` と同規律）:
  - Controller（外側）: クエリ文字列 → Input Model の数値解釈、Output Model → (HTTP, ボディ)。
  - 本モジュール（内側）: 検証 → 1 分足フレーム取得 → 集計 → 帯算出 の手順と error_type の決定。

依存: Output Boundary（:class:`OhlcFramePort` / :class:`RefValidationPort`）と、呼出時に注入された
集計協調子（``profile``＝marketdata.tickvol_profile 相当）のみ。HTTP にも具象データ層にも依存しない。

集計は **1 分足原子**（timeframe=``"1m"``）で行う。表示時間足に依らずビン幅は固定（15 分）で、
帯は「市場の性質」であって「チャートの拡大率」の関数ではないため（実測: 表示足ごとにビンを変えると
1 分足で帯が 27 本へ断片化する）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from usecase.dataset_port import DatasetPort, dataset_port as _default_port

# 集計に使う原子時間足（表示時間足とは独立）。
ATOM_TIMEFRAME = "1m"


@dataclass
class TickvolProfileRequest:
    """/tickvol_profile の Input Model（数値解釈は controller が済ませたプレーンデータ）。"""

    dataset_ref: Any = None
    sessions: "int | None" = None
    pct: "float | None" = None
    until: "int | None" = None


@dataclass
class TickvolProfileResult:
    """/tickvol_profile の Output Model（成功は帯・ビン、失敗は error_type/message）。"""

    bin_sec: int = 0
    sessions: int = 0
    bands: list = field(default_factory=list)
    bins: list = field(default_factory=list)
    threshold: float = 0.0
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error_type is None


def serve_tickvol_profile(
    request: TickvolProfileRequest,
    *,
    dataset_port: "Optional[DatasetPort]" = None,
    profile: Any,
) -> TickvolProfileResult:
    """取引密度プロファイルの業務手順（純関数）。

    Args:
        request: Input Model。
        dataset_port: DatasetPort（ホワイトリスト検証＋1 分足 DataFrame 供給）。None で既定を解決。
        profile: ``clamp_sessions`` / ``clamp_pct`` / ``session_offset_profile`` /
            ``concentration_bands`` / ``profile_threshold`` / ``BIN_SEC`` を持つ集計協調子（注入）。

    Returns:
        TickvolProfileResult。データが無い ref は帯 0 本の正常応答（エラーにしない）。
    """
    port = dataset_port if dataset_port is not None else _default_port()

    if not port.is_known(request.dataset_ref):
        return TickvolProfileResult(
            error_type="validation",
            error_message=f"未知の datasetRef です: {request.dataset_ref!r}",
        )

    sessions = profile.clamp_sessions(request.sessions)
    pct = profile.clamp_pct(request.pct)

    try:
        df = port.load_dataframe(request.dataset_ref, ATOM_TIMEFRAME)
    except Exception as exc:  # noqa: BLE001（業務手順の最後の砦・error 表現へ翻訳）
        return TickvolProfileResult(
            error_type="internal", error_message=f"tickvol_profile の取得に失敗しました: {exc}"
        )

    p = profile.session_offset_profile(df, sessions=sessions, until=request.until)
    values = p["values"]
    bands = profile.concentration_bands(values, bin_sec=p["bin_sec"], pct=pct)
    return TickvolProfileResult(
        bin_sec=p["bin_sec"],
        sessions=p["day_count"],
        bands=bands,
        bins=[{"off": int(b) * p["bin_sec"], "value": float(v)} for b, v in sorted(values.items())],
        threshold=profile.profile_threshold(values, pct=pct),
    )
