"""P-1 IndicatorSeriesPort / P-2 BarSupplyPort の実装（既存 `/compute` を read-only で読む）。

計算供給は `simulator.replay_ui.adapter._indicator_ui_bridge` の `full_compute` を
**in-process で読むだけ**である（replay / sim の前例と同形。HTTP でライブ core を叩かない
＝計算プールを奪わない・arch-spec §3）。指標の core は 1 行も変えない。

計算量の規律（§7・T-1・CLAUDE.md 絶対命令 §4.1）:
    同一 `(indicator_id, variant, params_key, timeframe)` の full 系列発行は **1 回以下**。
    P-1 は「1 呼出 = 1 計算 = 3 消費者（ラダー / 第 2 表 / 価格投影）で共有」の束契約なので、
    畳み込みはこの面が持つ。素材（DataFrame）も足ごとに 1 回だけ組み立てる。

素材の共有（ISSUE-457・§7 の 2 段をそのまま構造にする）:
    段 1（バー確定）… **確定足の素材**（形成中足を除いた前半）の full 系列を作る。これは
    その時間足の周期（epoch）の中では定義上不変なので、:class:`MaterialStore` へ置いて
    **要求をまたいで共有**する。
    段 2（ティック）… 形成中足の 1 点だけを `latest_compute`（ライブ core の増分ディスパッチ）
    で作り、確定系列の末尾へ継ぐ。素材（DataFrame）は毎要求読み直す（実測 1〜6 ms/足）ので、
    現在値・走行 H/L は共有と引き換えに古くならない。
    共有しないと epoch 不変のティックでも同じ確定系列を毎秒作り直す（§9-4 実測: 要求の 78%）。

技術隔離（CLEAN_ARCH §6）: pandas と indicator_ui はこのファイル（と同じ gateway 層）に
閉じる。usecase / domain は `dashboard_ui.usecase.sheet_ports` の Protocol 越しにしか
外を知らない。
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from marketdata.tf_meta import period_start_unix

from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.domain.bar import Bar
from dashboard_ui.usecase.sheet_ports import SeriesSupplyUnavailable

#: 系列 JSON の点の形（§6.3.2: time は UNIX 秒・value は float / 欠測は None）。
_DATA = "data"
_NAME = "name"
_TIME = "time"
_VALUE = "value"


class IndicatorUiComputeGateway:
    """P-1 / P-2 の実装。`bridge` は `_indicator_ui_bridge.load_compute()` の namespace。

    Args:
        bridge: dataset ＋ 計算面の namespace（None なら既定の bridge を遅延で解決する）。
        bar_limits: 足ごとに読む本数の上限。参照実装 `tools/measure/issue449/probe_inverse.py`
            の本数表と同じ役割で、費用の上限を素材の側で決める。None は全件。
        store: 確定素材を epoch 単位で持つストア（ISSUE-457）。**省略時はこの口だけの
            ストア**になり、共有は 1 要求で閉じる（従来と同じ費用）。要求をまたいで共有
            するかどうかは Composition Root の決定である（adapter は自分で相手を選ばない）。
    """

    def __init__(
        self, *, bridge: Any = None, bar_limits: "Mapping[str, int] | None" = None,
        store: "MaterialStore | None" = None,
    ) -> None:
        self._bridge = bridge
        self._bar_limits = dict(bar_limits or {})
        self._store = store if store is not None else MaterialStore()
        self._frames: "dict[tuple[str, str], Any]" = {}
        self._bars: "dict[tuple[str, str], tuple[Bar, ...]]" = {}
        self._series: "dict[tuple[str, str, str, str], Mapping[str, tuple]]" = {}

    # ------------------------------------------------------------------ P-1
    def full_series(
        self, *, indicator_id: str, variant: str, params: "Mapping[str, object]",
        dataset_ref: str, timeframe: str,
    ) -> "Mapping[str, tuple[tuple[int, float], ...]]":
        """系列名 → ((time, value), ...)。同一キーは 1 回しか計算しない。

        確定足ぶんは epoch 単位でストアから受け取り（要求をまたいで共有）、形成中足の
        1 点だけを毎要求作って継ぐ（§7 の 2 段そのもの・ISSUE-457）。
        """
        key = (indicator_id, variant, _params_key(params), timeframe)
        cached = self._series.get(key)
        if cached is not None:
            return cached
        bridge = self._resolve_bridge()
        frame = self._frame(dataset_ref, timeframe)
        settings = dict(params)
        confirmed, forming = _split_at_forming_bar(frame)
        if confirmed is None:
            # 素材が 1 本しかない（形成中足しか無い）。分けようがないので全件で 1 回だけ計算する。
            self._series[key] = _as_points(
                self._compute(bridge.full_compute, bridge, indicator_id, variant,
                              frame, settings, timeframe)
            )
            return self._series[key]

        confirmed_points = self._store.material(
            key=(dataset_ref, timeframe),
            epoch=_material_epoch(confirmed, forming, timeframe),
            name=(indicator_id, variant, _params_key(params)),
            factory=lambda: _as_points(
                self._compute(bridge.full_compute, bridge, indicator_id, variant,
                              confirmed, settings, timeframe)
            ),
        )
        # 形成中足の 1 点はライブ core の増分ディスパッチ（`latest_compute`）で作る。
        #   増分器を宣言した指標では末尾 1 点が full と bit 一致し（実測 2026-08-30）、
        #   宣言の無い指標では core 側が min_window ぶんの再計算へ落ちる（ライブと同一規約）。
        forming_points = _as_points(
            self._compute(bridge.latest_compute, bridge, indicator_id, variant,
                          frame, settings, timeframe)
        )
        self._series[key] = _splice(confirmed_points, forming_points)
        return self._series[key]

    def _compute(self, compute, bridge, indicator_id, variant, frame, params, timeframe):
        """計算面 1 回ぶんの呼び出し（失敗は usecase 境界の契約型へ翻訳する）。"""
        # ライブ core の検定エラー（ComputeError: 本数不足 E01 等）も当該 instance に
        #   固有の供給失敗である。型は bridge が `compute_error` として公開するものを使う
        #   （core の内部モジュール構成へ直接 import で密結合しない）。
        translated_failure_types = getattr(bridge, "compute_error", ())
        try:
            return compute(bridge.adapter, indicator_id, variant, frame, params)
        except KeyError as error:
            # ライブ core の束縛台帳に (indicatorId, variant) が無い。テンプレートは
            #   ダッシュボード非対応の指標も運びうるため、当該 instance に固有の
            #   契約上の失敗として usecase 境界の型で伝える（シート全体を落とさない）。
            raise SeriesSupplyUnavailable(
                f"ライブ core に束縛がありません: ({indicator_id!r}, {variant!r})"
            ) from error
        except translated_failure_types as error:
            # 例: 上位足の素材本数が指標の必要本数に満たない（1W 171 本 < 必要 523 本）。
            #   未捕捉のまま貫通させると応答の無い接続断＝ルータで 502 になる（ISSUE-459）。
            raise SeriesSupplyUnavailable(
                f"計算できません: ({indicator_id!r}, {timeframe!r}) — {error}"
            ) from error

    # ------------------------------------------------------------------ P-2
    def bars(self, *, dataset_ref: str, timeframe: str) -> "tuple[Bar, ...]":
        """足の全件（時刻昇順）。"""
        key = (dataset_ref, timeframe)
        cached = self._bars.get(key)
        if cached is not None:
            return cached
        frame = self._frame(dataset_ref, timeframe)
        self._bars[key] = _as_bars(frame)
        return self._bars[key]

    def forming_bar(
        self, *, dataset_ref: str, timeframe: str, now_unix: int
    ) -> "Bar | None":
        """形成中の足（素材の末尾が現在の周期を覆っていなければ None）。

        供給が現在の周期に届いていないとき、古い足を「形成中」と偽らない（§5.2 水準なし・
        無言の縮退禁止）。周期の判定は `marketdata.tf_meta.period_start_unix` が唯一源で
        あり、ライブ側と同じ規約になる。
        """
        supplied = self.bars(dataset_ref=dataset_ref, timeframe=timeframe)
        if not supplied:
            return None
        last = supplied[-1]
        if period_start_unix(int(last.time), timeframe) != period_start_unix(
            int(now_unix), timeframe
        ):
            return None
        return last

    # ------------------------------------------------------------------ 内部
    def _resolve_bridge(self) -> Any:
        if self._bridge is None:
            from simulator.replay_ui.adapter import _indicator_ui_bridge  # 遅延: 技術隔離

            self._bridge = _indicator_ui_bridge.load_compute()
        return self._bridge

    def _frame(self, dataset_ref: str, timeframe: str) -> Any:
        """素材の DataFrame（足ごとに 1 回だけ組み立てる）。"""
        key = (dataset_ref, timeframe)
        cached = self._frames.get(key)
        if cached is not None:
            return cached
        bridge = self._resolve_bridge()
        if not bridge.dataset.is_known(dataset_ref):
            raise ValueError(f"未知の datasetRef です: {dataset_ref!r}")
        if not bridge.dataset.is_known_timeframe(timeframe):
            raise ValueError(f"未知の timeframe です: {timeframe!r}")
        frame = bridge.dataset.load_dataframe(dataset_ref, timeframe)
        limit = self._bar_limits.get(timeframe)
        self._frames[key] = frame if limit is None else frame.tail(int(limit))
        return self._frames[key]


def _split_at_forming_bar(frame: Any) -> "tuple[Any | None, Any]":
    """素材を「確定足ぶん」と「形成中足（末尾 1 本）」へ分ける。

    末尾が形成中の足でありうることは P-2 の契約そのもの（`sheet_ports.BarSupplyPort.bars`
    の docstring・参照実装 `probe_heatmap.py:128` の `H0/L0 = h[-1]/l[-1]`）。**末尾が既に
    確定していた場合でも**、その 1 点は段 2 の `latest_compute` が同じ素材から作り直すので
    値は変わらない（分け方の誤りが出力に漏れない）。

    確定足が 1 本も無いときは `(None, frame)` を返す（分ける意味が無い）。
    """
    if len(frame) < 2:
        return None, frame
    return frame.iloc[:-1], frame.tail(1)


def _unix_seconds(frame: Any) -> int:
    """素材の末尾行の時刻（UNIX 秒）。`_as_bars` の符号化と同一。"""
    return int(frame.index.values.astype("datetime64[s]").astype("int64")[-1])


def _material_epoch(confirmed: Any, forming: Any, timeframe: str) -> tuple:
    """確定素材の版（この署名が同じなら確定素材は同じ物である）。

    周期の始端だけでは足りない。周期が進まないまま確定素材が入れ替わる経路が実在する
    （ロールアップの再生成・読み取り時の外れ値補正の変更・供給の遡り訂正）。周期だけを版に
    すると、その差し替えを 1 周期ぶん（1M なら 1 か月）見落とす。**古い素材を配らない**方が
    共有より優先する。

    署名の形は GPD 当てはめキャッシュの窓署名（usecase 側）と同じ考え方である
    ——本数だけを署名にしてはならない。
    周期の判定そのものは `marketdata.tf_meta.period_start_unix` が唯一源のままである。
    """
    seconds = confirmed.index.values.astype("datetime64[s]").astype("int64")
    return (
        period_start_unix(_unix_seconds(forming), timeframe),   # どの周期か
        int(len(seconds)),                                      # 窓の本数
        int(seconds[0]),                                        # 窓の先頭
        float(confirmed["close"].iloc[-1]),                     # 窓の末尾の値
    )


def _splice(
    confirmed: "Mapping[str, tuple[tuple[int, float], ...]]",
    forming: "Mapping[str, tuple[tuple[int, float], ...]]",
) -> "Mapping[str, tuple[tuple[int, float], ...]]":
    """確定系列の末尾へ、形成中足の点（確定の末尾より後のものだけ）を継ぐ。

    形成中の点を持たない系列（増分器が末尾 1 点を出せない系列）は確定足で終わる。粒度は
    ライブ core の毎ティック末尾値アダプタ（live_tick_tails）と同一である＝チャートで
    ティックごとに動く線と動かない線の区別に一致する。

    **これは instance 単位の縮退一覧では表せない**（同じ instance の中で系列ごとに粒度が
    違うため）。実測 2026-08-30: 該当は profit_rsi の 4 系列（帯外・GPD）だけであり、
    第 1 表の水準にも第 2 表のセルにも使われていない（40 instance の応答を是正前後で突合し
    完全一致を確認済み）。**シートが読む系列に該当が出たら、そのときは縮退として出す**
    （出さないまま粒度が落ちれば無言の縮退になる）。
    """
    names = [*confirmed, *(name for name in forming if name not in confirmed)]
    spliced: "dict[str, tuple[tuple[int, float], ...]]" = {}
    for name in names:
        head = tuple(confirmed.get(name, ()))
        boundary = head[-1][0] if head else None
        tail = tuple(
            point for point in forming.get(name, ())
            if boundary is None or point[0] > boundary
        )
        spliced[name] = head + tail
    return spliced


def _params_key(params: "Mapping[str, object]") -> str:
    """畳み込みキーのパラメータ部（Input Model の畳み込みキーと同一規約＝決定的）。"""
    return json.dumps(dict(params), sort_keys=True, ensure_ascii=False, default=str)


def _as_points(
    series: "list[dict[str, Any]] | None",
) -> "Mapping[str, tuple[tuple[int, float], ...]]":
    """系列 JSON を `名前 → ((time, value), ...)` へ写す。

    同名の系列が複数返ることがある（実測: MA 乖離率は line と horizontal_line を同名で
    返す）。後から来た**空の系列で実体を上書きしない**——上書きすると水準が丸ごと落ちる。
    """
    points: "dict[str, tuple[tuple[int, float], ...]]" = {}
    for entry in series or []:
        name = entry.get(_NAME)
        if name is None:
            continue
        values = tuple(
            (int(point[_TIME]), float(point[_VALUE]))
            for point in (entry.get(_DATA) or [])
            if point.get(_VALUE) is not None
        )
        if values or name not in points:
            points[name] = values
    return points


def _as_bars(frame: Any) -> "tuple[Bar, ...]":
    """DataFrame → `Bar` の列（時刻は UNIX 秒。`candle.time` と同一符号化）。"""
    seconds = frame.index.values.astype("datetime64[s]").astype("int64").tolist()
    columns = {
        name: frame[name].to_numpy(dtype="float64").tolist()
        for name in ("open", "high", "low", "close")
    }
    volume = (
        frame["volume"].to_numpy(dtype="float64").tolist()
        if "volume" in frame.columns
        else [0.0] * len(seconds)
    )
    return tuple(
        Bar(time=int(time), open=open_, high=high, low=low, close=close, volume=vol)
        for time, open_, high, low, close, vol in zip(
            seconds, columns["open"], columns["high"], columns["low"],
            columns["close"], volume,
        )
    )
