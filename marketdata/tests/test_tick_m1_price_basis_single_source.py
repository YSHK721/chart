"""価格基準の源を台帳 1 つに絞る（ISSUE-511 段階 3 の段階 6・V-3 / V-6 / TBD-4）。

用語（初出定義）:
    価格基準（price basis）
        ＝ 生ティックの bid/ask のどちらを「価格」とするか。``"mid"``＝(bid+ask)/2、``"bid"``＝bid。
          語彙の唯一源は :mod:`marketdata.tick_m1`、ref ごとの値は台帳記述子の ``price_basis``。
    登録済み ref
        ＝ ``marketdata.dataset_registry.REGISTRY`` に記述子がある ref。台帳が基準を名乗っている。
    継ぎ目（基準の解決）
        ＝ ``marketdata.dataset_registry.tick_price_basis``。登録済み ref の基準を引く唯一の口。
    発行 / 使用
        発行 ＝ 上記継ぎ目の呼出。使用 ＝ 引いた基準が、価格系列を選ぶ唯一の口
          （``marketdata.tick_m1._price_series``）へ実際に渡ったこと。

本検定が固定するもの:
  R-16 登録済み ref へ ``price_basis`` を明示すると拒否される（point の明示拒否と同型）。
       台帳と引数の 2 源を残すと、台帳だけ切り替えたときに片方の経路が旧基準で走り、日次再構築が
       当日を別基準で書き戻す。値はどちらも「それらしい」ので状態検証では気付けない。
  R-17 ``marketdata.tools.tick_m1_cli`` が登録済み ref に対し**台帳の基準**で書く。
       置き場の名前（``marketdata.dataset_registry.series_of``）と中身の基準が食い違わないことを、
       同じ 1 本の検定で見る。
  CX-F 基準の解決は「発行 − 使用 = 0」であり、**期間の日数を増やしても発行が増えない**
       （1 日 と 30 日 の 2 点）。回数そのものは期待値に焼き込まない。

本検定が固定しないもの（射程の明示）:
  - 台帳外 ref の ``price_basis`` 引数（従来どおり呼出側が渡す・TBD-5 と同じ理由で本段では触らない）。
  - 台帳への宣言の投入（段階 7）。本ファイルは合成 ref を一時登録して測る。

書込はすべて ``tmp_path``（``data_dir`` を必ず渡す）。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from marketdata import dataset_registry, tick_m1, tick_tree
from marketdata.tools import tick_m1_cli
from spread_series_fixture import (
    TICK_TREE,
    cleared_point_cache,  # noqa: F401  （import した先で autouse になる共有 fixture）
    day as _day,
    put_day as _put_day,
    put_days as _put_days,
    register_tick_ref as _register,
)

#: 合成の登録済み ref（宣言なし＝spread 列を持たない。台帳は基準だけを名乗る）。
_REGISTERED = "zz_basis_registered"
#: 台帳が名乗る基準（``register_tick_ref`` が記述子へ書く値）。
_LEDGER_BASIS = "bid"


def _build(ref: str, data_dir: Path, start, end, **kw) -> Path:
    """全構築を呼ぶ（``price_basis`` は既定で**渡さない**＝台帳が源であることを前提にする）。"""
    return tick_m1.build_m1_from_ticks(
        start, end, symbol=TICK_TREE, ref=ref, data_dir=data_dir, **kw
    )


def _append(ref: str, data_dir: Path, start, end, **kw) -> Path:
    """増分追記を呼ぶ（同上）。"""
    return tick_m1.append_m1_from_ticks(
        start, end, symbol=TICK_TREE, ref=ref, data_dir=data_dir, **kw
    )


_BUILD = pytest.param(_build, id="build")
_APPEND = pytest.param(_append, id="append")


# =====================================================================
# R-16 登録済み ref への基準の明示は拒否（IO の前に止める）
# =====================================================================
@pytest.mark.parametrize("entry", [_BUILD, _APPEND])
def test_r16_an_explicit_basis_for_a_registered_ref_is_refused_before_any_io(
    tmp_path, monkeypatch, entry
):
    """R-16: 台帳が基準の唯一源なので、登録済み ref へ呼出側の ``price_basis`` は受けない。

    宣言と同じ値でも拒否する（point の明示拒否と同型）。一致しているかを呼出ごとに照合する
    設計にすると、照合を通る限り 2 源が残り、台帳だけ切り替えたときに片方が旧基準で走る。
    ファイル bytes・mtime 不変、parquet を 1 回も読まないことまで見る（書いてから気付く形にしない）。
    """
    # Arrange: 既存 CSV（台帳の基準で作った状態）と、追記され得る翌日のティック。
    _register(monkeypatch, tmp_path, _REGISTERED, None)
    _put_day(tmp_path, _day(0))
    out = _build(_REGISTERED, tmp_path, _day(0), _day(0))
    before, mtime = out.read_bytes(), out.stat().st_mtime_ns
    _put_day(tmp_path, _day(1))
    reads: "list[tuple]" = []
    real_read = tick_m1.pd.read_parquet
    monkeypatch.setattr(
        tick_m1.pd, "read_parquet", lambda *a, **k: (reads.append(a), real_read(*a, **k))[1]
    )

    # Act / Assert
    with pytest.raises(ValueError, match="台帳"):
        entry(_REGISTERED, tmp_path, _day(0), _day(1), price_basis=_LEDGER_BASIS)
    assert out.read_bytes() == before
    assert out.stat().st_mtime_ns == mtime
    assert reads == []


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda ref, ticks, data_dir, basis: tick_m1.materialize_m1_day(
                ticks, ref=ref, price_basis=basis
            ),
            id="materialize_m1_day",
        ),
        pytest.param(
            lambda ref, ticks, data_dir, basis: tick_m1.fold_ticks_for(
                ticks, ref=ref, price_basis=basis, data_dir=data_dir
            ),
            id="fold_ticks_for",
        ),
    ],
)
def test_r16_the_two_folding_entries_refuse_an_explicit_basis_for_a_registered_ref(
    tmp_path, monkeypatch, call
):
    """R-16: 素材化の唯一源と日中の畳み口も、登録済み ref では基準を引数から受けない。

    片方だけが引数を受けると、そちらの経路だけが旧基準で走る余地が残る（V-3）。
    """
    # Arrange
    _register(monkeypatch, tmp_path, _REGISTERED, None)
    ticks = pd.DataFrame({
        "timestamp": pd.to_datetime([_day(0) + pd.Timedelta(seconds=s) for s in (5, 30)]),
        "bidPrice": [66000.0, 66001.0],
        "askPrice": [66010.0, 66011.0],
    })

    # Act / Assert
    with pytest.raises(ValueError, match="台帳"):
        call(_REGISTERED, ticks, tmp_path, _LEDGER_BASIS)


# =====================================================================
# R-17 CLI は台帳の基準で書く（置き場の名前と中身が食い違わない）
# =====================================================================
def test_r17_the_cli_writes_a_registered_ref_with_the_ledger_basis(tmp_path, monkeypatch, capsys):
    """R-17: CLI（合成点）は基準を渡さないが、書かれる足は**台帳の基準**である。

    段階 6 の前は ``build_m1_from_ticks`` が既定（mid）で全構築していたため、台帳が
    ``marketdata.dataset_registry.series_of`` で bid と名乗っている置き場へ mid の足を書いていた
    （V-6・本検定が 2026-09-17 に Red で実測）。ファイル名は「それらしい」ままなので、値を見て
    いるだけでは気付けない。是正は素材化の権威（``marketdata.tick_m1._resolved_basis``）に入れて
    おり、``marketdata/tools/tick_m1_cli.py`` は 1 行も変えていない（CLI は元から基準を渡さない。
    実測: 段階 6 の差分に同ファイルは含まれない）。
    ここでは置き場の名前と中身の基準を**同じ 1 本**で見る。
    """
    # Arrange: 実台帳の jp225_tick（基準 bid・series jp225_tick_bid）へ、bid と mid が必ず
    #   食い違う合成ティックを置く。書込先は tmp_path（既定 DATA_DIR へは 1 バイトも書かない）。
    ref = tick_m1._DEFAULT_REF
    assert dataset_registry.tick_price_basis(ref) == tick_m1.PRICE_BASIS_BID  # 前提の明示
    when = _day(0)
    bid, ask = 66000.0, 66020.0
    frame = pd.DataFrame({
        "timestamp": pd.to_datetime(
            [when + pd.Timedelta(seconds=s) for s in (5, 30, 55)]
        ).tz_localize("UTC"),
        "bidPrice": [bid, bid, bid],
        "askPrice": [ask, ask, ask],
    })
    parquet = tick_m1.day_parquet_path(when, symbol=TICK_TREE, data_dir=tmp_path)
    parquet.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(parquet)
    # CLI は既定 DATA_DIR の木を数えてから権威を呼ぶ。数える口と書く口の両方を tmp_path へ向ける
    #   （CLI 自身は data_dir を受け取らないため、合成点の引数解釈だけを残して経路を差し替える）。
    monkeypatch.setattr(
        tick_tree, "day_parquet_files", lambda *a, **k: [parquet]
    )
    real_build = tick_m1.build_m1_from_ticks
    monkeypatch.setattr(
        tick_m1, "build_m1_from_ticks",
        lambda start, end, **kw: real_build(
            start, end, **{**kw, "symbol": TICK_TREE, "data_dir": tmp_path}
        ),
    )

    # Act
    tick_m1_cli.main([str(when.date()), str(when.date())])
    capsys.readouterr()

    # Assert: 置き場は台帳の series（bid を名乗る名前）であり、中身も bid である。
    out = tick_m1.m1_csv_path(ref=ref, data_dir=tmp_path)
    assert out.name == f"{dataset_registry.series_of(ref)}_m1.csv"  # 空振り防止（名前の出所）
    assert "bid" in out.name                                        # 空振り防止（名前は bid を名乗る）
    written = pd.read_csv(out)
    assert len(written) == 1                                        # 空振り防止（実際に書いた）
    assert written["open"].iloc[0] == bid, (
        f"台帳が {tick_m1.PRICE_BASIS_BID} を名乗る置き場（{out.name}）へ"
        f" {written['open'].iloc[0]} を書きました（bid={bid} / mid={(bid + ask) / 2}）。"
    )


# =====================================================================
# CX-F 基準の解決は発行 − 使用 = 0・日数で増えない（1 日 と 30 日）
# =====================================================================
#: 測る規模（期間の日数）。2 点そろって初めてオーダーを表明できるので、1 本の中で順に測る。
_SPANS = (1, 30)


def _basis_spies(monkeypatch) -> "tuple[list[str | None], list[str]]":
    """（発行, 使用）を記録する Test Spy を **1 回だけ** 掛ける。

    規模ごとに掛け直すと、2 周目の包みが 1 周目の包みを包んで発行が二重に数えられる
    （記録は :func:`list.clear` で空にする）。
    """
    issued: "list[str | None]" = []
    real_basis = dataset_registry.tick_price_basis
    monkeypatch.setattr(
        dataset_registry, "tick_price_basis",
        lambda ref: (lambda got: (issued.append(got), got)[1])(real_basis(ref)),
    )
    applied: "list[str]" = []
    real_series = tick_m1._price_series
    monkeypatch.setattr(
        tick_m1, "_price_series",
        lambda ticks, price_basis: (applied.append(price_basis),
                                    real_series(ticks, price_basis))[1],
    )
    return issued, applied


def test_cxf_the_basis_is_resolved_without_waste_and_does_not_grow_with_the_span(
    tmp_path, monkeypatch
):
    """CX-F: 引いた基準はすべて畳みへ渡り（発行 − 使用 = 0）、発行は期間の日数で増えない。

    日ごとに台帳を引き直すと、1 日分の parquet を読むたびに解決が走る（出力は同じなので
    状態検証では落ちない）。固定するのは回数そのものではなく無駄の不在とオーダーである。

    2 規模（1 日 / 30 日）を **1 本の中で順に測って突き合わせる**。規模ごとに別テストへ分け、
    module グローバルへ発行数を残して比べる形にはしない（単体指定や ``-k`` 選択で 1 点しか
    走らず「2 点そろって測れていない」の**偽の赤**になる。2026-09-17 のレビューが 2 通りの
    実行で実測）。
    """
    # Arrange
    issued, applied = _basis_spies(monkeypatch)
    issued_by_span: "dict[int, int]" = {}

    for days in _SPANS:
        data_dir = tmp_path / f"span{days}"
        _register(monkeypatch, data_dir, _REGISTERED, None)
        put = _put_days(data_dir, days)
        issued.clear()
        applied.clear()

        # Act
        out = _build(_REGISTERED, data_dir, put[0], put[-1])

        # Assert
        written = pd.read_csv(out)
        used = len({basis for basis in issued if basis in applied})
        assert len(written) == days * 2              # 空振り防止（日数ぶん実際に畳んだ）
        assert applied, f"{days} 日: 価格系列を 1 度も選んでいない（検定が空回りしている）"
        assert issued, f"{days} 日: 台帳から基準を 1 度も引いていない（継ぎ目を通っていない）"
        assert len(issued) - used == 0, (
            f"{days} 日の期間で基準を {len(issued)} 回引き、使ったのは {used} 種でした"
            "（使わない解決を発行しています）。"
        )
        issued_by_span[days] = len(issued)

    # Assert（オーダーの表明・期待値は観測から導き、回数を焼き込まない）
    assert sorted(issued_by_span) == sorted(_SPANS)  # 空振り防止（2 点とも測った）
    assert issued_by_span[1] == issued_by_span[30], (
        f"期間を 1 日 → 30 日 にしたら基準の解決が {issued_by_span[1]} →"
        f" {issued_by_span[30]} へ増えました（日ごとに台帳を引き直しています）。"
    )
