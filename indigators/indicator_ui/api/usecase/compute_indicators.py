"""compute_indicators（ISSUE-092 ①）— POST /compute の業務手順（Application Business Rules）。

controller/compute_controller.handle_compute が担っていた業務手順を純関数へ移設したもの。
HTTP の殻にも marketdata / adapter の具象にも依存せず、Output Boundary（DatasetPort）と
呼出時注入された協調子（forming_bar / full_compute / latest_compute / compute_error /
compute_adapter）にのみ依存する（依存方向は外側 → 内側）。

業務手順（内部設計書 §4.5 / §6.3 / §7.3 / §7.4 と同値）:
  1. indicatorId（別名は controller が解決済み）/ variant の入口検証。
  2. datasetRef をホワイトリスト解決（DatasetPort.is_known）。
  3. timeframe をホワイトリスト解決（None は原子＝検査対象外）。
  4. DatasetPort.load_dataframe で DataFrame 化。
  5. mode="latest" のとき forming_bar を注入（resolve_now_unix → apply_forming_bar）。
  6. limit>0 のとき直近 N 本へ tail。
  7. full / latest ディスパッチで指標計算。ComputeError / KeyError を error 表現へ翻訳。

error_type → HTTP ステータスの対応（ERROR_STATUS）は presentation の責務であり、本層は
error_type と message のみを ComputeResult に載せる（HTTP 依存を内側へ持ち込まない）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from usecase.dataset_port import DatasetPort, dataset_port as _default_dataset_port


@dataclass
class ComputeRequest:
    """/compute の Input Model（プレーンデータ。フレームワーク型を含めない）。"""

    indicator_id: Optional[str]
    variant: Optional[str]
    params: dict
    dataset_ref: Any = None
    timeframe: Any = None
    mode: str = "full"
    forming_now: Any = None
    limit: Any = None
    generation: Any = 0

    @classmethod
    def from_body(cls, body: dict) -> "ComputeRequest":
        """リクエストボディを Input Model へ変換する（compute_id 別名・既定値を吸収）。"""
        return cls(
            indicator_id=body.get("indicatorId") or body.get("compute_id"),
            variant=body.get("variant"),
            params=body.get("params") or {},
            dataset_ref=body.get("datasetRef"),
            timeframe=body.get("timeframe"),
            mode=body.get("mode", "full"),
            forming_now=body.get("formingNow"),
            limit=body.get("limit"),
            generation=body.get("generation", 0),
        )


@dataclass
class ComputeResult:
    """/compute の Output Model（成功は series、失敗は error_type/message）。"""

    generation: Any
    series: Optional[list] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error_type is None

    @classmethod
    def success(cls, generation: Any, series: list) -> "ComputeResult":
        return cls(generation=generation, series=series)

    @classmethod
    def error(cls, generation: Any, error_type: str, message: str) -> "ComputeResult":
        return cls(generation=generation, error_type=error_type, error_message=message)


def compute_indicators(
    request: ComputeRequest,
    *,
    dataset_port: "Optional[DatasetPort]" = None,
    compute_adapter: Any,
    forming_bar: Any,
    full_compute: Callable[..., list],
    latest_compute: Callable[..., list],
    compute_error: type,
) -> ComputeResult:
    """POST /compute の業務手順（純関数）。

    Args:
        request: Input Model（ComputeRequest）。
        dataset_port: DatasetPort。None のとき既定 gateway を遅延合成する（未注入時の既定）。
        compute_adapter: IndicatorComputeAdapter 互換（full/latest_compute の第 1 引数）。
        forming_bar: resolve_now_unix / apply_forming_bar を持つ協調子（呼出時注入）。
        full_compute: 全件計算ディスパッチ（adapter, id, variant, df, params）。
        latest_compute: Latest（末尾 K）計算ディスパッチ（同上）。
        compute_error: 指標計算が送出する ComputeError 型（error_type/message を持つ）。

    Returns:
        ComputeResult（成功は series、失敗は error_type/message）。
    """
    port = dataset_port if dataset_port is not None else _default_dataset_port()
    generation = request.generation

    # 1. 入口検証。
    if not request.indicator_id or not request.variant:
        return ComputeResult.error(
            generation, "validation", "indicatorId と variant は必須です。"
        )

    # 2. datasetRef ホワイトリスト（§7.3）。
    if not port.is_known(request.dataset_ref):
        return ComputeResult.error(
            generation, "validation", f"未知の datasetRef です: {request.dataset_ref!r}"
        )

    # 3. timeframe ホワイトリスト（None は原子＝再集計なし・後方互換）。
    if request.timeframe is not None and not port.is_known_timeframe(request.timeframe):
        return ComputeResult.error(
            generation, "validation", f"未知の timeframe です: {request.timeframe!r}"
        )

    # 4. データロード。
    df = port.load_dataframe(request.dataset_ref, request.timeframe)

    mode = request.mode

    # 5. ライブ足内更新（mode="latest"）: 形成中バーを注入してから計算する。full は不変。
    if mode == "latest":
        now_unix = forming_bar.resolve_now_unix(request.forming_now)
        df = forming_bar.apply_forming_bar(df, request.dataset_ref, request.timeframe, now_unix)

    # 6. 表示範囲制限（直近 N 本）。
    limit = request.limit
    if isinstance(limit, int) and limit > 0:
        df = df.tail(limit)

    # 7. 指標計算＋エラー翻訳。
    compute_params = dict(request.params)
    try:
        series = (
            latest_compute(compute_adapter, request.indicator_id, request.variant, df, compute_params)
            if mode == "latest"
            else full_compute(compute_adapter, request.indicator_id, request.variant, df, compute_params)
        )
    except compute_error as exc:  # ComputeError（error_type/message）を error 表現へ。
        return ComputeResult.error(generation, exc.error_type, exc.message)
    except KeyError as exc:  # 未登録 id/variant は CallBinding.resolve が raw KeyError を投げる。
        return ComputeResult.error(
            generation, "validation", f"未登録の指標または variant です: {exc}"
        )

    return ComputeResult.success(generation, series)
