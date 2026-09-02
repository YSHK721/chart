#!/usr/bin/env python3
"""marketdata.tick_m1 — 生ティック parquet → M1 原子 OHLC CSV（上位足ロールアップの素材）。

Dukascopy 生ティック（日別 parquet）を mid=(bid+ask)/2 基準・UTC で 1 分足へ集計し、
``<ref>_m1.csv``（``date,open,high,low,close,volume`` 形式・:mod:`marketdata.rollup` 互換）を
出力する。以降の上位足（5m/1h/1D …）は :mod:`marketdata.rollup`（:mod:`marketdata.resample`
の規則）が本 M1 を素材に生成する。これによりチャートの足も足内更新も「同じティック
（mid・UTC）」由来となり、書き変わりなく整合する。

責務分離（重要）:
    本モジュールは **ticks → M1（原子）の素材生成**のみを担う。上位足ロールアップ生成は
    :mod:`marketdata.rollup` の責務であり本モジュールは行わない（rollup を逆 import しない）。

価格・volume の意味:
    - price の既定は mid=(bid+ask)/2（約定値を持たない quote feed のため・``ingest_ticks`` の
      last=mid 規約と整合する mid 採用）。
    - 価格基準は :func:`ticks_to_m1` の ``price_basis`` で選べる（:data:`PRICE_BASIS_MID` /
      :data:`PRICE_BASIS_BID`）。既定は mid であり、既存の全呼出は 1 バイトも挙動が変わらない。
      bid は MT5 端末チャートが描いている系列である（依頼者裁定 2026-09-02）。**どの ref が
      どちらを使うかは本モジュールが決めない**（供給側が渡す・OCP）。
    - volume はその 1 分の **ティック数**（``size``・float）。出来高ではなく更新密度を表す。

データ保全（重要）:
    物理パスは :data:`marketdata.paths.DATA_DIR` に一本化する（ハードコード禁止）。ティックは
    ``DATA_DIR/ticks/YYYY/MM/DD/<symbol>_ticks.parquet``、出力は ``DATA_DIR/<ref>_m1.csv``。
    既存 candle CSV（``jp225_m1.csv`` 等）には触れず、新規 ref を新規出力するのみ
    （読み取り＋新規追加・既存データへ波及させない）。

依存方向: 本モジュールは pandas と marketdata 内の下位部品
（:mod:`marketdata.paths` / :mod:`marketdata.outlier_policy` / :mod:`marketdata.csv_schema` /
:mod:`marketdata.tail_reader` / :mod:`marketdata.keep_last`）にのみ依存する（indicator_ui を逆 import しない・
marketdata の循環依存禁止）。

この宣言は ``marketdata/tests/test_module_dependency_declarations.py`` が **AST 走査で強制**する
（関数内の遅延 import も対象）。かつて本 docstring は「pandas + paths にのみ依存」と述べていたが
実際は 3 モジュールを追加で import しており、宣言だけが事実と食い違ったまま残っていた（ISSUE-262）。
依存を増やすときは本 docstring と当該テストの許可表を**同時に**更新する。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

import pandas as pd

from marketdata import keep_last as _keep_last
from marketdata import outlier_policy
from marketdata.paths import DATA_DIR

# ロールアップ互換の M1 CSV 列・date 書式は marketdata.csv_schema が唯一の規則源
# （旧: rollup._HEADER / _DATE_FMT と手動同期）。旧属性名は import 共有で温存する。
from marketdata import csv_schema as _csv_schema

_HEADER = _csv_schema.HEADER
_OHLCV_COLUMNS = _csv_schema.OHLCV_COLUMNS  # _HEADER から date を除いた値列。
_DATE_FMT = _csv_schema.DATE_FMT
# 集計に要する生ティックの必須列（ingest.RAW_COLUMNS の price 部分集合）。
_TICK_COLUMNS = ["timestamp", "bidPrice", "askPrice"]
# 価格基準（price basis）— 生ティックのどの気配を「価格」とするか。
#   既定は mid（従来の唯一の規則）。bid は MT5 端末チャートが描いている系列であり、
#   同じティックから mid で M1 を作ると端末表示と系統的にずれる（ISSUE.md 段階 0 実測 T5:
#   中央値 duka(mid) - mt5(bid) = +6.97・chart_mode=0 と整合／依頼者裁定 2026-09-02）。
#   **どの ref がどちらを使うかは本モジュールが決めない**（供給側が渡す・OCP）。
PRICE_BASIS_MID = "mid"
PRICE_BASIS_BID = "bid"
_PRICE_BASES = (PRICE_BASIS_MID, PRICE_BASIS_BID)
# 既定の銘柄・出力 ref（試作 prep_tick_rollup と一致: <ref>_m1.csv = jp225_tick_m1.csv）。
_DEFAULT_SYMBOL = "JP225"
_DEFAULT_REF = "jp225_tick"

# M1 バー date の実装可能下限（ISSUE-455 再発防止・意味的健全性の下界）。
#   これより前の date は「価格をナノ秒と誤解釈した 1970-01-01」や破損値であり実データたり得ない
#   （実測の最古 JP225 tick は 2012-06-14）。tools 層の _DEFAULT_FULL_START(2012-06-14) を直接
#   import すると marketdata → tools の逆依存になる（依存方向厳守・循環禁止）ため、marketdata 内に
#   十分な余裕を持つ下界を定義する（エポック近傍を弾き、正規データは 1 件も落とさない）。
_M1_MIN_PLAUSIBLE_DATE = pd.Timestamp("2000-01-01")
# ref の許容文字（パス区切り・".." を排除し DATA_DIR 外書込／既存データ破壊を防ぐ）。
_REF_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_ref(ref: str) -> None:
    """``ref`` を単純なファイル名トークンに限定する（fail-fast・データ保全）。

    パス区切りや ``..`` を含む値は ``<ref>_m1.csv`` を介して DATA_DIR 外への書込や既存 CSV の
    上書き（破壊）を招くため拒否する。
    """
    if not isinstance(ref, str) or not _REF_RE.match(ref):
        raise ValueError(
            f"ref は [A-Za-z0-9_-] のみ可: {ref!r}（パス区切り・'..' を含めない・データ保全）。"
        )


def ts_and_mid(ticks: pd.DataFrame) -> "tuple[pd.Series, pd.Series]":
    """生ティック frame から ``(timestamp(naive UTC), mid)`` を返す **mid/tz 規則の公開面**。

    実体は :func:`_ts_and_mid`。同一規則を外部（tools・market_profile gateway）が再実装して
    いたため公開名を与えた（ISSUE-262）。mid の定義を変えるときはここ 1 箇所を変える。
    """
    return _ts_and_mid(ticks)


def _validate_price_basis(price_basis: str) -> str:
    """価格基準を既知の値に限定して返す（fail-fast・黙って既定へ落ちない）。

    未知の綴りを既定（mid）へ縮退させると、bid のつもりで mid の系列を作り続けても誰も
    気付かない。出力は正しく見えるので状態検証でも落ちない。ここで止める。
    """
    if price_basis not in _PRICE_BASES:
        raise ValueError(
            f"price_basis は {_PRICE_BASES} のいずれかです: {price_basis!r}（既定は"
            f" {PRICE_BASIS_MID!r}）。"
        )
    return price_basis


def _price_series(ticks: pd.DataFrame, price_basis: str) -> pd.Series:
    """生ティック frame から**価格系列**を作る（価格の定義の単一規則源）。

    bid 基準では ask 列を**読まない**。読んで捨てれば「作ってから捨てる計算」になり、
    出力は正しいままなので状態検証では原理的に落ちない（絶対命令 2026-08-28・ISSUE-450 と同型）。
    その不在は ``marketdata/tests/test_tick_m1_price_basis.py`` の列アクセス Spy が固定する。
    """
    if _validate_price_basis(price_basis) == PRICE_BASIS_BID:
        return ticks["bidPrice"].astype("float64")
    return (ticks["bidPrice"].astype("float64") + ticks["askPrice"].astype("float64")) / 2.0


def _ts_and_price(
    ticks: pd.DataFrame, *, price_basis: str = PRICE_BASIS_MID
) -> "tuple[pd.Series, pd.Series]":
    """生ティック frame から ``(timestamp(naive UTC), price)`` を返す共通前処理（tz/価格の単一規則源）。

    timestamp は tz-aware なら UTC 揃え後に tz を剥がし naive datetime64 へ（全 UTC＝値不変）。
    価格は :func:`_price_series`（``price_basis`` 既定 mid）。:func:`ticks_to_m1`（M1 集計）と
    :func:`forming_bar_from_ticks`（形成中バー）が共有し、「同一ソース＝書き変わり無し」を
    構造で保証する（規則の二重定義を避ける）。
    """
    ts = pd.to_datetime(ticks["timestamp"])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    return ts, _price_series(ticks, price_basis)


def _ts_and_mid(ticks: pd.DataFrame) -> "tuple[pd.Series, pd.Series]":
    """生ティック frame から ``(timestamp(naive UTC), mid)`` を返す（mid 固定の既存面）。

    実体は :func:`_ts_and_price`（既定 mid）。mid 以外の基準を足しても本関数の意味は
    変わらない（既存呼出は 1 バイトも挙動が変わらない）。
    """
    return _ts_and_price(ticks)


def ticks_to_m1(ticks: pd.DataFrame, *, price_basis: str = PRICE_BASIS_MID) -> pd.DataFrame:
    """生ティック DataFrame を M1 OHLC（``price_basis`` 基準・UTC 分床）へ集計する純粋関数。

    ``price_basis``（既定 :data:`PRICE_BASIS_MID`）が価格の定義を選ぶ**唯一の拡張点**である。
    既定のままなら従来と 1 バイトも変わらない（既存 ref は無改変）。:data:`PRICE_BASIS_BID` は
    MT5 端末チャートと同じ bid 系列を使う（依頼者裁定 2026-09-02）。どの ref がどちらを使うかは
    本関数が決めない（供給側が渡す・OCP）。

    入力は ``timestamp``（tz-aware/naive いずれも可・tz-aware は UTC へ揃える）, ``bidPrice``,
    ``askPrice`` 列を持つ。選ばれた基準の価格を price とし、UTC naive の分床（``floor("min")``）で
    groupby して open=最初/high=最大/low=最小/close=最終、volume=その分のティック数（float）を
    返す。open/close を時刻順に確定させるため、集計前に ``timestamp`` 昇順へ安定ソートする。

    戻り値は date を index（名前は "date"・``DatetimeIndex`` 昇順）に持つ OHLCV DataFrame。
    入力が空なら空（列のみ）を返す。必須列を欠く場合は :class:`ValueError`（fail-fast）。
    本関数は**純粋な集計のみ**を担う（外れ分バーの除去は :func:`_clean_m1_day` ＝ CSV 素材化
    経路の責務・本関数は行除去しない）。
    """
    missing = [c for c in _TICK_COLUMNS if c not in ticks.columns]
    if missing:
        raise ValueError(
            f"tick frame に必須列がありません: {missing}（必須 {_TICK_COLUMNS}）。"
        )
    _validate_price_basis(price_basis)  # 空入力でも未知の基準は通さない（fail-fast）。
    if ticks.empty:
        empty_idx = pd.DatetimeIndex([], name="date")
        return pd.DataFrame(
            {c: pd.Series(dtype="float64")
             for c in ("open", "high", "low", "close", "volume", "up", "dn")},
            index=empty_idx,
        )

    ts, price = _ts_and_price(ticks, price_basis=price_basis)

    # 時刻順を保証してから分床で groupby（open=最初/close=最終を時刻基準で確定）。
    work = pd.DataFrame({"ts": ts.to_numpy(), "price": price.to_numpy()})
    work = work.sort_values("ts", kind="stable", ignore_index=True)
    work["date"] = work["ts"].dt.floor("min")
    # 方向内訳（up/dn）: 直前ティックとの価格差の符号を **その分バーの中で** 取る。
    #   価格は ``price_basis`` が選んだ系列そのものである（表示される足が bid なのに方向内訳
    #   だけ mid 由来、という食い違いを作らない）。
    #   分をまたいで比べない理由は チャンク独立性の契約: 本関数は日 parquet ごとに呼ばれ、結果を
    #   concat して全体とする（tests/test_tick_m1 の per-day concat == whole）。前の分／前の日の
    #   最終ティックを参照すると、どこで切って処理したかで値が変わり、この契約が壊れる。
    #   その代償として各分の先頭ティックは方向を持たず、up+dn は volume より「分数」だけ小さい。
    #   実測（jp225_tick）で等値ティックは 0.0%。等値は up/dn のどちらにも数えない。
    diff = work.groupby("date", sort=False)["price"].diff()
    work["up"] = (diff > 0).astype("float64")
    work["dn"] = (diff < 0).astype("float64")
    g = work.groupby("date", sort=True)["price"]
    gd = work.groupby("date", sort=True)
    m1 = pd.DataFrame(
        {
            "open": g.first(),
            "high": g.max(),
            "low": g.min(),
            "close": g.last(),
            "volume": g.size().astype("float64"),  # その 1 分のティック数。
            "up": gd["up"].sum(),                  # うち価格が上がったティック数。
            "dn": gd["dn"].sum(),                  # うち価格が下がったティック数。
        }
    )
    m1.index.name = "date"
    return m1


def _clean_m1_day(m1_day: pd.DataFrame) -> pd.DataFrame:
    """日別 M1 から配信欠損ファントム行を除去する（CSV 素材化経路の単一クリーニング点・ISSUE-107）。

    日内 close 中央値から OHLC のいずれかが ±30% 超乖離する分バーを
    :func:`marketdata.outlier_policy.repair_day_outliers`（参照実装 proto_server /
    replay_ui `_m1_repair` と同一式）で行ごと除去する。バー内エンベロープ式（open/close 基準・
    serving クランプ）は open/close 自体が不正な連続不良ラン（例 jp225_tick 2025-08-26
    06:34〜09:09 UTC の ~15,100 帯）を補正できないため、M1 素材の生成段で除去し
    全時間足（rollup 含む）へ清浄な素材を供給する。ティック parquet は UTC 日 partition の
    ため日別適用＝全体への日 groupby 適用と同値。正常日は no-op（同一オブジェクト）。
    """
    return outlier_policy.repair_day_outliers(m1_day)


def _dedupe_minutes(m1: pd.DataFrame) -> pd.DataFrame:
    """同一分（date index の重複）を keep-last で 1 行へ畳む（ISSUE-167・冪等・純粋）。

    ``ticks_to_m1`` は 1 つの日 parquet を分 groupby するため単一 parquet 内は一意だが、
    build/append は日別結果を ``pd.concat`` するため、境界分のティックが複数 parquet に分散
    （日 partition 跨ぎ・再取得の重畳等）していると同一分バーが二重に混じる。この重複が
    素材 CSV → /candles(1m 原子) → フロント series へ伝播すると lightweight-charts が
    「厳密増加 time」不変条件違反で candlestick 描画を毎フレーム "Value is null" で落とす
    （実測: 1m 切替で 31 秒フリーズ）。5m 以上は resample が融合するため露見せず 1m のみ発症する。
    本 dedupe を build/append の concat 直後（sort 済み）に適用し、素材段で分一意を保証する
    （後勝ち＝最新集計を採用）。正常データは has_duplicates が偽で同一オブジェクトを返す＝no-op。

    keep-last の規則そのものは :mod:`marketdata.keep_last`（唯一の実体）へ委譲する。かつては
    同じ式が repo 内 5 箇所へ手書き複製されていた（ISSUE-479 F-6）。
    """
    return _keep_last.dedupe_index_keep_last(m1)


def tick_root(data_dir: Any = DATA_DIR) -> Path:
    """ティック parquet の基点（``<DATA_DIR>/ticks``）。"""
    return Path(data_dir) / "ticks"


def m1_csv_path(ref: str = _DEFAULT_REF, data_dir: Any = DATA_DIR) -> Path:
    """M1 出力 CSV の解決パス（``<DATA_DIR>/<ref>_m1.csv``・rollup の ref_prefix と整合）。"""
    return Path(data_dir) / f"{ref}_m1.csv"


def day_parquet_path(day: Any, *, symbol: str = _DEFAULT_SYMBOL, data_dir: Any = DATA_DIR) -> Path:
    """``day`` の日別ティック parquet の正準パスを返す（実在チェックはしない）。

    tick tree レイアウト ``<DATA_DIR>/ticks/YYYY/MM/DD/<symbol>_ticks.parquet`` の単一権威
    （reader: :func:`day_parquet_files` / writer: tools.live_tick_watch が共用し、レイアウト
    変更を本所 1 箇所に閉じる）。

    この「単一権威」は ``marketdata/tests/test_tick_tree_layout_authority.py`` が
    **リポジトリ走査で強制**する（ISSUE-262）。かつて宣言だけがあり、実際は tools 3 本と
    replay adapter がレイアウトを自前で組んでいた。
    """
    d = pd.Timestamp(day)
    return (
        tick_root(data_dir)
        / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
        / f"{symbol}_ticks.parquet"
    )


def day_empty_marker_path(day: Any, *, symbol: str = _DEFAULT_SYMBOL,
                          data_dir: Any = DATA_DIR) -> Path:
    """``day`` の「取得したがティック 0 件」マーカー（``<symbol>_ticks.empty``）の正準パス。

    parquet と同じ tick tree に属するため、レイアウト権威は本モジュールに閉じる（ISSUE-262）。
    かつて ``.empty`` の名前は 4 箇所（tools 2・simulator 1・with_suffix 導出 1）に散っていた。
    """
    return day_parquet_path(day, symbol=symbol, data_dir=data_dir).with_suffix(".empty")


def day_parquet_name(symbol: str = _DEFAULT_SYMBOL) -> str:
    """日別ティック parquet のファイル名（tick tree レイアウトの一部・単一権威）。"""
    return f"{symbol}_ticks.parquet"


def day_parquet_files(
    start: Any, end: Any, *, symbol: str = _DEFAULT_SYMBOL, data_dir: Any = DATA_DIR
) -> List[Path]:
    """``[start, end]``（両端含む・日次）の実在する日別ティック parquet を昇順で列挙する。

    パスは :func:`day_parquet_path`（レイアウト単一権威）で解決し、実在するものだけ
    返す（欠損日はスキップ・休場日対応）。
    """
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    out: List[Path] = []
    d = s
    while d <= e:
        p = day_parquet_path(d, symbol=symbol, data_dir=data_dir)
        if p.is_file():
            out.append(p)
        d += pd.Timedelta(days=1)
    return out


def _format_m1_for_csv(m1: pd.DataFrame) -> pd.DataFrame:
    """date-index OHLCV を loader 互換 CSV 行へ整形する**単一規則源**（date=``_DATE_FMT`` 文字列）。

    全構築（:func:`_write_m1_csv`）と増分追記（:func:`_append_m1_csv`）の双方がこれを呼び、列射影・
    date 書式・昇順を一致させる（書式の二重定義による drift を防ぐ）。
    """
    # 方向内訳（up/dn）は tick 由来データだけが持つ任意列。持つときだけ末尾へ足す
    #   （持たない CSV は従来と 1 バイトも変わらない・列順の規則源は csv_schema.header_for）。
    cols = [c for c in (*_OHLCV_COLUMNS, *_csv_schema.UPDOWN_COLUMNS) if c in m1.columns]
    out = m1[cols].sort_index().copy()
    out.index = pd.DatetimeIndex(out.index).strftime(_DATE_FMT)
    out.index.name = _HEADER[0]
    return out


def _write_m1_csv(m1: pd.DataFrame, path: Path) -> None:
    """date-index OHLCV を loader 互換 CSV（``_HEADER`` / ``_DATE_FMT``）へ**原子的に**書く。

    rollup._write_rollup_df と同じ tmp→``os.replace`` の原子化で、確定パスを「完全な新 CSV」か
    「旧 CSV」のいずれかに限定する（書き掛けの破損 CSV を残さない）。
    """
    import os
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        out = _format_m1_for_csv(m1)
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            out.to_csv(fh, header=list(out.columns), index_label=_HEADER[0])
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _drop_forming_bars(m1: pd.DataFrame, until: Any) -> pd.DataFrame:
    """``until`` 指定時、``index >= until`` の分バー（形成中）を除外する共通フィルタ。

    ``until=None`` は素通し（従来出力を byte 不変に保つ）。用途は「形成中の分バー
    （``floor(now, "min")`` 以降）を確定値として書き込まない」こと。:func:`build_m1_from_ticks`
    と :func:`append_m1_from_ticks` の双方が共有し、除外規則の二重定義を避ける。
    """
    if until is None:
        return m1
    return m1[m1.index < pd.Timestamp(until)]


def build_m1_from_ticks(
    start: Any,
    end: Any,
    *,
    symbol: str = _DEFAULT_SYMBOL,
    ref: str = _DEFAULT_REF,
    data_dir: Any = DATA_DIR,
    until: Any = None,
    price_basis: str = PRICE_BASIS_MID,
) -> Path:
    """``[start, end]`` の日別ティック parquet を読み、M1 CSV を生成して出力パスを返す。

    対象期間に実在する parquet が 1 つも無ければ :class:`FileNotFoundError`（fail-fast・
    暗黙の空出力を作らない）。出力は ``<data_dir>/<ref>_m1.csv``。

    メモリ有界（marketdata の中核不変条件・rollup と同方針）: 全ティックを一括ロードせず
    **日別 parquet を 1 ファイルずつ** :func:`ticks_to_m1` で M1（数十〜数百倍に縮約）へ集約し、
    小さな日別 M1 のみを連結する。ティック parquet は UTC 日で partition されるため分バーが
    ファイルを跨がず、結果は全件一括集計と**数値同一**（RSS は 1 日分ティックに有界化）。

    ``until``（省略可・:class:`pd.Timestamp` 互換）を指定すると、生成する M1 バーのうち
    ``index >= until`` の行を除外する（用途: 形成中の分バー＝``floor(now, "min")`` 以降を確定値
    として書き込まない）。``until=None``（既定）は従来出力と完全一致（byte 不変）。

    ``price_basis``（既定 :data:`PRICE_BASIS_MID`）は :func:`ticks_to_m1` へそのまま渡す。
    権威（全量）経路も増分経路と同じ基準で回せるようにするためである（片方だけが mid のまま
    だと、日次再構築が表示中の系列を静かに mid へ戻す）。
    """
    _validate_ref(ref)
    files = day_parquet_files(start, end, symbol=symbol, data_dir=data_dir)
    if not files:
        raise FileNotFoundError(
            f"ティック parquet が見つかりません（{start}..{end} / {tick_root(data_dir)} / "
            f"symbol={symbol}）。"
        )
    daily_m1: List[pd.DataFrame] = []
    for p in files:
        m1_day = _clean_m1_day(
            ticks_to_m1(pd.read_parquet(p, columns=_TICK_COLUMNS), price_basis=price_basis)
        )
        if not m1_day.empty:
            daily_m1.append(m1_day)
    if daily_m1:
        m1 = _dedupe_minutes(pd.concat(daily_m1).sort_index())  # ISSUE-167: 境界分の二重を畳む。
    else:
        # parquet は在るが全日空（0 行）。ヘッダのみの空 M1 を出力する。
        m1 = ticks_to_m1(
            pd.DataFrame({c: [] for c in _TICK_COLUMNS}), price_basis=price_basis
        )
    m1 = _drop_forming_bars(m1, until)  # 形成中分バー（>= until）を確定値として書かない。
    out_path = m1_csv_path(ref=ref, data_dir=data_dir)
    _write_m1_csv(m1, out_path)
    return out_path


def _read_last_m1_row(out_path: Any) -> "pd.DataFrame | None":
    """既存 M1 CSV の末尾 1 行（date index・OHLCV 列）を逆シーク読みで返す。不在/空は ``None``。

    :mod:`marketdata.tail_reader` で末尾 1 行のみ読むためメモリ有界（全読みしない）。
    """
    from marketdata import tail_reader

    p = Path(out_path)
    if not p.is_file():
        return None
    tail = tail_reader.read_tail(p, 1)
    return None if tail.empty else tail


def last_m1_date(out_path: Any) -> "pd.Timestamp | None":
    """既存 M1 CSV の最終バーの日時（末尾行）。不在/空（ヘッダのみ）は ``None``。メモリ有界。"""
    tail = _read_last_m1_row(out_path)
    return None if tail is None else pd.Timestamp(tail.index[-1])


def _is_healthy_m1_row(tail: pd.DataFrame) -> bool:
    """末尾 1 行が健全か（date 解釈可・OHLCV 列が揃い NaN を含まない）。

    非原子追記がクラッシュ/ディスクフルで途中失敗すると末尾に torn/部分行（列欠落・NaN）が残りうる。
    これを検出して全構築フォールバック（原子的）で自己修復するための健全性判定。
    """
    last = tail.index[-1]
    if pd.isna(last):
        return False
    # date の実装可能性（ISSUE-455）: エポック近傍（価格をナノ秒解釈した 1970 誤変換）や
    # データ開始前の日は不健全とし、resume ガードが全履歴を再選択する連鎖を断つ。read_tail の
    # 構造検査（列数超過）を潜り抜けた意味的破損に対する多重防御（列数一致でも date だけ壊れる
    # 経路を塞ぐ）。
    if pd.Timestamp(last) < _M1_MIN_PLAUSIBLE_DATE:
        return False
    if any(c not in tail.columns for c in _OHLCV_COLUMNS):
        return False
    return not bool(tail.iloc[-1][_OHLCV_COLUMNS].isna().any())


def _existing_csv_header(path: Path) -> "list[str] | None":
    """既存 CSV の先頭行（ヘッダ）を列名リストで返す。不在/空は ``None``（追記は新規扱い）。

    追記のヘッダ整合契約の照合面。全読みせず先頭 1 行だけ読む（メモリ有界）。
    """
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return None
    with open(p, "r", encoding="utf-8") as fh:
        first = fh.readline()
    first = first.strip()
    return first.split(",") if first else None


def _assert_append_header_matches(path: Path, new_value_columns: "list[str]") -> None:
    """追記行の列（date + 値列）が既存ヘッダと一致することを保証する（ISSUE-455・ラガー遮断）。

    up/dn の任意列が「本体だけ増えてヘッダは据え置き」になると、6 列ヘッダの下に 8 フィールド行が
    積まれ、後続の read_tail が列をずらして date へ価格を入れ 1970 誤読→全再追記を招く。追記の
    入口で列の食い違いを検出したら **拒否（ValueError）** し、呼び出し側の全再構築（原子的・
    ヘッダごと書き直し）へ回すことで、黙って乖離した CSV を育てない。既存ヘッダが無い（新規）
    場合は照合しない。
    """
    existing = _existing_csv_header(path)
    if existing is None:
        return
    expected = [_HEADER[0], *new_value_columns]
    if existing != expected:
        raise ValueError(
            f"CSV ヘッダ不一致（{path}）: 既存ヘッダ {existing} に対し追記行の列は {expected}。"
            "黙って列数の乖離した CSV を育てないため追記を拒否し全再構築へ回す（ISSUE-455）。"
        )


def _append_m1_csv(m1_new: pd.DataFrame, path: Path) -> None:
    """新規 M1 行を既存 CSV の**末尾へ追記**する（ヘッダ無し・``_DATE_FMT``・date 昇順）。

    既存行は読み込まず（メモリ有界）末尾追記のみ行う。呼び出し側が ``m1_new`` の全 index を既存
    最終 date より後に保証するため、追記後も date 昇順（loader 前提）が保たれる。

    原子性（注意・:func:`_write_m1_csv` との非対称）: 末尾追記は tmp→``os.replace`` の原子化を
    持たない（既存 276MB を読み直さない設計＝メモリ有界のため）。クラッシュ時に末尾へ torn 行を
    残しうるが、その torn 行は次回 :func:`append_m1_from_ticks` の :func:`_is_healthy_m1_row`
    検出で全構築フォールバックされ自己修復する（無検出の永続破損を避ける）。

    ISSUE-186（並行読取との競合）: ``DataFrame.to_csv(fh)`` は行を**複数回の write に分けて**
    流し込むため、常駐 watch が追記している最中に読み手（実データ依存テスト・loader）が読むと
    **行の途中**を掴み、無関係なテストが一斉に落ちた（実測: 1 回だけ 13 failed / 405 passed、
    直後から 14 回連続 418 passed）。ここでは CSV 本文を**メモリ上で組み立ててから 1 回の
    ``write`` で流す**ことで、torn 行が観測される時間窓を実質消す。
    完全な保証は読み手側が担う（:func:`marketdata.ohlc_csv_loader` が末尾の不完全行を捨てる）。
    書き手・読み手の**両方**で守るのは、どちらか片方だけでは競合が残るため:
      - 書き手だけ: 単一 write でも巨大バッファは分割されうる
      - 読み手だけ: torn 行が末尾以外に現れる経路（複数追記の交錯）を救えない
    """
    out = _format_m1_for_csv(m1_new)
    _assert_append_header_matches(Path(path), list(out.columns))  # ラガー遮断（ISSUE-455）。
    text = out.to_csv(header=False, index_label=_HEADER[0])
    with open(Path(path), "a", newline="", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()


def append_m1_rows(m1: pd.DataFrame, path: Any) -> int:
    """date-index の M1 バーを loader 互換 CSV の末尾へ**追記**し、書いた行数を返す（ISSUE-447 A-5）。

    :func:`_format_m1_for_csv` を包む**公開の入り口**である。増分供給側
    （``marketdata/mt5_ticks/m1_chain.py``）は当日の M1 を自前で畳んで追記するが、書式
    （列射影・date 書式・昇順）は本モジュールが単一の規則源でなければならない。かつて増分側は
    private の :func:`_format_m1_for_csv` を直接 import していた。private への依存は「呼んで
    よい」と宣言されていない実装詳細への依存であり、権威側が内部を変えた瞬間に黙って壊れる。
    本 API はその依存を恒久的に解消するために足した（A-5 の承認範囲＝**追加 1 個のみ**）。

    :func:`_append_m1_csv` との違い（両者は用途が異なる・重複ではない）:
        あちらは :func:`append_m1_from_ticks` の内部段であり、既存 CSV が実在し末尾が健全で
        あることを呼び出し側が保証した状態でのみ呼ばれる（ヘッダは常に書かない）。本 API は
        供給ループの入り口として**ファイル不在から**呼ばれるため、不在時はヘッダ 1 行を先に
        書く。書式そのものは双方とも :func:`_format_m1_for_csv` に委ねるため、出力は
        :func:`_write_m1_csv`（全構築）と 1 バイト一致する。

    書込は **1 回の ``write``** に閉じる（ISSUE-186）。``to_csv`` にファイルを直接渡すと行が
    複数回の write に分かれ、常駐 watch が追記している最中の読み手が行の途中を掴む。

    空フレームでは**ファイルに触れない**（0 を返す）。新着 0 の周期で書込 0 という不変条件
    （計算量検定 CX-b）を、呼び出し側の分岐ではなく本 API の側で保証する。
    """
    formatted = _format_m1_for_csv(m1)
    if formatted.empty:
        return 0
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = out_path.is_file() and out_path.stat().st_size > 0
    if started:
        _assert_append_header_matches(out_path, list(formatted.columns))  # ラガー遮断（ISSUE-455）。
    text = formatted.to_csv(
        header=False if started else list(formatted.columns), index_label=_HEADER[0]
    )
    with open(out_path, "a", newline="", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
    return len(formatted)


def append_m1_from_ticks(
    start: Any,
    end: Any,
    *,
    symbol: str = _DEFAULT_SYMBOL,
    ref: str = _DEFAULT_REF,
    data_dir: Any = DATA_DIR,
    until: Any = None,
    price_basis: str = PRICE_BASIS_MID,
) -> Path:
    """既存 M1 CSV に「最終バー日以降の不足分」だけを集計して**追記**する（増分・メモリ有界・自己修復）。

    既存 CSV が不在/空、または末尾行が不健全（torn/部分書込み）なら :func:`build_m1_from_ticks`
    （原子的全構築）へフォールバックして自己修復する。健全時は **最終バー日（当日）以降**を再読込し、
    ``index > 最終 date`` の行だけ追記する。ティックは UTC 日で partition され分が日を跨がないため、
    完成済みの最終日は空追記（冪等 no-op）、途中までしか書けていない日は欠損分のみ追記され自己修復する。
    結果は全構築と一致する（過去確定日の再計算は不要）。

    ``until``（省略可・:class:`pd.Timestamp` 互換）を指定すると、追記する M1 バーのうち
    ``index >= until`` の行を除外する（形成中の分バー＝``floor(now, "min")`` 以降を確定値として
    書き込まない）。フォールバック先の :func:`build_m1_from_ticks` へも同じ ``until`` を伝播する。
    ``until=None``（既定）は従来出力と完全一致（byte 不変）。

    前提（重要）: 取得は前方追記（resume）である。過去日への遡及バックフィル（既存最終日より前の
    欠損日を後から追加）は本増分では取り込めない。その場合は :func:`build_m1_from_ticks` で全再構築する。
    """
    _validate_ref(ref)
    out_path = m1_csv_path(ref=ref, data_dir=data_dir)
    try:
        tail = _read_last_m1_row(out_path)
    except ValueError:
        # 既存 CSV の tail が構造破損（列数超過＝ISSUE-455 の 8 列化 tail 等）で read_tail が
        # Fail-Stop した。watch をクラッシュさせず、原子的全構築へフォールバックして自己修復する
        # （症状回避ではなく、壊れた素材を正しい素材で置き換える根本修復）。
        tail = None
    if tail is None or not _is_healthy_m1_row(tail):
        # 初回（M1 不在/空）or 末尾 torn 行 or 構造破損 tail → 原子的全構築で（再）生成し自己修復。
        return build_m1_from_ticks(
            start, end, symbol=symbol, ref=ref, data_dir=data_dir, until=until,
            price_basis=price_basis,
        )

    last_date = pd.Timestamp(tail.index[-1])
    # 最終バー日（当日）から再読込し index > last_date のみ追記する。完成日は冪等 no-op、
    # 部分日は欠損分のみ自己修復（要求 start がそれより後ろならそれを尊重）。
    resume_start = last_date.normalize()
    eff_start = max(resume_start, pd.Timestamp(start))
    files = day_parquet_files(eff_start, end, symbol=symbol, data_dir=data_dir)
    if not files:
        return out_path  # 追記すべき新しい日は無い。

    daily_m1: List[pd.DataFrame] = []
    for p in files:
        m1_day = _clean_m1_day(
            ticks_to_m1(pd.read_parquet(p, columns=_TICK_COLUMNS), price_basis=price_basis)
        )
        if not m1_day.empty:
            daily_m1.append(m1_day)
    if not daily_m1:
        return out_path
    m1_new = _dedupe_minutes(pd.concat(daily_m1).sort_index())  # ISSUE-167: 境界分の二重を畳む。
    m1_new = m1_new[m1_new.index > last_date]  # 厳密に既存最終 date より後のみ追記（重複防止）。
    m1_new = _drop_forming_bars(m1_new, until)  # 形成中分バー（>= until）を確定値として書かない。
    if m1_new.empty:
        return out_path
    try:
        _append_m1_csv(m1_new, out_path)
    except ValueError:
        # 既存ヘッダと追記行の列が食い違う（旧 6 列 CSV に up/dn 付き 8 列を積もうとした等）。
        # 黙って乖離を育てず、原子的全構築でヘッダごと正しく書き直して是正する（ISSUE-455）。
        return build_m1_from_ticks(
            start, end, symbol=symbol, ref=ref, data_dir=data_dir, until=until,
            price_basis=price_basis,
        )
    return out_path


def forming_bar_from_ticks(
    start_unix: int,
    end_unix: int,
    *,
    symbol: str = _DEFAULT_SYMBOL,
    data_dir: Any = DATA_DIR,
) -> "dict | None":
    """``[start_unix, end_unix)`` の実ティックから**形成中バー**（mid OHLCV・1本）を返す。

    ライブの足内更新へ「形成中（in-progress）バー」を供給するための純粋集計。期間内の実ティックを
    mid=(bid+ask)/2 で集計し、open=最初/high=最大/low=最小/close=最終、volume=ティック数、
    ``time``=期間始端（``start_unix``）の 1 本（lightweight-charts 形）を返す。期間内にティックが
    無ければ ``None``。

    引数は UNIX 秒（UTC・整数・半開 ``[start, end)``）。日 partition（``ticks/YYYY/MM/DD``）を跨ぐ
    場合は該当日 parquet を順に読む（通常 intraday は単一日）。メモリ有界（対象期間の日 parquet の
    mid 列のみ・全期間ロードしない）。集計規則（mid・open=最初/close=最終・volume=ティック数）は
    :func:`ticks_to_m1` と一致する（同一ソース由来＝書き変わり無し）。
    """
    s = pd.Timestamp(start_unix, unit="s")  # naive UTC wall time
    e = pd.Timestamp(end_unix, unit="s")
    if e <= s:
        return None
    files = day_parquet_files(s.normalize(), e.normalize(), symbol=symbol, data_dir=data_dir)
    if not files:
        return None
    frames = [pd.read_parquet(p, columns=_TICK_COLUMNS) for p in files]
    ticks = pd.concat(frames, ignore_index=True)
    ts, mid = _ts_and_mid(ticks)
    work = pd.DataFrame({"ts": ts.to_numpy(), "mid": mid.to_numpy()})
    work = work[(work["ts"] >= s) & (work["ts"] < e)].sort_values("ts", kind="stable")
    if work.empty:
        return None
    m = work["mid"]
    return {
        "time": int(start_unix),
        "open": float(m.iloc[0]),
        "high": float(m.max()),
        "low": float(m.min()),
        "close": float(m.iloc[-1]),
        "volume": float(len(m)),
    }


def main(argv: List[str] | None = None) -> None:
    """CLI: ``python -m marketdata.tick_m1 [START] [END] [SYMBOL] [REF]``。

    START 既定 ``2025-01-01``、END 既定は本日（UTC）。試作 prep_tick_rollup の CLI を踏襲する。
    """
    args = sys.argv[1:] if argv is None else list(argv)
    start = args[0] if len(args) > 0 else "2025-01-01"
    end = args[1] if len(args) > 1 else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    symbol = args[2] if len(args) > 2 else _DEFAULT_SYMBOL
    ref = args[3] if len(args) > 3 else _DEFAULT_REF

    files = day_parquet_files(start, end, symbol=symbol)
    print(f"範囲 {start}..{end}  symbol={symbol}  ティック日数: {len(files)}", flush=True)
    if not files:
        print(f"ティック parquet が見つかりません（{tick_root()}）", flush=True)
        return
    out_path = build_m1_from_ticks(start, end, symbol=symbol, ref=ref)
    m1 = pd.read_csv(out_path)
    if len(m1):
        print(
            f"M1バー: {len(m1):,}  ({m1['date'].iloc[0]} .. {m1['date'].iloc[-1]})  -> {out_path}",
            flush=True,
        )
    else:
        print(f"M1バー: 0  -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
