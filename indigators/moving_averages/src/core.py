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

増分版（前回の走行状態を授受し、full と bit 一致する続きを計算する。ISSUE-233）
    linear_weighted_ma_on_buffer_stateful / LwmaState
        sma/ema/smma は ``prev_calculated`` 契約がそのまま full の漸化を継続するため
        既存のバッファ版で増分計算できる（bit 一致を実測）。LWMA だけは
        ``prev_calculated>0`` 分岐が走行和を窓から再構築し full と一致しないため、
        走行和（total/lsum）を授受する本入口を用いる。

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

from typing import NamedTuple

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


class LwmaState(NamedTuple):
    """LWMA スライド和の走行状態（``linear_weighted_ma_on_buffer_stateful`` の授受単位）。

    ``linear_weighted_ma_on_buffer`` の ``prev_calculated>0`` 分岐は走行和を **窓から
    再構築**するため、full 経路（``prev_calculated=0``）が長い漸化で運んだ丸めが消え、
    末尾値が bit 一致しない（実測 max_dev 2.1e-09 @ n=1400・ISSUE-233）。``buffer[i] =
    total/weight`` は丸め済みで ``total`` を復元できないため、走行和そのものを持ち回る
    本状態が「full の続きから 1 点進める」唯一の手段である。

    Attributes:
        total: 加重和の走行値（``total - lsum + price[i]*period`` で更新）。
        lsum: 単純和の走行値（``lsum - price[i-period] + price[i]`` で更新）。
        weight: 重み合計（``period*(period+1)/2``・区間内で不変）。
        calculated: 計算済み本数（次の継続開始インデックス＝MQL ``prev_calculated``）。
    """

    total: float
    lsum: float
    weight: int
    calculated: int


def _lwma_seed(
    price: np.ndarray, start_position: int, period: int
) -> tuple[float, float, int]:
    """``start_position`` 直前の窓 ``period`` 本からスライド和を初期化する。

    LWMA の走行和初期化の **唯一の定義**（``linear_weighted_ma_on_buffer`` と
    ``linear_weighted_ma_on_buffer_stateful`` が共有する）。加算順序は元 MQL 実装と同一で
    あり、変更すると末尾値の最終ビットが動く。

    Returns:
        ``(total, lsum, weight)``。
    """
    total = 0.0
    lsum = 0.0
    weight = 0
    weight_idx = 1
    for i in range(start_position - period, start_position):
        total += price[i] * weight_idx
        lsum += price[i]
        weight += weight_idx
        weight_idx += 1
    return total, lsum, weight


def _lwma_advance(
    price: np.ndarray,
    buffer: np.ndarray,
    start: int,
    stop: int,
    period: int,
    total: float,
    lsum: float,
    weight: int,
) -> tuple[float, float]:
    """スライド和を ``[start, stop)`` へ前進させ ``buffer`` を更新する。

    LWMA 漸化式の **唯一の定義**（seed と同じく 2 つの公開関数が共有する）。``buffer`` は
    書き込みのみで読まないため、``[0, start)`` の内容には依存しない。

    Returns:
        ``(total, lsum)``（区間末尾時点の走行和）。
    """
    for i in range(start, stop):
        total = total - lsum + price[i] * period
        lsum = lsum - price[i - period] + price[i]
        buffer[i] = total / weight
    return total, lsum


def linear_weighted_ma_on_buffer(
    rates_total: int,
    prev_calculated: int,
    begin: int,
    period: int,
    price: np.ndarray,
    buffer: np.ndarray,
) -> int:
    """価格配列全体に対する線形加重移動平均（classic）を ``buffer`` へ書き込む。

    スライド和（``sum`` / ``lsum``）で逐次更新する古典実装。走行和の初期化・前進は
    ``_lwma_seed`` / ``_lwma_advance`` へ委譲する（``*_stateful`` と共有＝漸化式の定義は
    1 箇所）。委譲前の実装との bit 一致は ``tests/test_lwma_stateful.py`` の凍結オラクルで
    恒久固定する。

    Args:
        rates_total: 価格データの総本数。
        prev_calculated: 前回計算済み本数。
        begin: 有効データの開始インデックス。
        period: 加重本数。
        price: 価格配列（昇順）。
        buffer: 結果を書き込む配列（破壊的更新）。

    Returns:
        計算した総本数（``rates_total``）。条件不成立時は 0。

    Note:
        ``prev_calculated>0`` 分岐は走行和を窓から再構築するため、full 経路の続きとしては
        bit 一致しない（元 MQL 実装の挙動をそのまま維持している）。full と bit 一致する
        増分計算には ``linear_weighted_ma_on_buffer_stateful`` を使うこと。
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
    total, lsum, weight = _lwma_seed(price, start_position, period)
    buffer[start_position - 1] = total / weight
    # --- メインループ
    _lwma_advance(price, buffer, start_position, rates_total, period, total, lsum, weight)
    return rates_total


def linear_weighted_ma_on_buffer_stateful(
    rates_total: int,
    begin: int,
    period: int,
    price: np.ndarray,
    buffer: np.ndarray,
    state: "LwmaState | None" = None,
) -> "tuple[int, LwmaState | None]":
    """LWMA（classic）を走行和の授受で継続計算する（full と bit 一致する増分入口）。

    ``state=None`` は初回＝``linear_weighted_ma_on_buffer(..., prev_calculated=0, ...)`` と
    **bit 一致**する。``state`` を渡すと ``state.calculated`` から続きだけを計算し、その結果は
    「同じ本数で初回計算した結果」と **bit 一致**する（走行和を再構築せず継承するため）。
    ``linear_weighted_ma_on_buffer_fast`` が ``weight_sum`` を引数・戻り値で授受する前例と
    同じ様式。

    足内更新（同一の確定状態から形成中バーを差し替えて何度も計算する）で使えるよう、
    ``state`` は不変（NamedTuple）であり本関数は ``state`` を書き換えない。

    Args:
        rates_total: 価格データの総本数（``price`` の有効長）。
        begin: 有効データの開始インデックス（``state`` 継続時は seed 済みのため未使用）。
        period: 加重本数。
        price: 価格配列（昇順・長さ ``rates_total`` 以上）。``state`` 継続時も先頭からの
            全系列を渡す（``price[i-period]`` を参照するため）。
        buffer: 結果を書き込む配列（長さ ``rates_total`` 以上・破壊的更新）。``[0, calculated)``
            は読まないため、継続時に過去値を埋めておく必要はない。
        state: 前回の走行状態。``None`` は初回計算。

    Returns:
        ``(計算した総本数, 更新後の LwmaState)``。条件不成立時は ``(0, state)``。
    """
    # --- 期間チェック（既存 classic と同一契約）
    if period <= 1 or period > (rates_total - begin):
        return 0, state
    if state is None:
        # --- 初回: full 経路（prev_calculated=0）と同一の seed
        start_position = period + begin
        for i in range(start_position):
            buffer[i] = 0.0
        total, lsum, weight = _lwma_seed(price, start_position, period)
        buffer[start_position - 1] = total / weight
    else:
        # --- 継続: 走行和をそのまま引き継ぐ（再構築しない＝丸めの経路が full と同一）
        start_position = int(state.calculated)
        total, lsum, weight = float(state.total), float(state.lsum), int(state.weight)
    total, lsum = _lwma_advance(
        price, buffer, start_position, rates_total, period, total, lsum, weight
    )
    return rates_total, LwmaState(total, lsum, weight, rates_total)


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
