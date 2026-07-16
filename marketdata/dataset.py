"""dataset — datasetRef ホワイトリスト解決と OHLC/candles 供給（§7.3 / §6.3）。

datasetRef 識別子 → 実 CSV パスのホワイトリスト解決を単一定義し、生パス直送・パス
トラバーサルを防ぐ（外から組み立てたパスは解決しない・基本設計 §7.3）。

- ``DATASET_WHITELIST`` : 識別子 → 実 CSV パス（唯一の定義）。
- ``is_known(ref)``     : ホワイトリストに存在するか。
- ``load_dataframe(ref)``: 既存 loader で DataFrame 化（time 列を index に解決・キャッシュ）。
- ``load_candles(ref)`` : candles JSON（``[{time(UNIX秒),open,high,low,close}]``）へ変換。

時刻は **解像度非依存** に ``int(pd.Timestamp(v).timestamp())`` で UNIX 秒へ変換する
（pandas3 で ``astype // 10**9`` は誤り）。既存 loader / 指標 src は read-only。

依存方向（厳守）: marketdata は最下層＝indicator_ui / simulator / MP / indigators を一切 import
しない（逆依存ゼロ）。CSV ローダは marketdata.ohlc_csv_loader（byte 一致移設）を直接使い、
module_loader 経由の profit_band 到達を断つ。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

from marketdata import rollup_store, tail_reader

# datasetRef 台帳は marketdata.dataset_registry の記述子レジストリ（唯一源・ISSUE-094 🟡-9）から
# 導出する。従来 4 断片（DATASET_WHITELIST・_OUTLIER_CLAMP_REFS_SET・_ROLLUP_REFS・
# tf_meta.TICK_REFS）は同一 ref 台帳の分割で、新銘柄追加時に整合が必要だった。実 CSV パスの解決
# （DATA_DIR 単一基点・workspace ルート）は registry 側に集約した。
from marketdata import dataset_registry

# datasetRef ホワイトリスト（§7.3）。識別子 → 実 CSV パス。生パス直送・パストラバーサルを
# 防ぐため、ここに無いキーはすべて拒否する（外から組み立てたパスは解決しない）。registry から
# 導出した新規 mutable dict（monkeypatch.setitem で一時 ref を追加できる・利用側は無変更）。
DATASET_WHITELIST: dict[str, Path] = dataset_registry.whitelist()

# サンプル CSV の時刻列（解像度非依存に UNIX 秒へ変換する起点）。
_SAMPLE_TIME_COLUMN = "date"

# candles の必須 OHLC 列（小文字正規化後）。
_OHLC_COLUMNS = ("open", "high", "low", "close")

# 読み取り時 外れ値バー補正（per-bar OHLC クランプ）の許容相対乖離（既定 0.3=±30%）。
# 閾値の唯一の規約源は marketdata.outlier_policy.OUTLIER_THRESHOLD（ISSUE-094 🔴-3・書込側
# cleaning と単一化）。旧名 OUTLIER_CLAMP_THRESHOLD は再エクスポートで温存する。
from marketdata import outlier_policy

OUTLIER_CLAMP_THRESHOLD = outlier_policy.OUTLIER_THRESHOLD

# 外れ値クランプを適用する ref（実市場の JP225 系のみ）。sample 等の合成データセットは
# 対象外（正常な ±30% 超ヒゲを持つ golden を壊さないため・読み取り時補正は市場データ限定）。
# registry から導出した新規 mutable dict（monkeypatch.setitem で一時追加できる）。
_OUTLIER_CLAMP_REFS_SET: dict[str, bool] = dataset_registry.clamp_refs()


def _clamp_outlier_bars(df: pd.DataFrame, ref: str) -> pd.DataFrame:
    """各行(バー)の OHLC を読み取り時クランプして返す（供給側 ref ゲート＋エンベロープ委譲）。

    intraday の atomic 不良値（例 jp225_tick 2025-08-26 の low ~15,099）が集約バーの
    安値を異常に引き下げる問題を、返却直前に補正する。ソース（CSV/ロールアップ生成物）は
    改変せず、補正が必要なときのみコピー上で書き換える（不変・副作用なし）。

    「どの ref を補正対象とするか」（``_OUTLIER_CLAMP_REFS_SET`` の実市場 ref 限定）は供給側の
    ポリシーとして本関数が担い、エンベロープ判定/補正の式（min/max(open,close) 基準・±30%）は
    :func:`marketdata.outlier_policy.clamp_ohlc_envelope`（serving 戦略・唯一の定義）へ委譲する
    （ISSUE-094 🔴-3: 書込側 cleaning と閾値を単一化・式は 2 戦略として同居）。非対象 ref は素通し。
    """
    if ref not in _OUTLIER_CLAMP_REFS_SET:
        return df
    return outlier_policy.clamp_ohlc_envelope(df, threshold=OUTLIER_CLAMP_THRESHOLD)

# resample 規則源は marketdata.resample（enabler③・Sd 後の単一基点と同様に唯一化）。
# dataset は薄い再エクスポートへ降格し、resample_ohlc / TIMEFRAME_RULES / is_known_timeframe を
# marketdata から再公開する（既存 import 元の後方互換維持）。規則の二重実装を禁ずる（§4）。
from marketdata.resample import (  # noqa: E402  (再エクスポート)
    TIMEFRAME_RULES,
    is_known_timeframe,
    resample_ohlc,
)

# 1 分足原子を全ロードせず末尾だけ読む datasetRef（メモリ有界化・D-2）。1m は tail_reader、
# 上位足は事前生成のロールアップ CSV（rollup_store）から読む。それ以外の ref（sample/jp225 日足等・
# 小データ）は従来経路（_load_base_dataframe + resample_ohlc）据置。
_ROLLUP_REFS = dataset_registry.rollup_refs()
# 1m（原子）tail の安全上限（D-2）。表示 limit + 指標ルックバックぶんに十分な有界行数。
# 1m 全件 tail（4.5M 行）で OOM を復活させないための上限（全件読みではない有限値）。
_ATOMIC_TAIL_LOOKBACK_ROWS = 50_000


def is_known(ref: Any) -> bool:
    """datasetRef がホワイトリストに存在するか（未知・生パスは False）。"""
    return ref in DATASET_WHITELIST


# is_known_timeframe / resample_ohlc / TIMEFRAME_RULES は marketdata.resample から再エクスポート
# （ファイル冒頭の import 参照）。dataset 固有の規則実装は持たない（規則源は marketdata・§4）。


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

    ★メモリ有界化（D-2）: ``jp225_m1`` は 1 分足原子（4.5M 行 / 284MB）を二度と全ロードしない。
    1m（None/'1m'）は末尾安全上限ぶんを ``tail_reader.read_tail`` で逆シーク読み、上位足（5m..1M）は
    事前生成のロールアップ CSV を ``rollup_store.read`` で読む。それ以外の ref（sample/jp225 日足等・
    小データ）は従来経路（base + resample）据置（A' resample キャッシュも sample 経路のみ通る・D-3）。
    """
    # ★読み取り時 外れ値バー補正（_clamp_outlier_bars）は全返却経路の最終返却前に一様適用する
    #   （原子1m / rollup_store / resample のいずれも通過）。市場 ref 以外・正常バーは no-op。
    #   キャッシュには生の resample 結果を保存し、返却時にクランプする（mtime 無効化挙動は不変）。
    if ref in _ROLLUP_REFS:
        if timeframe in (None, "1m"):
            # 1m 原子: 末尾安全上限ぶんだけ逆シークで読む（全件 tail で OOM 復活させない・D-2）。
            df = tail_reader.read_tail(DATASET_WHITELIST[ref], _ATOMIC_TAIL_LOOKBACK_ROWS)
        else:
            # 上位足: 事前生成ロールアップ CSV（mtime キャッシュ + torn-read フォールバック）から読む。
            df = rollup_store.read(ref, timeframe)
        return _clamp_outlier_bars(df, ref)

    base = _load_base_dataframe(ref)
    if timeframe is None:
        # 原子（1m）は resample せず base を直接返す（resample キャッシュ非経由・従来どおり）。
        return _clamp_outlier_bars(base, ref)
    # ★P-1: base が実際に焼いた世代 mtime を resample キャッシュキーの単一真実源にする
    #   （_csv_mtime を独立に呼ばない。torn-read 時の恒久 stale 化を防ぐ）。
    mtime = _baked_mtime(ref)
    key = (ref, timeframe)
    cached = _RESAMPLE_CACHE.get(key)
    if cached is not None and (mtime is None or mtime == cached[0]):
        # mtime 不変、または取得不能（CSV 削除）なら直前の resample 結果を返す（再 resample しない）。
        return _clamp_outlier_bars(cached[1], ref)
    resampled = resample_ohlc(base, TIMEFRAME_RULES.get(timeframe))
    # (ref, timeframe) ごと最新 mtime の 1 エントリのみ保持（上書き＝有界・plain dict）。
    #   キャッシュは生（未クランプ）を保存し、返却時にクランプする（mtime 無効化挙動を変えない）。
    _RESAMPLE_CACHE[key] = (mtime, resampled)
    return _clamp_outlier_bars(resampled, ref)


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
    """OHLC CSV ローダ（marketdata.ohlc_csv_loader）を返す（module_loader/profit_band 非経由）。

    旧実装は module_loader 経由で ``indigators/profit_band/src/loader.py`` を借用していたが、
    marketdata→indigators の逆依存を断つため、実体を **byte 一致**で移した
    marketdata.ohlc_csv_loader を直接使う。呼び出し面 ``loader.load_ohlc_csv(path, time_column=...)``
    は不変（返す module は ``load_ohlc_csv`` を持つ）。既存 loader ロジックは read-only（改変しない）。
    """
    from marketdata import ohlc_csv_loader
    return ohlc_csv_loader
