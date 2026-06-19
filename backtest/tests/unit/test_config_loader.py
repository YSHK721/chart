"""framework/config_loader.py の load_config テスト（CLEAN_ARCH §9 / 依頼仕様）。

config_loader は framework 層の境界アダプタ。config.yaml（dict / ファイルパス /
YAML 文字列）を pydantic v2 で検証し、usecase の BacktestConfig（プレーン dataclass）
へ変換して返す。検証失敗は pydantic ValidationError を捕捉し ConfigError へ翻訳する
（DESIGN §9.2/§9.3）。pydantic 型を usecase 層へ漏らさない（CLEAN_ARCH §7 注記）。

決定論 9 項目の既定値は PROCESS §7（sltp_tie=SL 優先 / fill_delay=次 tick 等）。
"""
from __future__ import annotations

import dataclasses

import pytest


def _full_config_dict() -> dict:
    """9 項目を明示した妥当な config dict（同値分割の代表値）。"""
    return {
        "tick_model": "every_tick",
        "spread_model": "fixed",
        "sltp_tie": "sl",
        "fill_delay": "next_tick",
        "ohlc_order": "auto",
        "session_calendar": "broker",
        "digits": 5,
        "legacy_quirks": False,
        "return_basis": "equity_simple_bar",
    }


# ---- TC-001 正常系: 完全な dict から 9 項目が正しく構築される ----

def test_load_config_from_full_dict_builds_nine_determinism_fields():
    # Arrange
    from backtest.framework.config_loader import load_config
    from backtest.usecase.models import BacktestConfig

    source = _full_config_dict()

    # Act
    cfg = load_config(source)

    # Assert: PROCESS §7 の 9 項目が 1:1 で BacktestConfig へマップされる
    assert isinstance(cfg, BacktestConfig)
    assert cfg.tick_model == "every_tick"
    assert cfg.spread_model == "fixed"
    assert cfg.sltp_tie == "sl"            # §7 #3 同足両ヒットは SL 優先
    assert cfg.fill_delay == "next_tick"   # §7 #4 発注足と同一 tick 監視不可
    assert cfg.ohlc_order == "auto"
    assert cfg.session_calendar == "broker"
    assert cfg.digits == 5
    assert cfg.legacy_quirks is False
    assert cfg.return_basis == "equity_simple_bar"


# ---- TC-003 既定値: 省略時に PROCESS §7 既定が入る ----

def test_load_config_applies_process_section7_defaults_when_omitted():
    # Arrange: 9 項目をすべて省略した空 config
    from backtest.framework.config_loader import load_config

    # Act
    cfg = load_config({})

    # Assert: PROCESS §7 既定推奨が入る
    assert cfg.tick_model == "every_tick"        # §7 #1 全ティック
    assert cfg.spread_model == "fixed"           # §7 #2 Ask=Bid+spread 固定
    assert cfg.sltp_tie == "sl"                  # §7 #3 SL 優先（保守）
    assert cfg.fill_delay == "next_tick"         # §7 #4 次ティック以降
    assert cfg.ohlc_order == "auto"              # §7 #5 始値の近い側で切替
    assert cfg.session_calendar == "broker"      # §7 #6 ブローカー実カレンダー
    assert cfg.digits == 5                        # §7 #7 テスト銘柄で固定
    assert cfg.legacy_quirks is False            # §7 #8 原典踏襲（補正なし既定）
    assert cfg.return_basis == "equity_simple_bar"  # §7 #9 エクイティ・単純・足


# ---- TC-005 異常系: 列挙外の値（tick_model 不正）→ ConfigError ----

def test_load_config_raises_config_error_on_invalid_enum_value():
    # Arrange: tick_model に列挙外の値
    from backtest.domain.exceptions import ConfigError
    from backtest.framework.config_loader import load_config

    source = _full_config_dict()
    source["tick_model"] = "bogus_model"

    # Act / Assert: pydantic ValidationError ではなく ConfigError へ翻訳される
    with pytest.raises(ConfigError):
        load_config(source)


def test_load_config_does_not_leak_pydantic_validation_error_on_invalid_enum():
    # Arrange
    import pydantic
    from backtest.framework.config_loader import load_config

    source = _full_config_dict()
    source["tick_model"] = "bogus_model"

    # Act / Assert: pydantic.ValidationError が上位へ漏れない（DESIGN §9.2/§9.3）
    with pytest.raises(Exception) as exc_info:
        load_config(source)
    assert not isinstance(exc_info.value, pydantic.ValidationError)


# ---- TC-006 異常系（ガード）: 型不一致（digits に非数値文字列）→ ConfigError ----
# 注: TC-005 で導入した汎用 ValidationError→ConfigError 翻訳が型不一致も捕捉する。
# 本テストは「型不一致パスでも pydantic 例外が漏れない」ことの回帰ガードであり、
# 新規実装を要しない（即 pass・Red 観測対象外）。

def test_load_config_raises_config_error_on_type_mismatch():
    # Arrange: digits に非数値文字列（pydantic int 強制で coerce 不能）
    from backtest.domain.exceptions import ConfigError
    from backtest.framework.config_loader import load_config

    source = _full_config_dict()
    source["digits"] = "not_an_int"

    # Act / Assert
    with pytest.raises(ConfigError):
        load_config(source)


# ---- TC-007 異常系: 範囲外（digits 負値）→ ConfigError ----

def test_load_config_raises_config_error_on_out_of_range_digits():
    # Arrange: digits は桁数のため負値は範囲外
    from backtest.domain.exceptions import ConfigError
    from backtest.framework.config_loader import load_config

    source = _full_config_dict()
    source["digits"] = -1

    # Act / Assert
    with pytest.raises(ConfigError):
        load_config(source)


# ---- TC-009 異常系（🟡-1 再現性）: 未知キーは silent drop でなく ConfigError ----
# 決定論キーのタイポ（例 sltp_tei）が既定値に化け MT5 再現性を静かに破壊するのを禁止。
# _ConfigModel は決定論 9 項目専用のため extra="forbid"。

def test_load_config_raises_config_error_on_unknown_key():
    # Arrange: 決定論キー sltp_tie のタイポ（sltp_tei）— 既定 sl に化けてはならない
    from backtest.domain.exceptions import ConfigError
    from backtest.framework.config_loader import load_config

    source = _full_config_dict()
    source["sltp_tei"] = "tp"  # 未知キー（タイポ）

    # Act / Assert: silent drop されず ConfigError へ翻訳される
    with pytest.raises(ConfigError):
        load_config(source)


# ---- TC-010 異常系（🟡-3）: digits 上限超過（9）→ ConfigError ----
# FX 桁数の現実範囲 [0,8]（SymbolSpec.digits と整合）。上限なしで 9999 等を受理する
# 退行を禁止する境界値テスト（上限直上 digits=9）。

def test_load_config_raises_config_error_on_digits_above_upper_bound():
    # Arrange: 上限 8 の直上 9（FX 桁数の現実範囲外）
    from backtest.domain.exceptions import ConfigError
    from backtest.framework.config_loader import load_config

    source = _full_config_dict()
    source["digits"] = 9

    # Act / Assert
    with pytest.raises(ConfigError):
        load_config(source)


def test_load_config_accepts_digits_at_upper_bound():
    # Arrange: 上限 8（境界値・受理側）
    from backtest.framework.config_loader import load_config

    source = _full_config_dict()
    source["digits"] = 8

    # Act
    cfg = load_config(source)

    # Assert: 境界 8 は受理される
    assert cfg.digits == 8


# ---- TC-004 正常系: YAML ファイルパス / YAML 文字列を受理（dict と同結果） ----

_YAML_TEXT = """\
tick_model: every_tick
spread_model: fixed
sltp_tie: sl
fill_delay: next_tick
ohlc_order: auto
session_calendar: broker
digits: 5
legacy_quirks: false
return_basis: equity_simple_bar
"""


def test_load_config_from_yaml_file_path(tmp_path):
    # Arrange: 一時 YAML ファイル
    from backtest.framework.config_loader import load_config
    from backtest.usecase.models import BacktestConfig

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(_YAML_TEXT, encoding="utf-8")

    # Act
    cfg = load_config(yaml_path)

    # Assert: dict と同じ BacktestConfig が構築される
    assert isinstance(cfg, BacktestConfig)
    assert cfg.tick_model == "every_tick"
    assert cfg.sltp_tie == "sl"
    assert cfg.digits == 5
    assert cfg.legacy_quirks is False


def test_load_config_from_yaml_string():
    # Arrange: YAML 文字列
    from backtest.framework.config_loader import load_config
    from backtest.usecase.models import BacktestConfig

    # Act
    cfg = load_config(_YAML_TEXT)

    # Assert
    assert isinstance(cfg, BacktestConfig)
    assert cfg.return_basis == "equity_simple_bar"
    assert cfg.fill_delay == "next_tick"


# ---- TC-008 時刻正規化: ISO8601 文字列と epoch int を受理し一貫型へ ----
# 申し送り対応: domain と同じ numpy.datetime64 | int へ正規化する。
# BacktestConfig に時刻フィールドが無い（usecase/models.py の確定 9 項目はすべて
# 決定論ポリシー）ため、正規化は config_loader の公開ヘルパ normalize_time として提供
# し、models は変更しない（CLEAN_ARCH 厳守事項: 齟齬は config_loader 側で吸収）。

def test_normalize_time_accepts_iso8601_string():
    # Arrange
    import numpy as np
    from backtest.framework.config_loader import normalize_time

    # Act
    result = normalize_time("2024-01-02T03:04:05")

    # Assert: domain と同じ numpy.datetime64 へ正規化される
    assert isinstance(result, np.datetime64)
    assert result == np.datetime64("2024-01-02T03:04:05")


def test_normalize_time_accepts_epoch_int():
    # Arrange
    from backtest.framework.config_loader import normalize_time

    # Act
    result = normalize_time(1_704_164_645)

    # Assert: epoch int はそのまま int として一貫保持される（domain は datetime64|int）
    assert isinstance(result, int)
    assert result == 1_704_164_645


# ---- TC-011 異常系（🔵-3 特性化・回帰ガード）: normalize_time の不正入力 → ConfigError ----
# 注: 現実装（config_loader.py:55-73）は bool 除外・非 ISO 文字列・未対応型（float）を
# すでに ConfigError へ翻訳する。本 3 件は実装変更不要の特性化テストであり、その挙動を
# 回帰固定する（TC-006 同様、即 pass・Red 観測対象外）。

def test_normalize_time_rejects_bool_as_config_error():
    # Arrange: bool は int サブクラスのため誤って epoch int 扱いされてはならない
    from backtest.domain.exceptions import ConfigError
    from backtest.framework.config_loader import normalize_time

    # Act / Assert
    with pytest.raises(ConfigError):
        normalize_time(True)


def test_normalize_time_rejects_non_iso_string_as_config_error():
    # Arrange: ISO8601 として解釈できない文字列
    from backtest.domain.exceptions import ConfigError
    from backtest.framework.config_loader import normalize_time

    # Act / Assert
    with pytest.raises(ConfigError):
        normalize_time("not-a-date")


def test_normalize_time_rejects_float_as_config_error():
    # Arrange: 未対応型 float（str / int / bool いずれにも該当しない）
    from backtest.domain.exceptions import ConfigError
    from backtest.framework.config_loader import normalize_time

    # Act / Assert
    with pytest.raises(ConfigError):
        normalize_time(1.5)


# ---- cycle3: entry_price_basis を config から設定可能にする（MT5 current_open 約定の結線） ----


def test_load_config_defaults_entry_price_basis_to_close():
    # Arrange: 省略時は従来挙動（close 約定・spread 無視）= 後方互換
    from backtest.framework.config_loader import load_config

    # Act
    cfg = load_config({})

    # Assert: 既定は "close"（cycle2 の BacktestConfig 既定と一致）
    assert cfg.entry_price_basis == "close"


def test_load_config_accepts_entry_price_basis_current_open():
    # Arrange: 原典 .mq5（新規バー現値約定）= MT5 突合に必須
    from backtest.framework.config_loader import load_config

    # Act
    cfg = load_config({"entry_price_basis": "current_open"})

    # Assert: config_loader 経由で current_open が BacktestConfig へ伝播する
    assert cfg.entry_price_basis == "current_open"


def test_load_config_rejects_invalid_entry_price_basis():
    # Arrange: 列挙外の値（close / current_open のみ許容）
    from backtest.domain.exceptions import ConfigError
    from backtest.framework.config_loader import load_config

    # Act / Assert: 列挙外は ConfigError へ翻訳（silent drop 禁止）
    with pytest.raises(ConfigError):
        load_config({"entry_price_basis": "bogus_basis"})


# ---- 層1/層2: prime_first_trading_bar / floating_pnl_basis を config から設定可能にする ----


def test_load_config_defaults_prime_first_trading_bar_to_false():
    # 後方互換: 省略時は False（trading_start 境界バーも取引対象＝従来不変）。
    from backtest.framework.config_loader import load_config

    cfg = load_config({})
    assert cfg.prime_first_trading_bar is False


def test_load_config_accepts_prime_first_trading_bar_true():
    # 層1: 取引開始境界バーをプライム扱い（初回約定を次足へ落とす）= MT5 突合に必須。
    from backtest.framework.config_loader import load_config

    cfg = load_config({"prime_first_trading_bar": True})
    assert cfg.prime_first_trading_bar is True


def test_load_config_defaults_floating_pnl_basis_to_close():
    # 後方互換: 省略時は "close"（含み損益を close 固定評価＝従来不変）。
    from backtest.framework.config_loader import load_config

    cfg = load_config({})
    assert cfg.floating_pnl_basis == "close"


def test_load_config_accepts_floating_pnl_basis_bid_ask():
    # 層2: 含み損益を決済価格基準（買い=Bid/売り=Ask）で評価する = MT5 突合に必須。
    from backtest.framework.config_loader import load_config

    cfg = load_config({"floating_pnl_basis": "bid_ask"})
    assert cfg.floating_pnl_basis == "bid_ask"


def test_load_config_rejects_invalid_floating_pnl_basis():
    # 列挙外（close / bid_ask のみ許容）は ConfigError へ翻訳（silent drop 禁止）。
    from backtest.domain.exceptions import ConfigError
    from backtest.framework.config_loader import load_config

    with pytest.raises(ConfigError):
        load_config({"floating_pnl_basis": "bogus_basis"})


# ---- cycle (every-tick #1): tick_model に "real_ticks" を新設（実ティック隔離キー） ----
# every_tick(=OHLC 合成・既定) は不変。real_ticks は実ティック I/O 経路用の新列挙値。
# 既定が every_tick のまま不変であること・real_ticks 受理・列挙外は ConfigError を固定する。


def test_load_config_defaults_tick_model_to_every_tick_unchanged():
    # 後方互換: tick_model を省略すると既定は従来どおり "every_tick"（1行も挙動が変わらない）。
    from backtest.framework.config_loader import load_config

    cfg = load_config({})
    assert cfg.tick_model == "every_tick"


def test_load_config_accepts_tick_model_real_ticks():
    # every-tick #1: 実ティック隔離キー real_ticks を新設し受理する（cycle2 で経路結線）。
    from backtest.framework.config_loader import load_config

    cfg = load_config({"tick_model": "real_ticks"})
    assert cfg.tick_model == "real_ticks"


def test_load_config_still_accepts_existing_tick_model_values():
    # 既存3値（every_tick / ohlc_expand / open_only）は real_ticks 追加後も受理される。
    from backtest.framework.config_loader import load_config

    for value in ("every_tick", "ohlc_expand", "open_only"):
        cfg = load_config({"tick_model": value})
        assert cfg.tick_model == value


def test_load_config_rejects_invalid_tick_model_value():
    # 列挙外（4値以外）は silent drop されず ConfigError へ翻訳される。
    from backtest.domain.exceptions import ConfigError
    from backtest.framework.config_loader import load_config

    with pytest.raises(ConfigError):
        load_config({"tick_model": "bogus_model"})
