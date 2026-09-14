# ma_marod 戦略検証スクリプト集

`.doc/MA_MAROD_STRATEGY_VALIDATION_REPORT.md`（2026-07-21〜22）および
`.doc/MA_MAROD_BASIC_DESIGN.md` の設計判断を裏付けた実測スクリプト。
market_profile/analysis の前例に倣い、検証コードを恒久保存する（ユーザー指示 2026-07-22）。

## 前提

- Python: `lightweight-charts-python-main/.venv/bin/python`（pandas/numpy 必須。venv 以外に pandas なし）
- 【HTTP 系のみ】リプレイサーバ起動が必要:
  `cd simulator/replay_ui && bash serve.sh 8281`
- データ: `data/marketdata/`（jp225_m1.csv・rollups/jp225_m1_*.csv）
- パスはリポジトリ相対（`_ROOT = parents[3]`）。実行はどのディレクトリからでも可。

## スクリプト一覧（レポート章との対応）

| スクリプト | 入力経路 | 内容 | レポート章 |
|---|---|---|---|
| `measure_exceed.py` | HTTP(8281) | 外れ値バンド超過率（q_out 別・rolling min/max 理論限界） | 設計書 §2（バンド案棄却の根拠） |
| `outlier_quantile.py` | HTTP(8281) | 外れ値イベント分位の初回実測（バー単位・全履歴） | 設計書 §2（イベント分位採用の根拠） |
| `evq_soundness.py` | HTTP(8281) | バー単位 vs エピソード極値・絶対値 vs 超過量の統計点検 | 設計書 §2（episode 既定の根拠） |
| `touch_mfe_mae.py` | HTTP(8281) | 各ラインタッチ後の MFE/MAE/最終損益（h=5/10）＋med_lo 有意性 | 戦略レポート §1 |
| `kelly_nanpin.py` | HTTP(8281) | 3 段ケリーナンピン（フルケリー破産・縮小係数走査） | 戦略レポート §2 |
| `kelly_nanpin_med.py` | HTTP(8281) | 2 段版（med まで）。kelly_nanpin.py を runpy で再利用 | 戦略レポート §2 |
| `kelly_small.py` | HTTP(8281) | 10 万円口座・ミニ CFD の現実的枚数配分 | 戦略レポート §2.1 |
| `multi_tf.py` | CSV＋HTTP検証 | 多時間足展開（5m〜1D）。冒頭でサーバ出力と数値一致検証 | 戦略レポート §3 |
| `strategies_all.py` | CSV | 追加戦略①〜③（SMA押し目・オーバーナイト・上抜け順張り） | 戦略レポート §4 |
| `tpo_filter.py` | CSV＋mp_stats | 戦略④ TPO 集中度フィルタ A/B（market_profile/analysis を再利用） | 戦略レポート §4 |

## 実行時間の目安

- HTTP 系: 数秒〜1 分
- `strategies_all.py`: 〜2 分／`tpo_filter.py`: 〜3 分（M1 全読込）
- `multi_tf.py`: 〜10 分（5m の イベント分位ループが支配的）

## 注意（結果解釈の前提）

- すべてインサンプル（重み・保有日数・窓長は全期間で選択）。ウォークフォワード未実施。
- 費用はスプレッド 10 円/往復のみ（ファンディング・滑り未控除）。
- 確定結論はレポート正本を参照。既知の禁止事項: フルケリー運用・×10CFD の小口座利用・
  ショート系・1h 以下・オーバーナイト保有戦略・TPO フィルタ（いずれも実測で棄却）。
