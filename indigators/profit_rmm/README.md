# PRO!fitRMM（Python 移植）

4 つのオシレーター（**iRSI(Typical) / iWPR(+100) / iMFI / MAROD**）を、それぞれ
「系列平均 ±3σ のスパンで正規化した単位距離」へ `funLevelCount` で変換・合算した
**符号付きレベルカウント（市場の合成「温度」）** を、別ウィンドウ **[-10,10]** の
ヒストグラム 1 本で可視化する MQL4 インジケーターの Python 移植。元
`sample/MQL4/Indicators/PRO!fitRMM.mq4`（2015, PRO!fit Investars）準拠。

## 構成

| ファイル | 層 / 責務 | 元 MQL4 の対応 |
|---|---|---|
| `src/core.py` | 純粋計算（numpy ＋ 共有層のみ） | `iRSI` / `iWPR` / `iMFI` / MAROD / σ スパン / funLevelCount / σ6 水準 |
| `src/rmm.py` | 成果物層（DataFrame 整形） | `ExtBufferLevelCount`（DRAW_HISTOGRAM, `rmm_lc`） |
| `src/loader.py` | 入力アダプタ（CSV → OHLCV） | OnCalculate 引数 high/low/close/**volume** の供給 |
| `src/plot.py` | 出力アダプタ（matplotlib / PNG） | separate window [-10,10] + DRAW_HISTOGRAM(clrLime) + σ6 水準線 |
| `src/lwc_chart.py` | 出力アダプタ（lightweight-charts） | 同上（duck typing, `create_histogram` / `horizontal_line`） |
| `tests/test_core.py` | 計算の検証 | — |
| `tests/test_rmm.py` | 成果物層の検証 | — |
| `tests/test_lwc_chart.py` | 出力アダプタの検証（Fake） | — |
| `demo.py` | デモ（合成 OHLCV → PNG） | — |

## 使い方

```python
from src import load_ohlcv_csv, build_rmm, rmm_levels

df = load_ohlcv_csv("ohlcv.csv")          # open/high/low/close/volume 必須（列名大小不問）
out = build_rmm(df, osc_period=6, ma_period=6)   # rmm_lc 列を持つ DataFrame
levels = rmm_levels(df, osc_period=6, ma_period=6)  # up_1s..up_3s / dn_1s..dn_3s
```

### 描画（matplotlib / PNG）
```python
from src.plot import plot_rmm
plot_rmm(df, "out.png", osc_period=6, ma_period=6)   # 下段 y 軸 [-10,10] 固定
```

### 描画（lightweight-charts）
別ウィンドウ型のため subchart を用意して渡す（`create_histogram` / `horizontal_line` を
持つオブジェクトを duck typing で受ける。`lightweight_charts` は本パッケージで import しない）:
```python
from src.lwc_chart import add_rmm
sub = chart.create_subchart(position="below", height=0.3, sync=True)
add_rmm(sub, df, osc_period=6, ma_period=6)   # ヒスト 1 本 + σ6 水準線 6 本
```

### デモ / テスト
```bash
python demo.py                 # profit_rmm_demo.png を生成（matplotlib 必要）
python -m pytest -q            # 全テスト（Fake チャートで描画非依存）
```

## 計算の要点（合成「温度」）

1. **4 オシレーター**（昇順）: iRSI(Typical) / iWPR(+100, 権威 WPR.mq5 準拠) /
   iMFI(volume 必須) / MAROD=`(Typical-EMA)/EMA*100`。
2. **σ スパン**: 各系列の `avg±3σ`（母σ）幅。RSI/WPR/MFI は [0,100] にクランプ、
   **MAROD のみ非クランプ**（非対称）。
3. **funLevelCount**: 4 ケース採点で各オシレーターを単位距離化し合算 → `level_count`
   （符号付き。0 から離れるほど合成トレンドが強い）。
4. **σ6 水準**: `level_count` の `avg±{1,2,3}σ`（母σ）を水準線として表示。

## 元 MQL からの主な差分

- **iRSI flat→50 / iMFI 負MF==0→100 / iWPR flat→前値**（warm-up `i<period-1`）を 1:1 再現。
  iRSI/iMFI/iWPR は共有 mql_builtins、採点・MAROD は共有 profit_system の再公開、
  EMA/typical_price は共有層を再利用。
- **ゼロ割はガードしない**（`span==50`/`span==0`/`ma==0` で `inf`/`nan` を許容。元挙動
  1:1・退化入力でのみ発生）。
- **OBJ_RECTANGLE 背景色帯は移植しない**（描画装飾・対象外）。
- ビット完全一致は非保証（参照 CSV が無いため warm-up/EMA 初期化は実機と厳密一致しない
  可能性）。詳細・出典は `SPEC.md` §9。

詳細仕様は `SPEC.md` を参照。
```bash
cd indicators/profit_rmm && python -m pytest -q
```
