"""MarketProfileController — GET /market_profile の純ロジック（HTTP 殻非依存）。

``handle_market_profile(ref, timeframe, limit, bins, va) -> (HTTPステータス, ボディ)`` は
HTTP サーバ本体（BaseHTTPRequestHandler・ソケット）に依存しない純関数である。HTTP 殻
（``api/framework/server.py``）は本関数を呼ぶ薄い分岐として配線する（``handle_compute`` と同型）。

処理:
  1. datasetRef をホワイトリスト解決する（未知キー・パス文字列は 400 で拒否＝§7.3 パストラバーサル対策）。
  2. timeframe を検証する（None は原子＝再集計なし・後方互換。未知コードは 400）。
  3. ``dataset.load_candles(ref, tf, limit)`` で OHLC candles（``[{time,open,high,low,close}]``）を取得する
     ── この形はそのまま ``compute_candle_profile`` の入力形（time/open/high/low/close の辞書リスト）。
  4. ``bins``（int・既定 60・[1, _MAX_BINS] にクランプ）/ ``va``（float・既定と有効域は
     :func:`market_profile.resolve_va_pct` の単一規約）を反映して足ベース TPO プロファイルを計算する。
  5. 成功は (200, {ok, profile})。ref/timeframe の検証失敗は §6.3.4 nested error（error.type→
     HTTPステータスは api_shared.http_contract.ERROR_STATUS・単一定義）で 400 に翻訳する。
     bins/va は例外化せずクランプで吸収する（500 化しない）。data load / 計算の想定外失敗のみ
     HTTP 殻の包括 try/except で internal 500 になる。

依存方向: framework → adapter（本 controller）→ adapter.compute（dataset / market_profile）。
既存 dataset / market_profile 計算コアは read-only（改変しない）。src は candle（足ベース TPO）のみ。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from api_shared.http_contract import nested_error  # §6.3.4 単一定義（ISSUE-094 🔵-11: 中立共有パッケージへ移設）
from marketdata import dataset  # dataset 実体は marketdata へ移設済み（最下層 peer 依存）
from market_profile_api.compute import market_profile_dwell
from market_profile_api.compute import market_profile_zp
from market_profile_api.compute.market_profile import (
    compute_candle_profile,
    price_range,
    resolve_va_pct,
)

# パラメータ既定値（GET /market_profile のクエリ省略時）。
#   ISSUE-260: va（バリューエリア比率）の既定と有効域・クランプ規約は compute の単一情報源
#   （:data:`market_profile.VA_PCT_DEFAULT` / :func:`market_profile.resolve_va_pct`）が持つ。
#   ここに写しを置くと、tf-period 列や増分成長が別の既定へ黙って落ちる（本 ISSUE の原因）。
_DEFAULT_BINS = 60
_DEFAULT_SRC = "candle"
# 入力境界（単一スレッド常駐サーバの占有防止）。
#   bins は [1, _MAX_BINS] にクランプ（0 での ValueError→500 と巨大値での占有を封じる）。
_MAX_BINS = 1000

# ISSUE-097 🔴-1（OCP）: src（プロファイル計算ソース）の定義を SourceDescriptor 登録表へ集約する。
#   従来 4 箇所へ分散していた許可集合・metric 表・atom 表・実処理 if 連鎖を、1 ソース＝1 記述子の
#   単一レジストリ（本ファイル末尾 _SOURCE_DESCRIPTORS）へ寄せる。新ソース追加＝表への 1 エントリ追加のみ。
#   `_ALLOWED_SRC` / `_SRC_METRIC` / `_ATOM` は当該表からの導出値（同一値・同一順序＝応答 byte 不変）。
#   実体（登録表・導出値・dispatch handler）は handler 関数定義後にまとめて配置する。


@dataclass(frozen=True)
class _MPRequest:
    """handle_market_profile が解決済みパラメータを src handler へ渡す Input Model（HTTP 殻非依存）。

    src ごとの dispatch handler は本 DTO 1 つを受け取り (HTTPステータス, ボディ) を返す。既存の
    `_handle_candle` / `_handle_dwell` / `_handle_zp` の引数・戻り値・例外は不変（handler は薄い委譲）。
    """

    ref: Any
    timeframe: Any
    limit_n: int | None
    n_bins: int
    va_pct: float
    barw_val: float
    src_val: str
    to_ts: int | None
    from_ts: int | None
    want_today: bool
    want_sessions: bool
    want_fine: bool


@dataclass(frozen=True)
class SourceDescriptor:
    """1 プロファイルソース（src）の宣言的記述子。新ソース追加＝本記述子 1 件の追加のみで閉じる。

    Attributes:
        id: src 識別子（クエリ ``?src=`` の値・許可集合の 1 要素）。
        atom: 応答トップレベル ``atom``（UI 表示用・原子の意味）。
        metric: dwell モジュールに渡す metric（dwell 系のみ。非該当は None＝``_SRC_METRIC`` へ含めない）。
        handler: 解決済み :class:`_MPRequest` を受け ``(status, body)`` を返す dispatch 関数。
    """

    id: str
    atom: str
    metric: str | None
    handler: Callable[["_MPRequest"], tuple[int, dict[str, Any]]]
# tf → 足の秒長（dwell 窓の終端は t1 + bar_sec で最終足の期間を満たす）。未知/None は 1D 相当。
# ISSUE-097 🟡-10: 唯一源 marketdata.tf_meta.TF_BAR_SEC を参照（従来の自前コピーは byte 同一の重複だった）。
from marketdata.tf_meta import TF_BAR_SEC as _TF_BAR_SEC  # noqa: E402
# sessions（日別プロファイル）応答の日数上限。UI は列幅>=102px を確保できる直近 nFit 日
# （4K 幅でも ~37 列）しか描かないため、全期間ぶん（数千日×数百bin ≈ 10MB 超）を返すのは無駄。
# 直近 _SESSIONS_MAX_DAYS 日に切って応答を軽量化する（試作は窓 n で自然に制限されていた）。
_SESSIONS_MAX_DAYS = 60


def _cap_sessions(sessions: list) -> list:
    """sessions を直近 _SESSIONS_MAX_DAYS 日へキャップする（応答肥大の防止）。"""
    return sessions[-_SESSIONS_MAX_DAYS:] if len(sessions) > _SESSIONS_MAX_DAYS else sessions


def _bar_sec_for_tf(timeframe: Any) -> int:
    """timeframe から足の秒長を返す（None・未知は 86400=1D 相当・dwell 窓終端の延長量）。"""
    return _TF_BAR_SEC.get(timeframe, 86400)


def _resolve_n_bins(n_bins: int, barw: float, price_min: float, price_max: float) -> int:
    """barw（レンジpt・>0）指定時は ``n_bins = round((price_max-price_min)/barw)`` を bins に優先する。

    price_min/price_max 確定後に呼ぶ（試作 prototype_260630-01 の barw 意味論を移植）。クランプは既存
    bins と同じ ``[1, _MAX_BINS]`` を流用する（0 や巨大値での退化/占有を封じる）。barw<=0 やレンジ縮退時は
    従来 bins をそのまま返す（auto）。
    """
    if barw > 0 and price_max > price_min:
        return max(1, min(int(round((price_max - price_min) / barw)), _MAX_BINS))
    return n_bins


def _bar_width(profile: dict[str, Any]) -> float:
    """profile の実効レンジ(pt) = ``(price_max - price_min) / n_bins``（小数2桁）。0 除算は 0.0。"""
    nb = int(profile.get("n_bins") or 0)
    span = float(profile.get("price_max", 0.0)) - float(profile.get("price_min", 0.0))
    return round(span / nb, 2) if nb > 0 else 0.0


@dataclass(frozen=True)
class _ResolvedWindow:
    """dwell/zp 共通の窓確定結果（ISSUE-133 SRP: _handle_dwell/_handle_zp の同型複製を単一化）。

    ``dataset.load_candles`` → to/from 切り出し → 空判定 → 実期間 (t0/t1)・表示レンジ
    (price_min/price_max)・足秒 (bar_sec)・barw→n_bins 反映 を一箇所に集約する。旧 2 handler の
    inline 処理（load・フィルタ・空/非空分岐・スカラ導出）と byte 単位で同一の値を返す。
    """

    empty: bool
    t0: int
    t1: int
    price_min: float
    price_max: float
    bar_sec: int
    n_bins: int


def _resolve_window(
    ref: Any,
    timeframe: Any,
    limit_n: int | None,
    n_bins: int,
    barw: float,
    to_ts: int | None,
    from_ts: int | None,
) -> _ResolvedWindow:
    """tick 系（dwell/zp）の集計窓を確定する（旧 _handle_dwell/_handle_zp の inline と同一規則）。

    candles を load し ``to_ts``（as-seen-at-t 上限・含む）/ ``from_ts``（ローリング窓下限・含む）で
    切り出す。空なら実期間・レンジ 0・bar_sec=86400・n_bins 据え置き（旧空分岐と同値）。非空なら
    t0=先頭 time・t1=末尾 time・price_min=min(low)・price_max=max(high)・bar_sec=tf 足秒・
    barw>0 は price レンジ確定後に n_bins を上書き（旧非空分岐と同値）。
    """
    candles = dataset.load_candles(ref, timeframe, limit_n)
    if to_ts is not None:
        candles = [c for c in candles if c["time"] <= to_ts]
    if from_ts is not None:
        candles = [c for c in candles if c["time"] >= from_ts]
    if not candles:
        return _ResolvedWindow(True, 0, 0, 0.0, 0.0, 86400, n_bins)
    t1 = candles[-1]["time"]
    price_min = min(c["low"] for c in candles)
    price_max = max(c["high"] for c in candles)
    t0 = candles[0]["time"]
    bar_sec = _bar_sec_for_tf(timeframe)
    n_bins = _resolve_n_bins(n_bins, barw, price_min, price_max)
    return _ResolvedWindow(False, t0, t1, price_min, price_max, bar_sec, n_bins)


def _error_body(error_type: str, message: str) -> tuple[int, dict[str, Any]]:
    """§6.3.4 nested error（{ok:false, generation, error:{type, message, violations}}）。

    ステータス翻訳・ボディ形とも正典 api_shared.http_contract.nested_error（単一定義）へ
    委譲する（ISSUE-091 A2: 3 殻の契約分岐を構造排除）。
    """
    return nested_error(error_type, message)


def _parse_int(raw: Any, default: int | None) -> int | None:
    """クエリ由来の値（str|None）を非負 int へ変換する（不正・None は default）。"""
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return default


def _parse_float(raw: Any, default: float) -> float:
    """クエリ由来の値（str|None）を float へ変換する（不正・None は default）。"""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return default
    return default


def _parse_to(raw: Any) -> int | None:
    """リプレイ時間カーソル ``to``（UNIX 秒・str|int|None）を int へ変換する（不正・None は None=全期間）。

    移植元: prototype_260630-01 の as-seen-at-t（時間カーソル）。``to`` は「その時点までに観測できた足」
    に集計範囲を限定する上限 time（含む）。負値・非数値・None は None（＝全期間・現行挙動）へ丸める。
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, float):
        return int(raw) if math.isfinite(raw) and raw >= 0 else None
    if isinstance(raw, str):
        s = raw.strip()
        if s.isdigit():
            return int(s)
    return None


# ``from`` は ``to`` と同じ丸め（UNIX 秒・非負 int・不正/None は None）。ローリング窓の下限 time（含む）。
_parse_from = _parse_to


def _parse_bool_flag(raw: Any) -> bool:
    """``today`` 等の BOOL フラグ（クエリ str|int|bool|None）を判定する（'1'/1/True で真・それ以外は偽）。

    移植元 prototype_260630-01 の ``?today=1``（''→偽・'1'→真）に準拠する。省略・不正は偽（後方互換）。
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return raw == 1
    if isinstance(raw, str):
        return raw.strip() == "1"
    return False


def handle_market_profile(
    ref: Any,
    timeframe: Any = None,
    limit: Any = None,
    bins: Any = None,
    va: Any = None,
    src: Any = None,
    barw: Any = None,
    to: Any = None,
    **kwargs: Any,
) -> tuple[int, dict[str, Any]]:
    """GET /market_profile の純ロジック。

    Args:
        ref: datasetRef（ホワイトリスト済みキー）。未知は 400。
        timeframe: 時間足コード（None=原子・再集計なし）。未知は 400。
        limit: 直近 N 本に制限（クエリ str|int|None。不正・None は全件）。
        bins: 価格ビン分割数（クエリ str|int|None。不正・None は 60）。
        va: バリューエリア比率 0..1（クエリ str|float|None）。解決は単一情報源
            :func:`market_profile.resolve_va_pct`（不正・None は既定・範囲外はクランプ）。
        src: 集計原子（'candle'=足レンジ TPO・既定 / 'dwell'=実ティック滞在秒・セッション認識 /
            'm1'=生ティック数）。許可値以外は 400。'dwell'/'m1' はティック対応 ref（'jp225_tick'）以外は 400。
        barw: レンジ(pt・クエリ str|float|None)。>0 指定時は price レンジ確定後に
            n_bins = round((price_max-price_min)/barw) を算出し bins に優先する（candle/dwell/m1 いずれも）。
            None/0/不正・auto は従来 bins。
        to: リプレイ時間カーソル（UNIX 秒・クエリ str|int|None）。指定時は ``time <= to`` の足だけで
            集計する（as-seen-at-t・アンカー＝データ先頭..T の累積）。省略・不正・範囲外は無視して全期間
            （現行挙動＝後方互換）。移植元 prototype_260630-01。
        **kwargs: ``from``（UNIX 秒・ローリング窓の下限 time・含む）と ``today``（'1' でスナップショット
            当日強調）を受ける（``from`` は Python 予約語のため kwargs 経由）。増分2 A/C。
            ``from`` 指定時は ``from <= time`` の足だけで集計する（``to`` と併用で [from,to] のローリング窓）。
            ``today`` 真時は応答 profile に ``today[]``/``today_max``（窓最終日ぶんの表示 bin 値）を付加する。
            ``sessions`` 真時（'1'）は応答トップレベルに ``sessions[{date,tpo[]}]``（各カレンダー日の表示
            bin プロファイル・日付昇順）を付加する（profile 8 キーは不変・追加キーのみ）。
            いずれも省略・不正は無視して現行挙動（後方互換）。移植元 prototype_260630-01 mp_core。

    Returns:
        (HTTPステータス, ボディ)。成功は (200, {ok:true, profile:{...}, src, atom, bar_width})、
        失敗は (400/5xx, {ok:false, generation, error:{...}})。既存 candle/dwell の profile 応答スキーマは不変
        （src/atom/bar_width はトップレベルのメタ情報として付加・profile 内の既存キーは維持）。
    """
    # src ホワイトリスト（既定 candle）。許可値以外は 400 validation。
    src_val = _DEFAULT_SRC if src is None else src
    if src_val not in _ALLOWED_SRC:
        return _error_body(
            "validation", f"未知の src です: {src!r}（{'|'.join(_ALLOWED_SRC)}）"
        )

    # datasetRef ホワイトリスト解決（§7.3）。未知キー・パス文字列は拒否。
    if not dataset.is_known(ref):
        return _error_body("validation", f"未知の datasetRef です: {ref!r}")

    # timeframe — None は原子（後方互換）。未知コードは拒否（§7.3 同様）。
    if timeframe is not None and not dataset.is_known_timeframe(timeframe):
        return _error_body("validation", f"未知の timeframe です: {timeframe!r}")

    # bins/va は範囲クランプ（不正値でも 500/退化させず、安全な範囲へ丸める）。
    n_bins = max(1, min(int(_parse_int(bins, _DEFAULT_BINS)), _MAX_BINS))
    va_pct = resolve_va_pct(va)  # ISSUE-260: 解決規則は単一情報源（全経路で同一）。
    limit_n = _parse_int(limit, None)
    # barw（レンジpt）— 有限かつ非負のみ採用（不正・None・負・NaN/Inf は 0=auto へ丸める）。
    barw_val = _parse_float(barw, 0.0)
    if not math.isfinite(barw_val) or barw_val < 0:
        barw_val = 0.0
    # to（リプレイ時間カーソル・UNIX 秒）— 不正・None は None（全期間・後方互換）。
    #   ISSUE-129: to はリプレイの単一時計（as-seen-at-t の T）。zp はこれをそのまま「現在時刻」
    #   として読む（now=to・_handle_zp）。旧 ``asof`` パラメータは廃止（受信しても無視＝無害）。
    to_ts = _parse_to(to)
    # from（ローリング窓の下限 time・増分2 A）／today（スナップショット・増分2 C）。予約語 from は kwargs 経由。
    from_ts = _parse_from(kwargs.get("from"))
    want_today = _parse_bool_flag(kwargs.get("today"))
    # sessions（日別プロファイル分割・?sessions=1）。省略・不正は偽（後方互換）。移植元 prototype_260630-01。
    want_sessions = _parse_bool_flag(kwargs.get("sessions"))
    # want_fine（GRID_W 固定グリッド base の露出・tick 逐次成長の忠実 binning 用）。Python 内部呼び出し
    #   （market_profile_forming_controller）専用の追加フラグ。省略・不正は偽＝既存応答スキーマ不変（後方互換）。
    want_fine = _parse_bool_flag(kwargs.get("want_fine"))

    # ISSUE-097 🔴-1（OCP）: src 分岐は SourceDescriptor 登録表の handler へ委譲する（テーブル駆動）。
    #   src_val は上の許可集合検証を通過済み＝必ず登録表に存在する。新ソース追加＝表への 1 エントリのみ。
    request = _MPRequest(
        ref=ref, timeframe=timeframe, limit_n=limit_n, n_bins=n_bins, va_pct=va_pct,
        barw_val=barw_val, src_val=src_val, to_ts=to_ts, from_ts=from_ts,
        want_today=want_today, want_sessions=want_sessions, want_fine=want_fine,
    )
    return _SOURCE_REGISTRY[src_val].handler(request)


def _handle_candle(
    ref: Any,
    timeframe: Any,
    limit_n: int | None,
    n_bins: int,
    va_pct: float,
    barw: float,
    to_ts: int | None = None,
    from_ts: int | None = None,
    want_today: bool = False,
    want_sessions: bool = False,
) -> tuple[int, dict[str, Any]]:
    """src=candle（既定）— 足ベース TPO 経路（従来の inline 処理を抽出・挙動不変）。

    candles は load_candles が返す ``[{time,open,high,low,close}]`` がそのまま compute の入力形。
    barw>0 指定時のみ price レンジ確定後に n_bins を上書きする。応答スキーマは従来と同一。
    """
    candles = dataset.load_candles(ref, timeframe, limit_n)
    if to_ts is not None:
        # as-seen-at-t: T までに観測できた足だけへ切る（未来リーク無し）。空になれば従来の空プロファイル応答。
        candles = [c for c in candles if c["time"] <= to_ts]
    if from_ts is not None:
        # ローリング窓: from 以上の足だけへ切る（下限・含む）。to と併用で [from,to] 窓（増分2 A）。
        candles = [c for c in candles if c["time"] >= from_ts]
    if candles and barw > 0:
        # price レンジは compute_candle_profile と同一定義（price_range 単一情報源）。barw→n_bins に先取り使用。
        price_min, price_max = price_range(candles)
        n_bins = _resolve_n_bins(n_bins, barw, price_min, price_max)
    profile = compute_candle_profile(
        candles, n_bins=n_bins, va_pct=va_pct, want_today=want_today,
        want_sessions=want_sessions,
    )
    body = {
        "ok": True, "profile": profile, "src": "candle",
        "atom": _ATOM["candle"], "bar_width": _bar_width(profile),
    }
    # sessions は応答トップレベルへ移す（profile 8 キー不変・追加キーのみ）。省略時は付加しない。
    #   直近 _SESSIONS_MAX_DAYS 日へキャップ（UI が描くのは直近 nFit 列のみ・応答肥大の防止）。
    #   sessions_total はキャップ前の実日数（primitive 注記「直近N/全M日」の M＝キャップ後 60 の誤読防止）。
    if want_sessions:
        all_sessions = profile.pop("sessions", [])
        body["sessions_total"] = len(all_sessions)
        body["sessions"] = _cap_sessions(all_sessions)
    return 200, body


def _handle_dwell(
    ref: Any,
    timeframe: Any,
    limit_n: int | None,
    n_bins: int,
    va_pct: float,
    barw: float,
    src: str,
    to_ts: int | None = None,
    from_ts: int | None = None,
    want_today: bool = False,
    want_sessions: bool = False,
    want_fine: bool = False,
) -> tuple[int, dict[str, Any]]:
    """src=dwell/m1 の処理（実ティック・tick 対応 ref のみ）。非 tick ref は 400。

    src='dwell'→metric='dwell'（滞在秒・セッション認識）、src='m1'→metric='count'（生ティック数・
    セッション非依存）。load_candles で表示レンジ（price_min=min(low)/price_max=max(high)）と実期間
    （t0=先頭/t1=末尾の time）を求め、tf から bar_sec を決めて
    :func:`market_profile_dwell.compute_dwell_profile` を呼ぶ。barw>0 は price レンジ確定後に n_bins を上書き
    する。応答スキーマは candle 版と同一。src/atom/bar_width をトップレベルのメタ情報として付加する。
    """
    symbol = market_profile_dwell.resolve_symbol(ref)
    if symbol is None:
        return _error_body(
            "validation",
            f"src={src} はティック対応 ref のみ対応です: {ref!r}（例: 'jp225_tick'）",
        )

    metric = _SRC_METRIC[src]
    # 窓確定（load・to/from 切り出し・空判定・t0/t1/レンジ/bar_sec/barw→n_bins）は zp と共通化した
    #   _resolve_window に単一化（ISSUE-133 SRP）。旧空/非空分岐と同値。全期間化（250日キャップ撤廃）で
    #   レンジ（price_min/max）と実期間（t0/t1）は全 candle から算出＝集計窓（全期間）に一致する。
    w = _resolve_window(ref, timeframe, limit_n, n_bins, barw, to_ts, from_ts)
    profile = market_profile_dwell.compute_dwell_profile(
        symbol, w.t0, w.t1, w.price_min, w.price_max, w.n_bins,
        va_pct=va_pct, bar_sec=w.bar_sec, metric=metric, want_today=want_today,
        want_sessions=want_sessions, want_fine=want_fine,
    )
    body = {
        "ok": True, "profile": profile, "src": src,
        "atom": _ATOM[src], "bar_width": _bar_width(profile),
    }
    # sessions は応答トップレベルへ移す（profile 8 キー不変・追加キーのみ）。省略時は付加しない。
    #   直近 _SESSIONS_MAX_DAYS 日へキャップ（UI が描くのは直近 nFit 列のみ・応答肥大の防止）。
    #   sessions_total はキャップ前の実日数（primitive 注記「直近N/全M日」の M＝キャップ後 60 の誤読防止）。
    if want_sessions:
        all_sessions = profile.pop("sessions", [])
        body["sessions_total"] = len(all_sessions)
        body["sessions"] = _cap_sessions(all_sessions)
    return 200, body


def _handle_zp(
    ref: Any,
    timeframe: Any,
    limit_n: int | None,
    n_bins: int,
    va_pct: float,
    barw: float,
    to_ts: int | None = None,
    from_ts: int | None = None,
    want_today: bool = False,
    want_sessions: bool = False,
) -> tuple[int, dict[str, Any]]:
    """src=zp（超過占有スコア z(p)）の処理（実ティック・tick 対応 ref のみ）。非 tick ref は 400。

    _handle_dwell のミラー。窓確定（candles から t0/t1/price_min/max・to/from 切り出し・barw→n_bins）
    は同一規則で、集計のみ :func:`market_profile_zp.compute_zp_profile`（分単位滞在の Null B 超過）
    を呼ぶ。応答スキーマは candle/dwell 版と同一（tpo=z 値・norm=clip(z,0) 正規化・poc=POC*）＋
    additive（z_max/poc_star）。want_fine（forming accumulator 経路）は zp 非対応のため受けない。

    ISSUE-129（単一時計）: ``to_ts`` はリプレイの現在時刻そのもの（as-seen-at-t の T・リビール秒粒度）。
    指定時は compute の「現在時刻」now を to_ts で読む＝境界日はライブと同一機構（未完了日の経過分
    クランプ）で [セッション始端, to] の部分 z になり、1D でも日内推移が成長する。None は実時計
    （ライブ＝全期間・現行挙動）。旧 ``asof`` パラメータは廃止（now の二重化を排除）。
    """
    symbol = market_profile_dwell.resolve_symbol(ref)
    if symbol is None:
        return _error_body(
            "validation",
            f"src=zp はティック対応 ref のみ対応です: {ref!r}（例: 'jp225_tick'）",
        )

    # 窓確定は dwell と共通の _resolve_window に単一化（ISSUE-133 SRP・旧空/非空分岐と同値）。
    w = _resolve_window(ref, timeframe, limit_n, n_bins, barw, to_ts, from_ts)
    # ISSUE-129（単一時計）: to 指定時のみ compute の now を to（リプレイ現在時刻）で読む
    #   （None は実時計＝ライブ・後方互換）。
    now_kw = {"now": float(to_ts)} if to_ts is not None else {}
    profile = market_profile_zp.compute_zp_profile(
        symbol, w.t0, w.t1, w.price_min, w.price_max, w.n_bins,
        va_pct=va_pct, bar_sec=w.bar_sec, want_today=want_today,
        want_sessions=want_sessions, **now_kw,
    )
    body = {
        "ok": True, "profile": profile, "src": "zp",
        "atom": _ATOM["zp"], "bar_width": _bar_width(profile),
    }
    if want_sessions:
        all_sessions = profile.pop("sessions", [])
        body["sessions_total"] = len(all_sessions)
        body["sessions"] = _cap_sessions(all_sessions)
    return 200, body


# ── ISSUE-097 🔴-1（OCP）: src dispatch 登録表 ─────────────────────────────────────
# 各 dispatch 関数は解決済み _MPRequest 1 つを受け、対応する _handle_* へ既存と同一の引数で委譲する
# （呼び出し先・引数・戻り値・例外は完全不変）。dwell と m1 は metric 差のみで同一処理のため共通の
# _dispatch_dwell を共有し、src_val（_MPRequest 経由）で metric を分岐する（既存 if-chain と同挙動）。


def _dispatch_candle(request: _MPRequest) -> tuple[int, dict[str, Any]]:
    """src=candle の dispatch（_handle_candle へ委譲）。"""
    return _handle_candle(
        request.ref, request.timeframe, request.limit_n, request.n_bins, request.va_pct,
        request.barw_val, request.to_ts, request.from_ts, request.want_today,
        request.want_sessions,
    )


def _dispatch_dwell(request: _MPRequest) -> tuple[int, dict[str, Any]]:
    """src=dwell/m1 の dispatch（_handle_dwell へ委譲・metric は src_val で分岐）。"""
    return _handle_dwell(
        request.ref, request.timeframe, request.limit_n, request.n_bins, request.va_pct,
        request.barw_val, request.src_val, request.to_ts, request.from_ts,
        request.want_today, request.want_sessions, request.want_fine,
    )


def _dispatch_zp(request: _MPRequest) -> tuple[int, dict[str, Any]]:
    """src=zp の dispatch（_handle_zp へ委譲）。to_ts が単一時計（now=to・ISSUE-129）。"""
    return _handle_zp(
        request.ref, request.timeframe, request.limit_n, request.n_bins, request.va_pct,
        request.barw_val, request.to_ts, request.from_ts, request.want_today,
        request.want_sessions,
    )


# src 登録表（唯一の情報源）。順序は許可集合の列挙順＝400 メッセージの byte を固定する。
#   candle=足レンジ TPO・dwell=実ティック滞在秒/セッション認識・m1=生ティック数・
#   zp=超過占有スコア z(p)（Null B 帰無に対する分単位滞在の超過）。
_SOURCE_DESCRIPTORS: tuple[SourceDescriptor, ...] = (
    SourceDescriptor(
        id="candle", atom="足レンジ", metric=None, handler=_dispatch_candle,
    ),
    SourceDescriptor(
        id="dwell", atom="tick滞在秒(セッション認識)", metric="dwell", handler=_dispatch_dwell,
    ),
    SourceDescriptor(
        id="m1", atom="tick数", metric="count", handler=_dispatch_dwell,
    ),
    SourceDescriptor(
        id="zp", atom="超過占有z(p)(分単位滞在/NullB)", metric=None, handler=_dispatch_zp,
    ),
)
# id → 記述子（dispatch 解決用）。挿入順を保持（許可集合の順序と一致）。
_SOURCE_REGISTRY: dict[str, SourceDescriptor] = {d.id: d for d in _SOURCE_DESCRIPTORS}

# 以下 3 つは登録表からの導出値（従来のハードコードと同一値・同一順序＝応答 byte 不変）。
# src ホワイトリスト（許可値以外は 400。'|'.join(_ALLOWED_SRC) が 400 メッセージへ入るため順序を保持）。
_ALLOWED_SRC = tuple(d.id for d in _SOURCE_DESCRIPTORS)
# src → dwell モジュールの metric（dwell=滞在秒・count=生ティック数/セッション非適用。非該当 src は含めない）。
_SRC_METRIC = {d.id: d.metric for d in _SOURCE_DESCRIPTORS if d.metric is not None}
# 応答の atom 表示（UI 用・原子の意味）。
_ATOM = {d.id: d.atom for d in _SOURCE_DESCRIPTORS}
