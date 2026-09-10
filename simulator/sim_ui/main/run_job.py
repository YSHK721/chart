"""子プロセス CLI: 1 ジョブ分のバックテストを実行する（§12.7 並列実行の実体）。

起動形（`SubprocessJobLauncher`）:
    <venv python> simulator/sim_ui/main/run_job.py --job-dir <ジョブディレクトリの絶対パス>

**受ける引数は `--job-dir` だけ**である。実行仕様は job-dir の `spec.json` から読む。
仕様を argv に並べると、シェル経由のクォート事故・引数の取り違えという壊れ方を
新たに作ることになる（sim core と子プロセスの間で仕様の表現が二重化する）。

実行経路は 2 本あり、`spec.json` の `settings` ブロックの**有無だけ**で分岐する
（Phase 8 §18.3）:
    settings 不在（既定）: `simulator.main.run_backtest`（現行経路。`run_backtest` へ渡す
        引数も出力段も Phase 8 で変えておらず、旧 spec の `stats.json` は byte 等価）。
    settings 有り        : `main/tester_settings/run_settings_job`（`.ini` の生トークン →
        `TesterSettings` → `EffectiveSettings` → 窓の事後検証 N-15 込みの実行）。
どちらの経路も成果物（`stats.json` / `report.md`）は同一の出力段（`simulator.main` の
`present_outputs`）を通る。Phase 6/7 の拡張点（`strategy_override` / `position_manager` /
`strategy_decorator`）の組み立ては本 CLI の `main()` で**1 度だけ**行い、経路ごとに
書き写さない。

結果ペイロードは上記の既存出力（`stats.json` / `report.md`）に加えて、
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
# 実行トレース（ISSUE-508 段階 3・§6.5.3）の書出し失敗の置き場。**`failure.json` とは
# 別にする**: run 自体は成功しており、同じファイルへ書くと「失敗した run」と区別できなくなる。
# 終了コードも変えない（観測の失敗で成功した計算を捨てない・`report.json` と同じ扱い）。
_TRACE_ERROR_FILE = "trace_error.json"


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


def _record_trace_error(job_dir: Path, message: str) -> None:
    """実行トレースの書出し失敗を job-dir へ残す（run の成否は変えない・§6.5.3）。"""
    _write_note(job_dir, _TRACE_ERROR_FILE, {"message": message})


def _build_run_tracer(spec: "dict[str, Any]") -> Any:
    """spec.trace から実行トレースの観測口を組む（ISSUE-508 段階 3・E-2 の `run_tracer`）。

    Group（`simulator.adapter.trace`）へは**この関数の中でだけ**依存する（是正 D-5）。
    module 直下 import に足すと、トレース OFF の全ジョブが parquet 実装を読み込み、
    「OFF の経路が実装の import に巻き込まれないようにする」既存の隔離（:108-109）が
    黙って壊れる。sizing / strategy / position_manager と同一の様式である。

    窓の解釈（epoch 正規化・半開・`start > end` の拒否）は `TraceWindow` が唯一持つ。
    ここで既定値を置かない——窓を取り落とした run が黙って全期間を記録する形にしない。
    """
    from simulator.adapter.trace.columnar_run_trace import ColumnarRunTrace
    from simulator.adapter.trace.trace_window import TraceWindow

    block = spec.get("trace") or {}
    return ColumnarRunTrace(TraceWindow.of(block.get("start"), block.get("end")))


def _write_trace(job_dir: Path, tracer: Any, run_kwargs: "dict[str, Any]") -> None:
    """実行トレースを job-dir へ書く。**run の成否は変えない**（§6.5.3）。

    呼出点は `main()` のただ 1 箇所である（現行経路と settings 経路で書き写さない）。

    ``run_kwargs``: **その run が実際に `build_interactor` へ渡した引数**（§6.5.2.1）。
      `spec["backtest"]` ではない——`marketdata_window` の供給元は経路で異なるからである:

        現行経路（settings 不在）: `backtest` ブロック。
        settings 経路          : `.ini` の `FromDate`/`ToDate` →
                                 `simulator/main/tester_settings/window.py` の窓境界解決 →
                                 写像層が `marketdata_window` を導く（実測: 窓ありで
                                 `(2025-01-06, 2025-01-11)`・同じ run の `backtest`
                                 ブロックは `None`）。

      `spec["backtest"]` を無条件に読むと、**`.ini` で期間を絞った run ほど食い違いが
      大きいのに、まさにその run が「窓なし・比較可能」と偽って申告する**。§6.5.2 が
      段階 3 に課した義務は「隠さないこと」ただ 1 つであり、その不履行になる。
      同じファイルの `_write_report_payload` が settings 経路で `run_kwargs` を渡して
      いるのと同一の形にそろえる（表示用の足も同じ理由で `backtest` から取り直さない）。

    指標 registry は `build_ea_indicators(**run_kwargs)` から組んで **Callable で注入**する
    （是正 D-4・先例 `_supply_contacts`）——interactor._indicators への到達は
    ISSUE-395/398・ISSUE-405 で 2 度是正済みのカプセル化破りと同型であり、
    エンジン（`simulator/usecase/run_backtest.py`）へプロパティを新設する案は責務分割ゲート
    （`test_run_backtest_responsibility_split.py:275-284` のメソッド集合 8 固定）が赤にする。
    """
    from simulator.main import build_ea_indicators
    from simulator.sim_ui.adapter import trace_writer

    backtest = run_kwargs or {}
    try:
        trace_writer.write(
            job_dir,
            tracer,
            # `job_dir.name` は台帳の採番規則そのものである。writer に読み直させると
            #   `FileJobLedger.job_dir` の規約の 2 つ目の実装ができる（§6.5）。
            job_id=job_dir.name,
            indicators_supply=lambda: build_ea_indicators(**backtest),
            marketdata_window=backtest.get("marketdata_window"),
        )
    except Exception as exc:  # 観測の失敗で成功した計算を捨てない
        message = f"実行トレースの書出しに失敗しました: {exc}"
        print(message, file=sys.stderr)
        _record_trace_error(job_dir, message)


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


def _build_strategy_override(spec: "dict[str, Any]") -> Any:
    """spec.strategy から汎用戦略 :class:`GenericConditionStrategy` を組む（E-2 の `strategy_override`）。

    条件の解釈（未知 op / shift 負値の拒否）は framework の
    `strategy_spec_loader.load_strategy_spec`（sizing_config_loader と対称の単一ソース）へ
    委譲する。基準価格系列は約定価格基準（config_overrides.entry_price_basis）で決まる。

    Group（framework loader・adapter 戦略）へは**この関数の中でだけ**依存する。strategy OFF の
    経路が戦略実装の import に巻き込まれないようにするため（OFF は既存挙動と byte 等価）。
    """
    from simulator.adapter.strategy.generic_condition_strategy import (
        GenericConditionStrategy,
    )
    from simulator.framework.strategy_spec_loader import load_strategy_spec

    backtest = spec.get("backtest") or {}
    overrides = backtest.get("config_overrides") or {}
    # trailing/partial_close（Phase 7 の建玉変更サブブロック）は別 loader
    # （position_manager_spec_loader）が担うため、strategy_spec の extra="forbid" に触れない
    # よう**この 2 キーだけ**除外して残りを渡す。entry_long/entry_short 以外の未知キー（タイポ）は
    # load_strategy_spec の extra="forbid" が引き続き検出する（typo 検出を弱めない）。
    strategy_block = spec.get("strategy") or {}
    entry_block = {
        k: v for k, v in strategy_block.items() if k not in ("trailing", "partial_close")
    }
    entry_long, entry_short = load_strategy_spec(entry_block)
    return GenericConditionStrategy(
        entry_long=entry_long,
        entry_short=entry_short,
        entry_price_basis=overrides.get("entry_price_basis", "close"),
    )


def _build_position_manager(spec: "dict[str, Any]") -> Any:
    """spec.strategy.trailing / partial_close から建玉変更の適用器を組む（Phase 7・E-2 の
    `position_manager`）。トレーリング/部分決済のいずれも無ければ ``None``（OFF＝既存挙動
    byte 等価）。

    検証・domain 規則構築は framework の `position_manager_spec_loader.load_position_change_spec`
    （strategy_spec_loader と対称の単一ソース）へ委譲する。adapter :class:`PositionManager` の
    構築（point_size/volume_step 注入）は本 Composition Root が担う（framework→domain・
    adapter 構築は main の責務・層方向を守る）。Group（framework loader・adapter）へは
    **この関数の中でだけ**依存する（OFF 経路が実装 import に巻き込まれない）。
    """
    from simulator.adapter.position_manager.position_manager import PositionManager
    from simulator.framework.position_manager_spec_loader import (
        load_position_change_spec,
    )

    backtest = spec.get("backtest") or {}
    # 欠落は KeyError（既定値で黙って埋めない＝銘柄と違う点数/刻みで静かに誤らせない）。
    point_size = backtest["point_size"]
    volume_step = backtest["volume_step"]
    change = load_position_change_spec(spec.get("strategy") or {}, point_size=point_size)
    if change is None:
        return None
    return PositionManager(
        trailing_rule=change.trailing_rule,
        partial_close_rule=change.partial_close_rule,
        trailing_granularity=change.trailing_granularity,
        volume_step=volume_step,
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

    EA→指標の対応は `simulator.main.build_ea_indicators`（simulator/main/ea_bindings の
    宣言駆動な束縛表を単一ソースにする公開アクセサ）から得る。算出式は adapter
    （contacts_supply）が report_ui の単一ソースを import して持つ。ここは供給の束縛
    （Composition Root）だけを担う。

    ``bars`` は writer が読み込み済みの int 時刻ビュー（二重ロードしない）。
    """
    from simulator.main import build_ea_indicators

    indicators = build_ea_indicators(**backtest)
    return contacts_supply.build_contacts(
        bars=bars, backtest=backtest, indicators=indicators,
    )


def _settings_supplied_params() -> "frozenset[str]":
    """Settings 写像層が供給する `build_interactor` 引数名の集合（Phase 8）。

    `EngineBinding.ea_params` は「`.ini` からは供給できない EA 固有引数」だけを受ける
    （重なるキーは `ConfigError`＝権威の二重化を防ぐ設計）。その残余を求めるための集合で
    あり、**名前の表を手書きしない**——写像層の公開宣言（`EXPLICIT_BINDINGS`）と、写像層が
    名前一致で導出する 2 つの DTO（`SymbolSpec` / `DataWindow`）のフィールド名から機械的に
    導く（`kwargs_mapper._derived_bindings` と同じ導出規則）。

    導出が実際の写像結果と一致することは
    `sim_ui/tests/integration/test_run_job_settings.py` が実行結果で突き合わせる
    （ずれれば `ea_params` の衝突・不足として即座に落ちる）。
    """
    from dataclasses import fields

    from simulator.main.tester_settings.kwargs_mapper import EXPLICIT_BINDINGS
    from simulator.main.tester_settings.window import DataWindow
    from simulator.usecase.models import SymbolSpec

    return (
        frozenset(binding.param for binding in EXPLICIT_BINDINGS)
        | frozenset(field.name for field in fields(SymbolSpec))
        | frozenset(field.name for field in fields(DataWindow))
    )


def _build_engine_binding(spec: "dict[str, Any]", effective: Any) -> Any:
    """`backtest` ブロック ＋ カタログから `EngineBinding`（§6 補助 DTO）を組む。

    Settings 層は銘柄仕様・データパス・EA 固有引数・決済通貨を持たない（`.ini` に無い）。
    それらの供給は **`sim_ui` 側 Composition Root の責務**である（不変条件 I-6: 変換層は
    `sim_ui` を import しない）。

    供給元（憶測で埋めない）:
        銘柄仕様   — 投入された `backtest`（front が profile から導いた 8 キー）。
                     `SymbolSpec` のフィールド名と同名であるため機械的に写す。
        leverage   — 同じ `backtest` の口座属性（ISSUE-445 段階 3-D2）。`SymbolSpec` から
                     外れたため機械導出（`fields(SymbolSpec)`）に載らない。**明示注入する**
                     ——ここで既定値を置くと、front が送らなくなったことに気付けないまま
                     人が書いた除数で証拠金計算が回る（RC-1 と同型）。欠落は KeyError で
                     即座に落ちる（投入 body のキー集合は不変であり、front は従来どおり
                     `leverage` を送る＝実測）。
        決済通貨   — `SymbolSpecCatalog` の profile（A-2 で恒久化された唯一の供給源）。
                     登録の無い銘柄は**推定しない**で失敗させる。
        EA 固有引数 — `backtest` のうち写像層が供給しない残余（`_settings_supplied_params`）。
        data_path  — バー系列を消費する modelling のときだけ渡す（規則 S）。要否の宣言は
                     `tick_model_registry.consumes_market_data` の 1 箇所にしかない。
    """
    from dataclasses import fields

    from simulator.adapter.execution.tick_model_registry import consumes_market_data
    from simulator.main import known_ea_names
    from simulator.main.tester_settings.kwargs_mapper import EngineBinding, tick_model_word
    from simulator.sim_ui.main.composition_root_jobs import build_run_options_port
    from simulator.usecase.models import SymbolSpec

    backtest = spec.get("backtest") or {}
    symbol = backtest["symbol"]
    profile = next(
        (p for p in build_run_options_port().datasets() if p.symbol == symbol), None
    )
    if profile is None:
        raise ValueError(
            f"銘柄 {symbol!r} の実行プロファイルが登録されていません"
            "（決済通貨の供給源が無いため実行できません。推定値では N-11 の判定が壊れます）"
        )
    supplied = _settings_supplied_params()
    return EngineBinding(
        symbol_spec=SymbolSpec(**{f.name: backtest[f.name] for f in fields(SymbolSpec)}),
        leverage=backtest["leverage"],
        symbol=symbol,
        period=backtest["period"],
        data_path=(
            str(backtest["data_path"])
            if consumes_market_data(tick_model_word(effective.tick_model))
            else None
        ),
        known_ea_names=frozenset(known_ea_names()),
        settlement_currency=profile.settlement_currency,
        ea_params={k: v for k, v in backtest.items() if k not in supplied},
        config_overrides=dict(backtest.get("config_overrides") or {}),
    )


def _write_report_payload(job_dir: Path, result: Any, *, load_run_inputs, contacts_supply) -> None:
    """表示用ペイロード（report.json）を書く。**run の成否は変えない**。

    書出しに失敗しても終了コードを変えないのは、バックテスト自体は成功しており、表示の
    失敗で成功した計算を捨てないためである。ただし理由は残す——起動器が stderr を
    DEVNULL に固定するため、print だけでは「完了なのに結果が出ない」の原因が誰にも届かない。
    """
    try:
        report_payload_writer.write(
            job_dir, result,
            load_run_inputs=load_run_inputs,
            contacts_supply=contacts_supply,
        )
    except Exception as exc:  # 表示の失敗で成功した計算を捨てない
        message = f"report.json の書出しに失敗しました: {exc}"
        print(message, file=sys.stderr)
        _record_report_payload_error(job_dir, message)


def _run_with_settings(
    job_dir: Path, spec: "dict[str, Any]", extensions: "dict[str, Any]"
) -> "tuple[int, dict[str, Any] | None]":
    """Tester Settings 経路（Phase 8 §18.3「実行」）。

    `.ini` の生トークン → `TesterSettings` → `EffectiveSettings` → `run_settings_job`（T-1）。
    検証（規則 B〜Q）は受付段と**同じ実体**（`tester_settings_from_mapping`）を通る。

    終了コードの翻訳は `exit_codes.exit_code_for`（唯一の宣言場所）で行い、**文言は
    `failure.json` に残す**。翻訳を実行 facade の中で行うと理由が終了コードへ潰れ、
    運用者には「なぜ落ちたか」が届かない（起動器は stderr を捨てる）。

    戻り値が `(終了コード, 実効 kwargs)` である理由（ISSUE-508 §6.5.2.1）: 本経路の
    `marketdata_window` は `.ini` の `FromDate`/`ToDate` 由来であり、`spec["backtest"]`
    には**現れない**。トレースの窓の申告をその run の事実に合わせるには、ここで組んだ
    実効 kwargs を書出し点へ渡すしかない（`main()` で組み直すと写像が 2 箇所になる）。
    実行に失敗した run では `None`（そのとき書出しも行われない）。
    """
    from simulator.domain.exceptions import BacktestError
    from simulator.framework.tester_settings import tester_settings_from_mapping
    from simulator.main.tester_settings.exit_codes import exit_code_for
    from simulator.main.tester_settings.kwargs_mapper import effective_to_interactor_kwargs
    from simulator.main.tester_settings.run_settings_job import run_settings_job

    block = spec.get("settings") or {}
    try:
        settings = tester_settings_from_mapping(
            dict(block.get("tester") or {}), list(block.get("inputs") or [])
        )
        effective = settings.effective()
        binding = _build_engine_binding(spec, effective)
    except Exception as exc:
        message = f"Tester Settings の解釈に失敗しました: {exc}"
        print(message, file=sys.stderr)
        _record_failure(job_dir, message)
        return _EXIT_SPEC_ERROR, None

    try:
        exit_code, result, _metadata = run_settings_job(
            effective, binding, output_dir=job_dir, extensions=extensions
        )
    except BacktestError as error:
        message = f"Tester Settings からの実行に失敗しました: {error}"
        print(message, file=sys.stderr)
        _record_failure(job_dir, message)
        return exit_code_for(error), None
    except Exception as exc:  # 内部例外を呼出側へ生で漏らさない
        message = f"バックテストの実行に失敗しました: {exc}"
        print(message, file=sys.stderr)
        _record_failure(job_dir, message)
        return _EXIT_SPEC_ERROR, None

    run_kwargs: "dict[str, Any] | None" = None
    if exit_code == 0 and result is not None:
        # 表示用の足は **settings 経路で実際に使われた投入引数**から取り直す。`backtest`
        # ブロックから取り直すと、`.ini` の期間窓が効いていない全期間の足が「今の結果の足」
        # として表示される（窓を絞った run ほど食い違いが大きくなる）。
        run_kwargs = effective_to_interactor_kwargs(effective, binding)
        _write_report_payload(
            job_dir, result,
            load_run_inputs=lambda _backtest: _load_run_inputs(run_kwargs),
            contacts_supply=lambda bars, _backtest: _supply_contacts(bars, run_kwargs),
        )
    return exit_code, run_kwargs


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

    # `build_interactor` の拡張点への注入物（JSON スカラーでは渡せない実体）。現行経路は
    # `meta` へ載せ、settings 経路は `run_settings_job(extensions=...)` へ渡す——**組み立ては
    # 1 箇所**であり、経路ごとに書き写さない。
    extensions: "dict[str, Any]" = {}
    sizing = spec.get("sizing") or {}
    if sizing.get("enabled", False):
        try:
            extensions["strategy_decorator"] = _build_decorator(spec)
        except Exception as exc:
            message = f"サイジングの構築に失敗しました: {exc}"
            print(message, file=sys.stderr)
            _record_failure(job_dir, message)
            return _EXIT_SPEC_ERROR

    # 戦略項目（Phase 6 F-8・P6-E4）: strategy present のときだけ override を組んで渡す。
    # 不在/空は渡さない（引数の不在で既存挙動 byte 等価）。override と sizing decorator は
    # 独立に `extensions` へ載せ（現行経路では下で `meta` へ合流する）、build_interactor が
    # override 置換→sizing wrap の順で合成する。
    if spec.get("strategy"):
        try:
            extensions["strategy_override"] = _build_strategy_override(spec)
        except Exception as exc:
            message = f"戦略項目の構築に失敗しました: {exc}"
            print(message, file=sys.stderr)
            _record_failure(job_dir, message)
            return _EXIT_SPEC_ERROR

    # 建玉変更（Phase 7 FR-07/08・P7）: strategy.trailing / partial_close が present のときだけ
    # PositionManager を組んで渡す。不在は渡さない（引数の不在で既存挙動 byte 等価）。
    # position_manager と strategy_override/sizing decorator は独立に `extensions` へ載せ、
    # build_interactor が各拡張点へ注入する。
    if spec.get("strategy"):
        try:
            pm = _build_position_manager(spec)
        except Exception as exc:
            message = f"建玉変更（トレーリング/部分決済）の構築に失敗しました: {exc}"
            print(message, file=sys.stderr)
            _record_failure(job_dir, message)
            return _EXIT_SPEC_ERROR
        if pm is not None:
            extensions["position_manager"] = pm

    # 実行トレース（ISSUE-508 段階 3・§6.6）: trace present かつ enabled のときだけ
    # 観測口を組んで渡す。不在/OFF は渡さない（引数の不在で既存挙動 byte 等価）。
    # 窓の指定が不正なら **run を始めない**——「窓を間違えたのに 0 行で成功する run」を
    # 作らないため、実行前の仕様エラーとして落とす（§7）。
    tracer: Any = None
    if (spec.get("trace") or {}).get("enabled", False):
        try:
            tracer = _build_run_tracer(spec)
        except Exception as exc:
            message = f"実行トレースの構築に失敗しました: {exc}"
            print(message, file=sys.stderr)
            _record_failure(job_dir, message)
            return _EXIT_SPEC_ERROR
        extensions["run_tracer"] = tracer

    # Tester Settings 経路（Phase 8 §18・T-1）。settings 不在は**現行経路**へ落ちる。
    # 分岐の下は拡張点の合流（`meta.update`）と書出しの関数化のみで、`run_backtest` への
    # 引数も出力段も変えていない＝旧 spec の `stats.json` は byte 等価
    # （`tests/integration/test_run_job_settings.py` の直接実行との突合で固定）。
    #
    # **どちらの分岐も戻り値で受ける**（早期 return しない）。トレースの書出し点を
    # 分岐ごとに写すと、`_write_report_payload` が 2 箇所から呼ばれている形が増える
    # ——片方だけ改訂される複製を新しく作らない（§6.5.1）。
    if spec.get("settings"):
        exit_code, run_kwargs = _run_with_settings(job_dir, spec, extensions)
    else:
        meta.update(extensions)
        # 現行経路が `build_interactor` へ渡す引数は `meta` そのものである
        #   （`run_backtest(output_dir=..., **meta)`）。窓の申告はここから採る（§6.5.2.1）。
        run_kwargs = meta
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
            _write_report_payload(
                job_dir, _result,
                load_run_inputs=_load_run_inputs,
                contacts_supply=_supply_contacts,
            )

    # 実行トレースの書出し（**唯一の呼出点**）。§12.7 不変: run 完了後に書き出す
    # （実行中の部分結果を出さない）。成功 run のときだけ書く——失敗 run の途中経過を
    # 成果物として出すと、壊れた run の記録が「その run の事実」に見える。
    # 渡すのは spec ではなく**その run が実際に使った引数**である（§6.5.2.1）。
    #   `spec["backtest"]` を読むと、`.ini` で期間を絞った settings run が
    #   「窓なし・比較可能」と偽って申告する（窓を絞った run ほど食い違いが大きい）。
    if tracer is not None and exit_code == 0:
        _write_trace(job_dir, tracer, run_kwargs)
    return exit_code


if __name__ == "__main__":  # pragma: no cover — 子プロセスの入口
    raise SystemExit(main())
