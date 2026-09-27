"""OHLC CSV の parse を 1 プロセス 1 実体 1 回にする読み口（ISSUE-541 段 2）。

なぜ在るか（実測 2026-09-27・cProfile）:
    1 回の run が同じ系列 CSV（147MB・2,148,667 行）を **12 回** parse し、固定費 24.0 秒の
    うち 18.8 秒を占めていた。出力は 1 ビットも変わらないため状態検証では原理的に落ちない
    （ISSUE-450/257 と同型）。読みの単一点をここに置き、2 つの読み口
    （sources 側の _read_or_data_error と _ohlc_frame 側の read_csv_or_data_error）が同じ実体を
    2 度 parse しない形にする。

契約:
    * 返す DataFrame は**読むだけ**にする（呼び出し側の契約。RunTracePort の observe の
      引数と同じ規律）。書き換えれば同じ実体を読む他の消費者の入力が変わる。列を足す・
      値を書く消費者は自分で copy する。既存の消費者は全員読むだけである（rename・
      astype・reset_index・rolling はいずれも新しいオブジェクトを返す）。
    * 鍵は (実パス, mtime_ns, サイズ, sep)。実体が書き換われば鍵が変わり読み直す。
      run の子プロセスは 1 ジョブで死ぬため、キャッシュの寿命は 1 ジョブである。
    * `usecols` は**射影**であり parse ではない——全列の parse 1 回から選ぶ
      （usecols だけのために 2 回目の parse を発行しない）。
    * 上記以外の読みオプションは受けない（`ConfigError` で止める）。黙って素通しすると、
      新しいオプションつきの読みがキャッシュ鍵に載らず、別条件の読みが同じ鍵を共有する。

観測の境界（検査側の設計・絶対命令 2026-09-25）:
    計算量検定は内部名を monkeypatch しない。本モジュールが**測るための注入点**を宣言する:
    `set_reader`（parse の実体の差し替え・既定 pandas.read_csv）と `parse_log`（発行した
    parse の鍵の列）。期待値は「発行 − 相異なる実体の数 = 0」で表明し、回数そのものは
    焼き込まない。`clear` はテストが 1 run ずつ独立に測るための後始末である。
"""
from __future__ import annotations

import importlib.util
import os
from typing import Any, Callable

import pandas as pd

from simulator.domain.exceptions import ConfigError

#: parse エンジン（ISSUE-541 段 3）。pyarrow は複数スレッドで parse し、同じ 147MB の
#: 実体で 1.22 秒 → 実測で数分の一になる。pyarrow は tick-store（tick_parquet）が既に
#: 依存している＝新しいライブラリではない。導入されていない環境では pandas 既定の
#: C パーサ（値は同じ・遅いだけ）。判定は import 時に 1 回（呼び出しごとに黙って
#: 切り替わらない・テストが読める定数）。
PARSE_ENGINE: "str | None" = (
    "pyarrow" if importlib.util.find_spec("pyarrow") is not None else None
)

#: parse の実体（注入点）。既定は pandas。例外の内側翻訳は呼び出し側の読み口が行う
#: （sources 側と _ohlc_frame 側の読み口＝従来どおり）。
_reader: "Callable[..., pd.DataFrame]" = pd.read_csv

#: (実パス, mtime_ns, サイズ, sep) → parse 済み DataFrame。
_frames: "dict[tuple, pd.DataFrame]" = {}

#: 発行した parse の鍵の列（観測境界。検定は「発行 − 相異なる実体 = 0」を表明する）。
parse_log: "list[tuple]" = []

#: 派生値の memo（鍵 = (frame の同一性, 名前)）。同じ frame からの同じ導出（例: 時刻列の
#: UTC 解釈）を 2 度計算しない（ISSUE-541 段 3。実測: 全列 to_datetime ×4 で 1.36 秒）。
#: frame はキャッシュが生かしている実体だけを鍵にする（id は frame が生きている間だけ一意）。
_derived: "dict[tuple, Any]" = {}


def set_reader(reader: "Callable[..., pd.DataFrame] | None") -> None:
    """parse の実体を差し替える（``None`` で既定へ戻す）。検定の注入点。"""
    global _reader
    _reader = pd.read_csv if reader is None else reader


def clear() -> None:
    """キャッシュと発行記録を空にする（テストが run を独立に測るための後始末）。"""
    _frames.clear()
    parse_log.clear()
    _derived.clear()


def memo_on(frame: pd.DataFrame, name: str, fn: "Callable[[pd.DataFrame], Any]") -> Any:
    """``frame`` からの導出 ``fn`` を、同じ (frame, name) につき 1 回だけ計算する。

    事前条件: ``fn`` は frame を読むだけ（書き換えない）。戻り値も共有実体になるため
        呼び出し側は読むだけにする（read_frame の契約と同じ規律）。
    事後条件: 同じ frame・同じ名前への 2 回目以降は同じ実体を返す。

    キャッシュ外の frame（テストの合成 frame 等）にも使える——その場合の寿命は本 memo が
    参照を持つ間（clear まで）である。
    """
    key = (id(frame), name)
    if key not in _derived:
        # id の再利用（元 frame の解放後に別オブジェクトが同じ id を得る）で別物の導出を
        # 返さないよう、frame 自体も值として保持する（生存保証＋同一性の検証）。
        _derived[key] = (frame, fn(frame))
    held, value = _derived[key]
    if held is not frame:
        _derived[key] = (frame, fn(frame))
        held, value = _derived[key]
    return value


def _key_of(source_ref: Any, sep: "str | None") -> tuple:
    path = os.path.realpath(str(source_ref))
    stat = os.stat(path)
    return (path, stat.st_mtime_ns, stat.st_size, sep)


def read_frame(
    source_ref: Any, *, sep: "str | None" = None, usecols: "list | None" = None, **unknown: Any
) -> pd.DataFrame:
    """実体を parse 済み DataFrame として返す（同じ実体は 1 回だけ parse する）。

    事前条件: ``source_ref`` は実在するファイルパス（stat できない実体は OSError を
        そのまま送出し、呼び出し側の読み口が内側例外へ翻訳する）。
    事後条件: 同じ (実体, sep) への 2 回目以降は同じ DataFrame 実体を返す。``usecols``
        は全列 parse からの射影（列の欠落は KeyError → 呼び出し側が翻訳）。
    例外: 未知の読みオプションは ``ConfigError``（黙って鍵の外で読まない）。
    """
    if unknown:
        raise ConfigError(
            f"ohlc_frame_cache が受けない読みオプションです: {sorted(unknown)}",
            context={"options": sorted(unknown)},
        )
    key = _key_of(source_ref, sep)
    frame = _frames.get(key)
    if frame is None:
        options: "dict[str, Any]" = {} if sep is None else {"sep": sep}
        if _reader is pd.read_csv and PARSE_ENGINE is not None:
            # エンジン指定は既定 reader のときだけ（注入された検定用 reader へ渡すと
            # 偽の引数になる）。値は同じでパースだけ速い（段 3）。
            options["engine"] = PARSE_ENGINE
        frame = _reader(source_ref, **options)
        _frames[key] = frame
        parse_log.append(key)
    if usecols is not None:
        return frame[list(usecols)]
    return frame
