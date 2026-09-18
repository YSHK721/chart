"""MarketdataCsvOHLCRepository: marketdata 形式 CSV から domain.Bar 列を読み込む。

marketdata 系列（`data/marketdata/*.csv`・ヘッダ `date,open,high,low,close,volume[,up,dn][,spread]`・
日付 ISO `2012-06-14 10:35:00`・時刻系は UTC＝裁定 2026-08-18）を list[domain.Bar] へ変換する
MarketDataPort 実装。2012 年からの全期間 JP225 実データを sim の実行データセットにするための
リーダ（依頼者承認 2026-09-06。従来はどのリーダも本形式を読めず、全 12 組合せの実測で
「fixture×MT5 ローダ EA」しか動かなかった）。

spread 列（ISSUE-511 段階 2・整数 points）が在ればその値を Bar.spread へ写し、列が無いときは
spread=0 である。spread 列の無い系列を spread 依存 EA（MA_Slope 系）へ供給してはならない
（H-4 裁定・MARKETDATA_TIMESERIES_BOUNDARY_DESIGN §10.2）。その遮断は非対象宣言
N-17（`main/tester_settings/unsupported.py`）が実行前に Fail-Stop で担う。

**窓はフレーム段で先に適用する**（構築時パラメータ ``window``・ISSUE-135 と同じ隔離）。
Bar を全件組み立ててから捨てる後段フィルタは、4,604,080 行の実測で Bar 構築だけに
442.6 秒を要する「作ってから捨てる」浪費になる（ISSUE-450 型）。窓内の行だけを
``frame_to_bars`` へ渡す＝構築数と採用数の差 0 は計算量テストが固定する。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from marketdata.csv_schema import SPREAD_COLUMN
from simulator.adapter.repository._ohlc_frame import (
    ColumnSpec,
    frame_to_bars,
    read_csv_or_data_error,
)
from simulator.domain.bar import Bar
from simulator.usecase.ports import MarketDataPort

#: marketdata 形式の必須列。任意列 up・dn は読み飛ばす（Bar に写さない）。任意列 spread は
#: 在るときだけ Bar.spread へ写す（_SPEC_WITH_SPREAD・列が無ければ spread=0）。
_REQUIRED = ("date", "open", "high", "low", "close", "volume")

#: 正規化済み時刻列（読み込み後に 1 回だけベクトル計算で付ける内部列）。
_TIME_COLUMN = "_marketdata_time"


def _ohlcv(df: pd.DataFrame, i: int) -> "dict[str, Any]":
    """marketdata 形式 1 行の time..volume を domain.Bar 引数へマッピングする。

    時刻は前計算済みの ``_TIME_COLUMN``（datetime64・UTC naive＝MT5 リーダと同じ表現）を
    使う（行ごとの文字列パースをしない）。
    """
    return {
        "time": df[_TIME_COLUMN].iat[i].to_datetime64(),
        "open": float(df["open"].iat[i]),
        "high": float(df["high"].iat[i]),
        "low": float(df["low"].iat[i]),
        "close": float(df["close"].iat[i]),
        "volume": float(df["volume"].iat[i]),
    }


def _extract(df: pd.DataFrame, i: int) -> "dict[str, Any]":
    """spread 列の無い marketdata 形式 1 行（spread=0）。"""
    return {**_ohlcv(df, i), "spread": 0}


def _spread_of(df: pd.DataFrame, i: int) -> int:
    """位置 ``i`` の spread（整数 points）。"""
    return int(df[SPREAD_COLUMN].iat[i])


def _extract_with_spread(df: pd.DataFrame, i: int) -> "dict[str, Any]":
    """spread 列を持つ marketdata 形式 1 行（spread は列の値）。"""
    return {**_ohlcv(df, i), "spread": _spread_of(df, i)}


_SPEC = ColumnSpec(required=_REQUIRED, extract=_extract)
_SPEC_WITH_SPREAD = ColumnSpec(
    required=(*_REQUIRED, SPREAD_COLUMN), extract=_extract_with_spread
)


def _header_line(source_ref: Any) -> "str | None":
    """価格 CSV のヘッダ 1 行（本文は読まない）。読めない・パスでないときは None。

    本モジュールで**ヘッダだけを読む唯一の口**である（全件を読む ``load`` は
    ``read_csv_or_data_error`` を通る別経路であり、ヘッダも本文も読む）。形式の判定
    （``detect_ohlc_form``）と気配幅の列の有無（``supplies_spread``）は同じ 1 行から決まる
    別々の問いであり、同じ実体に対して 2 回読む必要はない。読取が問いの数だけ増えないことは
    `simulator/tests/unit/test_ohlc_marketdata_csv_supplies_spread.py` が継ぎ目で固定する。
    """
    try:
        with open(source_ref, "rb") as f:
            return f.readline().decode("utf-8", "replace").strip()
    except (OSError, TypeError, ValueError):
        return None


class _Unread:
    """「ヘッダをまだ読んでいない」を表す型（値は ``_UNREAD`` の 1 つだけ）。

    ``None`` を「未読」の合図に使わない理由: ``_header_line`` は**読めなかった**実体に
    対して ``None`` を返す。同じ ``None`` を「まだ読んでいない」の意味にも使うと、読めな
    かった結果をそのまま渡した呼び手に対して、同じ実体をもう一度読みに行く。**その差は
    答えに 1 ビットも現れない**ため状態検証では落ちない（実測 2026-09-18・本作業ツリー。
    数え方: 実体 4 件＝9 列 / 6 列 / MT5 TAB / 不在 を 1 件ずつ判定し ``_header_line`` の
    発行を数えた。答えは両者同一のまま、読取だけが 4 回 → 5 回になり、増分は読めない
    実体 1 件に集中した）。2 つの意味に別々の値を与えて、構造でこの穴を塞ぐ。

    この契約（**読めなかったことを表す ``None`` を渡しても読み直さない**）を測るのは
    `simulator/tests/unit/test_ohlc_marketdata_csv_supplies_spread.py` の
    test_a_header_that_could_not_be_read_is_not_read_again である。素朴な ``None``
    番兵へ戻す改変はそこでだけ落ちる（実測 2026-09-18・本作業ツリー。数え方:
    `simulator/tests/unit` を全件走らせ、赤になった検定を数えた——3,242 件中 当該 1 件のみ）。
    """


#: 「まだ読んでいない」を表す唯一の値（``None`` は「読んだが読めなかった」に使う）。
_UNREAD = _Unread()


def _columns(head: str, separator: str) -> "list[str]":
    """ヘッダ 1 行を列名の並びへ割る（前後の空白は落とす）。

    「ヘッダは区切り文字で連ねた列名の並びである」という 1 つの事実の唯一の所有者。
    形式の判定と気配幅の列の有無は同じ割り方を使うが、その割り方をそれぞれに書き写さない。
    """
    return [c.strip() for c in head.split(separator)]


def detect_ohlc_form(source_ref: Any, *, header: "str | None | _Unread" = _UNREAD) -> str:
    """価格 CSV の形式をヘッダ 1 行の実測で判定する（"mt5_tab" / "comma" / "marketdata" / ""）。

    判定材料はファイル自身のヘッダ（形式の権威はデータ実体）であり、EA 名や拡張子から
    推測しない。読めない・どれでもない・パスでない（None＝データ非供給の modelling 等）
    場合は ""（呼び出し側が既定へ倒す。実測: `Model=3` の job は data_path=None で通る）。

    ``header``（任意・キーワード専用・既定 ``_UNREAD``）: 既に読んだヘッダ 1 行。渡された
    ときは IO をしない。**読めなかったことを表す ``None`` を渡してもよい**——その実体は
    ""（判定不能）になり、読み直さない。同じ実体について「何形式か」と「気配幅の列が
    在るか」を続けて問う呼び手（``supplies_spread``）が、同じ 1 行を 2 回読まないための
    受け口である。**判定規則も戻り値の語彙も変わらない**——形式の語彙の所有者は本関数の
    ままであり、その結びは
    `simulator/tests/unit/test_ea_bindings_source_declarations_are_bound.py` が
    宣言表との一致で固定する。語彙を本関数の外（別関数）へ出すとその結びは測れなくなる
    （反実仮想で実測 2026-09-18: 判定の分岐を別関数へ切り出した写しに同じ AST 抽出器を
    当てると、本関数から読める語彙が {""} だけになり宣言集合 {"", "comma", "marketdata",
    "mt5_tab"} と一致しなくなる）。
    """
    head = _header_line(source_ref) if header is _UNREAD else header
    if head is None:
        return ""
    if head.startswith("<DATE>"):
        return "mt5_tab"
    columns = _columns(head, ",")
    if columns[:1] == ["time"]:
        return "comma"
    if columns[:1] == ["date"]:
        return "marketdata"
    return ""


#: MT5 エクスポート形式での気配幅の列名。
#:
#: 概念の所有者は読み手 `simulator/adapter/repository/ohlc_mt5_csv.py`（必須列として持つ）
#: だが、対応表を公開しておらずその改変は本段の範囲外である。したがってここは**写し**で
#: あり、写しが腐らないことは
#: `simulator/tests/unit/test_ohlc_marketdata_csv_supplies_spread.py` が読み手の必須列と
#: 機械で結んで固定する（同じ語を人手で 2 箇所に保つのではなく、検定が一致を強制する）。
_MT5_SPREAD_COLUMN = "<SPREAD>"

#: 形式 → (ヘッダの区切り文字, その形式での気配幅の列名)。
#:
#: 形式ごとに区切りと綴りが違うだけで、問いは 1 つ（気配幅の列が在るか）である。表に無い
#: 形式（判定できない実体）は False へ倒れる。
_SPREAD_COLUMN_BY_FORM = {
    "mt5_tab": ("\t", _MT5_SPREAD_COLUMN),
    "comma": (",", SPREAD_COLUMN),
    "marketdata": (",", SPREAD_COLUMN),
}


def supplies_spread(source_ref: Any) -> bool:
    """その実体が気配幅の列を供給するか（ヘッダ 1 行の実測・2 値・**3 形式すべてに正直**）。

    列名を知ってよいのは、その列を Bar へ写す読み手だけである。保証境界の側
    （`simulator/main/tester_settings/unsupported.py`）に「ヘッダに spread 列があるか」を
    書くと列名の所有者が 2 つになるため、判定はここが持つ。

    ``detect_ohlc_form`` とは問いが違う（畳まない）: あちらは「どのリーダで読むか」（3 値・
    リーダが増えたとき変わる）、こちらは「その実体が気配幅を供給するか」（2 値・列が増えた
    とき変わる）。本関数は**その判定を利用する**が、両者は別の問いのままである。

    形式ごとに正しい区切りでヘッダを割り、その形式での気配幅の列名を探す
    （marketdata / comma は comma 区切りの spread、MT5 TAB はタブ区切りの `<SPREAD>`）。
    したがって **MT5 TAB は気配幅の列を持てば True** である（実測 2026-09-18: 突合
    フィクスチャの実ヘッダは `<SPREAD>` を持ち、TAB の読み手はその値を Bar.spread へ写す）。
    形式で決め打たない——`<SPREAD>` を持たないタブ区切りの実体は False になる。

    是正前（同日・段階 8-A の初版）は comma 区切りだけで割っていたため MT5 TAB が False に
    なっていた。これは「MT5 が気配幅を供給しない」という意味ではなく、タブ区切りのヘッダを
    comma で割ると列にならないという機構上の帰結にすぎなかった。述語が嘘をつく形を別の
    条件で覆い隠す（連言にする）のではなく、述語そのものを正す（依頼者裁定 2026-09-18）。

    comma 形式（``time,…,spread``）は列を持てば True・持たなければ False（TBD-6 は Option A
    で確定・同裁定）。形式では限定しない——「その気配幅が MT5 由来か」はヘッダから検証
    できず、かつ comma 形式の実体が走査範囲に無いため、形式での限定は識別力を持たない。
    走査（2026-09-18・本作業ツリー。数え方: 拡張子 .csv のファイルの 1 行目を本モジュールの
    形式判定で分類した件数。**範囲は実体パスで書く**——「リポジトリ直下」「データ実体の木」
    のような呼び名では、読者が再現したとき別の木を辿って数が食い違う）:

        走査範囲 1 = リポジトリ根 /workspaces/app 以下すべて
            （.git 除外・symlink を辿らない）… 103 件
            内訳 mt5_tab 41 / marketdata 8 / どの形式でもない 54 / **comma 0**
        走査範囲 2 = data/marketdata/** （symlink を辿る）… 46 件
            内訳 marketdata 46 / **comma 0**
        走査範囲 3 = 走査範囲 1 から data/ を除いたもの… 102 件
            差の 1 件は data/test/JP225_202608250103_202608262359.csv（mt5_tab の実体）
        走査範囲 4 = data/** （symlink を辿る）… 47 件
            内訳 marketdata 46 / mt5_tab 1 / **comma 0**

    **comma が 0 件であることは上の 4 通りの走査すべてで一致する。**

    読めない・形式を判定できない・パスでない（None＝データ非供給の modelling 等）は False
    （``detect_ohlc_form`` が "" へ倒れるのと同じ縮退方向）。

    **呼び手はまだ無い**（2026-09-18 時点。数え方: リポジトリ全体の .py を ``grep`` で走査し、
    本関数名の出現を数えた——定義 1 件と検定 1 ファイルのみで、非テストの呼び手は 0 件）。
    保証境界 N-17 の述語を本関数へ置き換えるのは段階 8-C であり、本段では N-17 の挙動を
    変えない。
    """
    head = _header_line(source_ref)
    # 読めなかった実体（``head is None``）をここで弾かない。``None`` は「未読」と別の値なので
    # ``detect_ohlc_form`` へそのまま渡してよく、判定は ""（形式不明）へ倒れ、下の表引きが
    # None を返して False になる。**同じ危険に 2 つ目の防御を置かない**——片方ずつ撤去しても
    # 答えも読取回数も変わらない配置では、どちらの変異も単独では検定に映らないためである
    # （実測 2026-09-18・本作業ツリー。数え方: 9 列 / 6 列 / MT5 TAB / 不在 の 4 実体を 1 件
    # ずつ判定し、答えと ``_header_line`` の発行を数えた。早期 return の有無で
    # 答え [True, False, True, False]・発行 4 回がいずれも一致した＝この枝は冗長）。
    spec = _SPREAD_COLUMN_BY_FORM.get(detect_ohlc_form(source_ref, header=head))
    if spec is None:
        return False
    separator, column = spec
    return column in _columns(head, separator)


class MarketdataCsvOHLCRepository(MarketDataPort):
    """marketdata 形式 CSV → list[domain.Bar] へ変換する MarketDataPort 実装。

    ``window``（任意・(start, end) 半開・UTC aware datetime）: 指定時はフレーム段で
    窓内の行だけに絞ってから Bar を組む（窓外の Bar を作らない）。
    """

    def __init__(self, window: "tuple[Any, Any] | None" = None) -> None:
        self._window = window

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> list[Bar]:
        df = read_csv_or_data_error(source_ref)
        if "date" in df.columns:
            # UTC aware で 1 回だけベクトルパース（date 列の時刻系は UTC＝裁定 2026-08-18）
            times = pd.to_datetime(df["date"], utc=True)
            if self._window is not None:
                start, end = self._window
                df = df.loc[(times >= start) & (times < end)]
                times = times.loc[df.index]
            df = df.assign(**{_TIME_COLUMN: times.dt.tz_localize(None)}).reset_index(drop=True)
        spec = _SPEC_WITH_SPREAD if SPREAD_COLUMN in df.columns else _SPEC
        return frame_to_bars(df, spec)
