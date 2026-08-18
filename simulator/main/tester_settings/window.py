"""期間指定の写像と適用結果の事後検証（内部設計 §8.4・D-11）。

1. 層名/責務:
    main 層（Composition Root）。`EffectiveSettings.date_range` を現行投入契約の
    取得窓（`marketdata_window` / `tick_start` / `tick_end` / `trading_start`）へ
    写し、**適用された結果**（`RunBacktestRequest.bars` の実時刻範囲）が要求窓に
    収まることを検証する。どの EA がどの Repository を使うかは予測しない。

2. 含む構造:
    DataWindow            : 投入契約へ渡す 4 値の束（§6 の補助 DTO）。
    resolve_data_window   : API-07。`date_range` → `DataWindow`。
    verify_window_applied : 事後検証（不一致・空は N-15）。
    epoch_seconds         : `bar.time`（`numpy.datetime64` / epoch 整数）の正規化。
                            **実体は `simulator.domain.bar_time`**（A-3 で移設）。本モジュール
                            は再 export するのみで、公開名・挙動は移設前と不変。

3. 元 MQL 対応:
    Settings タブの `Date`（#4）・`From`（#5）・`To`（#6）。MT5 は日付単位で区間を
    指定し `To` 当日を含む（V-2）。これを半開区間 `[from 00:00Z, to+1day 00:00Z)`
    へ写す。

4. 依存:
    標準: dataclasses / datetime / typing
    外部: なし（numpy / pandas を import しない。`bar.time` は duck typing で扱う）
    プロジェクト内: simulator.domain.bar_time（A-3・epoch 正規化の単一ソース） /
                    simulator.domain.tester_settings_exceptions /
                    simulator.usecase.tester_settings

実測に基づく確定事項（推測しない）:
    W-1: `marketdata_window` は **全 `MarketDataPort` 実装で効く**（A-3）。`CsvOHLCRepository`
         のときは委譲 repo（`MarketDataSourceRepository`）へ差し替わり、それ以外は
         `WindowedMarketDataRepository` が包んで窓を適用する（`main/__init__.py` 実測）。
         A-3 以前は MT5 タブ形式ローダを使う EA で窓が無視されていた（実測: 窓あり/なしで
         bars の sha256 が同一・28097 本）。本層は機構を予測せず結果を測る方針を維持する
         （`verify_window_applied`）。
    W-2: 窓は半開 `[start, end)`（`marketdata/csv_source.py:59` 実測）。
    W-3: 境界は `datetime.timestamp()` で epoch 秒へ変換される。naive datetime は
         プロセスのローカル TZ で解釈されるため、本モジュールは **UTC aware** の
         datetime だけを生成する（環境依存という原因そのものの除去）。
    W-4: `bar.time` の実体は経路で異なる（comma 形式 CSV = epoch 整数 /
         MT5 タブ形式 = `numpy.datetime64`＝両ローダの実読）。比較は
         ``epoch_seconds`` で正規化してから行う。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

# A-3: `bar.time` の epoch 正規化は domain（`Bar.time` 型契約の所有者）が単一ソースを持つ。
# 本モジュールは実体を再定義せず読むだけにする（窓デコレータとの手書き複製を作らない）。
# 再 export（`EPOCH_CONVERTERS` / `epoch_seconds`）は `tester_settings/__init__.py` の公開
# 名を維持するために残す（移設前後で本モジュールの公開面と挙動は不変）。
from simulator.domain.bar_time import EPOCH_CONVERTERS, epoch_seconds  # noqa: F401
from simulator.domain.exceptions import ConfigError
from simulator.domain.tester_settings_exceptions import SettingsKeyMissingError
from simulator.main.tester_settings.unsupported import RULES, raise_unsupported
from simulator.usecase.tester_settings import (
    DateRangeKind,
    DatesPreset,
    EffectiveSettings,
)

#: 期間の実行時規則（規則 R の一部）に付す ID。実行要求時の必須値検査であるため
#: 検証層の規則 ID（A〜S）のうち R を用いる（基本設計 §4.5.5）。
_RULE_RUNTIME_REQUIRED: str = "R"


@dataclass(frozen=True)
class DataWindow:
    """投入契約へ渡す取得窓（§6 の補助 DTO）。

    marketdata_window: `[start, end)` の UTC aware 半開区間。``None`` はフィルタなし。
    trading_start:     ウォームアップ境界。本層は**常に ``None``** とする（後述）。
    tick_start / tick_end: 実ティック読込区間（`REAL_TICKS` のときのみ意味を持つ）。

    ``trading_start`` を供給しない根拠（実測・症状回避ではない）:
        `RunBacktestInteractor` は ``bar.time < trading_start`` で比較する（実読）。
        ``bar.time`` の実体は経路により epoch 整数（comma 形式）と
        ``numpy.datetime64``（MT5 タブ形式）に分かれ、前者に aware datetime を渡すと
        ``TypeError`` になる。どちらの型になるかは EA→Repository の対応を予測しない
        限り決まらず、その予測は D-11 で棄却済み（代替案 A）。取引開始境界は
        ``marketdata_window`` が既に表現しており（窓外のバーはそもそも読まれない）、
        窓が適用されたことは ``verify_window_applied`` が実測で確認する。
    """

    marketdata_window: "tuple[datetime, datetime] | None"
    trading_start: "datetime | None"
    tick_start: "datetime | None"
    tick_end: "datetime | None"


def _midnight_utc(value: date) -> datetime:
    """日付 → その日の 00:00 UTC（aware）。W-3 のローカル TZ 依存を除去する。"""
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _no_window() -> DataWindow:
    """フィルタなし（全期間）。"""
    return DataWindow(marketdata_window=None, trading_start=None, tick_start=None, tick_end=None)


def _entire_history(_effective: EffectiveSettings) -> DataWindow:
    """`Dates=0`（entire history）: 窓を課さない（K-14: データの範囲は Repository が持つ）。"""
    return _no_window()


def _last_year(_effective: EffectiveSettings) -> DataWindow:
    """`Dates=2`（last year）: 起点を決められないため実行しない（N-16）。

    非対象の宣言（ID・理由・TBD 番号）は `unsupported.RULES` が唯一の所有者であり、
    ここは**送出地点**にすぎない（文言を書き写さない）。実行要求時は
    `apply_unsupported_rules` が先に同じ宣言で弾くが、API-07 は単独でも呼ばれ得る
    ため、窓の解決地点にも同じ宣言による Fail-Stop を置く。
    """
    raise_unsupported(RULES["N-16"], value=int(DatesPreset.LAST_YEAR))


#: `Dates` の値 → 窓の解決（プリセットの追加は**本表への 1 エントリ追加**で済む）。
PRESET_RESOLVERS: "dict[DatesPreset, Callable[[EffectiveSettings], DataWindow]]" = {
    DatesPreset.ENTIRE_HISTORY: _entire_history,
    DatesPreset.LAST_YEAR: _last_year,
}


def _resolve_preset(effective: EffectiveSettings) -> DataWindow:
    date_range = effective.date_range
    preset = None if date_range is None else date_range.preset
    if preset is None:
        raise SettingsKeyMissingError(keys=("Dates",), rule_id=_RULE_RUNTIME_REQUIRED)
    resolver = PRESET_RESOLVERS.get(preset)
    if resolver is None:
        raise ConfigError(
            f"窓の解決規則が未登録の期間プリセットです: {preset!r}",
            context={"preset": int(preset), "allowed": sorted(int(k) for k in PRESET_RESOLVERS)},
        )
    return resolver(effective)


def _resolve_custom(effective: EffectiveSettings) -> DataWindow:
    """`FromDate` / `ToDate`: `[from 00:00Z, to+1day 00:00Z)`（V-2 を半開へ写す）。"""
    date_range = effective.date_range
    from_date = None if date_range is None else date_range.from_date
    to_date = None if date_range is None else date_range.to_date
    missing = [
        key
        for key, value in (("FromDate", from_date), ("ToDate", to_date))
        if value is None
    ]
    if missing:
        raise SettingsKeyMissingError(keys=tuple(missing), rule_id=_RULE_RUNTIME_REQUIRED)
    start = _midnight_utc(from_date)
    end = _midnight_utc(to_date) + timedelta(days=1)
    return DataWindow(
        marketdata_window=(start, end),
        trading_start=None,
        tick_start=start,
        tick_end=end,
    )


#: 期間指定の形 → 解決関数（形の追加は本表への 1 エントリ追加で済む）。
KIND_RESOLVERS: "dict[DateRangeKind, Callable[[EffectiveSettings], DataWindow]]" = {
    DateRangeKind.PRESET: _resolve_preset,
    DateRangeKind.CUSTOM: _resolve_custom,
}


def resolve_data_window(effective: EffectiveSettings) -> DataWindow:
    """API-07: `EffectiveSettings` → `DataWindow`。

    事前条件: ``effective.date_range`` が非 ``None``（inert 化されていない）。
    事後条件: ``marketdata_window`` は UTC aware・半開区間、または ``None``。
    例外: ``SettingsKeyMissingError``（E-08・規則 R）/ ``UnsupportedSettingError``
        （E-07・N-16）/ ``ConfigError``（未登録の期間形式）。
    """
    date_range = effective.date_range
    if date_range is None:
        raise SettingsKeyMissingError(
            keys=("Dates", "FromDate", "ToDate"), rule_id=_RULE_RUNTIME_REQUIRED
        )
    resolver = KIND_RESOLVERS.get(date_range.kind)
    if resolver is None:
        raise ConfigError(
            f"窓の解決規則が未登録の期間形式です: {date_range.kind!r}",
            context={"kind": str(date_range.kind), "allowed": sorted(str(k) for k in KIND_RESOLVERS)},
        )
    return resolver(effective)


# ---------------------------------------------------------------------------
# 適用結果の事後検証（§8.4.3）
# ---------------------------------------------------------------------------


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _raise_window_not_applied(
    *,
    requested: "tuple[int, int]",
    actual: "tuple[int, int] | None",
    ea_name: "str | None",
) -> None:
    """N-15 を送出する（宣言＝ID・field・理由は `unsupported.RULES` が所有）。

    `context` には §8.4.4 の診断 3 点（要求窓・実バー範囲・EA 名）を**常に**載せる。
    ``ea_name`` が ``None`` の場合もキーを落とさない: 落とすと「EA 名が供給されて
    いない」と「EA 名の供給地点が壊れている」が受け手から区別できなくなる。
    """
    start, end = requested
    raise_unsupported(
        RULES["N-15"],
        value=("空" if actual is None else f"{_iso(actual[0])}..{_iso(actual[1])}"),
        requested_window=[_iso(start), _iso(end)],
        actual_range=([] if actual is None else [_iso(actual[0]), _iso(actual[1])]),
        ea_name=ea_name,
    )


def verify_window_applied(
    request: Any, window: DataWindow, *, ea_name: "str | None" = None
) -> None:
    """要求した期間窓がエンジンへ実際に適用されたことを検証する（N-15）。

    事前条件: ``request`` は `build_interactor` が返した `RunBacktestRequest`
        （``bars`` は時刻昇順＝`MarketDataPort` の契約）。
    事後条件: 例外を送出しなければ、``request.bars`` の実時刻範囲は要求窓に収まる。
    例外: 収まらない／バーが 0 本のとき ``UnsupportedSettingError``（E-07・N-15）。
        その `context` は §8.4.4 の診断 3 点（``requested_window`` /
        ``actual_range`` / ``ea_name``）を必ず持つ。

    ``ea_name`` は**診断専用**であり、合否の判定には一切用いない（判定は要求窓と
    ``request.bars`` の実時刻範囲だけで決まる）。`RunBacktestRequest` は EA 名を
    持たないため（6 フィールド実測）、供給できるのは EA 名を既に解決している
    呼出側だけである。判定に使わない値を必須引数にすると、単独で窓検証だけを
    行う呼出しが EA 名の捏造を強いられるため、省略可能とし未供給は ``None``
    として `context` に明示する。実行経路（`run_from_settings`）は
    `ea_stem(effective.subject_path)` を渡すため常に実名が載る。

    `controller.run()` の**前**に呼ぶこと（Fail-Stop の維持: 非対象設定でエンジンを
    走らせない）。窓を課していない場合（``marketdata_window is None``）は検証対象が
    存在しないため何もしない。
    """
    if window.marketdata_window is None:
        return
    start, end = window.marketdata_window
    requested = (epoch_seconds(start), epoch_seconds(end))
    bars = list(request.bars)
    if not bars:
        _raise_window_not_applied(requested=requested, actual=None, ea_name=ea_name)
    actual = (epoch_seconds(bars[0].time), epoch_seconds(bars[-1].time))
    if actual[0] < requested[0] or actual[1] >= requested[1]:
        _raise_window_not_applied(requested=requested, actual=actual, ea_name=ea_name)
