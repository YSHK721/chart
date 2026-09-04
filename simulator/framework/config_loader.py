"""config.yaml → BacktestConfig ローダ（CLEAN_ARCH §7/§9 framework 層）。

pydantic v2 を「検証付き DTO」として境界に限定し、検証後に usecase のプレーン DTO
（BacktestConfig＝dataclass）へ変換して返す。pydantic 型を usecase 層へ漏らさない。

決定論 9 項目の既定値は PROCESS §7（sltp_tie=SL 優先 / fill_delay=次 tick 等）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Union

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from simulator.adapter.execution.tick_model_registry import TICK_MODEL_IDS
from simulator.domain.exceptions import ConfigError
from simulator.usecase.models import BacktestConfig

ConfigSource = Union[dict, str, Path]


class _ConfigModel(BaseModel):
    """検証付き DTO（境界限定）。PROCESS §7 決定論 9 項目の既定値を保持する。

    本モデルは framework 層の内部にのみ存在し、外部へは BacktestConfig（プレーン
    dataclass）へ変換してから返す（pydantic 型を usecase 層へ漏らさない）。

    列挙型項目（tick_model / spread_model / sltp_tie / fill_delay / ohlc_order /
    return_basis）は Literal で許容値を固定し、列挙外の値は ValidationError とする。
    session_calendar はブローカー名等の自由文字列のため制約しない（PROCESS §7 #6）。

    本モデルは決定論 9 項目専用。ea_name/symbol/period 等のメタは Section 5 で別モデル
    が受ける。未知キー（決定論キーのタイポ含む）は extra="forbid" で ValidationError と
    し、silent drop による既定値化（MT5 再現性の静かな破壊）を禁止する（🟡-1）。
    """

    model_config = ConfigDict(extra="forbid")

    # §7 #1。real_ticks は実ティック I/O 経路用の隔離キー（every-tick #1）。
    # every_tick(=OHLC 合成) は既定のまま不変。経路結線は cycle2。
    # 許容値は tick_model 単一レジストリ（TICK_MODEL_IDS）から導出する（ISSUE-097 🟡-5）。
    # 従来ハードコードの Literal 4 値（every_tick/ohlc_expand/open_only/real_ticks）と
    # 同一集合・同一順序であり検証挙動は完全不変（回帰ガード test_tick_model_registry）。
    tick_model: Literal[TICK_MODEL_IDS] = "every_tick"
    spread_model: Literal["fixed", "variable"] = "fixed"          # §7 #2 Ask=Bid+spread 固定
    sltp_tie: Literal["sl", "tp"] = "sl"                          # §7 #3 SL 優先（保守）
    fill_delay: Literal["next_tick", "same_tick"] = "next_tick"   # §7 #4 次ティック以降
    ohlc_order: Literal["auto", "ohlc", "olhc"] = "auto"          # §7 #5 始値の近い側で切替
    session_calendar: str = "broker"                              # §7 #6 ブローカー実カレンダー（自由文字列）
    digits: int = Field(default=5, ge=0, le=8)                    # §7 #7 桁数（FX 現実範囲 [0,8]・SymbolSpec.digits と整合）
    legacy_quirks: bool = False                                   # §7 #8 原典踏襲（補正なし既定）
    return_basis: Literal[
        "equity_simple_bar", "equity_log_bar", "balance_simple_trade"
    ] = "equity_simple_bar"                                       # §7 #9 エクイティ・単純・足
    # 約定価格基準（cycle2 で BacktestConfig へ追加・cycle3 で config_loader 経由設定を結線）。
    # 既定 "close"＝従来挙動（後方互換）。"current_open"＝原典 .mq5（新規バー現値約定）。
    entry_price_basis: Literal["close", "current_open"] = "close"
    # 証拠金ストップアウト時の挙動（cycle4 で追加）。既定 "fail_stop"＝従来どおり raise。
    # "close_and_halt"＝強制決済して完走。default 付きのため既存 config と後方互換。
    stop_out_action: Literal["fail_stop", "close_and_halt"] = "fail_stop"
    # 取引開始境界バーのプライム扱い（層1）。既定 False＝従来不変。True で初回約定を次足へ。
    prime_first_trading_bar: bool = False
    # 含み損益の評価基準（層2）。既定 "close"＝close 固定。"bid_ask"＝買い Bid・売り Ask。
    floating_pnl_basis: Literal["close", "bid_ask"] = "close"
    # 約定損益の口座通貨丸め桁（ISSUE-020）。既定 None＝丸めず（byte-identical）。
    # 0=JPY 整数丸め。実 MT5 は約定損益を口座通貨精度へ丸めて balance/stats に反映する。
    profit_round_digits: Optional[int] = Field(default=None, ge=0, le=8)
    # stop-out をバー open でも先行評価するか（ISSUE-022）。既定 False＝従来 close 基準のみ。
    # True で実 MT5 OHLC の open pseudo-tick 評価に整合（週末ギャップ等で open クォート約定）。
    stop_out_at_open: bool = False
    # 指値/逆指値ペンディング注文のライフサイクルを有効化するか（2603-01）。既定 False＝
    # 従来経路（成行のみ）で byte-identical。True で execute() を every-tick 経路へ振り向け、
    # ペンディングを足途中ティックでトリガ評価する（MaSlopePending 用）。
    pending_lifecycle: bool = False
    # 同時設置した複数ペンディングの OCO（2604-02）。既定 False＝従来挙動（兄弟は独立約定）。
    # True で 1 本約定時に残る兄弟ペンディングを全取消（StopEntryProbe の両建て用）。
    pending_oco: bool = False
    # ペンディング持続＋足途中ティック再アーム（2604-02・ISSUE-024）。既定 False＝従来挙動。
    # True で resting をバー境界でリセットせず保持し、フラット＆未装填のティックで on_tick を呼ぶ。
    pending_persistent: bool = False
    # hedging 口座の両建て証拠金相殺（2604-02・ISSUE-024）。既定 False＝単純加算。
    # True で stop-out 判定の証拠金を「買い計・売り計の大きい側」とする（反対玉は相殺）。
    hedged_margin: bool = False


def normalize_time(value: Union[str, int]) -> Union[np.datetime64, int]:
    """時刻入力を domain と同じ ``numpy.datetime64 | int`` へ正規化する。

    - ISO8601 文字列 → ``numpy.datetime64``
    - epoch int      → ``int``（domain が epoch int を受理するため変換しない）

    検証失敗（非 ISO 文字列・未対応型）は ConfigError へ翻訳する（pydantic 非経由）。
    """
    if isinstance(value, bool):  # bool は int のサブクラスのため明示除外
        raise ConfigError(
            f"未対応の時刻型です: {type(value).__name__}",
            context={"value_type": type(value).__name__},
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return np.datetime64(value)
        except (ValueError, TypeError) as exc:
            raise ConfigError(
                f"ISO8601 として解釈できない時刻文字列です: {value!r}",
                context={"value": value},
            ) from exc
    raise ConfigError(
        f"未対応の時刻型です: {type(value).__name__}",
        context={"value_type": type(value).__name__},
    )


def _to_mapping(source: ConfigSource) -> dict:
    """入力ソースを dict へ正規化する（dict / YAML ファイルパス / YAML 文字列）。

    - dict             : そのまま使用
    - Path / 既存ファイル: YAML ファイルとして読み込む
    - その他の str      : YAML 文字列として解釈する
    """
    if isinstance(source, dict):
        return source
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8")
    elif isinstance(source, str):
        path = Path(source)
        # 改行を含まず実在するパスならファイル、それ以外は YAML 文字列とみなす
        if "\n" not in source and path.is_file():
            text = path.read_text(encoding="utf-8")
        else:
            text = source
    else:
        raise ConfigError(
            f"未対応の config ソース型です: {type(source).__name__}",
            context={"source_type": type(source).__name__},
        )
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ConfigError(
            "config YAML はマッピング（key: value）である必要があります",
            context={"parsed_type": type(loaded).__name__},
        )
    return loaded


def load_config(source: ConfigSource) -> BacktestConfig:
    """config（dict / YAML パス / YAML 文字列）を pydantic v2 で検証し
    BacktestConfig（プレーン DTO）へ変換する。

    検証失敗（列挙外・型不一致・範囲外・必須欠落）は pydantic ValidationError を捕捉し
    ConfigError へ翻訳する（DESIGN §9.2/§9.3。pydantic 例外を上位へ漏らさない）。
    """
    mapping = _to_mapping(source)
    try:
        model = _ConfigModel(**mapping)
    except ValidationError as exc:
        raise ConfigError(
            f"設定検証に失敗しました: {exc.error_count()} 件のエラー",
            context={"validation_errors": exc.errors()},
        ) from exc
    return BacktestConfig(
        tick_model=model.tick_model,
        spread_model=model.spread_model,
        sltp_tie=model.sltp_tie,
        fill_delay=model.fill_delay,
        ohlc_order=model.ohlc_order,
        session_calendar=model.session_calendar,
        digits=model.digits,
        legacy_quirks=model.legacy_quirks,
        return_basis=model.return_basis,
        entry_price_basis=model.entry_price_basis,
        stop_out_action=model.stop_out_action,
        prime_first_trading_bar=model.prime_first_trading_bar,
        floating_pnl_basis=model.floating_pnl_basis,
        profit_round_digits=model.profit_round_digits,
        stop_out_at_open=model.stop_out_at_open,
        pending_lifecycle=model.pending_lifecycle,
        pending_oco=model.pending_oco,
        pending_persistent=model.pending_persistent,
        hedged_margin=model.hedged_margin,
    )


# ===========================================================================
# 週次ボラ・バンド戦略 config 追記（詳細設計 §11・既存無改変・追記のみ）
# 既存ブロックは 1 行も変更しない。usecase へは pydantic を漏らさずプレーン引数へ
# 変換して渡す（DI-1）。
# ===========================================================================

class VolEstimationParams(BaseModel):
    """WV1 週末バッチ推定パラメータ（詳細設計 §11）。"""

    model_config = ConfigDict(extra="forbid")

    window: int = Field(260, ge=1)
    nw_lag: int = Field(4, ge=0)
    min_bars_per_week: int = Field(..., ge=1)  # 立会内最小5分本数（D4・要供給）


class ValidationParams(BaseModel):
    """WV3 検証パラメータ（詳細設計 §11・TBD コストは既定 0.0）。"""

    model_config = ConfigDict(extra="forbid")

    seed: int = 0
    B: int = Field(5000, ge=1)
    alpha_stop: float = 0.05
    f_risk: float = 0.01
    min_weeks: int = 260
    min_stop_hits: int = 30
    c_spread: float = 0.0
    c_comm: float = 0.0
    r_fund: float = 0.0
    e_grid: list[str] = ["E0", "E1(0.5)", "E1(1.0)", "E1(1.5)", "E1(2.0)"]
    p_tp_grid: list[float] = [0.40, 0.50, 0.60, 0.70]
