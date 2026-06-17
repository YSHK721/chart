"""薄い CLI ラッパ（DESIGN §5.2・§9.4）。

    python -m backtest.main run --config config.yaml --data m1.csv --output result/

yaml は determinism（config_loader へ渡す決定論 9 項目の上書き）と meta（戦略/シンボル
パラメータ）を分けて持つ。CLI 本体は引数解析と run_backtest への委譲・終了コードの
sys.exit のみを担い、DI 構築は build_interactor（__init__.py）に委譲する（CLI を薄く保つ）。

終了コード: 成功 0 / ConfigError 2 / BacktestError 1（run_backtest が controller の翻訳で返す）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import yaml

from backtest.main import run_backtest


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="backtest.main")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="1 run のバックテストを実行する")
    run.add_argument("--config", required=True, help="config.yaml（determinism + meta）")
    run.add_argument("--data", required=True, help="価格データ CSV パス")
    run.add_argument("--output", default=None, help="出力先ディレクトリ（任意）")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI エントリポイント。終了コード（0/1/2）を返す。

    config ファイルの読込・YAML パース失敗は設定起因（ConfigError 相当）として
    exit 2 へ翻訳する（DESIGN §5.2「config.yaml をロード」・§9.4）。トレースバックを
    呼出側へ漏らさない。
    """
    args = _parse_args(argv)
    try:
        text = Path(args.config).read_text(encoding="utf-8")
        cfg = yaml.safe_load(text) or {}
    except (OSError, yaml.YAMLError):
        # config 読込不能・YAML 不正は設定エラー → exit 2
        return 2
    determinism = cfg.get("determinism", {})
    meta = dict(cfg.get("meta", {}))
    meta["data_path"] = args.data
    meta["config_overrides"] = determinism
    exit_code, _ = run_backtest(output_dir=args.output, **meta)
    return exit_code


if __name__ == "__main__":  # pragma: no cover（薄いラッパの実行入口）
    sys.exit(main())
