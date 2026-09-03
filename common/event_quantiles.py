"""外れ値イベント分位（純粋ロジック・外部 I/O 非依存・numpy のみ）。

①層名/責務:
    共有プリミティブ層。オシレータ系列と正常バンド（因果分位バンド）から「外れ値イベント」
    を検出し、その値集合の因果分位水準（典型深度＝中央値／極端深度＝極端分位）を生成する。
    MAROD 系（btlm_trail_marod / ma_marod）を初出として、正常バンドを持つあらゆる別 pane
    オシレータで再利用する横断ユーティリティ。特定の指標には属さない。

②含む構造:
    DEFAULT_Q_OUT / DEFAULT_K_EVENTS / DEFAULT_EVENT_AGG : 既定値（単一情報源）。
    q_out_valid              : 極端分位の有効判定（btlm_trail q_out 前例と同規約）。
    outlier_event_quantiles  : 本体（バンド超過イベントの因果分位水準）。
    水準線の表示仕様（色・線種・系列名サフィックスと定型 emit）は本モジュールにはない。
    表示仕様層（common_view の event_quantile_view）が単一情報源である（ISSUE-479 C-1）。

③設計の経緯（ユーザー裁定 2026-07-21・実測根拠は .doc/MA_MAROD_BASIC_DESIGN.md）:
    バンド線（因果分位バンドでは新記録スパイクを原理的に含められない）→ 超過点マーク
    （事後表示でトレードの事前把握要件を満たさない）→ イベント分位水準線（採用）。
    集計単位は episode（連続超過の declustering）が既定（バー単位は持続時間の重み付けで
    典型深度を歪める実測 +24.6% vs +19.1%）。bar は復帰用に保持。

④依存:
    標準: __future__ / 外部: numpy
"""

from __future__ import annotations

import numpy as np

# 既定値（ユーザー裁定 2026-07-21・全採用指標で共通の単一情報源）。
DEFAULT_Q_OUT: float = 0.99            # イベントの極端分位（上側 q_out・下側 1-q_out）
DEFAULT_K_EVENTS: int = 50             # ローリング側の直近観測件数（分散非定常対策・実測 2026-07-20）
DEFAULT_EVENT_AGG: str = "episode"     # episode＝エピソード極値（既定）／bar＝旧方式（復帰用）
_MIN_EVENTS: int = 5                   # 分位を計算する最小観測数（未満は NaN＝描画除外）


def q_out_valid(q_out, q_high: float) -> bool:
    """q_out（イベント極端分位）の有効判定。btlm_trail q_out 前例と同規約＋対称ペア契約。

    有効条件: ``max(q_high, 0.5) < q_out < 1``。q_out <= q_high は正常バンドと同深度以浅、
    q_out <= 0.5 は下側 1-q_out が中央値以上になり「極端」の意味を失うため無効化する。
    無効は黙ってオフ（前例の「黙って無効化」規約）。
    """
    if q_out is None:
        return False
    return max(float(q_high), 0.5) < float(q_out) < 1.0


def _event_flags(value: float, band_lo: float, band_hi: float) -> "tuple[bool, bool]":
    """当該バーがバンド外に出たか（上側・下側）を判定する（イベント定義の唯一の実装）。"""
    finite = np.isfinite(value)
    is_up = bool(finite and np.isfinite(band_hi) and value > band_hi)
    is_dn = bool((not is_up) and finite and np.isfinite(band_lo) and value < band_lo)
    return is_up, is_dn


def _step_bar(
    value: float, band_lo: float, band_hi: float,
    up: list, dn: list, run_up: list, run_dn: list,
) -> None:
    """bar 集計の 1 バーぶん（超過バーの値をそのまま 1 観測とする。旧方式・復帰用）。"""
    is_up, is_dn = _event_flags(value, band_lo, band_hi)
    if is_up:
        up.append(float(value))
    elif is_dn:
        dn.append(float(value))


def _step_episode(
    value: float, band_lo: float, band_hi: float,
    up: list, dn: list, run_up: list, run_dn: list,
) -> None:
    """episode 集計の 1 バーぶん（超過が途切れたバーでエピソード確定・極値を 1 観測とする）。"""
    is_up, is_dn = _event_flags(value, band_lo, band_hi)
    if not is_up and run_up:
        up.append(max(run_up))
        run_up.clear()
    if not is_dn and run_dn:
        dn.append(min(run_dn))
        run_dn.clear()
    if is_up:
        run_up.append(float(value))
    elif is_dn:
        run_dn.append(float(value))


# 集計単位 → 1 バーぶんのステッパの **単一表**（ISSUE-479 Wave2 C-6）。
# 集計単位の追加・削除は本表 1 箇所で完結する。既知集合も本表から導出する。
_EVENT_STEPPERS: "dict[str, object]" = {
    "episode": _step_episode,
    "bar": _step_bar,
}
_EVENT_AGGS: frozenset[str] = frozenset(_EVENT_STEPPERS)


def normalize_event_agg(event_agg) -> str:
    """集計単位を正規化する（**検証の唯一の実装**）。未知値は ValueError。

    従来は outlier_event_quantiles だけが検証しており、step_events は
    ``str(event_agg).lower() == "bar"`` の比較のみで**未知値を黙って episode へ縮退**
    させていた（タイプミスが無言で別の集計になる）。本関数へ一本化して封鎖する。
    """
    agg = str(event_agg).lower()
    if agg not in _EVENT_STEPPERS:
        raise ValueError(f"未知の event_agg です: {event_agg}（episode/bar）")
    return agg


def event_stepper(event_agg):
    """集計単位に対応する 1 バーぶんのステッパを返す（未知値は ValueError）。

    走査ループを持つ呼び出し側は、**ループの外で 1 回だけ**本関数を呼ぶこと
    （バーごとに解決すると n 回の正規化を発行して捨てることになる）。
    """
    return _EVENT_STEPPERS[normalize_event_agg(event_agg)]


def step_events(
    value: float, band_lo: float, band_hi: float, event_agg: str,
    up: list, dn: list, run_up: list, run_dn: list,
) -> None:
    """1 バーぶんのイベント検出・エピソード確定を行う（**唯一の定義**・リストを更新する）。

    呼び出し前に当該バーの水準を計算しておくこと（本バーの観測は次バー以降に効く＝因果）。
    ``run_up`` / ``run_dn`` は進行中のエピソード（in-place で更新する）。

    ISSUE-233: 増分計算が確定バーぶんだけ状態を進めるための公開入口。
    :func:`outlier_event_quantiles` のバー走査は :func:`event_stepper` で解決した
    ステッパを各バーで呼ぶループである。

    Raises:
        ValueError: ``event_agg`` が episode/bar のいずれでもないとき。
    """
    event_stepper(event_agg)(value, band_lo, band_hi, up, dn, run_up, run_dn)


def levels_at(
    events, m: int, k_events: int, ext_q: "float | None", *, whole: bool = False
) -> "tuple[float, float]":
    """確定観測 ``events`` が m 件ある時点の水準 ``(中央値, 極端分位)`` を返す（**唯一の定義**）。

    ``whole=False`` は直近 ``k_events`` 件、True は全履歴（``*_all``）。観測数 m が
    :data:`_MIN_EVENTS` 未満なら (NaN, NaN)。
    """
    if m < _MIN_EVENTS:
        return float("nan"), float("nan")
    arr = np.asarray(events, dtype=np.float64)
    window = arr[:m] if whole else arr[max(0, m - int(k_events)):m]
    med = float(np.median(window))
    ext = float(np.quantile(window, ext_q)) if ext_q is not None else float("nan")
    return med, ext


def event_levels_latest(
    up: list, dn: list, *, q_high: float, q_out: "float | None" = DEFAULT_Q_OUT,
    k_events: int = DEFAULT_K_EVENTS,
) -> "dict[str, float]":
    """確定観測列（``up`` / ``dn``）の **次のバー** に適用する水準 4 値を返す。

    :func:`outlier_event_quantiles` がバー t に与える med_hi/ext_hi/med_lo/ext_lo と同値
    （バー t の水準は t より前に確定した観測のみから決まるため）。ISSUE-233 の増分計算が
    末尾 1 点だけを求めるための公開入口。
    """
    qo = float(q_out) if q_out_valid(q_out, q_high) else None
    med_hi, ext_hi = levels_at(up, len(up), k_events, qo)
    med_lo, ext_lo = levels_at(dn, len(dn), k_events, (1.0 - qo) if qo is not None else None)
    return {"med_hi": med_hi, "ext_hi": ext_hi, "med_lo": med_lo, "ext_lo": ext_lo}


def outlier_event_quantiles(
    values: np.ndarray,
    band_lo: np.ndarray,
    band_hi: np.ndarray,
    *,
    q_high: float,
    q_out: float | None = DEFAULT_Q_OUT,
    k_events: int = DEFAULT_K_EVENTS,
    event_agg: str = DEFAULT_EVENT_AGG,
    include_all: bool = True,
) -> dict[str, np.ndarray]:
    """外れ値イベント（正常バンド超）の因果分位水準を返す（系列汎用の正実装）。

    イベント定義（因果・非リペイント）: バー t の系列値が正常バンド（呼び出し側が算出した
    当該バー除外の因果バンド）の外に出たとき（上側: 値 > band_hi、下側: 値 < band_lo）。
    集計単位は ``event_agg`` で選択:

        - "episode"（既定）: 連続超過バーの 1 まとまり＝1 エピソードとし、その極値
          （上側 max・下側 min）を 1 観測とする（runs declustering）。エピソードは
          「終了が確認されたバー」（内側へ戻った/反対側へ移った/値未定義のバー）で確定し、
          そのバーの水準計算より後（次バー以降）に観測として利用可能になる（因果）。
          データ末尾で進行中のエピソードは未確定のため集計に含めない（非リペイント）。
        - "bar": 超過バーの値を 1 バー＝1 観測とする（旧方式。復帰用に保持）。

    バー t の水準は **t より前に確定した観測のみ**から計算する（当該バー除外の規約に整合）。

    水準（上側・下側それぞれ）:
        - 中央値（典型深度）: 「外れたとき、典型的にどこまで行くか」。
        - 極端分位（q_out・上側 q_out／下側 1-q_out）: 「外れたとき、極端にはどこまで
          行き得るか」。q_out 無効（None/範囲外）は極端水準のみ NaN（黙って無効化）。
    集計範囲は 2 系統: 直近 k_events 件（ローリング＝分散非定常対策）と全履歴（参照用）。
    観測数 < _MIN_EVENTS は NaN。

    Args:
        values: オシレータ系列（NaN は未定義として扱う）。
        band_lo/band_hi: 正常バンド（values と同長・因果・warm-up は NaN）。
        q_high: 正常バンドの上側分位（q_out 有効判定にのみ使用）。
        q_out: イベントの極端分位（有効条件 max(q_high, 0.5) < q_out < 1）。
        k_events: ローリング側の直近観測件数（min 1。episode ではエピソード数）。
        event_agg: 集計単位（"episode"/"bar"）。
        include_all: False で全履歴（*_all）系の計算を省略する（キーは全 NaN のまま返す＝
            戻り値の形は不変）。表示層は直近 K 件のみ描画するため（2026-07-21 裁定）、
            ライブ経路の性能是正（ISSUE-154）として計算を省ける。既定 True（後方互換）。

    Returns:
        dict。キーは med_hi/ext_hi/med_lo/ext_lo（直近 k_events 件）と
        med_hi_all/ext_hi_all/med_lo_all/ext_lo_all（全履歴）。各長さ n・未定義は NaN。

    Raises:
        ValueError: k_events < 1、event_agg 不正、または系列とバンドの長さ不一致のとき。
    """
    k = int(k_events)
    if k < 1:
        raise ValueError(f"k_events は 1 以上が必要です: k_events={k_events}")
    # 集計単位の解決は **ループの外で 1 回**（n=0 でも未知値はここで ValueError＝挙動不変）。
    stepper = event_stepper(event_agg)
    v = np.asarray(values, dtype=np.float64).ravel()
    lo = np.asarray(band_lo, dtype=np.float64).ravel()
    hi = np.asarray(band_hi, dtype=np.float64).ravel()
    n = v.size
    if lo.size != n or hi.size != n:
        raise ValueError(f"系列とバンドの長さが一致しません: n={n}, lo={lo.size}, hi={hi.size}")
    qo = float(q_out) if q_out_valid(q_out, q_high) else None

    keys = ("med_hi", "ext_hi", "med_lo", "ext_lo",
            "med_hi_all", "ext_hi_all", "med_lo_all", "ext_lo_all")
    out = {key: np.full(n, np.nan) for key in keys}
    up: list[float] = []      # 上側の確定観測（時系列順・bar=バー値/episode=エピソード max）
    dn: list[float] = []      # 下側の確定観測（同・episode はエピソード min）
    run_up: list[float] = []  # episode 用: 進行中の上側連続超過バー値
    run_dn: list[float] = []  # episode 用: 進行中の下側連続超過バー値

    # ISSUE-154（性能是正）: 水準は「その時点で確定済みの観測数 m」だけで決まる（観測列は
    #   時系列順に単調追記＝m が同じなら同じ集合）。旧実装はバーごとに median/quantile を
    #   計算し n×4 回の numpy 呼出（1,500 本で ~0.25 秒）だった。①バー走査で観測列と
    #   「各バー時点の観測数」を先に確定し、②観測数 m ごとの水準テーブルを 1 回だけ計算し、
    #   ③バーへ写像する（呼出回数 n×4 → 観測数×スコープ数・出力は完全一致）。
    up_cnt = np.zeros(n, dtype=np.int64)   # バー t の水準計算時点で確定済みの上側観測数
    dn_cnt = np.zeros(n, dtype=np.int64)
    for t in range(n):
        up_cnt[t] = len(up)
        dn_cnt[t] = len(dn)
        stepper(v[t], lo[t], hi[t], up, dn, run_up, run_dn)
    # データ末尾で進行中のエピソード（run_up/run_dn 残）は未確定のため破棄（非リペイント）。

    def _tables(events: list[float], ext_q: float | None):
        """観測数 m（_MIN_EVENTS..len）ごとの水準テーブル（med/ext × 直近K件/全履歴）。

        1 点ぶんの算出は :func:`levels_at` へ委譲する（定義は 1 箇所）。
        """
        m_max = len(events)
        med_k = np.full(m_max + 1, np.nan)
        ext_k = np.full(m_max + 1, np.nan)
        med_a = np.full(m_max + 1, np.nan)
        ext_a = np.full(m_max + 1, np.nan)
        for m in range(_MIN_EVENTS, m_max + 1):
            med_k[m], ext_k[m] = levels_at(events, m, k, ext_q)
            if include_all:
                med_a[m], ext_a[m] = levels_at(events, m, k, ext_q, whole=True)
        return med_k, ext_k, med_a, ext_a

    for events, cnt, med_key, ext_key, ext_q in (
        (up, up_cnt, "med_hi", "ext_hi", qo),
        (dn, dn_cnt, "med_lo", "ext_lo", (1.0 - qo) if qo is not None else None),
    ):
        if len(events) < _MIN_EVENTS:
            continue
        med_k, ext_k, med_a, ext_a = _tables(events, ext_q)
        out[med_key] = med_k[cnt]
        out[ext_key] = ext_k[cnt]
        if include_all:
            out[med_key + "_all"] = med_a[cnt]
            out[ext_key + "_all"] = ext_a[cnt]
    return out
