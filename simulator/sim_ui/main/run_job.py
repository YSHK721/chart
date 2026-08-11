"""子プロセス CLI: 1 ジョブ分のバックテストを実行する（§12.7 並列実行の実体）。

起動形（`SubprocessJobLauncher`）:
    <venv python> simulator/sim_ui/main/run_job.py --job-dir <ジョブディレクトリの絶対パス>

**受ける引数は `--job-dir` だけ**である。実行仕様は job-dir の `spec.json` から読む。
仕様を argv に並べると、シェル経由のクォート事故・引数の取り違えという壊れ方を
新たに作ることになる（sim core と子プロセスの間で仕様の表現が二重化する）。

結果ペイロードは `run_backtest` の**既存出力に限る**（`stats.json` / `report.md`）。
report_ui 形の `report.json` は Phase 4 の範囲（§8.1）であり、ここで private ヘルパを
写して作ることはしない（§12.3-3 複製禁止）。

ジョブ状態はここでは書かない。子が状態を書く設計にすると、SIGKILL 等で何も書けずに
死んだときに「実行中のまま固まる」経路が生まれる。状態は sim core 側が
終了コードと突き合わせて確定する（`simulator/sim_ui/usecase/query_job.py`）。
本 CLI の責務は「走らせて、終了コードを返す」ことだけである。

SIGTERM（取消・§12.7）: ハンドラを**入れない**。既定の SIGTERM はカーネルが即座に
プロセスを終わらせるが、Python のシグナルハンドラはバイトコード境界でしか走らないため、
pandas/numpy の長い C 呼び出しの最中は遅れる。取消の応答性は既定の方が良い。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from simulator.main import run_backtest

# 仕様の読めないジョブ・内部例外は失敗（非 0）で返す。`run_backtest` の終了コード
# （0 成功 / 1 BacktestError / 2 ConfigError）と衝突しない値を使う。
_EXIT_SPEC_ERROR = 3
# 失敗理由の置き場（`FileJobLedger.read_failure_report` が読む）。**状態は書かない**。
# 終了コードだけでは「なぜ落ちたか」が運用者へ届かない（`BacktestController.run` は
# BacktestError を終了コードのみへ翻訳し、文言はそこで消える）。
# 起動器の stderr を PIPE にする案は採らない: 未読パイプが 64KB で埋まると子が
# ブロックして終わらなくなる。ファイルなら子は書き切って終われる。
_FAILURE_FILE = "failure.json"


def _record_failure(job_dir: Path, reason: str) -> None:
    """失敗理由を job-dir へ残す（書けなくても本処理の失敗を覆い隠さない）。"""
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / _FAILURE_FILE).write_text(
            json.dumps({"reason": reason}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except OSError:
        pass


class _VolumeConstraints:
    """ジョブ仕様（JSON dict）を SymbolSpec 相当の**属性**へ写す最小のアダプタ。

    `build_sizing_decorator` はコードベース共通の `SymbolSpec` と同じ duck typing
    （`.volume_min` / `.volume_max` / `.volume_step`）で量制約を読む。JSON 由来の dict と
    その規約の食い違いを、ここ 1 箇所で吸収する。
    """

    __slots__ = ("volume_min", "volume_max", "volume_step")

    def __init__(self, backtest: "dict[str, Any]") -> None:
        # 欠落は KeyError（既定値で黙って埋めない）。
        self.volume_min = backtest["volume_min"]
        self.volume_max = backtest["volume_max"]
        self.volume_step = backtest["volume_step"]


def _build_decorator(spec: "dict[str, Any]") -> Any:
    """sizing 設定から `StrategyPort` の Decorator を作る（E-2 の `strategy_decorator`）。

    Group B（`simulator.framework.sizing_config_loader` /
    `simulator.adapter.strategy.sizing_decorator`）へは**この関数の中でだけ**依存する。
    sizing OFF の経路がサイジング実装の import に巻き込まれないようにするため
    （OFF は既存挙動と byte 等価であるべき・§12.1）。
    """
    from simulator.adapter.strategy.sizing_decorator import build_sizing_decorator
    from simulator.framework.sizing_config_loader import load_sizing_config

    backtest = spec.get("backtest") or {}
    overrides = backtest.get("config_overrides") or {}
    config = load_sizing_config(spec.get("sizing") or {})
    return build_sizing_decorator(
        config,
        # `build_sizing_decorator` は SymbolSpec と同じ**属性アクセス**（duck typing）で
        # 量制約を読む。ジョブ仕様は JSON 由来の dict なので、ここで属性を持つ形へ
        # 変換する（dict をそのまま渡すと AttributeError になり、例外が握られて
        # exit=2 に化けるため「sizing ON のジョブが常に仕様エラー」になる）。
        # 欠落キーは KeyError にして明示エラーへ載せる（既定値で黙って埋めない＝
        # 銘柄と違う刻みのロットが静かに出るのを防ぐ）。
        symbol_spec=_VolumeConstraints(backtest),
        entry_price_basis=overrides.get("entry_price_basis", "close"),
    )


def main(argv: "list[str] | None" = None) -> int:
    """1 ジョブを実行して終了コードを返す。"""
    parser = argparse.ArgumentParser(
        prog="run_job",
        description="1 ジョブ分のバックテストを実行する（sim core の子プロセス）",
    )
    parser.add_argument(
        "--job-dir", required=True, help="ジョブディレクトリ（絶対パス）"
    )
    args = parser.parse_args(argv)

    job_dir = Path(args.job_dir).resolve()
    try:
        spec = json.loads((job_dir / "spec.json").read_text(encoding="utf-8"))
        meta = dict(spec.get("backtest") or {})
    except Exception as exc:
        message = f"ジョブ仕様を読めませんでした: {exc}"
        print(message, file=sys.stderr)
        _record_failure(job_dir, message)
        return _EXIT_SPEC_ERROR

    sizing = spec.get("sizing") or {}
    if sizing.get("enabled", False):
        try:
            meta["strategy_decorator"] = _build_decorator(spec)
        except Exception as exc:
            message = f"サイジングの構築に失敗しました: {exc}"
            print(message, file=sys.stderr)
            _record_failure(job_dir, message)
            return _EXIT_SPEC_ERROR

    try:
        exit_code, _result = run_backtest(output_dir=job_dir, **meta)
    except Exception as exc:  # 内部例外を呼び出し側へ生で漏らさない
        message = f"バックテストの実行に失敗しました: {exc}"
        print(message, file=sys.stderr)
        _record_failure(job_dir, message)
        return _EXIT_SPEC_ERROR
    return exit_code


if __name__ == "__main__":  # pragma: no cover — 子プロセスの入口
    raise SystemExit(main())
