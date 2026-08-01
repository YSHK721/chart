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

from dataclasses import dataclass

from adapter.compute.call_binding import latest_meta_fields


@dataclass(frozen=True)
class LatestMeta:
    """1 指標(+variant+params) の Latest 計算メタ。

    Attributes:
        archetype: incremental / recurrence / window / lookahead / axis_distribution のいずれか。
        min_window: tail 本数。None は full（tail せず全件で adapter.compute）。
        trailing_k: 末尾切り点数。None は切らない（axis_distribution＝全件）。
        incremental: 増分状態器の名前（``adapter.compute.incremental`` のレジストリ名）。
            None は増分計算を行わない（＝従来の full 切り出し経路）。増分器が当該
            (df, params) を扱えない場合も従来経路へ落ちるため、宣言は挙動を変えない
            （OCP: 既存経路は不変・宣言した指標だけが新経路へ乗る）。
    """

    archetype: str
    min_window: int | None
    trailing_k: int | None
    incremental: str | None = None


def latest_meta(compute_id: str, variant: str, params: dict) -> LatestMeta:
    """compute_id(+variant+params) から LatestMeta を解決する。

    ISSUE-097 🟡-6: archetype 分類（archetype / min_window / trailing_k）の宣言は
    ``call_binding._BindingSpec`` の ``latest_meta`` フィールドへ一元化した。本関数は
    per-indicator if 連鎖を持たず、宣言テーブルの解決値を LatestMeta に組み立てる薄い
    アダプタに徹する。未登録 / 未宣言の指標は安全既定 LatestMeta("recurrence", None, 1)
    （full＋K=1＝必ず full と一致）へ落ちる（従来の未登録安全既定と同一挙動）。

    宣言タプルは 3 要素（archetype, min_window, trailing_k）または 4 要素（＋ incremental）。
    3 要素の宣言は incremental=None（従来経路）になる（ISSUE-233・additive）。
    """
    fields = latest_meta_fields(compute_id, variant, params)
    if fields is None:
        # 安全既定（未登録 / 未宣言）= full＋K=1（必ず full と一致）。
        return LatestMeta("recurrence", None, 1)
    return LatestMeta(*fields)
