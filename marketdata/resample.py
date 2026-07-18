"""resample — OHLC 再集計の唯一の規則源（enabler③・dataset から物理移設）。

時間足コード（``"5m"/"1h"/"1D"`` …）→ pandas resample ルールの写像（:data:`TIMEFRAME_RULES`）と、
DataFrame を当該ルールで OHLC 再集計する :func:`resample_ohlc` を提供する。これは indicator_ui の
``dataset.resample_ohlc`` から物理移設した「唯一の規則源」であり、rollup（:mod:`marketdata.rollup`）と
indicator_ui ``dataset``（薄い再エクスポート）が共通して再利用する（再実装を禁ずる）。

依存方向（厳守）: 本モジュールは **pandas のみ** に依存し、indicator_ui を逆 import しない
（marketdata の循環依存禁止・設計 §4）。

時刻は解像度非依存。pandas 3 系では分/時は ``"5min"/"1h"``、週は取引週末（金曜ラベル ``W-FRI``）、
月末は ``"ME"``（旧 ``"M"`` は廃止）。``"1m"`` は無変換（``None``＝原子そのもの）。
"""

from __future__ import annotations

from typing import Any, NamedTuple

import pandas as pd

# candles の必須 OHLC 列（小文字正規化後）。
_OHLC_COLUMNS = ("open", "high", "low", "close")


class TfDescriptor(NamedTuple):
    """時間足コードの派生属性を集約した単一台帳エントリ（ISSUE-134 OCP）。

    - ``rule``: pandas resample ルール（``"5min"/"W-FRI"/"ME"`` …・``"1m"`` は None＝無変換）。
    - ``floorable``: 単純 floor で期間始端を表せるか（日中足・1D=True / 1W・1M=False）。
    - ``calendar``: セッション日（ブローカー暦日）で集計する上位 tf か（1D/1W/1M=True）。

    従来 :data:`TIMEFRAME_RULES`（rule のみ）・:data:`SESSION_TFS`・``tf_meta.NON_FLOORABLE_TF``・
    ``period_label_naive`` の tf 判定が各所で個別に列挙していた派生属性を本台帳へ集約し、membership
    set を導出値化する（新カレンダー足追加時は本台帳 1 箇所の追記で全派生値が更新される）。
    """

    rule: str | None
    floorable: bool
    calendar: bool


# 時間足コード → 派生属性台帳（§チャート表示時間選択・1 分足原子）。**唯一の規則源**。
# 全時間足は 1 分足（原子）を resample して生成する。"1m" は無変換（rule=None＝原子そのもの）。
# pandas 3 系では分/時は "5min"/"1h"、週は取引週末（金曜ラベル）、月末は "ME"（旧 "M" は廃止）。
# ここに無いキーはすべて拒否する（is_known_timeframe）。日足ベース dataset（sample/jp225）でも
# "1D"/"1W"/"1M" は冪等に機能する（同日 1 本の再集計は値不変）。日足未満は日足 dataset には無効
# （フロントが dataset 別に提示足を制限する）。挿入順は順序依存の消費者（build_tick_rollup 等）が
# あるため保存する。
TF_DESCRIPTORS: "dict[str, TfDescriptor]" = {
    "1m": TfDescriptor(None, True, False),
    "5m": TfDescriptor("5min", True, False),
    "15m": TfDescriptor("15min", True, False),
    "30m": TfDescriptor("30min", True, False),
    "1h": TfDescriptor("1h", True, False),
    "4h": TfDescriptor("4h", True, False),
    "1D": TfDescriptor("1D", True, True),
    "1W": TfDescriptor("W-FRI", False, True),
    "1M": TfDescriptor("ME", False, True),
}

# 時間足コード → pandas resample ルール（台帳からの互換ビュー・dict[str, str|None]）。
# 既存の外部消費者（``TIMEFRAME_RULES[tf]`` / ``set(TIMEFRAME_RULES)`` / dict 等価比較 / 挿入順反復）を
# 非破壊にするため名称・型・内容・順序を温存し、値のみ台帳 rule から導出する。
TIMEFRAME_RULES: dict[str, str | None] = {
    code: d.rule for code, d in TF_DESCRIPTORS.items()
}

# OHLC 集約規則（再集計時の列別 agg）。volume は合算、その他（OHLC 外）は最終値。
_OHLC_AGG = {"open": "first", "high": "max", "low": "min", "close": "last"}
_VOLUME_NAMES = ("volume", "vol")


def is_known_timeframe(timeframe: Any) -> bool:
    """timeframe がホワイトリスト（1m..1M）に存在するか（未知は False）。"""
    return timeframe in TIMEFRAME_RULES


# セッション日（NY17:00 ET 基準・ISSUE-078）で集計する上位 tf。日中足（5m..4h）は UTC floor 不変。
# 台帳 :data:`TF_DESCRIPTORS` の calendar フラグからの導出値（内容・順序を温存）。
SESSION_TFS = tuple(code for code, d in TF_DESCRIPTORS.items() if d.calendar)

# 暦ラベル tf（単純 floor 不可のカレンダー tf＝W-FRI/ME ラベル規約）。period_label_naive が扱う集合。
# 台帳の calendar かつ非 floorable からの導出値（= {"1W", "1M"}）。
CALENDAR_LABEL_TFS = frozenset(
    code for code, d in TF_DESCRIPTORS.items() if d.calendar and not d.floorable
)
_NY_TZ = "America/New_York"
_BROKER_SHIFT = pd.Timedelta(hours=7)  # ブローカー時間 = NY + 7h（NY17:00 → 00:00）。


def _to_broker_naive_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """naive UTC index → naive ブローカー時間 index（DST は IANA tz へ委譲・自前カレンダー禁止）。"""
    return idx.tz_localize("UTC").tz_convert(_NY_TZ).tz_localize(None) + _BROKER_SHIFT


def resample_ohlc_session(df: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    """1D/1W/1M をセッション日（ブローカー暦日）で集計する :func:`resample_ohlc` 変種（ISSUE-078）。

    index（naive UTC）をブローカー時間へ写像して resample する。返す index ラベルは naive の
    ブローカー暦日で、意味は「そのセッション日ラベルの UTC 深夜 epoch」（チャートの日付軸・
    既存 loader の date 列と整合する表示規約＝marketdata.session_day.session_bar_time と同値）。
    集約規則（OHLC/volume/dropna）は resample_ohlc へ委譲する（規則の二重定義なし）。
    """
    if rule is None:
        return df
    shifted = df.copy()
    shifted.index = _to_broker_naive_index(df.index)
    return resample_ohlc(shifted, rule)


def resample_ohlc_tf(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """tf 名で resample する単一入口（ISSUE-078）: 1D/1W/1M はセッション集計・日中足は UTC floor。

    rollup（stream/increment）と全件再集計の両方が本関数を使うことで、セッション日規則の
    二重定義を防ぐ（旧: 呼び出し側が TIMEFRAME_RULES→resample_ohlc を直接組み合わせ）。
    """
    rule = TIMEFRAME_RULES[tf]
    if tf in SESSION_TFS:
        return resample_ohlc_session(df, rule)
    return resample_ohlc(df, rule)


def period_utc_start(tf: str, label: pd.Timestamp) -> pd.Timestamp:
    """period ラベル → その期間の **UTC 始端**（naive UTC Timestamp）を返す（ISSUE-078）。

    日中足はラベル＝始端（UTC floor）。セッション tf はラベル（ブローカー暦日）から期間先頭の
    ブローカー日を求め、その日のセッション始端（NY 前日 17:00）へ写像する:
      1D: ラベル日そのもの / 1W(W-FRI): ラベル金曜の 6 日前（週= [土..金] ブローカー日）/
      1M(ME): ラベル月の 1 日。rollup の probe 被覆判定（「現周期の始端を probe が含むか」）に使う。
    """
    label = pd.Timestamp(label)
    if tf not in SESSION_TFS:
        return label
    if tf == "1D":
        first = label
    elif tf == "1W":
        first = label - pd.Timedelta(days=6)
    else:  # 1M
        first = label.replace(day=1)
    # ブローカー日 first のセッション始端 = NY ローカル（first - 1 日）17:00。
    ny_naive = first - _BROKER_SHIFT
    return ny_naive.tz_localize(_NY_TZ).tz_convert("UTC").tz_localize(None)


def period_label_naive(tf: str, ts: "pd.Timestamp") -> "pd.Timestamp":
    """naive Timestamp ``ts`` が属する tf バケットの右端ラベル（1W=金曜 / 1M=暦月末）を返す。

    規則源は :data:`TIMEFRAME_RULES` の ``1W='W-FRI'`` / ``1M='ME'``。pandas offset の
    ``rollforward`` で「``ts`` 以降の最初の期間右端」を求める（W-FRI: ``ts`` が金曜ならその日、
    さもなくば次の金曜 / ME: その月の暦月末）。:func:`marketdata.session_day.session_period_label`
    はブローカー暦日を naive 化して本関数へ委譲し、週/月ラベル規則の二重表現（手書き暦算術）を
    解消する（ISSUE-094 🟡-10a）。``tf`` は ``'1W'|'1M'`` のみ（他は ValueError）。
    """
    if tf not in CALENDAR_LABEL_TFS:
        raise ValueError(f"period_label_naive: 1W|1M のみ対応: {tf!r}")
    offset = pd.tseries.frequencies.to_offset(TIMEFRAME_RULES[tf])
    return offset.rollforward(pd.Timestamp(ts))


def resample_ohlc(df: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    """DataFrame を指定 pandas rule で OHLC 再集計する（§チャート表示時間選択・1 分足原子）。

    ``rule=None`` は無変換で同一 DataFrame を返す（原子＝1 分足そのもの）。それ以外は
    resample し、open=最初/high=最大/low=最小/close=最終、volume=合算、その他列=最終値で
    集約する。取引の無い期間（OHLC が NaN の行）は除去する（resample は連続区間を埋めるため、
    休場区間の空行を落とす）。
    """
    if rule is None:
        return df
    agg: dict[Any, str] = {}
    for col in df.columns:
        lc = str(col).lower()
        if lc in _OHLC_AGG:
            agg[col] = _OHLC_AGG[lc]
        elif lc in _VOLUME_NAMES:
            agg[col] = "sum"
        else:
            agg[col] = "last"
    resampled = df.resample(rule).agg(agg)
    lower_map = {str(c).lower(): c for c in df.columns}
    ohlc_cols = [lower_map[k] for k in _OHLC_COLUMNS if k in lower_map]
    return resampled.dropna(subset=ohlc_cols)
