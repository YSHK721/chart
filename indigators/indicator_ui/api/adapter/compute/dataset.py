"""dataset — datasetRef ホワイトリスト解決と OHLC/candles 供給（§7.3 / §6.3）。

datasetRef 識別子 → 実 CSV パスのホワイトリスト解決を単一定義し、生パス直送・パス
トラバーサルを防ぐ（外から組み立てたパスは解決しない・基本設計 §7.3）。

- ``DATASET_WHITELIST`` : 識別子 → 実 CSV パス（唯一の定義）。
- ``is_known(ref)``     : ホワイトリストに存在するか。
- ``load_dataframe(ref)``: 既存 loader で DataFrame 化（time 列を index に解決・キャッシュ）。
- ``load_candles(ref)`` : candles JSON（``[{time(UNIX秒),open,high,low,close}]``）へ変換。

時刻は **解像度非依存** に ``int(pd.Timestamp(v).timestamp())`` で UNIX 秒へ変換する
（pandas3 で ``astype // 10**9`` は誤り）。既存 loader / 指標 src は read-only。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

from adapter.compute.module_loader import load_module

# workspace ルート（このファイル: api/adapter/compute/ → parents[5] = /workspaces/app）。
_WORKSPACE_ROOT = Path(__file__).resolve().parents[5]

# datasetRef ホワイトリスト（§7.3）。識別子 → 実 CSV パス。生パス直送・パストラバーサルを
# 防ぐため、ここに無いキーはすべて拒否する（外から組み立てたパスは解決しない）。
DATASET_WHITELIST: dict[str, Path] = {
    "sample": _WORKSPACE_ROOT
    / "lightweight-charts-python-main"
    / "examples"
    / "4_line_indicators"
    / "ohlcv.csv",
    # JP225（日経225・Dukascopy E_N225Jap）。marketdata から書き出した日足 CSV
    # （date,open,high,low,close・外れ値補正済み）。生成: indicator_ui/tools/export_jp225_csv.py。
    "jp225": _WORKSPACE_ROOT / "marketdata" / "data" / "jp225_daily.csv",
    # JP225 1分足（原子データ）。全時間足はこの 1 分足を resample して生成する
    # （date(UTC %Y-%m-%d %H:%M:%S),open,high,low,close,volume）。生成: tools/export_jp225_m1.py。
    "jp225_m1": _WORKSPACE_ROOT / "marketdata" / "data" / "jp225_m1.csv",
}

# サンプル CSV の時刻列（解像度非依存に UNIX 秒へ変換する起点）。
_SAMPLE_TIME_COLUMN = "date"

# candles の必須 OHLC 列（小文字正規化後）。
_OHLC_COLUMNS = ("open", "high", "low", "close")

# 時間足コード → pandas resample ルール（§チャート表示時間選択・1 分足原子）。
# 全時間足は 1 分足（原子）を resample して生成する。"1m" は無変換（None＝原子そのもの）。
# pandas 3 系では分/時は "5min"/"1h"、週は取引週末（金曜ラベル）、月末は "ME"（旧 "M" は廃止）。
# ここに無いキーはすべて拒否する（is_known_timeframe）。日足ベース dataset（sample/jp225）でも
# "1D"/"1W"/"1M" は冪等に機能する（同日 1 本の再集計は値不変）。日足未満は日足 dataset には無効
# （フロントが dataset 別に提示足を制限する）。
TIMEFRAME_RULES: dict[str, str | None] = {
    "1m": None,
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1D": "1D",
    "1W": "W-FRI",
    "1M": "ME",
}

# OHLC 集約規則（再集計時の列別 agg）。volume は合算、その他（OHLC 外）は最終値。
_OHLC_AGG = {"open": "first", "high": "max", "low": "min", "close": "last"}
_VOLUME_NAMES = ("volume", "vol")


def is_known(ref: Any) -> bool:
    """datasetRef がホワイトリストに存在するか（未知・生パスは False）。"""
    return ref in DATASET_WHITELIST


def is_known_timeframe(timeframe: Any) -> bool:
    """timeframe がホワイトリスト（1m..1M）に存在するか（未知は False）。"""
    return timeframe in TIMEFRAME_RULES


def resample_ohlc(df: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    """DataFrame を指定 pandas rule で OHLC 再集計する（§チャート表示時間選択・1 分足原子）。

    ``rule=None`` は無変換で同一 DataFrame を返す（原子＝1 分足そのもの）。それ以外は
    resample し、open=最初/high=最大/low=最小/close=最終、volume=合算、その他列=最終値で
    集約する。取引の無い期間（OHLC が NaN の行）は除去する（resample は連続区間を埋めるため、
    休場区間の空行を落とす）。
    """
    if rule is None:
        return df
    agg: dict[Any, str] = {}
    for col in df.columns:
        lc = str(col).lower()
        if lc in _OHLC_AGG:
            agg[col] = _OHLC_AGG[lc]
        elif lc in _VOLUME_NAMES:
            agg[col] = "sum"
        else:
            agg[col] = "last"
    resampled = df.resample(rule).agg(agg)
    lower_map = {str(c).lower(): c for c in df.columns}
    ohlc_cols = [lower_map[k] for k in _OHLC_COLUMNS if k in lower_map]
    return resampled.dropna(subset=ohlc_cols)


def _to_unix_seconds(value: Any) -> int:
    """時刻値を UNIX 秒（整数・解像度非依存）へ変換する（fake_chart と同一式）。"""
    return int(pd.Timestamp(value).timestamp())


# CSV mtime 検知キャッシュ（最内 _load_base_dataframe の1段のみ）。
#   ref → (mtime_ns, DataFrame)。CSV 読込のみをキャッシュし、mtime 変化で当該 ref の
#   旧エントリを破棄して再読込する（ライブ更新で CSV が上書きされたら全段へ貫通）。
#   有界化: ref ごとに最新 mtime の 1 エントリのみ保持する（旧 mtime は上書きで消える）。
_BASE_CACHE: dict[str, tuple[int, pd.DataFrame]] = {}

# resample 結果キャッシュ（load_dataframe の1段・性能最適化 A’）。
#   キー (ref, timeframe) → 値 (mtime, resampled_df)。(ref, timeframe) ごとに最新 mtime の
#   1 エントリのみ保持する（上書き＝有界・plain dict）。★P-2: functools.lru_cache を
#   (ref,tf,mtime) キーで使わない（mtime ごとにエントリが残り maxsize=None でリークする＝
#   先行修正が潰した欠陥の再混入）。plain dict 上書き方式のみ。
#   ★P-1: キー mtime は _csv_mtime(ref) を独立に呼ばず、base が実際に焼いた世代 mtime
#   （_baked_mtime）を単一真実源にする。torn-read 時 base は旧 df を返し _BASE_CACHE の
#   mtime を据え置くため、_csv_mtime（進行済の新 mtime）を使うと古い resample を新 mtime で
#   焼き、base 復帰後も恒久 stale 化する。
_RESAMPLE_CACHE: dict[tuple[str, str | None], tuple[int | None, pd.DataFrame]] = {}


def _baked_mtime(ref: str) -> int | None:
    """base が実際に焼いた世代 mtime（_BASE_CACHE[ref][0]）を返す（無ければ None）。

    ★P-1: resample キャッシュのキー mtime の単一真実源。_csv_mtime(ref) を独立に呼ぶと
    torn-read 時に base の据え置き世代と乖離し恒久 stale 化するため、base が保持する世代
    mtime をそのまま使う（_BASE_CACHE 内部表現への直接依存をこのヘルパに局所化する）。
    """
    cached = _BASE_CACHE.get(ref)
    return cached[0] if cached is not None else None


def _csv_mtime(ref: str) -> int | None:
    """ref の実 CSV の最終更新時刻（ns・整数）を返す（mtime キャッシュキー）。

    CSV が存在しない場合は None を返す（キャッシュ済みなら直前結果を維持するため・
    再読込判定が None で新規読込へ落ちて FileNotFoundError になるのは未キャッシュ時のみ）。
    """
    try:
        return DATASET_WHITELIST[ref].stat().st_mtime_ns
    except OSError:
        return None


def _load_base_dataframe(ref: str) -> pd.DataFrame:
    """ホワイトリスト解決済みキーの原子 CSV を DataFrame 化する（resample 前・mtime キャッシュ）。

    既存 loader を再利用し、time 列（date）を index へ解決する（line 系指標の時刻解決）。
    CSV の mtime が前回と同一ならキャッシュ DataFrame を返す。mtime 変化（CSV 上書き）時は
    再読込して当該 ref の 1 エントリを置換する（旧 mtime のエントリは保持しない＝有界）。
    """
    mtime = _csv_mtime(ref)
    cached = _BASE_CACHE.get(ref)
    if cached is not None and (mtime is None or mtime == cached[0]):
        # mtime 不変、または取得不能（CSV 削除）ならキャッシュヒット（再読込しない）。
        return cached[1]
    loader = _load_loader()
    try:
        df = loader.load_ohlc_csv(
            str(DATASET_WHITELIST[ref]), time_column=_SAMPLE_TIME_COLUMN
        )
    except (OSError, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError):
        # ライブ更新の writer が CSV を非アトミックに追記中だと、末尾行が途中の torn-read に
        # なり pandas が解析失敗しうる（🟡-1）。失敗をキャッシュへ焼かず、直前の良好 df が
        # あればそれを返す（最大 ~1 ポーリング分 stale だが不正データを配信しない）。次の
        # mtime 変化で正常読込へ復帰する。良好キャッシュが無ければ送出する（隠蔽しない）。
        if cached is not None:
            logger.warning("CSV 読込に失敗（torn-read 等）。直前のキャッシュを維持: %s", ref)
            return cached[1]
        raise
    _BASE_CACHE[ref] = (_csv_mtime(ref), df)
    return df


def load_dataframe(ref: str, timeframe: str | None = None) -> pd.DataFrame:
    """ホワイトリスト解決済みキーの DataFrame を指定時間足へ再集計して返す（無キャッシュ純変換）。

    キャッシュは最内 ``_load_base_dataframe``（mtime 検知）の1段のみ。本関数は毎回 base を
    取得し resample する純変換へ降格（mtime 無効化を全段へ貫通させるため・公開シグネチャ不変）。
    ``timeframe=None`` は原子（再集計なし）をそのまま返す（既存挙動・後方互換）。指定時は
    ``TIMEFRAME_RULES`` の rule で resample する。未知 timeframe は呼び出し側（controller/server）
    が事前に ``is_known_timeframe`` で拒否する前提（ここでは rule 解決のみ）。
    """
    base = _load_base_dataframe(ref)
    if timeframe is None:
        # 原子（1m）は resample せず base を直接返す（resample キャッシュ非経由・従来どおり）。
        return base
    # ★P-1: base が実際に焼いた世代 mtime を resample キャッシュキーの単一真実源にする
    #   （_csv_mtime を独立に呼ばない。torn-read 時の恒久 stale 化を防ぐ）。
    mtime = _baked_mtime(ref)
    key = (ref, timeframe)
    cached = _RESAMPLE_CACHE.get(key)
    if cached is not None and (mtime is None or mtime == cached[0]):
        # mtime 不変、または取得不能（CSV 削除）なら直前の resample 結果を返す（再 resample しない）。
        return cached[1]
    resampled = resample_ohlc(base, TIMEFRAME_RULES.get(timeframe))
    # (ref, timeframe) ごと最新 mtime の 1 エントリのみ保持（上書き＝有界・plain dict）。
    _RESAMPLE_CACHE[key] = (mtime, resampled)
    return resampled


def load_candles(
    ref: str, timeframe: str | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    """ホワイトリスト解決済みキーを candles JSON へ変換する（§6.3・lightweight-charts 形）。

    無キャッシュ純変換（mtime キャッシュは最内 ``_load_base_dataframe`` の1段のみ）。毎回
    base を取得→resample/format するため CSV 更新（mtime 変化）が即座に反映される。

    Args:
        ref: datasetRef（ホワイトリスト済み）。
        timeframe: 時間足コード（None=原子）。指定時は resample 後に変換する。
        limit: 直近 N 本に制限する（None=全件）。1 分足原子の全期間（数百万点）を直接
            配信しないための表示範囲制限（§配信設計: リサンプル＋直近 N 本）。

    Returns:
        ``[{time: UNIX秒, open, high, low, close}, ...]``（time 昇順・直近 limit 本）。
    """
    df = load_dataframe(ref, timeframe)
    if limit is not None and limit > 0:
        df = df.tail(limit)
    lower_map = {str(c).lower(): c for c in df.columns}
    cols = {k: lower_map[k] for k in _OHLC_COLUMNS}
    candles: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        candles.append(
            {
                "time": _to_unix_seconds(idx),
                "open": float(row[cols["open"]]),
                "high": float(row[cols["high"]]),
                "low": float(row[cols["low"]]),
                "close": float(row[cols["close"]]),
            }
        )
    return candles


@lru_cache(maxsize=None)
def _load_loader():
    """指標 src の loader モジュールを一意名で読み込む（read-only・改変しない）。

    importlib 機構は ``module_loader.load_module`` に集約（重複解消）。
    """
    pkg_dir = _WORKSPACE_ROOT / "indigators" / "profit_band" / "src"
    return load_module("_dataset_loader_src", pkg_dir / "loader.py")
