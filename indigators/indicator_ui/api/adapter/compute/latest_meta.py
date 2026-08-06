"""LatestMeta（Latest 増分計算フレームワーク・Stage A 基盤）— archetype メタ解決。

Latest は /compute 境界で「入力 df を min_window で tail → 既存 adapter.compute を不変
呼び出し → 応答 series の line/histogram data を末尾 K 点に切る」操作を行う。各指標が
どの archetype（再帰 / 窓 / look-ahead / 価格軸分布）に属し、tail 窓と末尾切り点数を
どう取るかを本モジュールが単一定義する（後続 21 指標が従う基盤）。

archetype:
    "incremental"       : 前バーまでの状態を保持し 1 点だけ進める（ISSUE-233・真の増分計算）。
                          所要は依存深度に依らず一定で、full 再計算を行わない。状態器の名前を
                          ``incremental`` フィールドで宣言する（実体は adapter/compute/incremental/）。
    "recurrence"        : EMA/SMMA 等。先頭シードからの再帰のため full 必須（min_window=None）。
    "window"            : SMA/LWMA 等。窓 length 確定だが core 実装はスライド和の再帰のため、
                          df.tail で開始点を変えると末尾値に浮動小数ドリフトが乗る（実測
                          ~1e-15）。spec の分岐「2*length が float 完全一致を満たさなければ
                          full フォールバックを既定にする」に従い min_window=None を既定とする。
    "lookahead"         : 将来情報を含む系（本 Stage では登録なし）。
    "axis_distribution" : price_range_power 等の非時系列（価格軸分布）。末尾K切りせず全件
                          （trailing_k=None）。

安全既定（未登録 compute_id）= LatestMeta("recurrence", None, 1)（full＋K=1＝必ず full と一致）。
"""

from __future__ import annotations

from adapter.compute.call_binding import latest_meta_fields
# 型は中立モジュールが所有する（ISSUE-278 #7）。宣言側 call_binding も同じ型を import するため、
#   宣言が位置タプルでなく LatestMeta を直接返せる＝要素の書き忘れが構築時に落ちる。
#   従来の `from adapter.compute.latest_meta import LatestMeta` を壊さないよう再 export する。
from adapter.compute.latest_meta_spec import LatestMeta

__all__ = ["LatestMeta", "latest_meta"]


def latest_meta(compute_id: str, variant: str, params: dict) -> LatestMeta:
    """compute_id(+variant+params) から LatestMeta を解決する。

    ISSUE-097 🟡-6: archetype 分類（archetype / min_window / trailing_k）の宣言は
    ``call_binding._BindingSpec`` の ``latest_meta`` フィールドへ一元化した。本関数は
    per-indicator if 連鎖を持たず、宣言テーブルの解決値を LatestMeta に組み立てる薄い
    アダプタに徹する。未登録 / 未宣言の指標は安全既定 LatestMeta("recurrence", None, 1)
    （full＋K=1＝必ず full と一致）へ落ちる（従来の未登録安全既定と同一挙動）。

    宣言は LatestMeta を直接返す（ISSUE-278 #7 で位置タプルを廃止）。増分器名の書き忘れが
    「例外なく full へ縮退」する経路を型で塞ぐため。
    """
    meta = latest_meta_fields(compute_id, variant, params)
    if meta is None:
        # 安全既定（未登録 / 未宣言）= full＋K=1（必ず full と一致）。
        return LatestMeta("recurrence", None, 1)
    return meta
