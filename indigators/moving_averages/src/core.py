"""移動平均のコア計算（純粋ロジック・外部 I/O 非依存）。

元 MQL5 標準ライブラリ ``MovingAverages.mqh`` を Python へ移植したもの。
入出力・描画を一切含まない純粋な数値計算層であり、依存は numpy のみ。

提供する関数は 3 系統:

スカラー版（指定位置 1 点の値を返す）
    simple_ma            : 単純移動平均（SMA）
    exponential_ma       : 指数移動平均（EMA）
    smoothed_ma          : 平滑移動平均（SMMA / RMA）
    linear_weighted_ma   : 線形加重移動平均（LWMA）

バッファ版（配列全体を逐次計算し buffer を破壊的に更新、計算本数を返す）
    simple_ma_on_buffer
    exponential_ma_on_buffer
    linear_weighted_ma_on_buffer        : LWMA（classic, スライド和）
    linear_weighted_ma_on_buffer_fast   : LWMA（fast, weight_sum を保持）
    smoothed_ma_on_buffer

狭いラッパ（純粋関数・種別ディスパッチ。ISSUE-182 項目 2）
    ma                   : ``ma(price, ma_type, length) -> ndarray``
    MA_TYPES             : 受理する種別キー（"sma"/"ema"/"smma"/"lwma"）

移植上の注意:
    - MQL の ``ArrayGetAsSeries`` / ``ArraySetAsSeries`` による時系列向き調整は、
      入力配列が昇順（index 0 = 最古、末尾 = 最新）であることを前提として除去した。
      呼び出し側は昇順の配列を渡すこと。
    - ``prev_calculated`` は MQL の差分再計算機構をそのまま再現するための引数。
      全本数を一括計算する場合は 0 を渡す。
    - 元コードの挙動（後述の smoothed_ma のシード上書き等）は意図的に忠実再現する。
"""

from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------------------
# スカラー版（指定位置 1 点の移動平均値を返す）
# ---------------------------------------------------------------------------
def simple_ma(position: int, period: int, price: np.ndarray) -> float:
    """位置 ``position`` における単純移動平均（SMA）を返す。

    Args:
        position: 計算対象のインデックス（``price`` 内）。
        period: 平均本数。
        price: 価格配列（昇順）。``price[position]`` が最新側。

    Returns:
        SMA 値。``period`` が無効（``period<=0`` または
        ``period>position+1``）の場合は 0.0。
    """
    result = 0.0
    # --- 期間チェック
    if period > 0 and period <= (position + 1):
        for i in range(period):
            result += price[position - i]
        result /= period
    return result


def exponential_ma(
    position: int, period: int, prev_value: float, price: np.ndarray
) -> float:
    """位置 ``position`` における指数移動平均（EMA）を返す。

    Args:
        position: 計算対象のインデックス（``price`` 内）。
        period: 平滑期間。
        prev_value: 1 つ前の EMA 値。
        price: 価格配列（昇順）。

    Returns:
        EMA 値。``period<=0`` の場合は 0.0。
    """
    result = 0.0
    # --- 期間チェック
    if period > 0:
        pr = 2.0 / (period + 1.0)
        result = price[position] * pr + prev_value * (1 - pr)
    return result


def smoothed_ma(
    position: int, period: int, prev_value: float, price: np.ndarray
) -> float:
    """位置 ``position`` における平滑移動平均（SMMA / RMA）を返す。

    Args:
        position: 計算対象のインデックス（``price`` 内）。
        period: 平滑期間。
        prev_value: 1 つ前の SMMA 値。
        price: 価格配列（昇順）。

    Returns:
        SMMA 値。``period`` が無効（``period<=0`` または
        ``period>position+1``）の場合は 0.0。

    Note:
        元 MQL5 実装の挙動を忠実再現している。``position==period-1``（シード位置）
        では先に単純平均を計算するが、その値は直後の再帰式
        ``(prev_value*(period-1)+price[position])/period`` で上書きされ、
        実質的に破棄される。この癖をそのまま維持している。
    """
    result = 0.0
    # --- 期間チェック
    if period > 0 and period <= (position + 1):
        if position == period - 1:
            for i in range(period):
                result += price[position - i]
            result /= period
        # 注: 上の単純平均は下式で上書きされる（元コードの挙動を維持）
        result = (prev_value * (period - 1) + price[position]) / period
    return result


def linear_weighted_ma(position: int, period: int, price: np.ndarray) -> float:
    """位置 ``position`` における線形加重移動平均（LWMA）を返す。

    最新の価格ほど大きい重み（1..period）を与える。

    Args:
        position: 計算対象のインデックス（``price`` 内）。
        period: 加重本数。
        price: 価格配列（昇順）。

    Returns:
        LWMA 値。``period`` が無効（``period<=0`` または
        ``period>position+1``）の場合は 0.0。
    """
    result = 0.0
    # --- 期間チェック
    if period > 0 and period <= (position + 1):
        total = 0.0
        wsum = 0
        for i in range(period, 0, -1):
            wsum += i
            total += price[position - i + 1] * (period - i + 1)
        result = total / wsum
    return result


# ---------------------------------------------------------------------------
# バッファ版（配列全体を逐次計算し buffer を破壊的に更新する）
# ---------------------------------------------------------------------------
def simple_ma_on_buffer(
    rates_total: int,
    prev_calculated: int,
    begin: int,
    period: int,
    price: np.ndarray,
    buffer: np.ndarray,
) -> int:
    """価格配列全体に対する単純移動平均を ``buffer`` へ書き込む。

    Args:
        rates_total: 価格データの総本数。
        prev_calculated: 前回計算済み本数（初回または本数変化時は 0）。
        begin: 有効データの開始インデックス。
        period: 平均本数。
        price: 価格配列（昇順, 長さ ``rates_total`` 以上）。
        buffer: 結果を書き込む配列（``price`` と同長, 破壊的更新）。

    Returns:
        計算した総本数（``rates_total``）。``period<=1`` または
        ``period>rates_total-begin`` の場合は 0。
    """
    # --- 期間チェック
    if period <= 1 or period > (rates_total - begin):
        return 0
    # --- 開始位置の算出
    if prev_calculated == 0:  # 初回計算 または 本数変化
        start_position = period + begin
        for i in range(start_position - 1):
            buffer[i] = 0.0
        # --- 最初の可視値を計算
        first_value = 0.0
        for i in range(begin, start_position):
            first_value += price[i]
        buffer[start_position - 1] = first_value / period
    else:
        start_position = prev_calculated - 1
    # --- メインループ
    for i in range(start_position, rates_total):
        buffer[i] = buffer[i - 1] + (price[i] - price[i - period]) / period
    return rates_total


def exponential_ma_on_buffer(
    rates_total: int,
    prev_calculated: int,
    begin: int,
    period: int,
    price: np.ndarray,
    buffer: np.ndarray,
) -> int:
    """価格配列全体に対する指数移動平均を ``buffer`` へ書き込む。

    Args:
        rates_total: 価格データの総本数。
        prev_calculated: 前回計算済み本数（初回または本数変化時は 0）。
        begin: 有効データの開始インデックス。
        period: 平滑期間。
        price: 価格配列（昇順）。
        buffer: 結果を書き込む配列（破壊的更新）。

    Returns:
        計算した総本数（``rates_total``）。条件不成立時は 0。
    """
    # --- 期間チェック
    if period <= 1 or period > (rates_total - begin):
        return 0
    smooth_factor = 2.0 / (1.0 + period)
    # --- 開始位置の算出
    if prev_calculated == 0:  # 初回計算 または 本数変化
        for i in range(begin):
            buffer[i] = 0.0
        start_position = period + begin
        buffer[begin] = price[begin]
        for i in range(begin + 1, start_position):
            buffer[i] = price[i] * smooth_factor + buffer[i - 1] * (1.0 - smooth_factor)
    else:
        start_position = prev_calculated - 1
    # --- メインループ
    for i in range(start_position, rates_total):
        buffer[i] = price[i] * smooth_factor + buffer[i - 1] * (1.0 - smooth_factor)
    return rates_total


def linear_weighted_ma_on_buffer(
    rates_total: int,
    prev_calculated: int,
    begin: int,
    period: int,
    price: np.ndarray,
    buffer: np.ndarray,
) -> int:
    """価格配列全体に対する線形加重移動平均（classic）を ``buffer`` へ書き込む。

    スライド和（``sum`` / ``lsum``）で逐次更新する古典実装。

    Args:
        rates_total: 価格データの総本数。
        prev_calculated: 前回計算済み本数。
        begin: 有効データの開始インデックス。
        period: 加重本数。
        price: 価格配列（昇順）。
        buffer: 結果を書き込む配列（破壊的更新）。

    Returns:
        計算した総本数（``rates_total``）。条件不成立時は 0。
    """
    # --- 期間チェック
    if period <= 1 or period > (rates_total - begin):
        return 0
    # --- 開始位置の算出
    if prev_calculated <= period + begin + 2:  # 初回計算 または 本数変化
        start_position = period + begin
        for i in range(start_position):
            buffer[i] = 0.0
    else:
        start_position = prev_calculated - 2
    # --- 最初の可視値を計算
    total = 0.0
    lsum = 0.0
    weight = 0
    weight_idx = 1
    for i in range(start_position - period, start_position):
        total += price[i] * weight_idx
        lsum += price[i]
        weight += weight_idx
        weight_idx += 1
    buffer[start_position - 1] = total / weight
    # --- メインループ
    for i in range(start_position, rates_total):
        total = total - lsum + price[i] * period
        lsum = lsum - price[i - period] + price[i]
        buffer[i] = total / weight
    return rates_total


def linear_weighted_ma_on_buffer_fast(
    rates_total: int,
    prev_calculated: int,
    begin: int,
    period: int,
    price: np.ndarray,
    buffer: np.ndarray,
    weight_sum: int = 0,
) -> tuple[int, int]:
    """価格配列全体に対する線形加重移動平均（fast）を ``buffer`` へ書き込む。

    元 MQL5 では ``LinearWeightedMAOnBuffer`` のオーバーロード（``weight_sum`` を
    参照渡しで保持する版）。Python ではオーバーロード不可のため別名・別関数とし、
    ``weight_sum`` は引数で受け取り戻り値でも返す。

    Args:
        rates_total: 価格データの総本数。
        prev_calculated: 前回計算済み本数（初回または本数変化時は 0）。
        begin: 有効データの開始インデックス。
        period: 加重本数。
        price: 価格配列（昇順）。
        buffer: 結果を書き込む配列（破壊的更新）。
        weight_sum: 重み合計のキャッシュ（差分計算時に前回値を渡す）。

    Returns:
        ``(計算した総本数, 更新後の weight_sum)``。条件不成立時は
        ``(0, weight_sum)``。
    """
    # --- 期間チェック
    if period <= 1 or period > (rates_total - begin):
        return 0, weight_sum
    # --- 開始位置の算出
    if prev_calculated == 0:  # 初回計算 または 本数変化
        start_position = period + begin
        for i in range(start_position):
            buffer[i] = 0.0
        # --- 最初の可視値を計算
        first_value = 0.0
        wsum = 0
        k = 1
        for i in range(begin, start_position):
            first_value += k * price[i]
            wsum += k
            k += 1
        buffer[start_position - 1] = first_value / wsum
        weight_sum = wsum
    else:
        start_position = prev_calculated - 1
    # --- メインループ
    for i in range(start_position, rates_total):
        total = 0.0
        for j in range(period):
            total += (period - j) * price[i - j]
        buffer[i] = total / weight_sum
    return rates_total, weight_sum


def smoothed_ma_on_buffer(
    rates_total: int,
    prev_calculated: int,
    begin: int,
    period: int,
    price: np.ndarray,
    buffer: np.ndarray,
) -> int:
    """価格配列全体に対する平滑移動平均（SMMA / RMA）を ``buffer`` へ書き込む。

    Args:
        rates_total: 価格データの総本数。
        prev_calculated: 前回計算済み本数（初回または本数変化時は 0）。
        begin: 有効データの開始インデックス。
        period: 平滑期間。
        price: 価格配列（昇順）。
        buffer: 結果を書き込む配列（破壊的更新）。

    Returns:
        計算した総本数（``rates_total``）。条件不成立時は 0。
    """
    # --- 期間チェック
    if period <= 1 or period > (rates_total - begin):
        return 0
    # --- 開始位置の算出
    if prev_calculated == 0:  # 初回計算 または 本数変化
        start_position = period + begin
        for i in range(start_position - 1):
            buffer[i] = 0.0
        # --- 最初の可視値を計算
        first_value = 0.0
        for i in range(begin, start_position):
            first_value += price[i]
        buffer[start_position - 1] = first_value / period
    else:
        start_position = prev_calculated - 1
    # --- メインループ
    for i in range(start_position, rates_total):
        buffer[i] = (buffer[i - 1] * (period - 1) + price[i]) / period
    return rates_total


# ---------------------------------------------------------------------------
# 狭いラッパ（純粋関数・種別ディスパッチ）
#
# 上のバッファ版は MQL ``MovingAverages.mqh`` の 1:1 移植資産であり、
# ``(rates_total, prev_calculated, begin, period, price, buffer)`` の 6 引数
# out-param 契約を持つ。しかし本番の全呼出は ``prev_calculated=0`` / ``begin=0``
# 固定かつ ``np.zeros(n)`` の事前確保という単一の作法しか使っていない
# （ISSUE-182 の Grep 実測）。そこで、その作法だけを固定した狭い純粋関数面を
# **追加**する（既存 6 引数版は無改変で残置する）。
# ---------------------------------------------------------------------------

# 種別キー → バッファ版関数。種別追加は本表への 1 行追加だけで済む（分岐を書かない）。
# キー集合は既存の種別写像（lwc_chart._MA_FUNCS / ma_marod._MA_FUNCS）と同一。
_MA_ON_BUFFER = {
    "sma": simple_ma_on_buffer,
    "ema": exponential_ma_on_buffer,
    "smma": smoothed_ma_on_buffer,
    "lwma": linear_weighted_ma_on_buffer,
}

# 受理する MA 種別キー（表から導出＝単一情報源）。
MA_TYPES: tuple[str, ...] = tuple(_MA_ON_BUFFER)

# 「最初の有効値」を index=0 から定義する種別（warm-up マスク不要）。他は period-1 までマスク。
# MA 種別ごとの warm-up 規約は本 core（種別の所有者）が単一情報源として持つ
# （ISSUE-179 項目 4: lwc_chart の ``_FROM_ZERO`` はここへの別名になった）。
MA_FROM_ZERO: frozenset[str] = frozenset({"ema"})


def ma(price: np.ndarray, ma_type: str, length: int) -> np.ndarray:
    """移動平均系列を新規配列で返す（バッファ版の狭いラッパ・出力は bit 等価）。

    ``buffer = np.zeros(n); <type>_ma_on_buffer(n, 0, 0, length, price, buffer)``
    と厳密に同一の計算を行い、``buffer`` を返す。呼び出し側は未使用の
    ``prev_calculated`` / ``begin`` と事前確保から解放される。

    Args:
        price: 価格配列（昇順。float64 以外は float64 へ変換して扱う）。
        ma_type: 種別キー（``MA_TYPES`` のいずれか。大文字小文字は区別しない）。
        length: 期間。

    Returns:
        ``price`` と同長の float64 配列（新規確保）。``length<=1`` または
        ``length>len(price)`` のときバッファ版は何も書かないため全 0 が返る
        （既存契約をそのまま踏襲する）。

    Raises:
        ValueError: ``ma_type`` が ``MA_TYPES`` に無い場合。
    """
    key = str(ma_type).lower()
    fn = _MA_ON_BUFFER.get(key)
    if fn is None:
        raise ValueError(f"未知の MA 種別です: {ma_type}")
    values = np.asarray(price, dtype=np.float64)
    n = int(values.shape[0])
    buffer = np.zeros(n, dtype=np.float64)
    fn(n, 0, 0, length, values, buffer)
    return buffer
