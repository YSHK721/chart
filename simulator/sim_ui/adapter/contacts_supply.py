"""接点（agg.contacts）の結線（adapter 層・Phase 5 F-7）。

sim の payload には `agg.contacts` が無い（実測）。接点マーカー（FR-18・価格×MA の交差）は
**その run が実際に使った EA の MA 系列**と表示足から組む。

    ジョブ仕様 ＋ EA の指標系列 ──> compute_segment_contacts ──> [{time, price, dir}]
                                   （report_ui/tools の単一ソース＝プロト bit 一致）

算出式はここに 1 行も書かない。書いた瞬間、同じ接点を出す 2 経路（report_ui の IS/OOS
レポートと sim ジョブ）が静かに食い違う（§12.3-3 複製禁止）。本モジュールが持つのは
「ジョブ仕様と指標供給 → その関数の引数」への変換だけである。

指標系列は**注入**で受ける（`simulator.main.build_ea_indicators` の戻り＝`.get(name)` を
持つもの）。EA→指標の対応は `simulator.main` の単一ソースにあり、ここには写さない。

tick は読まない（`full_scan=False`＝preview・確定足 close クロスのみ）。移植元
`report_ui/tools/export_report_payload.py` と同じ選択である——sim ジョブは合成ティック
経路で走り、実ティック源を持たないため、full_scan は「無いもの」を読むことになる。
"""
from __future__ import annotations

from typing import Any, Sequence

from simulator.domain.exceptions import IndicatorBufferError
from simulator.report_ui.tools.contacts_export import compute_segment_contacts

#: 接点の相手となる指標系列の名前。接点は「価格 × 移動平均」の交差であり、EA の指標
#: レジストリでは EMA が ``"ema"`` で登録されている（`simulator.main` の registry 群）。
#: これを持たない EA（既定 TC 経路＝madiff/close）では接点は定義できない。
MA_SERIES_NAME = "ema"


def ma_values_of(series: "Sequence[Any]") -> "dict[int, float]":
    """指標系列を bar_index → 値の写像へ変換する（位置対応・NaN は落とす）。

    NaN は warmup（指標の未定義区間）である。0 とみなして渡すと、価格が「0 を跨いだ」と
    判定されて先頭に偽の接点が並ぶ。値が無い足は**渡さない**（usecase 側は写像に無い
    bar_index を「前足 MA 無し」として正しくスキップする）。
    """
    values: "dict[int, float]" = {}
    for index, value in enumerate(series):
        number = float(value)
        if number != number:  # NaN（pandas/numpy を import せずに判定する）
            continue
        values[index] = number
    return values


def build_contacts(
    *,
    bars: "Sequence[Any]",
    backtest: "dict[str, Any]",
    indicators: Any,
    series_name: str = MA_SERIES_NAME,
) -> "list[dict]":
    """1 run の表示足と EA 指標から `agg.contacts`（`[{time, price, dir}]`）を組む。

    ``bars``: `.time/.high/.low/.close` を持つ昇順バー列（表示に使うものと同一実体）。
    ``backtest``: ジョブ仕様の backtest セクション（symbol / period / ma_period / ma_method）。
      欠落は KeyError にする（既定値で黙って埋めない＝別の指標設定の接点を出さない）。
    ``indicators``: `.get(name)` を持つ指標供給（`simulator.main.build_ea_indicators` の戻り）。

    MA 系列を持たない EA では **[]** を返す（接点は価格×MA の交差なので定義できない）。
    足が無いときも [] を返す。
    """
    if not len(bars):
        return []
    try:
        series = indicators.get(series_name)
    except IndicatorBufferError:
        # 接点を持てない EA。表示上は「接点が無い run」であって、失敗ではない。
        return []
    return compute_segment_contacts(
        bars=bars,
        ma_values=ma_values_of(series),
        ref=backtest["symbol"],
        timeframe=backtest["period"],
        indicator=series_name,
        variant="",
        params={"period": backtest["ma_period"], "method": backtest["ma_method"]},
        full_scan=False,
    )
