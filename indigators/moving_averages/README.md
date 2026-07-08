# MovingAverages（Python 移植）

MQL5 標準ライブラリ `MovingAverages.mqh` の Python 移植。
移動平均 4 種（SMA / EMA / SMMA / LWMA）の計算関数を提供する純粋ロジック
ライブラリで、入出力・描画は含まない（依存は numpy のみ）。

## 構成

```
moving_averages/
├── README.md
├── src/
│   ├── __init__.py   # 公開 API
│   └── core.py       # 移動平均計算本体（numpy のみ）
└── tests/
    └── test_core.py  # pytest
```

元 `MovingAverages.mqh` は I/O・描画を持たない計算専用 include のため、
`loader` / `plot` / `lwc_chart` / `demo` 層は作成していない。

## API

### スカラー版（指定位置 1 点の値）

| 関数 | 内容 |
|---|---|
| `simple_ma(position, period, price)` | 単純移動平均（SMA） |
| `exponential_ma(position, period, prev_value, price)` | 指数移動平均（EMA） |
| `smoothed_ma(position, period, prev_value, price)` | 平滑移動平均（SMMA / RMA） |
| `linear_weighted_ma(position, period, price)` | 線形加重移動平均（LWMA） |

### バッファ版（配列全体を逐次計算、`buffer` を破壊的更新、計算本数を返す）

| 関数 | 内容 |
|---|---|
| `simple_ma_on_buffer(rates_total, prev_calculated, begin, period, price, buffer)` | SMA |
| `exponential_ma_on_buffer(...)` | EMA |
| `linear_weighted_ma_on_buffer(...)` | LWMA（classic, スライド和） |
| `linear_weighted_ma_on_buffer_fast(..., weight_sum=0)` | LWMA（fast）。`(本数, weight_sum)` を返す |
| `smoothed_ma_on_buffer(...)` | SMMA |

## 使い方

```python
import numpy as np
from moving_averages import simple_ma, simple_ma_on_buffer

price = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

# 1 点だけ
simple_ma(4, 3, price)          # -> 4.0

# 配列全体（buffer を破壊的に更新）
buffer = np.zeros_like(price)
simple_ma_on_buffer(len(price), 0, 0, 3, price, buffer)
# buffer -> [0, 0, 2, 3, 4]
```

## MQL からの主な変更点

- **時系列の向き**: MQL の `ArrayGetAsSeries` / `ArraySetAsSeries` による向き調整を
  除去し、入力配列は**昇順**（index 0 = 最古、末尾 = 最新）前提とした。
- **オーバーロード**: `LinearWeightedMAOnBuffer` の 2 つのオーバーロードは
  Python では別名 `linear_weighted_ma_on_buffer` / `_fast` に分離。`weight_sum` は
  参照渡しの代わりに引数＋戻り値で受け渡す。
- **挙動の忠実再現**: `smoothed_ma` の `position == period-1` におけるシード単純平均が
  直後の再帰式で上書きされる元コードの癖をそのまま維持している
  （`core.py` の Note 参照）。

## テスト

```bash
python -m pytest indicators/moving_averages/tests/ -q
```
