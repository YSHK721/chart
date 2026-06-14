# 自己レビュー出力（prompt-validation-workflow）— profit_hl_band 🟡修正 TDD（Red→Green）＋ドキュメント反映

## 対象成果物

`feature/profit-hl-band-causal-ratio` ブランチ上の以下の差分（未コミット）：

- `profit_hl_band/src/core.py`：(1) `window<1`（int の 0・負）を `ValueError` に正規化する入口
  ガードを新設。(2) `available` の真実源を `min(window,n)` から実スライス長 `effective = len(slice_high)`
  へ単一化。(3) `HlBandResult.dist_high/dist_low` の docstring を「normalize=True で比率 / False で
  絶対距離」と明確化。(4) `compute_hl_band` の Returns/Raises docstring を更新。
- `profit_hl_band/src/hl_band.py`：`hl_band_levels` の Raises に `window<1` を追記。
- `profit_hl_band/tests/test_causal_ratio.py`：境界テスト 5 件追加（window=0/-1 → ValueError、
  window=None&n<2 → ValueError、window=1 → available=False、available 真実源＝実スライス長）。
- `profit_hl_band/README.md` / `SPEC.md`：比率正規化・因果窓・available・後方互換モードを反映。

## Pre-mortem 分析（最も可能性の高い失敗原因）

本成果物が本番で失敗するとしたら、最も可能性の高い死因：

1. **F1: 真実源の片側 len 依存** — `effective = len(slice_high)` のみを採り `slice_low` 長と
   乖離する余地があれば、High/Low で available 判定がずれる。
2. **F2: window<1 ValueError が既存呼出を破壊** — UI・api/tests・demo 等が window=0 や負値を渡して
   いた場合、従来 silent NaN だったものが例外化し下流が落ちる。
3. **F3: 後方互換 bit 一致の破壊** — `effective` 算出経路の変更が window=None,normalize=False の
   投影値に副作用を与え、旧 MQL 値との bit 一致が崩れる。
4. **F4: Red 観測欠落（R-7）** — 追加 5 テストのうち 3 件（TC-7c/d/e）が初回実行で Pass。
   過剰実装・実装事前残存・assertion 弱体のいずれかによる Red バイパスの疑い。
5. **F5: 修正が乖離を隠蔽しただけ** — window=0 が依然 silent に NaN を返し（真の修正でなく）
   表面的に test を通しただけの可能性。

## 追従性バイアス点検（軽量検証の落とし穴 / 用語拡大解釈）

- 上流（🟡-1）は「ValueError とするか全長フォールバックか、どちらか一方を選び根拠明示」と委任。
  これを「ValueError が唯一正しい」と拡大解釈していないか → 採否の根拠（負/0 は窓として無意味・
  乖離経路の根本遮断・暗黙救済の回避）を独立に明示済み（拡大解釈なし・委任範囲内の選択）。
- 「Red→Green 完了」を謳う場合、Red は**期待された理由での失敗**を要する。初回 Pass した 3 件を
  「Red 成立」と誤認していないか → F4 で 4 分類軸により個別判定する。

## 検証する N 辺（事前列挙 / Triangulation Checklist）

| # | 辺 | 検証内容 | 実施状態 | 証拠強度 |
|---|----|---------|----------|---------|
| 1 | core.py vs 下流呼出（window<1） | 既存に window<1 を渡す呼出が存在しないこと | ☑ done | ★★★ |
| 2 | 修正後 vs 後方互換固定点（TC-3） | window=None,normalize=False の bit 一致維持 | ☑ done | ★★★ |
| 3 | profit_hl_band vs api/tests vs JS | 3 スイート全緑（共有経路 call_binding 経由） | ☑ done | ★★★ |
| 4 | Red 観測（TC-7a/b） vs Red バイパス（TC-7c/d/e） | 真の Red 1 件以上・初回 Pass の分類 | ☑ done | ★★★ |

## 証拠先行検証

| 死因 | 実証手段 | 出力 | 判定 |
|---|---|---|---|
| F1 | core.py:245 `effective = len(slice_high)`。`slice_high/slice_low` は同一 `series` から同一 `_tail(_, window)` で切るため長さ恒等。compute_ratios/compute_distances は双方 np.abs(...)（入力同形）で同長を返す | 構造的に len 一致。w=None/3/5/1 で available 一貫 | 棄却 |
| F2 | `grep -rn "window=0\|window=-" --include=*.py indigators/`（test 除く） | NONE（window<1 を渡す呼出なし）。api/tests 151 passed・JS 207 passed | 棄却 |
| F3 | `pytest -k backward_compat` | 1 passed（close_ref=12.0/dn_165=9.08 等 bit 一致維持） | 棄却 |
| F4 | window=0/-1 で `pytest -k "window_zero or window_negative"` 初回 | **2 failed: DID NOT RAISE ValueError**（真の Red）。TC-7c=既存 N>=2 ガードの特性化テスト（regression 固定）、TC-7d=挙動保存テスト（min(1,5)=1<2 も len(tail-1)=1<2 も同結果）、TC-7e=正の window では min(window,n)==len(tail) で coincide するため tail スライス挙動の regression 固定。いずれも grep で実装事前不在を確認済（window<1 ガード・len 真実源は absent）。Red バイパス（過剰実装/事前残存/assertion 弱体）非該当 | 棄却（真の Red＝TC-7a/b 2 件成立） |
| F5 | 修正後 window=0/-1/-3 を実行 | 全て `ValueError: window>=1 または None が必要`。silent NaN 解消。`if window is not None and window < 1` が _tail 到達前に raise（乖離経路消滅） | 棄却 |

## 検証と反映

- **F1**: 棄却。`slice_high`/`slice_low` は同一 series・同一 window で切るため長さ恒等。片側 len 依存に
  実害なし。
- **F2**: 棄却。window<1 を渡す既存呼出は皆無。下流（api/tests 151・JS 207）全緑で破壊なし。
- **F3**: 棄却。後方互換固定点（TC-3）が bit 一致を維持。`effective` 算出は available 判定にのみ作用し、
  available=True 時の投影値計算経路には不変。
- **F4**: 棄却。真の Red は TC-7a（window=0）/ TC-7b（window=-1）の 2 件で「DID NOT RAISE」失敗を観測。
  初回 Pass した TC-7c/d/e は **regression／特性化テスト**であり、対応する driving 実装（window<1 ガード・
  len 真実源）は grep で事前不在を実証済。Red 観測ゲートの 4 分類軸（過剰実装/成功テスト先行/事前残存/
  assertion 弱体）いずれにも非該当。Green は TC-7a/b の Red→Pass 遷移で実証（2 failed → 5 passed）。
- **F5**: 棄却。修正は乖離経路（_tail が full/部分スライスを返すのに available=False になる矛盾）を
  入口 ValueError で物理的に消去し、かつ生存経路では `len(slice)` を真実源化。表面的マスクではない。

## 残存リスク

- **R1**: `window=float`（例 2.5）等の非整数窓長は今回スコープ外（🟡-1 は int 値域 <1 のみ指摘）。
  現状 `_tail(series, 2.5)` は `series[-2.5:]` で TypeError になる可能性があるが、本タスクの範囲外。
  後続で型バリデーションが必要なら別途。
- **R2**: コミット未実施（依頼指定「コミットしない・後続フェーズ」）。差分は未ステージ状態。
- **R3**: MT4 純正との bit-exact は依然非保証（参照 CSV 不在・SPEC §9 既述）。本修正は後方互換モードの
  bit 一致のみ保証し、実機一致は対象外。
