"""export_trade_markers.py — 確定トレードのマーカー JSON を生成する実行スクリプト。

設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §2.5（列ブリッジ・run・出力・集合包含検証）、
  §4（集合包含: 全マーカー time ⊆ candles time 集合・包含外件数を明示・0 件合格）。

責務＝結線（Composition Root 利用側・main 無改変＝C3）。
  1. DATA_DIR/jp225_m1.csv を読み取り専用で pandas ロード（date,open,high,low,close,volume）。
  2. 列ブリッジ（既存データ非改変・新規 tmp へ書く）: date→time（epoch 秒化）/ +spread=0。
  3. build_interactor(...) で controller/request を構築（committed IF のみ使用）。
  4. result = controller.execute(request)。
  5. TradeMarkersPresenter().present_markers(result, OUT, symbol=spec, ea_name=ea)。
  6. 集合包含検証（全マーカー time ⊆ candles time）。包含外件数を stdout と
     `run_and_export` の戻り値（`markers_outside_candles`）に明示。

既存データ（marketdata/）は読み取り専用。tempfile（実行後削除）と新規 OUT のみ書く（C1）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from marketdata.paths import DATA_DIR
from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from simulator.adapter.presenter.trade_markers import TradeMarkersPresenter
from simulator.adapter.repository.tick_parquet import timestamp_epoch_seconds
from simulator.main import build_interactor

# 既定パス（リポジトリルート相対）。時系列データは marketdata.paths.DATA_DIR（単一基点・
# Sd §10.1 C-1 / §10.2 H-5）配下へ集約する（tools 層のみ・usecase/domain/adapter 無改変）。
_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CSV = DATA_DIR / "jp225_m1.csv"
_DEFAULT_OUT = _ROOT / "indigators" / "indicator_ui" / "web" / "data" / "trade_markers.json"

# 銘柄仕様の供給元（ISSUE-445 段階 2 と同じ唯一の権威）。銘柄名・サーバ名は**同一性**の指定で
# あって仕様の値ではない（値は 1 つもここに書かない）。
_SYMBOL = "JP225"
_SERVER = OANDA_JAPAN_MT5_LIVE

# engine（comma 形式 CsvOHLCRepository）が要求する列順。
_ENGINE_COLUMNS = ["time", "open", "high", "low", "close", "volume", "spread"]


@dataclass(frozen=True)
class _Symbol:
    """present_markers に注入する symbol（name/digits）。SymbolSpec に name は無いため別物。"""
    name: str
    digits: int


def read_recent_marketdata(src: Any, n: int) -> pd.DataFrame:
    """marketdata（date,open,high,low,close,volume）の末尾 n 行を header 付きで返す（Fix-A）。

    297MB を丸読みしないため、まず行数を数え `skiprows=range(1, total-n+1)` で末尾のみ読む
    （行 0=header は保持）。`total<=n` の場合は全行を返す。src は読み取り専用（バイト不変）。
    """
    # データ行数（header を除く）を数える。
    with open(src, encoding="utf-8") as f:
        total = sum(1 for _ in f) - 1
    if total <= n:
        return pd.read_csv(src)
    # 先頭 (total-n) データ行をスキップし末尾 n 行のみ読む（header=行 0 は保持）。
    return pd.read_csv(src, skiprows=range(1, total - n + 1))


def _load_marketdata(csv_path: Any, rows: "int | None", from_head: bool) -> pd.DataFrame:
    """読み取り専用ロードの経路選択を一箇所に集約する（src 非改変）。

    rows=None は全行、from_head=True は先頭 N 本（後方互換）、既定は直近 tail
    （`read_recent_marketdata`）。run_and_export から読み込み分岐を分離する（SRP）。
    """
    if rows is None:
        return pd.read_csv(csv_path)
    if from_head:
        return pd.read_csv(csv_path, nrows=rows)
    return read_recent_marketdata(csv_path, rows)


def bridge_marketdata_df(src: pd.DataFrame) -> pd.DataFrame:
    """marketdata 形式（date,open,...,volume）を engine 形式へブリッジする（src 非改変）。

    date→time rename・epoch 秒化・spread=0 付与。既存 DataFrame は変更しない（コピーで構築）。

    ISSUE-411: engine の `Bar.time` 契約は epoch int / ``numpy.datetime64`` であり、rename
    しただけの naive 文字列は契約違反だった（`CsvOHLCRepository` が ``Bar(time=str)`` を作る）。
    marketdata の date 列は naive 文字列で UTC（ユーザー裁定 2026-08-18）。文字列 →
    epoch 秒の規則は tick store の公開実体 `timestamp_epoch_seconds` が唯一持ち（naive=UTC・
    秒へ floor）、ここでは書き写さず呼ぶだけにする。
    """
    bridged = src.rename(columns={"date": "time"}).copy()
    bridged["time"] = timestamp_epoch_seconds(pd.to_datetime(bridged["time"]))
    bridged["spread"] = 0
    return bridged[_ENGINE_COLUMNS]


def candle_unix_times(bridged: pd.DataFrame) -> set[int]:
    """ブリッジ後の time 列（既に epoch 秒）を集合にする。変換実体を持たない。"""
    return {int(v) for v in bridged["time"]}


def markers_outside_candle_times(payload: dict, candle_times: set[int]) -> list[int]:
    """全マーカー time のうち candles time 集合に包含されない time を列挙する（無音禁止）。"""
    return [
        m["lwc"]["time"]
        for m in payload.get("markers", [])
        if m["lwc"]["time"] not in candle_times
    ]


def _meta(data_path: Any, ea_name: str) -> dict:
    """JP225 既定の run メタ（design §2.5）。committed build_interactor の全必須引数を満たす。

    銘柄仕様 8 項目（contract_size / volume_min / volume_max / volume_step / stops_level /
    digits / point_size / leverage）は**供給元スナップショットだけを権威**とする
    （ISSUE-445 RC-1・段階 2 と同じ規律）。ここに数値リテラルを書かない＝人が値を選べない。

    以前は ``contract_size`` に 10.0、``leverage`` に 100.0 を直書きしていた。前者は MT5 レポート
    に一度も現れない逆算値（真値 1.0）であり、後者は実口座値 10 と食い違っていた（ISSUE-445）。
    ``leverage`` は本ツールでは観測不能である（実測 2026-08-26: 0.5 / 10 / 100 のいずれでも
    出力が同一）。理由は ``build_interactor(stop_out_level=0.0)`` の既定のまま
    ``stop_out_level`` を渡しておらず、``margin_level() < stop_out_level`` が成立しないためで、
    証拠金経路そのものが無効化されている。よって真値へ寄せるのは無償である。

    ``lot_size`` は EA 入力であって銘柄仕様ではないが、``TC24051901`` は原典 ``.mq5`` を持たず
    ``NormalizeLot`` 相当を実装しない（``cfg["lot_size"]`` を素通し・ISSUE-445 段階 3-B の申し送り）。
    したがって Root が**発注可能な lot** を供給しなければ ``Order.validate`` が
    ``InvalidPriceError`` を出す（実測: 供給元の ``volume_min=1.0`` の下で従来の 0.1 は不成立）。
    本ツールは特定の MT5 run を再現するものではなく最小建玉でマーカーを描くだけなので、
    最小発注単位＝供給元の ``volume_min`` を用いる（人が選んだ数ではない）。
    従来値との積 ``lot × contract_size`` は ``0.1×10 = 1.0×1.0`` で不変である。実測（2026-08-26・
    直近 5000 本）: ``markers`` 配列は従来と **bit-exact 一致**、``pairs`` は ``volume`` が
    0.1 → 1.0（＝実ブローカーの最小発注単位・MT5 レポートの実約定 volume と同値）になり、
    602 件中 106 件の ``profit`` が乗算順序に由来する 1 ULP（最大 2.9e-14・相対 1.8e-16）だけ
    動く。合計損益は最終桁まで同値。
    """
    spec = load_spec_fields(_SERVER, _SYMBOL)
    return dict(
        data_path=data_path,
        symbol=_SYMBOL,
        period="M1",
        ea_name=ea_name,
        initial_deposit=10_000.0,
        # contract_size / volume_min / volume_max / volume_step / stops_level /
        # digits / point_size / leverage の 8 キー（供給元が唯一の権威）。
        **spec,
        ma_period=14,
        ma_method="sma",
        # 最小建玉（供給元の最小発注単位）。素通し戦略のため Root で発注可能値を供給する。
        lot_size=spec["volume_min"],
        stop_loss_points=500,
        take_profit_points=3000,
        # Fix-B: 証拠金割れでも強制決済して完走する（MarginCallError を出さない）。
        config_overrides={"stop_out_action": "close_and_halt"},
    )


def run_and_export(
    *,
    csv_path: Path,
    out_path: Path,
    ea_name: str,
    rows: "int | None",
    from_head: bool = False,
) -> dict:
    """marketdata を読み取り専用ロード→ブリッジ→run→presenter→集合包含検証して summary を返す。

    Fix-A: rows 指定時は既定で直近 tail（`read_recent_marketdata`）を読む。先頭 N 本が必要な
    場合は `from_head=True`（後方互換オプション）。rows=None は全行。

    戻り値は書き出した markers JSON の内容に step 6（集合包含検証）の結果
    ``markers_outside_candles``（包含外マーカー件数）を加えた summary である。ISSUE-411:
    包含外件数は stdout への print だけで、呼出側から検証できずサイレントだった。
    **出力 JSON ファイルには加えない**（out_path のバイト列は不変）。
    """
    # 1. marketdata 読み取り専用ロード（経路選択は _load_marketdata に集約）。
    src = _load_marketdata(csv_path, rows, from_head)
    # 2. 列ブリッジ（tmp へ engine 形式 CSV を書く・実行後削除）。
    bridged = bridge_marketdata_df(src)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    )
    try:
        bridged.to_csv(tmp.name, index=False)
        tmp.close()
        # 3. controller/request 構築（committed IF のみ）。run メタは 1 回だけ組み、
        #    presenter へ渡す symbol も同じ meta から取る（同じ値を 2 か所に書かない）。
        meta = _meta(tmp.name, ea_name)
        controller, request = build_interactor(**meta)
        # 4. result = execute（committed 公開 IF）。
        result = controller.execute(request)
    finally:
        os.unlink(tmp.name)

    # 5. presenter → OUT（新規パス）。
    out_path.parent.mkdir(parents=True, exist_ok=True)
    spec = _Symbol(name=meta["symbol"], digits=meta["digits"])
    # 該当時間足＝建玉の時間足。バックテストは M1（1分足）なので '1m'。
    TradeMarkersPresenter().present_markers(
        result, out_path, symbol=spec, ea_name=ea_name, timeframe="1m"
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))

    # 6. 集合包含検証（全マーカー time ⊆ candles time）。
    candle_times = candle_unix_times(bridged)
    outside = markers_outside_candle_times(payload, candle_times)

    n_trades = len(result.trades)
    n_markers = payload["count"]
    times = [m["lwc"]["time"] for m in payload["markers"]]
    rng = (min(times), max(times)) if times else (None, None)
    print(f"[trade-markers] trades={n_trades} markers={n_markers} time_range={rng}")
    print(f"[trade-markers] candle_times={len(candle_times)} markers_outside_candles={len(outside)}")
    if outside:
        print(f"[trade-markers] WARNING: {len(outside)} markers outside candle time set: {outside[:10]}")
    # step 6 の結果を戻り値でも観測可能にする（print のみ＝サイレントの解消・ISSUE-411）。
    return {**payload, "markers_outside_candles": len(outside)}


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description="Export trade markers JSON for chart overlay.")
    # Fix-A: 既定は直近 N 本（tail）。UI の RECENT_BARS=1500 を内包する余裕（5000）。
    parser.add_argument("--rows", type=int, default=5000, help="直近 N 本に限定（既定 5000・tail）")
    parser.add_argument(
        "--from-head",
        action="store_true",
        help="先頭 N 本を読む（後方互換オプション・既定は直近 tail）",
    )
    parser.add_argument("--ea", dest="ea_name", default="TC24051901", help="EA 名（既定 TC24051901）")
    parser.add_argument("--out", default=str(_DEFAULT_OUT), help="出力 JSON パス")
    parser.add_argument("--csv", default=str(_DEFAULT_CSV), help="入力 marketdata CSV")
    args = parser.parse_args(argv)
    try:
        summary = run_and_export(
            csv_path=Path(args.csv),
            out_path=Path(args.out),
            ea_name=args.ea_name,
            rows=args.rows,
            from_head=args.from_head,
        )
    except Exception as exc:  # noqa: BLE001 — 非ゼロ終了＋メッセージ（既存データ非改変）
        print(f"[trade-markers] ERROR: {exc}", file=sys.stderr)
        return 1
    # step 6（集合包含検証）は 0 件合格（design §4）。包含外を成功終了にしない
    # （ISSUE-411 🟡-3: summary へ載せても main が捨てれば CLI としてはサイレント）。
    outside = summary["markers_outside_candles"]
    if outside:
        print(
            f"[trade-markers] ERROR: {outside} markers outside candle time set",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
