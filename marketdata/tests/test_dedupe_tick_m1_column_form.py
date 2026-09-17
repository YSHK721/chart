"""marketdata.tools.dedupe_tick_m1 の列形の導出元を固定する（ISSUE-511 段階 3・V-5）。

対象の欠陥（是正前の実測・2026-09-17）:
    ``dedupe_tick_m1`` は出力の列形を 3 つの台帳導出値の**手結合**
    （``[csv_schema.HEADER[0], *OHLCV_COLUMNS, *UPDOWN_COLUMNS]``）で固定し、幅を
    ``_N_COLS = 8`` に焼き込んでいた。実測した帰結は 2 つある。

    - 9 列（spread 付き）の M1 を通すと ``_normalize`` が
      ``ValueError: 列数超過の破損行（9 > 8）`` で停止する（1 行目で停止）。
    - ヘッダが 6 列のファイルを通すと、出力ヘッダに存在しない ``up,dn`` が足され、
      全行の末尾へ空フィールド 2 つが書かれる（実測した出力ヘッダ:
      ``date,open,high,low,close,volume,up,dn``）。

是正の設計（ISSUE-511 段階 3 構造設計 §5）:
    **一様幅は「対象ファイル自身のヘッダ」から、列順は :mod:`marketdata.csv_schema` の
    公開面（:func:`marketdata.csv_schema.header_for`）から導出する。**

なぜ振る舞いで固定するのか:
    手結合はリテラルではないため、``marketdata/tests/test_rollup_spread_aggregation.py``
    が行うような AST のリテラル走査では検出できない。列形の導出元は、9 列往復・冪等・
    列を捏造しないことという**観測可能な振る舞い**でしか固定できない。

本ファイルの射程:
    ``marketdata/tests/test_dedupe_tick_m1.py``（8 列ファイルの既存 10 件・2026-09-17
    時点の収集数）は 1 行も変更しない。本ファイルは 8 列以外の列形と、``_normalize``
    の発行量だけを足す。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from marketdata import csv_schema
from marketdata.tools import dedupe_tick_m1

_HEADER_9 = "date,open,high,low,close,volume,up,dn,spread"
_HEADER_6 = "date,open,high,low,close,volume"


# =====================================================================
# R-8: 9 列（spread 付き）の M1 を通せる（ヘッダ一致・値の保存）
# =====================================================================

def test_a_nine_column_file_keeps_its_header_and_values(tmp_path: Path) -> None:
    """spread 付き 9 列の M1 を通すと、ヘッダが入力と一致し値が保存される。

    是正前は 1 行目で ``ValueError: 列数超過の破損行（9 > 8）`` になり、1 行も書けなかった。
    """
    # Arrange: 9 列・1 分だけ重複（最終出現の spread=71 が残るべき）。
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(
        _HEADER_9 + "\n"
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,3.0,2,1,70\n"
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,4.0,2,1,71\n"
        "2025-01-02 09:01:00,101.0,102.0,100.0,101.0,3.0,1,2,72\n",
        encoding="utf-8",
    )

    # Act
    res = dedupe_tick_m1.dedupe_file(p)

    # Assert: ヘッダは入力の 9 列そのもの（列を落とさない・足さない）。
    text = p.read_text(encoding="utf-8")
    assert text.splitlines()[0] == _HEADER_9
    df = pd.read_csv(p)
    assert list(df.columns) == _HEADER_9.split(",")
    # 値は keep-last で保存される（spread も含めて欠損なし）。
    assert res.total_rows_in == 3 and res.unique_rows_out == 2 and res.removed == 1
    assert list(df["date"]) == ["2025-01-02 09:00:00", "2025-01-02 09:01:00"]
    assert list(df["spread"]) == [71, 72]
    assert list(df["volume"]) == [4.0, 3.0]
    assert df.notna().all().all()


# =====================================================================
# R-9: 冪等（2 回目の実行で 0 バイトの変化）
# =====================================================================

def test_a_nine_column_file_is_idempotent_on_rerun(tmp_path: Path) -> None:
    """一意化済みの 9 列ファイルを再度通しても 1 バイトも変わらない。"""
    # Arrange
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(
        _HEADER_9 + "\n"
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,3.0,2,1,70\n"
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,4.0,2,1,71\n"
        "2025-01-02 09:01:00,101.0,102.0,100.0,101.0,3.0,1,2,72\n",
        encoding="utf-8",
    )
    dedupe_tick_m1.dedupe_file(p)
    after_first = p.read_bytes()

    # Act: 2 回目。
    res2 = dedupe_tick_m1.dedupe_file(p)

    # Assert: no-op（退避も置換もせず、本体は 0 バイトの変化）。
    assert res2.removed == 0
    assert res2.replaced is False
    assert res2.backup_path is None
    assert p.read_bytes() == after_first


# =====================================================================
# R-10: 列を捏造しない（6 列ファイルへ up/dn を足さない）
# =====================================================================

def test_a_six_column_file_does_not_gain_fabricated_updown_columns(tmp_path: Path) -> None:
    """ヘッダが 6 列のファイルは 6 列のまま出る（存在しない up/dn を作らない）。

    是正前の実測: 出力ヘッダが ``date,open,high,low,close,volume,up,dn`` になり、
    全行の末尾へ空フィールド 2 つ（``,,``）が書かれていた。ティック由来でない素材に
    方向内訳の列が生えると、読み手には「列はあるが全行が空欄」という別の事実に見える。
    """
    # Arrange
    p = tmp_path / "jp225_m1.csv"
    p.write_text(
        _HEADER_6 + "\n"
        "2012-06-14 17:46:00,8568.89,8568.89,8568.89,8568.89,1.0\n"
        "2012-06-14 17:46:00,8568.89,8568.89,8568.89,8568.89,2.0\n"
        "2012-06-14 17:47:00,8569.00,8569.00,8569.00,8569.00,1.0\n",
        encoding="utf-8",
    )

    # Act
    dedupe_tick_m1.dedupe_file(p)

    # Assert: 列は 6 のまま・末尾に空フィールドを付けない。
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[0] == _HEADER_6
    for line in lines[1:]:
        assert not line.endswith(",")
        assert len(line.split(",")) == 6
    df = pd.read_csv(p)
    assert list(df.columns) == _HEADER_6.split(",")
    assert "up" not in df.columns and "dn" not in df.columns
    assert list(df["volume"]) == [2.0, 1.0]  # keep-last は従来どおり。


# =====================================================================
# 列順の出どころは台帳側（csv_schema の公開面）である
# =====================================================================

#: 台帳と列順が食い違うヘッダ（ヘッダ, 同じ幅のデータ行）。**8 列の形を必ず含める**。
#:
#: なぜ 8 列を外さないか（実測 2026-09-17・追記中のスナップショット）: data/marketdata 配下の
#: 本番の CSV は 5 ファイルで、うち M1 tick 系の 3 ファイル
#: （jp225_tick_m1.csv ・jp225_tick_bid_m1.csv ・jp225_mt5_m1.csv）が
#: ``date,open,high,low,close,volume,up,dn`` の **8 列**である。残り 2 つは
#: jp225_m1.csv が **6 列**（up/dn を持たない。この形へ up/dn を捏造しないことも本ファイルで
#: 固定する）、jp225_daily.csv が 5 列。9 列だけで照合を確かめると、
#: 本番の列形に対する検出力が無いまま緑になる（変異「8 列を照合から外す」を入れても当時の
#: 21 件が全て緑のままだった）。
#:
#: 8 列で実際に何が起きたか（変更前の実装での実測・2026-09-17）: ``…,volume,dn,up`` という
#: ヘッダの ``dn=1 / up=2`` の行は、出力では ``…,volume,up,dn`` のヘッダの下に同じ並びで
#: 書かれた＝``up=1 / dn=2`` になった。値が別の列名の下へ移る事故は、この 8 列の形でこそ
#: 起きていた。
_DISORDERED_HEADERS = [
    pytest.param(
        "date,open,high,low,close,volume,dn,up",
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,3.0,1,2",
        id="8col-dn-before-up",
    ),
    pytest.param(
        "date,open,high,low,close,volume,spread,up,dn",
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,3.0,70,2,1",
        id="9col-spread-before-updown",
    ),
]


@pytest.mark.parametrize("header,row", _DISORDERED_HEADERS)
def test_the_column_order_is_taken_from_the_ledger_and_a_disordered_header_is_refused(
        tmp_path: Path, header: str, row: str) -> None:
    """台帳の列順と食い違うヘッダは拒否する（黙って並べ替えない）。

    幅を対象ファイル自身のヘッダから採るだけでは、列順が台帳と食い違うファイルを
    そのまま通してしまう。短い行を末尾の空フィールドで埋める整形は「欠けているのは
    末尾の列である」を前提にしており、その前提は列順が台帳
    （:func:`marketdata.csv_schema.header_for`）と一致するときにだけ成り立つ。
    値を別の列名の下へ書き換えて黙って採用しないため、ここで停止する。

    幅は 8 列と 9 列の 2 形で確かめる（根拠は :data:`_DISORDERED_HEADERS`）。
    """
    # Arrange
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(header + "\n" + row + "\n", encoding="utf-8")
    before = p.read_bytes()

    # Act / Assert
    with pytest.raises(ValueError, match="列順"):
        dedupe_tick_m1.dedupe_file(p)
    assert p.read_bytes() == before  # 拒否したファイルは書き換えない。


def test_the_expected_order_is_derived_not_hand_written(tmp_path: Path) -> None:
    """受理される 9 列ヘッダは、台帳の公開面が返す列順と同一である。

    期待値をこのテストに書き写さず :func:`marketdata.csv_schema.header_for` から引く
    （引き写すと、台帳が変わったときに検定側が取り残される）。
    """
    # Arrange
    derived = csv_schema.header_for(_HEADER_9.split(",")[1:])
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(
        ",".join(derived) + "\n"
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,3.0,2,1,70\n"
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,4.0,2,1,71\n",
        encoding="utf-8",
    )

    # Act
    dedupe_tick_m1.dedupe_file(p)

    # Assert
    assert p.read_text(encoding="utf-8").splitlines()[0] == ",".join(derived)


# =====================================================================
# CX-G: 計算量検定（継ぎ目 = dedupe_tick_m1._normalize）
# =====================================================================

def _run_with_dup_factor(path: Path, factor: int, monkeypatch: pytest.MonkeyPatch,
                         ) -> "tuple[int, int, int]":
    """重複度 ``factor`` で dedupe し ``(_normalize 発行回数, 出力行数, 入力行数)`` を返す。

    Test Spy は継ぎ目 :func:`marketdata.tools.dedupe_tick_m1._normalize` の発行回数だけを
    数え、処理そのものは元の実装へ委譲する（振る舞いを変えずに回数を観測する）。
    """
    dates = [f"2025-01-02 09:{m:02d}:00" for m in range(6)]
    lines = [_HEADER_9]
    for block in range(factor):
        for i, d in enumerate(dates):
            lines.append(f"{d},{100.0 + i},101.0,99.0,100.0,3.0,2,1,{70 + block}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    original = dedupe_tick_m1._normalize
    issued = 0

    def _spy(*args, **kwargs):
        nonlocal issued
        issued += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(dedupe_tick_m1, "_normalize", _spy)
    res = dedupe_tick_m1.dedupe_file(path)
    written = len(pd.read_csv(path))
    assert written == res.unique_rows_out
    return issued, written, res.total_rows_in


def test_no_normalized_row_is_built_and_thrown_away(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """発行した正規化行 − 書いた行 = 0（作って捨てる行が無い）。

    固定するのは**無駄の不在**であって回数そのものではない。回数を期待値へ焼き込むと、
    そのときの浪費が仕様へ昇格する（ISSUE-450 で実際にそうなった: 窓外バーの計算が
    発行されることを assert した検定が、浪費を仕様の側へ固定していた）。

    是正前は入力 1 行ごとに正規化行を組み立てていたため、重複度が上がるほど作った直後に
    捨てる組み立てが増えた。出力は正しいままなので状態検証では原理的に落ちない。

    反事実の実測（2026-09-17）— 数え方は本検定と同じ Test Spy（``_normalize`` の呼出回数）:
    「発行 − 出力」は**重複度 2 で 6・重複度 5 で 24**。次の 2 構成で測り、どちらも同じ値に
    なった。

    - 構成 A: 変更前の実装（``git show HEAD:marketdata/tools/dedupe_tick_m1.py``）＋ **8 列**
      素材。変更前の実装は幅を 8 に焼き込んでいるため、**本検定と同じ 9 列素材では 1 行目で
      ``ValueError: 列数超過の破損行（9 > 8）`` になり測れない**（「同じ素材で旧方式を測る」は
      原理的に成立しない）。
    - 構成 B: 現行の列形導出の上で、発行位置だけを旧位置（入力行ごと）へ戻した変種
      ＋ 9 列素材（＝本検定と同じ素材）。

    発行**回数そのもの**は入力行数と同じ（重複度 2 で 12・5 で 30）であって、これは
    「発行 − 出力」ではない。本 assert が見るのは差であり、上の反事実では落ちる。
    """
    # Arrange / Act
    issued2, written2, rows_in2 = _run_with_dup_factor(tmp_path / "a.csv", 2, monkeypatch)
    issued5, written5, rows_in5 = _run_with_dup_factor(tmp_path / "b.csv", 5, monkeypatch)

    # Assert: 2 点それぞれで「作った数 − 使った数 = 0」。
    assert issued2 - written2 == 0
    assert issued5 - written5 == 0


def test_normalization_does_not_grow_with_the_duplicate_factor(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """オーダーの表明: 一意数を固定し入力量だけ増やしても発行は増えない（2 点）。"""
    # Arrange / Act
    issued2, written2, rows_in2 = _run_with_dup_factor(tmp_path / "a.csv", 2, monkeypatch)
    issued5, written5, rows_in5 = _run_with_dup_factor(tmp_path / "b.csv", 5, monkeypatch)

    # Assert: 入力は 2 点で実際に違う規模である（検定が空回りしていない）。
    assert rows_in5 > rows_in2
    # 出力量が同じなら発行量も同じ（入力量に不感）。
    assert written2 == written5
    assert issued2 == issued5


# =====================================================================
# 畳む対象が無いファイルは何もしない（列順も見ない）
# =====================================================================

def test_an_empty_file_is_a_no_op(tmp_path: Path) -> None:
    """空ファイルは何もしない。

    畳む対象のデータ行が 1 行も無いのだから、停止する理由が無い。列順の照合は
    データ行が在るときにだけ意味を持つ（照合の目的は、末尾を空フィールドで埋める整形が
    値を別の列の下へ移さないことの保証であり、整形する行が無ければ守るものが無い）。
    """
    # Arrange
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text("", encoding="utf-8")
    before = p.read_bytes()

    # Act
    res = dedupe_tick_m1.dedupe_file(p)

    # Assert: 件数 0・置換なし・退避なし・本体 0 バイトの変化。
    assert (res.total_rows_in, res.unique_rows_out, res.removed) == (0, 0, 0)
    assert res.replaced is False
    assert res.backup_path is None
    assert p.read_bytes() == before
    assert not Path(str(p) + dedupe_tick_m1._BACKUP_SUFFIX).exists()


def test_a_file_with_only_a_header_is_a_no_op(tmp_path: Path) -> None:
    """ヘッダ 1 行だけ（データ行 0）のファイルも何もしない。"""
    # Arrange
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(_HEADER_9 + "\n", encoding="utf-8")
    before = p.read_bytes()

    # Act
    res = dedupe_tick_m1.dedupe_file(p)

    # Assert: 件数 0・置換なし・退避なし・本体 0 バイトの変化。
    assert (res.total_rows_in, res.unique_rows_out, res.removed) == (0, 0, 0)
    assert res.replaced is False
    assert res.backup_path is None
    assert p.read_bytes() == before
    assert not Path(str(p) + dedupe_tick_m1._BACKUP_SUFFIX).exists()


def test_a_file_without_a_header_is_a_no_op(tmp_path: Path) -> None:
    """ヘッダの無いファイル（データに見える 1 行だけ）も何もしない。

    本ツールは 1 行目を常にヘッダとして読み飛ばす規約であり、この入力のデータ行数は 0 になる。
    是正前（列形を手結合していた頃）も同じく no-op であり、その挙動を変えない。
    """
    # Arrange
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text("2025-01-02 09:00:00,100.0,101.0,99.0,100.0,3.0\n", encoding="utf-8")
    before = p.read_bytes()

    # Act
    res = dedupe_tick_m1.dedupe_file(p)

    # Assert: 件数 0・置換なし・退避なし・本体 0 バイトの変化。
    assert (res.total_rows_in, res.unique_rows_out, res.removed) == (0, 0, 0)
    assert res.replaced is False
    assert res.backup_path is None
    assert p.read_bytes() == before
    assert not Path(str(p) + dedupe_tick_m1._BACKUP_SUFFIX).exists()


@pytest.mark.parametrize("header,row", _DISORDERED_HEADERS)
def test_a_disordered_header_is_still_refused_when_data_rows_exist(
        tmp_path: Path, header: str, row: str) -> None:
    """データ行が 1 行でも在れば、列順の食い違うヘッダは拒否したままである。

    no-op へ戻す範囲を「畳む対象が無いとき」に限る（拒否そのものを緩めない）。
    幅は 8 列と 9 列の 2 形で確かめる（根拠は :data:`_DISORDERED_HEADERS`）。
    """
    # Arrange
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(header + "\n" + row + "\n", encoding="utf-8")
    before = p.read_bytes()

    # Act / Assert
    with pytest.raises(ValueError, match="列順"):
        dedupe_tick_m1.dedupe_file(p)
    assert p.read_bytes() == before


# =====================================================================
# 台帳が列順を定義していない列を持つヘッダは拒否する
# =====================================================================

@pytest.mark.parametrize("header", [
    "date,open,high,low,close,volume,up,mystery",
    "date,open,high,low,close,volume,up,dn,mystery",
])
def test_a_header_with_a_column_the_ledger_does_not_know_is_refused(
        tmp_path: Path, header: str) -> None:
    """台帳が列順を定義していない列を末尾に持つヘッダは ``ValueError`` で拒否する。

    裁定（依頼者・2026-09-17）: dedupe は重複行を畳む道具であって、台帳に無い列を
    推測して通す役ではない。通すと ISSUE-455 型の列ずれ検出をすり抜ける。

    幅を対象ファイル自身のヘッダから採る是正を入れた時点で、この入力は**受理**に
    変わっていた（:func:`marketdata.csv_schema.header_for` が未知列を末尾へ置くため
    ヘッダが自分自身と一致してしまう）。列順の照合だけでは、台帳が位置を定義していない
    列を捕まえられない。

    例外は食い違った列名を告げる（どの列が台帳に無いのか読めないと直せない）。
    """
    # Arrange
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(
        header + "\n"
        + "2025-01-02 09:00:00," + ",".join(["1.0"] * (header.count(",") )) + "\n",
        encoding="utf-8",
    )
    before = p.read_bytes()

    # Act / Assert
    with pytest.raises(ValueError) as exc:
        dedupe_tick_m1.dedupe_file(p)
    assert "mystery" in str(exc.value)
    assert p.read_bytes() == before  # 拒否したファイルは書き換えない。


# =====================================================================
# 計算量検定（継ぎ目 = 辞書が 1 行あたり保持しているバイト数）
# =====================================================================

def _deep_bytes(obj: object) -> int:
    """保持物 1 つぶんのバイト数（容れ物と、その中の文字列の実体を合算する）。

    ``sys.getsizeof`` は容れ物だけを数えるため、列へ分解した ``list`` では中の文字列が
    数から漏れる。漏れたまま比べると「分解しても保持量は増えない」という誤った像になる。
    """
    total = sys.getsizeof(obj)
    if isinstance(obj, (list, tuple)):
        total += sum(sys.getsizeof(x) for x in obj)
    return total


def _write_fixed_width_material(path: Path, *, n_cols: int, rows: int,
                                line_bytes: int) -> None:
    """列数が違っても **1 行のバイト長が等しい**素材を書く。

    列数だけを動かして行の長さを揃えるのは、保持量の増減が「行の中身が長くなったこと」
    ではなく「行を列へ分解したこと」から来ると切り分けるためである。長さの調整は
    volume 列の桁を足して行う。
    """
    header = ["date", "open", "high", "low", "close", "volume", "up", "dn", "spread"][:n_cols]
    lines = [",".join(header)]
    for i in range(rows):
        date = f"2025-01-{1 + i // 1440:02d} {(i // 60) % 24:02d}:{i % 60:02d}:00"
        fields = [date, "10000.0", "10001.0", "9999.0", "10000.0", "3.0", "2", "1", "7"][:n_cols]
        pad = line_bytes - len(",".join(fields))
        assert pad >= 0, f"line_bytes={line_bytes} が n_cols={n_cols} の自然長より短い"
        fields[5] = fields[5] + "0" * pad
        lines.append(",".join(fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _retained_bytes_per_row(path: Path) -> float:
    """``_collect_last`` の辞書が、一意 1 行あたり保持しているバイト数。

    継ぎ目は辞書の**値**（行ぶんの保持物）に置く。キーの date は出力の並び順を決めるのに
    要るので、無駄かどうかを問える対象ではない。
    """
    columns = dedupe_tick_m1._header_columns(path)
    last, _total = dedupe_tick_m1._collect_last(path, columns)
    return sum(_deep_bytes(v) for v in last.values()) / len(last)


def test_the_retained_bytes_per_row_do_not_grow_with_the_column_count(tmp_path: Path) -> None:
    """行あたりの保持量は列数に比例しない（保持しているのは行そのものである）。

    本モジュールは「3229 万行対策のメモリ有界」を掲げており、辞書の保持量は出力量で
    頭打ちになると宣言している。行を列へ分解して保持すると、同じ行バイト長でも列 1 つ
    につき文字列オブジェクトが 1 つ増え、宣言した特性が列数ぶん劣化する。

    固定するのは**バイト数そのものではなく、列数を増やしても行あたりの保持量が増えない
    こと**（規模 2 点＝列数 6 と 9）。バイト数を焼き込むと、そのときの保持形が仕様へ
    昇格する。
    """
    # Arrange: 行バイト長と行数を揃え、列数だけ 6 と 9 で変える。
    six = tmp_path / "six.csv"
    nine = tmp_path / "nine.csv"
    _write_fixed_width_material(six, n_cols=6, rows=2000, line_bytes=70)
    _write_fixed_width_material(nine, n_cols=9, rows=2000, line_bytes=70)
    # 2 点が同条件であることの表明（検定が空回りしていない）。
    body6 = six.read_text(encoding="utf-8").splitlines()[1:]
    body9 = nine.read_text(encoding="utf-8").splitlines()[1:]
    assert {len(line) for line in body6} == {len(line) for line in body9}
    assert len(body6) == len(body9)

    # Act
    per_row_6 = _retained_bytes_per_row(six)
    per_row_9 = _retained_bytes_per_row(nine)

    # Assert: 列数を 6 → 9 にしても行あたりの保持量は増えない。
    assert per_row_9 <= per_row_6


def test_the_retained_bytes_per_row_do_not_grow_with_the_row_count(tmp_path: Path) -> None:
    """オーダーの表明: 行数を増やしても行あたりの保持量は増えない（規模 2 点）。

    保持量が一意行数に比例する（＝行あたりが定数）ことが「メモリ有界」の内容である。
    """
    # Arrange
    small = tmp_path / "small.csv"
    large = tmp_path / "large.csv"
    _write_fixed_width_material(small, n_cols=9, rows=2000, line_bytes=70)
    _write_fixed_width_material(large, n_cols=9, rows=8000, line_bytes=70)

    # Act
    per_row_small = _retained_bytes_per_row(small)
    per_row_large = _retained_bytes_per_row(large)

    # Assert: 行数は実際に 4 倍違う（検定が空回りしていない）／行あたりは増えない。
    assert len(large.read_text(encoding="utf-8").splitlines()) > \
        len(small.read_text(encoding="utf-8").splitlines())
    assert per_row_large <= per_row_small


# =====================================================================
# 未覆だった挙動差の固定（依頼者裁定・2026-09-17）
#
# 以下 3 件は**現行の挙動をそのまま固定する検定**（characterization test）であり、
# 書いた時点で緑である。振る舞いを変えるための Red ではない。検出力は変異
# （M7 / M10 / M11）で別途実測する。
# =====================================================================

def test_a_file_without_a_header_but_with_data_rows_is_refused(tmp_path: Path) -> None:
    """ヘッダが無くデータ行が残るファイルは ``ValueError`` で止める。

    裁定（依頼者・2026-09-17）: 1 行目をデータ行と知りつつヘッダとして扱う方が危険である
    （通すと 1 行目が黙って捨てられ、残りの行が「データ値を列名にしたヘッダ」の下に書かれる）。

    **「既存検定と同一経路だから固定済み」ではない。** 既存の
    :func:`test_a_file_without_a_header_is_a_no_op` が通るのはデータ行が 0 行の経路であり、
    こちらはデータ行が 1 行以上残る別経路である。変更前（HEAD 実装）の実測（2026-09-17）では
    この入力は **no-op** だった（幅を 8 に焼き込んでいてヘッダの中身を見ていなかった）。
    """
    # Arrange: ヘッダ無し・データに見える行が 2 行（畳む対象が残る）。
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,3.0\n"
        "2025-01-02 09:01:00,101.0,102.0,100.0,101.0,3.0\n",
        encoding="utf-8",
    )
    before = p.read_bytes()

    # Act / Assert: 台帳が列順を定義していない列（＝データ値）として拒否する。
    with pytest.raises(ValueError) as exc:
        dedupe_tick_m1.dedupe_file(p)
    assert "100.0" in str(exc.value)
    assert p.read_bytes() == before  # 拒否したファイルは書き換えない。


def test_a_row_wider_than_a_six_column_header_is_refused(tmp_path: Path) -> None:
    """6 列ヘッダのファイルに 8 フィールドの行が混ざれば ``列数超過`` で止める。

    幅の出どころは対象ファイル自身のヘッダなので、6 列ファイルにとって 8 フィールドの行は
    列ずれの破損である（黙って採用しない）。
    """
    # Arrange
    p = tmp_path / "jp225_m1.csv"
    p.write_text(
        _HEADER_6 + "\n"
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,3.0,2,1\n",
        encoding="utf-8",
    )
    before = p.read_bytes()

    # Act / Assert
    with pytest.raises(ValueError, match="列数超過"):
        dedupe_tick_m1.dedupe_file(p)
    assert p.read_bytes() == before


def test_old_six_field_rows_are_padded_to_the_nine_column_header(tmp_path: Path) -> None:
    """9 列ヘッダのファイルに混ざる旧 6 フィールド行は、9 列＋末尾の空欄 3 に揃う。

    ``marketdata/tests/test_dedupe_tick_m1.py`` が持つ 8 列版
    （test_old_six_col_rows_padded_to_uniform_eight）の 9 列版。9 列は段階 7 で
    作る系列の実形なので、その幅でも不足列の空欄埋めが効くことを固定する。
    """
    # Arrange: 旧 6 フィールド行（重複 2 本）＋ 新 9 フィールド行。
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(
        _HEADER_9 + "\n"
        "2012-06-14 17:46:00,8568.89,8568.89,8568.89,8568.89,1.0\n"
        "2012-06-14 17:46:00,8568.89,8568.89,8568.89,8568.89,1.0\n"
        "2025-01-02 09:00:00,100.0,101.0,99.0,100.0,3.0,2,1,70\n",
        encoding="utf-8",
    )

    # Act
    dedupe_tick_m1.dedupe_file(p)

    # Assert: 旧行は末尾へ空フィールド 3 つが付いて 9 列になる。
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[0] == _HEADER_9
    assert lines[1] == "2012-06-14 17:46:00,8568.89,8568.89,8568.89,8568.89,1.0,,,"
    df = pd.read_csv(p)
    assert list(df.columns) == _HEADER_9.split(",")
    assert len(df) == 2
    old = df[df["date"] == "2012-06-14 17:46:00"].iloc[0]
    assert pd.isna(old["up"]) and pd.isna(old["dn"]) and pd.isna(old["spread"])
    new = df[df["date"] == "2025-01-02 09:00:00"].iloc[0]
    assert (new["up"], new["dn"], new["spread"]) == (2, 1, 70)
