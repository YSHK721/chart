"""tf_ledger — 時間足コードの派生属性台帳（**唯一の規則源**・依存ゼロ）。

本モジュールは stdlib 以外に一切依存しない（pandas / numpy / marketdata 内の他モジュールを
import しない）。台帳は「どんな時間足があり、それぞれどんな性質を持つか」という純粋なドメイン
データであり、再集計の実装（pandas）とは別の関心事だからである。

なぜ分離したか（ISSUE-261）:
    台帳は元々 :mod:`marketdata.resample` に置かれていたが、同モジュールは pandas を import する。
    そのため「純・stdlib のみ」を宣言する層（例: :mod:`simulator.usecase.contact_scan.bar_window`）
    は台帳を参照できず、**時間足→秒長の手書き複製**を持つしかなかった。写しは台帳へ時間足を
    足しても追随せず、実際に ISSUE-253（ライブの更新粒度が時間足で割れる）と同型の事故を生む。
    台帳を依存ゼロにすることで、pandas を使う層と使わない層が**同じ 1 つの定義**から導出できる。

配置の規約:
    - 値を持つのは本モジュールだけ。他モジュールは**導出**のみを行う（第 2 定義を作らない）。
    - :mod:`marketdata.resample` は本モジュールを再輸出する（``TIMEFRAME_RULES`` 等の従来名・型・
      内容・挿入順は不変＝既存消費者は無改変）。
    - 時間足の追加は本モジュールへ 1 行足すだけで全派生値（rule / floor 可否 / セッション集合 /
      バー秒長 / JS 生成台帳）へ伝播する（OCP）。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, NamedTuple


def _first_day_of_same_broker_day(d: "date") -> "date":
    """1D の期間先頭ブローカー日＝ラベル日そのもの。"""
    return d


def _first_day_of_broker_week(d: "date") -> "date":
    """1W（W-FRI）の期間先頭ブローカー日＝ラベル金曜の 6 日前（週 = [土..金]）。"""
    return d - timedelta(days=6)


def _first_day_of_month(d: "date") -> "date":
    """1M（ME）の期間先頭ブローカー日＝ラベル月の 1 日。"""
    return d.replace(day=1)


class TfDescriptor(NamedTuple):
    """時間足コードの派生属性を集約した単一台帳エントリ（ISSUE-134 OCP）。

    - ``rule``: pandas resample ルール（``"5min"/"W-FRI"/"ME"`` …・``"1m"`` は None＝無変換）。
      値は文字列であり本モジュールは pandas を要求しない（解釈するのは resample 側の責務）。
    - ``floorable``: 単純 floor で期間始端を表せるか（日中足・1D=True / 1W・1M=False）。
    - ``calendar``: セッション日（ブローカー暦日）で集計する上位 tf か（1D/1W/1M=True）。
    - ``bar_sec``: バー秒長の**名目値**（1W=7日・1M=30日）。窓幅・表示計算用であり、厳密な期間
      境界は resample/session_day のラベル規約が担う（本値を境界計算に使わない）。
    - ``period_first``: 期間ラベル日 → その期間の**先頭ブローカー日**を返す暦算術（ISSUE-479 M-3）。
      日中足は期間先頭を単純 floor で表せる（``floorable``）ため None。この 1 属性が無かった間、
      同じ規則（1D=同日 / 1W=6 日前 / 1M=月初）が resample・session_day・tf_meta の 5 箇所へ
      ``tf == "1D"`` 等のリテラル分岐として書き写されていた。暦算術は stdlib ``datetime`` だけで
      表せるため、本モジュールの「依存ゼロ（stdlib のみ）」宣言と両立する。
    """

    rule: "str | None"
    floorable: bool
    calendar: bool
    bar_sec: int
    period_first: "Callable[[date], date] | None" = None


# 時間足コード → 派生属性台帳（§チャート表示時間選択・1 分足原子）。**唯一の規則源**。
# 全時間足は 1 分足（原子）を resample して生成する。"1m" は無変換（rule=None＝原子そのもの）。
# pandas 3 系では分/時は "5min"/"1h"、週は取引週末（金曜ラベル）、月末は "ME"（旧 "M" は廃止）。
# ここに無いキーはすべて拒否する（resample.is_known_timeframe）。日足ベース dataset（sample/jp225）
# でも "1D"/"1W"/"1M" は冪等に機能する（同日 1 本の再集計は値不変）。日足未満は日足 dataset には
# 無効（フロントが dataset 別に提示足を制限する）。挿入順は順序依存の消費者（build_tick_rollup 等）
# があるため保存する。
TF_DESCRIPTORS: "dict[str, TfDescriptor]" = {
    "1m": TfDescriptor(None, True, False, 60),
    "5m": TfDescriptor("5min", True, False, 300),
    "15m": TfDescriptor("15min", True, False, 900),
    "30m": TfDescriptor("30min", True, False, 1800),
    "1h": TfDescriptor("1h", True, False, 3600),
    "4h": TfDescriptor("4h", True, False, 14400),
    "1D": TfDescriptor("1D", True, True, 86400, period_first=_first_day_of_same_broker_day),
    "1W": TfDescriptor("W-FRI", False, True, 604800, period_first=_first_day_of_broker_week),
    "1M": TfDescriptor("ME", False, True, 2592000, period_first=_first_day_of_month),
}

# 時間足 → バー秒長（名目値）。台帳からの導出値（唯一源）。純層（stdlib のみを許す層）が
# 期間長を必要とするときは本表を参照する（手書き dict を作らない）。
TF_BAR_SEC: "dict[str, int]" = {
    code: d.bar_sec for code, d in TF_DESCRIPTORS.items()
}

# 暦ラベル tf（単純 floor では期間始端を表せないカレンダー tf＝W-FRI/ME ラベル規約）の**唯一源**。
# calendar かつ非 floorable からの導出値（= {"1W", "1M"}）。:data:`marketdata.resample.CALENDAR_LABEL_TFS`
# は本値の再輸出であり（名前・型・内容とも不変）、値を持たない。
CALENDAR_LABEL_CODES: "frozenset[str]" = frozenset(
    code for code, d in TF_DESCRIPTORS.items() if d.calendar and not d.floorable
)


def period_first_ymd(tf: str, y: int, m: int, d: int) -> "date":
    """ブローカー暦日 ``(y, m, d)`` を期間ラベルとみなし、その期間の**先頭ブローカー日**を返す。

    規則は台帳 :data:`TF_DESCRIPTORS` の ``period_first`` が持つ（1D=同日 / 1W=ラベル金曜の 6 日前 /
    1M=ラベル月の 1 日）。時間足を足すときは台帳へ 1 行足すだけで、期間始端を必要とする全経路
    （resample の period_utc_start / session_day の period_session_labels）が追随する。
    本モジュールは依存ゼロであり、それらを import しない（呼ぶのは向こう側である）。

    台帳引きは呼び出しあたり 1 回だけ発行する（全行走査へ退化させない＝計算量テストが固定する）。
    未知の時間足は KeyError（台帳の既存契約）、期間始端規則を持たない時間足は ValueError。
    """
    rule = TF_DESCRIPTORS[tf].period_first
    if rule is None:
        raise ValueError(f"period_first_ymd: 期間始端規則を持たない時間足: {tf!r}")
    return rule(date(y, m, d))
