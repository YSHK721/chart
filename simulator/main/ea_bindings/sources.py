"""EA 束縛が共有するデータ供給（CSV 読み・OHLC リーダの選択）。ISSUE-502 段階 4A。

複数の EA が同じ読み方をするため、読み方そのものはここが唯一の所有者である
（EA モジュールごとに写すと、列名の正規化が片方だけ改訂される）。

**読み方の差（区切り文字と列名）は本モジュールに閉じる**（ISSUE-511 段階 8-B）。EA が
宣言するのは「何の系列を読むか」であり、「どの形式のファイルか」ではない（形式は実体が
決める）。

実際に走査して確かめた範囲（``grep`` で非テストの .py を全走査・2026-09-18 時点。
走査語は ``<OPEN>`` / ``<SPREAD>`` / ``<DATE>`` / ``sep="\t"``）:

    * **限定は「価格 OHLC CSV を読む simulator の非テストモジュール」**である。その範囲で
      区切り文字（TAB / comma）と MT5 列名（`_MT5_COLUMN_NAMES`）を知るのは、本モジュールと
      ``adapter/repository`` の 3 リーダだけである。**この限定より広く言ってはならない**——
      下の反例がある。
    * 反例（いずれも読み手ではないが、同じ語彙を持つ非テストモジュール）:
      `simulator/sim_ui/adapter/ea_build_probe.py`（探索用に MT5 見出し行と TAB 行を
      **書く**）・`tools/capture_mt5_bars.py`（端末エクスポートと byte 一致の見出しを
      **書く**）・`marketdata/mt5_ticks/archive_ingest.py`（MT5 **ティック**アーカイブの
      見出し ``<DATE> <TIME> <BID> <ASK> <LAST> <VOLUME>`` を読む。OHLC とは別の列集合）。
    * ``simulator/usecase`` と ``simulator/domain`` には形式に関する語が 0 件である
      （形式名・MT5 列名・区切り指定・リーダ実装名のいずれも出現しない。上の走査語に
      ``mt5_tab`` と ``CsvOHLCRepository`` を足した再走査で 0 件を実測）。
    * ただし**判定結果の形式名での分岐は本モジュールの外にも 2 箇所ある**——
      保証境界 `simulator/main/tester_settings/unsupported.py`（N-17・段階 8-C の対象）と
      実行条件 `simulator/sim_ui/adapter/symbol_spec_catalog.py`（段階 8-D の対象）。
      この 2 つが見るのは形式の名前だけで、区切り文字と列名は見ない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository
from simulator.adapter.repository.ohlc_marketdata_csv import (
    MarketdataCsvOHLCRepository,
    detect_ohlc_form,
)
from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from simulator.domain.exceptions import DataError
from simulator.usecase.ports import MarketDataPort

#: MT5 エクスポート列名 → registry へ載せる正規化列名。``<SPREAD>`` を含めるのは、
#: 気配幅を読む EA が形式ごとに別の列名を書き分けないためである（形式差はここに閉じる）。
#:
#: 同じ事実（MT5 が始値を ``<OPEN>`` と綴ること）は `Mt5CsvOHLCRepository` 側にも
#: Bar 用の対応として在り、ここはその写しである。1 つにするには読み手側（`ohlc_mt5_csv.py`）が
#: 対応表を公開する必要があり、その改変は段階 8-B の範囲外＝申し送り。
_MT5_COLUMN_NAMES = {
    "<OPEN>": "open",
    "<HIGH>": "high",
    "<LOW>": "low",
    "<CLOSE>": "close",
    "<SPREAD>": "spread",
}

#: 対応する 3 形式のいずれにも必ず在る価格列（registry ビルダが参照する最小集合）。
#: 気配幅の列は**任意**である——持たない系列が実在し、それを読まない EA は
#: 正常に組める。読む EA に対してだけ `series_or_data_error` が Fail-Stop する。
_REQUIRED_COLUMNS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class _CsvForm:
    """1 形式ぶんの読み方の宣言。

    ``read_options``: pandas へ渡す読取引数（区切り文字など）。
    ``column_names``: registry へ載せる正規化列名への対応（空＝既に正規化済み）。
    ``repository``: その形式をバー列へ読む `MarketDataPort` 実装。
    """

    read_options: "dict[str, Any]"
    column_names: "dict[str, str]"
    repository: "Callable[[], MarketDataPort]"


#: 形式 → 読み方の宣言。**形式を知る表は本モジュールにこの 1 つだけ**であり、
#: `source_for`（解決口）・`dataframe_for`（registry 用の DataFrame）・
#: `ohlc_repository_for`（バーの読み手）はどれも同じ表を引く。段階 8-B の Green までは
#: 同じ形式判定の分岐が 2 つの関数に別々に書かれており、形式が 1 つ増えるときに
#: 2 箇所を揃えて直す必要があった。
_FORMS = {
    "mt5_tab": _CsvForm(
        read_options={"sep": "\t"},
        column_names=_MT5_COLUMN_NAMES,
        repository=Mt5CsvOHLCRepository,
    ),
    "marketdata": _CsvForm(
        read_options={}, column_names={}, repository=MarketdataCsvOHLCRepository
    ),
    "comma": _CsvForm(read_options={}, column_names={}, repository=CsvOHLCRepository),
}

#: 形式を判定できない実体に**バーの読み手だけ**を求められたときの既定（従来挙動）。
#: registry 用の DataFrame 側は既定へ倒さず `DataError` で止める。この非対称は意図である
#: ——読み手は後段の読み込みで必ず必要列を検査して落ちるが、DataFrame 側を既定へ倒すと
#: 「読めなかった」事実が数値の中に隠れて実行が通ってしまう。
_UNKNOWN_FORM_REPOSITORY = CsvOHLCRepository


def _read_or_data_error(data_path: Any, **read_options: Any) -> pd.DataFrame:
    """pandas で読み、外側例外を内側 `DataError` へ翻訳する（漏出禁止・CLEAN_ARCH §6）。"""
    try:
        return pd.read_csv(data_path, **read_options)
    except Exception as exc:
        raise DataError(
            f"指標計算用 CSV の読み込みに失敗しました: {data_path}",
            context={"data_path": str(data_path), "cause": repr(exc)},
        ) from exc


def _resolved_spec(data_path: Any) -> "tuple[str, _CsvForm]":
    """データ実体の形式を**1 回だけ**判定し、その読み方の宣言を返す。

    「この実体が何形式か」は 1 実体につき 1 つの事実である。同じ実体へ 2 回目の判定を
    発行しても出力には何も足さない——その無駄の不在は
    `simulator/tests/unit/test_ea_bindings_source_read_complexity.py` が
    「発行 − 使用 = 0」（使用は data_path 単位）で固定する。

    どの形式でもない実体は既定へ倒さず `DataError` で止める（倒すと「読めなかった」
    事実が数値の中に隠れ、状態検証では落ちない実行が通る）。
    """
    form = detect_ohlc_form(data_path)
    spec = _FORMS.get(form)
    if spec is None:
        raise DataError(
            f"指標計算用 CSV の形式を判定できません: {data_path}",
            context={"data_path": str(data_path), "form": form},
        )
    return form, spec


def _frame_of(data_path: Any, form: str, spec: _CsvForm) -> pd.DataFrame:
    """解決済みの宣言で registry 用 DataFrame を読む（形式の判定はしない）。"""
    frame = _read_or_data_error(data_path, **spec.read_options)
    if spec.column_names:
        # 正規化が要る形式でだけ rename する（空の対応表で呼ぶと、既に正規化済みの
        # 系列に対して全行の複製を作って捨てることになる）。
        frame = frame.rename(columns=spec.column_names)
    missing = [name for name in _REQUIRED_COLUMNS if name not in frame.columns]
    if missing:
        raise DataError(
            f"指標計算用 CSV に必須の価格列がありません: {missing}",
            context={
                "data_path": str(data_path),
                "form": form,
                "missing": missing,
                "columns": list(frame.columns),
            },
        )
    return frame


def load_dataframe(data_path: Any) -> pd.DataFrame:
    """指標 registry の事前計算用に価格 CSV を DataFrame として読み込む。

    registry は系列（pandas）を要するため DataFrame が必須。一方 Interactor は Bar 列を
    消費し、controller.run は committed IF（source_ref パス）上で再度 load する。registry
    の DataFrame 需要・Interactor の Bar 需要・controller の path 再読みを 1 回の読み込みへ
    統合するには committed adapter/usecase の IF 変更が要るため範囲外＝申し送り。
    外側（pandas/OS）例外は内側 DataError へ翻訳し漏出を防ぐ（CLEAN_ARCH §6）。

    **本関数が comma 固定であることが、保証境界の識別力の出所になっている**
    （ISSUE-511 段階 8-B 以降）。段階 8-B より前、「気配幅に依存する EA」は
    「自分のモジュールで `Mt5CsvOHLCRepository` を名指す EA」として機械的に識別できた。
    段階 8-B で 3 本が `ohlc_repository_for` へ移った結果、識別の出所は
    「本関数を使う EA は MT5 タブ形式を読めない」という事実へ置き換わった。識別の結果は
    同じだが、根拠が変わっている。

    実測（2026-09-18 時点。数え方: 各 EA 束縛を MT5 タブ形式のデータで実際に build し、
    返る読み手が `Mt5CsvOHLCRepository` であるものを数えた）:

        * 本関数を呼ぶ EA 束縛は **6 本**（weekly_vol_band / pro_fit_band / sma_touch_long /
          simple_touch_long / open_then_close_5m / tc24051901 の各モジュール）。
          **うち 1 本（tc24051901）が既定 TC 経路**であり、残る 5 本が登録表の中にある
          （既定 TC を別勘定にすると二重計上になる）。
        * データを読む EA 束縛は計 **9 本**（登録表 8 本＋既定 TC 経路 1 本。データを
          読まない dataless は含めない）。内訳は本関数を呼ぶ 6 本と `dataframe_for` を
          呼ぶ 3 本であり、6+3=9 で尽きる。
        * MT5 タブ形式で組めるのは現状 **3 本**（`dataframe_for` を呼ぶ 3 本）。本関数を
          呼ぶ 6 本はタブ区切りを comma として読むため 1 列の DataFrame になり、
          ``KeyError`` で組めない。

    したがって `dataframe_for` の正規化を他の EA へ広げるときは、
    `simulator/tests/unit/test_unsupported_spread_dependency.py` の等式が赤くなる。
    **ただしその等式が走査するのは登録表 8 本だけ**である（`_EA_BINDINGS` を回すため、
    表の外側にある既定 TC 経路は走査対象に入らない）。上の 9 本は本 docstring の数えで
    あって、当該検定が測る量ではない。
    これは脆さではなく、保証境界の宣言を人手で見直させるための仕掛け線である
    （赤を消すために測り方を緩めると、宣言はただの手書きの 3 つの名前に戻る）。
    """
    return _read_or_data_error(data_path)


def dataframe_for(data_path: Any) -> pd.DataFrame:
    """指標 registry 用の DataFrame を**データ実体の形式**から読む（EA 名で選ばない）。

    形式の権威はデータのヘッダ（`detect_ohlc_form`）であり、EA 名や拡張子ではない。
    読み方（区切り文字と列名の正規化）は `_FORMS` の宣言 1 行から引く。
    どの形式でもない実体（読めない・ヘッダ不一致・パスでない）は既定形式へ倒さず
    `DataError` で止める——倒すと「読めなかった」事実が数値の中に隠れ、状態検証では
    落ちない実行が通る。是正前は TAB 固定で読んでいたため、comma 系を渡すと 1 列の
    DataFrame になり、後続の ``df["close"]`` が翻訳されない ``KeyError`` で抜けていた。

    形式の判定は `_resolved_spec` に閉じており、本関数 1 回につき 1 回だけ発行する。
    """
    form, spec = _resolved_spec(data_path)
    return _frame_of(data_path, form, spec)


def series_or_data_error(frame: pd.DataFrame, column: str) -> pd.Series:
    """registry へ載せる 1 列を引く（欠落は既定で補わず `DataError`）。

    既定 0 で補うと「気配幅 0 の約定」が正常な数値として出力され、状態検証では
    落ちない（H-4: spread=0 供給は実 MT5 と一致しない）。読む EA に対してだけ
    ここが止める——列を持たない系列そのものは正当であり、読まない EA は組める。
    """
    if column not in frame.columns:
        raise DataError(
            f"指標 registry が要る列がデータにありません: {column}",
            context={"column": column, "columns": list(frame.columns)},
        )
    return frame[column].astype(float).reset_index(drop=True)


def ohlc_repository_for(data_path: Any) -> MarketDataPort:
    """OHLC リーダをデータ実体の形式で選ぶ（データを読む EA 束縛 9 本の唯一の規則）。

    形式の権威はデータのヘッダ（`detect_ohlc_form`）であり、選ぶ実装は `_FORMS` の
    宣言 1 行から引く。MT5 タブ形式は `Mt5CsvOHLCRepository`（気配幅を Bar へ写す）、
    marketdata 形式は `MarketdataCsvOHLCRepository`（2012 年からの全期間 JP225 実データ・
    依頼者承認 2026-09-06）、comma 形式と形式不明は従来どおり `CsvOHLCRepository`
    （comma 合成データの既存経路と byte 等価）。ISSUE-511 段階 8-B 以前は MT5 の枝が無く、
    気配幅を読む EA が自分のモジュールで MT5 リーダを名指していた（EA 名から形式を
    決める形＝実体との二重管理）。

    戻り値はどれも `MarketDataPort` の実装であり、呼出側は差し替えても同じ契約
    （``load(source_ref, timeframe, period) -> バー列``）だけを使う（LSP）。
    """
    spec = _FORMS.get(detect_ohlc_form(data_path))
    return (spec.repository if spec is not None else _UNKNOWN_FORM_REPOSITORY)()


@dataclass(frozen=True)
class ResolvedSource:
    """1 つのデータ実体を**1 回の形式判定**で解いた結果。

    ``frame``: 指標 registry の事前計算に載せる正規化済み DataFrame。
    ``repository``: 同じ実体から Bar 列を読む `MarketDataPort` 実装。

    2 つを組で返す理由（ISSUE-511 段階 8-B 工程 5 🟡-4）: EA 束縛が要るのはこの 2 つだけ
    であり、どちらも「この実体は何形式か」という**同じ 1 つの事実**から決まる。是正前は
    `dataframe_for` と `ohlc_repository_for` を同じ data_path で続けて呼ぶ形だったため、
    1 構築あたり形式判定が 2 回発行されていた（実測）。出力は 1 ビットも変わらないので
    状態検証では落ちない——だから組で返し、判定を 1 回に構造で閉じる。
    """

    frame: pd.DataFrame
    repository: MarketDataPort


def source_for(data_path: Any) -> ResolvedSource:
    """**データを読む EA 束縛の唯一の解決口**。形式を 1 回だけ判定して 2 つの口を返す。

    形式の権威はデータのヘッダであり、EA 名や拡張子ではない。呼出側（EA 束縛）は
    「何の系列を読むか」だけを宣言し、区切り文字も列名も知らない。

    例外は `dataframe_for` と同じ（形式不明・読めない・必須列欠落はいずれも `DataError`
    で Fail-Stop し、既定形式へは倒さない）。読み手だけを既定へ倒す非対称は
    `ohlc_repository_for` の側に残る——本解決口は registry 用の DataFrame も同時に
    要求するため、読めない実体をここで通すと数値の中に事実が隠れる。
    """
    form, spec = _resolved_spec(data_path)
    return ResolvedSource(
        frame=_frame_of(data_path, form, spec), repository=spec.repository()
    )
