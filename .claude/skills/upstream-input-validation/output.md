# 上流入力前提検証（upstream-input-validation）— profit_hl_band 🟡修正 TDD

## 上流入力の整理（step S-1）

| 種別 | 件数 | 内容 |
|---|---|---|
| 依頼者指示 | 1 件 | 🟡-1〜4 をTDD（Red→Green）＋ドキュメント反映で是正。window<1の規約はValueError推奨だが「どちらか一方を選び根拠明示」を委任 |
| 他者レビュー指摘 | 4 件 | 🟡-1（available真実源乖離）/🟡-2（DTO docstring）/🟡-3（境界テスト欠落）/🟡-4（README/SPEC陳腐化） |
| 前段成果物 | 1 件 | feature/profit-hl-band-causal-ratio の未コミット実装（core.py/hl_band.py 等） |
| 既存合意の引き継ぎ | 1 件 | 後方互換（window=None,normalize=False）はbit一致で不変 |

## 前提抽出（step S-2）

| # | 上流主張（要約） | 暗黙の前提 | 独立検証可能性 |
|---|---|---|---|
| P1 | `effective=min(window,n)` が実スライス長と乖離する | `_tail(s,0)=s[-0:]`=全長、`_tail(s,-3)=s[3:]` であり min(window,n) と不一致 | 可（python実行） |
| P2 | window=0/-1 は現状 available=False（NaN）になる | 現コードの分岐挙動 | 可（python実行） |
| P3 | build_hl_band は compute_distances 独立呼出のため dist_high/low docstring変更は無影響 | build_hl_band が compute_hl_band を呼ばず compute_distances を直接呼ぶ | 可（Read済 hl_band.py:65-66） |
| P4 | README/SPEC が旧挙動（絶対距離・available なし）のまま | 両ファイルに「比率」「available」記述が無い | 可（grep済） |
| P5 | 後方互換 window=None,normalize=False で bit一致が既存testで固定済 | test_causal_ratio.py TC-3 が legacy 期待値で固定 | 可（Read済 test_causal_ratio.py:114-140） |
| P6 | UI JS テスト・api/tests も緑維持が必要（共有非接触） | core.py の window<1 規約変更が下流 hl_band_levels/UI に波及しないか | 可（実行） |

## 証拠先行検証（step S-3）

| # | 実証手段 | 出力 | 判定 |
|---|---|---|---|
| P1 | python3 `_tail` 実行 | `_tail(arr,0) len=5`（全長）、`_tail(arr,-3) len=2`（s[3:]）。`min(0,5)=0`/`min(-3,5)=-3` と乖離 | 実証取得（乖離成立） |
| P2 | python3 compute_hl_band(window=0/-1/-3) | 全て available=False, up_165=nan | 実証取得 |
| P3 | Read hl_band.py:65-66 | `build_hl_band` は `compute_distances(high,low,close)` を直接呼ぶ。compute_hl_band 非経由 | 実証取得（docstring変更無影響） |
| P4 | grep README/SPEC | 「絶対距離」「加算/減算」記述のみ。「比率」「normalize」「available」「window」記述なし | 実証取得（陳腐化成立） |
| P5 | Read test_causal_ratio.py:114-140 | TC-3 が close_ref=12.0/dn_165=9.08 等 legacy 値で bit一致固定済 | 実証取得 |
| P6 | （後続 Green で pytest 実行予定） | 是正後に api/tests・JSテスト実行で確認 | Green フェーズで実証 |

## 判定結果（step S-4）

| 上流入力 | 判定 | 根拠 |
|---|---|---|
| 🟡-1（available真実源乖離） | 採用 | P1で `min(window,n)` と実スライス長 `len(_tail)` の乖離を実コードで実証 |
| 🟡-1 window<1規約=ValueError推奨 | 条件付き採用 | 委任された判断。ValueError採用（負/0は窓として無意味・実スライス長との乖離を根本遮断）。根拠コメント明示で対応 |
| 🟡-2（DTO docstring） | 採用 | P3で build_hl_band 無影響を実証。docstring明確化に限定（改名なし） |
| 🟡-3（境界テスト欠落） | 採用 | 退化false-green回避のため window=0/-1/None&n<2/window=1 を追加 |
| 🟡-4（README/SPEC陳腐化） | 採用 | P4で「比率/available/window」記述不在を実証 |
| 後方互換 bit一致不変 | 採用 | P5で既存test固定を確認。是正は window<1 経路のみに限局し None 経路を変えない |

### window<1 規約の最終決定
**ValueError を採用**。理由: (1) 負/0 は「直近 W 本」の窓として数学的に無意味、(2) 現状の `min(window,n)` 真実源は実スライス長（`len(_tail)`）と乖離し silent に誤った available を返す（P1実証）。これを `effective=len(slice_high)` へ単一真実源化したうえで、window<1 は入口で ValueError とし乖離経路自体を消す。全長フォールバック（window<1→None化）は「不正値の暗黙救済」となり呼出側のバグを隠蔽するため不採用。

## 残存リスク（step S-5）

- P6（UI JS・api/tests 緑維持）は Green フェーズの pytest 実行で実証する。本検証時点では未実行。
- window=float（例 2.5）等の型不正は今回スコープ外（指摘は int 値域 <1 のみ）。後続で必要なら別途。
