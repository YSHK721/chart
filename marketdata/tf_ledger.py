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

from typing import NamedTuple


class TfDescriptor(NamedTuple):
    """時間足コードの派生属性を集約した単一台帳エントリ（ISSUE-134 OCP）。

    - ``rule``: pandas resample ルール（``"5min"/"W-FRI"/"ME"`` …・``"1m"`` は None＝無変換）。
      値は文字列であり本モジュールは pandas を要求しない（解釈するのは resample 側の責務）。
    - ``floorable``: 単純 floor で期間始端を表せるか（日中足・1D=True / 1W・1M=False）。
    - ``calendar``: セッション日（ブローカー暦日）で集計する上位 tf か（1D/1W/1M=True）。
    - ``bar_sec``: バー秒長の**名目値**（1W=7日・1M=30日）。窓幅・表示計算用であり、厳密な期間
      境界は resample/session_day のラベル規約が担う（本値を境界計算に使わない）。
    """

    rule: "str | None"
    floorable: bool
    calendar: bool
    bar_sec: int


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
    "1D": TfDescriptor("1D", True, True, 86400),
    "1W": TfDescriptor("W-FRI", False, True, 604800),
    "1M": TfDescriptor("ME", False, True, 2592000),
}

# 時間足 → バー秒長（名目値）。台帳からの導出値（唯一源）。純層（stdlib のみを許す層）が
# 期間長を必要とするときは本表を参照する（手書き dict を作らない）。
TF_BAR_SEC: "dict[str, int]" = {
    code: d.bar_sec for code, d in TF_DESCRIPTORS.items()
}
