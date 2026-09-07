"""層名: 共有計算カーネル（σ スパン統計）— profit_rmm 所有の単一情報源。

責務:
    オシレーターの「スパン（採点の分母）」= avg±3σ の幅を求める純関数を **1 箇所だけ**
    定義する層。numpy のみに依存し、pandas・描画・成果物層・アダプタ層を一切 import
    しない。姉妹指標 profit_rmm_macd は本モジュールを import して同一実装を共有する
    （従来は profit_rmm_macd/src/core.py に verbatim 複製されていた＝ISSUE-502 D-5）。

含む構造:
    series_avg     : 系列平均（全系列）。
    series_std     : 母標準偏差（÷N・全系列）。
    oscillator_span: avg±3σ のスパン（clamp で [0,100] クランプ・MAROD は非クランプ）。
    rolling_span   : ``oscillator_span`` の因果ローリング版（単一掃引・freeze_last 対応）。

配置の理由（src/ 配下ではなくパッケージ直下に置く根拠）:
    profit_rmm/src/__init__.py は成果物層（pandas）と出力アダプタを再公開する集約層で
    ある。profit_rmm/src/core.py を姉妹指標の **core 層**から import すると、親パッケージ
    初期化により pandas とアダプタ層が連鎖 import され、(1) core 層の「pandas/描画
    import は禁止」契約に反し、(2) core（内側）→ アダプタ（外側）の依存方向逆流を招く。
    profit_rmm は __init__.py を持たない名前空間パッケージ（PEP 420）であるため、直下に
    置いた本モジュールは import profit_rmm.span_stats で **初期化コードを 1 行も実行せず**
    解決でき、numpy のみの純度を保てる。

    なお構造上の本筋は、両指標が既に import している中立共有層 profit_system
    （MAROD・funLevelCount 採点・因果 z 化を所有し numpy のみに依存）へ本モジュールを
    移すことである。profit_system の改修は本タスクの承認範囲外のため実施しない
    （移送は別タスクで扱う）。

import 解決の前提:
    indigators/ が import パスにあること。これは本モジュールの利用側（両 core）が既に
    共有ライブラリ（moving_averages・mql_builtins・profit_system）を同じ方法で解決して
    いる前提と**完全に同一**であり、新たな解決点を要求しない。解決点の台帳は
    tools/dev_paths.txt（テストは pyproject.toml の pythonpath、本番は venv の .pth と
    指標ローダが同じ台帳から導出する）。

依存:
    標準: __future__ / 外部: numpy のみ。pandas・描画・指標パッケージの import は禁止。
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "series_avg",
    "series_std",
    "oscillator_span",
    "rolling_span",
]


def series_avg(x: np.ndarray) -> float:
    """系列平均（全系列）。"""
    return float(np.mean(x))


def series_std(x: np.ndarray) -> float:
    """母標準偏差（÷N・全系列）。"""
    x = np.asarray(x, dtype=np.float64)
    avg = series_avg(x)
    return float(np.sqrt(np.mean((x - avg) ** 2)))


def oscillator_span(x: np.ndarray, *, clamp: bool) -> float:
    """avg±3σ のスパン（x3p - x3m）を返す。

    ``avg=series_avg(x)``, ``dev=series_std(x)``, ``x3p=avg+3*dev``,
    ``x3m=avg-3*dev``。``clamp=True``（RSI/WPR/MFI）→ x3p=min(100,x3p),
    x3m=max(0,x3m)。``clamp=False``（MAROD）→ クランプ無し。

    Args:
        x: 対象オシレーター系列。
        clamp: True で [0,100] クランプ、False で素値。

    Returns:
        x3p - x3m（float）。
    """
    avg = series_avg(x)
    dev = series_std(x)
    x3p = avg + 3.0 * dev
    x3m = avg - 3.0 * dev
    if clamp:
        x3p = min(100.0, x3p)
        x3m = max(0.0, x3m)
    return x3p - x3m


def rolling_span(
    x: np.ndarray, window: int, *, clamp: bool, freeze_last: bool = False
) -> np.ndarray:
    """``oscillator_span`` の因果ローリング版（各バーの avg±3σ スパンを直近 W 本から算出）。

    バー i のスパンを区間 ``[i-window+1, i]`` の平均・母標準偏差から
    ``(avg+3σ) - (avg-3σ)``（clamp 時は各端を [0,100] に丸め）で求める。未来を含まないため
    確定バーのスパン＝レベルカウントは repaint しない。warm-up（``i<window-1``）は ``NaN``。

    計算量: 累積和・累積二乗和（prefix sum）による単一掃引で、発行するスパンは
    ``n-window+1`` 個＝出力の有限要素数と一致する（窓ごとの再走査をしない＝作って捨てる
    計算が 0）。窓長 ``window`` を増やしても発行数は増えない。

    ``freeze_last``（既定 ``False``）:
        * ``False``: 上記の通り（既定。出力は 1 ビットも変えない）。
        * ``True``: **最終要素 ``out[-1]`` のみ** 基準窓を確定足
          ``[n-1-window .. n-2]``（最終点を除く直前 window 本）へ差し替えてスパンを
          算出する。``out[0..n-2]`` は ``freeze_last=False`` と完全に同一。形成中（足内）
          の最新足をティック粒度で採点する際、スパン（採点の分母）の基準を 1 足 1 回・
          足内で固定（凍結）する用途。平均・母標準偏差・分母 ``window``・クランプは
          本関数の既存定義と厳密に同一で、最終点だけ窓をずらした以外は数値が一致する。
          直前 window 本が満たせない（``n < window + 1``）場合は ``out[-1]=NaN``
          （warm-up と同様）。これは共有層 profit_system の因果 z 化が持つ freeze_last と
          同じ意味である。

    Args:
        x: 対象オシレーター系列。
        window: 過去参照本数 W（>=2）。
        clamp: True で各端を [0,100] にクランプ（RSI/WPR/MFI）、False で素値（MAROD）。
        freeze_last: True で最終点のスパン基準を確定足（直前 W 本）へ凍結する。既定
            False で挙動不変。

    Returns:
        各バーのスパン（同長, float64。warm-up は NaN）。
    """
    a = np.asarray(x, dtype=np.float64)
    n = a.size
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 2 or n < window:
        return out
    csum = np.concatenate([[0.0], np.cumsum(a)])
    csq = np.concatenate([[0.0], np.cumsum(a * a)])
    for i in range(window - 1, n):
        lo = i - window + 1
        avg = (csum[i + 1] - csum[lo]) / window
        var = (csq[i + 1] - csq[lo]) / window - avg * avg
        dev = np.sqrt(var) if var > 0.0 else 0.0
        x3p = avg + 3.0 * dev
        x3m = avg - 3.0 * dev
        if clamp:
            x3p = min(100.0, x3p)
            x3m = max(0.0, x3m)
        out[i] = x3p - x3m
    if freeze_last:
        # 最終点 out[-1] のみ、基準窓を確定足 [n-1-window .. n-2]（最終点を除く直前
        # window 本）へ差し替える。out[0..n-2] は上のループ結果のまま不変。
        if n < window + 1:
            out[-1] = np.nan  # 直前 window 本を満たせない（warmup 同様）。
        else:
            lo = n - 1 - window  # 直前 window 本 = a[lo:n-1]（= a[n-1-window .. n-2]）。
            hi = n - 1  # csum 上限 index（確定足 a[lo:n-1] の和 = csum[hi]-csum[lo]）。
            avg = (csum[hi] - csum[lo]) / window
            var = (csq[hi] - csq[lo]) / window - avg * avg
            dev = np.sqrt(var) if var > 0.0 else 0.0
            x3p = avg + 3.0 * dev
            x3m = avg - 3.0 * dev
            if clamp:
                x3p = min(100.0, x3p)
                x3m = max(0.0, x3m)
            out[-1] = x3p - x3m
    return out
