# 週次ボラティリティ・バンド戦略 内部（詳細）設計書

## 1. 文書情報

- 作成日：2026-06-25
- バージョン：v1.0.0
- 作成者：system-internal-design エージェント
- 対象システム種別：バッチ／API 単体（オフライン分析・バックテスト・検証パイプライン。UI なし）
- 一次情報（入力・全 Read 済）：
  - `/workspaces/app/.doc/WEEKLY_VOL_BAND_SPEC_v1_0.md`（仕様 v1.0 確定版）
  - `/workspaces/app/.doc/WEEKLY_VOL_BAND_BASIC_DESIGN.md`（基本設計 v0.1.0）
  - `/workspaces/app/ISSUE.md` ISSUE-027（金曜引け＝週単位セグメント実行・RESOLVED）
- 既存実装の手本（無改変対象・本書で signature を引用）：
  - `simulator/usecase/run_is_oos.py`（run_segment コールバック DIP・slice_is_bars）
  - `simulator/usecase/compute_stats.py`（usecase で numpy 使用の先例 L36）
  - `simulator/usecase/run_backtest.py`（L588 OCO・L610-656 market 約定・L722-765 SL/TP 監視・L888-910 end_of_test 清算）
  - `simulator/adapter/strategy/stop_entry_probe.py`（StrategyPort 実装・SL/TP 付き Order）
  - `simulator/domain/order.py`（Order VO）/ `simulator/domain/bar.py`（frozen VO 様式・numpy のみ可）/ `simulator/domain/trade_record.py`（exit_reason 語彙）
  - `simulator/usecase/ports.py`（StrategyPort L97-121）/ `simulator/usecase/optimize_ports.py`（Protocol を usecase に置く先例）
  - `simulator/report_ui/usecase/derive.py`（epoch int→曜日 UTC 規約：`datetime.fromtimestamp(int_ts, tz=timezone.utc).weekday()`・Mon=0）
- 設計レベル：内部設計（クラス／関数 signature・数式擬似コード・look-ahead 依存グラフ・物理データ・テスト計画）。誰が実装しても同一結果になる決定論水準。

### 1.1 確定事項（メイン会話が一意確定・D1〜D5）の継承

本書は依頼の確定事項 D1〜D5 を所与とし、再議論しない。各章で D1〜D5 をどの signature／数式へ落としたかを明示する。上流前提の実証検証は本書末尾「自己レビュー」「上流入力前提検証」で記録する。

---

## 2. 全体アーキテクチャと依存方向（クリーンアーキ遵守）

### 2.1 レイヤーと新規ファイル配置（D5・既存無改変）

```
tools（Composition Root・pandas/IO 許容）
  simulator/tools/run_weekly_vol_band_cli.py          [新規] バッチ推定/検証エントリ
        │ DI（具象 Port 注入）
        ▼
usecase（domain のみ依存・pandas/numpy を import しない／numpy は Port 実装へ委譲）
  simulator/usecase/estimate_weekly_band.py           [新規] UC-WV1 週末バッチ推定
  simulator/usecase/run_weekly_segments.py            [新規] UC-WV2 週単位セグメント orchestration
  simulator/usecase/validate_strategy.py              [新規] UC-WV3 検証 S1〜S6
  simulator/usecase/vol_band_ports.py                 [新規] VarianceEstimatorPort / VolBandRepositoryPort
  simulator/usecase/validation_ports.py               [新規] BacktestTestPort / SpaTestPort
        │ Port 抽象（Protocol）に依存
        ▼ 具象（numpy・pandas は repository 境界のみ）
adapter
  simulator/adapter/strategy/weekly_vol_band.py       [新規] StrategyPort 実装（WV2 戦略）
  simulator/adapter/indicator/gk_har_estimator.py     [新規] VarianceEstimatorPort 実装（GK/HAR/OLS/NW）
  simulator/adapter/repository/vol_band_parquet.py    [新規] VolBandRepositoryPort 実装（永続）
  simulator/adapter/validation/var_backtests.py       [新規] BacktestTestPort 実装（Kupiec/Christoffersen）
  simulator/adapter/validation/spa.py                 [新規] SpaTestPort 実装（Hansen SPA・PW block）
domain（numpy のみ可・frozen・振る舞い最小）
  simulator/domain/volatility_band.py                 [新規] VolatilityBand（S/T/N）
  simulator/domain/variance_forecast.py               [新規] VarianceForecast（σ̂⁺/σ̂⁻/σ̂ᵗᵒᵗᵃˡ）
  simulator/domain/trading_week.py                    [新規] TradingWeek（週境界・取引日集合）
  simulator/domain/oco_order_pair.py                  [新規] OcoOrderPair（Order 内包）
  simulator/domain/backtest_test_result.py            [新規] BacktestTestResult（採否 VO）
framework
  simulator/framework/config_loader.py                [拡張] VolEstimationParams/ValidationParams 追加（既存無改変部は不触）
main
  simulator/main.py（build_interactor）               [拡張] WeeklyVolBand_EA 分岐＋バッチ/検証エントリ
既存（無改変・部品再利用）
  usecase/run_is_oos.py（slice_is_bars 再利用）/ run_backtest.py（RunBacktestInteractor.execute）
  usecase/ports.py（StrategyPort）/ domain/{bar,order,trade_record}
```

### 2.2 依存方向の不変条件（CI/grep で担保）

| 規律 | 内容 | 検証手段 |
|---|---|---|
| DI-1 | usecase は domain と新規 Port 抽象のみ import。`import pandas` / `import numpy` / `simulator.adapter` / `simulator.framework` / `simulator.main` を含まない | `grep -nE "import (pandas\|numpy)\|from simulator\.(adapter\|framework\|main)" simulator/usecase/{estimate_weekly_band,run_weekly_segments,validate_strategy,vol_band_ports,validation_ports}.py` が 0 件 |
| DI-2 | domain は numpy のみ依存可（pandas/adapter 禁止） | 同 grep を domain/ に適用し pandas 0 件 |
| DI-3 | numpy は adapter の Port 実装に局所化（D5・基本設計 §3.4 案W2-A） | adapter/{indicator,validation}/ のみ numpy import |
| DI-4 | pandas は tools と adapter/repository の入出力境界のみ | 上記以外 0 件 |
| DI-5 | 既存ファイル差分 0 行（config_loader.py・main.py は追記のみ・既存ブロック不触） | `git diff --stat` で既存行の変更 0（追加のみ） |

> 注：基本設計 §3.4 は「usecase numpy も既存規約上は可（compute_stats.py 先例）」と詳細設計余地を残すが、本書は **D5 の配置（adapter 局所化）を一意確定**とする。実装者の選択余地は閉じる（決定論水準の要請）。

---

## 3. ドメイン層 詳細設計（VO・frozen・既存 bar.py 様式）

すべて `@dataclass(frozen=True)`・`__post_init__` で不変条件検証・振る舞いは導出のみ（外部 IO なし）。時刻は `numpy.datetime64 | int`（既存規約・`pd.Timestamp` 禁止）。

### 3.1 `domain/trading_week.py` — TradingWeek

```python
@dataclass(frozen=True)
class TradingWeek:
    week_id: str                 # ISO 週 "YYYY-Www"（例 "2024-W07"）。集計・参照キー
    first_trading_time: int      # その週の最初の取引日寄りバーの epoch int（= 週初寄り基準）
    last_trading_time: int       # その週の最後の取引日引けバーの epoch int（= 金曜引け基準）
    trading_times: tuple[int, ...]   # 週内の全取引日（昇順・重複なし）。len>=1
    event_flag: bool = False     # 日銀/FOMC/SQ/主要指標（D4・TBD-6 供給。既定 False）

    def __post_init__(self) -> None:
        if len(self.trading_times) < 1:
            raise ValueError("TradingWeek: trading_times は 1 件以上")
        if self.first_trading_time != self.trading_times[0]:
            raise ValueError("first_trading_time は trading_times[0] と一致")
        if self.last_trading_time != self.trading_times[-1]:
            raise ValueError("last_trading_time は trading_times[-1] と一致")
        if list(self.trading_times) != sorted(set(self.trading_times)):
            raise ValueError("trading_times は昇順・重複なし")
```

**week_id 規約（D2・決定論）**：`week_id_of(ts: int) -> str` を `domain/trading_week.py` の module-level 純関数として定義。
```python
def week_id_of(ts: int) -> str:
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)   # report_ui/derive.py 規約踏襲
    iso_year, iso_week, _ = dt.isocalendar()                 # ISO 週（D2）
    return f"{iso_year:04d}-W{iso_week:02d}"

def weekday_of(ts: int) -> int:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).weekday()   # Mon=0（D2）

def same_trading_day(ts_a: int, ts_b: int) -> bool:
    da = datetime.fromtimestamp(int(ts_a), tz=timezone.utc).date()
    db = datetime.fromtimestamp(int(ts_b), tz=timezone.utc).date()
    return da == db
```

### 3.2 `domain/variance_forecast.py` — VarianceForecast

```python
@dataclass(frozen=True)
class VarianceForecast:
    week_id: str
    sigma_plus: float | None     # σ̂⁺_w（上方半実現ボラ予測・推定不可時 None）
    sigma_minus: float | None    # σ̂⁻_w（下方半実現ボラ予測・推定不可時 None）
    sigma_total_prev: float | None  # σ̂ᵗᵒᵗᵃˡ_w = 前週実現 GK 週次ボラ（E1 閾値用・D2・look-ahead 安全側）
    estimable: bool              # 推定可否（False=ノートレード確定）

    def __post_init__(self) -> None:
        if self.estimable:
            for nm, v in (("sigma_plus", self.sigma_plus), ("sigma_minus", self.sigma_minus)):
                if v is None or not math.isfinite(v) or v <= 0.0:
                    raise ValueError(f"estimable=True では {nm} は有限正数")
        # estimable=False のとき sigma_* は None 可（ノートレード）

    @staticmethod
    def no_trade(week_id: str, sigma_total_prev: float | None = None) -> "VarianceForecast":
        return VarianceForecast(week_id, None, None, sigma_total_prev, estimable=False)
```

> 設計判断：`σ̂ᵗᵒᵗᵃˡ_prev` は **E1 閾値専用の前週確定実現値**（D2）であり予測値ではない。estimable=False（ノートレード）でも E1 判定に使うため sigma_total_prev は独立に保持し検証対象外とする。

### 3.3 `domain/volatility_band.py` — VolatilityBand

D2 の式を一意実装。z(p_tp) は定数表（D2・仕様 §2.5）。

```python
_Z_TP = {0.40: 0.842, 0.50: 0.674, 0.60: 0.524, 0.70: 0.385}   # 仕様 §2.5（D2）
SL_Z = 1.96                                                     # ストップ固定（D2）

@dataclass(frozen=True)
class VolatilityBand:
    week_id: str
    O: float          # 週セグメント先頭バーの open（D2）
    S: float          # ストップ
    T: float          # 利確
    N: float          # 数量
    p_tp: float

    def __post_init__(self) -> None:
        if not (self.O - self.S > 0.0):
            raise ValueError("不変条件 O-S>0 違反")
        if not (self.S > 0.0):
            raise ValueError("不変条件 S>0 違反")
        if not (self.T > self.O):
            raise ValueError("不変条件 T>O 違反")

    @staticmethod
    def from_forecast(week_id, O, sigma_minus, sigma_plus, p_tp, f_risk, capital) -> "VolatilityBand":
        if p_tp not in _Z_TP:
            raise ValueError(f"p_tp={p_tp} は探索グリッド {sorted(_Z_TP)} 外")
        z = _Z_TP[p_tp]
        S = O * math.exp(-SL_Z * sigma_minus)          # S=O·exp(−1.96·σ̂⁻)
        T = O * math.exp(z * sigma_plus)               # T=O·exp(z(p_tp)·σ̂⁺)
        N = f_risk * capital / (O - S)                 # N=f_risk·Capital/(O−S)
        return VolatilityBand(week_id, O, S, T, N, p_tp)
```

**数値例自己検証（仕様 §数値例・NFR-D1）**：O=39000, σ̂⁻=0.020, σ̂⁺=0.025, p_tp=0.50, Capital=1,000,000, f_risk=0.01 →
S=39000·exp(−0.0392)=37,501（round 0）, T=39000·exp(0.674·0.025=0.01685)=39,663, N=0.01·1e6/1499=6.67。**校正テストで assert（§9.1）**。

> 浮動小数演算順序固定（NFR-D1）：`exp` の引数を `(-SL_Z * sigma_minus)` の順で評価し、丸めは VO 内では行わず（生値保持）、ログ出力時のみ round する（§7.2）。round 位置を一意化する。

### 3.4 `domain/oco_order_pair.py` — OcoOrderPair

D1：戦略はセグメント先頭バーで **market ロング 1 件を sl=S / tp=T 付き**で発注。OCO は engine の SL/TP 監視（run_backtest.py L722-765）と end_of_test（L888-910）で実現する。よって OcoOrderPair は「market エントリ Order（sl=S, tp=T 内包）」を表す VO とし、別個の stop/limit 子注文は **持たない**（engine の market+SL/TP 経路で OCO・ストップ優先・金曜引けを充足する＝ISSUE-027 の非破壊解決）。

```python
@dataclass(frozen=True)
class OcoOrderPair:
    entry: Order              # kind="market", side="buy", volume=N, price=None, sl=S, tp=T
    band: VolatilityBand

    def __post_init__(self) -> None:
        if self.entry.kind != "market" or self.entry.side != "buy":
            raise ValueError("OcoOrderPair.entry は market buy のみ（ロング専用・D1）")
        if self.entry.sl is None or self.entry.tp is None:
            raise ValueError("OcoOrderPair.entry は sl/tp 必須（S/T 監視）")

    def as_orders(self) -> list[Order]:
        return [self.entry]   # StrategyPort.on_new_bar 戻り値へ展開
```

> 設計根拠（ISSUE-027・D1）：エンジンは「market 約定（bar open・run_backtest.py L640）→ 後続ティックで SL/TP 監視（L742 check_sltp_hit_at_tick・sltp_tie="sl"）→ 未到達なら end_of_test 清算（L897 close_price_for(side, bid=close)）」を提供する。これがそのまま「週初ロング→週内 S/T 先着決済（同一バー両到達=SL 優先）→金曜引け強制手仕舞い」に一致する。よって stop/limit の明示子注文は不要。

### 3.5 `domain/backtest_test_result.py` — BacktestTestResult

```python
class Verdict(str, Enum):
    ADOPT = "adopt"                       # 採用（S6 全充足）
    REJECT_STRATEGY = "reject_strategy"   # 戦略棄却（SPA p>=0.05・S4）
    NOT_ADOPT = "not_adopt"               # 不採用（S6 一部不充足）
    INSUFFICIENT_SAMPLE = "insufficient_sample"  # 第3結果（D4・サンプル下限未達）

@dataclass(frozen=True)
class BacktestTestResult:
    verdict: Verdict
    spa_p: float | None                   # SPA p 値（S3）
    selected_e: str | None                # 選択候補 e（"E0" | "E1(0.5)" 等）
    selected_p_tp: float | None
    best_f_k: float | None                # 最良 f_k（棄却時も記録・DoD）
    kupiec_p: float | None                # OOS Kupiec p（S6-b）
    christoffersen_p: float | None        # OOS Christoffersen 独立性 p（S6-c）
    tp_calibration_diff: float | None     # |T到達率 − p_tp|（NFR-Q3・±5%pt）
    oos_mean_weekly_net_return: float | None  # OOS 週次純リターン平均（S6-a）
    oos_weeks: int                        # OOS 検証週数（サンプル下限判定）
    oos_stop_hits: int                    # OOS ストップ到達回数（サンプル下限判定）
```

---

## 4. usecase 層 詳細設計（Port 抽象＋3 UC）

### 4.1 Port 抽象（D5）

#### `usecase/vol_band_ports.py`

```python
@runtime_checkable
class VarianceEstimatorPort(Protocol):
    def forecast(
        self,
        rs_plus_series: Sequence[float],   # 週次 RS⁺ の昇順系列（target 週の手前まで）
        rs_minus_series: Sequence[float],  # 週次 RS⁻ の昇順系列
        *,
        window: int = 260,                 # 推定窓（D2）
        nw_lag: int = 4,                   # Newey-West lag（D2）
    ) -> tuple[float | None, float | None]:
        """log-semivariance-HAR で翌週 (σ̂⁺, σ̂⁻) を予測。
        利用可能週数<window・OLS 特異・NaN/Inf → (None, None)（D4・ノートレード）。"""
        ...

@runtime_checkable
class VolBandRepositoryPort(Protocol):
    def save(self, forecast: VarianceForecast) -> None: ...
    def save_all(self, forecasts: Sequence[VarianceForecast]) -> None: ...
    def get(self, week_id: str) -> "VarianceForecast | None": ...
    def all_week_ids(self) -> tuple[str, ...]: ...
```

#### `usecase/validation_ports.py`

```python
@runtime_checkable
class BacktestTestPort(Protocol):
    def kupiec(self, hit_series: Sequence[int], alpha: float = 0.05) -> float:
        """ストップ被覆 POF 尤度比検定 → χ²(1) p 値。hit∈{0,1}（1=ストップ到達週）。"""
        ...
    def christoffersen_independence(self, hit_series: Sequence[int]) -> float:
        """例外の独立性（マルコフ1次）尤度比検定 → χ²(1) p 値。"""
        ...

@runtime_checkable
class SpaTestPort(Protocol):
    def spa_pvalue(
        self,
        f_matrix: Sequence[Sequence[float]],  # 形状 [n_weeks][n_candidates]（週×候補の週次純リターン率）
        *,
        seed: int,
        B: int = 5000,
    ) -> float:
        """Hansen(2005) SPA_c consistent p 値（定常ブート・PW 自動ブロック長・再センタリング・D3）。"""
        ...
```

> Protocol 採用根拠：既存 `optimize_ports.py` が Protocol を usecase に置く先例（無改変対象）。`vol_band_ports`/`validation_ports` は同様式。

### 4.2 UC-WV1 `usecase/estimate_weekly_band.py`（週末バッチ推定）

純手続き：RS 週次集計（純 Python・numpy 不使用）→ VarianceEstimatorPort で予測 → VarianceForecast 構築 → Repository 保存。

```python
@dataclass
class EstimateWeeklyBandRequest:
    five_min_bars: Sequence[Bar]    # 日中5分OHLC（昇順・epoch int 時刻）
    daily_bars: Sequence[Bar]       # 日足OHLC（昇順・GK 週次・前週実現ボラ用）
    window: int = 260
    nw_lag: int = 4

@dataclass
class EstimateWeeklyBandResult:
    forecasts: list[VarianceForecast]   # 全週（推定不可週は estimable=False）

def estimate_weekly_band(
    *, request, estimator: VarianceEstimatorPort, repo: VolBandRepositoryPort
) -> EstimateWeeklyBandResult:
    # 1) 5分→週次 RS⁺/RS⁻ 集計（純関数 aggregate_weekly_rs）
    weekly_rs = aggregate_weekly_rs(request.five_min_bars)   # dict[week_id] -> (rs_plus, rs_minus)
    # 2) 日足→週次 GK 実現ボラ（純関数 aggregate_weekly_gk）
    weekly_gk = aggregate_weekly_gk(request.daily_bars)       # dict[week_id] -> sigma_total_realized
    week_ids = sorted(weekly_rs)                              # 決定論順序
    forecasts = []
    for i, wid in enumerate(week_ids):
        prev_gk = weekly_gk.get(week_ids[i-1]) if i >= 1 else None   # σ̂ᵗᵒᵗᵃˡ_w=前週実現（D2）
        hist_plus  = [weekly_rs[w][0] for w in week_ids[:i]]   # target 週の手前まで（look-ahead 排除）
        hist_minus = [weekly_rs[w][1] for w in week_ids[:i]]
        if i < request.window:                                # 窓<260（D4・ウォームアップ）
            fc = VarianceForecast.no_trade(wid, prev_gk)
        else:
            sp, sm = estimator.forecast(hist_plus, hist_minus,
                                        window=request.window, nw_lag=request.nw_lag)
            if sp is None or sm is None:                      # 算出不可（D4）
                fc = VarianceForecast.no_trade(wid, prev_gk)
            else:
                fc = VarianceForecast(wid, sp, sm, prev_gk, estimable=True)
        forecasts.append(fc)
    repo.save_all(forecasts)
    return EstimateWeeklyBandResult(forecasts)
```

#### 純関数（usecase・numpy 不使用・D2 の数式擬似コード）

**`aggregate_weekly_rs(five_min_bars) -> dict[str, tuple[float, float]]`**（FR-WV-01・D2）
```
for each pair of adjacent 5-min bars (b_{t-1}, b_t) in time order:
    # 同一立会日内の隣接5分のみ（D2：昼休み・オーバーナイト跨ぎ除外）
    if not same_trading_day(b_{t-1}.time, b_t.time):  continue
    if (b_t.time - b_{t-1}.time) != 300:              continue   # 隣接5分=300秒のみ（欠落跨ぎ除外）
    if b_{t-1}.close <= 0 or b_t.close <= 0:          continue   # log(0) 回避（D4）
    r = log(b_t.close / b_{t-1}.close)
    wid = week_id_of(b_t.time)
    if r > 0:  rs_plus[wid]  += r*r          # RS⁺=Σr²·1[r>0]（D2・週次集計）
    elif r < 0: rs_minus[wid] += r*r         # RS⁻=Σr²·1[r<0]
    # r==0 はどちらにも寄与しない
# 立会内最小本数未達の週は欠損週として除外（D4：呼出側でノートレード扱い）
```
> 5分本数の週次最小本数（MIN_BARS_PER_WEEK）は config（ValidationParams）注入。未達週は dict から除外し UC が estimable=False とする（D4・無音禁止でログ）。

**`aggregate_weekly_gk(daily_bars) -> dict[str, float]`**（FR-WV-02・GK 週次集計）
```
# Garman-Klass 日次分散: gk_d = 0.5*(ln(H/L))² − (2ln2 − 1)*(ln(C/O))²
for each daily bar d:
    if min(d.open,d.high,d.low,d.close) <= 0:  skip (D4 NaN 回避・無音禁止ログ)
    gk_d = 0.5*(log(d.high/d.low))**2 - (2*log(2)-1)*(log(d.close/d.open))**2
    gk_d = max(gk_d, 0.0)                       # 数値誤差で負になり得るため床 0
    week_sum[week_id_of(d.time)] += gk_d
# 週次 GK ボラ = sqrt(週内 gk_d 合計)（週次集計＝週内日次分散の和の平方根）
sigma_total[wid] = sqrt(week_sum[wid])
```

> look-ahead 排除（NFR-D4）：RS/GK は確定 OHLC のみで計算。UC は target 週 i の予測に `week_ids[:i]`（手前のみ）を渡す。σ̂ᵗᵒᵗᵃˡ_w は前週（i-1）実現値のみ。§6 依存グラフで保証。

### 4.3 UC-WV2 `usecase/run_weekly_segments.py`（週単位セグメント orchestration・D1）

run_is_oos と同型の `run_segment` コールバック DIP。各週セグメントの bars=その週の `first_trading_time` 寄り〜`last_trading_time` 引け。

```python
SegmentRunner = Callable[[Sequence[Bar], str, VarianceForecast], Any]
# 引数: (week_bars, week_id, forecast) / 戻り値: BacktestResult（その週セグメント）

@dataclass
class RunWeeklySegmentsRequest:
    full_bars: Sequence[Bar]          # 執行対象バー（5分 or 日足。週内 S/T 監視粒度に合わせ 5分推奨）
    e_rule: str                       # 検証で確定の e（"E0" | "E1(0.5)".."E1(2.0)"）
    p_tp: float                       # 検証で確定の p_tp
    capital: float
    f_risk: float = 0.01

@dataclass
class WeeklySegmentOutcome:
    week_id: str
    log: WeeklyLogRecord              # §7.2 14項目
    stats: Any                        # その週セグメント BacktestStats

def split_into_weeks(full_bars) -> list[TradingWeek]:
    """full_bars を week_id ごとに分割し TradingWeek 群を構築（純関数・決定論）。"""

def slice_week_bars(full_bars, week: TradingWeek) -> list[Bar]:
    """first_trading_time<=bar.time<=last_trading_time の bars（その週セグメント）。"""

def run_weekly_segments(
    *, request, repo: VolBandRepositoryPort, run_segment: SegmentRunner,
) -> list[WeeklySegmentOutcome]:
    weeks = split_into_weeks(request.full_bars)
    prev_week_close = None                       # 前週 close-to-close（E1 用）
    outcomes = []
    for wk in weeks:
        fc = repo.get(wk.week_id)
        if fc is None or not fc.estimable:       # ノートレード（D4・無音禁止ログ）
            outcomes.append(_no_trade_outcome(wk, reason="not_estimable"))
            prev_week_close = _week_close(request.full_bars, wk)
            continue
        # エントリ規則 e 適用（前週リターンに）
        if not _entry_rule_true(request.e_rule, prev_week_close, wk, fc):
            outcomes.append(_no_trade_outcome(wk, reason="entry_rule_false"))
            prev_week_close = _week_close(request.full_bars, wk)
            continue
        week_bars = slice_week_bars(request.full_bars, wk)
        stats = run_segment(week_bars, wk.week_id, fc)   # ← tools が build_interactor で注入
        outcomes.append(_outcome_from_stats(wk, fc, stats))
        prev_week_close = _week_close(request.full_bars, wk)
    return outcomes
```

**エントリ規則 e（FR-WV-07・D2）**
```python
def _entry_rule_true(e_rule, prev_week_close, wk, fc) -> bool:
    if e_rule == "E0":
        return True                                    # 無条件
    theta = _parse_e1_theta(e_rule)                    # "E1(1.0)"->1.0  ∈{0.5,1.0,1.5,2.0}
    if prev_week_close is None or fc.sigma_total_prev is None:
        return False                                   # 前週確定値なし→不発（look-ahead 安全側・D4）
    O = wk_open(wk)                                     # 当週セグメント先頭 open
    r_prev = log(this_week_prev_close_to_close)        # 前週 close-to-close リターン（下記）
    # E1(θ): 前週 close-to-close ≤ −θ·σ̂ᵗᵒᵗᵃˡ_w（D2）
    return r_prev <= -theta * fc.sigma_total_prev
```
> 「前週 close-to-close リターン」は `log(前週末 close / 前々週末 close)` の確定値。`_week_close` で各週末 close を保持し UC が連鎖参照する（当週以降の値を一切使わない＝look-ahead 排除）。

**WV2 が StrategyPort（adapter）と engine をどう使うか（D1・ISSUE-027）**：`run_segment` は tools が `build_interactor` で組み立てる薄いラッパで、内部は既存 `RunBacktestInteractor.execute(RunBacktestRequest)` を **週セグメント bars** で 1 回呼ぶ。config は `pending_lifecycle=True`・`pending_oco=False`（market+SL/TP 単玉のため OCO 子注文なし）・`sltp_tie="sl"`（同一バー S/T 両到達=SL 優先・config_loader.py:46）・`tick_model="ohlc_expand"`（D2 bar-mode）。戦略 `weekly_vol_band.WeeklyVolBand`（§5.1）が先頭バーで market buy(N, sl=S, tp=T) を返す。週内 S/T 未到達は L888-910 end_of_test が最終足 close で清算（=金曜引け）。

### 4.4 UC-WV3 `usecase/validate_strategy.py`（検証 S1〜S6・D2/D3）

```python
@dataclass
class ValidateStrategyRequest:
    full_bars: Sequence[Bar]
    capital: float
    f_risk: float = 0.01
    alpha_stop: float = 0.05
    seed: int = 0                       # params 固定（NFR-D3）
    B: int = 5000
    min_weeks: int = 260               # サンプル下限（D4）
    min_stop_hits: int = 30
    e_grid: tuple[str, ...] = ("E0","E1(0.5)","E1(1.0)","E1(1.5)","E1(2.0)")
    p_tp_grid: tuple[float, ...] = (0.40,0.50,0.60,0.70)

CandidateRunner = Callable[[Sequence[Bar], str, float], list[WeeklySegmentOutcome]]
# (bars, e, p_tp) -> 週次 outcome 群（run_weekly_segments を内部で 1 候補ぶん回す）

def validate_strategy(
    *, request, run_candidate: CandidateRunner,
    spa: SpaTestPort, tests: BacktestTestPort,
) -> BacktestTestResult:
    # S1 IS/OOS 週数ベース分割（D2・floor）
    weeks = unique_week_ids(request.full_bars)
    W = len(weeks)
    split_idx = floor(W * 0.7)                    # split 週 index（D2）
    is_week_ids  = set(weeks[:split_idx])         # IS=[0,split)
    oos_week_ids = set(weeks[split_idx:])         # OOS=[split,W)
    is_bars  = [b for b in request.full_bars if week_id_of(b.time) in is_week_ids]
    oos_bars = [b for b in request.full_bars if week_id_of(b.time) in oos_week_ids]

    # S2 IS 全20候補 f_k と週×候補行列
    f_matrix = []         # [n_is_weeks][20]：週次純リターン率（D2：週次純損益円/Capital）
    f_k = []              # 候補別 f_k = Σ(週次純リターン率)/n, n=IS 全週数（ノートレード週=0 寄与）
    candidates = [(e,p) for e in request.e_grid for p in request.p_tp_grid]   # 決定論順序（5×4=20）
    per_cand_weekly = {}
    for (e,p) in candidates:
        outs = run_candidate(is_bars, e, p)
        weekly_ret = [o.log.net_pnl / request.capital for o in outs]   # ノートレード週=0
        per_cand_weekly[(e,p)] = weekly_ret
        f_k.append(sum(weekly_ret) / len(weekly_ret))                  # n=IS 全週数
    # 週×候補行列（SPA 入力・週順は IS 週順）
    n_is = len(per_cand_weekly[candidates[0]])
    f_matrix = [[per_cand_weekly[c][w] for c in candidates] for w in range(n_is)]

    # サンプル下限（D4・第3結果）：IS 週数 or 後段 OOS で下限未達は INSUFFICIENT_SAMPLE
    if n_is < 1 or W < request.min_weeks:
        return _insufficient(spa_p=None, best_f_k=max(f_k) if f_k else None,
                             oos_weeks=len(oos_week_ids), oos_stop_hits=0)

    # S3 SPA p 値（D3・seed 固定）
    spa_p = spa.spa_pvalue(f_matrix, seed=request.seed, B=request.B)

    # S4 判定
    best_idx = argmax(f_k)
    best = f_k[best_idx]
    if spa_p >= 0.05:
        return BacktestTestResult(Verdict.REJECT_STRATEGY, spa_p, None, None, best,
                                  None, None, None, None, len(oos_week_ids), 0)
    if not (best > 0.0):
        return BacktestTestResult(Verdict.REJECT_STRATEGY, spa_p, None, None, best,
                                  None, None, None, None, len(oos_week_ids), 0)
    e_star, p_star = candidates[best_idx]          # f_k 最大かつ>0（S4）

    # S5 OOS：選択候補のみ1回実行（コスト込み）
    oos_outs = run_candidate(oos_bars, e_star, p_star)
    oos_weekly_ret = [o.log.net_pnl / request.capital for o in oos_outs]
    oos_mean = sum(oos_weekly_ret) / len(oos_weekly_ret) if oos_weekly_ret else 0.0
    hit_series = [1 if o.log.exit_type == "stop" else 0
                  for o in oos_outs if o.log.entry_flag]   # トレード週のみ（被覆対象）
    tp_hits    = sum(1 for o in oos_outs if o.log.exit_type == "tp" and o.log.entry_flag)
    traded     = sum(1 for o in oos_outs if o.log.entry_flag)
    stop_hits  = sum(hit_series)

    # サンプル下限（OOS 側・D4）
    if len(oos_week_ids) < request.min_weeks or stop_hits < request.min_stop_hits:
        return _insufficient(spa_p, best, len(oos_week_ids), stop_hits)

    # S6 検定（D3）
    kupiec_p = tests.kupiec(hit_series, alpha=request.alpha_stop)
    chris_p  = tests.christoffersen_independence(hit_series)
    tp_rate  = (tp_hits / traded) if traded else 0.0
    tp_calib = abs(tp_rate - p_star)

    cond_a = oos_mean > 0.0
    cond_b = kupiec_p >= 0.05
    cond_c = chris_p  >= 0.05
    verdict = Verdict.ADOPT if (cond_a and cond_b and cond_c) else Verdict.NOT_ADOPT
    return BacktestTestResult(verdict, spa_p, e_star, p_star, best,
                              kupiec_p, chris_p, tp_calib, oos_mean,
                              len(oos_week_ids), stop_hits)
```

> 注：仕様 §4.1 の利確校正（±5%pt）は **NFR 観測項目**であり S6 採否条件には含まれない（仕様 §3.2 S6 は a/b/c の3条件のみ）。tp_calibration_diff はログ・DoD 確認用に記録するが verdict には使わない（仕様 §3.2 厳守）。

---

## 5. adapter 層 詳細設計（numpy 局所化・pandas は repository 境界のみ）

### 5.1 `adapter/strategy/weekly_vol_band.py` — WeeklyVolBand（StrategyPort 実装）

stop_entry_probe.py を手本に、セグメント先頭バーで market ロング 1 件（sl=S, tp=T）を返す。S/T/N は当週 forecast から VolatilityBand で算出。`on_position_check` は使わない（D1）。

```python
class WeeklyVolBand(StrategyPort):
    def __init__(self, forecast: VarianceForecast, p_tp: float,
                 capital: float, f_risk: float) -> None:
        self._fc = forecast; self._p_tp = p_tp
        self._capital = capital; self._f_risk = f_risk
        self._armed = False                       # 先頭バー1回のみ発注

    def on_init(self, config, indicators) -> None:
        self._config = config

    def on_new_bar(self, bar_index, indicators, account) -> list[Order]:
        if self._armed or bar_index != 0:         # セグメント先頭バー（D1）でのみ
            return []
        self._armed = True
        O = indicators_open_at(indicators, 0)     # セグメント先頭 open（D2：O=週セグメント先頭バー open）
        band = VolatilityBand.from_forecast(
            self._fc.week_id, O, self._fc.sigma_minus, self._fc.sigma_plus,
            self._p_tp, self._f_risk, self._capital)
        N = _round_volume(band.N, self._config)   # volume_step 丸め（Order.validate 整合）
        sl = round(band.S, self._config["digits"]); tp = round(band.T, self._config["digits"])
        return OcoOrderPair(Order("buy","market",N,None,sl=sl,tp=tp), band).as_orders()

    def on_position_check(self, position, bar_index, indicators) -> str:
        return "hold"                             # D1：金曜引けは end_of_test が担う（未使用）
```

> O の取得：engine は market を **bar open クォート**で約定（run_backtest.py L610-656 `derive_quotes`）。戦略が VolatilityBand に使う O は「セグメント先頭バーの open」（D2）であり、indicators 経由でセグメント先頭バー open を参照する。digits 丸めは Order/engine と一致させ約定価格と S/T の桁を揃える。

### 5.2 `adapter/indicator/gk_har_estimator.py` — VarianceEstimatorPort 実装（numpy・D2/D4）

```python
class GkHarEstimator(VarianceEstimatorPort):
    def forecast(self, rs_plus_series, rs_minus_series, *, window=260, nw_lag=4):
        sp = self._har_one(rs_plus_series,  window, nw_lag)   # σ̂⁺
        sm = self._har_one(rs_minus_series, window, nw_lag)   # σ̂⁻
        if sp is None or sm is None: return (None, None)
        return (sp, sm)

    def _har_one(self, rs_series, window, nw_lag) -> float | None:
        rs = np.asarray(rs_series, dtype=float)
        if rs.size < window: return None                      # 窓未満（D4）
        rs = rs[-window:]                                       # 260週ローリング（D2）
        # log-semivariance（D2）：被説明変数 y_t = log(RS_t)。RS_t<=0 は log 不能→None
        if np.any(rs <= 0) or not np.all(np.isfinite(rs)): return None
        y = np.log(rs)
        # 説明変数 HAR（D2）: x_t = [1, RS^(1週)_{t-1}, RS^(4週平均)_{t-1}, RS^(12週平均)_{t-1}]
        #   ※全て log スケール（log-semivariance-HAR）
        X, yv = self._build_har_design(y)                      # 行=t、列=[const,1w,4w,12w]
        if X.shape[0] < X.shape[1] + 1: return None
        beta = self._ols(X, yv)                                # 正規方程式（特異→None）
        if beta is None: return None
        # 翌週予測：x_next = [1, y[-1], mean(y[-4:]), mean(y[-12:])]
        x_next = np.array([1.0, y[-1], y[-4:].mean(), y[-12:].mean()])
        mu_hat = float(x_next @ beta)                          # μ̂_w（log-semivariance 予測）
        if not math.isfinite(mu_hat): return None
        sigma = math.sqrt(math.exp(mu_hat))                    # σ̂=√(exp(μ̂))（D2・Jensen 補正なし）
        return sigma if math.isfinite(sigma) and sigma > 0 else None

    @staticmethod
    def _ols(X, y) -> "np.ndarray | None":
        XtX = X.T @ X
        try:
            return np.linalg.solve(XtX, X.T @ y)              # 特異行列→LinAlgError→None（D4）
        except np.linalg.LinAlgError:
            return None
```

> Newey-West(lag=4) の用途（D2）：HAR 係数の **HAC 共分散** 推定。点予測 σ̂ には NW は不要（OLS 係数 β がそのまま予測に入る）が、係数有意性・SPA の studentize 文脈との一貫性のため NW 共分散を計算し `_nw_cov(X, resid, lag=4)` として副産物で返す（テスト §9.2 で校正）。予測値は β に依存し NW に依存しないため σ̂ の決定論は OLS のみで保証。
>
> **HAR 説明変数の look-ahead（NFR-D4）**：x_t は全て t-1 以前の RS から構成（`_build_har_design` は y[t] を y[t-1], mean(y[t-4:t]), mean(y[t-12:t]) で説明し、当週 t を右辺に含めない）。x_next も観測済 y[-12:] のみ。§6 依存グラフで保証。

### 5.3 `adapter/repository/vol_band_parquet.py` — VolBandRepositoryPort 実装（pandas 境界）

物理形式は §8。pandas は本ファイル内に局所化（DI-4）。出力先は新規 OUT 配下のみ（出力先検証・§8.3）。

```python
class VolBandParquetRepo(VolBandRepositoryPort):
    def __init__(self, out_dir: Path): self._path = out_dir / "vol_band_forecasts.parquet"
    def save_all(self, forecasts):
        _assert_out_path(self._path)                          # 出力先検証（§8.3）
        df = pd.DataFrame([_to_row(f) for f in forecasts])
        df.to_parquet(self._path, index=False)
    def get(self, week_id):
        df = pd.read_parquet(self._path); row = df[df.week_id == week_id]
        return _from_row(row.iloc[0]) if len(row) else None
```

### 5.4 `adapter/validation/var_backtests.py` — BacktestTestPort 実装（numpy・D3）

**Kupiec POF（FR-WV-19・D3）**：LR_POF → χ²(1)。Φ・χ² は `math.erf`/`math.erfc`（D3・scipy 禁止）。
```
n = len(hit); x = sum(hit); pi_hat = x/n
LR_pof = -2*[ x*log(alpha) + (n-x)*log(1-alpha)
              - x*log(pi_hat) - (n-x)*log(1-pi_hat) ]      # pi_hat∈{0,1} は項を 0 扱い（極限）
p = chi2_sf_df1(LR_pof)                                     # = erfc(sqrt(LR_pof/2))（D3）
```
**Christoffersen 独立性（D3）**：マルコフ1次遷移 LR_ind → χ²(1)。
```
n00,n01,n10,n11 = transition_counts(hit)
pi01 = n01/(n00+n01); pi11 = n11/(n10+n11); pi = (n01+n11)/(n00+n01+n10+n11)
L_null = (1-pi)^(n00+n10) * pi^(n01+n11)
L_alt  = (1-pi01)^n00 * pi01^n01 * (1-pi11)^n10 * pi11^n11
LR_ind = -2*(log L_null - log L_alt)                       # 0 割は項 0 扱い
p = chi2_sf_df1(LR_ind)
```
```python
def chi2_sf_df1(x: float) -> float:                         # χ²(1) 生存関数（D3）
    if x <= 0: return 1.0
    return math.erfc(math.sqrt(x / 2.0))
def norm_cdf(x: float) -> float:                            # Φ（D3・SPA studentize 用）
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
```

### 5.5 `adapter/validation/spa.py` — SpaTestPort 実装（numpy・D3）

Hansen(2005) SPA_c consistent。定常ブート（Politis-Romano）・PW 自動ブロック長・再センタリング閾値 √(2 log log n)。seed 固定（`np.random.default_rng(seed)`・NFR-D3）。

```python
def spa_pvalue(self, f_matrix, *, seed, B=5000) -> float:
    F = np.asarray(f_matrix, float)             # [n][K]：週×候補（週次純リターン率）
    n, K = F.shape
    fbar = F.mean(axis=0)                        # 候補別平均 f̄_k
    omega = _bootstrap_std(F, seed, B, _pw_block_len(F))   # studentize 用 ω̂_k（ブート標準偏差）
    omega = np.where(omega <= 0, 1e-12, omega)
    V = np.max(np.sqrt(n) * fbar / omega)        # 統計量 V=max_k √n·f̄_k/ω̂_k（studentized・D3）
    thr = math.sqrt(2.0 * math.log(math.log(n))) # 再センタリング閾値 √(2 log log n)（D3）
    g = np.where(np.sqrt(n) * fbar / omega >= -thr, fbar, 0.0)   # consistent 再センタリング
    rng = np.random.default_rng(seed)            # seed 固定（NFR-D3）
    block = _pw_block_len(F)
    exceed = 0
    for b in range(B):
        idx = _stationary_bootstrap_indices(n, block, rng)   # Politis-Romano（幾何 block）
        Fb = F[idx]
        fbar_b = Fb.mean(axis=0)
        Vb = np.max(np.sqrt(n) * (fbar_b - g) / omega)       # 再センタリング後
        if Vb > V: exceed += 1
    return exceed / B                            # p 値（再センタリング後 V*_b が観測 V を超える割合）
```

**Politis-White(2004) 自動ブロック長（D3・固定手続き）**
```
def _pw_block_len(F):                            # 各候補列の自己相関から flat-top kernel で推定→中央値
    n = F.shape[0]
    K_n = ceil(2 * sqrt(log10(n)))               # lag 上限（D3）
    # 各列 j: rho_h = autocorr(F[:,j], h), h=1..M; M=自己相関打切り c=2 規則で決定
    # flat-top λ(s)=1 (|s|<=0.5), 2(1-|s|) (0.5<|s|<=1), 0 else
    # g_hat=Σ λ(h/M)|h|ρ_h ; G_hat=Σ λ(h/M)ρ_h ; D=…(Politis-White 式)
    # b_opt=(2 G_hat² / D)^{1/3} * n^{1/3}; block=round(median_j b_opt)
    return max(1, block)

def _stationary_bootstrap_indices(n, block, rng):    # Politis-Romano（1994）
    p = 1.0 / block                              # 幾何分布パラメータ（期待 block 長=1/p）
    idx = np.empty(n, dtype=int); idx[0] = rng.integers(n)
    for t in range(1, n):
        if rng.random() < p: idx[t] = rng.integers(n)      # 新ブロック開始
        else:                idx[t] = (idx[t-1] + 1) % n    # 連続（wrap）
    return idx
```
> 自己相関打切り c=2（D3）：`M = min(K_n, 最小 h s.t. |ρ_h| < c·sqrt(log10 n / n) が KN_n=ceil(sqrt(log10 n))+1 連続)` の Politis-White 固定規則。実装は §9.5 で論文既知例校正。

---

## 6. look-ahead 依存グラフ（NFR-D4・当日以降 H/L/C 不使用の保証）

ノード=計算量／エッジ=「左が右の入力」。**全ての σ̂・S・T・N ノードは target 週 w の先頭バー open（O_w）以外に当週以降の H/L/C を入力に持たない**ことを示す。

```
[確定領域：週 < w の OHLC のみ]                         [当週 w 以降]
daily_OHLC[<w] ──► GK_d[<w] ──► σ̂ᵗᵒᵗᵃˡ_{w-1}(前週実現) ─┐
                                                          ├─► E1(θ) 判定（前週 c2c ≤ −θ·σ̂ᵗᵒᵗᵃˡ）
prev_week_close[<w], prev2_week_close ──► r_prev ─────────┘
5min_OHLC[<w] ──► RS⁺/RS⁻[<w] ──► HAR.OLS(260週<w) ──► μ̂⁺/μ̂⁻ ──► σ̂⁺_w/σ̂⁻_w
                                                                       │
                                  O_w = open(週 w 先頭バー) ◄──────────┤（当週で唯一許容＝先頭 open）
                                                                       ▼
                                                    S=O_w·exp(−1.96·σ̂⁻_w)
                                                    T=O_w·exp(z(p_tp)·σ̂⁺_w)
                                                    N=f_risk·Cap/(O_w−S)
                                                                       │（発注後）
                            週 w 内バー H/L/C ──► engine の S/T 監視・end_of_test ──► 決済（執行のみ・推定に不帰還）
```

**禁止エッジ（テストで検出・§9.6）**：
- `σ̂⁺_w / σ̂⁻_w ← RS[w]`（当週 RS を予測の右辺に入れない。HAR は y[t-1] 以前のみ）
- `σ̂ᵗᵒᵗᵃˡ ← GK_d[w 以降]`（E1 閾値は前週実現＝GK_d[<w] のみ・D2）
- `S/T/N ← H_w / L_w / C_w`（O_w のみ許容）
- `r_prev ← close[w 以降]`（前週末・前々週末 close のみ）

**検証コード（§9.6）**：`assert_no_lookahead(target_week, recorded_inputs)` を tools/usecase 純関数で実装し、各 σ̂/S/T/N の入力 week_id 集合が `{w' : w' < w}`（O_w を除く）に含まれることを assert。違反時 `LookaheadViolationError`。

---

## 7. 状態遷移（週サイクル）とログ

### 7.1 週サイクル状態機械（WV2・1 週セグメント）

```
[WEEK_START]
   │ repo.get(week_id)
   ├─ forecast None/estimable=False ──► [NO_TRADE(not_estimable)] ──► [WEEK_END]
   │ estimable
   ▼
[RULE_EVAL]
   ├─ e 規則 偽 ──► [NO_TRADE(entry_rule_false)] ──► [WEEK_END]
   │ 真
   ▼
[ARMED] 先頭バー market buy(N, sl=S, tp=T)（engine が bar open 約定）
   │
   ▼
[IN_POSITION] 週内バー走査（engine SL/TP 監視・sltp_tie="sl"）
   ├─ S 到達 ──► [CLOSED(stop)]
   ├─ T 到達 ──► [CLOSED(tp)]
   ├─ 同一バー S&T 両到達 ──► [CLOSED(stop)]（SL 優先・D1）
   │ 週末まで未到達
   ▼
[FORCE_CLOSE] end_of_test：最終足 close で清算 ──► [CLOSED(end_of_test=時間切れ)]
   ▼
[WEEK_END] 純損益計上＋週次ログ ──► 次週
```

exit_reason→exit_type 写像：`sl→"stop"` / `tp→"tp"` / `end_of_test→"timeout"`（仕様 §2.5 {利確,ストップ,時間切れ}）。

### 7.2 週次ログ（FR-WV-13・仕様 §4.2 14項目）

`WeeklyLogRecord`（usecase 純 dataclass。tools が JSON 化）：
```python
@dataclass
class WeeklyLogRecord:
    week_id: str; O: float
    sigma_plus: float | None; sigma_minus: float | None
    S: float | None; T: float | None; N: float | None
    entry_flag: bool
    exit_type: str           # "tp" | "stop" | "timeout" | "none"
    holding_days: float      # (exit_time−entry_time)/86400（D2・カレンダー日数）
    gross_pnl: float; cost: float; net_pnl: float
    event_flag: bool
```
**純損益（FR-WV-11・D4）**：`net_pnl = gross_pnl − c_spread − c_comm − r_fund*(holding_days/365)*notional ± dividend_adj`。
- gross_pnl=(exit−entry)·N（TradeRecord.pnl() の swap/commission=0 ベース。コストは UC が外付け）。
- 配当調整：ロング受取（+）。配当落ち日が [entry_time, exit_time] 内のとき適用（D4）。
- c_spread/c_comm/r_fund/dividend = config 注入・既定 0.0（D4・TBD-1）。

> 実装方針：コストは engine 内 swap/commission ではなく **UC 後付け**（run_weekly_segments が stats から gross を取り、config コストを差し引く）。これにより engine 無改変（C2）かつ config で 0.0 → 確定値の差替が容易。

---

## 8. 物理データモデル（VolBandRepository 保存形式）

### 8.1 週次予測ストア `vol_band_forecasts.parquet`（新規 OUT 配下）

| 列 | 型 | 説明 |
|---|---|---|
| week_id | str | ISO 週 "YYYY-Www"（主キー・昇順） |
| sigma_plus | float64 nullable | σ̂⁺_w（estimable=False は null） |
| sigma_minus | float64 nullable | σ̂⁻_w |
| sigma_total_prev | float64 nullable | 前週実現 GK 週次ボラ（E1 閾値） |
| estimable | bool | 推定可否 |

- 形式＝Parquet（小規模時系列・列指向・決定論シリアライズ）。索引＝week_id（参照は等値 lookup・O(1) 相当）。
- 正規化レベル：第3正規形（week_id 関数従属のみ・冗長なし）。1 週 1 行。
- 件数規模：複数年 → 数百行（260週ローリング＋OOS で ~500-800 行想定）。インデックス物理構造は不要（全件メモリ可）。

### 8.2 出力レポート（新規 OUT 配下）

| ファイル | 形式 | 内容 |
|---|---|---|
| `weekly_log.jsonl` | JSON Lines | WeeklyLogRecord 14項目×週数（`asdict` 1行1週・event_flag で別集計可） |
| `validation_result.json` | JSON | BacktestTestResult（採否・SPA p・選択候補・各検定 p・OOS 期待値・サンプル数） |
| `validation_report.md` | Markdown | 採用時 §4.1 全閾値・棄却時 SPA p と最良 f_k（DoD・tools 整形） |

### 8.3 出力先検証（NFR-S2・C1・既存データ非波及）

`tools` 純関数 `_assert_out_path(path) -> None`：解決済絶対パスが `marketdata/` `simulator/tests/fixtures/` `simulator/tests/confirmation/` のいずれかプレフィクスなら `OutputPathError`。許可は CLI 指定の `--out-dir`（新規）配下のみ。結合テストで実行前後の既存ディレクトリ mtime 不変を assert（§9.7）。

---

## 9. テスト設計（単体／統合／E2E・カバレッジ目標・自動化範囲）

| 層 | カバレッジ目標 | 自動化 |
|---|---|---|
| domain VO | 行 100%・分岐 100%（不変条件全分岐） | pytest |
| usecase（UC・純関数） | 行 ≥ 95%・S4/S6 全 verdict 分岐 100% | pytest |
| adapter（numpy 検定） | 行 ≥ 90%＋参照論文既知数値例の校正 | pytest |
| 統合（WV1→WV2→WV3） | 主要決定論パス・mtime 不変 | pytest |
| E2E（CLI） | 合成小データ 1 本・採用/棄却/不採用/INSUFFICIENT 各1 | pytest |

### 9.1 単体（domain・NFR-D1 校正）
- `test_volatility_band_numeric_example`：O=39000,σ̂⁻=0.020,σ̂⁺=0.025,p_tp=0.50 → S≈37501・T≈39663・N≈6.67（許容 round 0/2）。**回帰テスト**（NFR-D1・誰が計算しても一致）。
- 不変条件：O−S≤0 / S≤0 / T≤O / p_tp グリッド外 → ValueError 各々。
- VarianceForecast：estimable=True で σ̂≤0/None/Inf → ValueError。no_trade → estimable=False。
- TradingWeek：trading_times 空/非昇順/first≠[0]/last≠[-1] → ValueError。week_id_of/weekday_of の UTC 規約（既知 epoch→既知 ISO 週・Mon=0）。
- OcoOrderPair：entry が非 market/非 buy/sl or tp None → ValueError。

### 9.2 単体（GK/HAR・参照論文校正）
- **GK 校正**：Garman-Klass(1980) の効率係数を満たす既知 OHLC（手計算 gk_d）で `aggregate_weekly_gk` の値一致。負分散の床 0。
- **HAR 校正**：Corsi(2009) 型 HAR を既知合成系列で OLS 係数を手計算（正規方程式 3×3＋const）と一致。`σ̂=√(exp(μ̂))`（Jensen 補正なし・D2）の値一致。
- 窓<260/特異行列/RS≤0/NaN → None（D4）。
- Newey-West(lag=4) HAC 共分散：既知系列で半正定値・lag=4 重み（1−h/5）一致。

### 9.3 単体（UC・決定論分岐）
- `aggregate_weekly_rs`：同一立会日内隣接5分のみ（昼休み・オーバーナイト・欠落跨ぎ除外を assert）。RS⁺/RS⁻ 符号別二乗和。r=0 非寄与。
- エントリ規則 E0（常真）・E1(θ) 4 値（前週 c2c ≤ −θ·σ̂ᵗᵒᵗᵃˡ の境界）。前週確定値なし→False。
- f_k：n=IS 全週数・ノートレード週=0 寄与（週次純リターン率=net_pnl/Capital）。
- S1 split：W=10 → floor(7)=7、IS=[0,7)/OOS=[7,10)。
- S4：spa_p≥0.05→REJECT_STRATEGY／best≤0→REJECT_STRATEGY／else 選択候補。
- S6：a/b/c 全充足→ADOPT、各1欠け→NOT_ADOPT（4 分岐）。tp_calibration は verdict に不使用を assert。
- INSUFFICIENT_SAMPLE：W<260 or stop_hits<30（D4 第3結果）。

### 9.4 単体（Kupiec/Christoffersen 校正）
- **Kupiec(1995) 既知例**：n・x・α から LR_POF を手計算し χ²(1) p 一致。pi_hat∈{0,1} 極限項 0。
- **Christoffersen(1998) 既知例**：遷移カウント n00/01/10/11 から LR_ind 手計算一致。
- `chi2_sf_df1(x)=erfc(√(x/2))`：既知 χ²(1) 分位（x=3.841→p≈0.05）一致（D3）。

### 9.5 単体（SPA・seed 固定・NFR-D3）
- **再現性回帰テスト**：同一 f_matrix・同一 seed で 2 回 `spa_pvalue` → p 完全一致（NFR-D3・**回帰テスト**）。
- **Politis-White ブロック長**：論文の既知 AR(1) 系列で b_opt が論文値域に一致（c=2・K_n=⌈2√(log10 n)⌉・D3）。
- 定常ブート：seed 固定で index 列が決定論（幾何 block・wrap）。
- 帰無で p 大・強優位候補で p 小（方向性 sanity）。Φ=`0.5(1+erf(x/√2))` 既知値一致。

### 9.6 単体（look-ahead 依存グラフ・NFR-D4）
- `assert_no_lookahead`：σ̂_w 入力 week_id が全て < w（HAR 右辺に当週 RS 不在）。σ̂ᵗᵒᵗᵃˡ が前週のみ。S/T/N が O_w 以外の当週 H/L/C を参照しない。
- **混入注入テスト**：故意に当週 RS を HAR 右辺へ入れた fixture で `LookaheadViolationError` が上がること（**回帰テスト**）。

### 9.7 統合
- WV1→WV2→WV3 を合成小データで実行し、同一入力で選択候補(e*,p_tp*)・verdict が 2 回一致（NFR-D2 決定論）。
- 既存データディレクトリ（marketdata/・fixtures/・confirmation/）の mtime 実行前後不変（NFR-S2・C1）。
- `_assert_out_path` 禁止プレフィクス拒否・新規 OUT 許可。
- 週セグメント実行：S 到達週=stop・T 到達週=tp・未到達週=timeout（end_of_test）・同一バー両到達=stop（sltp_tie）。

### 9.8 E2E（CLI）
- 採用シナリオ（合成で a/b/c 全充足）→ `validation_report.md` に全閾値記録。
- 棄却（SPA p≥0.05）→ SPA p と最良 f_k 記録。不採用（b 欠け）→ NOT_ADOPT。INSUFFICIENT（週数不足）→ 明示出力。

### 9.9 依存方向 CI（DI-1〜DI-5）
- `grep` 検証（§2.2 各コマンド）を CI に組み込み 0 件を assert。既存ファイル差分 0（git diff）。

---

## 10. シーケンス（週末バッチ→週次執行→検証）

```
【週末バッチ（WV1）】
tools ─load(5分/日足/配当)─► EstimateWeeklyBandRequest ─► estimate_weekly_band
  estimate ─aggregate_weekly_rs/gk(純)─► (RS,GK) ─forecast(Port)─► GkHarEstimator(numpy)
  estimate ─VarianceForecast 群─► repo.save_all ─► vol_band_forecasts.parquet(新規OUT)

【週次執行（WV2・1候補ぶん）】
tools ─build_interactor(WeeklyVolBand_EA 分岐)─► run_weekly_segments
  for week: repo.get ─► (estimable?→rule?) ─► run_segment(week_bars,wid,fc)
     run_segment ─► RunBacktestInteractor.execute(pending_lifecycle,sltp_tie=sl,ohlc_expand)
        WeeklyVolBand.on_new_bar(先頭) ─► market buy(N,sl=S,tp=T)
        engine ─► bar open 約定→週内 S/T 監視→未達 end_of_test 清算 ─► BacktestStats
  outcomes ─► WeeklyLogRecord(14項目) ─► weekly_log.jsonl(新規OUT)

【検証（WV3）】
tools ─► validate_strategy
  S1 split(floor 0.7) ─► IS/OOS bars
  S2 for 20候補: run_candidate(is_bars,e,p) ─► f_k, f_matrix
  S3 spa.spa_pvalue(f_matrix,seed,B=5000) ─► spa_p
  S4 spa_p<0.05 ∧ best>0 ─► (e*,p_tp*)  else REJECT/INSUFFICIENT
  S5 run_candidate(oos_bars,e*,p_tp*) ─► hit_series,tp_rate,oos_mean
  S6 tests.kupiec/christoffersen ─► a∧b∧c ─► ADOPT/NOT_ADOPT
  ─► BacktestTestResult ─► validation_result.json / validation_report.md(新規OUT)
```

---

## 11. config 拡張（framework・既存無改変部不触）

`config_loader.py` に **追記のみ**（既存 `_ConfigModel`・既存変換は不触）：
```python
class VolEstimationParams(BaseModel):       # WV1
    model_config = ConfigDict(extra="forbid")
    window: int = Field(260, ge=1); nw_lag: int = Field(4, ge=0)
    min_bars_per_week: int = Field(default=..., ge=1)   # 立会内最小5分本数（D4・要供給既定）

class ValidationParams(BaseModel):          # WV3
    model_config = ConfigDict(extra="forbid")
    seed: int = 0; B: int = Field(5000, ge=1)
    alpha_stop: float = 0.05; f_risk: float = 0.01
    min_weeks: int = 260; min_stop_hits: int = 30
    c_spread: float = 0.0; c_comm: float = 0.0; r_fund: float = 0.0   # D4・TBD-1 既定0.0
    e_grid: list[str] = ["E0","E1(0.5)","E1(1.0)","E1(1.5)","E1(2.0)"]
    p_tp_grid: list[float] = [0.40,0.50,0.60,0.70]
```
usecase へは pydantic を漏らさずプレーン dataclass/引数へ変換して渡す（既存 config_loader 様式・DI-1）。`main.build_interactor` に `WeeklyVolBand_EA` 分岐（ea_name でディスパッチ）とバッチ/検証エントリを **追記**。

---

## 12. 未解決の数値入力（TBD・アルゴリズム曖昧性ではない）

| # | 項目 | 状態 |
|---|---|---|
| TBD-1 | 外部コスト `c_spread`/`c_comm`/`r_fund`・配当調整値 | config 既定 0.0 で検証実行可。確定値供給後に採否再評価（仕様 §7・D4） |
| TBD-2 | 検証の経験的結果（採否・選択 e/p_tp） | 手続きが返す値。データ実行まで未確定（仕様 §7） |
| TBD-3 | `min_bars_per_week`（立会内最小5分本数） | 欠損週判定の閾値。データ供給元の立会時間に依存（要数値供給・D4） |
| TBD-4 | イベント週カレンダー（日銀/FOMC/SQ） | event_flag 供給元未定（仕様 §2.6・基本設計 TBD-6） |
| TBD-5 | 新規 OUT ディレクトリの具体パス | 運用規約で確定（§8.3 許可ディレクトリ） |

> 上記は全て「数値・データ供給待ち」であり、アルゴリズム／signature／数式は本書で一意確定済。実装は既定値で着手可能。

---

## 自己レビュー（prompt-validation-workflow・努力レベル xhigh）

### Pre-mortem（最も可能性の高い失敗原因）

**死因仮説 H1（最有力）**：D1「market+SL/TP 単玉で OCO・ストップ優先・金曜引けを充足する」が engine 実挙動と乖離し、`pending_oco` 不要・`sltp_tie` 適用・`end_of_test` 清算のいずれかが成立せず、週セグメント実行が仕様 §2.6 と不一致になる。

**死因仮説 H2**：look-ahead 依存グラフで σ̂ᵗᵒᵗᵃˡ_w を「前週実現」と定義したが、HAR の説明変数（4週/12週平均）が当週 RS を含む実装に滑り、NFR-D4 違反。

**死因仮説 H3**：SPA の studentize（ω̂_k）・再センタリング（√(2 log log n)）の式が Hansen(2005) と細部で異なり、seed 固定でも p 値が論文校正に外れる。

### 証拠先行検証

| 原因 | 実証的証拠（先行提示） | 判定 |
|---|---|---|
| H1 | `run_backtest.py` L640 `fill_market_order`（bar open 約定）／L742 `check_sltp_hit_at_tick(..., sltp_tie=config.sltp_tie)`（market 約定玉の SL/TP をティック監視・sl/tp は `_OpenTrade(sl=order.sl, tp=order.tp)` L651-653 から）／L888-910 `if pending_mode and open_trades: ... exit_reason="end_of_test"`（最終足 close 清算）。`config_loader.py:46 sltp_tie: Literal["sl","tp"]="sl"`。`trade_record.py:27 _EXIT_REASONS` に sl/tp/end_of_test 含む。→ market buy(sl=S,tp=T)＋pending_lifecycle=True で「週内 S/T 監視（SL 優先）＋週末 close 清算」が成立。pending_oco は単玉のため不要。 | **棄却**（H1 不成立＝設計は engine 実挙動と整合。OcoOrderPair を単玉化した §3.4/§4.3 が正しい） |
| H2 | §4.2 UC が `hist_plus=[weekly_rs[w][0] for w in week_ids[:i]]`（target i の手前まで）を渡し、§5.2 `_build_har_design` が y[t] を y[t-1]/mean(y[t-4:t])/mean(y[t-12:t]) で説明（当週 t 右辺不在）。x_next=[1,y[-1],y[-4:].mean(),y[-12:].mean()]。§9.6 に混入注入回帰テストを規定。 | **棄却**（設計上当週 RS は右辺に不在。ただし実装逸脱リスクは §9.6 回帰テストで構造的に検出） |
| H3 | §5.5 は studentize（√n·f̄_k/ω̂_k）・再センタリング閾値 √(2 log log n)・consistent 再センタリング `g` を D3 の指定どおり実装。ただし Hansen(2005) の ω̂_k（ブート分散）と g の正確な定義は §9.5 で「論文既知数値例校正」に委ねており、本書は擬似コード水準。論文の数値例 fixture は未取得。 | **成立（部分）**：SPA 実装の論文厳密一致は校正テストに依存。本書 signature/擬似コードは確定だが、ω̂_k と PW block の数値正確性は §9.5 校正で担保する必要があり、**残存リスクへ転記** |

### 反映

- H1/H2：棄却。設計（§3.4 単玉 OcoOrderPair・§4.3 config・§6 依存グラフ・§9.6 回帰テスト）に反映済。
- H3：成立（部分）。SPA/PW block の数値正確性は擬似コード水準であり、論文既知数値例による校正テスト（§9.5）合格をもって確定とする旨を残存リスクに明示。signature・seed 固定再現（NFR-D3）は確定済。

### 残存リスク（後続作業へ委譲）

1. SPA の ω̂_k・再センタリング g・PW block 長の Hansen(2005)/Politis-White(2004) 厳密一致は §9.5 校正テスト（論文既知数値例 fixture 取得）で実装時に確定する。本書は signature・擬似コード・seed 固定再現を確定。
2. Newey-West HAC 共分散は σ̂ 点予測に不使用（β のみで予測）。NW は係数有意性の副産物。SPA studentize へ NW を使うか否かは §5.5 ではブート分散 ω̂_k を採用しており NW 非依存。実装時に整合確認。
3. config 既定値（min_bars_per_week・コスト・OUT パス）は数値供給待ち（§12 TBD）。アルゴリズムは確定。

---

## 上流入力前提検証（upstream-input-validation）

### 上流入力の整理（4 種別）

| 種別 | 件数 | 内容 |
|---|---|---|
| 依頼者指示 | 1 件 | 本依頼（D1〜D5 確定事項・成果物要求） |
| 前段成果物 | 2 件 | 仕様 v1.0／基本設計 v0.1.0 |
| 既存合意の引き継ぎ | 1 件 | ISSUE-027（金曜引け＝週単位セグメント・RESOLVED） |
| 他者レビュー指摘 | 0 件 | 該当なし |

### 前提抽出

- 前提P1（D1）：engine の `end_of_test` 清算が「market 単玉＋pending_lifecycle=True」で発火し金曜引けを実現する。
- 前提P2（D1）：同一バー S/T 両到達は `sltp_tie="sl"`（config_loader.py:46）でストップ優先。
- 前提P3（D5/基本設計）：usecase で numpy 不使用・adapter 局所化が既存規約と両立（compute_stats.py は usecase numpy 先例＝矛盾しうる）。
- 前提P4（D2）：epoch int→曜日/週は `datetime.fromtimestamp(ts,UTC).weekday()`（derive.py 規約）。
- 前提P5（ISSUE-027）：run_backtest.py に on_position_check 配線が 0 件。

### 証拠先行検証

| 前提 | 実証手段・出力 | 判定 |
|---|---|---|
| P1 | `Read run_backtest.py L888-910`：`if pending_mode and open_trades and bars: ... exit_reason="end_of_test"`（最終足 close 清算）。pending_mode は `pending_lifecycle=True` で有効。 | 実証取得→採用 |
| P2 | `Read config_loader.py:46`：`sltp_tie: Literal["sl","tp"]="sl"`。`Read run_backtest.py L742-747`：`check_sltp_hit_at_tick(..., sltp_tie=config.sltp_tie)`。 | 実証取得→採用 |
| P3 | `Read compute_stats.py:36`：`import numpy as np`（usecase で numpy 使用の先例＝確認）。`bar.py:9`「domain 層は numpy のみ依存可」。→ 既存規約では usecase numpy も可。D5 は adapter 局所化を**追加制約**として課す（既存規約に反しない・依頼意図尊重）。 | 実証取得→**条件付き採用**（D5 を一意確定とし usecase numpy 余地を閉じる旨を §2.1 注に明示） |
| P4 | `Read report_ui/derive.py L107-111`：`datetime.fromtimestamp(int(entry_time), tz=timezone.utc); wday=WEEK[dt.weekday()]`（Mon=0・UTC）。 | 実証取得→採用 |
| P5 | `Grep "on_position_check" run_backtest.py`（ISSUE-027 記載の grep 実証を継承）＋本書 §3.4/§4.3 で on_position_check 不使用・end_of_test 経路を採用。 | 実証取得→採用 |

### 判定結果

- P1/P2/P4/P5：**採用**（実コード Read で前提成立を確認）。
- P3：**条件付き採用**。既存規約は usecase numpy を許容するが、本書は決定論水準の要請から D5（adapter 局所化）を一意確定とし、基本設計 §3.4 が残した「実装者選択余地」を本詳細設計で閉じる（§2.1 注記）。これは追従ではなく実証に基づく独立判断。

### 残存リスク

1. SPA/PW block の論文厳密一致は §9.5 校正テストへ委譲（自己レビュー残存リスク 1 と同一）。
2. config 数値供給待ち（§12 TBD）。
