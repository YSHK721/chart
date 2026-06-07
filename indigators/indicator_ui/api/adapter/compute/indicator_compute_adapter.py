"""IndicatorComputeAdapter（内部設計書 §3.3.1）— 既存 add_* 隔離点（唯一）。

FakeChart を生成 → CallBinding で実 add_* を呼出 → FakeChart 収集結果を系列 JSON
（§6.3.2 / §6.3.3）へ変換する。例外は §6.3.4 / §7.4 の error.type へ翻訳する。

既存 src は read-only import（改変禁止）。描画ライブラリは import しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adapter.compute.call_binding import CallBinding
from adapter.compute.fake_chart import FakeHorizontalChart, FakeLineChart

# 必須 OHLC 列（全 3 指標共通。ComputeEntry.required_columns と一致・§3.1.3）。
_REQUIRED_COLUMNS = ("open", "high", "low", "close")

# time が必須な compute_id（line 系。price_range_power は価格軸分布で time 非必須・§3.1.3）。
_TIME_REQUIRED = {"tgp_btlm", "profit_band"}

# ValueError を empty_series へ翻訳しうる compute_id（src が「必須バケット空」を投げるのは
# profit_band のみ。bands.build_bands(require_full=True)→ValueError・profit_band/src/bands.py:75）。
_EMPTY_SERIES_INDICATORS = {"profit_band"}

# error.type → HTTP ステータス対応（§6.3.4 / §7.4）の **単一定義**。
# ComputeError.error_type と同じ層（adapter/compute）に置き、controller・server 殻が
# 本表を import して共有する（status 表の二重定義を排する）。
ERROR_STATUS: dict[str, int] = {
    "validation": 400,
    "missing_column": 400,
    "missing_time": 400,
    "empty_series": 422,
    "backend_unavailable": 500,
    "internal": 500,
}


# profit_band の「必須バケット空」ValueError を識別する sentinel 文字列。
#
# 構造的判定にできない理由（既存 src は read-only・改変禁止）:
#   profit_band は「必須バケット空」(empty_series) と「normalize 不正」(validation・
#   robust_bands.py:140) の双方を *同じ* 素の ``ValueError`` 型で投げる。型・属性で両者を
#   区別できないため、profit_band に限り message を最小限照合する（他指標は照合不要）。
# なお「空入力」由来の ValueError（bands.py:65 / core.py:75 の "空です"）は adapter 冒頭の
#   ``len(df) == 0`` pre-check で確定済みのため invoke 後には到達せず、ここでは扱わない。
_BUCKET_EMPTY_MARKER = "バケット"


@dataclass
class ComputeError(Exception):
    """§6.3.4 計算 API エラー。``error_type`` は翻訳済みの type。"""

    error_type: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - 表示補助
        return f"{self.error_type}: {self.message}"


def _has_columns(df: Any) -> bool:
    cols = {str(c).lower() for c in df.columns}
    return all(c in cols for c in _REQUIRED_COLUMNS)


def _translate_value_error(compute_id: str, exc: ValueError) -> ComputeError:
    """invoke 中の ValueError を error.type へ翻訳する（§6.3.4 / §7.4）。

    判定は compute_id 起点の構造的分岐を主とし、message 照合は profit_band の
    「必須バケット空」(empty_series) と「validation」を分ける残余のみに限定する
    （理由は _BUCKET_EMPTY_MARKER の注記。空入力は pre-check で確定済み）。
    """
    if compute_id in _EMPTY_SERIES_INDICATORS and _BUCKET_EMPTY_MARKER in str(exc):
        return ComputeError("empty_series", str(exc))
    return ComputeError("validation", str(exc))


class IndicatorComputeAdapter:
    """IndicatorCallBinding ポートの実装（§3.3.1・§7.1.4）。"""

    def compute(
        self, compute_id: str, variant: str, df: Any, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """既存 add_* を改変せず呼び、収集系列を系列 JSON で返す。例外翻訳＝§7.4。"""
        binding = CallBinding.resolve(compute_id, variant)

        # 決定論的な事前判別（KeyError/ValueError は型のみでは区別不能なため・§7.4）。
        if len(df) == 0:
            raise ComputeError("empty_series", "入力 OHLC が空です。")
        if not _has_columns(df):
            raise ComputeError("missing_column", "必須 OHLC 列が不足しています。")

        fake = FakeLineChart() if binding.output_kind == "line" else FakeHorizontalChart()
        try:
            binding.invoke(fake, df, params)
        except ImportError as exc:
            raise ComputeError("backend_unavailable", str(exc)) from exc
        except RuntimeError as exc:  # TgpBtlmFitter の tgp ロード失敗
            raise ComputeError("backend_unavailable", str(exc)) from exc
        except KeyError as exc:
            # OHLC は事前確認済みのため、ここでの KeyError は時刻解決失敗（missing_time）。
            if compute_id in _TIME_REQUIRED:
                raise ComputeError("missing_time", str(exc)) from exc
            raise ComputeError("missing_column", str(exc)) from exc
        except ValueError as exc:
            raise _translate_value_error(compute_id, exc) from exc

        return fake.to_payloads()
