"""結合テスト（confirmation オラクル照合・詳細設計 §8.2）。

bars CSV → committed IF(build_interactor/execute) → BuildReportPayload UC → Presenter →
report.json。trades 件数・net・final_balance が confirmation 既知値に一致することを固定し、
致命3（len(trades)==len(balance_curve) / balance_curve[i].time==trades[i].exit_time）を回帰固定する。

オラクル（NOTE.md / reconcile_is.py）:
  IS  5224 / +11370 / 21370
  OOS 2438 / −4020  / 5980
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from simulator.report_ui.tools import export_report_payload as exp


# 実 run（数千トレード）。マーク付与で選択実行可能にする。
pytestmark = pytest.mark.integration

# 致命-1 オラクル: IS の MT5 ReportTester xlsx（read-only）。
_IS_XLSX = Path(
    "/workspaces/app/simulator/tests/confirmation/2026-04_stop-probe_oos"
    "/ReportTester-900005560_2604_03.xlsx"
)


def _xlsx_pending_orders(xlsx_path):
    """xlsx Orders 表の pending order（price>0）を {(open_epoch, price, side): (sl,tp)} へ。

    列はヘッダ名（'Price'/'S / L'/'T / P'/'Type'/'Open Time'）で引く（位置ハードコード回避・
    プロトタイプ prep_data.py の Orders 読み取りを堅牢化）。price==0 の約定行（buy/sell）は
    S/L,T/P を持たないため除外する。sl/tp は値が空のものも (sl, tp) として保持する。
    """
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb.active
    rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]

    def cell(r, i):
        return r[i] if i < len(r) and r[i] is not None else None

    orders_hdr = deals_hdr = None
    for i, r in enumerate(rows):
        if cell(r, 0) == "Orders":
            orders_hdr = i + 1
        elif cell(r, 0) == "Deals":
            deals_hdr = i + 1
    hdr = rows[orders_hdr]
    idx = {str(h).strip(): j for j, h in enumerate(hdr) if h}

    def to_epoch(s):
        dt = datetime.datetime.strptime(s, "%Y.%m.%d %H:%M:%S")
        return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())

    out = {}
    end = (deals_hdr - 2) if deals_hdr else len(rows)
    for r in rows[orders_hdr + 1:end]:
        if cell(r, 0) is None:
            continue
        if cell(r, 0) == "Deals":
            break
        price = cell(r, idx["Price"])
        if not price or round(price, 1) == 0.0:
            continue  # 約定行（pending でない）には S/L,T/P が無い
        typ = str(cell(r, idx["Type"])).lower()
        side = "buy" if typ.startswith("buy") else "sell"
        sl = cell(r, idx["S / L"])
        tp = cell(r, idx["T / P"])
        out[(to_epoch(cell(r, 0)), round(price, 1), side)] = (sl, tp)
    wb.close()
    return out


def _match_order(pending, trade):
    """trade に対応する pending order の (sl, tp) を返す。

    trade.entry_time は約定（stop 発火）時刻で、pending 設置時刻（Open Time）より後になる。
    side・建値（digits=1 で一致）が一致し Open Time <= entry_time を満たす中で最も近接した
    pending を採用する（採番規約: 建値＋side＋時刻最近接）。該当なしは None。
    """
    key_price = round(trade.entry_price, 1)
    best = None
    for (open_epoch, price, side), sltp in pending.items():
        if side != trade.side or price != key_price:
            continue
        if open_epoch > trade.entry_time:
            continue
        if best is None or open_epoch > best[0]:
            best = (open_epoch, sltp)
    return None if best is None else best[1]


def _fmt(v, digits=1):
    """xlsx の S/L,T/P 数値を digits 桁固定文字列へ（derive_sl_tp の fmt と同形）。"""
    return "" if v is None else f"{round(float(v), digits):.{digits}f}"


@pytest.fixture(scope="module")
def payload():
    """IS/OOS を committed IF で実 run し ReportPayloadModel を 1 回だけ構築する。"""
    return exp.build_payload()


class TestOracleReconciliation:
    def test_is_trade_count(self, payload):
        assert payload.summary["is"].trades == 5224

    def test_is_net(self, payload):
        assert round(payload.summary["is"].net) == 11370

    def test_is_final_balance(self, payload):
        assert round(payload.summary["is"].final_balance) == 21370

    def test_oos_trade_count(self, payload):
        assert payload.summary["oos"].trades == 2438

    def test_oos_net(self, payload):
        assert round(payload.summary["oos"].net) == -4020

    def test_oos_final_balance(self, payload):
        assert round(payload.summary["oos"].final_balance) == 5980


class TestCritical3FixedOnRealRun:
    def test_is_trades_len_equals_balance_curve(self, payload):
        seg = payload.segments["is"]
        assert len(seg.trades) == len(seg.agg["balance_curve"])
        assert len(seg.trades) == 5224

    def test_oos_trades_len_equals_balance_curve(self, payload):
        seg = payload.segments["oos"]
        assert len(seg.trades) == len(seg.agg["balance_curve"])

    def test_balance_curve_time_equals_exit_time_all_i(self, payload):
        for key in ("is", "oos"):
            seg = payload.segments[key]
            for i, tr in enumerate(seg.trades):
                assert seg.agg["balance_curve"][i]["time"] == tr.exit_time

    def test_final_balance_equals_last_balance_curve(self, payload):
        seg = payload.segments["is"]
        assert seg.agg["balance_curve"][-1]["value"] == payload.summary["is"].final_balance


class TestVerdictOnRealRun:
    def test_verdict_is_fail_overfit(self, payload):
        # IS 黒字(+11370) / OOS 赤字(-4020) → 過剰最適化 fail
        assert payload.verdict.result == "fail"


class TestCritical1SlTpOracle:
    """致命-1 固定（詳細設計 §8.2・T-1）: 実 run の entry_price から導出した sl/tp が
    xlsx Orders の S/L,T/P と桁一致（digits=1 完全一致・許容0）することを回帰固定する。"""

    @pytest.fixture(scope="class")
    def pending(self):
        if not _IS_XLSX.exists():
            pytest.skip(f"オラクル xlsx 不在: {_IS_XLSX}")
        return _xlsx_pending_orders(_IS_XLSX)

    def _check_side(self, payload, pending, side):
        seg = payload.segments["is"]
        checked = 0
        for tr in seg.trades:
            if tr.side != side:
                continue
            oracle = _match_order(pending, tr)
            if oracle is None:
                continue
            sl_oracle, tp_oracle = oracle
            assert tr.sl == _fmt(sl_oracle), (
                f"{side} sl 不一致 entry_time={tr.entry_time} "
                f"entry_price={tr.entry_price}: derived={tr.sl!r} oracle={_fmt(sl_oracle)!r}"
            )
            assert tr.tp == _fmt(tp_oracle), (
                f"{side} tp 不一致 entry_time={tr.entry_time} "
                f"entry_price={tr.entry_price}: derived={tr.tp!r} oracle={_fmt(tp_oracle)!r}"
            )
            checked += 1
            if checked >= 3:
                break
        assert checked >= 1, f"{side} の照合対象が 0 件（オラクル突合不成立）"

    def test_buy_sl_tp_matches_xlsx_orders(self, payload, pending):
        self._check_side(payload, pending, "buy")

    def test_sell_sl_tp_matches_xlsx_orders(self, payload, pending):
        self._check_side(payload, pending, "sell")


class TestJsonExport:
    def test_writes_parseable_json(self, tmp_path, payload):
        out = tmp_path / "report.json"
        exp.write_report(payload, out)
        data = json.loads(out.read_text())
        assert data["segments"]["is"]["meta"]["trades"] == 5224
        # 非有限値が JSON テキストに無い（allow_nan=False）
        text = out.read_text()
        assert "Infinity" not in text and "NaN" not in text
