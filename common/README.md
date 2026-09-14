# lib（共有プリミティブ層）

特定の指標に属さず、複数の指標から横断的に再利用する純粋ロジック（numpy のみ）を
置く共有層。MQL の Include 系ユーティリティの移植先。

## 構成

```
lib/
├── README.md
├── __init__.py          # 公開 API
├── applied_price.py     # 適用価格 7 種（MQL ENUM_APPLIED_PRICE 互換）
└── tests/
    └── test_applied_price.py
```

## applied_price — 適用価格

OHLC 配列から「適用価格」系列を生成する。移動平均・RSI・MACD など、価格系列を
入力に取る指標で共通利用する。`MovingAverages.mqh` が価格非依存であるのに対応し、
価格種の決定をこの共有層に独立させている。

| 種別（`AppliedPrice`） | 値 | 計算 |
|---|---|---|
| `CLOSE` | 1 | 終値 |
| `OPEN` | 2 | 始値 |
| `HIGH` | 3 | 高値 |
| `LOW` | 4 | 安値 |
| `MEDIAN` | 5 | (高値 + 安値) / 2 |
| `TYPICAL` | 6 | (高値 + 安値 + 終値) / 3 |
| `WEIGHTED` | 7 | (高値 + 安値 + 2×終値) / 4 |

### 使い方（移動平均との合成）

価格種の選択（本層）と移動平均の計算（`indicators/moving_averages`）は分離されており、
呼び出し側で合成する。

```python
import numpy as np
from lib import applied_price, AppliedPrice
from indicators.moving_averages.src import simple_ma

open_ = np.array([5.0, 12.0, 11.0, 13.0, 14.0])
high  = np.array([10.0, 20.0, 18.0, 19.0, 21.0])
low   = np.array([2.0, 4.0, 6.0, 7.0, 8.0])
close = np.array([8.0, 16.0, 15.0, 17.0, 18.0])

# 1) 適用価格を選ぶ（例: TYPICAL）
price = applied_price(AppliedPrice.TYPICAL, open_, high, low, close)
# 2) 移動平均へ渡す
sma = simple_ma(4, 3, price)
```

## テスト

```bash
python -m pytest lib/tests/ -q
```
