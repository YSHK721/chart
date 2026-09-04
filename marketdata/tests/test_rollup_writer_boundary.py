"""ロールアップ永続化の境界（RollupWriter）と原子化の単一化の検定（ISSUE-479 Wave2 M-1）。

なぜ必要か:
    ``stream_build`` は永続化の具象（``_RollupWriter``）を自分で直接生成していたため、
    「チャンク跨ぎ carry-over で確定したバーを、確定順にちょうど 1 回ずつ書き出す」という
    本体の責務を、ファイル出力なしには観測できなかった（DIP 違反）。書き出し先を差し替える
    注入点を設けることで、本体の振る舞いを永続化から切り離して固定できる。

    もう 1 つの欠陥は原子化（tmp → ``os.replace``）の**手書き 3 重**である。同じ「確定パスを
    完全な新 CSV か旧 CSV のいずれかに限定する」規則が 3 箇所に書かれていた。1 箇所だけ直した
    ときに残り 2 箇所が古いまま残る形になっており、mt5_ticks 取込側にある同型の原子化とも
    別実装だった。ここでは rollup.py 内の ``tempfile.mkstemp`` が 1 箇所に閉じることを固定する。

計算量（発行 − 使用 = 0）:
    ``stream_build`` が発行する ``write``（＝1 行書き出し）の回数から、出力 CSV のデータ行数を
    引いた差が 0 であること。つまり「書いたのに出力に現れない行」を 1 本も作らない。チャンク幅・
    TF 本数を変えた 2 点で、発行が出力量だけで決まることも固定する。回数そのものは焼き込まない。
"""

from __future__ import annotations

import ast
import csv as _csv
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from marketdata import rollup as rb

_ROLLUP_SRC = Path(rb.__file__)


# --------------------------------------------------------------------------- #
# 合成 1 分足（決定論・実データを読まない）
# --------------------------------------------------------------------------- #
def _synthetic_m1(start: str, minutes: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=minutes, freq="1min")
    base = list(range(minutes))
    return pd.DataFrame(
        {
            "open": [100.0 + b for b in base],
            "high": [100.0 + b + 0.5 for b in base],
            "low": [100.0 + b - 0.5 for b in base],
            "close": [100.0 + b + 0.2 for b in base],
            "volume": [1.0 + (b % 7) for b in base],
        },
        index=idx,
    )


def _write_m1_csv(path: Path, df: pd.DataFrame) -> None:
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for ts, row in df.iterrows():
            w.writerow([
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                row["open"], row["high"], row["low"], row["close"], row["volume"],
            ])


@pytest.fixture()
def m1_csv(tmp_path: Path) -> Path:
    """週境界を跨ぐ 12 日分の合成 1 分足 CSV（carry-over を強制できる長さ）。"""
    path = tmp_path / "m1.csv"
    _write_m1_csv(path, _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 12))
    return path


# --------------------------------------------------------------------------- #
# 原子化の単一化（AST）
# --------------------------------------------------------------------------- #
def test_atomic_swap_has_exactly_one_temp_file_creation():
    """rollup.py 内の ``tempfile.mkstemp`` が 1 箇所に閉じている（原子化の手書き複製を禁ずる）。"""
    tree = ast.parse(_ROLLUP_SRC.read_text(encoding="utf-8"))
    sites = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mkstemp"
    ]
    assert len(sites) == 1, (
        f"原子化（tmp→os.replace）が {len(sites)} 箇所に手書きされています（行: {sites}）。"
        " 一時ファイルの作成と確定スワップは単一の実体へ集約してください。"
    )


def test_atomic_swap_has_exactly_one_final_replace():
    """確定パスへの ``os.replace`` も 1 箇所に閉じている（スワップ規則の単一化）。"""
    tree = ast.parse(_ROLLUP_SRC.read_text(encoding="utf-8"))
    sites = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "replace"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
    ]
    assert len(sites) == 1, (
        f"確定スワップ（os.replace）が {len(sites)} 箇所に手書きされています（行: {sites}）。"
    )


# --------------------------------------------------------------------------- #
# DIP: 永続化先の注入点
# --------------------------------------------------------------------------- #
class _SpyWriter:
    """RollupWriter プロトコルの Test Spy（何も永続化せず、受け取った呼び出しを記録する）。"""

    def __init__(self, out_dir: Path, tf: str, ref_prefix: str) -> None:
        self.out_dir = Path(out_dir)
        self.tf = tf
        self.ref_prefix = ref_prefix
        self.rows: "list[tuple[Any, dict[str, Any]]]" = []
        self.commits = 0
        self.closes = 0

    def write(self, period: Any, bar: "dict[str, Any]") -> None:
        self.rows.append((period, dict(bar)))

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closes += 1


class _SpyFactory:
    """生成された Spy writer を TF ごとに保持する factory（生成回数も数える）。"""

    def __init__(self) -> None:
        self.made: "list[_SpyWriter]" = []

    def __call__(self, out_dir: Path, tf: str, ref_prefix: str) -> _SpyWriter:
        w = _SpyWriter(out_dir, tf, ref_prefix)
        self.made.append(w)
        return w


def test_stream_build_accepts_an_injected_writer_factory(m1_csv: Path, tmp_path: Path):
    """stream_build は永続化の具象を自分で決めず、注入された factory から受け取る（DIP）。"""
    out_dir = tmp_path / "rollups"
    factory = _SpyFactory()

    rb.stream_build(m1_csv, ["1h"], out_dir, chunk_rows=2500, writer_factory=factory)

    assert len(factory.made) == 1
    spy = factory.made[0]
    assert (spy.tf, spy.ref_prefix) == ("1h", "jp225_m1")
    assert spy.commits == 1 and spy.closes == 1
    # 注入した writer が全ての確定バーを受け取っている（本体が別経路で書いていない）。
    assert spy.rows, "確定バーが 1 本も writer へ渡っていません"
    assert [p for p, _ in spy.rows] == sorted(p for p, _ in spy.rows)
    assert not list(out_dir.glob("*.csv")), "注入した writer を迂回して CSV が書かれています"


def test_default_writer_factory_keeps_the_output_byte_identical(m1_csv: Path, tmp_path: Path):
    """既定（未注入）と ``_RollupWriter`` 明示注入の出力が byte 一致する（既定値の同一性）。"""
    implicit = tmp_path / "implicit"
    explicit = tmp_path / "explicit"

    rb.stream_build(m1_csv, ["1h"], implicit, chunk_rows=2500)
    rb.stream_build(m1_csv, ["1h"], explicit, chunk_rows=2500, writer_factory=rb._RollupWriter)

    assert (implicit / "jp225_m1_1h.csv").read_bytes() == (explicit / "jp225_m1_1h.csv").read_bytes()


def test_rollup_writer_protocol_is_satisfied_by_the_default_writer(tmp_path: Path):
    """既定の具象は公開プロトコル ``RollupWriter`` の構造部分型である（境界が名だけでない）。"""
    w = rb._RollupWriter(tmp_path, "1D")
    try:
        assert isinstance(w, rb.RollupWriter)
        assert isinstance(_SpyWriter(tmp_path, "1D", "jp225_m1"), rb.RollupWriter)
    finally:
        w.close()


# --------------------------------------------------------------------------- #
# 計算量: 発行（write）− 使用（出力 CSV のデータ行数）= 0
# --------------------------------------------------------------------------- #
class _CountingWriter:
    """本物の writer へ委譲しつつ ``write`` の発行回数を数える Test Spy（decorator）。"""

    def __init__(self, out_dir: Path, tf: str, ref_prefix: str) -> None:
        self._inner = rb._RollupWriter(out_dir, tf, ref_prefix)
        self.writes = 0

    def write(self, period: Any, bar: "dict[str, Any]") -> None:
        self.writes += 1
        self._inner.write(period, bar)

    def commit(self) -> None:
        self._inner.commit()

    def close(self) -> None:
        self._inner.close()


def _data_rows(path: Path) -> int:
    """ロールアップ CSV のデータ行数（ヘッダを除く＝出力として使われた行の数）。"""
    with open(path, newline="", encoding="utf-8") as fh:
        return max(sum(1 for _ in _csv.reader(fh)) - 1, 0)


@pytest.mark.parametrize("chunk_rows", [2000, 8000])
@pytest.mark.parametrize("tf_list", [["1h"], ["1h", "1D"]])
def test_stream_build_issues_exactly_the_rows_it_outputs(m1_csv, tmp_path, chunk_rows, tf_list):
    """書き出し発行数 − 出力 CSV データ行数 = 0（作って捨てる行を 1 本も作らない）。

    チャンク幅（2000/8000）と TF 本数（1/2）の 2 軸 2 点で固定する。回数そのものは期待値へ
    焼き込まない（浪費を仕様へ昇格させないため）。
    """
    out_dir = tmp_path / f"rollups_{chunk_rows}_{len(tf_list)}"
    made: "list[_CountingWriter]" = []

    def factory(out, tf, ref_prefix):
        w = _CountingWriter(out, tf, ref_prefix)
        made.append(w)
        return w

    rb.stream_build(m1_csv, tf_list, out_dir, chunk_rows=chunk_rows, writer_factory=factory)

    issued = sum(w.writes for w in made)
    used = sum(_data_rows(out_dir / f"jp225_m1_{tf}.csv") for tf in tf_list)
    assert issued - used == 0


@pytest.mark.parametrize("chunk_rows", [2000, 8000])
def test_writer_creation_does_not_grow_with_the_chunk_width(m1_csv, tmp_path, chunk_rows):
    """writer の生成数は TF 本数だけで決まり、チャンク幅（＝読み回数）に依存しない。"""
    factory = _SpyFactory()
    tf_list = ["1h", "1D"]

    rb.stream_build(m1_csv, tf_list, tmp_path / str(chunk_rows), chunk_rows=chunk_rows,
                    writer_factory=factory)

    assert len(factory.made) == len(tf_list)
