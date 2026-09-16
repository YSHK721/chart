"""resample — OHLC 再集計の唯一の規則源（enabler③・dataset から物理移設）。

時間足コード（``"5m"/"1h"/"1D"`` …）→ pandas resample ルールの写像（:data:`TIMEFRAME_RULES`）と、
DataFrame を当該ルールで OHLC 再集計する :func:`resample_ohlc` を提供する。これは indicator_ui の
``dataset.resample_ohlc`` から物理移設した「唯一の規則源」であり、rollup（:mod:`marketdata.rollup`）と
indicator_ui ``dataset``（薄い再エクスポート）が共通して再利用する（再実装を禁ずる）。

依存方向（厳守）: 本モジュールは **pandas / marketdata.csv_schema / marketdata.tf_ledger のみ** に
依存し、indicator_ui を逆 import しない（marketdata の循環依存禁止・設計 §4）。``csv_schema`` は
依存ゼロのモジュールで、**列別集約規則（値列台帳）の唯一源**＝ここへ列名も集約名も書き写さない
ために参照する。``tf_ledger`` も依存ゼロの定数モジュールで、時間足台帳（``TfDescriptor`` /
``TF_DESCRIPTORS``）の唯一源である。台帳を本モジュールから外へ出したのは、pandas を import できない
純層（``simulator.usecase.contact_scan``）が台帳を参照できず時間足→秒長の手書き複製を持たざるを
得なかったため（ISSUE-261）。本モジュールは台帳を**再輸出**するだけで値を持たない。

この宣言は ``marketdata/tests/test_module_dependency_declarations.py`` が **AST 走査で強制**する
（関数内の遅延 import も対象）。かつて本 docstring は「pandas のみ」と述べていたが実際は
``csv_schema`` を import しており、宣言だけが事実と食い違ったまま残っていた（ISSUE-262）。
依存を増やすときは本 docstring と当該テストの許可表を**同時に**更新する。

stdlib（``datetime`` / ``zoneinfo``）も使う（ISSUE-502 D-14）: ブローカー時間座標の
**スカラ面**（:func:`to_broker_time` / :func:`from_broker_naive_unix`）を本モジュールが持つ
ためである。1 点ずつ問う経路（:mod:`marketdata.session_day`）に index は組めず、そこへ pandas を通すと
呼び出しあたりの費用が桁で増える。面は 2 つでも、定数（:data:`BROKER_TZ_NAME` /
:data:`BROKER_SHIFT_HOURS`）と規則は 1 つに保つ。

時刻は解像度非依存。pandas 3 系では分/時は ``"5min"/"1h"``、週は取引週末（金曜ラベル ``W-FRI``）、
月末は ``"ME"``（旧 ``"M"`` は廃止）。``"1m"`` は無変換（``None``＝原子そのもの）。
"""

from __future__ import annotations

from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from typing import Any
from zoneinfo import ZoneInfo as _ZoneInfo

import pandas as pd

from marketdata import csv_schema as _csv_schema
from marketdata import tf_ledger as _tf_ledger

# candles の必須 OHLC 列（小文字正規化後）。
_OHLC_COLUMNS = ("open", "high", "low", "close")


# 台帳（TfDescriptor / TF_DESCRIPTORS）は :mod:`marketdata.tf_ledger`（依存ゼロ）が唯一源。
# 本モジュールは **再輸出**するだけで値を持たない（ISSUE-261: pandas を使えない純層も同じ台帳から
# 導出できるようにするための分離）。従来 `from marketdata.resample import TF_DESCRIPTORS` を書いて
# いた消費者は無改変で動く（名前・型・内容・挿入順とも不変）。
TfDescriptor = _tf_ledger.TfDescriptor
TF_DESCRIPTORS: "dict[str, TfDescriptor]" = _tf_ledger.TF_DESCRIPTORS

# 時間足コード → pandas resample ルール（台帳からの互換ビュー・dict[str, str|None]）。
# 既存の外部消費者（``TIMEFRAME_RULES[tf]`` / ``set(TIMEFRAME_RULES)`` / dict 等価比較 / 挿入順反復）を
# 非破壊にするため名称・型・内容・順序を温存し、値のみ台帳 rule から導出する。
TIMEFRAME_RULES: dict[str, str | None] = {
    code: d.rule for code, d in TF_DESCRIPTORS.items()
}

# 列別集約規則（再集計時の agg）の唯一源は csv_schema の値列台帳である。
#   かつてここに OHLC の集約規則を辞書リテラルで持っていたが、同じ規則が rollup 側にも
#   手書きされており、列が 1 つ増えたとき片方だけが取り残されても出力は正しげなまま残った。
#   台帳に無い列は従来どおり "last"（最終値）で集約される（既存挙動不変）。


def is_known_timeframe(timeframe: Any) -> bool:
    """timeframe がホワイトリスト（1m..1M）に存在するか（未知は False）。"""
    return timeframe in TIMEFRAME_RULES


# セッション日（NY17:00 ET 基準・ISSUE-078）で集計する上位 tf。日中足（5m..4h）は UTC floor 不変。
# 台帳 :data:`TF_DESCRIPTORS` の calendar フラグからの導出値（内容・順序を温存）。
SESSION_TFS = tuple(code for code, d in TF_DESCRIPTORS.items() if d.calendar)

# 暦ラベル tf（単純 floor 不可のカレンダー tf＝W-FRI/ME ラベル規約）。period_label_naive が扱う集合。
# 台帳 :data:`marketdata.tf_ledger.CALENDAR_LABEL_CODES`（calendar かつ非 floorable からの導出値
# = {"1W", "1M"}）の**再輸出**。名前・型（frozenset）・内容は不変で、値はここに持たない
# （同じ導出式を 2 つ書けば台帳の第 2 定義になる）。
CALENDAR_LABEL_TFS = _tf_ledger.CALENDAR_LABEL_CODES

# セッション日起点の等間隔グリッドで切る日中足（ISSUE-489・4h）。台帳の再輸出（値を持たない）。
SESSION_ANCHORED_TFS = _tf_ledger.SESSION_ANCHORED_CODES

# --------------------------------------------------------------------------- #
# ブローカー時間座標（NY + 7h）の唯一源（ISSUE-502 D-14・SOLID 精査 2026-09-06）
#
# 座標系を決めるのは 2 つの定数（基準 tz 名・シフト時間数）だけで、規則は 1 つである:
#     ブローカー壁時計 = 基準 tz で読んだ壁時計 + シフト時間数
# 以下の 4 関数は、この 1 規則の**面**（ベクトル / スカラ × 順 / 逆）にすぎない。面が 2 つ
# 要るのは実行時の都合（index 全体を一括変換する pandas 面と、1 点を扱う stdlib 面）であって、
# 規則が 2 つあるからではない。
#
# なぜここが唯一源か: かつて同じ 2 定数と同じ式が :mod:`marketdata.session_day` にも書かれて
# いた（tz 名と 7h が 2 箇所）。片方だけ動かせば日足・週足・月足の境界と session_day_start が
# 静かに 1 時間ずれ、出力はどちらも「それらしい」ので値を見ても気付けない。第 2 定義の再出現は
# ``marketdata/tests/test_broker_time_single_source.py`` が AST 走査で落とす。
# --------------------------------------------------------------------------- #

#: ブローカー時間の基準 tz（IANA 名）。DST の切替は tzdata へ委譲する（自前カレンダー禁止＝
#: 制度変更・歴史的切替日も tzdata が単一真実源）。**綴りはここにしか無い。**
BROKER_TZ_NAME = "America/New_York"

#: 基準 tz の壁時計へ加えるシフト時間数（NY 17:00 → 00:00）。**数値はここにしか無い。**
BROKER_SHIFT_HOURS = 7

_NY_TZ = BROKER_TZ_NAME                                     # pandas 面が受け取る tz 名
_BROKER_SHIFT = pd.Timedelta(hours=BROKER_SHIFT_HOURS)      # pandas 面のシフト
_NY_ZONE = _ZoneInfo(BROKER_TZ_NAME)                        # stdlib 面の tz
_BROKER_SHIFT_TD = _timedelta(hours=BROKER_SHIFT_HOURS)     # stdlib 面のシフト


def to_broker_naive_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """naive UTC index → naive ブローカー時間 index（DST は IANA tz へ委譲・自前カレンダー禁止）。

    ISSUE-479 M-4: セッション日への index 写像はセッション集計の規則そのものであり、外部
    （検証スクリプト）が同じ写像を必要とする。式を写させないため公開名を与えた。
    """
    return idx.tz_localize("UTC").tz_convert(_NY_TZ).tz_localize(None) + _BROKER_SHIFT


#: 旧 private 名（**同一オブジェクト**）。既存参照を 1 箇所も変えないために温存する。
_to_broker_naive_index = to_broker_naive_index


def to_broker_time(t: "int | float") -> "_datetime":
    """UNIX 秒 → ブローカー時間（:func:`to_broker_naive_index` の**スカラ面**・同一規則）。

    1 点ずつ問う経路（:mod:`marketdata.session_day` の日切り・ラベル）は index を組めないため
    stdlib で同じ規則を適用する。ベクトル面との一致は
    ``marketdata/tests/test_broker_time_single_source.py`` が 20,034 点（DST 切替を 32 回跨ぐ
    2012〜2027 の 7 時間刻み）で固定する。

    返り値の注意（ISSUE-502 D-14 で移設した際の形をそのまま保つ）: ``tzinfo`` は基準 tz の
    ままだが壁時計は +7h 済みであり、**両者は対応しない**。意味を持つのは壁時計（暦日・
    時分秒）だけで、この値から ``.timestamp()`` を取ってはならない。逆写像は
    :func:`from_broker_naive_unix`（naive を受ける）である。
    """
    return _datetime.fromtimestamp(float(t), tz=_NY_ZONE) + _BROKER_SHIFT_TD


def from_broker_naive_unix(broker_naive: "_datetime") -> int:
    """ブローカー壁時計（naive datetime）→ UNIX 秒（:func:`from_broker_naive_index` のスカラ面）。

    秋 DST の重複時刻は ``fold=0``（夏側）で決定的に解決する＝ベクトル面の ``ambiguous=True``
    と同値。ブローカー真夜中は NY 17:00 であり切替時刻（NY 02:00）と重ならないため、
    セッション始端の経路では曖昧・不存在は生じない（決定性のための明示）。
    """
    ny_naive = broker_naive - _BROKER_SHIFT_TD
    return int(ny_naive.replace(tzinfo=_NY_ZONE, fold=0).timestamp())


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


def resample_ohlc_anchored(df: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    """日中足を**セッション日起点**の等間隔グリッドで集計する変種（ISSUE-489・依頼者承認 2026-09-05）。

    UTC 床（:func:`resample_ohlc`）だと、取引日の長さ（実測 22〜23 時間）が刻みで割り切れない
    tf（4h）は毎営業日 1 本、休場を内包し 2 セッションを混在させるバーを作る（2019 年以降の
    実測: 混在 1,525 本・オーバーナイトギャップ 1,525 回をローソク内部に隠蔽）。ブローカー
    時間（セッション日境界＝休場帯の中）を座標系にして floor すれば、グリッドが常に取引日の
    頭から始まり混在は 0 本になる（同実測）。

    実装は :func:`resample_ohlc_session` と同型: index をブローカー時間へ写像 → その座標系で
    floor 集計（ブローカー日 00:00 起点＝セッション日起点）→ ラベルを UTC へ戻す。ラベルは
    他の日中足と同じ**期間始端**（naive UTC）。集約規則は :func:`resample_ohlc` へ委譲する。

    DST 切替との干渉について: 米 DST の切替（NY 02:00）は常に週末休場中であり、ラベル逆写像の
    曖昧時刻（NY 01:00・秋の重複）に実バーは生じない（生じるならその期間に取引が要る）。
    ``ambiguous=True``（夏側）は決定性のための明示であって経路としては使われない。
    """
    if rule is None:
        return df
    shifted = df.copy()
    shifted.index = to_broker_naive_index(df.index)
    out = resample_ohlc(shifted, rule)
    out.index = from_broker_naive_index(out.index)
    out.index.name = df.index.name
    return out


def from_broker_naive_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """:func:`to_broker_naive_index` の逆写像（naive ブローカー時間 → naive UTC）。

    anchored 集計のラベルを UTC へ戻す唯一の式（ISSUE-489）。外部（pv 等の付加列を同じ
    グリッドで合算する消費者）にも同じ式が要るため公開する（写しを作らせない）。
    秋 DST の曖昧時刻は夏側（``ambiguous=True``）で決定的に解決する——切替は常に週末休場中で
    あり、実バーの経路としては使われない。
    """
    ny_naive = idx - _BROKER_SHIFT
    return (
        ny_naive.tz_localize(_NY_TZ, ambiguous=True, nonexistent="shift_forward")
        .tz_convert("UTC").tz_localize(None)
    )


def resample_ohlc_tf(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """tf 名で resample する単一入口（ISSUE-078）: 1D/1W/1M はセッション集計・4h はセッション日
    起点グリッド（ISSUE-489）・他の日中足は UTC floor。

    rollup（stream/increment）と全件再集計の両方が本関数を使うことで、セッション日規則の
    二重定義を防ぐ（旧: 呼び出し側が TIMEFRAME_RULES→resample_ohlc を直接組み合わせ）。
    """
    rule = TIMEFRAME_RULES[tf]
    if tf in SESSION_TFS:
        return resample_ohlc_session(df, rule)
    if tf in SESSION_ANCHORED_TFS:
        return resample_ohlc_anchored(df, rule)
    return resample_ohlc(df, rule)


def period_utc_start(tf: str, label: pd.Timestamp) -> pd.Timestamp:
    """period ラベル → その期間の **UTC 始端**（naive UTC Timestamp）を返す（ISSUE-078）。

    日中足はラベル＝始端（UTC floor）。セッション tf はラベル（ブローカー暦日）から期間先頭の
    ブローカー日を求め、その日のセッション始端（NY 前日 17:00）へ写像する:
      1D: ラベル日そのもの / 1W(W-FRI): ラベル金曜の 6 日前（週= [土..金] ブローカー日）/
      1M(ME): ラベル月の 1 日。rollup の probe 被覆判定（「現周期の始端を probe が含むか」）に使う。

    期間先頭ブローカー日の暦算術は台帳（:func:`marketdata.tf_ledger.period_first_ymd`）が持つ
    （ISSUE-479 M-3）。ここに tf 別のリテラル分岐を書かない＝時間足の追加は台帳 1 行で完結する。
    """
    label = pd.Timestamp(label)
    if tf not in SESSION_TFS:
        return label
    first_day = _tf_ledger.period_first_ymd(tf, label.year, label.month, label.day)
    # 日付だけを差し替える（``pd.Timestamp(first_day)`` は時刻成分を 00:00 に落としてしまう）。
    first = label.replace(year=first_day.year, month=first_day.month, day=first_day.day)
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
    resample し、列ごとの集約規則は :data:`marketdata.csv_schema.VALUE_COLUMN_LEDGER`（値列台帳）
    が決める（規則をここへ書き写さない）。台帳に無い列は従来どおり最終値で集約する。取引の無い
    期間（OHLC が NaN の行）は除去する（resample は連続区間を埋めるため、休場区間の空行を落とす）。
    """
    if rule is None:
        return df
    # 列別集約は台帳へ 1 列につき 1 回だけ問う（行数に依存しない）。
    agg: dict[Any, str] = {col: _csv_schema.agg_for(col) for col in df.columns}
    resampled = df.resample(rule).agg(agg)
    lower_map = {str(c).lower(): c for c in df.columns}
    ohlc_cols = [lower_map[k] for k in _OHLC_COLUMNS if k in lower_map]
    return resampled.dropna(subset=ohlc_cols)
