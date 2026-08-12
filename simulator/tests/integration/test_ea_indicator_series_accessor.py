"""`build_ea_indicators`（simulator.main の公開アクセサ・Phase 5 R-3）の結合テスト。

なぜ要るか: sim の表示層は「その run が実際に使った EA の指標系列」から接点
（agg.contacts＝価格×MA の交差）を算出する。EA→指標の対応は `_EA_FACTORIES` が単一
ソースで持っており、外側スライスがそれを書き写せば必ず取り残される。よって
**私有名の越境 import ではなく公開アクセサ**で渡す（ISSUE-378 #7 と同型の DIP 是正）。

固定する不変条件:
    1. `build_interactor` と同じジョブ仕様（余分なキーを含んでよい）から、その EA の
       指標レジストリ（IndicatorPort）が得られる。
    2. 得られる系列は `build_interactor` が interactor へ渡すものと**同じ内容**である
       （EA→指標の対応を二重化していないことの実証）。
    3. 未登録 EA は既定 TC 経路へフォールバックする（`build_interactor` と同じ規則）。
    4. `build_interactor` の既存の戻り・挙動は変わらない（追加のみ）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.domain.exceptions import IndicatorBufferError
from simulator.main import build_ea_indicators, build_interactor

_MT5_HEADER = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"
_COMMA_HEADER = "time,open,high,low,close,volume"


def _write_mt5_csv(path: Path, n: int = 30) -> Path:
    lines = [_MT5_HEADER]
    for i in range(n):
        price = 39400.0 + i
        lines.append(
            f"2025.01.02\t01:{i:02d}:00\t{price}\t{price}\t{price}\t{price}\t1\t0\t100"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_comma_csv(path: Path, n: int = 30) -> Path:
    lines = [_COMMA_HEADER]
    for i in range(n):
        price = 39400.0 + i
        lines.append(f"2025-01-02T01:{i:02d}:00,{price},{price},{price},{price},1")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _kwargs(csv_path: Path, ea_name: str) -> dict:
    return dict(
        data_path=csv_path,
        symbol="JP225",
        period="M1",
        ea_name=ea_name,
        initial_deposit=10_000.0,
        contract_size=10.0,
        volume_min=0.1,
        volume_max=100.0,
        volume_step=0.1,
        stops_level=0,
        digits=1,
        point_size=0.1,
        leverage=10.0,
        ma_period=20,
        ma_method="ema",
        lot_size=0.1,
        stop_loss_points=0,
        take_profit_points=0,
        config_overrides={"tick_model": "open_only", "entry_price_basis": "current_open"},
    )


# --- 1/2. EA が実行に使う系列がそのまま得られる ------------------------------

def test_ma_slope_eaのema系列が公開アクセサで得られる(tmp_path) -> None:
    csv_path = _write_mt5_csv(tmp_path / "mt5.csv")
    indicators = build_ea_indicators(**_kwargs(csv_path, "MA_Slope_EA"))
    ema = indicators.get("ema")
    assert len(ema) == 30


def test_得られる系列はbuild_interactorが使うものと同一(tmp_path) -> None:
    """EA→指標の対応を二重化していないことの実証（値まで一致する）。"""
    csv_path = _write_mt5_csv(tmp_path / "mt5.csv")
    kwargs = _kwargs(csv_path, "MA_Slope_EA")
    controller, _request = build_interactor(**kwargs)
    used = controller._interactor._indicators.get("ema")
    exposed = build_ea_indicators(**kwargs).get("ema")
    assert list(exposed) == list(used)


def test_未登録eaは既定TC経路の系列へフォールバックする(tmp_path) -> None:
    csv_path = _write_comma_csv(tmp_path / "bars.csv")
    indicators = build_ea_indicators(**_kwargs(csv_path, "TC24051901"))
    assert len(indicators.get("madiff")) == 30
    assert len(indicators.get("close")) == 30


def test_登録の無い系列名は公開エラー契約で拒否される(tmp_path) -> None:
    """接点が組めない EA を呼び出し側が判別できる（黙って空を返さない）。"""
    csv_path = _write_comma_csv(tmp_path / "bars.csv")
    indicators = build_ea_indicators(**_kwargs(csv_path, "TC24051901"))
    with pytest.raises(IndicatorBufferError):
        indicators.get("ema")


# --- 3. 余分なキーを含むジョブ仕様をそのまま渡せる ---------------------------

def test_ジョブ仕様を丸ごと渡せる(tmp_path) -> None:
    """sim の spec.json（backtest）は build_interactor 用の全キーを持つ。仕分けを
    呼び出し側に強いると、キーの取捨が 2 か所に分かれて食い違う。"""
    csv_path = _write_mt5_csv(tmp_path / "mt5.csv")
    kwargs = dict(_kwargs(csv_path, "MA_Slope_EA"), strategy_decorator=None)
    assert build_ea_indicators(**kwargs).get("ema") is not None


def test_指標周期の異なる仕様は異なる系列を返す(tmp_path) -> None:
    """周期がアクセサへ効いている（既定値を握り潰していない）ことの実証。"""
    csv_path = _write_mt5_csv(tmp_path / "mt5.csv")
    fast = build_ea_indicators(**dict(_kwargs(csv_path, "MA_Slope_EA"), ma_period=5))
    slow = build_ea_indicators(**dict(_kwargs(csv_path, "MA_Slope_EA"), ma_period=20))
    assert list(fast.get("ema")) != list(slow.get("ema"))
