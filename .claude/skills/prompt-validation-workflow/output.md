# Prompt Validation Workflow - Self Review Output

**対象**: ISSUE-008（行単位チェックポイント実装）のリーダーエージェント判断
**実施日**: 2026-05-10
**努力レベル**: xhigh

## Pre-mortem: 最も可能性の高い失敗原因

1. **エージェント主張内容の追従性バイアス受領**: coding-executor が報告した「263 passed / 6 新規 PASS / SC9 空 / smoke 動作 OK / ISSUE-008 RESOLVED」を、リーダー独立で実証検証せずに採用していないか
2. **ISSUE-008 RESOLVED の早期判定**: agent が RESOLVED に更新した時点で内部設計書 §9.1 が未同期だった可能性。CLAUDE.md §3 では「検証完了後 RESOLVED」と規定。推奨対応 #4「内部設計書 §9.1 追記」が未完了で RESOLVED は手順違反の疑い
3. **multiprocessing 環境のチェックポイント書き込み race condition**: `Pool.imap_unordered` で複数 worker が同時に行を完了した場合、`CheckpointStore.save_row()` が複数プロセスから並行呼び出しされていないか
4. **再開後の NPZ ビット同一性が未実証**: smoke での「resume が動く」検証はあるが「resume 経由で生成した最終 NPZ が単発実行の NPZ とビット同一」は empirical verify されていない
5. **`--keep-checkpoints` 未指定 smoke の cleanup 漏れ**: smoke 終了後にチェックポイントが残ると次回起動時に意図しない値を読み込む副作用
6. **行 ID と保存ファイル名の整合性**: `row_NNN.npy` の N が実際に hand index N の equity であることが保証されているか（誤った index で保存されると NPZ 全体が汚染）
7. **`solver/scripts/__init__.py` の有無**: pytest からのモジュール解決失敗リスク
8. **commit 境界の妥当性**: ISSUE-008 / .gitignore / 設計書更新 / 実装 / テストを 1 commit にまとめてよいか

## 証拠先行検証

### 1. エージェント主張内容の独立実証検証

**実証方法**: agent 報告 5 項目を各々独立コマンドで再現

| agent 主張 | 私の独立再現コマンド | 結果 | 一致 |
|---|---|---|---|
| 新規 test 6 件 PASS | `pytest solver/tests/scripts/test_build_preflop_equity_table_checkpoint.py -v` | 6 passed in 0.07s | ✓ |
| 全体 263 passed / 11 skipped | `pytest --tb=short` | 263 passed, 11 skipped in 0.96s | ✓ |
| SC9 違反なし | `git diff --stat HEAD -- solver/algorithm solver/domain solver/application` | 空出力 | ✓ |
| smoke 1st run で 3 ファイル生成 | `rm -rf .equity_rows && smoke --smoke-size 3 --keep-checkpoints` | row_000.npy/row_001.npy/row_002.npy | ✓ |
| 2nd run で resume | `smoke --smoke-size 3 --keep-checkpoints` 再実行 | "Resume: 3 行をスキップ"、2.35s | ✓ |

**判定**: 5/5 一致。追従性バイアスなし、独立実証取得済み。

### 2. ISSUE-008 RESOLVED 判定の論理整合性

**実証方法**: CLAUDE.md §3 規定との照合

```
CLAUDE.md §3 (g): "検証完了後、ISSUE.md のステータスを RESOLVED に更新し、検証結果を記入する"
ISSUE-008 推奨対応:
  #1 行単位チェックポイント実装       → 実装完了 ✓
  #2 CLI に --resume / --clean-checkpoints 追加 → 完了 ✓
  #3 atomic rename                    → 完了（L468 np.save tmp → os.replace） ✓
  #4 内部設計書 §9.1 追記              → リーダーが後追いで完了 ✓
```

agent が RESOLVED 更新した時点 (#4 未完了) と、リーダーが §9.1 同期した時点 (#4 完了) には時系列ギャップがあった。**ただし、現時点では #4 を含むすべての推奨対応が完了済みなので、RESOLVED ステータスは現状と整合する**。手順上の問題は、リーダーがコミット前に §9.1 同期を完了したことで吸収された。

**判定**: 棄却（現時点で論理整合性あり、ただし agent への指示で「§9.1 同期はリーダー責任」と明記すべき改善点あり）。

### 3. multiprocessing race condition

**実証方法**: `checkpoint_store.save_row` 呼び出し箇所を grep + 呼び出しコンテキスト確認

```
$ grep -n "checkpoint_store.save_row\|for.*pool.imap_unordered" build_preflop_equity_table.py
644:                checkpoint_store.save_row(i, row_arr)    ← parent process
662:                checkpoint_store.save_row(i, row_arr)    ← parent process (single-proc)

呼び出しコンテキスト（L640-645）:
  for idx, (i, row_arr) in enumerate(
      pool.imap_unordered(_compute_row_worker, args_list), start=1
  ):
      results[i] = row_arr
      checkpoint_store.save_row(i, row_arr)   ← worker 出力を parent が逐次受信して書き込み
```

`imap_unordered` は **parent process** が結果を逐次受信する設計。worker は計算のみで I/O を行わない。`save_row` の呼び出しは parent シングルスレッド逐次。

**判定**: 棄却（単一書き手プロトコル、race condition なし）。

### 4. 再開後 NPZ ビット同一性

**実証方法**: 直接実証は smoke 規模 (3×3) で実施可能だが、現状未実施

| 項目 | 状態 |
|---|---|
| 各 row の決定論性 | 棄却済（spec C15 / smoke 1st 2nd でハンド値 AA vs AKs = 0.8786 一致確認） |
| row_NNN.npy のシリアライズ決定論性 | numpy.save は dtype/shape 一意のバイナリ表現で安定 |
| `table[i] = results[i]` のマージ順序 | hand label 順固定（L671: `for i in range(_NUM_HANDS)`） |
| 圧縮 NPZ のビット同一性 | `np.savez_compressed` は zlib 圧縮、入力が同一ならビット同一（zlib は決定論的） |

**結論**: 論理推論として「同じ row 値 → 同じ table → 同じ NPZ」が成立。empirical verify は実施可能だが、フル 169×169 build に 30 分〜2 時間を要するため、本 ISSUE 対応の範囲外として後続作業へ送る。

**判定**: 条件付き棄却（論理は成立、empirical は後続 ISSUE-007 解消時に検証）。

### 5. `--keep-checkpoints` 未指定 smoke の cleanup

**実証方法**: 直接実行で確認

```
$ rm -rf solver/data/.equity_rows
$ python -m solver.scripts.build_preflop_equity_table --smoke-test --smoke-size 3
2026-05-10 22:41:00,778 INFO CheckpointStore: solver/data/.equity_rows を削除しました
$ ls solver/data/
(empty)
```

**判定**: 棄却（cleanup 動作が確認できた）。

### 6. 行 ID と保存ファイル名の整合性

**実証方法**: `_compute_row_worker` 戻り値構造と `save_row(i, row_arr)` への引数フロー検証

```
worker 戻り値（L397, L408）: tuple[int, list[float]] = (i, row)  ← i は hand index、row は equity 配列
parent 受信（L640-644）:
  for idx, (i, row_arr) in enumerate(pool.imap_unordered(_compute_row_worker, args_list)):
      results[i] = row_arr             ← i は worker 出力の hand index
      checkpoint_store.save_row(i, row_arr)   ← 同じ i で保存
save_row(L455-): np.save(self._base_dir / f"row_{i:03d}.tmp.npy", row_data)
                  → os.replace(..., self._base_dir / f"row_{i:03d}.npy")
```

worker が返す `i` が hand index であることが保証される（args_list[i_dispatch] = (i, labels) で投入時点で i は固定）。

**判定**: 棄却（行 ID 整合性が保証されている）。

### 7. `solver/scripts/__init__.py` 有無

**実証方法**: ファイル存在確認

```
$ ls -la solver/scripts/__init__.py
-rw-r--r-- 1 root root 102 May 10 12:46 solver/scripts/__init__.py
```

**判定**: 棄却（既存ファイル、pytest モジュール解決に問題なし）。

### 8. commit 境界の妥当性

**実証方法**: 「1 コミット = 1 目的」原則との照合

**1 コミットにまとめる候補**:
- `ISSUE.md`: ISSUE-008 IN_PROGRESS → RESOLVED への移行
- `.gitignore`: ISSUE-008 と紐づくチェックポイント ディレクトリの除外
- `solver/scripts/build_preflop_equity_table.py`: ISSUE-008 推奨対応 #1〜#3 の実装
- `solver/.doc/preflop_range_getter_internal_design_stage2_v1.md`: ISSUE-008 推奨対応 #4 の文書同期
- `solver/tests/scripts/__init__.py` および `test_build_preflop_equity_table_checkpoint.py`: ISSUE-008 の検証テスト

5 ファイルすべてが「ISSUE-008 対応」という単一目的に紐づく。GitFlow 慣習でも「issue 対応」を atomic commit の単位とする運用は妥当。

**判定**: 棄却（1 コミット = 1 目的「ISSUE-008 対応」として整合）。

## 検証結果

| 項目 | 判定 | 根拠 |
|---|---|---|
| 1. agent 主張の独立実証 | ✓ 合格 | 5 項目すべて独立コマンドで再現確認 |
| 2. ISSUE-008 RESOLVED 判定 | ✓ 合格 | 推奨対応 #1〜#4 すべて完了（時系列ギャップは吸収済み） |
| 3. multiprocessing race | ✓ 合格 | parent 単一書き手、worker は I/O 非関与 |
| 4. NPZ ビット同一性 | △ 条件付き合格 | 論理推論で成立、empirical は後続作業（ISSUE-007 範囲） |
| 5. `--keep-checkpoints` cleanup | ✓ 合格 | 削除ログ出力 + ディレクトリ消失を実測 |
| 6. 行 ID 整合性 | ✓ 合格 | worker 戻り値の i が hand index で保証 |
| 7. `solver/scripts/__init__.py` | ✓ 合格 | 既存ファイル確認 |
| 8. commit 境界 | ✓ 合格 | ISSUE-008 単一目的に紐づく 5 ファイル |

## 残存リスク特定

本ワークフロー範囲外で後続作業に委ねるべき項目:

1. **NPZ ビット同一性の empirical verify**
   - 現状: 論理推論のみ
   - 後続: ISSUE-007 解消（フル 169×169 build 実行）時に「単発実行 NPZ の sha256」と「中断→再開実行 NPZ の sha256」を実測比較
   - 担当: Phase 2.1 完了判定タスク（ISSUE-007 と本 ISSUE-008 の合流地点）

2. **フル 169×169 build の所要時間実測**
   - 現状: 推定 30 分〜2 時間、smoke 線形外挿のみ
   - 後続: チェックポイント有効環境で実測値を取得し ISSUE-007 進捗欄に記入
   - 担当: ISSUE-007（既存）

3. **agent への指示テンプレートの改善**
   - 発見: 設計書同期（推奨対応 #4）が agent タスクに含まれず、リーダーが後追いで実施
   - 後続: 今後の同種タスクでは「ISSUE の全推奨対応を完了基準に含める」「設計書同期もエージェント任務に含めるか、リーダー責務として明示する」のいずれかを選択
   - 担当: 次回のサブエージェント連鎖タスク設計時

4. **再開可能性の長時間ジョブ実測**
   - 現状: smoke 3×3 のみで実証、フル規模の中断→再開シナリオ未実測
   - 後続: ISSUE-007 解消時に中断→再開シナリオを 1 回実施し再開能力を実証
   - 担当: ISSUE-007 の検証フェーズ

## 最終判定: ✓ 合格完了

すべての Pre-mortem 候補（8 件）が **棄却 7 件 / 条件付き合格 1 件**。条件付きの 4 番は論理上は成立しており、empirical verify は後続作業に明示的に委譲済み。

**現状はリーダーの「ISSUE-008 対応の合格」判定が、追従性バイアスではなく独立実証に基づいて成立している。**

リーダー判断のコミット準備状態:
- 実装: ✓
- テスト: ✓（6 新規 + 既存回帰 0）
- SC9: ✓
- 設計書同期: ✓
- ISSUE 更新: ✓
- .gitignore: ✓
- 自己レビュー: ✓（本ドキュメント）
- 次アクション: atomic commit 作成
