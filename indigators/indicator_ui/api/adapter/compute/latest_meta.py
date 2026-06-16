"""LatestMeta（Latest 増分計算フレームワーク・Stage A 基盤）— archetype メタ解決。

Latest は /compute 境界で「入力 df を min_window で tail → 既存 adapter.compute を不変
呼び出し → 応答 series の line/histogram data を末尾 K 点に切る」操作を行う。各指標が
どの archetype（再帰 / 窓 / look-ahead / 価格軸分布）に属し、tail 窓と末尾切り点数を
どう取るかを本モジュールが単一定義する（後続 21 指標が従う基盤）。

archetype:
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

from dataclasses import dataclass


@dataclass(frozen=True)
class LatestMeta:
    """1 指標(+variant+params) の Latest 計算メタ。

    Attributes:
        archetype: recurrence / window / lookahead / axis_distribution のいずれか。
        min_window: tail 本数。None は full（tail せず全件で adapter.compute）。
        trailing_k: 末尾切り点数。None は切らない（axis_distribution＝全件）。
    """

    archetype: str
    min_window: int | None
    trailing_k: int | None


# ma_type → archetype の分類（reference 登録）。
#   sma / lwma は窓系（理論上は窓 length 確定）だが core がスライド和の再帰のため full
#   フォールバックを既定にする（min_window は latest_meta で None を返す）。
_MA_WINDOW_TYPES = {"sma", "lwma"}
_MA_RECURRENCE_TYPES = {"ema", "smma"}


def latest_meta(compute_id: str, variant: str, params: dict) -> LatestMeta:
    """compute_id(+variant+params) から LatestMeta を解決する。

    未登録は安全既定 LatestMeta("recurrence", None, 1)（full＋K=1）。
    """
    if compute_id == "moving_averages":
        ma_type = str(params.get("ma_type", "ema")).lower()
        if ma_type in _MA_WINDOW_TYPES:
            # 窓系。spec の分岐に従い full フォールバック（min_window=None）を既定とする
            #   （2*length は core のスライド和再帰により float 完全一致しないため）。
            return LatestMeta("window", None, 1)
        # ema / smma ほかは再帰（先頭シード必須）→ full。
        return LatestMeta("recurrence", None, 1)
    if compute_id == "price_range_power":
        # 価格軸分布（非時系列）。末尾K切りしない（全件）。
        return LatestMeta("axis_distribution", None, None)
    # 安全既定（未登録）= full＋K=1（必ず full と一致）。
    return LatestMeta("recurrence", None, 1)
