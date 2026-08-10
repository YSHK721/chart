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
  5. 形成中バーを注入（resolve_now_unix → apply_forming_bar）。計算窓＝チャートの窓とする
     ため mode に依らず注入する（ISSUE-361）。欠落閉周期の合成のみ mode="latest" 専用。
  6. limit>0 のとき直近 N 本へ tail。
  7. full / latest ディスパッチで指標計算。ComputeError / KeyError を error 表現へ翻訳。

error_type → HTTP ステータスの対応（ERROR_STATUS）は presentation の責務であり、本層は
error_type と message のみを ComputeResult に載せる（HTTP 依存を内側へ持ち込まない）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# ISSUE-182 item4: 協調子 4 依存の契約（Protocol）。是正前は Any / Callable / type ＝契約未定義だった。
from usecase.compute_ports import (
    ComputeDispatchPort,
    ComputeErrorPort,
    FormingBarPort,
    IndicatorComputePort,
    LatestComputeDispatchPort,
    MtfProjectionPort,
    PeriodBoundaryPort,
)
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
    #: 計算足 H（ISSUE-274）。``None`` / ``"chart"`` / ``timeframe`` と同値なら投影しない
    #: ＝リクエストの意味も応答も従来と完全に同一（後方互換）。
    compute_timeframe: Any = None

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
            compute_timeframe=body.get("computeTimeframe"),
        )


@dataclass
class ComputeResult:
    """/compute の Output Model（成功は series、失敗は error_type/message）。"""

    generation: Any
    series: Optional[list] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    #: 指標が申告した機械可読な診断（ISSUE-283）。既定は空＝従来の応答形と同一。
    error_violations: Optional[list] = None

    @property
    def ok(self) -> bool:
        return self.error_type is None

    @classmethod
    def success(cls, generation: Any, series: list) -> "ComputeResult":
        return cls(generation=generation, series=series)

    @classmethod
    def error(cls, generation: Any, error_type: str, message: str,
              violations: Optional[list] = None) -> "ComputeResult":
        return cls(generation=generation, error_type=error_type, error_message=message,
                   error_violations=list(violations) if violations else None)


def compute_indicators(
    request: ComputeRequest,
    *,
    dataset_port: "Optional[DatasetPort]" = None,
    compute_adapter: "IndicatorComputePort",
    forming_bar: "FormingBarPort",
    full_compute: "ComputeDispatchPort",
    latest_compute: "LatestComputeDispatchPort",
    compute_error: "type[ComputeErrorPort]",
    project_mtf: "Optional[MtfProjectionPort]" = None,
    period_boundary: "Optional[PeriodBoundaryPort]" = None,
) -> ComputeResult:
    """POST /compute の業務手順（純関数）。

    Args:
        request: Input Model（ComputeRequest）。
        dataset_port: DatasetPort。None のとき既定 gateway を遅延合成する（未注入時の既定）。
        compute_adapter: IndicatorComputePort（full/latest_compute の第 1 引数）。
        forming_bar: FormingBarPort（resolve_now_unix / apply_forming_bar）。
        full_compute: ComputeDispatchPort（全件計算・adapter, id, variant, df, params）。
        latest_compute: LatestComputeDispatchPort（Latest 計算・上記 ＋ キーワード専用 min_tail）。
        compute_error: 指標計算が送出する例外の型（ComputeErrorPort＝error_type/message を持つ）。
        project_mtf: MtfProjectionPort（上位足系列 → チャート足時間軸への投影）。
            ``computeTimeframe`` を伴う要求でのみ使う（ISSUE-274）。
        period_boundary: PeriodBoundaryPort（期間始端の唯一源）。同上。

    契約（ISSUE-182 item4）は型注釈と ``tests/test_usecase_compute_ports.py`` で固定する。
    実行時の ``isinstance`` 強制は行わない（挙動不変・DatasetPort と同じ扱い）。

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

    # 3b. 計算足 H の解決（ISSUE-274）。``timeframe`` はチャート足 C＝時間軸であり、
    #     ``computeTimeframe`` は「この指標を計算する足」。両者が異なるときだけ、計算は H で行い
    #     応答は C の時間軸へ投影する。未指定 / "chart" / C と同値なら投影経路に入らず、
    #     ロード・計算・応答のすべてが従来と完全に同一（後方互換）。
    compute_tf = request.compute_timeframe
    if compute_tf == "chart":
        compute_tf = None
    if compute_tf is not None and not port.is_known_timeframe(compute_tf):
        return ComputeResult.error(
            generation, "validation", f"未知の computeTimeframe です: {compute_tf!r}"
        )
    project = compute_tf is not None and compute_tf != request.timeframe
    if project and (project_mtf is None or period_boundary is None):
        # 結線漏れ（DatasetPort 未注入時と対称の扱い）。投影せずに H の系列を返すと、時間軸の
        #   汚染と未来情報の混入をそのまま配ることになる＝黙って誤った描画を出すより即時に落とす。
        raise RuntimeError(
            "computeTimeframe が指定されましたが投影協調子が未結線です"
            "（project_mtf / period_boundary を注入してください）。"
        )
    load_timeframe = compute_tf if project else request.timeframe

    # 4. データロード（計算に使う足＝投影時は H・非投影時は従来どおり C）。
    df = port.load_dataframe(request.dataset_ref, load_timeframe)

    mode = request.mode
    # 上位足の因果系列（ISSUE-295）は「確定 H 足 ＋ C から畳んだ形成 H 足」で各バーを計算する。
    #   保存済み H の進行中期間の足は**期間全体の OHLC**（＝そのバーの時点では知り得ない値）を
    #   含むため使わない。形成中バーの注入も行わない（畳みで作る）。よって H 側の一括計算は
    #   丸ごと不要になり、この分岐では 5〜7 を通さない。
    df_source = df if project else None
    series: "list | None" = None

    # 5. 形成中バーの注入（ライブの計算窓＝チャートの窓・ISSUE-361）。
    #    チャートは「確定足（/candles）＋形成中バー」を描く。ここで確定足だけを計算窓にすると、
    #    最新足に値を持てるのは足内追従（mode="latest"）に載れる指標だけになり、載れない指標
    #    （level_dash の cvfe＝front の tailUpdatable=false）は最新足の値がどの経路でも生成
    #    されない。注入を mode で分けていたことが原因なので、注入は mode に依らない 1 つの規約に
    #    する（窓の末端＝チャートの末端）。リプレイは窓の末端＝リビール位置のため元から不整合が
    #    無く、本規約はライブ経路（本 usecase）に閉じる。
    #    欠落閉周期の合成だけは latest 専用のまま（確定バーのリペイントになる・2026-07-23 承認設計）。
    #    ISSUE-162: 注入で増えたバー数（欠落閉周期の合成＋形成中）を数え、末尾切りの下限
    #    （min_tail）として計算側へ渡す（合成バーが応答から切り落とされる歯抜けを防ぐ）。
    injected_tail = None
    now_unix = forming_bar.resolve_now_unix(request.forming_now)
    if not project:
        try:
            n_before = len(df)
        except TypeError:  # len 非対応の注入 fake（テスト）＝従来挙動（min_tail なし）
            n_before = None
        # 形成中バーは「計算に使う足」のものを注入する（投影時は H の形成中バー）。
        df = forming_bar.apply_forming_bar(
            df, request.dataset_ref, load_timeframe, now_unix,
            synthesize_closed_gaps=(mode == "latest"),
        )
        if n_before is not None:
            try:
                injected_tail = max(1, len(df) - n_before + 1)
            except TypeError:
                injected_tail = None

    # 6. 表示範囲制限（直近 N 本）。
    limit = request.limit
    if isinstance(limit, int) and limit > 0 and not project:
        df = df.tail(limit)

    # 7. 指標計算＋エラー翻訳（上位足の因果系列は 8 で各バーを計算するためここは通らない）。
    compute_params = dict(request.params)
    if not project:
        try:
            series = (
                latest_compute(compute_adapter, request.indicator_id, request.variant, df,
                               compute_params, min_tail=injected_tail)
                if mode == "latest"
                else full_compute(compute_adapter, request.indicator_id, request.variant, df,
                                  compute_params)
            )
        except compute_error as exc:  # ComputeError（error_type/message）を error 表現へ。
            return ComputeResult.error(generation, exc.error_type, exc.message,
                                       getattr(exc, "violations", None))
        except KeyError as exc:  # 未登録 id/variant は CallBinding.resolve が raw KeyError。
            return ComputeResult.error(
                generation, "validation", f"未登録の指標または variant です: {exc}"
            )

    # 8. 上位足の因果系列（ISSUE-274 → 295）: チャート足 C の各バー τ へ「τ 時点で計算できた
    #    H の値」を載せる。従来は「いまの H 系列を期間単位で C へ写す」規約だったため、点の
    #    意味が「その期間の値」であり、過去のバーの点に τ より後の情報が載っていた（各期間が
    #    その期間の最終値で塗り潰される）。規約の実体は project_mtf 協調子（= mtf_causal）が持つ。
    #    投影先の時刻集合は /candles と同じ経路（load_dataframe + tail(limit)）から採るため、
    #    応答の time は必ず C のバー時刻の部分集合になる（時間軸へ C 外の時刻が混ざらない）。
    if project:
        df_chart_all = port.load_dataframe(request.dataset_ref, request.timeframe)
        # 投影先の時間軸も同じ規約（計算窓＝チャートの窓）で組む。ここで形成中バーを落とすと
        #   上位足指標だけが最新足を持たない非対称が残る（ISSUE-361）。
        df_chart_all = forming_bar.apply_forming_bar(
            df_chart_all, request.dataset_ref, request.timeframe, now_unix,
            synthesize_closed_gaps=(mode == "latest"),
        )
        df_chart = df_chart_all
        if isinstance(limit, int) and limit > 0:
            df_chart = df_chart.tail(limit)
        if mode == "latest":
            # 足内更新（ティック粒度）で動くのは**最後のバーだけ**である（過去の点は定義上
            #   不変＝時刻不変）。全窓を作り直すのは捨てられる計算になるため末尾 1 本に絞る。
            #   畳みは期間の先頭から行う（fold_from）ので値は全窓計算と一致する。
            df_chart = df_chart.tail(1)
        try:
            series = project_mtf(
                df_chart=df_chart,
                df_source=df_source,
                compute_tf=compute_tf,
                fold_from=df_chart_all,
            )
        except compute_error as exc:
            return ComputeResult.error(generation, exc.error_type, exc.message,
                                       getattr(exc, "violations", None))
        except KeyError as exc:
            return ComputeResult.error(
                generation, "validation", f"未登録の指標または variant です: {exc}"
            )

    return ComputeResult.success(generation, series or [])
