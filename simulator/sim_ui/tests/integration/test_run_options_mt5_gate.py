"""SymbolSpecCatalog の MT5 突合ゲート（Phase 6 拡張・最重要）.

裁定（憶測禁止・**2026-08-25 にオラクルを移した**: ISSUE-445 段階 2 /
`.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md` §7）: フォームが供給する JP225 の銘柄仕様は
**供給元スナップショット**（`marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json`）を
唯一のオラクルとする。従来は `case.yaml` を唯一のオラクルにしていたが、case.yaml は自身が
「人が読むためのメタ要約」と宣言する転記物であり、そこに書かれた `contract_size: 10` は
出所の無い逆算値だった（ISSUE-445 の RC-1）。本ゲートは 3 段:

    (a) **実保証**。SymbolSpecCatalog の JP225 プロファイルの銘柄仕様 8 項目が供給元
        スナップショットと等値であること（カタログがリテラルを持たないことの実証）。
    (a') **独立検証**。供給元と**独立な**証拠＝`expected/report.json` の deals から機械導出
        した値とも一致すること（設計書 §3.3 の検出ゲート）。これが (a) の同語反復化を防ぐ。
        導出できない項目（volume_min/step/max・stops_level）はレポートが出力しないため
        (a') の対象外である（導出できないものを導出したふりをしない）。
    (b) **補助（現状 vacuous）**。その定数で build_interactor→実走した結果が決定的である
        ことのみ確認する。**本 _oscillating_csv は TC24051901 で trades=0（実測 2026-08-12）**の
        ため定数差分が結果に効かず、(b) は「定数の実感度」を独立には拘束しない（実保証は (a)(a')）。
        意味ある negative control（TC が建玉を出す CSV での定数感度検定）は非 cheap のため ISSUE 化。

build_interactor 既存引数は無改変。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from marketdata.symbol_spec_snapshot import (
    OANDA_JAPAN_MT5_LIVE,
    load_snapshot,
    settlement_currency,
    spec_fields,
)
from simulator.main import run_backtest
from simulator.sim_ui.main.composition_root_jobs import build_run_options_port
from simulator.tests.fixtures.mt5 import load_case
from simulator.tests.fixtures.mt5 import spec_derivation as sd

_CASE = "ma_slope_jp225_202501"
_SYMBOL = "JP225"


def _jp225_profile():
    return [p for p in build_run_options_port().datasets() if p.symbol == _SYMBOL][0]


def _snapshot():
    return load_snapshot(OANDA_JAPAN_MT5_LIVE, _SYMBOL)


def test_catalog_constants_match_the_supply_snapshot():
    """(a) 銘柄仕様 8 項目 == 供給元スナップショット（カタログにリテラルが無いことの実証）。

    期待値をここにリテラルで書かない。カタログが値を持ち始めたら（＝供給元から乖離したら）
    赤になる。
    """
    expected = spec_fields(_snapshot())
    jp = _jp225_profile()
    assert len(expected) == 8
    for name, value in expected.items():
        assert getattr(jp, name) == value, f"{name}: カタログ {getattr(jp, name)!r} != 供給元 {value!r}"
    assert jp.symbol == _SYMBOL and jp.period == "M1"


def test_catalog_constants_agree_with_the_independent_report_derivation():
    """(a') 供給元と**独立な**証拠（report.json の deals からの機械導出）とも一致する。

    設計書 §3.3 の検出ゲート。(a) だけだと「スナップショット == スナップショット」の
    同語反復になるため、実 MT5 テスターの確定出力から機械導出した値と突き合わせる。
    **このゲートがあれば ISSUE-445 は fixture 作成時点（2026-06-18）に赤で止まっていた**
    （実測: contract_size=10 は 1163 件中 1088 件で棄却・最大残差 2205）。

    導出できない項目（volume_min / volume_step / volume_max / stops_level）はレポートが
    制約を出力しないため対象外（設計書 §3.3・導出できないものを導出したふりをしない）。
    """
    expected = load_case(_CASE).expected
    jp = _jp225_profile()
    # 損益の整合から contract_size を片側検査する。
    report = sd.contract_size_consistency(expected, jp.contract_size)
    assert report.ok, report.describe()
    # digits は観測小数桁の下限を下回らない（片側検査）。
    assert jp.digits >= sd.price_decimals(expected)
    # leverage は口座属性としてレポートに載る。
    assert jp.leverage == sd.account_leverage(expected)


def test_catalog_settlement_currency_matches_the_supply_snapshot():
    """決済通貨も供給元を唯一のオラクルとする（A-2・D-10 の供給源恒久化）。

    出典（実測・憶測禁止）: スナップショットの ``symbol.currency_profit``＝MT5 端末が
    銘柄の属性として出力する profit 通貨。期待値をここにリテラルで書かない。
    従来は ``case.yaml`` の ``symbol.currency``（人が書いた転記物）を唯一のオラクルに
    していた（ISSUE-445 段階 2 で移管）。
    """
    jp = _jp225_profile()
    assert jp.settlement_currency == settlement_currency(_snapshot())
    # 独立な証拠（実 MT5 テスター出力の口座通貨）とも一致する。
    assert jp.settlement_currency == sd.settlement_currency(load_case(_CASE).expected)


def test_catalog_settlement_currency_agrees_with_report_oracle():
    """最終オラクル（expected/report.json）とも一致することを固定する。

    case.yaml は「人が読むためのメタ要約」（case.yaml 冒頭コメント）であり、数値の最終
    オラクルは report.json 側。report.json の 2 箇所が独立に JPY を示す（実測）:
        - ``settings.currency``（L19）= 実 MT5 テスターの**口座通貨**。
        - ``settings.derived.note`` ``… 1 JPY per price unit``
          = **損益が JPY 建てで発生する**＝銘柄の決済（profit）通貨が JPY。
    後者が決済通貨の直接証拠であり、前者との一致は「本 fixture では口座通貨＝決済通貨」
    （N-11 非該当のケース）であることを示す。両方を固定して取り違えを検出する。

    申告値の供給元（ISSUE-445 段階 3-E1・2026-08-26）: 従来は `case.yaml` の
    `symbol.currency` から引いていた。case.yaml の `symbol:` ブロックは段階 2 で権威を失い
    **転記**になったため、ここも供給元スナップショットから引く。これで `case.yaml` を
    「銘柄仕様の申告値」として読むゲートは無くなった。続く段階 3-E2（2026-08-26）で
    `case.yaml` の `symbol:` から重複 5 キーを撤去し `name` だけを残したため、いま
    `case.yaml` の `symbol:` を読む検定は
    `test_mt5_case_spec_agrees_with_report.py::TestCaseYamlHoldsOnlyTheIdentity`
    （権威値が生え直さないことの固定）1 件だけである。
    """
    case = load_case(_CASE)
    settings = case.expected["settings"]
    currency = settlement_currency(_snapshot())
    assert settings["currency"] == currency
    # 決済（profit）通貨の直接証拠。期待値をここにリテラルで書かない。
    assert f"1 {currency} per price unit" in settings["derived"]["note"]
    assert _jp225_profile().settlement_currency == settings["currency"]


#: 2024-01-01T00:00:00Z。comma 形式 CSV の `time` は UNIX 秒 int が契約である
#: （`Bar.time` = ``numpy.datetime64`` | epoch int。`CsvOHLCRepository._extract` は CSV の値を
#: **そのまま** `Bar.time` に載せるため、ISO 文字列を書くと契約違反の Bar が生まれる。
#: 委譲経路 `CsvCandleSource` は同じ CSV を ValueError で fail-fast する＝経路で解釈が割れる）。
_EPOCH_2024_01_01 = 1_704_067_200


def _oscillating_csv(path: Path) -> Path:
    # 強い上下動で MADiff ゼロクロスを起こし TC が建玉を出す（contract_size が profit に効く）。
    rows = []
    for i in range(60):
        c = 100.0 + (5.0 if i % 2 == 0 else -5.0) + (i % 7)
        rows.append({
            # 是正前 "2024-01-01 {i//60:02d}:{i%60:02d}:00" と同一時刻の epoch 秒（UTC）。
            "time": _EPOCH_2024_01_01 + 3600 * (i // 60) + 60 * (i % 60),
            "open": c, "high": c + 2, "low": c - 2, "close": c, "volume": 100, "spread": 0,
        })
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _meta_from(profile_like, csv: Path) -> dict:
    p = profile_like
    return dict(
        data_path=str(csv), symbol="JP225", period="M1", ea_name="TC24051901",
        initial_deposit=10000.0,
        contract_size=p["contract_size"], digits=p["digits"], point_size=p["point_size"],
        leverage=p["leverage"], stops_level=p["stops_level"],
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        ma_period=2, ma_method="sma", lot_size=0.1,
        stop_loss_points=50, take_profit_points=100,
    )


def test_catalog_spec_reproduces_snapshot_run_bit_exact(tmp_path: Path):
    # (b) カタログ定数で実走 == 供給元スナップショット定数で実走（bit-exact）
    csv = _oscillating_csv(tmp_path / "osc.csv")
    jp = _jp225_profile()
    keys = ("contract_size", "digits", "point_size", "leverage", "stops_level")
    catalog_spec = {k: getattr(jp, k) for k in keys}
    # 期待側は供給元から直に引く（case.yaml は stops_level / volume を持たないため
    # オラクルになれない＝ISSUE-445 段階 2 でオラクルをスナップショットへ移した）。
    supply = spec_fields(_snapshot())
    fixture_spec = {k: supply[k] for k in keys}

    out_a, out_b = tmp_path / "a", tmp_path / "b"
    out_a.mkdir(); out_b.mkdir()
    code_a, _ = run_backtest(output_dir=out_a, **_meta_from(catalog_spec, csv))
    code_b, _ = run_backtest(output_dir=out_b, **_meta_from(fixture_spec, csv))
    assert code_a == 0 and code_b == 0
    a = (out_a / "stats.json").read_text(encoding="utf-8")
    b = (out_b / "stats.json").read_text(encoding="utf-8")
    assert a == b, "カタログ定数と case.yaml 定数の実走結果が食い違う（定数不一致）"
    # 申し送り（🟡-1・実測 2026-08-12）: 本 _oscillating_csv は TC24051901 で trades=0 を出す
    # （実測）。そのため (b) は「0 建玉どうしの一致」を突合しており、定数を変えても結果が動かない
    # ＝実質 vacuous。意味ある negative control には TC が建玉を出す CSV が要る（非 cheap）＝ISSUE 化。
