"""rollup — 上位足の増分ロールアップ（メモリ有界化・enabler③④・rollup_builder から物理移設）。

server が 4.5M 行 / 284MB の 1 分足を全ロードして OOM する問題に対し、1 分足を二度と
全ロードしない設計の生成側を担う。1 分足を ``pd.read_csv(chunksize=...)`` でストリーム読みし、
各時間足（TF）を :func:`marketdata.resample.resample_ohlc`（marketdata の規則を再利用・再実装しない）で
集約して TF 別ロールアップ CSV（``date,open,high,low,close,volume``・loader 互換）へ書き出す。

依存方向（厳守）: 本モジュールは pandas + :mod:`marketdata.resample` + :mod:`marketdata.tail_reader`
+ :mod:`marketdata.rollup_paths`（配置権威・ISSUE-502 D-16）+ :mod:`marketdata.csv_schema` に
のみ依存し、indicator_ui を逆 import しない（marketdata の循環依存禁止・設計 §4）。

メモリ有界（厳守）:
    全行を同時に pandas へ載せない。``chunk_rows`` 単位でストリーム読みし、チャンク跨ぎの未確定
    period は carry-over（確定まで書き出さない・D-1）する。:func:`merge_same_period` の結合性が
    チャンク分割と全件 resample の数値一致を保証する。

数値一致の根拠:
    resample 規則（W-FRI/ME/5min/tz/closed/label）を再実装せず必ず
    :func:`marketdata.resample.resample_ohlc` を呼ぶ。チャンク末尾の未確定 period のみ次チャンクへ
    繰り越し、確定 period のみ書き出すことで ``stream_build`` 結果は ``resample_ohlc(全件, rule)`` と
    完全一致する。

enabler④（銘柄汎用化・§10.3 M-3）:
    ロールアップ CSV のファイル名 prefix は ``ref_prefix``（既定 ``"jp225_m1"``）引数で外部化する。
    ``ref_prefix`` は :func:`_rollup_path` と :class:`_RollupWriter` の**両所**に通し、
    :func:`stream_build` / :func:`incremental_update` から伝播する（既定値で全既存呼出は不変）。
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, Protocol, TextIO, runtime_checkable

import numpy as np
import pandas as pd

# marketdata の resample 規則を再利用する（再実装しない・indicator_ui を逆 import しない）。
from marketdata import resample as _resample
from marketdata import rollup_paths as _rollup_paths
from marketdata import tail_reader

# 増分更新で「state 以降の新規 1 分足」を逆シークで拾う末尾 probe 行数。--watch は毎分 ~1 行
# 追記なので十分大（≈14 日分の連続 1 分足）。probe が last_ts を内包できない長期 catch-up のみ
# 全件スキャンへフォールバックする。
_INCREMENTAL_TAIL_PROBE_ROWS = 20_000

# ロールアップ CSV の列・date 書式は marketdata.csv_schema が唯一の規則源
# （旧: tick_m1._HEADER / _DATE_FMT と手動同期）。旧属性名は import 共有で温存する。
from marketdata import csv_schema as _csv_schema

logger = logging.getLogger(__name__)

_HEADER = _csv_schema.HEADER
_DATE_FMT = _csv_schema.DATE_FMT
# ロールアップ CSV のファイル名 prefix の既定（jp225_m1 由来・<prefix>_<tf>.csv）。
# §10.3 M-3: 銘柄汎用化のため ref_prefix 引数で外部化（既定でこの値）。
_REF_PREFIX = "jp225_m1"
_STATE_FILENAME = "rollup_state.json"


def merge_same_period(prev_bar: dict[str, Any], new_bar: dict[str, Any]) -> dict[str, Any]:
    """同一 period の 2 つの partial bar を結合する（結合的）。

    列ごとの畳み方は :data:`marketdata.csv_schema.VALUE_COLUMN_LEDGER`（値列台帳）が持つ
    （規則をここへ書き写さない）。必須列は欠けていたら :class:`KeyError`、任意列は**両者が
    持つときだけ**運ぶ。結合的（``merge(merge(a,b),c) == merge(a,merge(b,c))``）であり、
    チャンク跨ぎ carry-over の正しさ（D-1）の根拠となる。
    """
    # 必須列は欠けていたら KeyError で落とす（黙って列を落とさない＝従来の契約）。
    merged = {
        col: _csv_schema.combine_for(col)(prev_bar[col], new_bar[col])
        for col in _csv_schema.required_columns()
    }
    # 任意列（方向内訳 up/dn・気配幅 spread）は**両者が持つときだけ**運ぶ（結合的）。
    #   かつてここは辞書リテラル 5 キー + up/dn ループだけで、台帳へ列が増えても本経路が
    #   その列を運ばず、チャンク跨ぎの carry-over で列ごと消えていた（spread が該当）。
    for col in _csv_schema.optional_columns():
        if col in prev_bar and col in new_bar:
            merged[col] = _csv_schema.combine_for(col)(prev_bar[col], new_bar[col])
    return merged


@dataclass
class RollupState:
    """ロールアップ進捗状態（増分更新の基点）。``last_processed_ts`` 以降だけを増分処理する。"""

    last_processed_ts: datetime

    def save(self, out_dir: Path) -> None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {"last_processed_ts": self.last_processed_ts.strftime(_DATE_FMT)}
        (out_dir / _STATE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def load(cls, out_dir: Path) -> Optional["RollupState"]:
        path = Path(out_dir) / _STATE_FILENAME
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        ts = datetime.strptime(payload["last_processed_ts"], _DATE_FMT)
        return cls(last_processed_ts=ts)


def rollup_timeframes() -> "tuple[str, ...]":
    """ロールアップ対象の上位足（原子 ``"1m"`` を除く全 TF）の唯一源（ISSUE-262）。

    かつて ``tools/build_tick_rollup`` と ``tools/live_tick_watch`` が同一実装を各自に持ち、
    後者の docstring は「前者と同規則」と**人手同期**を宣言していた。``tools`` パッケージが
    「ロジックの重複を持たない合成点」と宣言している以上、規則は本モジュールに置く。
    """
    from marketdata import resample

    return tuple(tf for tf in resample.TIMEFRAME_RULES if tf != "1m")


def _rollup_path(out_dir: Path, tf: str, ref_prefix: str = _REF_PREFIX) -> Path:
    """ロールアップ CSV の解決パス（``<out_dir>/<ref_prefix>_<tf>.csv``）。

    §10.3 M-3: ``ref_prefix``（既定 ``"jp225_m1"``）で銘柄を汎用化する。既定値で全既存呼出は不変。
    ファイル名の綴りは :func:`marketdata.rollup_paths.csv_name`（配置権威）が唯一所有する
    （ISSUE-502 D-16）。本関数は「書き手が決めた ``out_dir`` の下で解決する」呼出点である。
    """
    return _rollup_paths.csv_path(out_dir, ref_prefix, tf)


class RollupCellCastError(ValueError):
    """値のあるセルを台帳の型へ戻せなかった失敗（列名と period を添える）。

    欠損（空欄）はこの失敗に当たらない。欠損は cast せず欠損のまま通す規約であり、本例外は
    **欠損以外**の理由で cast が失敗したときだけ上がる（素材破損・型の取り違え等）。
    ``ValueError`` の下位型にしてあるのは、是正前に ``int()`` が出していた ``ValueError`` を
    捕捉している呼出側の面を狭めないためである。
    """


def _cell_cast_error(col: Any, value: Any, period: Any) -> RollupCellCastError:
    """失敗の言い方の単一定義（列名と period を添える）。"""
    return RollupCellCastError(
        f"列 {col} の値を台帳の型へ戻せません（period={period}・値={value!r}）"
    )


#: 台帳の cast と欠損判定が出しうる失敗（実測 pandas 3.0.3 / numpy 2.4.6 / CPython 3.13.5）。
#:
#: - 台帳の cast: 非数字の文字列は ``ValueError``、``tuple`` / ``dict`` / ``set`` / ``None`` は
#:   ``TypeError``、無限大は ``OverflowError``（``ValueError`` の下位型では**ない**）。
#: - 欠損判定そのもの: 要素 2 つ以上の ``list`` / ``ndarray`` は ``pd.isna`` が要素ごとの配列を
#:   返し、``bool()`` が ``ValueError`` を出す（cast へ到達しない）。
#: - pandas の ``astype``: 欠損や無限大を含む整数化は ``IntCastingNaNError``（``ValueError`` の
#:   下位型・実測）、dtype として解釈できない規則は ``TypeError``。
_CAST_FAILURES = (TypeError, ValueError, OverflowError)


class _ColumnCaster:
    """1 列ぶんの型付け（台帳の規則を**列につき 1 回**引いて持つ）。

    型の規則そのものは :func:`marketdata.csv_schema.cast_for` が所有し続ける。本クラスが足すのは
    (1) 欠損の扱いと (2) period という文脈の 2 つだけである（``csv_schema`` は依存ゼロ＝stdlib
    のみで、pandas の欠損も period も知れない）。

    規則を列につき 1 回だけ引くのは、``cast_for`` が台帳の行を引くたびに列名を
    ``str().lower()`` し直すためである。この引き直しは出力に 1 バイトも現れないので、値の検査でも
    「cast の発行数」を数える検定（CX-C）でも落ちない。**cast の発行は値ごとに保つ**（減らすのは
    引き直しであって仕事の量ではない）。引き回数は CX-H が別に固定する。

    規約（3 つの適用点で共通）:
        - 欠損は cast せず欠損のまま通す。
        - 値のあるセルだけ台帳の型で書く（型を**列単位**でなく**値単位**で決める）。
        - 欠損**以外**の理由で失敗したら :class:`RollupCellCastError` へ包み直す。

    ``period`` を渡すのは、失敗を列名と period で名指したいときだけでよい。渡さない呼出の失敗は
    素の失敗（:data:`_CAST_FAILURES`）のまま上がる。文脈を作るのは失敗したときだけでよく、
    成功する限り period は出力に 1 バイトも現れない（CX-P が「生成 − 失敗 = 0」で固定する）。
    """

    __slots__ = ("column", "rule")

    def __init__(self, column: Any) -> None:
        self.column = column
        # 束縛名を ``cast`` のような一般語にしない: 宣言整合性検定（C1）の記号索引は入れ子の
        #   代入先も「リポジトリに実在する記号」として数えるため、一般語を束縛すると、その語を
        #   バッククォートで名指している**無関係なモジュール**のコメントが到達不能違反になる
        #   （実測: marketdata/tests/test_symbol_spec_snapshot.py:331 の `cast`）。
        self.rule = _csv_schema.cast_for(column)

    def __call__(self, value: Any, *, period: Any = None) -> Any:
        """1 セルを台帳の型へ戻す（欠損は cast せず素通し）。"""
        try:
            if bool(pd.isna(value)):
                return value
            return self.rule(value)
        except _CAST_FAILURES as exc:
            if period is None:
                raise
            raise _cell_cast_error(self.column, value, period) from exc


def _cell_caster(col: Any) -> "_ColumnCaster":
    """列 1 つぶんの型付けを作る（規則は**列につき 1 回**引く）。規約は :class:`_ColumnCaster`。"""
    return _ColumnCaster(col)


def _cast_cell(col: Any, value: Any, *, period: Any) -> Any:
    """1 セルを台帳の型へ戻す（欠損は cast せず素通し）。

    規約は :class:`_ColumnCaster` が 1 つ持つ。本関数は「1 セルだけ」を扱う呼出の入口であり、
    列ごとの caster を持ち回れない場面（検定・単発の呼出）のためにある。**列の全値**を扱う
    場合は :func:`_cast_column`、**1 バーの全列**を扱う場合は :func:`_casters_for` が作った
    caster を行ループの外から渡すこと（どちらも規則を列につき 1 回しか引かない）。
    """
    return _cell_caster(col)(value, period=period)


def _casters_for(columns: Any) -> "dict[str, _ColumnCaster]":
    """型付けする列ごとに caster を 1 つ作る（規則は**列につき 1 回**だけ引く）。

    列と順序の唯一源は台帳（:func:`marketdata.csv_schema.header_columns`）であり、本関数は
    そこから「素材が実際に持つ列」だけを選ぶ。**行ループの外で 1 度作り、ループの中へ渡す**こと。
    ループの中で作ると規則の引き直しが行数に比例する（実測・是正前: ``stream_build`` が
    ``cast_for`` を引く回数は出力 10 行で 144 回・100 行で 1440 回＝書いたセル 1 つあたり 2.4 回。
    :func:`_resample_chunk` と :class:`_RollupWriter` は型付け列 6・出力 4 本で 24 回、
    40 本で 240 回引いていた）。引き直しは出力に 1 バイトも現れないので、値の検査でも
    cast の発行数を数える検定（CX-C）でも落ちない。CX-H が経路ごとに固定する。
    """
    return {col: _cell_caster(col) for col in _csv_schema.header_columns() if col in columns}


def _caster_of(casters: "dict[str, _ColumnCaster]", col: Any) -> "_ColumnCaster":
    """持ち回りの caster から ``col`` のものを採る（持っていない列はその場で作る）。

    行ループの外で作った caster は「見本にした bar／frame が持つ列」ぶんしかない。見本より
    列の多い bar が来ても**是正前と 1 バイトも変わらない行を書く**ために、その場で作って配る。
    持ち上げが変えてよいのは規則を**引く回数**だけであって、書く内容ではない。

    実測（この分岐が無い形と是正前を突合）: 見本より列の多い bar で出力が食い違った
    （是正前は余った列も書き、持ち上げた側は落としていた）。本番の呼出でこの形が出るかは
    **未検証**であり、ここで揃えているのは「持ち上げは表示を変えない」という不変条件である。
    """
    return casters[col] if col in casters else _cell_caster(col)


def _csv_cell(caster: "_ColumnCaster", value: Any, *, period: Any) -> Any:
    """``csv.writer`` の面の 1 セル（欠損は空欄で書く）。

    実測: ``csv.writer`` は非数（float の欠損）を 3 文字の文字列として書く（空文字列と None は
    空欄）。pandas の ``to_csv`` は欠損を空欄で書くため、揃えないと同じ欠損が経路によって
    2 通りに書かれる。
    """
    cell = caster(value, period=period)
    return "" if pd.isna(cell) else cell


def _cast_column(col: Any, values: "pd.Series") -> "pd.Series":
    """1 列を台帳の型へ戻す（欠損は欠損のまま）。

    型付けの規約は :class:`_ColumnCaster` が 1 つ持ち、本関数が決めるのは**どう配るか**だけで
    ある（型を列単位で決めるのではない。欠損の有無で cast するしないを変えない）。

    - 欠損の**無い**列は列ごと配る（``astype``）。値単位に配った結果と出力バイトが一致することは、
      凍結スナップショットの実ロールアップ 32 ファイル（最大 982,274 行・8 列形と 6 列形の両方）で
      実測した。``astype`` は失敗を取りこぼさない（:data:`_CAST_FAILURES` の注記）。
    - 欠損の**混ざる**列は値単位に配る（pandas の整数 dtype は欠損を表現できない）。
    - どちらかが失敗したときだけ、遅い経路（:func:`_name_the_failing_cell`）で列名と period を
      特定する。

    period を値ごとに添えないのは、それが失敗の名指しにしか使われないためである。成功する限り
    出力に 1 バイトも現れないので、値の検査でも CX-C でも CX-H でも**原理的に落ちない**。
    実測（1,000,000 行 × 8 列・min of 3・出力は byte 一致）: 値ごとに添える形は 6.414 秒、
    列ごとに配る形は 0.027 秒（237 倍）。メモリも ``dtype=object`` 化で実ロールアップ相当
    （982,272 行 × 5 列）が 47.1MB → 165.0MB（3.5 倍）になっていた。本モジュールは冒頭で
    「1 分足を二度と全ロードしない＝OOM を避ける」ことを存在理由に掲げている。
    """
    caster = _cell_caster(col)
    if not bool(values.isna().any()):
        try:
            return values.astype(caster.rule)
        except _CAST_FAILURES:
            # 列ごとには配れない（値が壊れている／規則が dtype として解釈できない）。
            #   値単位へ落とし、そこでも失敗するなら列名と period で名指す。
            pass
    return _cast_values(values, caster)


def _cast_values(values: "pd.Series", caster: "_ColumnCaster") -> "pd.Series":
    """列を**値単位**で配る（欠損は欠損のまま・period は添えない）。

    dtype を object で固定するのは、``map(cast, na_action="ignore")`` だと pandas が結果の
    dtype を再推論して float64 へ戻し ``70.0`` と書かれるためである（実測 pandas 3.0.3）。
    """
    # 束縛名を ``cast`` のような一般語にしない（:class:`_ColumnCaster` の注記と同じ理由。
    #   実測: 一般語を束縛すると test_symbol_spec_snapshot.py の無関係なコメントが C1 違反になる）。
    try:
        typed_values = [caster(value) for value in values]
    except _CAST_FAILURES:
        _name_the_failing_cell(values, caster)
        raise
    return pd.Series(typed_values, index=values.index, dtype=object)


def _name_the_failing_cell(values: "pd.Series", caster: "_ColumnCaster") -> None:
    """失敗した 1 セルを、今度は period を添えて通し直す（**失敗したときだけ**通る遅い経路）。

    値と period を組にする仕事はここにしかない。成功する限り 1 度も走らないので、出力に
    現れない仕事を作らない（CX-P が「period 文脈の生成 − cast 失敗数 = 0」で固定する）。
    通し直した呼出が :class:`RollupCellCastError` を送出するため、本関数は正常に戻らない。
    戻った場合（2 度目は通った場合）は呼び手が元の失敗をそのまま送出する。
    """
    for period, value in values.items():
        try:
            caster(value)
        except _CAST_FAILURES:
            caster(value, period=period)


def _bar_to_dict(row: pd.Series,
                 casters: "dict[str, _ColumnCaster] | None" = None) -> dict[str, Any]:
    """resample 済みの 1 行を bar 辞書へ（列・順序・型の唯一源は csv_schema の値列台帳）。

    型を台帳の ``cast`` で戻すのは、``resample().agg()`` が空き期間を NaN で埋める際に
    整数列（spread）を float へ昇格させるためである（``dropna`` は昇格の後に走るので
    取り消せない）。任意列は持つときだけ運ぶ（無い素材の出力は 1 バイトも変わらない）。

    :func:`marketdata.csv_schema.cast_for` の**適用点 3 箇所のうちの 1 つ**。ここが守るのは
    **bar 辞書の面**（``_resample_chunk`` / ``_resample_suffix`` が作り、``stream_build`` の
    carry-over と writer へ渡る値）である。実測（本適用だけを撤去）: 休場を挟んだ素材で
    bar の spread が ``np.float64(70.0)`` になる。

    型付けは**値単位**で行う。欠損セルは cast せず欠損のまま運ぶ（carry-over の
    :func:`merge_same_period` が畳める形で残す）。是正前はここで ``int(NaN)`` が
    ``ValueError`` を出し、spread 列を得る前に書かれた period で ``stream_build`` が
    止まっていた（実測）。

    ``casters`` は :func:`_casters_for` が作った列ごとの caster で、**行ループの外**から
    渡す（省略時はこの 1 行ぶんだけ作る）。渡さないと規則の引き直しが行数に比例する。
    """
    casters = _casters_for(row.index) if casters is None else casters
    return {col: _caster_of(casters, col)(row[col], period=row.name)
            for col in _csv_schema.header_columns() if col in row.index}


def _header_for_bars(bars: "dict[Any, dict[str, Any]]") -> list[str]:
    """bars の内容からロールアップ CSV ヘッダを決める（任意列は持つときだけ末尾へ足す）。"""
    sample = next(iter(bars.values()), {})
    return _csv_schema.header_for(
        [c for c in _csv_schema.header_columns() if c in sample]
    )


def _merge_agg(columns: Any) -> "dict[Any, str]":
    """既存 CSV と新規 tail をマージするときの列別集約規則（実在列から導出・単一定義）。

    規則は :func:`marketdata.resample.resample_ohlc` と同一であり、どちらも
    :mod:`marketdata.csv_schema` の値列台帳から導出する（規則の第 2 定義を作らない）。
    列名を呼び出し側へ直書きしないことで、台帳へ列が増えても本経路が列を落とさない。
    """
    return {col: _csv_schema.agg_for(col) for col in columns}


def _header_of(path: Path) -> "list[str] | None":
    """ロールアップ CSV の 1 行目（ヘッダ）を列名リストで返す（不在・空は ``None``）。"""
    try:
        with open(path, "r", newline="", encoding="utf-8") as fh:
            line = fh.readline()
    except OSError:
        return None
    line = line.strip()
    return line.split(",") if line else None


def _bar_to_csv_row(period: Any, bar: dict[str, Any],
                    casters: "dict[str, _ColumnCaster] | None" = None) -> list[Any]:
    """(period, bar) を ロールアップ CSV の 1 行（loader 互換）へ整形する（単一定義）。

    ``_write_rollup``（全件一括書き）と :class:`_RollupWriter`（逐次 flush）で同一フォーマットを
    共用し、両経路の出力 CSV をバイト一致させるための行整形の単一真実源。

    :func:`marketdata.csv_schema.cast_for` の**適用点 3 箇所のうちの 1 つ**。ここが守るのは
    **``csv.writer`` で書く行の面**（``_RollupWriter`` の逐次 flush と、増分の追記
    ``_truncate_append_bars``）である。``stream_build`` 経由では :func:`_bar_to_dict` が先に
    整数へ戻すため、本適用だけを撤去しても ``stream_build`` の出力は変わらない（遮蔽・実測）。
    本適用単独の効きは、float 値を持つ bar を直接渡したときに観測できる（撤去すると
    ``70.0`` が行へ入る）。
    """
    casters = _casters_for(bar) if casters is None else casters
    row: "list[Any]" = [pd.Timestamp(period).strftime(_DATE_FMT)]
    # 列・順序・型は台帳が唯一源（書く直前に cast で型を戻す＝spread は整数で書かれる）。
    #   型付けは値単位。欠損セルは空欄で書く（_csv_cell）。caster は行ループの外から渡す
    #   （省略時はこの 1 行ぶんだけ作る）。
    row.extend(_csv_cell(_caster_of(casters, col), bar[col], period=period)
               for col in _csv_schema.header_columns() if col in bar)
    return row


class _AtomicCsvSwap:
    """確定パスと同一ディレクトリの一時ファイルへ書き、:meth:`commit` で原子スワップする実体。

    原子化（🔴）の**唯一の実装**（ISSUE-479 M-1）。かつては同じ tmp→``os.replace`` の手順が
    ``_write_rollup`` / ``_write_rollup_df`` / :class:`_RollupWriter` の 3 箇所に手書きされており、
    1 箇所を直しても残り 2 箇所が古いまま残る形だった（先例: mt5_ticks の取込側にある同型の原子化）。

    なぜ原子化が要るか: ``--watch`` は毎分ロールアップの全書き直しを行うため、書込中の
    crash/OOM-kill で確定パスに部分 CSV が残ると、cold-start の server が torn-read
    フォールバック不能のまま不完全データを配信する。tmp→replace により確定パスは
    「完全な新 CSV」か「旧 CSV」のいずれかに限定される。

    :meth:`commit` されないまま :meth:`abort` されたときは tmp を破棄し確定パスを汚さない（冪等）。
    """

    def __init__(self, final: Path) -> None:
        final = Path(final)
        final.parent.mkdir(parents=True, exist_ok=True)
        self.final = final
        fd, tmp_name = tempfile.mkstemp(
            dir=str(final.parent), prefix=final.name + ".", suffix=".tmp"
        )
        self._tmp: Optional[Path] = Path(tmp_name)
        self.file: TextIO = os.fdopen(fd, "w", newline="", encoding="utf-8")
        self._committed = False

    @property
    def committed(self) -> bool:
        return self._committed

    def commit(self) -> None:
        """tmp を閉じ確定パスへ原子スワップする（成功時のみ呼ぶ・冪等）。"""
        if self._committed:
            return
        self.file.close()
        os.replace(self._tmp, self.final)
        self._committed = True
        self._tmp = None

    def abort(self) -> None:
        """tmp を閉じて破棄する（未 commit 時のみ実効・冪等）。確定パスは触らない。"""
        if not self.file.closed:
            self.file.close()
        if not self._committed and self._tmp is not None:
            self._tmp.unlink(missing_ok=True)
            self._tmp = None


@contextmanager
def _atomic_csv(final: Path) -> "Iterator[TextIO]":
    """一度に書き切る経路のための原子スワップ（正常終了で commit・例外で tmp 破棄）。"""
    swap = _AtomicCsvSwap(final)
    try:
        yield swap.file
    except BaseException:
        swap.abort()
        raise
    swap.commit()


@runtime_checkable
class RollupWriter(Protocol):
    """確定 period バーの永続化境界（:func:`stream_build` が依存する抽象・ISSUE-479 M-1）。

    ``stream_build`` の責務は「チャンク跨ぎ carry-over を解いて、確定したバーを確定順に
    ちょうど 1 回ずつ渡す」ことであり、渡した先が CSV かどうかは関知しない。具象を本体が
    直接生成していた間、この責務はファイル出力なしには観測できなかった（DIP 違反）。

    - :meth:`write`: 確定済み 1 バーを渡す（呼び出しは date 昇順であること）。
    - :meth:`commit`: 成功確定（永続化を可視化する）。
    - :meth:`close`: 後始末（未 commit なら中断として扱う・冪等）。
    """

    def write(self, period: Any, bar: "dict[str, Any]") -> None: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...


#: 出力ディレクトリ・TF・ref_prefix から :class:`RollupWriter` を作る生成関数の型。
RollupWriterFactory = Callable[[Path, str, str], RollupWriter]


def _write_rollup(
    out_dir: Path, tf: str, bars: dict[Any, dict[str, Any]], ref_prefix: str = _REF_PREFIX
) -> None:
    """period→bar の辞書を date 昇順でロールアップ CSV へ**原子的に**書き出す（loader 互換形式）。

    書き出しの実体は :class:`_RollupWriter`（逐次 flush）を date 昇順で回すだけであり、行整形・
    ヘッダ決定・原子化を持たない（ISSUE-479 M-1: 出力規則の第 2 実装を作らない）。ヘッダは
    従来どおり **bars の反復順先頭**を見本に決める（``_header_for_bars`` と同一の見本選択）。
    """
    bars = dict(bars)
    writer = _RollupWriter(out_dir, tf, ref_prefix)
    try:
        writer._ensure_header(next(iter(bars.values()), None))
        for period in sorted(bars):
            writer.write(period, bars[period])
        writer.commit()
    finally:
        writer.close()


def _write_rollup_df(
    out_dir: Path, tf: str, df: pd.DataFrame, ref_prefix: str = _REF_PREFIX
) -> None:
    """date-index OHLCV DataFrame を date 昇順でロールアップ CSV へ**原子的に**書く（増分経路）。

    増分更新（ISSUE-012）の memory-bounded 経路。原子化は :func:`_atomic_csv`（唯一の実装）へ
    委譲し、確定パスを「完全な新 CSV」か「旧 CSV」のいずれかに限定する。出力列・date 書式
    （``_DATE_FMT``）・ヘッダは loader 互換（:mod:`marketdata.csv_schema`）で揃える。90 万件規模でも
    辞書化せず pandas の to_csv をストリーム書きするため RSS は DataFrame 1〜2 個分に有界化する。

    出力列は **実在する列から導出**する（``_bar_to_csv_row`` / ``_merge_agg`` と同じ規約・ISSUE-258）。
    かつてここは ``["open","high","low","close","volume"]`` を直書きしており、csv_schema へ up/dn が
    増えたとき**本経路だけが列を落とした**。当時はヘッダ不一致を直す経路が無く、6 列で書かれた
    まま方向内訳が恒久的に失われた（消費側の tickvol_updown は値を捏造せず KeyError で落ちる）。
    現在は :func:`incremental_update` が不一致を検出して全件 rewrite へ落とし**ヘッダごと書き直す**
    ため、**ヘッダ不一致を理由とする**転落は 1 回で終わる（実測 1h: 2 回目の増分は速い経路へ戻る）。
    probe が期間始端を覆えない tf（実測 1M: probe 20,000 行 ≒ 13.9 日 < 1 か月）は、この理由とは
    無関係に毎回ここを通る＝転落ではなく常態である。列の決定は csv_schema 1 点に閉じること。
    """
    final = _rollup_path(Path(out_dir), tf, ref_prefix)
    with _atomic_csv(final) as fh:
        cols = [c for c in _csv_schema.header_columns() if c in df.columns]
        out = df[cols].sort_index()
        out = out.copy()
        # 書く直前に台帳の型へ戻す（spread は整数）。型の**適用**がここ（pandas 面）にあるのは
        #   csv_schema が依存ゼロ（stdlib のみ・test_module_dependency_declarations が強制）で
        #   pandas の欠損を扱えないためであり、台帳の外に規則を置いているのではない。
        # cast_for の**適用点 3 箇所のうちの 1 つ**。ここが守るのは**全件 rewrite 経路**の表記で
        #   あり、_bar_to_dict / _bar_to_csv_row はこの経路を通らない（両者は bar 辞書面と
        #   csv.writer 面を守る）。死コードではない: 型付けを外すと、欠損なしの float64
        #   （休場を挟んで resample → dropna した df）で 70 が 70.0 になる（実測）。検定は
        #   test_rollup_spread_aggregation の
        #   test_the_full_rewrite_writes_the_spread_as_an_integer。
        # 是正前はここが notna().all() の**列単位**分岐で、欠損を 1 つでも含む列は cast を丸ごと
        #   飛ばしていた。そのため既に 70 と書かれていた行が全件 rewrite のたびに 70.0 へ戻る
        #   （実測）。値単位で決めれば、空欄は空欄のまま・値のあるセルだけ台帳の型で書ける。
        #   欠損を持たない列の出力バイトが是正前と一致することは S-11 / S-11b が固定する。
        for col in cols:
            out[col] = _cast_column(col, out[col])
        out.index = pd.DatetimeIndex(out.index).strftime(_DATE_FMT)
        out.index.name = "date"
        out.to_csv(fh, header=list(out.columns), index_label=_HEADER[0])


class _RollupWriter:
    """確定 period バーを date 昇順で逐次ファイルへ flush する writer（メモリ有界化）。

    ``stream_build`` の確定バー streaming-write 化（巨大期間でも確定済みバーを蓄積せず即時 flush）。
    ヘッダを最初に 1 行書き、以後 :meth:`write` を確定順（＝昇順）に呼ぶ。出力 CSV の内容・行順序は
    ``_write_rollup`` と完全一致する（行整形は :func:`_bar_to_csv_row` で共用・バイト一致）。

    原子化（🔴）は :class:`_AtomicCsvSwap`（唯一の実装）へ委譲する。一時ファイルへ逐次 flush し、
    :meth:`commit`（成功時）で確定パスへスワップする。:meth:`close` は未 commit なら tmp を破棄し
    確定パスを汚さない。これにより書込中 crash でも確定パスは「完全な新 CSV」か「旧 CSV」の
    いずれかに限定される。

    §10.3 M-3: ``ref_prefix``（既定 ``"jp225_m1"``）を ``__init__`` に追加し確定パス名へ反映する。
    本クラスは :class:`RollupWriter` プロトコルの既定の具象であり、``stream_build`` は
    ``writer_factory`` 未指定時にこれを使う（ISSUE-479 M-1）。
    """

    def __init__(self, out_dir: Path, tf: str, ref_prefix: str = _REF_PREFIX) -> None:
        import csv as _csv

        self._final = _rollup_path(Path(out_dir), tf, ref_prefix)
        self._swap = _AtomicCsvSwap(self._final)
        self._fh = self._swap.file
        self._w = _csv.writer(self._fh)
        # ヘッダは最初の bar が来るまで書かない（up/dn を持つ素材かは bar を見ないと決まらない）。
        #   1 行も書かれなければ commit 時に既定ヘッダを書く＝従来の空 CSV と同一。
        self._header_written = False
        self._committed = False
        # 型の規則は列につき 1 回だけ引く。どの列を持つかは最初の bar を見るまで決まらない
        #   ため、ヘッダと同じ見本から 1 度だけ作る（:func:`_casters_for`）。
        self._casters: "dict[str, _ColumnCaster] | None" = None

    def _ensure_header(self, bar: "dict[str, Any] | None") -> None:
        if self._header_written:
            return
        self._w.writerow(_header_for_bars({0: bar} if bar is not None else {}))
        self._header_written = True

    def write(self, period: Any, bar: dict[str, Any]) -> None:
        """確定済み 1 バーを 1 行 flush する（呼び出しは date 昇順であること）。"""
        self._ensure_header(bar)
        if self._casters is None:
            self._casters = _casters_for(bar)
        self._w.writerow(_bar_to_csv_row(period, bar, self._casters))

    def commit(self) -> None:
        """tmp を閉じ確定パスへ原子スワップする（成功時のみ呼ぶ）。"""
        if self._committed:
            return
        self._ensure_header(None)
        self._swap.commit()
        self._committed = True

    def close(self) -> None:
        """fh を閉じる。未 commit なら tmp を破棄して確定パスを汚さない（冪等）。"""
        self._swap.abort()

    def __enter__(self) -> "_RollupWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _resample_chunk(chunk: pd.DataFrame, tf: str) -> "OrderedBars":
    """チャンク（date index 化済み）を tf で resample し period→bar の順序辞書を返す。

    ISSUE-078: 規則解決は :func:`marketdata.resample.resample_ohlc_tf`（1D/1W/1M はセッション日
    集計・日中足は UTC floor）へ単一化する。
    """
    resampled = _resample.resample_ohlc_tf(chunk, tf)
    # 型の規則は列につき 1 回だけ引く（行ループの外で作る・:func:`_casters_for`）。
    casters = _casters_for(resampled.columns)
    bars: "OrderedBars" = {}
    for period, row in resampled.iterrows():
        bars[period] = _bar_to_dict(row, casters)
    return bars


# 型エイリアス（period(Timestamp) → bar(dict)）。挿入順＝date 昇順を保つ。
OrderedBars = dict


def _index_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    """read_csv のチャンク（date 列を持つ）を date を DatetimeIndex にして返す。"""
    chunk = chunk.copy()
    chunk["date"] = pd.to_datetime(chunk["date"])
    return chunk.set_index("date")


def _rollup_last_period(path: Path) -> Optional[pd.Timestamp]:
    """既存ロールアップの末尾バーの period（最終行のみ逆シーク読み）。空・不在なら None。"""
    if not path.exists():
        return None
    tail = tail_reader.read_tail(path, 1)
    if tail.empty:
        return None
    return pd.Timestamp(tail.index[-1])


def _last_data_line_offset(path: Path) -> int:
    """ロールアップ CSV の「最終データ行」が始まるバイト位置を逆シークで求める（truncate 起点）。

    末尾の改行を 1 つ無視し、その手前の改行の次バイト＝最終データ行の先頭。データ行が 1 本
    （ヘッダ＋1 行）のときはヘッダ直後（＝ヘッダ行末改行の次）を返す。
    """
    block = 64 * 1024
    with open(path, "rb") as f:
        f.seek(0, io.SEEK_END)
        size = f.tell()
        if size == 0:
            return 0
        buf = b""
        cur = size
        while cur > 0:
            step = min(block, cur)
            cur -= step
            f.seek(cur)
            buf = f.read(step) + buf
            stripped = buf[:-1] if buf.endswith(b"\n") else buf
            idx = stripped.rfind(b"\n")
            if idx != -1:
                return cur + idx + 1
        return 0


def _truncate_append_bars(path: Path, offset: int, bars: "OrderedBars") -> None:
    """``offset`` で切り詰め、period→bar を昇順で追記する（末尾だけ書く＝O(新規)・原子的でない）。

    最終データ行（形成中バー）を ``offset`` から切り落とし、再計算した末尾バー群（形成中の上書き
    ＋新規確定 append）を ``_bar_to_csv_row`` 形式で書く。履歴（prefix）は一切触らない。
    書込中 crash の窓では末尾が欠けうるが、(1) state は全 TF 成功後に保存するため次 tick で
    再処理され、(2) 形成中バーは probe から**再計算**（マージでなく上書き）するため再処理が冪等、
    (3) ロールアップは 1 分足から再生成可能、で復元できる。
    """
    import csv as _csv

    with open(path, "r+", newline="", encoding="utf-8") as fh:
        fh.seek(offset)
        fh.truncate()
        w = _csv.writer(fh)
        periods = sorted(bars)
        # 型の規則は列につき 1 回だけ引く（行ループの外で作る・:func:`_casters_for`）。
        casters = _casters_for(bars[periods[0]]) if periods else {}
        for period in periods:
            w.writerow(_bar_to_csv_row(period, bars[period], casters))
        fh.flush()
        os.fsync(fh.fileno())


def _resample_suffix(probe: pd.DataFrame, tf: str, since_period: pd.Timestamp) -> "OrderedBars":
    """probe 全体を tf で resample し、``since_period`` 以降の period→bar（完全バー）を返す。

    probe が ``since_period`` の期間 **UTC 始端**（:func:`marketdata.resample.period_utc_start`）を
    内包する前提。since_period 以降の各 period の 1 分足は probe に連続して含まれるため、その
    resample 結果は形成中バーも含め完全（partial でない）＝そのまま上書きしてよい（ISSUE-078:
    セッション tf はラベルがブローカー暦日のため、被覆判定は period_utc_start で行うこと）。
    """
    resampled = _resample.resample_ohlc_tf(probe, tf)
    suffix = resampled[resampled.index >= since_period]
    # 型の規則は列につき 1 回だけ引く（行ループの外で作る・:func:`_casters_for`）。
    casters = _casters_for(suffix.columns)
    bars: "OrderedBars" = {}
    for period, row in suffix.iterrows():
        bars[period] = _bar_to_dict(row, casters)
    return bars


def stream_build(
    m1_csv_path: Path,
    tf_list: Iterable[str],
    out_dir: Path,
    ref_prefix: str = _REF_PREFIX,
    chunk_rows: int = 500_000,
    *,
    writer_factory: "RollupWriterFactory | None" = None,
    save_state: bool = True,
) -> "RollupState":
    """1 分足を chunk 単位でストリーム読みし、各 TF をロールアップ CSV へ書き出す（メモリ有界）。

    チャンク跨ぎの未確定（最終）period は carry-over し、確定するまで書き出さない（D-1）。
    全行を同時に DataFrame 化しない（``pd.read_csv(chunksize=chunk_rows)``）。

    §10.3 M-3: ``ref_prefix``（既定 ``"jp225_m1"``）を writer へ伝播し出力ファイル名を
    銘柄汎用化する（既定値で全既存呼出は不変）。

    ISSUE-479 M-1（DIP）: 永続化先は ``writer_factory``（:data:`RollupWriterFactory`）から受け取る。
    未指定なら既定の具象 :class:`_RollupWriter` を使うため、既存呼出は 1 文字も変わらない。
    本関数の責務は「確定したバーを確定順にちょうど 1 回ずつ writer へ渡す」ことであり、
    渡した先が CSV かどうかは関知しない（この分離により、その責務をファイル出力なしに観測できる）。
    """
    tf_list = list(tf_list)
    m1_csv_path = Path(m1_csv_path)
    factory: "RollupWriterFactory" = writer_factory or _RollupWriter
    # TF ごとに確定済み period バーを逐次 flush する writer（確定バーを蓄積しない＝メモリ有界化）。
    #   常駐は 1 chunk ＋ TF ごとの carry-over 境界バー 1 本のみ（巨大期間でも確定バーが嵩まない）。
    writer_by_tf: "dict[str, RollupWriter]" = {
        tf: factory(out_dir, tf, ref_prefix) for tf in tf_list
    }
    # TF ごとに「未確定（carry-over 中）の最終 period→bar」を 1 本だけ保持する。
    pending_by_tf: dict[str, Optional[tuple[Any, dict[str, Any]]]] = {tf: None for tf in tf_list}

    last_ts: Optional[pd.Timestamp] = None

    try:
        for raw_chunk in pd.read_csv(m1_csv_path, chunksize=chunk_rows):
            chunk = _index_chunk(raw_chunk)
            if not chunk.empty:
                last_ts = chunk.index.max()
            for tf in tf_list:
                chunk_bars = _resample_chunk(chunk, tf)
                if not chunk_bars:
                    continue
                periods = list(chunk_bars)
                # 直前チャンクの未確定 period が、このチャンク先頭 period と一致するならマージ。
                pending = pending_by_tf[tf]
                if pending is not None:
                    p_period, p_bar = pending
                    if periods and periods[0] == p_period:
                        chunk_bars[p_period] = merge_same_period(p_bar, chunk_bars[p_period])
                    else:
                        # 一致しなければ直前未確定 period は確定済み（後続で延びない）→ 即 flush。
                        #   p_period（前チャンク最終）< periods[0] のため確定順＝昇順を保つ。
                        writer_by_tf[tf].write(p_period, p_bar)
                # このチャンクの最終 period は次チャンクへ延びうるため未確定として carry-over。
                last_period = periods[-1]
                new_pending_bar = chunk_bars.pop(last_period)
                # 残り（最終 period 以外）は確定として昇順 flush する（蓄積しない）。
                for period, bar in chunk_bars.items():
                    writer_by_tf[tf].write(period, bar)
                pending_by_tf[tf] = (last_period, new_pending_bar)

        # 全チャンク終了後、残った未確定 period を確定して flush する（各 TF の最終バー）。
        for tf in tf_list:
            pending = pending_by_tf[tf]
            if pending is not None:
                writer_by_tf[tf].write(pending[0], pending[1])
        # 成功時のみ各 TF の tmp を確定パスへ原子スワップ（🔴）。例外時は finally の close が
        #   tmp を破棄し確定パスを汚さない。
        for w in writer_by_tf.values():
            w.commit()
    finally:
        for w in writer_by_tf.values():
            w.close()

    state = RollupState(
        last_processed_ts=(last_ts.to_pydatetime() if last_ts is not None else datetime.min)
    )
    if save_state:
        # 自己修復（heal_tail_gaps）が **1 TF だけ**を再構築する経路では state を進めない。
        #   state は全 TF 共通の増分カーソルであり、ここで M1 末尾まで進めると、同じ呼び出しの
        #   中でまだ増分処理していない他 TF の新規行が「処理済み」と見なされて恒久欠落する
        #   （ISSUE-488 と同型の穴を自己修復自身が作る）。
        state.save(out_dir)
    return state


# --------------------------------------------------------------------------- #
# 末尾整合の機械的検査と自己修復（ISSUE-488 根治）
# --------------------------------------------------------------------------- #
#: 検査 probe の行数（1 分足 ~2 か月）。1M の**確定した前周期**を必ず 1 本以上覆う長さにする
#: （20k=約 14 日では月周期の完全被覆が作れず、8 月バー消失のような欠落を検査できない）。
VERIFY_TAIL_ROWS = 90_000


def tail_gap_report(probe: pd.DataFrame, tf: str, path: Path) -> "str | None":
    """M1 末尾 probe から再集計した**確定 period** とロールアップ実体を突合する。

    返り値は不一致の説明（欠落 period・値不一致 period）。一致なら ``None``。
    probe で完全に覆えない period（先頭＝probe で切れている可能性・末尾＝形成中）は比較しない
    （断定できないものを不一致と呼ばない）。probe の被覆が確定 period 1 本に満たない TF も
    判定しない（``None``＝検査不能であって合格ではない。probe を伸ばせば検査できる）。

    なぜ必要か（ISSUE-488 実測 2026-09-04）: 増分更新は state より古い行を二度と読まないため、
    競合書込等でバーが消えると**出力は正しげなまま恒久欠落**する（1D: 9/1〜9/3・1M: 8 月バー）。
    宣言でなく機械的検査で担保する（CLAUDE.md）。
    """
    expected = _resample.resample_ohlc_tf(probe, tf)
    if len(expected) < 3:
        return None
    confirmed = expected.iloc[1:-1]
    if not path.exists():
        return f"ロールアップ CSV が存在しない: {path.name}"
    actual = tail_reader.read_tail(path, len(expected) + 2)
    missing = confirmed.index.difference(actual.index)
    if len(missing) > 0:
        return "欠落 period: " + ", ".join(str(p) for p in missing[:5])
    joined = actual.loc[confirmed.index]
    for col in ("open", "high", "low", "close", "volume"):
        if col not in joined.columns:
            return f"列が無い: {col}"
        bad = ~np.isclose(
            joined[col].to_numpy(dtype="float64"),
            confirmed[col].to_numpy(dtype="float64"),
            rtol=1e-9, atol=1e-6,
        )
        if bad.any():
            first = confirmed.index[bad.argmax()]
            return f"値不一致: {col} @ {first}"
    return None


def heal_tail_gaps(
    m1_csv_path: Path,
    tf_list: Iterable[str],
    out_dir: Path,
    ref_prefix: str = _REF_PREFIX,
    *,
    probe_rows: int = VERIFY_TAIL_ROWS,
) -> "list[str]":
    """末尾整合の検査に落ちた TF を M1 から**全件再構築**して自己修復する（ISSUE-488 根治）。

    probe は 1 回だけ読み、全 TF の検査で共有する（同じ末尾を TF ごとに読み直さない）。
    再構築（``stream_build``）は当該 TF のみ・``save_state=False``（state は増分カーソルの
    持ち主が保存する）。返り値は再構築した TF のリスト（空＝全 TF 一致）。
    """
    m1_csv_path = Path(m1_csv_path)
    if not m1_csv_path.is_file():
        return []
    probe = tail_reader.read_tail(m1_csv_path, int(probe_rows))
    if probe.empty:
        return []
    healed: "list[str]" = []
    for tf in tf_list:
        report = tail_gap_report(probe, tf, _rollup_path(out_dir, tf, ref_prefix))
        if report is None:
            continue
        logger.warning(
            "ロールアップ末尾の欠落/不一致を検出（%s: %s）。M1 から全件再構築して自己修復します。",
            tf, report,
        )
        stream_build(m1_csv_path, [tf], out_dir, ref_prefix, save_state=False)
        healed.append(tf)
    return healed


def incremental_update(
    m1_csv_path: Path,
    state: Optional[RollupState],
    tf_list: Iterable[str],
    out_dir: Path,
    ref_prefix: str = _REF_PREFIX,
) -> "RollupState":
    """state 以降の追記 tail のみ読み、各 TF ロールアップ末尾へマージする（増分・メモリ有界）。

    state 不在（None）は初回として :func:`stream_build` へフォールバックする。同一 period は
    上書き（形成中バー更新）、新規 period は append（period クローズ＝確定 append）する。

    §10.3 M-3: ``ref_prefix``（既定 ``"jp225_m1"``）を :func:`_rollup_path` /
    :func:`_write_rollup_df` / :func:`stream_build` へ伝播し出力ファイル名を銘柄汎用化する。
    """
    tf_list = list(tf_list)
    if state is None:
        return stream_build(m1_csv_path, tf_list, out_dir, ref_prefix)

    last_ts = pd.Timestamp(state.last_processed_ts)
    # まず末尾 probe（逆シーク）で state 以降の新規行を拾う。--watch は毎分 ~1 行追記なので
    # probe（≈14 日分）が last_ts を内包し、全件スキャンを避けられる（OOM 回避の本丸）。
    probe = tail_reader.read_tail(Path(m1_csv_path), _INCREMENTAL_TAIL_PROBE_ROWS)
    if not probe.empty and probe.index.min() <= last_ts:
        tail_df = probe[probe.index > last_ts]
    else:
        # probe が last_ts を内包できない長期 catch-up のみ全件ストリームへフォールバック。
        tail_frames: list[pd.DataFrame] = []
        for raw_chunk in pd.read_csv(m1_csv_path, chunksize=500_000):
            chunk = _index_chunk(raw_chunk)
            t = chunk[chunk.index > last_ts]
            if not t.empty:
                tail_frames.append(t)
        tail_df = pd.concat(tail_frames) if tail_frames else probe.iloc[0:0]

    if tail_df.empty:
        return state

    new_last: Optional[pd.Timestamp] = tail_df.index.max()
    probe_covers = not probe.empty

    for tf in tf_list:
        path = _rollup_path(out_dir, tf, ref_prefix)
        # ---- O(新規) 速い経路: 末尾だけ truncate+append（過去確定足を read/write しない）----
        #   probe が「既存末尾 period の期間始端」を内包すれば、形成中バーを probe から再計算
        #   （上書き＝冪等）でき、ロールアップ全体（5m≈64MB）の read/write を避けられる。
        last_period = _rollup_last_period(path)
        # ISSUE-078: セッション tf のラベルはブローカー暦日＝probe（UTC index）との被覆判定は
        #   期間の UTC 始端（period_utc_start）で行う（ラベル直接比較は最大 24h 過大評価し、
        #   形成中バー前半を欠落させ得る）。日中足は period_utc_start がラベル素通し＝従来同値。
        if (
            last_period is not None
            and probe_covers
            and probe.index.min() <= _resample.period_utc_start(tf, last_period)
        ):
            suffix = _resample_suffix(probe, tf, last_period)
            if suffix:
                # 追記する行の列構成が既存ヘッダと一致するときだけ速い経路を使う。食い違ったまま
                #   追記すると CSV が恒久的に読めなくなる（ヘッダ 6 列のファイルへ 8 列行が入り、
                #   以後その tf の /candles・rollup 読取・ライブ watch が全部落ちる＝実障害）。
                #   不一致は「列が増えた直後」に起きるので、全件 rewrite へ落として**ヘッダごと
                #   書き直す**（次回以降は一致して速い経路へ戻る＝自己修復）。
                if _header_of(path) == _header_for_bars(suffix):
                    offset = _last_data_line_offset(path)
                    _truncate_append_bars(path, offset, suffix)
                    continue
                logger.warning(
                    "ロールアップ CSV の列構成が変わりました（%s）。追記でなく全件 rewrite で"
                    "ヘッダごと書き直します。", path.name,
                )
            else:
                continue
        # ---- フォールバック（全件 rewrite）: probe 不足（1M 等）・ファイル不在・空 ----
        # 追記 tail を resample（小）。既存ロールアップは DataFrame のまま扱い辞書化しない
        #   （ISSUE-012: 90 万件の dict-of-dict が RSS を 618MB へ急騰させる回帰の防止）。
        new_df = _resample.resample_ohlc_tf(tail_df, tf)
        if new_df.empty:
            continue
        if path.exists():
            existing = pd.read_csv(path)
            if existing.empty:
                merged = new_df
            else:
                existing["date"] = pd.to_datetime(existing["date"])
                existing = existing.set_index("date")
                # 新規 tail の最小 period 未満は確定済み（再集計しない）＝値をそのまま温存。
                #   形成中の overlap（>= cut・高々 1 本）のみ new と groupby マージする。
                cut = new_df.index.min()
                keep = existing[existing.index < cut]
                overlap = existing[existing.index >= cut]
                union_tail = pd.concat([overlap, new_df])
                # merge_same_period と同じ縮約（規則は csv_schema の値列台帳・ここへ書き写さない）。
                #   concat 順（既存→新規）が first/last の意味（既存 open・新 close）を保証する。
                #   集約対象の列は **実在する列から導出**する（列名をここに直書きしない）。直書きは
                #   csv_schema へ列（up/dn）が増えたときに本経路だけ列を落とし、同じファイルへ
                #   速い経路（_bar_to_csv_row＝全列）が追記した瞬間にヘッダと行の列数が食い違って
                #   CSV を恒久破壊する（実際に jp225_tick_1M.csv がこれで壊れた）。
                merged_tail = union_tail.groupby(level=0, sort=True).agg(
                    _merge_agg(union_tail.columns)
                )
                merged = pd.concat([keep, merged_tail])
        else:
            merged = new_df
        _write_rollup_df(out_dir, tf, merged, ref_prefix)

    new_state = RollupState(
        last_processed_ts=(new_last.to_pydatetime() if new_last is not None else state.last_processed_ts)
    )
    new_state.save(out_dir)
    return new_state
