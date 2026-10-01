"""MT5 日中追記（C-1）が台帳の宣言を通る（ISSUE-511 段階 3 の段階 5・V-2）。

用語（初出定義）:
    C-1（日中追記）
        ＝ ``marketdata.mt5_ticks.m1_chain.append_m1_for_closed_minutes``。閉じた分の新着ティック
          だけを畳んで M1 CSV の末尾へ足す経路（当日累積を読み直さない）。
    C-2（日次再構築＝権威経路）
        ＝ ``marketdata.mt5_ticks.rebuild.authoritative_day_m1``。UTC 日が閉じた後、確定 parquet から
          同じ日を作り直す経路。素材化の順序（畳む → 外れ分除去 → 残った分だけ気配幅）の唯一源
          ``marketdata.tick_m1.materialize_m1_day`` を通る。
    宣言
        ＝ 台帳記述子の ``spread_point_snapshot``（``marketdata.dataset_registry``）。None＝spread 列を
          持たない系列。
    台帳照合
        ＝ 既存 M1 CSV の先頭行が spread 列を持つかと宣言の有無を突き合わせ、食い違えば
          ``marketdata.tick_m1.SpreadSchemaMismatch`` で止めること。規則の実体は
          ``marketdata.tick_m1`` の 1 つ（``tick_m1._checked_series``）であり、書き手の入口はそこを通る。

本検定が固定するもの:
  R-14 宣言付きの系列で、C-1 が書いた CSV は C-2 の出力と**列も値も一致**する。
  R-15 C-1 が台帳照合を通る（既存 CSV の列形が宣言と食い違えば 1 バイトも書かずに
       :class:`~marketdata.tick_m1.SpreadSchemaMismatch`）。照合を迂回して ``tick_m1.append_m1_rows``
       を直呼びする形へ戻したときの落ち方は**既存 CSV の列形で 2 つに分かれる**（段階 5 前のツリーで
       実測・数値は各試験の docstring）: 既存ヘッダが spread 列を**持つ**側は ISSUE-455 のヘッダ不一致
       ``ValueError`` で止まる（「止まった」ことでは区別できず**型でだけ**区別できる）。**持たない**側は
       迂回では**そもそも落ちず**、宣言と違う列形を黙って書き足す（静かな乖離）。
  R-15b 偽陽性が無い（列形が宣言と一致する既存 CSV へは従来どおり追記する）。
  CX-E 継ぎ目 ``marketdata.quote_spread.minute_spread_points``: 発行した分 − 追記した行数 = 0 かつ
       持ち越し分（まだ閉じていない分）を計算しない。閉じた分 2 と 20 の 2 点。
       **回数そのものは期待値に焼き込まない**（期待値は観測した追記行数から導く）。

本検定が固定しないもの（射程の明示）:
  - 日次クリーニング（外れ分バーの除去）の非対称は変わらない。C-1 は適用せず C-2 は適用する
    （``marketdata/tests/test_mt5_equivalence.py`` の
    test_the_intraday_fold_alone_still_carries_the_phantom_bars が固定する）。本ファイルの
    R-14 は**外れ分を含まない**素材で両経路を突き合わせる。
  - 台帳への宣言の投入そのもの（段階 7）。本ファイルは合成 ref を一時登録して測る。

書込はすべて ``tmp_path``（data_dir を必ず渡す）。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from marketdata import csv_schema, quote_spread, tick_m1
from marketdata import symbol_spec_snapshot as sss
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor
from marketdata.mt5_ticks import journal, m1_chain, rebuild
from spread_series_fixture import (
    cleared_point_cache,  # noqa: F401  （import した先で autouse になる共有 fixture）
)

#: tick 木の枝（MT5 系列の実トークンと同じ綴り・書込は tmp_path の中だけ）。
_TOKEN = "JP225@OANDA-Japan-MT5-Live"
_DAY = dt.date(2026, 8, 25)
_START = dt.datetime(2026, 8, 25, 9, 0)
#: 宣言する組（実在するスナップショット）。
_PAIR = (sss.OANDA_JAPAN_MT5_LIVE, "JP225")
#: 宣言付きの合成 ref（台帳へ一時登録する）。
_DECLARED = "zz_mt5_intraday_declared"
#: 台帳に在り宣言の無い実 ref（本番の日中追記の対象）。
_UNDECLARED = "jp225_mt5"

#: 列形 2 種（順序は台帳側の規則 :func:`marketdata.csv_schema.header_for` から導く）。
_PLAIN = csv_schema.header_for(["open", "high", "low", "close", "volume", "up", "dn"])
_WITH_SPREAD = csv_schema.header_for(
    ["open", "high", "low", "close", "volume", "up", "dn", csv_schema.SPREAD_COLUMN]
)

_PER_MINUTE = 4


def _label_ms(utc: dt.datetime) -> int:
    """UTC の壁時計をサーバ時刻ラベル（UTC+3）の epoch ms へ（既存検定と同じ変換）。"""
    return int(utc.replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + 3 * 3600 * 1000


def _rows(minutes: int, per_minute: int = _PER_MINUTE):
    """``minutes`` 分ぶんの clean なティック（外れ分なし・気配幅は分ごとに違う）。

    気配幅を分ごとに変えるのは、spread 列が定数になると「列は在るが値は測れていない」状態でも
    検定が緑になるためである（:func:`test_the_intraday_series_equals_the_authoritative_day` の
    空振り防止が、この分散に依る）。
    """
    rows = []
    for m in range(minutes):
        for i in range(per_minute):
            when = _START + dt.timedelta(minutes=m, seconds=i * (60 // per_minute))
            bid = 66000.0 + m * 2.0 + i * 0.1
            width = 7.0 + (m % 3) * 0.5 + i * 0.1   # 分内最小は i=0 の 7.0 + (m%3)*0.5
            rows.append((_label_ms(when), bid, bid + width))
    return rows


def _until(minutes: int) -> dt.datetime:
    """``minutes`` 分ぶんが閉じた時点の境界（これ以降の分は形成中）。"""
    return (_START + dt.timedelta(minutes=minutes)).replace(tzinfo=dt.timezone.utc)


def _register_declared(monkeypatch, tmp_path: Path) -> None:
    """宣言付きの合成 ref を台帳へ一時登録する（test_mt5_rebuild_materialize と同じ作法）。"""
    monkeypatch.setitem(REGISTRY, _DECLARED, DatasetDescriptor(
        path=tmp_path / f"{_DECLARED}_m1.csv", symbol="JP225", tick=True,
        price_basis="bid", vendor="mt5", tick_token=_TOKEN, spread_point_snapshot=_PAIR,
    ))


def _finalize_day(tmp_path: Path, rows) -> None:
    """同じ行をジャーナル経由で確定 parquet まで進める（C-2 の素材を C-1 と同一にする）。"""
    journal.append(_DAY, rows, symbol=_TOKEN, data_dir=tmp_path)
    assert journal.finalize(_DAY, symbol=_TOKEN, data_dir=tmp_path) == "written"


def _written(ref: str, tmp_path: Path) -> pd.DataFrame:
    return pd.read_csv(tick_m1.m1_csv_path(ref=ref, data_dir=tmp_path))


def _place_header(ref: str, tmp_path: Path, columns) -> Path:
    """``ref`` の置き場へ、``columns`` の列形だけを持つ既存 CSV を置く（別の書き手が作った跡）。"""
    path = tick_m1.m1_csv_path(ref=ref, data_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(columns) + "\n", encoding="utf-8")
    return path


def _spy_spread(monkeypatch) -> "list[tuple[int, int, frozenset]]":
    """``quote_spread.minute_spread_points`` を包み、発行ごとに (入力ティック数, 出力分数, 分の集合) を記録。"""
    real = quote_spread.minute_spread_points
    calls: "list[tuple[int, int, frozenset]]" = []

    def spy(bid, ask, minute, **kwargs):
        out = real(bid, ask, minute, **kwargs)
        calls.append((len(bid), len(out), frozenset(pd.DatetimeIndex(pd.Series(minute).unique()))))
        return out

    monkeypatch.setattr(quote_spread, "minute_spread_points", spy)
    return calls


# =====================================================================
# R-14 C-1 の出力 == C-2（権威経路）の出力（列も値も）
# =====================================================================
def test_the_intraday_series_equals_the_authoritative_day(tmp_path, monkeypatch):
    """R-14: 宣言付き系列で、日中追記が書いた CSV は権威経路の当日 M1 と列も値も一致する。

    一致しなければ、UTC 日が閉じた瞬間に日次再構築が当日区間を丸ごと置換する（``rebuild.REPLACED``）。
    置換された値はどちらも「それらしい」ので、状態検証だけでは置換されたことに気付けない。
    """
    # Arrange: 同じ行を 2 経路（日中追記 / 確定 parquet）へ通す（外れ分なし＝日次クリーニングは no-op）。
    _register_declared(monkeypatch, tmp_path)
    rows = _rows(minutes=5)
    _finalize_day(tmp_path, rows)

    # Act
    got = m1_chain.append_m1_for_closed_minutes(
        rows, ref=_DECLARED, data_dir=tmp_path, until=_until(5)
    )

    # Assert
    intraday = _written(_DECLARED, tmp_path)
    authoritative = tick_m1._format_m1_for_csv(
        rebuild.authoritative_day_m1(_DAY, symbol=_TOKEN, ref=_DECLARED, data_dir=tmp_path)
    ).reset_index()
    assert got.bars == 5                                    # 空振り防止（実際に書いた）
    assert csv_schema.SPREAD_COLUMN in intraday.columns, (
        f"日中追記が宣言どおりの列形で書いていません: {list(intraday.columns)}"
    )
    assert intraday[csv_schema.SPREAD_COLUMN].nunique() > 1  # 空振り防止（定数列ではない）
    pd.testing.assert_frame_equal(intraday, authoritative, check_dtype=False)


def test_a_declared_intraday_day_needs_no_replacement_by_the_rebuild(tmp_path, monkeypatch):
    """R-14 の帰結: 清浄日は日次再構築が :data:`rebuild.UNCHANGED`（当日区間を書き戻さない）。

    R-14 が列と値の一致を直接見るのに対し、こちらは**書き戻しが発行されないこと**を見る
    （一致していなければ毎日 1 回、当日区間が置換される）。
    """
    # Arrange
    _register_declared(monkeypatch, tmp_path)
    rows = _rows(minutes=4)
    _finalize_day(tmp_path, rows)
    m1_chain.append_m1_for_closed_minutes(
        rows, ref=_DECLARED, data_dir=tmp_path, until=_until(4)
    )

    # Act
    outcome = rebuild.rebuild_day(
        _DAY, symbol=_TOKEN, ref=_DECLARED, data_dir=tmp_path, update_rollups=False
    )

    # Assert
    assert outcome == rebuild.UNCHANGED, (
        "清浄日なのに再構築が置換を行いました（日中追記と権威経路の列形か値が食い違っています）。"
    )


# =====================================================================
# R-15 台帳照合を通る（迂回へ戻すと型で落ちる）
# =====================================================================
def _as_declared(monkeypatch, tmp_path) -> str:
    _register_declared(monkeypatch, tmp_path)
    return _DECLARED


def _as_in_the_real_ledger(monkeypatch, tmp_path) -> str:
    """実台帳の ref をそのまま使う（宣言は無い＝spread 列を持たない系列）。"""
    return _UNDECLARED


@pytest.mark.parametrize(
    ("registration", "existing"),
    [
        pytest.param(_as_in_the_real_ledger, _WITH_SPREAD, id="spread_file_under_an_undeclared_ref"),
        pytest.param(_as_declared, _PLAIN, id="plain_file_under_a_declared_ref"),
    ],
)
def test_the_intraday_append_refuses_a_column_form_that_contradicts_the_ledger(
    tmp_path, monkeypatch, registration, existing
):
    """R-15: 既存 CSV の列形が宣言と食い違えば、日中追記は 1 バイトも書かずに止まる。

    型を名指しする理由は 2 つのパラメータで異なる（段階 5 前のツリー＝HEAD 9f598012 を scratchpad へ
    展開し、同じ入力で実測・2026-09-17・本コンテナ）:
      - ``spread_file_under_an_undeclared_ref``: 迂回していた頃も**止まった**が、型は ISSUE-455 の
        ヘッダ不一致 ``ValueError`` だった（書込 0 バイト）。どちらも「止まった」ので例外の有無では
        区別できず、**型でだけ**区別できる（``SpreadSchemaMismatch`` は ``ValueError`` の派生ではない）。
      - ``plain_file_under_a_declared_ref``: 迂回していた頃は**止まらなかった**（``DID NOT RAISE``・
        192 バイト・3 行を追記）。宣言と違う列形を黙って書き足す＝**静かな乖離**であり、こちらは
        例外の有無だけで迂回を落とせる（型の名指しは前者のために要る）。
    """
    # Arrange
    ref = registration(monkeypatch, tmp_path)
    out = _place_header(ref, tmp_path, existing)
    before = out.read_bytes()

    # Act
    with pytest.raises(Exception) as caught:
        m1_chain.append_m1_for_closed_minutes(
            _rows(minutes=3), ref=ref, data_dir=tmp_path, until=_until(3)
        )

    # Assert
    assert caught.type is tick_m1.SpreadSchemaMismatch
    assert out.read_bytes() == before


@pytest.mark.parametrize(
    ("registration", "existing"),
    [
        pytest.param(_as_in_the_real_ledger, _PLAIN, id="plain_file_under_an_undeclared_ref"),
        pytest.param(_as_declared, _WITH_SPREAD, id="spread_file_under_a_declared_ref"),
    ],
)
def test_a_matching_column_form_is_appended_as_before(
    tmp_path, monkeypatch, registration, existing
):
    """R-15b: 列形が宣言と一致する既存 CSV へは従来どおり追記する（照合が偽陽性を作らない）。"""
    # Arrange
    ref = registration(monkeypatch, tmp_path)
    out = _place_header(ref, tmp_path, existing)

    # Act
    got = m1_chain.append_m1_for_closed_minutes(
        _rows(minutes=3), ref=ref, data_dir=tmp_path, until=_until(3)
    )

    # Assert
    written = _written(ref, tmp_path)
    assert got.bars == 3
    assert list(written.columns) == list(existing)
    assert len(written) == 3


# =====================================================================
# CX-E 計算量（継ぎ目 quote_spread.minute_spread_points・発行 − 追記 = 0・閉じた分 2 と 20）
# =====================================================================
@pytest.mark.parametrize("closed", [2, 20])
def test_cxe_spreads_are_issued_only_for_the_minutes_that_are_appended(
    tmp_path, monkeypatch, closed
):
    """CX-E: 発行した分 − 追記した行数 = 0、かつ持ち越し分（形成中の分）を計算しない。

    - 回数そのものは期待値に焼き込まない（期待値は観測した追記行数から導く）。
    - 閉じた分 2 と 20 の 2 点で表明するのは「発行が出力量だけで決まる」というオーダーである。
    - 形成中の分の気配幅を計算すると、その分は次の周期でもう一度畳まれる＝毎周期、出力に使わない
      計算を発行することになる（ISSUE-450 と同型・状態検証では出力が正しいままなので落ちない）。
    """
    # Arrange: 閉じた分 + 形成中の分 1 つ。
    _register_declared(monkeypatch, tmp_path)
    rows = _rows(minutes=closed + 1)
    calls = _spy_spread(monkeypatch)

    # Act
    got = m1_chain.append_m1_for_closed_minutes(
        rows, ref=_DECLARED, data_dir=tmp_path, until=_until(closed)
    )

    # Assert
    appended = len(_written(_DECLARED, tmp_path))
    issued_minutes = frozenset().union(*(c[2] for c in calls)) if calls else frozenset()
    forming = pd.Timestamp(_START + dt.timedelta(minutes=closed))
    assert (got.bars, appended) == (closed, closed)          # 空振り防止（実際に書いた）
    assert len(got.pending_rows) == _PER_MINUTE              # 空振り防止（形成中の分が在る）
    assert sum(c[1] for c in calls) - appended == 0, (
        f"閉じた分 {closed}: 発行した分 {sum(c[1] for c in calls)} − 追記した行 {appended} ≠ 0"
    )
    assert sum(c[0] for c in calls) - closed * _PER_MINUTE == 0, (
        f"閉じた分 {closed}: 気配幅の計算へ渡ったティック {sum(c[0] for c in calls)} −"
        f" 追記した分のティック {closed * _PER_MINUTE} ≠ 0"
    )
    assert forming not in issued_minutes, "形成中の分の気配幅を計算しています"
