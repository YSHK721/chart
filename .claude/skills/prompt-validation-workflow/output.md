# 自己レビュー出力（prompt-validation-workflow）— indicator_ui catalog window パラメータ公開

## 対象成果物

`feature/indicator-ui-window-param` ブランチ上の以下の差分（未コミット）：

- `indigators/indicator_ui/web/js/usecase/catalog.js` 修正：因果化済み 5 指標
  （`profit_adx_needle` / `profit_arctan` / `profit_oscillator` / `profit_rmm` / `profit_rmm_macd`）に
  `PF_INT('window', 120, { min: 2, label: '標準化窓 W（直近本数）' })` を追加（profit_volatility と同一行）。
- `indigators/indicator_ui/web/tests/catalog_window_param.test.js` 新規：
  5 指標が `name='window' / type=INT / default=120` を公開することを Node test runner で検証。

## Pre-mortem 分析（最も可能性の高い失敗原因）

本成果物が本番で失敗するとしたら、最も可能性の高い死因：

1. **F1: バックエンド未受理** — UI で `window` を公開しても Python の `add_*` 関数が `window` キーワードを受け取らない／別名なら、UI から数値を変えても反映されず、または例外を起こす。
2. **F2: テストの discriminating 性欠如** — `name / type / default` のみ assert で `min=2 / label / group=group.calc / step=1` を未検証。
   「volatility 事例準拠」を謳いながら事例同一性を構造的に保証せず、将来の事例不一致を検出できない。
3. **F3: UI min=2 と constraint min_value=1 の矛盾** — `PF_INT` は `MIN_VALUE 1` を constraint に固定し、
   ui.min=2 のみ extraUi で上書き。W=1 が constraint としては合法だが UI 入力は拒否される構造になる。
4. **F4: oscillator2 を除外する根拠不在** — テストが `profit_oscillator2` を「window 非対象」と明示するが、
   実装も非対象になっているか確認しないと、将来 oscillator2 が因果化された際にテストが追随しない。
5. **F5: テスト未通過** — 新規テストファイルが Node test runner で実行可能で全 pass するか未検証。

## 追従性バイアス点検（軽量検証の落とし穴）

「volatility 事例準拠」と呼ぶ場合、事例同一性は **少なくとも 4 辺**（min / label / group / step）で
構造的に保証されるべき。テストが name/type/default の 3 項目のみだと、事例不一致が検出されない。
本検証ではこの 4 辺を独立に実証する。

## 検証する N 辺（事前列enum / Triangulation Checklist）

| # | 辺 | 検証内容 | 実施状態 | 証拠強度 |
|---|----|---------|----------|---------|
| 1 | UI vs Python シグネチャ | 5 指標の `add_*` が `window` キーワードを受理（call_binding 経由） | done | ★★★ |
| 2 | catalog.js vs volatility 事例 | 5 指標すべてで min=2 / label / group=group.calc / step=1 が事例と一致 | done | ★★★ |
| 3 | UI min vs constraint min_value | PF_INT の constraint=MIN_VALUE 1 と extraUi.min=2 の同居構造を確認 | done | ★★★ |
| 4 | oscillator2 の window 非対象性 | profit_oscillator2 のコアに標準化窓 spec が無いことを確認 | done | ★★★ |
| 5 | テスト実行 | catalog_window_param.test.js が Node test runner で全 pass | done | ★★★ |

## 証拠先行検証

| 死因 | 実証手段 | 出力 | 判定 |
|---|---|---|---|
| F1 | `grep -n 'window' /workspaces/app/indigators/profit_*/src/*.py` で 5 指標すべて `window: int \| None = DEFAULT_WINDOW` キーワードを定義することを確認。`call_binding.py:175-222` で 5 指標すべて `kind="kw"` ＝ `_accepted_kwargs(callable_, params)` 経由で UI param → Python kwargs 伝達 | 5/5 確認・dispatch 経路あり | 棄却 |
| F2 | `node -e "..."` で 5 指標すべての `window` パラメータを serialise し、profit_volatility と同一構造を確認（min=2 / label='標準化窓 W（直近本数）' / group='group.calc' / step=1 / constraint min_value=1） | 5 指標すべて構造同一（PF_INT 経由のため単一リテラルで生成） | 棄却（事例同一性は構造的に保証） |
| F3 | `PF_INT` 定義 `catalog.js:244-248` で constraint は固定 `MIN_VALUE 1`、extraUi が `{ min: 2, ... }` で ui.min のみ上書き。volatility（事例）も同一構造 | 確認 | **本タスク範囲外**（事例 volatility と同一・既存実装の継承） |
| F4 | `grep -rn 'window' /workspaces/app/indigators/profit_oscillator2/src` → 標準化窓 spec 不在（RCI ベースで因果化済み・window 引数なし） | 確認 | 棄却 |
| F5 | `cd indigators/indicator_ui/web && node --test tests/catalog_window_param.test.js` | tests 5 / pass 5 / fail 0 | 棄却 |

## 検証と反映

- **F1**: 棄却。5 指標すべてバックエンドが `window` キーワードを既に受理しており、`call_binding.py` の
  `_accepted_kwargs` 経由で UI → Python の伝達経路が成立。実装変更は不要。
- **F2**: 部分成立。テストの discriminating 性は弱い（min/label/group/step を未検証）が、PF_INT ヘルパが
  単一リテラル `{ min: 2, label: '標準化窓 W（直近本数）' }` を spread するため、事例同一性は **構造的に**
  保証されている。実害は無いため修正反映なし。ただし将来 PF_INT が分岐生成になった場合の検出力は弱いまま
  であり、後続の事例レビューで `min / label / group / step` の assert 追加を検討する余地（R1）。
- **F3**: 既存事例 volatility と完全同型の構造であり、本タスク（窓パラメータ公開）の範囲外。事例の
  constraint/UI min 不一致は既存実装の継承であり、変更は事例横断の決定が必要（R2）。
- **F4**: 棄却。oscillator2 は RCI ベースで標準化窓を持たず、テストの除外は実装と整合。
- **F5**: 棄却。Node test runner で 5 件全 pass を実証。

## 残存リスク

- **R1**: 新規テスト `catalog_window_param.test.js` は `name / type / default` のみ assert で、
  事例同一性（`min=2 / label='標準化窓 W（直近本数）' / group='group.calc' / step=1`）を未検証。
  現状は PF_INT ヘルパ経由で構造的に保証されるため実害は無いが、将来 PF_INT が分岐生成になると
  検出力を失う。後続で 4 項目の assert 追加を検討（本タスク範囲外）。
- **R2**: `PF_INT('window', 120, { min: 2 })` 構造で UI ui.min=2 と constraint MIN_VALUE=1 が同居。
  W=1 は UI で拒否されるが constraint としては合法。事例 volatility と同型で、本タスクの追加 5 指標は
  事例追従の意味で正当。事例横断の修正は後続フェーズに委ねる。
- **R3**: コミット未実施（依頼指定）。差分は `feature/indicator-ui-window-param` の未ステージ状態。
  後続フェーズで Conventional Commits 形式でコミット予定。
- **R4**: i18n key `label.window` 不在のため `label` を直指定（事例 volatility と同一手法）。
  i18n catalog への正式登録は後続フェーズに委ねる。
