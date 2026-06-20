# バックテストエンジン Python 設計仕様書

`sample/MQL5/` のカスタム EA・指標を Python で再現するためのバックテストエンジンを設計する。本書は **9 項目（Objective / Scope / Assumptions / Constraints / Input / Processing / Entities / Output / Exception）** を決定論的に確定し、「誰が実装しても同じ結果になる」水準を保証する。

- **関連ドキュメント**:
  - 戦略ロジック仕様: [`./BACKTEST_SPEC.md`](./BACKTEST_SPEC.md)
  - 実行プロセス（OnTick の処理順）: [`./BACKTEST_PROCESS.md`](./BACKTEST_PROCESS.md)
  - 分析結果の用語・算出式: [`./BACKTEST_METRICS.md`](./BACKTEST_METRICS.md)
  - MQL5 → Python 移植ガイド: [`./PORTING_GUIDE.md`](./PORTING_GUIDE.md)

---

## 1. Objective（目的）

カスタム EA（`TC24051901.mq5` 等）の **MetaTrader 5 ストラテジーテスター結果** と、Python 実装によるバックテスト結果を、定量基準で一致させる。

**成功判定基準（定量）：**

| 指標 | 許容誤差 | 出典 |
|---|---|---|
| `STAT_PROFIT` | **±0.5%** | `TesterStatistics()` 公式値 |
| `STAT_GROSS_PROFIT` / `STAT_GROSS_LOSS` | ±0.5% | 同上 |
| `STAT_TRADES` / `STAT_DEALS` | **完全一致** | 同上（件数は誤差不可） |
| `STAT_PROFIT_TRADES` / `STAT_LOSS_TRADES` | 完全一致 | 同上 |
| `STAT_BALANCE_DD` / `STAT_BALANCEDD_PERCENT` | ±0.5% | 同上 |
| `STAT_MAX_CONWINS` / `STAT_MAX_CONLOSSES` | 完全一致 | 同上 |
| `STAT_MAX_PROFITTRADE` / `STAT_MAX_LOSSTRADE` | ±0.5% | 同上 |
| エントリ／決済の足時刻 | 完全一致 | バックテストログ |
| エントリ／決済の方向（buy/sell） | 完全一致 | 同上 |

> ±0.5% の許容理由: スプレッド・スワップ・約定価格のティック単位差分が原典側にも非決定性として残るため、完全一致は不可能。`BACKTEST_METRICS.md §12` の手順で算出した値と突合する。

---

## 2. Scope（範囲）

### 2.1 対象 EA（フェーズ別）

| Phase | 対象 EA | 戦略の核 | 依存指標 |
|---|---|---|---|
| **Phase 1** | `TC24051901.mq5` | MADiff ゼロクロス両建て | MADiff |
| **Phase 2** | `TC24051902.mq5` / `TC24051903.mq5` | MADiff 系（買い専・指標反転決済） | MADiff |
| **Phase 3** | `PRO!fit_Band.mq5` | EMA 傾き + ADX + DI | EMA / ADX / DI（標準指標） |

### 2.2 対象外（理由付き）

| EA | 除外理由 |
|---|---|
| `TC24051903_24052301.mq5` | Band 指標ソース不在（`BACKTEST_SPEC.md §4`）。再現不能 |
| `range.mq5` | `OnTick` 空スケルトン。売買発生せず |
| `my_first_ea.mq5` | `PRO!fit_Band.mq5` のバイト同一クローン |

### 2.3 機能範囲

| 機能 | 含む / 含まない |
|---|---|
| ヒストリカルバックテスト | **含む** |
| 1 EA × 1 シンボル × 1 期間 × 1 パラメータセット | **含む** |
| パラメータスイープ／最適化 | **Phase 1 では含まない**（Phase 2 以降） |
| フォワードテスト | **含まない**（Phase 1） |
| マルチシンボル | **含まない** |
| クロス通貨換算 | **含まない**（口座通貨 = 決済通貨と仮定） |
| ライブトレード | **含まない**（バックテスト専用） |

---

## 3. Assumptions（前提条件）

エンジンは以下を **「与えられたものとして扱う」**。違反は `DataError`／`ConfigError` で即停止（Item 9）。

### 3.1 入力データ前提

| # | 前提 | 検証方法 |
|---|---|---|
| 1 | 入力は `pd.DataFrame`、`pd.DatetimeIndex`（UTC、tz-aware）、昇順、重複なし | スキーマ検証（起動前） |
| 2 | 列構成: `open / high / low / close / volume / spread` （`spread` は points 単位、整数） | 同上 |
| 3 | OHLC は `low ≤ open, close ≤ high` を満たす | 同上 |
| 4 | タイムフレーム M1 を標準。それ以外は `config.timeframe` で明示 | `config` で固定 |
| 5 | 週末・市場休場ギャップは **欠落行として表現**（補間しない） | NaN 行を含まない前提 |
| 6 | 入力期間中、シンボル仕様（`SYMBOL_TRADE_CONTRACT_SIZE` 等）は不変 | `SymbolSpec` で 1 つのみ受領 |

### 3.2 シンボル仕様前提

`SymbolSpec` として以下を `config` 経由で供給（MT5 `SymbolInfoDouble`／`SymbolInfoInteger` の値）:

- `digits: int`（小数桁数）
- `point_size: float`（1 point の値、例 USD/JPY = 0.001）
- `contract_size: float`（標準ロット数量、例 FX = 100_000）
- `volume_step: float`（最小ロット刻み、例 0.01）
- `volume_min / volume_max: float`
- `swap_long / swap_short: float`（翌日繰越金利、口座通貨建て）
- `commission: float`（1 ロットあたり手数料、口座通貨建て）
- `stops_level: int`（SL/TP の最小許容距離、points）
- `freeze_level: int`（ポジション凍結距離、points）

### 3.3 環境前提

- 口座通貨 = シンボル決済通貨。クロス換算は当面非対応。
- 初期残高 `initial_deposit` は `config` で指定（口座通貨）。
- レバレッジ `leverage: int` は `config` で指定。

---

## 4. Constraints（制約条件）

### 4.1 技術スタック

| 領域 | 採用 | バージョン |
|---|---|---|
| 言語 | Python | **3.11+** |
| データフレーム | `pandas` | 2.x |
| 数値計算 | `numpy` | 1.26+ |
| 設定モデル | `pydantic` | **v2** |
| テスト | `pytest` | 7.x+ |
| チャート | `lightweight-charts-python` | 既存導入版に揃える |
| テンプレ（レポート） | `jinja2` | 標準 |

### 4.2 不採用とその理由

| 技術 | 不採用理由 |
|---|---|
| `polars` | 既存 `common/`／`indicators/profit_*/src/` が `pandas` 中心。依存差分回避 |
| `numba` / `cython` | Phase 1 規模ではベクトル化で十分。YAGNI |
| `multiprocessing` / `joblib` | パラメータスイープは Phase 2 以降に後付け |
| `SQLAlchemy` / DB 永続化 | Item 7=A メモリ完結方針 |
| `matplotlib` | `lightweight-charts` で統一 |

### 4.3 性能目標

| 区分 | 目標 |
|---|---|
| **Phase 1** 1 run（M1 × 1 年 × 1 EA × 1 パラメータセット） | **30 秒以内**（Apple Silicon M シリーズ前提） |
| メモリ消費 | 1 GB 以下 |
| 逸脱時の対応 | ホットスポット計測 → numpy ベクトル化最適化に着手（先取り最適化はしない） |

### 4.4 設計制約

- **Fail-Stop 方針**: 異常検出時は run を中止（Item 6）。沈黙のスキップは禁止。
- **責務境界**: データクレンジングはエンジン外（`analysis/` 側）に置く（Item 3）。
- **YAGNI 原則**: パラメータスイープ・並列化・永続化はエンジン本体に内蔵しない。

---

## 5. Input / Trigger（入力・実行モデル）

### 5.1 エントリポイント

```python
from backtest import Engine, BacktestConfig, SymbolSpec

config = BacktestConfig(
    symbol_spec=SymbolSpec(...),
    initial_deposit=100_000.0,
    leverage=100,
    timeframe="M1",
    ea_name="TC24051901",
    ea_params={"FastMA": 12, "SlowMA": 26, "SL_points": 200, "TP_points": 400},
    tick_model="ohlc_simulate",  # M1 OHLC からの擬似ティック生成
)
engine = Engine(config)
result = engine.run(data=df)     # df: pd.DataFrame（§3.1 形式）
```

- 同期実行。`engine.run()` は終了まで戻らない。
- `engine` は再利用可能（状態は run 単位でリセット）。
- 並列実行は呼出側で複数 `Engine` インスタンスを並走させる（エンジン本体は責務を持たない）。

### 5.2 CLI ラッパ（薄い）

```
python -m backtest run \
  --config config.yaml \
  --data path/to/m1.parquet \
  --output result/
```

- `config.yaml` を `BacktestConfig` にロード。
- `data` は parquet / csv を判別ロード。
- 終了コード: 0（成功）／1（`BacktestError`）／2（`ConfigError`）。
- 出力: `result/stats.json` ／ `result/trades.parquet` ／ `result/report.html`。

CLI は `if __name__ == "__main__":` で `Engine().run()` を呼ぶ薄いラッパとして実装（10〜30 行）。

### 5.3 Notebook 利用

```python
result = Engine(config).run(df)
result.equity_curve.plot()
result.compare(mt5_stats_dict)
```

---

## 6. Processing Logic（処理内容）

### 6.1 原子性方針

1 run は **原子的**。途中で異常を検出した時点で `BacktestError` を送出し、部分結果は **破棄する**。再開・スキップは行わない。

### 6.2 メインループ

`BACKTEST_PROCESS.md §2` に準拠。骨格は以下:

```
for bar_index in range(warmup, len(df)):
    bar = df.iloc[bar_index]

    # (1) 指標バッファ更新（前バー確定後の値で評価）
    indicators.update(bar_index)

    # (2) 既存ポジションの SL/TP / 反転シグナル判定
    for position in account.open_positions:
        deal = check_close_conditions(position, bar, indicators)
        if deal:
            execute_close(position, deal)

    # (3) 新規エントリシグナル評価（EA ロジック）
    signal = ea.on_new_bar(bar_index, indicators, account)
    if signal:
        order = build_order(signal, bar, config)
        execute_open(order)

    # (4) Equity / Margin 更新（含み損益反映）
    account.update_floating_pnl(bar)
    if account.margin_level < stop_out_level:
        raise MarginCallError(...)
```

### 6.3 ティック生成モデル

| モデル | 仕様 | Phase 1 採用 |
|---|---|---|
| `ohlc_simulate` | M1 OHLC から擬似 4 ティック（O → H → L → C または O → L → H → C）を生成。順序は前バー終値と当バー始値の関係で決定 | **採用** |
| `every_tick_real` | 実ティックデータ供給時のみ | 非採用（Phase 1） |
| `open_only` | 各バー始値のみ | 非採用 |

`ohlc_simulate` の順序確定ルールは `BACKTEST_PROCESS.md §0` に準拠。

### 6.4 約定モデル

- 成行: 当該ティック価格で即時約定。スプレッドは `bid = price - spread/2 × point_size`、`ask = price + spread/2 × point_size` で算出（または `spread` 列を `Ask − Bid` として扱う）。
- 指値（Limit）／逆指値（Stop）: 当該ティックがトリガー価格に到達した時点で約定。約定価格はトリガー価格（スリッページ 0）。
- SL/TP: ポジション保有中のティック単位判定。トリガー時は SL/TP 価格そのもので約定。
- 部分決済は Phase 1 では未対応（仕様上発生しない）。

### 6.5 1 トレード損益式

```
p_i = (close_i − entry_i) · sign_i · lot_i · contract_size
      + swap_i
      + commission_i
```

`BACKTEST_METRICS.md §5.2` に準拠。

---

## 7. Entities（ドメインモデル）

### 7.1 永続化方針

**メモリ完結型**。run 終了時に `BacktestResult` を返却し、永続化は呼出側責務（`result.trades.to_parquet(...)` 等）。

### 7.2 ドメインモデル定義

```python
from pydantic import BaseModel, ConfigDict
import pandas as pd

class SymbolSpec(BaseModel):
    digits: int
    point_size: float
    contract_size: float
    volume_step: float
    volume_min: float
    volume_max: float
    swap_long: float
    swap_short: float
    commission: float
    stops_level: int
    freeze_level: int

class BacktestConfig(BaseModel):
    symbol_spec: SymbolSpec
    initial_deposit: float
    leverage: int
    timeframe: str
    ea_name: str
    ea_params: dict
    tick_model: Literal["ohlc_simulate"] = "ohlc_simulate"
    stop_out_level: float = 50.0  # margin_level (%)

class Bar(BaseModel):
    time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: int

class Order(BaseModel):
    type: Literal["market", "limit", "stop"]
    side: Literal["buy", "sell"]
    volume: float
    price: float | None     # market は None
    sl: float | None
    tp: float | None
    deviation: int = 10
    comment: str = ""

class Position(BaseModel):
    ticket: int
    side: Literal["buy", "sell"]
    volume: float
    entry_time: pd.Timestamp
    entry_price: float
    sl: float | None
    tp: float | None
    swap: float = 0.0
    commission: float = 0.0

class Deal(BaseModel):
    ticket: int
    position_ticket: int
    direction: Literal["in", "out"]
    type: Literal["buy", "sell", "sl", "tp"]
    time: pd.Timestamp
    price: float
    volume: float
    profit: float
    swap: float
    commission: float

class Account(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    open_positions: list[Position]

class BacktestStats(BaseModel):
    # MT5 STAT_* と 1:1 対応
    initial_deposit: float
    profit: float                        # STAT_PROFIT
    gross_profit: float                  # STAT_GROSS_PROFIT
    gross_loss: float                    # STAT_GROSS_LOSS
    profit_factor: float                 # STAT_PROFIT_FACTOR
    expected_payoff: float               # STAT_EXPECTED_PAYOFF
    recovery_factor: float               # STAT_RECOVERY_FACTOR
    sharpe_ratio: float                  # STAT_SHARPE_RATIO
    balance_min: float                   # STAT_BALANCEMIN
    balance_dd: float                    # STAT_BALANCE_DD
    balance_dd_percent: float            # STAT_BALANCEDD_PERCENT
    balance_ddrel_percent: float         # STAT_BALANCE_DDREL_PERCENT
    balance_dd_relative: float           # STAT_BALANCE_DD_RELATIVE
    equity_min: float                    # STAT_EQUITYMIN
    equity_dd: float                     # STAT_EQUITY_DD
    equity_dd_percent: float             # STAT_EQUITYDD_PERCENT
    equity_ddrel_percent: float          # STAT_EQUITY_DDREL_PERCENT
    equity_dd_relative: float            # STAT_EQUITY_DD_RELATIVE
    trades: int                          # STAT_TRADES
    deals: int                           # STAT_DEALS
    profit_trades: int                   # STAT_PROFIT_TRADES
    loss_trades: int                     # STAT_LOSS_TRADES
    long_trades: int                     # STAT_LONG_TRADES
    short_trades: int                    # STAT_SHORT_TRADES
    profit_long_trades: int              # STAT_PROFIT_LONGTRADES
    profit_short_trades: int             # STAT_PROFIT_SHORTTRADES
    max_profit_trade: float              # STAT_MAX_PROFITTRADE
    max_loss_trade: float                # STAT_MAX_LOSSTRADE
    max_con_wins: int                    # STAT_MAX_CONWINS
    max_con_profit_trades: float         # STAT_MAX_CONPROFIT_TRADES
    max_con_losses: int                  # STAT_MAX_CONLOSSES
    max_con_loss_trades: float           # STAT_MAX_CONLOSS_TRADES
    con_profit_max: float                # STAT_CONPROFITMAX
    con_profit_max_trades: int           # STAT_CONPROFITMAX_TRADES
    con_loss_max: float                  # STAT_CONLOSSMAX
    con_loss_max_trades: int             # STAT_CONLOSSMAX_TRADES
    avg_con_wins: float                  # STAT_PROFITTRADES_AVGCON
    avg_con_losses: float                # STAT_LOSSTRADES_AVGCON
    # 計算値（ENUM_STATISTICS に該当なし）
    ahpr: float
    ghpr: float
    z_score: float
    lr_correlation: float
    lr_standard_error: float

class BacktestResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    config: BacktestConfig
    trades: pd.DataFrame             # 確定トレード（往復）。columns: ticket, side, entry_time, exit_time, entry_price, exit_price, volume, profit, swap, commission
    deals: pd.DataFrame              # 約定明細
    equity_curve: pd.Series          # index: pd.DatetimeIndex
    balance_curve: pd.Series         # index: 確定トレード closing 時刻
    stats: BacktestStats
    indicator_values: dict[str, pd.Series]
```

### 7.3 EA 抽象

```python
from abc import ABC, abstractmethod

class EAStrategy(ABC):
    @abstractmethod
    def on_init(self, config: BacktestConfig, indicators: IndicatorRegistry) -> None: ...

    @abstractmethod
    def on_new_bar(
        self,
        bar_index: int,
        indicators: IndicatorRegistry,
        account: Account,
    ) -> list[Order]: ...

    @abstractmethod
    def on_position_check(
        self,
        position: Position,
        bar_index: int,
        indicators: IndicatorRegistry,
    ) -> Literal["hold", "close"]: ...
```

各 EA は `EAStrategy` を継承して実装（例: `TC24051901(EAStrategy)`）。

### 7.4 ディレクトリ構造（提案）

```
backtest/
├── __init__.py
├── engine.py              # Engine, run loop
├── config.py              # BacktestConfig, SymbolSpec
├── models.py              # Bar, Order, Position, Deal, Account
├── result.py              # BacktestResult, BacktestStats
├── stats.py               # STAT_* 算出ロジック
├── indicators/
│   ├── registry.py        # IndicatorRegistry
│   ├── madiff.py          # MADiff（BACKTEST_SPEC.md §2）
│   └── ema_adx_di.py      # Phase 3 用
├── strategies/
│   ├── base.py            # EAStrategy
│   ├── tc24051901.py      # Phase 1
│   ├── tc24051902.py      # Phase 2
│   └── pro_fit_band.py    # Phase 3
├── execution/
│   ├── order_executor.py  # 成行/指値/逆指値
│   ├── sltp_checker.py
│   └── tick_simulator.py  # ohlc_simulate
├── reporting/
│   ├── markdown.py
│   ├── html.py
│   └── templates/
├── exceptions.py          # 階層化例外
└── __main__.py            # CLI ラッパ

tests/
├── unit/
├── integration/
└── fixtures/
    └── mt5_outputs/       # 各 EA の MT5 STAT_* 期待値（JSON）
```

---

## 8. Output（出力）

### 8.1 `BacktestResult` API

```python
result = engine.run(data=df)

# 統計
result.stats                       # BacktestStats（型安全）
result.stats.model_dump()          # dict
result.stats.profit                # 直接アクセス

# 明細
result.trades                      # pd.DataFrame
result.deals                       # pd.DataFrame
result.equity_curve                # pd.Series
result.balance_curve               # pd.Series
result.indicator_values["MADiff"]  # pd.Series

# レポート生成
result.to_markdown() -> str        # MT5 風レポート（Markdown）
result.to_html(path: Path) -> None # 同 HTML（lightweight-charts 埋込）
result.to_json(path: Path) -> None # stats.json

# MT5 突合
result.compare(mt5_stats: dict) -> ComparisonReport
```

### 8.2 標準 Markdown レポート（テンプレ）

```markdown
# Backtest Report: {ea_name}

- Symbol: {symbol}
- Period: {start} ~ {end}
- Initial Deposit: {initial_deposit}

## Summary
| 指標 | 値 |
|---|---|
| Net Profit | {profit} |
| Gross Profit / Loss | {gross_profit} / {gross_loss} |
| Profit Factor | {profit_factor} |
| Recovery Factor | {recovery_factor} |
| Sharpe Ratio | {sharpe_ratio} |

## Drawdown
...
## Trade Statistics
...
```

### 8.3 MT5 突合 (`result.compare()`)

```python
class ComparisonReport(BaseModel):
    matches: list[tuple[str, float, float]]      # 一致した項目
    mismatches: list[tuple[str, float, float, float]]  # (name, py_value, mt5_value, error_pct)
    passed: bool                                  # 全項目が許容誤差内か

mt5_stats = {
    "STAT_PROFIT": 12345.67,
    "STAT_TRADES": 42,
    ...
}
report = result.compare(mt5_stats)
assert report.passed  # Item 1 = A の達成判定
```

---

## 9. Exception（例外処理）

### 9.1 例外階層

```
BacktestError                              # 基底
├── ConfigError                            # 設定不正（起動前検出）
├── DataError                              # 入力データ起因
│   ├── MissingBarError                    # 欠損足
│   ├── OHLCInvalidError                   # OHLC 矛盾（high < low 等）
│   └── TimeOrderError                     # 時刻昇順違反
├── IndicatorError                         # 指標計算起因
│   ├── IndicatorNaNError                  # NaN がシグナル評価必要箇所に出た
│   └── IndicatorBufferError               # バッファ参照不正
└── ExecutionError                         # 約定・口座起因
    ├── InsufficientMarginError            # 証拠金不足
    ├── InvalidPriceError                  # SL/TP 価格制約違反（stops_level 等）
    └── MarginCallError                    # ストップアウト
```

### 9.2 例外クラス共通属性

```python
class BacktestError(Exception):
    def __init__(
        self,
        message: str,
        *,
        timestamp: pd.Timestamp | None = None,
        symbol: str | None = None,
        bar_index: int | None = None,
        context: dict | None = None,
    ):
        super().__init__(message)
        self.timestamp = timestamp
        self.symbol = symbol
        self.bar_index = bar_index
        self.context = context or {}
```

### 9.3 例外送出箇所と原因

| 例外 | 送出箇所 | 原因例 |
|---|---|---|
| `ConfigError` | `Engine.__init__` | `initial_deposit ≤ 0` / 不正な `ea_name` |
| `MissingBarError` | データロード時 | 期間内に欠損行（NaN） |
| `OHLCInvalidError` | データロード時 | `high < low` 等の矛盾 |
| `TimeOrderError` | データロード時 | index が昇順でない |
| `IndicatorNaNError` | `EAStrategy.on_new_bar` 内で NaN 検出 | warmup 不足／指標バッファ未初期化 |
| `IndicatorBufferError` | `IndicatorRegistry.get` | 未登録バッファ参照 |
| `InsufficientMarginError` | `execute_open` | 必要証拠金 > free_margin |
| `InvalidPriceError` | `Order` バリデーション | SL/TP が `stops_level` 違反 |
| `MarginCallError` | メインループ §6.2 (4) | `margin_level < stop_out_level` |

### 9.4 呼出側の扱い

```python
try:
    result = Engine(config).run(df)
except ConfigError as e:
    sys.exit(2)
except BacktestError as e:
    logger.error(f"Backtest failed at bar {e.bar_index} ({e.timestamp}): {e}")
    logger.error(f"Context: {e.context}")
    sys.exit(1)
```

### 9.5 テスト

```python
def test_nan_indicator_raises():
    df = make_test_data_with_missing_indicator_warmup()
    with pytest.raises(IndicatorNaNError) as exc_info:
        Engine(config).run(df)
    assert exc_info.value.bar_index == 5
    assert "MADiff" in exc_info.value.context

def test_mt5_stats_match():
    df = load_fixture("USDJPY_m1_2024.parquet")
    mt5_expected = json.load(open("fixtures/mt5_outputs/tc24051901.json"))
    result = Engine(config_tc24051901).run(df)
    report = result.compare(mt5_expected)
    assert report.passed, report.mismatches
```

---

## 10. 9 項目確定一覧

| # | Item | 確定 | 要点 |
|---|---|---|---|
| 1 | Objective | A | MT5 完全再現基準（±0.5%／件数完全一致） |
| 2 | Scope | C | Phase 1: TC24051901 のみ。#4・#6・#5'は除外 |
| 3 | Assumptions | A | クリーンデータ前提・前処理は `analysis/` 側 |
| 4 | Constraints | A | Python 3.11+ / pandas / numpy / pydantic v2 |
| 5 | Input/Trigger | B | `Engine().run()` ライブラリ API。CLI は薄いラッパ |
| 6 | Processing | A | Fail-Stop。run は原子的 |
| 7 | Entities | A | メモリ完結。pydantic ドメインモデル＋ `BacktestResult` |
| 8 | Output | B | `BacktestResult` ＋標準 Markdown/HTML レポート同梱 |
| 9 | Exception | B | 階層化例外。`BacktestError` を基底に 4 系統 |

---

## 11. 次工程（実装着手前のタスク）

1. **MT5 突合用フィクスチャ作成**: `TC24051901` を MT5 で実行し `STAT_*` を JSON 化 → `tests/fixtures/mt5_outputs/tc24051901.json` に保存。
2. **入力データ準備**: USDJPY M1 1 年分を Parquet で `tests/fixtures/` に配置。MetaTrader からエクスポート → `analysis/` 側で前処理。
3. **MADiff 指標の Python 移植**: `BACKTEST_SPEC.md §2` の式に従い `backtest/indicators/madiff.py` 実装。単体テストで MT5 の `iCustom` 戻り値と一致確認。
4. **Engine 骨格実装**: §7.4 ディレクトリ構造で空クラスを作成し、`engine.run()` がメインループを回せるところまで。
5. **TC24051901 戦略実装**: `EAStrategy` 継承で MADiff ゼロクロス両建てを実装。
6. **MT5 突合テスト実行**: `result.compare(mt5_expected).passed == True` を達成（Item 1 = A の達成判定）。
