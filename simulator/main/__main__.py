"""薄い CLI ラッパ（DESIGN §5.2・§9.4）。

    python -m simulator.main run --config config.yaml --data m1.csv --output result/

yaml は determinism（config_loader へ渡す決定論 9 項目の上書き）と meta（戦略/シンボル
パラメータ）を分けて持つ。CLI 本体は引数解析と run_backtest への委譲・終了コードの
sys.exit のみを担い、DI 構築は build_interactor（__init__.py）に委譲する（CLI を薄く保つ）。

終了コードの規約（成功値・例外 → コードの対応・評価順）は `simulator.adapter.exit_codes`
が唯一宣言する（A-6）。本モジュールは表を持たず、config 読込の外側例外を内側 `ConfigError`
へ翻訳したうえで同じ `exit_code_for` に載せる。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

import yaml

from simulator.adapter.exit_codes import exit_code_for
from simulator.domain.exceptions import BacktestError, ConfigError
from simulator.main import run_backtest


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="simulator.main")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="1 run のバックテストを実行する")
    run.add_argument("--config", required=True, help="config.yaml（determinism + meta）")
    run.add_argument("--data", required=True, help="価格データ CSV パス")
    run.add_argument("--output", default=None, help="出力先ディレクトリ（任意）")
    return parser.parse_args(argv)


def _load_config_file(path: Path) -> Any:
    """config.yaml を読み込む。外側 I/O 例外は内側 `ConfigError` へ翻訳する。

    事前条件: `path` は config.yaml のパス。
    事後条件: YAML の読み取り結果を返す（空ファイル・`null` は `{}`）。
    例外: 読込不能（`OSError`）・YAML 不正（`yaml.YAMLError`）は設定起因のため
         `ConfigError` へ翻訳して送出する。トレースバックを呼出側へ漏らさない。

    なぜ翻訳するのか（A-6）:
        終了コードの規約は `simulator.adapter.exit_codes` が唯一宣言し、その表は
        `BacktestError` 系統だけを引数に取る（範囲外の例外は握り潰さず再送出する）。
        `OSError` / `yaml.YAMLError` は framework/OS 由来の**外側**例外であり、
        そのまま `exit_code_for` へ渡すと再送出になる。`_present_outputs`（`__init__.py`）
        が出力 I/O の `OSError` を `DataError` へ翻訳するのと同じ流儀で、設定起因の
        外側例外を `ConfigError` へ翻訳してから翻訳表に載せる
        （DESIGN §5.2「config.yaml をロード」・§9.4・CLEAN_ARCH §6 外側例外の内側翻訳）。
    """
    try:
        text = path.read_text(encoding="utf-8")
        return yaml.safe_load(text) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(
            f"config の読込に失敗しました: {path}",
            context={"config_path": str(path), "cause": repr(exc)},
        ) from exc


def main(argv: Sequence[str] | None = None) -> int:
    """CLI エントリポイント。終了コードを返す。

    終了コードの値は本モジュールでは宣言せず、`exit_code_for`（唯一の宣言場所は
    `simulator.adapter.exit_codes`）へ委譲する。config 読込段の外側例外は
    `_load_config_file` が `ConfigError` へ翻訳済みであり、同じ翻訳に載る。
    """
    args = _parse_args(argv)
    try:
        cfg = _load_config_file(Path(args.config))
    except BacktestError as error:
        return exit_code_for(error)
    determinism = cfg.get("determinism", {})
    meta = dict(cfg.get("meta", {}))
    meta["data_path"] = args.data
    meta["config_overrides"] = determinism
    try:
        meta[_STOP_OUT_LEVEL] = _ledger_stop_out_level(meta)
    except BacktestError as error:
        return exit_code_for(error)
    exit_code, _ = run_backtest(output_dir=args.output, **meta)
    return exit_code


#: 「`build_interactor`」 の引数名（ストップアウト水準）。
_STOP_OUT_LEVEL = "stop_out_level"


def _ledger_stop_out_level(meta: "dict[str, Any]") -> float:
    """ストップアウト水準を台帳から引く（ISSUE-546）。

    水準の出所は台帳の口座 ``margin_so_so``（`spec_fields` の `「`stop_out_level`」`）ただ 1 つ。
    config.yaml の ``meta`` からは受け取らない——人が書いた値が台帳に優先する入口になる。
    台帳に無い銘柄は既定値で走らせず `ConfigError` で止める。
    """
    from marketdata.symbol_spec_snapshot import (
        OANDA_JAPAN_MT5_LIVE,
        SnapshotError,
        load_spec_fields,
    )

    if _STOP_OUT_LEVEL in meta:
        raise ConfigError(
            "config.yaml の meta に stop_out_level は書けません"
            "（ストップアウト水準は台帳の口座 margin_so_so から引きます・ISSUE-546）",
            context={"stop_out_level": meta[_STOP_OUT_LEVEL]},
        )
    symbol = meta.get("symbol")
    try:
        return load_spec_fields(OANDA_JAPAN_MT5_LIVE, str(symbol))[_STOP_OUT_LEVEL]
    except SnapshotError as exc:
        raise ConfigError(
            f"銘柄 {symbol!r} のストップアウト水準を台帳から引けません: {exc}",
            context={"symbol": symbol},
        ) from exc


if __name__ == "__main__":  # pragma: no cover（薄いラッパの実行入口）
    sys.exit(main())
