"""CSV から OHLC / OHLCV データを読み込む入力アダプタ（共有実体）。

データ取得そのもの（ブローカー接続等）は本ライブラリの責務外。呼び出し側が
用意した CSV を読み込み、コア計算が要求する OHLC 列を備えた DataFrame に正規化する。

本モジュールは 2 つの公開面を持つ（ISSUE-179 項目 1: `indigators/*/src/loader.py` 一本化）。

``load_ohlc_csv``
    従来からの公開 API。OHLC 4 列を必須とし、必須列判定は既定方針で行う。
    シグネチャ・挙動は移設時点から不変（``marketdata.dataset`` /
    ``marketdata.serving_cache`` / ``indigators/profit_band/src/loader.py`` が利用）。

``read_ohlc_csv_with_policy``
    各指標パッケージの ``src/loader.py`` が自パッケージの方針を渡して使う共有機構。
    パッケージ間で実測された 4 軸の差異（必須列 / 列名 cast / 空 CSV ガード /
    ``require=`` の公開有無）をパラメータ化した上位集合であり、既定値は
    ``load_ohlc_csv`` の挙動と完全一致する。

``read_csv_kwargs`` を「可変長キーワードではなく位置引数の Mapping」で受けるのは意図的
である。``**kwargs`` で受けると ``require`` 等の方針パラメータ名が pandas へ渡すべき
キーワードを横取りし、``load_ohlc_csv(path, require=...)`` が現在送出している
``TypeError: read_csv() got an unexpected keyword argument 'require'`` を消してしまう。
名前空間を分離することで、方針を公開するか否かを呼び出し側の ``def`` だけで決められる。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import os
import time
from typing import Any

import pandas as pd

_REQUIRED = ("open", "high", "low", "close")


#: 追記途中の CSV を掴んだときの再読取の待ちと総時間予算（ISSUE-186）。
#:
#: 固定回数だと「毎回 torn を踏む」確率が残る（実測: 4 回打ち切りで 1,773 回中 2 回＝0.11% が
#: 送出まで到達した）。回数ではなく**時間予算**にすると、書き手が完結する瞬間を掴むまで待てる。
#: 予算を使い切っても成功しない場合は、追記が止まらない異常として最後の例外を送出する。
#: 本物のデータ異常（並行追記の証拠なし）は予算に関わらず**即時**送出する（無駄な再読取をしない）。
_TORN_TAIL_WAIT_SEC = 0.005
_TORN_TAIL_BUDGET_SEC = 0.25


def _file_size(csv_path: Path) -> int:
    """ファイルサイズ（取得不能は -1）。読取の前後で変われば並行追記が起きた証拠になる。"""
    try:
        return os.stat(csv_path).st_size
    except OSError:
        return -1


def _tail_is_incomplete(csv_path: Path) -> bool:
    """ファイル末尾が改行で終わっていない＝**追記が進行中**であることを O(1) で判定する。

    テキスト CSV の完全な状態は必ず改行で終わる。終わっていなければ、書き手が最終行を
    書いている途中の瞬間を掴んでいる。空ファイルは「不完全」とはしない（別の異常）。
    """
    try:
        with open(csv_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return False
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) != b"\n"
    except OSError:
        return False


def _retry_while_being_appended(csv_path: Path, read_once):
    """並行追記中の CSV を安全に読む（ISSUE-186）。``read_once`` は「読取＋整形」全体。

    常駐 watch（``tools/live_tick_watch.py --stream`` / ``tools/export_jp225_m1.py --watch``）が
    276MB / 300MB の CSV へ末尾追記している最中に読むと、**行の途中**を掴む。実測では
    ``pd.to_datetime`` が ``time data "2026-01-01 0" doesn't match format`` で落ち、無関係な
    実データ依存テストが一斉に失敗した（1 回だけ 13 failed / 405 passed、直後から 14 回連続
    418 passed）。非決定的な失敗は回帰判定の信頼性を壊し、挙動不変検証で偽陽性・偽陰性の
    両方を生む。

    方針は「楽観読取＋検証」。失敗したときに**並行追記が起きていた証拠**があれば短く待って
    読み直し、無ければ本物のデータ異常として**そのまま送出する**（欠陥を隠さない）。証拠は 2 つ:
      1. いま末尾が改行で終わっていない＝まさに書いている最中
      2. 読取の前後でサイズが変わった＝読んでいる間に書かれた

    **包む範囲が要点**: 支配的な失敗は ``pd.read_csv`` ではなく**後段の時刻列変換**で起きる。
    `read_csv` だけを包んだ実装では最悪ケースの失敗率が 34% のまま変わらなかった（実測）。

    ``on_bad_lines="skip"`` は誤った対策である（実測で否定済み・32.9% → 34.3% と改善しない）。
    torn 行は列数が合うことがあり、その場合に落ちるのは時刻のパースだからである。

    書き手側でも追記を 1 回の ``write`` にまとめている（:func:`marketdata.tick_m1._append_m1_csv`）。
    実測の失敗率は 32.61%（分割 write）→ 0.07%（単一 write）。本関数はその残りを塞ぐ。
    """
    deadline = time.monotonic() + _TORN_TAIL_BUDGET_SEC
    while True:
        size_before = _file_size(csv_path)
        try:
            return read_once()
        except (ValueError, pd.errors.ParserError):
            appended_during_read = (
                _tail_is_incomplete(csv_path) or _file_size(csv_path) != size_before
            )
            if not appended_during_read:
                raise                       # 完結したファイルでの失敗＝本物の異常。隠さない。
            if time.monotonic() >= deadline:
                raise                       # 予算超過＝追記が止まらない異常。握りつぶさない。
            time.sleep(_TORN_TAIL_WAIT_SEC)


def read_ohlc_csv_with_policy(
    path: str | Path,
    read_csv_kwargs: Mapping[str, Any],
    *,
    time_column: str | None = None,
    require: tuple[str, ...] = _REQUIRED,
    cast_column_names: bool = False,
    require_non_empty: bool = False,
) -> pd.DataFrame:
    """方針を受け取って CSV を読み込み、必須列を備えた DataFrame を返す。

    Args:
        path: CSV ファイルパス。
        read_csv_kwargs: pandas.read_csv へそのまま渡す追加引数（sep 等）の Mapping。
            方針パラメータとの名前衝突を避けるため位置引数で受ける。
        time_column: 指定すると当該列を datetime 化して index に設定する（大小不問）。
        require: 必須列（既定は open/high/low/close）。
        cast_column_names: True なら列名を ``str(c).lower()`` で正規化する。False なら
            ``c.lower()``（非文字列の列名は AttributeError を送出する）。
        require_non_empty: True なら読み込み結果が 0 行のとき ValueError を送出する。
            判定順は「必須列 → 空行 → 時刻列」で固定する。

    Returns:
        必須列（および任意の追加列）を持つ DataFrame。行は時系列昇順を前提とする。

    Raises:
        FileNotFoundError: ファイルが存在しない場合。
        KeyError: 必須列が欠けている場合、または指定の時刻列が存在しない場合。
        ValueError: require_non_empty=True かつ読み込んだ CSV が空（0 行）の場合。
        AttributeError: cast_column_names=False かつ列名が文字列でない場合。
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV が見つかりません: {csv_path}")

    # ISSUE-186: 「読取＋整形」を 1 単位として並行追記へ耐える（時刻列の変換まで含める）。
    return _retry_while_being_appended(
        csv_path,
        lambda: _read_and_shape(
            csv_path, read_csv_kwargs,
            time_column=time_column, require=require,
            cast_column_names=cast_column_names, require_non_empty=require_non_empty,
        ),
    )


def _read_and_shape(
    csv_path: Path,
    read_csv_kwargs: Mapping[str, Any],
    *,
    time_column: str | None,
    require: tuple[str, ...],
    cast_column_names: bool,
    require_non_empty: bool,
) -> pd.DataFrame:
    """CSV を 1 回読んで列検証・時刻列の index 化まで行う（:func:`read_ohlc_csv_with_policy` の本体）。"""
    df = pd.read_csv(csv_path, **read_csv_kwargs)

    if cast_column_names:
        lower_map = {str(c).lower(): c for c in df.columns}
    else:
        lower_map = {c.lower(): c for c in df.columns}

    missing = [k for k in require if k not in lower_map]
    if missing:
        raise KeyError(
            f"CSV に必須列が不足しています: {missing}（存在する列: {list(df.columns)}）"
        )

    if require_non_empty and len(df) == 0:
        raise ValueError("CSV が空です（0 行）。計算には 1 行以上が必要です。")

    if time_column is not None:
        tcol = lower_map.get(time_column.lower(), time_column)
        if tcol not in df.columns:
            raise KeyError(f"指定された時刻列が存在しません: {time_column}")
        df[tcol] = pd.to_datetime(df[tcol])
        df = df.set_index(tcol)

    return df


def load_ohlc_csv(
    path: str | Path,
    *,
    time_column: str | None = None,
    **read_csv_kwargs,
) -> pd.DataFrame:
    """CSV を読み込み OHLC 列を備えた DataFrame を返す。

    ``require`` は公開しない。本関数へ ``require=`` を渡した場合は従来どおり
    pandas.read_csv へ転送され ``TypeError`` となる（挙動不変）。

    Args:
        path: CSV ファイルパス。
        time_column: 指定すると当該列を datetime としてパースし index に設定する
            （列名は大文字小文字を区別しない）。None なら既定の連番 index。
        **read_csv_kwargs: pandas.read_csv へそのまま渡す追加引数（sep 等）。

    Returns:
        open/high/low/close 列（および任意の追加列）を持つ DataFrame。

    Raises:
        FileNotFoundError: ファイルが存在しない場合。
        KeyError: open/high/low/close のいずれかが欠けている場合。
    """
    return read_ohlc_csv_with_policy(
        path, read_csv_kwargs, time_column=time_column, require=_REQUIRED
    )
