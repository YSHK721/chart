# TDD 実行記録 — profit_hl_band 🟡-1（available 真実源）/ 🟡-3（境界テスト）是正

## §1 要件分析

| 要素 | 内容 |
|---|---|
| 機能 | `compute_hl_band(high,low,close,*,window,normalize)` の available 真実源を実スライス長へ単一化し、window<1 を ValueError に正規化する |
| 目的 | `effective=min(window,n)` が `_tail` の実スライス長と乖離（window=0→s[-0:]=全長 / window=-3→s[3:]）し available の真実源が崩れる欠陥（🟡-1）を是正。境界テスト欠落（🟡-3）を補完 |
| 入力 | high/low/close（np.ndarray・昇順同長）＋ window（int>=1 or None）＋ normalize（bool） |
| 出力 | HlBandResult。window<1 で ValueError。available は len(slice)>=2 で判定 |
| 制約 | 後方互換（window=None,normalize=False）は bit 一致で不変。共有モジュール不接触。api/tests・JS テスト緑維持。コミットしない |
| 対象外 | window=float 等の非整数型バリデーション。DTO フィールド改名（docstring 明確化のみ）。MT4 bit-exact |

### 🟡-1 採用規約（window<1 の扱い）
**ValueError を採用**（委任された二択 ValueError/全長フォールバックのうち）。理由: (1) 負/0 は「直近 W 本」の
窓として数学的に無意味、(2) `min(window,n)` 真実源は実スライス長と silent に乖離し誤った available を返す、
(3) 全長フォールバックは呼出側バグの暗黙救済となる。入口で ValueError とし乖離経路自体を消去したうえで、
生存経路は `effective=len(slice_high)` を単一の真実源とする。

## §2 テストケース設計（TD.2 境界値分析）

- [TD.2 境界・下限不正] TC-7a: window=0 → ValueError（窓長 0 は無意味）
- [TD.2 境界・負] TC-7b: window=-1 → ValueError（normalize True/False 双方）
- [TD.2 境界・close[-2]不在] TC-7c: window=None & n<2 → ValueError（既存 N>=2 ガードの特性化固定）
- [TD.1 正常系・帯潰れ] TC-7d: window=1 → available=False・NaN levels（ValueError ではない・退化 false-green 回避）
- [TD.5 真実源] TC-7e: window=2,n=5 → available=True かつ tail 2 本のみ参照（index 0..2 改変で levels 不変）

## §3 Red 結果

- **Red 観測ゲート（実装の事前不在実証）**:
  - `grep -n "window < 1\|不正な窓" src/core.py` → NOT FOUND（window<1 ガード不在）
  - `grep -n "len(slice_high)\|effective = len" src/core.py` → NOT FOUND（len 真実源不在）
  - `grep -rn "window=0\|window=-1" tests/` → NOT FOUND（境界テスト不在）
  - 結論: 過剰実装（AP.2 G-1）／成功テスト先行（AP.1 R-2）／実装事前残存／assertion 弱体いずれにも非該当。
- **初回実行（5 件）**: `pytest -k "window_zero or window_negative or ..."` → **2 failed, 3 passed**。
  - TC-7a（window=0）/ TC-7b（window=-1）: **Failed: DID NOT RAISE ValueError**（真の Red・期待された理由＝
    window<1 ガード未実装）。
  - TC-7c/d/e: 初回 Pass。R-7 分類 → TC-7c=既存 N>=2 ガードの特性化テスト（regression 固定）、TC-7d=挙動保存
    テスト（min(1,5)=1<2 ≡ len(tail-1)=1<2 で同結果）、TC-7e=正の window では min(window,n)==len(tail) で
    coincide のため tail スライス挙動の regression 固定。driving 実装は上記 grep で事前不在を実証済。
    Red バイパス非該当（R-7 欠落なし。真の Red＝TC-7a/b 2 件が成立）。

## §4 Green 結果

- **最小実装** `src/core.py`:
  - 入口に `if window is not None and window < 1: raise ValueError(...)` を追加（_tail 到達前に乖離経路を消去）。
  - `effective = min(window, n) if window is not None else n` → `effective = len(slice_high)`（単一真実源）。
  - 根拠コメントを 🟡-1 規約に従い明示。
- **当該テスト**: `pytest -k "window_zero or ..."` → **5 passed**（2 failed → 5 passed の Red→Green 遷移）。
- **全テスト**: profit_hl_band **51 passed**（既存 46 ＋ 新規 5）。破壊なし。
- **下流不変実証**: api/tests **151 passed**・JS（catalog_window_param 7 / 全 207）**passed**・後方互換 TC-3 **1 passed**（bit 一致維持）。
- **修正実証**: window=0/-1/-3 が全て `ValueError: window>=1 または None が必要`（silent NaN 解消）。

## §5 Refactor 結果

- 🟡-2（DTO docstring 明確化）/ 🟡-4（README/SPEC 反映）を構造改善として実施（新規ロジック・分岐追加なし）:
  - `HlBandResult.dist_high/dist_low` docstring を「normalize=True で比率 / False で絶対距離」と明記。
  - `compute_hl_band` Returns/Raises・`hl_band.py` Raises docstring を更新。
  - README/SPEC に window/normalize/available・比率正規化式・因果窓・後方互換モードを追記。
- リファクタ前後とも全テスト Green（51 passed）。テストコード不変。

## §6 完了判定

- [x] テスト存在・実行可能（test_causal_ratio.py 境界 5 件）
- [x] Red / Green / Refactor 出力あり
- [x] 各 Red step で実装事前不在を実証してから Green（Red 観測ゲート充足・真の Red＝TC-7a/b・R-7 非該当）
- [x] テスト名が機能・期待結果を記述（raises_value_error / available_false / truth_source）
- [x] リファクタ後も全テスト通過（51 passed・api 151・JS 207）
- [x] 横断アンチパターン非該当（テスト改変なし・スキップなし・順序逆転なし・カバレッジ偽装なし）
- カバレッジ: window 値域（0/負/None&n<2/1/正常 W）の全境界＋真実源（len(slice)）＋後方互換 bit 一致。
- 違反リスト: 空集合。
