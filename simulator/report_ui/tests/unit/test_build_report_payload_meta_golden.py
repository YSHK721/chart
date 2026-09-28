"""execute の出力 golden（G-1 の抽出ガード・特性化テスト）。

役割: `build_report_payload.execute` 内の meta 構築を `_payload_meta` へ**抽出する前に**
出力を固定し、抽出後も 1 バイトも変わらないことを機械的に保証する。純粋な構造移動
（Refactor）であって振る舞いの変更ではない、という主張の唯一の裏付けである。

golden の取り方（順序が重要）:
    1. 抽出**前**の実装で execute → ReportUiPresenter → JSON バイト列を得る
    2. その SHA-256 と meta dict を本ファイルへ literal として焼き込む
    3. 抽出後に本テストを再実行し、同一であることを確認する
golden を抽出後に取り直すことは禁止する（それでは何も証明しない）。

固定値は 2026-08-11 に上記手順で採取した（採取時の実装＝抽出前）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from simulator.report_ui.adapter.report_presenter import ReportUiPresenter
from simulator.report_ui.tests.unit.test_build_report_payload import (
    _ea_params,
    _make_result,
    _meta,
    _spec,
)
from simulator.report_ui.usecase.build_report_payload import BuildReportPayload

#: 抽出前の実装で採取した report.json のダイジェストと長さ。
_GOLDEN_SHA256 = "c2072ad7546da0ac980f22035b30ed30c0456cbb6941a796ff7bdbbc56125c14"
_GOLDEN_LEN = 7635

#: 抽出対象そのもの（execute の meta 構築 9 行）の出力。
_GOLDEN_META = {
    "initial_deposit": 10000.0,
    "note": "IS/OOS 単純分割（同一パラメータを両区間で評価・最適化なし）",
    "params": "ProbeDir=2(両建て) / offset100 / Lot0.1 / SL200 / TP500",
    "split": "2026-04-15",
    "strategy": "StopEntryProbe_EA",
    "symbol": "JP225",
    "timeframe": "M1",
}


def _payload():
    """golden 採取時と同一の入力で payload を組む（決定的・時刻依存なし）。"""
    result_is = _make_result(
        [100.0, -50.0, 30.0], [2000, 3000, 4000], [10100.0, 10050.0, 10080.0])
    result_oos = _make_result([20.0, -80.0], [5000, 6000], [10020.0, 9940.0])
    return BuildReportPayload().execute(
        result_is=result_is, result_oos=result_oos,
        bars_is=[], bars_oos=[],
        spec=_spec(), ea_params=_ea_params(),
        meta_is=_meta("is"), meta_oos=_meta("oos"),
    )


#: golden 採取後に**振る舞いとして**足した report キー（理由と日付つきで宣言する）。
#:   golden は取り直さない（上記の禁止）。これらを外した出力が golden と 1 バイトも
#:   違わないことで、「足した以外は何も変えていない」を表明する。
_REPORT_KEYS_ADDED_AFTER_GOLDEN = {
    # 2026-09-27: サマリー (Report) タブの欠落項目（MT5 レポート 9 本との突き合わせで式を確定）。
    "GHPR", "Total Deals", "Equity Drawdown Relative", "LR Correlation", "LR Standard Error",
}

#: golden 採取後に**値の形を変えた**キーと、採取時の値（両区間で同じ）。比べる前に採取時の値へ戻す。
#:   2026-09-28 ISSUE-548: 書式を MT5 と同じ形へ（件数 (割合%)・AHPR の増減%・Z-Score の確率%）。
#:   2026-09-28 ISSUE-547: 検査用の偽の統計を取引から作るようにした（Average profit/loss が 0 でなくなる）。
#:   差し戻す前に、HEAD の出力と新しい出力を中身で比べ、違いがこの 8 キーだけ（meta・summary・取引行・
#:   集計は不変）であることを確かめた。
_REPORT_VALUES_AS_OF_GOLDEN = {
    "Profit Trades (% of total)": "0.00% (0)", "Loss Trades (% of total)": "0.00% (0)",
    "Short Trades (won %)": "0.00% (0)", "Long Trades (won %)": "0.00% (0)",
    "AHPR": "0.0000", "Z-Score": "0.00",
    "Average profit trade": "0.00", "Average loss trade": "0.00",
}


def _payload_as_of_golden():
    payload = _payload()
    for seg in payload.segments.values():
        for key in _REPORT_KEYS_ADDED_AFTER_GOLDEN:
            seg.report.pop(key, None)
        for key, value in _REPORT_VALUES_AS_OF_GOLDEN.items():
            seg.report[key] = value
    return payload


def test_execute_output_is_byte_identical_to_the_golden(tmp_path: Path) -> None:
    """G-1 の抽出が report.json を 1 バイトも変えないこと（後から足したキーを除いて）。"""
    out = tmp_path / "report.json"
    ReportUiPresenter().present_report_payload(_payload_as_of_golden(), out)
    raw = out.read_bytes()
    assert len(raw) == _GOLDEN_LEN
    assert hashlib.sha256(raw).hexdigest() == _GOLDEN_SHA256


def test_payload_meta_matches_the_golden_dict() -> None:
    """抽出対象（meta 構築）の出力を直接固定する（差分の所在を特定できるようにする）。"""
    assert _payload().meta == _GOLDEN_META


def test_meta_keys_and_order_are_preserved() -> None:
    """キー集合と挿入順（JSON のキー順を規定する）を固定する。"""
    assert list(_payload().meta) == [
        "symbol", "timeframe", "strategy", "params",
        "initial_deposit", "split", "note",
    ]


def test_golden_is_json_parseable_and_has_the_six_top_keys(tmp_path: Path) -> None:
    """Presenter の契約（6 トップキー）も同時に固定する。"""
    out = tmp_path / "report.json"
    ReportUiPresenter().present_report_payload(_payload(), out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) == {
        "meta", "segments", "summary", "degradation", "verdict", "_contract_notes",
    }
