# marketdata 時系列境界 内部設計書（案a・実装可能詳細設計）

> 上位方針: 承認済み案a「時系列データを必要とする全アクターが marketdata 境界に依存する」。
> 本書は案aを前提に、実装者がクラス名・メソッドシグネチャ・処理フロー水準まで迷わず着手できる詳細を確定する。
> 接地（一次情報）: 本書の全 signature・写像・移行手順は実コードを正として確定済み（推測ではない）。
> 参照実体は本文中に `path:line` で明示する。
>
> クリーンアーキ依存方向（厳守）: `usecase → domain` のみ。ライブラリ（dukascopy_python / pandas / pyarrow）は
> adapter / tools へ隔離。simulator usecase は無改変・domain のみ依存を維持する。

---

## 0. 用語・現状の確定事実（接地サマリ）

| 記号 | 実体 | 一次情報 |
|---|---|---|
| `Candle` | `TypedDict{time:int(UNIX秒), open, high, low, close}` ※**volume 無し** | `marketdata/port.py:13-21` |
| `CandleSource` | `Protocol.fetch_candles(start:datetime, end:datetime) -> List[Candle]`（time 昇順・空可） | `marketdata/port.py:23-33` |
| `DukascopyCandleSource` | 銘柄/足種/気配側を**構築時に固定**し fetch_candles は期間のみ受ける | `marketdata/dukascopy_source.py:55-81` |
| `INTERVALS` / `JP225` | 足種名→ライブラリ INTERVAL 定数 / `INSTRUMENT_IDX_ASIA_E_N225JAP` 固定 | `marketdata/dukascopy_source.py:23-35` |
| `repair_ohlc_outliers` | 足内 OHLC 外れ値の純粋補正（ベンダ非依存） | `marketdata/cleaning.py:12-58` |
| `Bar` | `@dataclass(frozen) {time, open, high, low, close, volume:float, spread:int}` ＋ OHLC 不変条件検証 | `simulator/domain/bar.py:19-49` |
| `MarketDataPort` | `abc.load(source_ref, timeframe, period) -> Any(=list[Bar])` | `simulator/usecase/ports.py:47-53` |
| `TickDataPort` | `abc.load_ticks(symbol, start, end, columns=None) -> TickFrame` | `simulator/usecase/ports.py:56-64` |
| `TickStorePort` | `abc.write_ticks(symbol, frame_or_csv, mode="overwrite") -> TickWriteResult` | `simulator/usecase/ports.py:67-78` |
| simulator OHLC 具象 | `CsvOHLCRepository` / `Mt5CsvOHLCRepository` ＋ `MarketDataSourceRepository`（いずれも `MarketDataPort` 実装・`_ohlc_frame.frame_to_bars` 共有）。`ParquetOHLCRepository` は撤去済み（コミット b62bcc3） | `simulator/adapter/repository/{ohlc_csv,ohlc_mt5_csv,marketdata_source,_ohlc_frame}.py` |
| simulator tick 具象 | `ParquetTickRepository(TickDataPort, TickStorePort)`・hive partition | `simulator/adapter/repository/tick_parquet.py:42` |
| simulator 合成点 | `simulator/main/__init__.py` が ea_name 別に具象を**直接 new** し `market_data.load(data_path, None, None)` で `list[Bar]` 取得 | `simulator/main/__init__.py:329-374` |
| indicator_ui resample | `dataset.resample_ohlc(df, rule)` ＋ `TF_DESCRIPTORS`（`marketdata/resample.py`）が唯一の規則源・`TIMEFRAME_RULES` は導出値（ISSUE-134） | `indigators/indicator_ui/api/adapter/compute/dataset.py:60-117` |
| indicator_ui rollup | `rollup_builder`（`dataset.resample_ohlc` を**再利用**してロールアップ CSV 生成）＋ `rollup_store`（読取）＋ `rollup_state.json` | `indigators/indicator_ui/tools/rollup_builder.py`, `api/adapter/compute/rollup_store.py` |
| volume 必須経路の port 迂回 | `export_jp225_m1.py` は **Candle に volume が無いため** dukascopy_python を直接呼ぶ（enabler①の対象） | `indigators/indicator_ui/tools/export_jp225_m1.py:11-14, 42-43, 127-133` |
| indicator_ui の marketdata 直利用 | `prototype_inject_marketdata` / `prototype_swap_data` / `jp225_chart` / `export_jp225_csv` が `marketdata` から `DukascopyCandleSource/INTERVALS/repair_ohlc_outliers` を import | 各 tools の import 行 |
| 孤児スクリプト | ルート `fetch_dukascopy.py` は `bull_bear_analysis` 入力形式へ変換（消費者不在＝孤児） | `fetch_dukascopy.py:1-37` |
| tick canonical 列 | `TICK_COLUMNS = ("timestamp","bid","ask","last","volume")` | `simulator/adapter/repository/_tick_frame.py:20` |
| tick raw→canonical | `to_canonical_ticks`（last=mid 規約・naive UTC 正規化） | `simulator/tools/ingest_ticks.py:25-69` |
| tick 取得 | `fetch_ticks_dukascopy.py`（raw landing・段1）＋ `ingest_ticks.py`（canonical 変換・段2） | 同 tools |

**接地で確定した重要な非自明事実**:
1. simulator は「`MarketDataPort` という seam を**既に持つ**」。指示の「`simulator.usecase.ports.MarketDataPort` を seam として残す」は既存実体であり、新設不要。新設するのは「marketdata へ委譲しつつ `Candle→Bar` 写像する `MarketDataPort` の**別実装**」である。
2. 現状 simulator は委譲アダプタを経由していない（具象 repo を直接 new）。strangler の起点は「既存3 repo を marketdata 委譲の薄いラッパへ置換」だが、**現 repo は marketdata に一切依存していない**（自前 `_ohlc_frame`）。よって「委譲」は将来の実体移管先が marketdata 側に存在して初めて成立する。本書はこの順序依存を §6 で明示する。
3. indicator_ui の resample/rollup は既に「`dataset.resample_ohlc` を単一規則源として再利用」する構造で完成している。marketdata への配置は「規則源の物理的移設」であり、再実装ではない（§4）。

---

## 1. 目標アーキテクチャ

### 1.1 marketdata レイヤ構成（port / adapter / service）

```
marketdata/                         ← 時系列データアクセスの唯一の境界（インフラ層）
├─ port.py                          [PORT] Candle, CandleSource(Protocol)
│                                          ＋ TickSource(Protocol)              ← 新設(enabler②)
├─ dukascopy_source.py              [ADAPTER] DukascopyCandleSource             ← 銘柄/足種を汎用化(enabler④)
│                                          ＋ DukascopyTickSource               ← 新設(enabler②: fetch_ticks_dukascopy 移管)
├─ csv_source.py                    [ADAPTER] CsvCandleSource (新設・任意)       ← CSV→Candle（simulator csv の実体移管先）
├─ mt5_csv_source.py               [ADAPTER] Mt5CsvCandleSource (新設・任意)    ← MT5タブ→Candle
├─ parquet_source.py               [ADAPTER] ParquetCandleSource (新設・任意)
├─ stooq_source.py                 [ADAPTER] StooqCandleSource (要否は§9・別承認) ← 日足・週次ボラ戦略向け
├─ cleaning.py                      [SERVICE] repair_ohlc_outliers（既存・無改変）
├─ resample.py                     [SERVICE] resample_ohlc + TIMEFRAME_RULES    ← 新設(enabler③: dataset から移設)
├─ rollup.py                       [SERVICE] stream_build/incremental_update/RollupState ← 新設(enabler③)
└─ data/                            時系列データ（jp225_m1.csv / rollups / ticks / rollup_state.json）
```

> 実装フェーズ注記: `csv_source`/`mt5_csv_source`/`parquet_source`/`stooq_source` は §6 の移行で「実体移管が必要になった段階」で新設する。strangler の初期ステップでは作らない（§6・順序依存②）。

### 1.2 依存方向図（アクター別の依存点）

```
                          ┌──────────────────────────────────────────┐
                          │            marketdata (境界)               │
                          │  port: CandleSource / TickSource           │
                          │  service: resample / rollup / cleaning     │
                          │  adapter: Dukascopy / CSV / MT5 / Parquet  │
                          └──────────────────────────────────────────┘
                              ▲ (直接依存)        ▲ (委譲・写像経由)
                              │                   │
        ┌─────────────────────┴──────┐   ┌────────┴───────────────────────────────┐
        │  indicator_ui              │   │  simulator                              │
        │  (ポートへ直接依存)         │   │  (adapter/composition 層でのみ依存)      │
        │                            │   │                                         │
        │  api/adapter/compute/      │   │  usecase/  ← domain のみ依存(無改変)     │
        │    dataset (resample 委譲) │   │    ports.MarketDataPort (seam・既存)     │
        │  tools/ (export/inject)    │   │  adapter/repository/                     │
        │    → CandleSource/TickSrc  │   │    MarketDataSourceRepository (新設)     │
        │                            │   │      = MarketDataPort 実装               │
        │                            │   │        ├ marketdata.CandleSource へ委譲  │
        │                            │   │        └ Candle→domain.Bar 写像          │
        └────────────────────────────┘   └─────────────────────────────────────────┘
```

**依存点の確定**:

- **indicator_ui**: `marketdata` ポート/サービスへ**直接依存**してよい（indicator_ui は usecase が JS フロント側で、Python api/tools 層は adapter 相当）。`dataset.resample_ohlc` は `marketdata.resample.resample_ohlc` の**薄い再エクスポート**へ降格する（§4）。tools は既に `marketdata` を直 import 済み（接地済み）。
- **simulator**: usecase は `MarketDataPort`（abc）にのみ依存し**無改変**。`marketdata` への依存は新設 adapter `MarketDataSourceRepository`（`MarketDataPort` 実装）に閉じる。これが `marketdata.CandleSource` へ委譲し `Candle → domain.Bar` を写像する。**simulator usecase は marketdata を import しない**（クリーンアーキ厳守）。
- **enabler**: `Candle` に volume 追加（①）／`TickSource` 新設（②）／resample・rollup 移設（③）／銘柄・足種 externalize（④）。

---

## 2. 型設計: Candle ↔ Bar の関係と写像

### 2.1 Candle の volume 追加後の定義（enabler①）

現状 `Candle`（`port.py:13`）は volume を持たない。これが `export_jp225_m1.py` の port 迂回（dukascopy 直呼び）の根本原因（`export_jp225_m1.py:13-14` が「marketdata の Candle は volume を持たないため、原子に volume を残す目的でライブラリを直接呼ぶ」と明記）。

**変更後の Candle 定義**:

```python
# marketdata/port.py
class Candle(TypedDict):
    """供給する 1 本の OHLC。time は解像度非依存の UNIX 秒（整数）。"""
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float   # ← 追加（enabler①）。tick volume または出来高。
```

**後方互換の確定（重要・非破壊保証）**:
- `Candle` は `TypedDict`。**キー追加は既存の読み取り側を壊さない**（既存コードは `c["open"]` 等の固定キーアクセスのみ。`port.py` の `Candle` を消費する `cleaning.repair_ohlc_outliers` は OHLC 4 値のみ参照し volume を transparent に保持する。`cleaning.py:57` の `{"time":..., **fixed}` は volume を含まないため、**cleaning を volume 保持に拡張する必要がある**（§2.4 で確定）。
- `DukascopyCandleSource._to_candles`（`dukascopy_source.py:38-52`）は現状 volume を抽出しない。volume 列を抽出して Candle に載せるよう拡張する（dukascopy fetch 結果は `volume` 列を持つ＝`export_jp225_m1.py:83`・`fetch_dukascopy.py:147` で実証）。
- `export_jp225_csv.py` は CSV ヘッダに volume を含めない（`date,open,high,low,close`・`export_jp225_csv.py:84`）。volume 追加後も**この CSV 出力は volume 列を書かない選択でよい**（jp225 日足 dataset は OHLC のみで足りる）。Candle に volume が乗っても CSV writer が無視するだけ＝出力不変（report.json/既存 CSV バイト不変）。

### 2.2 Bar（simulator domain）— 無改変

`Bar`（`bar.py:20`）は `{time, open, high, low, close, volume:float, spread:int}` で **volume と spread を必須**とする。OHLC 不変条件を生成時検証（`bar.py:31-49`）。**本設計で Bar は一切変更しない**（domain 無改変・クリーンアーキ）。

### 2.3 Candle → Bar 写像仕様（adapter・simulator 側）

| Bar フィールド | Candle 由来 | 規則 |
|---|---|---|
| `time` | `Candle["time"]`(UNIX 秒 int) | **そのまま採用**。Bar.time は「numpy.datetime64 または epoch int」を許容（`bar.py:8,26`）＝int でよい。既存 `frame_to_bars` も epoch を直渡し（`ohlc_csv.py:32`）。 |
| `open/high/low/close` | 同名 | `float()` 化（Candle は既に float） |
| `volume` | `Candle["volume"]`（①後） | `float()`。①前の移行期は **`0.0` 既定**で写像（§6 順序依存③）。 |
| `spread` | ─（Candle に無い） | **`0` 既定**。Candle は気配側を構築時に固定し spread を持たない（`port.py:28-29`）。Bar.spread は非負必須（`bar.py:45`）＝0 は合法。spread が必要な戦略（MA_Slope 等）は **MT5 CSV 経路を維持**（§6 注記）。 |

**写像の不変条件保証**: Candle は OHLC 整合を保証しないため、写像時に `Bar(**mapped)` を呼べば Bar の `__post_init__` が `low<=min(o,c)<=max(o,c)<=high` を検証し、違反は `OHLCInvalidError`（domain 例外）を送出する。これは既存 `frame_to_bars`（`_ohlc_frame.py:63`）と同一の検証点であり、**写像経由でも検証が走る**ことを保証する。time 昇順は Candle が保証（`port.py:27`）するが、写像 adapter 側でも `frame_to_bars` 同等の `TimeOrderError` ガードを置く（§3.3）。

### 2.4 cleaning の volume 透過（①付随変更）

`repair_ohlc_outliers`（`cleaning.py:12-58`）は現状 OHLC のみ補正し、出力 dict（`cleaning.py:57`）に volume を含めない。Candle に volume が乗ったら **volume を transparent に保持**するよう 1 行修正:

```python
# cleaning.py:57 を
repaired.append({"time": cd["time"], **fixed})
# → volume を保持して
repaired.append({"time": cd["time"], "volume": cd.get("volume", 0.0), **fixed})
```

> 後方互換: `cd.get("volume", 0.0)` により ①前の volume 無し Candle でも壊れない（TypedDict はランタイム dict のため `.get` 可）。

---

## 3. ポート仕様（signature 確定）

### 3.1 CandleSource.fetch_candles（既存・無改変）

```python
# marketdata/port.py（既存）
@runtime_checkable
class CandleSource(Protocol):
    def fetch_candles(self, start: datetime, end: datetime) -> List[Candle]: ...
```

signature は**変更しない**。戻り値 `Candle` が volume を持つようになるのみ（①）。

### 3.2 TickSource（新設・enabler②）

`fetch_ticks_dukascopy.py`（raw landing）＋ `ingest_ticks.py`（canonical 変換）を marketdata の adapter として port 化する。

```python
# marketdata/port.py（新設）
@runtime_checkable
class TickSource(Protocol):
    """実 tick の取得ポート（境界・抽象）。

    [start, end) の raw tick を timestamp 昇順で返す。canonical 列
    (timestamp/bid/ask/last/volume) への変換は呼び出し側 service（ingest）が担い、
    ポートはベンダ raw（DataFrame）を返す（last=mid 規約等の意味付けを port に焼かない）。
    """
    def fetch_ticks(self, start: datetime, end: datetime) -> "pandas.DataFrame": ...
```

> 設計判断（戻り値型）: tick は列数が多く DataFrame が自然（既存 `fetch_ticks_dukascopy` は DataFrame を返す・`fetch_ticks_dukascopy.py:24`）。**ただし pandas を port シグネチャに出すと marketdata port が pandas へ漏れる**。代替案を §3.2.1 で比較。

#### 3.2.1 TickSource 戻り値型の代替案比較

| 案 | 戻り値 | パフォーマンス | 保守性 | テスト容易性 | 判定 |
|---|---|---|---|---|---|
| **A: DataFrame 直返し** | `pd.DataFrame` | ◎（ゼロコピー・既存と一致） | △（port が pandas へ漏れる） | ○（fixture frame で可） | **採用**（marketdata は pandas を技術ドライバとして許容するインフラ層。simulator usecase と異なり「フレームワーク型を漏らさない」制約は marketdata port には課されていない＝port.py の既存 CandleSource も `datetime` を受ける） |
| B: `List[TypedDict]` tick | `List[RawTick]` | ✕（数百万 tick を dict 化＝RSS 急騰。`tick_parquet` が一括 dict 化を禁ずる思想と矛盾） | ○ | ○ | 棄却（メモリ非有界・memory `perf-minimize-heavy-ops` 違反） |
| C: Iterator[chunk] | `Iterator[pd.DataFrame]` | ◎ | △ | △ | 条件付き（大容量では将来採用余地。初版は A・§9 申し送り） |

**確定**: 案A。marketdata はインフラ境界であり pandas を内部技術ドライバとして許容する（simulator usecase の「pandas を漏らさない」制約はクロスしない＝simulator は TickSource を直接使わず、tick は既存 `ParquetTickRepository` 経由のため）。

```python
# marketdata/dukascopy_source.py（新設 adapter）
class DukascopyTickSource:
    """Dukascopy 実 tick を取得する TickSource 実装（INTERVAL_TICK を隔離）。"""
    def __init__(self, *, instrument: str = JP225,
                 offer_side: Any = dukascopy_python.OFFER_SIDE_BID) -> None: ...
    def fetch_ticks(self, start: datetime, end: datetime) -> pd.DataFrame:
        # fetch_ticks_dukascopy.fetch_range のロジックを移管（日次チャンク・resilient）。
        ...
```

#### 3.2.2 既存 simulator MarketDataPort / TickDataPort をどう満たすか

| simulator port | marketdata で満たす方法 |
|---|---|
| `MarketDataPort.load(source_ref, timeframe, period) -> list[Bar]` | 新設 `MarketDataSourceRepository`（adapter）が `marketdata.CandleSource.fetch_candles` を呼び `Candle→Bar` 写像（§2.3）。**または** 既存 `CsvOHLCRepository` 等を維持（strangler 初期・§6）。 |
| `TickDataPort.load_ticks(symbol, start, end, columns)` | **無改変**。tick の**読み取り**は `ParquetTickRepository`（tick-store）が担当し続ける。marketdata の `TickSource` は**取得（fetch）**のみを担い、読み取り store とは別系統（`fetch_ticks_dukascopy`=取得 / `tick_parquet`=store の既存 2 系統を踏襲）。 |
| `TickStorePort.write_ticks` | **無改変**（`ParquetTickRepository`）。 |

> 重要な分離（接地で確定）: 既存設計は「**取得（fetch_ticks_dukascopy → ingest）**」と「**store 読み書き（tick_parquet）**」が別系統。enabler② は前者（取得）のみを marketdata へ port 化する。後者（store）は simulator adapter に残す（simulator 固有の hive layout・`load_ticks` の半開区間/partition プルーニング仕様は simulator backtest 専用＝marketdata 境界の責務外）。これにより `TickDataPort/TickStorePort` は無改変。

### 3.3 委譲アダプタの class / method 図（simulator 側）

```python
# simulator/adapter/repository/marketdata_source.py（新設）
#   adapter 層 = usecase + domain + marketdata + 技術ドライバ に依存可。
#   usecase は本 adapter を import しない（DIP: usecase は MarketDataPort abc のみ）。

from marketdata import CandleSource                      # 境界ポート
from simulator.domain.bar import Bar
from simulator.domain.exceptions import OHLCInvalidError, TimeOrderError
from simulator.usecase.ports import MarketDataPort

class MarketDataSourceRepository(MarketDataPort):
    """marketdata.CandleSource へ委譲し Candle→domain.Bar 写像する MarketDataPort 実装。

    simulator usecase は本 adapter を知らない（MarketDataPort abc にのみ依存）。
    Candle→Bar 写像（§2.3）と昇順/OHLC 検証（domain.Bar の __post_init__）をここに閉じる。
    """
    def __init__(self, source: CandleSource) -> None:
        self._source = source                            # DI: 構築時に CandleSource 注入

    def load(self, source_ref, timeframe=None, period=None) -> list[Bar]:
        # source_ref は path 系実装との API 対称性のため受けるが未使用（ISSUE-135）。
        # 取得窓は構築時 self._window から解決し fetch_candles へ委譲。
        start, end = self._window  # (start, end) は半開・§10.1 C-2
        candles = self._source.fetch_candles(start, end)
        return _candles_to_bars(candles)                 # 写像＋検証（§2.3）

def _candles_to_bars(candles) -> list[Bar]:
    bars, prev = [], None
    for c in candles:
        bar = Bar(time=c["time"], open=float(c["open"]), high=float(c["high"]),
                  low=float(c["low"]), close=float(c["close"]),
                  volume=float(c.get("volume", 0.0)), spread=0)  # §2.3 写像規則
        if prev is not None and bar.time <= prev:
            raise TimeOrderError("時刻が昇順ではありません", bar_index=len(bars),
                                 context={"prev_time": str(prev), "time": str(bar.time)})
        prev = bar.time
        bars.append(bar)
    return bars
```

**合成点（`simulator/main/__init__.py`）の扱い**: 本 adapter の DI は composition root（`main/__init__.py`）でのみ行う。**既存の `CsvOHLCRepository()` 直 new 分岐（`main/__init__.py:329-374`）は strangler 初期では変更しない**（§6・順序依存②）。`MarketDataSourceRepository` は「marketdata から Candle を取得して backtest する新経路」が必要になった段階で composition に追加する分岐であり、既存 CSV/MT5/parquet 経路の置換は実体移管完了後（§6 ステップ3）。

---

## 4. resample / rollup の marketdata 配置（enabler③）

### 4.1 現状の規則源（単一）と移設方針

`dataset.resample_ohlc` ＋ `TF_DESCRIPTORS`（`marketdata/resample.py:49-85`）が**唯一の resample 規則源**。`TIMEFRAME_RULES`・`SESSION_TFS`・`NON_FLOORABLE_TF` は台帳より導出される値（ISSUE-134）。`rollup_builder` は `dataset.resample_ohlc` を**再利用**（`rollup_builder.py:33,40,228-234,312,447`）し、再実装しない。`rollup_store` は読取専用（`rollup_store.py`）。

**移設設計（再実装ではなく規則源の物理移動）**:

```
marketdata/resample.py   ← resample_ohlc / TIMEFRAME_RULES / is_known_timeframe / _OHLC_AGG を移設
marketdata/rollup.py     ← RollupState / merge_same_period / stream_build / incremental_update を移設
```

`dataset.py` 側は**薄い再エクスポート**へ降格して公開 signature を不変に保つ:

```python
# dataset.py（移設後）
from marketdata.resample import resample_ohlc, TIMEFRAME_RULES, is_known_timeframe  # 再エクスポート
```

> 後方互換: `dataset.resample_ohlc` / `dataset.TIMEFRAME_RULES` を参照する全箇所（`rollup_builder.py:347`, `rollup_store` 経路, controller, テスト `test_dataset_resample_cache.py` 等）は import 名が変わらず動作不変。**移設はバイト一致テスト（§7）で保証**。

### 4.2 既存 indicator_ui resample との統合

統合は「物理移設＋再エクスポート」で達成する。**ロジックの二重化は発生しない**（規則源は 1 つのまま `marketdata.resample` へ移るだけ）。indicator_ui 固有のキャッシュ（`_BASE_CACHE`/`_RESAMPLE_CACHE`/`_ROLLUP_CACHE`・`dataset.py:129-140`, `rollup_store.py:39`）は **indicator_ui 配信固有の最適化**（mtime 検知・torn-read フォールバック）であり marketdata へ移さない（marketdata は純変換サービスに留め、配信キャッシュは利用側 indicator_ui に残す＝関心分離）。

### 4.3 rollup_state.json の扱い

`rollup_state.json`（`marketdata/data/rollups/rollup_state.json`・`rollup_builder.py:52`）は**既に marketdata/data 配下に物理配置済み**。`RollupState.save/load`（`rollup_builder.py:77-90`）が `out_dir` パラメータでパスを受けるため、移設後 `marketdata/data/rollups` を既定 out_dir とする externalize（§5）で配置不変を維持。**state ファイルのフォーマット・パスは無改変**（既存 rollups を再生成しない）。

---

## 5. 銘柄 / 足種の汎用化（enabler④・INTERVALS/JP225 固定の externalize）

### 5.1 現状の固定点（接地）

| 固定 | 箇所 |
|---|---|
| `JP225 = INSTRUMENT_IDX_ASIA_E_N225JAP` | `dukascopy_source.py:24` |
| `INTERVALS`（足種名→定数の固定 dict） | `dukascopy_source.py:27-35` |
| `_ROLLUP_TIMEFRAMES`（5m..1M 固定） | `export_jp225_m1.py:50`, `rollup_builder` 呼出側 |
| `_REF_PREFIX="jp225_m1"`（ファイル名固定） | `rollup_builder.py:51` |
| `_ROLLUP_REFS=("jp225_m1",)` | `dataset.py:79` |

### 5.2 externalize 設計（構築時パラメータへ）

`DukascopyCandleSource` は**既に銘柄/足種/気配側を構築時パラメータ化済み**（`dukascopy_source.py:63-69` の `instrument`/`interval`/`offer_side`）。残る externalize は:

1. **`INTERVALS` の汎用化**: 名前→定数の写像は dukascopy 固有。`INTERVALS` を `dukascopy_source` に残しつつ、**adapter 非依存の足種コード**（`"5m"/"1h"/"1D"` 等＝`TIMEFRAME_RULES` のキー）を一次識別子とし、ベンダ定数への変換は adapter 構築時に閉じる（既存 `INTERVALS[args.interval]` パターン・`prototype_inject_marketdata.py:197` を踏襲）。
2. **rollup の銘柄/TF パラメータ化**: `rollup_builder.stream_build(m1_csv_path, tf_list, out_dir)` は既に tf_list/out_dir をパラメータで受ける（`rollup_builder.py:320`）。`_REF_PREFIX` のみがファイル名にハードコード（`rollup_builder.py:51`）。これを **`ref_prefix` 引数**（既定 `"jp225_m1"`）へ昇格して銘柄汎用化:

```python
# rollup.py（移設後）
def stream_build(m1_csv_path, tf_list, out_dir,
                 ref_prefix: str = "jp225_m1", chunk_rows: int = 500_000) -> RollupState: ...
def _rollup_path(out_dir, tf, ref_prefix="jp225_m1") -> Path:
    return Path(out_dir) / f"{ref_prefix}_{tf}.csv"
```

> 後方互換: `ref_prefix` 既定 `"jp225_m1"` により全既存呼出（`export_jp225_m1.py:483` の `build_rollup_hook` 経由）は不変。新銘柄追加時のみ ref_prefix を渡す。`dataset._ROLLUP_REFS`/`DATASET_WHITELIST` への新銘柄追加は **whitelist パターン**（`dataset.py:34-46`）に従い、新キー追加のみ（既存キー不変）。

3. **enabler④ の最小実装範囲**: 銘柄/足種を構築時パラメータへ昇格するのは「パラメータの**口を開ける**」ことに留める。実際の多銘柄データ生成は別タスク（本書は JP225 既定で全経路バイト不変を保証）。

---

## 6. 移行ステップ（strangler・非破壊）

> 大原則（memory `no-ripple-to-existing-data`）: 既存データへの変更・波及は禁止。新機能は読み取り専用＋新規追加のみ。各ステップで「無改変なもの」「テスト不変条件」を明示する。

### 順序依存（着手前に確定）

- **順序依存①**: enabler①（Candle volume）は cleaning の volume 透過（§2.4）と DukascopyCandleSource の volume 抽出（§2.1）を**同一ステップ**で行う（部分適用すると volume が途中で欠落）。
- **順序依存②**: simulator 既存 repo（csv/parquet/mt5）の「marketdata 委譲ラッパ化」は、**委譲先（marketdata の CSV/MT5/Parquet adapter）が存在して初めて成立**する。現状 marketdata には CSV/MT5/Parquet adapter が無い（Dukascopy のみ）。よって委譲は「marketdata 側 adapter 新設」が先行する。本書は委譲を**最終段**に置く。
- **順序依存③**: `MarketDataSourceRepository` の volume 写像は ①完了後に実 volume を載せる。①前は `volume=0.0` 既定（§2.3）。

### ステップ表

| # | 内容 | 無改変なもの | テスト不変条件 |
|---|---|---|---|
| **Sd** | U2: `marketdata/data/` を `data/marketdata/`（パッケージ外・gitignore）へ移動し、参照を単一定数 `marketdata.paths.DATA_DIR` 経由へ集約（`dataset.py` whitelist・rollup_state・jp225_m1 等） | data の中身（バイト不変・移動のみ）/ コードロジック | 全 dataset/rollup/export テスト緑（新パスで読込）。移動前後でデータファイルのバイト一致 |
| **S0** | enabler①: `Candle` に volume 追加＋`cleaning` volume 透過＋`DukascopyCandleSource._to_candles` volume 抽出 | Bar / simulator 全体 / 既存 CSV 出力（export_jp225_csv は volume 列を書かない） | marketdata 既存テスト緑。`repair_ohlc_outliers` の OHLC 補正結果バイト不変（volume はパススルー追加のみ） |
| **S1** | enabler③: resample/rollup を `marketdata.resample`/`marketdata.rollup` へ移設し `dataset.py` を再エクスポートへ降格 | `dataset` 公開 signature / `TIMEFRAME_RULES` 内容 / `rollup_state.json` パス | `test_dataset_resample_cache.py` / `test_dataset_rollup_routing.py` / `test_rollup_builder.py` / `test_rollup_store.py` 全緑。**ロールアップ CSV バイト一致**（§7 oracle） |
| **S2** | enabler②: `TickSource` 新設＋`DukascopyTickSource`（fetch_ticks_dukascopy 移管）。`ingest_ticks.to_canonical_ticks` は marketdata service へ移設可（任意） | `ParquetTickRepository`（store 読み書き）/ `TickDataPort`/`TickStorePort` / tick-store hive layout | `test_ingest_ticks.py` / `test_tick_parquet.py` 全緑。canonical 変換（last=mid・naive UTC）バイト不変 |
| **S3** | enabler①解消: `export_jp225_m1.py` の dukascopy 直呼びを `DukascopyCandleSource`（volume 付き）委譲へ置換 | jp225_m1.csv の出力列・date 書式・外れ値補正結果 | `test_export_jp225_m1_incremental.py` 全緑。CSV 出力バイト一致 |
| **S4** | enabler④: 銘柄/足種を構築時パラメータへ昇格（`ref_prefix` 引数等）。既定 JP225 で全経路不変 | 全既存呼出（既定値で不変） | 全 tools テスト緑（既定パスはバイト不変） |
| **S5** | simulator strangler: marketdata に CSV/MT5 adapter を新設し、simulator 既存2 repo を marketdata 委譲の薄いラッパへ（**実体移管**）。`MarketDataSourceRepository` で Candle→Bar 写像。`ParquetOHLCRepository` は撤去済み（コミット b62bcc3） | simulator usecase（domain のみ依存・無改変）/ `MarketDataPort` abc / `Bar` / `frame_to_bars` の検証規則 | `test_ohlc_csv_repository.py` / `test_ohlc_mt5_csv.py` / `test_usecase_ports.py` 全緑。**`market_data.load(...)` の Bar 出力が写像前後でバイト一致** |
| **S6** | U1: 孤児 `fetch_dukascopy.py`（消費者不在）を削除し「足取得スクリプト」の選択肢を整理 | 他の取得経路すべて（参照ゼロを再確認してから削除） | 削除後に全テスト緑（参照不在＝回帰なし）。`grep fetch_dukascopy` が 0 件 |

> **Sd・S0〜S4 は indicator_ui/marketdata に閉じる。例外は Sd が `simulator/tools/export_trade_markers.py` の path 定数 1 箇所を `DATA_DIR` 参照へ置換する点のみ**（tools 層・§10.2 H-5）。**simulator の usecase/domain/adapter は Sd〜S4 で無改変**。S5 のみ simulator adapter を触る（usecase は無改変）。S5 は simulator backtest の Bar 出力不変が最重要不変条件（report.json 再現性）。S6（孤児削除）は独立・最後。

---

## 7. テスト設計

### 7.1 既存テストの不変保証（回帰の壁）

| テスト | 保証する不変条件 | ステップ |
|---|---|---|
| marketdata 既存（dukascopy_source/cleaning） | Candle volume 追加で既存 OHLC 補正不変 | S0 |
| `test_dataset_resample_cache.py` / `test_dataset.py` | resample 結果・キャッシュ挙動不変 | S1 |
| `test_rollup_builder.py` / `test_rollup_store.py` / `test_dataset_rollup_routing.py` | ロールアップ CSV バイト一致・state 不変 | S1 |
| `test_ingest_ticks.py` / `test_tick_parquet.py` | canonical 変換・store 読み書き不変 | S2 |
| `test_export_jp225_m1_incremental.py` | jp225_m1.csv 出力バイト一致 | S3 |
| `test_ohlc_csv_repository.py` / `test_ohlc_mt5_csv.py` / `test_usecase_ports.py` | Bar 列出力・検証規則・MarketDataPort 契約不変。`test_ohlc_parquet_repository.py` は撤去済み（コミット b62bcc3） | S5 |

**カバレッジ目標**: 既存テストは**全件緑が S0〜S5 各段の必須通過条件**（カバレッジ低下＝不合格）。

### 7.2 新規テスト

| 種別 | 対象 | ケース |
|---|---|---|
| 単体 | `Candle` volume（S0） | DukascopyCandleSource が volume 抽出／volume 無し raw で 0.0／cleaning が volume を透過保持 |
| 単体 | `_candles_to_bars` 写像（S5） | OHLC 整合 Candle→Bar 成立／不整合 Candle→`OHLCInvalidError`／非昇順→`TimeOrderError`／volume・spread 既定値（0.0/0）／time(int) 直渡し |
| 単体 | `DukascopyTickSource`（S2） | fetch_ticks が timestamp 昇順 DataFrame／空期間で空 frame／日次チャンク連結 |
| 単体 | `marketdata.resample`（S1） | `resample_ohlc` が移設前 `dataset.resample_ohlc` と**同一出力**（同 fixture で diff 0） |
| 統合 | resample/rollup 移設（S1） | `marketdata.rollup.stream_build` 出力 ＝ 移設前 `rollup_builder.stream_build` 出力（CSV バイト一致 oracle） |
| 統合 | `MarketDataSourceRepository`（S5） | fake `CandleSource` 注入→`load` が期待 Bar 列／既存 `CsvOHLCRepository.load` と同 CSV で**同一 Bar 列**（写像経路の等価性） |
| 統合 | export_jp225_m1 委譲（S3） | DukascopyCandleSource 委譲版が直呼び版と同一 CSV（volume 含む）を出力 |

**自動化範囲**: 全単体・統合を CI 自動化（既存 `simulator/tests` / `indicator_ui/.../tests` のランナーへ追加）。E2E（実 Dukascopy fetch）は**手動・非自動**（ネットワーク依存・既存方針＝memory `dukascopy-data-source` のバックテストは公開フィード）。

### 7.3 バイト一致 oracle（移設の核心保証）

S1/S3 は「移設前出力を golden として保存→移設後出力と diff 0」を回帰テストとする（memory `bugfix-pair-with-regression-test`: 移設という間違いを禁止する回帰を 1 本添える）。具体: 移設前に `marketdata/data/rollups/*.csv` のハッシュを採取し、移設後再生成のハッシュ一致をアサート。

---

## 8. 影響範囲と非破壊保証

| 保証対象 | 保証内容 | 根拠 |
|---|---|---|
| simulator usecase 無改変 | usecase は `MarketDataPort` abc にのみ依存し marketdata を import しない。S5 でも usecase 行は 0 変更 | §3.3・DIP |
| report.json / 既存データ無改変 | S5 で `market_data.load` の Bar 出力がバイト一致（§7.1）＝backtest 再現性不変 | §7.3 oracle |
| 既存 CSV/parquet バイト不変 | jp225_daily.csv（volume 列を書かない・§2.1）/ jp225_m1.csv（S3 バイト一致）/ rollups（S1 バイト一致） | §6・§7.3 |
| vendor バージョン不変 | dukascopy_python / pandas / pyarrow の version 変更なし（移設はロジック移動のみ・新規 import なし） | CLAUDE.md 絶対遵守 |
| tick-store 無改変 | `ParquetTickRepository` の hive layout / `load_ticks` 半開区間仕様は不変（enabler② は fetch のみ port 化） | §3.2.2 |
| rollup_state.json 無改変 | パス・フォーマット不変（out_dir externalize の既定値で配置維持） | §4.3 |
| Bar domain 無改変 | Bar 定義に手を入れない（写像は adapter 側） | §2.2 |

**破壊的変更の不在確認**: DROP/一括 DELETE/本番操作/認証無効化/CI 削除/共有モジュール破壊いずれも該当なし。
ファイル削除は孤児 `fetch_dukascopy.py` 1 本のみ（消費者不在・ユーザー承認済・git 復元可＝§9 U1）。
`marketdata/data/` の移動（§9 U2）は中身バイト不変の再配置で、参照は単一定数経由に集約するため破壊的でない。

---

## 9. 未確定 / 別承認事項

| # | 事項 | 状態 | 理由 |
|---|---|---|---|
| U1 | ルート `fetch_dukascopy.py` の削除 | **承認済み・実装範囲（S6 で削除）** | 消費者 `bull_bear_analysis` 不在＝孤児。ユーザー承認済（2026-06-25）。S6 で削除し「足取得スクリプト」の選択肢を1つ減らす。git 履歴に残るため復元可（commit `29bd284` 系統とは別・本体は develop 6395a2d 以前に存在） |
| U2 | `marketdata/data/`（生成物）とコード分離 | **承認済み・実装範囲（準備ステップ Sd で最初に実施）** | data 配下を `marketdata` パッケージ外へ移す。推奨先＝リポジトリ直下 `data/marketdata/`（生成物＝gitignore 対象）。パス解決は単一定数 `marketdata.paths.DATA_DIR`（環境変数 override 可）へ集約し、`dataset.py:30,42-45` の whitelist／`rollup_state.json`／`jp225_m1.csv` 等の参照を当該定数経由へ置換。バイト不変（移動のみ）。**Sd（S0 の前）で実施**し以降の移設は新パス前提とする |
| U3 | Stooq 日足 adapter の要否（週次ボラ戦略向け） | **保留（ポートは対応可能のまま）・ユーザー承認済** | 今回は `StooqCandleSource` を作らない（2026-06-25 承認）。`CandleSource` ポートは差替可能なため、週次ボラ戦略を実データで回す段で別小タスクとして追加すればよい（利用側無改変）。本書はポート口の存在のみ保証する |
| U4 | TickSource の Iterator 化（大容量 chunk streaming・§3.2.1 案C） | 申し送り | 初版は DataFrame 直返し（案A）。tick 量が RSS 上限を超えたら案C へ。memory `perf-minimize-heavy-ops` に従い計測で判断 |
| U5 | `ingest_ticks.to_canonical_ticks` の marketdata service 移設 | 任意（S2 で選択） | 移設しても simulator 無改変だが、last=mid 規約が tick-store 契約に密結合。移設可否は S2 着手時に確定 |
| U6 | `MarketDataSourceRepository` の `source_ref→(start,end)` 解決仕様 | **ISSUE-135 で確定** | 取得窓は構築時パラメータ `window` で隔離・`load` の `source_ref` は path 系実装との対称性のため受けるが未使用（usecase IF は不変）。composition root が ea_name 別に振り分け（`main/__init__.py:374`） |

---

## 付録A: クリーンアーキ依存方向チェックリスト（DoD）

- [x] simulator usecase → domain のみ（marketdata 非 import）— §3.3
- [x] simulator adapter → usecase + domain + marketdata + 技術ドライバ — §3.3
- [x] marketdata port → 標準ライブラリのみ（pandas は service/adapter へ隔離・CandleSource は datetime のみ）— §3.1
- [x] marketdata adapter → ベンダライブラリ隔離（dukascopy_python は dukascopy_source に限定）— §1.1
- [x] indicator_ui api/tools → marketdata 直依存可（adapter 相当層）— §1.2
- [x] domain（Bar）無改変 — §2.2
- [x] 全 enabler が新規追加 or パラメータ口開けに留まり既存破壊なし — §6

## 付録B: 確定 signature 一覧（実装者向け抜粋）

```python
# marketdata/port.py
class Candle(TypedDict): time:int; open:float; high:float; low:float; close:float; volume:float  # ①
class CandleSource(Protocol): def fetch_candles(self, start, end) -> List[Candle]: ...            # 不変
class TickSource(Protocol):   def fetch_ticks(self, start, end) -> pd.DataFrame: ...               # ②新設

# marketdata/dukascopy_source.py
class DukascopyTickSource: __init__(*, instrument=JP225, offer_side=BID); fetch_ticks(start,end)->DataFrame  # ②

# marketdata/resample.py（dataset から移設・③）
def resample_ohlc(df, rule) -> DataFrame
TIMEFRAME_RULES: dict[str, str|None]
def is_known_timeframe(tf) -> bool

# marketdata/rollup.py（rollup_builder から移設・③④）
@dataclass class RollupState: last_processed_ts: datetime; save(out_dir); load(out_dir)
def stream_build(m1_csv_path, tf_list, out_dir, ref_prefix="jp225_m1", chunk_rows=500_000) -> RollupState  # ④
def incremental_update(m1_csv_path, state, tf_list, out_dir, ref_prefix="jp225_m1") -> RollupState         # ④

# simulator/adapter/repository/marketdata_source.py（新設・委譲＋写像）
class MarketDataSourceRepository(MarketDataPort):
    __init__(self, source: CandleSource, *, window: tuple[datetime, datetime])  # ISSUE-135: 取得窓（半開 [start,end)）を構築時パラメータ化
    def load(self, source_ref, timeframe=None, period=None) -> list[Bar]   # Candle→Bar 写像（§2.3）
```

---

## 10. レビュー反映（確定化・spec-reviewer 2026-06-25・本節が各章に優先）

spec-reviewer 判定＝**修正後 GO**（Blocker 0 / Critical 2 / High 5）。以下を binding 決定として確定する。実装はこの §10 を最優先で参照する。

### 10.1 Critical（着手前必須）
- **C-1 `DATA_DIR` 単一基点**: 新設 `marketdata/paths.py` に
  `DATA_DIR = Path(os.environ.get("MARKETDATA_DATA_DIR", _REPO_ROOT / "data" / "marketdata"))`、
  `_REPO_ROOT = Path(__file__).resolve().parents[1]`（marketdata 直上＝唯一の基点）。
  env が指す path 不在は `FileNotFoundError` で fail-fast（fallback 禁止）。
  現状の多基点（`dataset.py` parents[5] / `export_jp225_m1.py` parents[3] / `export_trade_markers.py` parents[2]）を**全て DATA_DIR 経由へ置換**。Sd 完了条件 = `rg "marketdata/data" --type py` が定義行を除き 0 件、かつ移動前後で全データファイルの SHA-256 一致。
- **C-2 `source_ref` 対称化規約（S5・ISSUE-135 で確定改定）**: `MarketDataPort.load(source_ref, ...)` の
  `source_ref` は**全実装対称の path 様参照**に統一（コミット c39799d）。`MarketDataSourceRepository` 固有の
  取得窓 `tuple[datetime, datetime]`（半開 [start,end)）は**構築時パラメータ `window` へ隔離**し、`load` 内で
  `source_ref` は未使用。composition root（`main/__init__.py`）は ea_name 別に repository を構築するのみで、
  旧規約にあった `isinstance` による `load_source` 作り分けは撤去済み（全実装へ `data_path` を統一的に渡す）。
  **usecase IF（`RunBacktestRequest.bars`）は不変**。

### 10.2 High
- **H-1 Sd 対象テスト集合の明示**: Sd 完了の「緑」= `test_dataset.py` / `test_dataset_resample_cache.py` /
  `test_dataset_rollup_routing.py` / `test_rollup_builder.py` / `test_rollup_store.py` /
  `test_export_jp225_m1_incremental.py`（path 解決を読む全件）。
- **H-2 tick raw の形**: `TickSource.fetch_ticks(start,end)` は timestamp を**列**に持つ DataFrame
  （`reset_index` 済・列名 `timestamp`）を返し `ingest_ticks.RAW_COLUMNS` 契約へ直接適合。
- **H-3 tick offer_side 撤去**: `DukascopyTickSource` は bidPrice/askPrice **両列を常に返す**
  （`offer_side` 単一指定を削除）。last=mid=(bid+ask)/2 算出を保全。
- **H-4 spread=0 の適用範囲**: `_candles_to_bars` の `spread=0` 固定は **spread 非依存戦略のみ**
  （既定 TC・WeeklyVolBand）。spread 依存戦略（MA_Slope / MA_Slope_Pending / StopEntryProbe＝
  `main/__init__.py:329-346`）は委譲対象外とし `Mt5CsvOHLCRepository` を維持（S5 委譲対象 = comma 形式 2 戦略に限定）。
- **H-5 Sd スコープ漏れの補完**: 集約対象に `rollup_store.py:30 _ROLLUPS_DIR` と
  `simulator/tools/export_trade_markers.py:34` を**追加**。後者により §6 line 351 を訂正＝
  「Sd は export_trade_markers の path 定数 1 箇所を DATA_DIR 参照へ置換する（simulator の
  usecase/domain/adapter は無改変）」。

### 10.3 Medium（実装時遵守）
- **M-1 INTERVALS 命名変換**: enabler④の一次識別子（`"5m"/"1h"`）→ `INTERVALS`（`"min_5"/"hour_1"`）
  変換表を adapter 内に新設（呼出面は TIMEFRAME_RULES キー系へ統一）。
- **M-2 oracle 採取時点**: golden は **Sd 完了後の新 path** で採取（Sd→S1 順序固定）。移設前コードで
  生成した CSV を物理 commit（`tests/golden/rollups/`）し移設後出力と `filecmp.cmp(shallow=False)` 判定。
- **M-3 ref_prefix 伝播**: `_rollup_path` と `_RollupWriter.__init__` の**両方**に `ref_prefix`（既定 `"jp225_m1"`）。
- **M-4 失敗定義**: oracle 不一致＝即 fail（許容なし・float repr 差は物理 golden で排除）。
  Sd 部分失敗は「移動後 `rg` で旧 path 0 件＋SHA 一致」で検出。DATA_DIR 不正は fail-fast。

### 10.4 確定した移行順序
**Sd（data 分離＋DATA_DIR）→ S0（volume）→ S1（resample/rollup 移設）→ S2（TickSource）→
S3（export_jp225_m1 委譲）→ S4（汎用化）→ S5（simulator strangler・C-2 規約）→ S6（孤児削除）**。
Critical（C-1=Sd 内、C-2=S5 前）は各該当ステップ着手前に確定済みであること。
