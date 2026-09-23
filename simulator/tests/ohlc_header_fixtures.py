"""価格 CSV のヘッダ 1 行・本文・その書き出しの**唯一の宣言**（テストヘルパ・非テストモジュール）。

なぜ在るか（工程 5 レビュー 🟡-1 の実測）:
    `simulator/tests/unit/test_ohlc_marketdata_csv_supplies_spread.py`（述語 supplies_spread
    の検定）と `simulator/tests/unit/test_unsupported_n17_supplies_spread_boundary.py`
    （保証境界 N-17 の検定）が、同じヘッダ定数と同じ書き出しヘルパを**手書きで複製**して
    いた。複製は必ず取り残しを生む（「同じコードを手書き複製するな」）。

    とりわけ危険だったのは ``MD9`` である。N-17 側は「ヘッダ → 期待」の対
    （_BOUNDARY）で持つため、**``MD9`` が気配幅なしへ腐っても期待値ごと辻褄が合い、
    どの検定も落ちない**。「marketdata 9 列は非発火」という主張が、何も測らない恒真式へ
    退化する。

    是正前の実測（2026-09-18・本作業ツリー。数え方: ``_MD9`` を気配幅なしへ腐らせ、同じ
    ファイル内の期待値と CX-2 の非発火フィクスチャをその腐敗と辻褄が合うよう直した状態で
    `marketdata/tests simulator/tests/unit` を全件走らせ、赤になった検定を数えた
    ——**新たに赤になった検定は 0 件**。1 failed / 4758 passed / 2 xfailed は是正前の
    baseline と一致し、既知の失敗 ISSUE-517 の 1 件だけが赤のままだった）。

    本モジュールが唯一源になった後は、同じ腐敗が supplies_spread 側の期待
    （``MD9`` は気配幅を供給する＝True）と N-17 側の期待（非発火）の**両方**と食い違う
    ため、片側の辻褄合わせでは緑にできない。

含む構造:
    MD6 / MD9                   marketdata 形式（気配幅なし 6 列 / あり 9 列）
    COMMA / COMMA_NO_SPREAD     comma 形式（気配幅あり / なし）
    MT5_TAB / MT5_TAB_NO_SPREAD MT5 エクスポート形式（タブ区切り・気配幅あり / なし）
    GARBAGE                     どの形式でもないヘッダ
    MD9_PADDED / COMMA_PADDED   区切りの両側に空白を挟んだ版（空白は列名の一部ではない）
    body_rows                   ヘッダと同じ列数の本文を ``count`` 行（区切りはヘッダから導く）
    write_header                ヘッダ 1 行（+ 任意の本文）を ``tmp_path`` の下へ書く

本モジュールは既定のデータ木を読み書きしない（書込先は呼出側が渡す ``tmp_path`` のみ）。
**テストではない**（ファイル名が test で始まらないため pytest は収集しない）。
"""
from __future__ import annotations

#: marketdata 形式・気配幅なし 6 列（現行の実行データセット。以下 2 つのデータ実体の
#: パスをバッククォートで囲まないのは、静的品質検定 C1 の索引が data/marketdata の
#: symlink を辿らず「存在しない」と報せるためである（実体は head -1 で実測済み）。
#: data/marketdata/jp225_m1.csv の 1 行目・実測 2026-09-18）。
MD6 = "date,open,high,low,close,volume"
#: marketdata 形式・気配幅あり 9 列（data/marketdata/jp225_mt5_spread_m1.csv の
#: 1 行目・同日実測）。バッククォートで囲まないのは、静的品質検定 C1 の索引が
#: data/marketdata の symlink を辿らず「存在しない」と報せるためである。
MD9 = "date,open,high,low,close,volume,up,dn,spread"
#: comma 形式（CsvOHLCRepository の必須列）・気配幅あり。
COMMA = "time,open,high,low,close,volume,spread"
#: comma 形式・気配幅なし（探索用サンプルと同形）。
COMMA_NO_SPREAD = "time,open,high,low,close,volume"
#: MT5 エクスポート（タブ区切り）・気配幅あり。
MT5_TAB = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"
#: 同じタブ区切りだが気配幅の列を持たない実体（形式で決め打っていないことの対照）。
MT5_TAB_NO_SPREAD = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>"
#: どの形式でもないヘッダ。値そのものは任意であり、満たすべき条件は「先頭列が
#: date でも time でもなく、`<DATE>` でも始まらない」ことだけである。
GARBAGE = "alpha,beta"
#: 区切りの両側に空白を挟んだ `MD9`（空白は列名の一部ではない）。
#: 列名を割るときに前後の空白を落とさないと、気配幅の列が " spread" になって見つからない。
MD9_PADDED = "date, open, high, low, close, volume, up, dn, spread "
#: 同じ空白の入れ方をした comma 形式（空白の扱いが 1 形式だけの都合でないことの対照）。
COMMA_PADDED = "time, open, high, low, close, volume, spread "


def body_rows(header: str, count: int) -> str:
    """``header`` と同じ列数の本文 ``count`` 行（値は呼出側の assertion に使わない）。

    列数を**ヘッダ定数から導く**のは、定数が変わったときに本文だけが取り残されないため
    である（列の綴りを書き写さない）。先頭列は日時。**区切りもヘッダから導く**——タブを
    含むヘッダはタブで、それ以外は comma で割る。comma 固定だと、同じモジュールが宣言する
    タブ区切りの 2 定数で本文が 2 列になり、宣言（ヘッダと同じ列数）が偽になっていた
    （工程 5 レビュー 🟡-4 の実測・2026-09-23。呼出は当時 1 箇所だったため現に壊れてはいなかった）。

    規模 2 点で「読取がデータ量で増えない」ことを表明する計算量検定が、実体の行数を
    変えるために使う。同じ組み立てが
    `simulator/tests/unit/test_symbol_spec_catalog_spread_axis.py` と
    `simulator/tests/unit/test_symbol_spec_catalog_ledger_wiring.py` の 2 箇所で要るため、
    ヘッダ定数と同じくここを唯一源にする（手書き複製を作らない）。
    """
    sep = "\t" if "\t" in header else ","
    tail = sep.join("0" for _ in header.split(sep)[1:])
    return "".join(f"2024-01-08 00:00:00{sep}{tail}\n" for _ in range(count))


def write_header(tmp_path, name: str, header: str, body: str = "") -> str:
    """``header`` 1 行（+ 任意の ``body``）を ``tmp_path/name`` へ書き、そのパスを返す。

    戻り値を文字列にするのは、呼出側がデータ実体の参照（`EngineBinding.data_path` や
    supplies_spread の引数）としてそのまま渡すためである。
    """
    path = tmp_path / name
    path.write_text(header + "\n" + body, encoding="utf-8")
    return str(path)
