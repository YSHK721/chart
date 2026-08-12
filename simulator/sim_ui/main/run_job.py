"""子プロセス CLI: 1 ジョブ分のバックテストを実行する（§12.7 並列実行の実体）。

起動形（`SubprocessJobLauncher`）:
    <venv python> simulator/sim_ui/main/run_job.py --job-dir <ジョブディレクトリの絶対パス>

**受ける引数は `--job-dir` だけ**である。実行仕様は job-dir の `spec.json` から読む。
仕様を argv に並べると、シェル経由のクォート事故・引数の取り違えという壊れ方を
新たに作ることになる（sim core と子プロセスの間で仕様の表現が二重化する）。

結果ペイロードは `run_backtest` の既存出力（`stats.json` / `report.md`）に加えて、
表示用の `report.json`（report_ui 形）を成功 run のときだけ書く（Phase 4・§8.1）。
写像そのものは report_ui の UC / Presenter が単一ソースで、ここには写さない
（§12.3-3 複製禁止）。書出しは `simulator.sim_ui.adapter.report_payload_writer` へ委譲する。
表示用ペイロードの書出しに失敗しても**終了コードは変えない**——バックテスト自体は成功して
おり、表示の失敗で成功した計算を捨てないため。

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
from simulator.sim_ui.adapter import contacts_supply, report_payload_writer

# 仕様の読めないジョブ・内部例外は失敗（非 0）で返す。`run_backtest` の終了コード
# （0 成功 / 1 BacktestError / 2 ConfigError）と衝突しない値を使う。
_EXIT_SPEC_ERROR = 3
# 失敗理由の置き場（`FileJobLedger.read_failure_report` が読む）。**状態は書かない**。
# 終了コードだけでは「なぜ落ちたか」が運用者へ届かない（`BacktestController.run` は
# BacktestError を終了コードのみへ翻訳し、文言はそこで消える）。
# 起動器の stderr を PIPE にする案は採らない: 未読パイプが 64KB で埋まると子が
# ブロックして終わらなくなる。ファイルなら子は書き切って終われる。
_FAILURE_FILE = "failure.json"
# 表示用ペイロード（report.json）の書出し失敗の置き場。**`failure.json` とは別にする**:
# run 自体は成功しており、同じファイルへ書くと「失敗した run」と区別できなくなる。
# これが無いと、書出しに失敗したジョブは「完了なのに結果が出ない」だけの状態になり、
# 理由がどこにも残らない（stderr は起動器が DEVNULL に固定している
# ＝`adapter/subprocess_job_launcher.py:75-76`）。
_REPORT_PAYLOAD_ERROR_FILE = "report_payload_error.json"


def _write_note(job_dir: Path, filename: str, payload: "dict[str, str]") -> None:
    """job-dir へ 1 件の記録を書く（書けなくても本処理の結果を覆い隠さない）。"""
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8",
        )
    except OSError:
        pass


def _record_failure(job_dir: Path, reason: str) -> None:
    """失敗理由を job-dir へ残す（`FileJobLedger.read_failure_report` が読む）。"""
    _write_note(job_dir, _FAILURE_FILE, {"reason": reason})


def _record_report_payload_error(job_dir: Path, message: str) -> None:
    """表示用ペイロードの書出し失敗を job-dir へ残す（run の成否は変えない）。"""
    _write_note(job_dir, _REPORT_PAYLOAD_ERROR_FILE, {"message": message})


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


def _load_run_inputs(backtest: "dict[str, Any]") -> "tuple[Any, Any]":
    """ジョブ仕様から (bars, symbol_spec) を得る（committed 公開 IF 経由・R-4）。

    `BacktestResult` は bars を保持しないため、表示用のローソク足と建値推定（MFE/MAE）に
    要る bars は `build_interactor` から取り直す。読み込みの実体（EA 別 MarketDataPort の
    選択・CSV 解析）は `simulator.main` の単一ソースのまま。**この束縛を main 層（本 CLI＝
    Composition Root）が持つ**ことで、adapter（report_payload_writer）が `simulator.main` を
    掴む層違反（adapter→main）を解消する（ISSUE-378 #7 と同一箇所）。
    """
    from simulator.main import build_interactor

    _controller, request = build_interactor(**backtest)
    return request.bars, request.symbol_spec


def _supply_contacts(bars: "list", backtest: "dict[str, Any]") -> "list[dict]":
    """接点（agg.contacts）を「その run が使った EA の指標系列」から組む（FR-18・R-3）。

    EA→指標の対応は `simulator.main.build_ea_indicators`（`_EA_FACTORIES` を単一ソースに
    する公開アクセサ）から得る。算出式は adapter（contacts_supply）が report_ui の単一
    ソースを import して持つ。ここは供給の束縛（Composition Root）だけを担う。

    ``bars`` は writer が読み込み済みの int 時刻ビュー（二重ロードしない）。
    """
    from simulator.main import build_ea_indicators

    indicators = build_ea_indicators(**backtest)
    return contacts_supply.build_contacts(
        bars=bars, backtest=backtest, indicators=indicators,
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

    # 表示用ペイロード（report.json）は**成功 run のときだけ**書く。失敗 run の結果を
    # 表示面へ出すと、古い/壊れた結果が「今の結果」に見える。
    if exit_code == 0 and _result is not None:
        try:
            report_payload_writer.write(
                job_dir, _result,
                load_run_inputs=_load_run_inputs,
                contacts_supply=_supply_contacts,
            )
        except Exception as exc:  # 表示の失敗で成功した計算を捨てない
            message = f"report.json の書出しに失敗しました: {exc}"
            print(message, file=sys.stderr)
            # 終了コードは変えない。ただし**理由は残す**——起動器が stderr を DEVNULL に
            # するため、print だけでは「完了なのに結果が出ない」の原因が誰にも届かない。
            _record_report_payload_error(job_dir, message)
    return exit_code


if __name__ == "__main__":  # pragma: no cover — 子プロセスの入口
    raise SystemExit(main())
