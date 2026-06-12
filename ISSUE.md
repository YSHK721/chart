# Issue Log

## ISSUE-001

- 概要：`programmer` サブエージェントが現 Claude Code セッションのエージェントレジストリに未登録のため起動できない
- 重大度：中（動作確認不能）
- ステータス：OPEN
- 検出日：2026-05-05
- 検出経路：メイン会話からの動作確認指示に対し `Agent({ subagent_type: "programmer" })` 呼び出しが「Agent type 'programmer' not found」で失敗
- 再現条件：
  - 対象ファイル：`/workspaces/claude/.claude/agents/programmer.md`（git 未追跡 / セッション開始後に追加）
  - 利用可能エージェント一覧に `programmer` が含まれない
- 原因（推定）：
  - Claude Code はセッション開始時にエージェント定義を読み込み、実行中はホットリロードを行わない
  - `programmer.md` はセッション開始後に作成されたため、現セッションでは認識されない
- 切り分け根拠：
  - 定義ファイルの YAML frontmatter 形式は登録済みの `coding-executor.md` と整合（`name` / `description` / `model` / `color` / `skills` 各フィールドに不足なし）
  - `tools: [Read, Write, Edit, Glob, Grep, Bash]` 配列構文は許容形式
  - `model: sonnet[1m]` も登録済みエージェントと同一値
- 対策案：
  1. Claude Code セッションを再起動し、エージェントレジストリを再読込する
  2. 再起動後に再度 `Agent({ subagent_type: "programmer" })` を呼び出して動作確認する
  3. それでも未登録の場合は、定義ファイルの差分（`spec-driven-developer.md` との比較等）から原因を再調査する
- 備考：
  - 定義ファイル本体は git 未追跡（`spec-driven-developer.md` / `spec-driven-orchestrator copy.md` も同様）。セッション再起動前にコミット要否をユーザーに確認することを推奨

## ISSUE-002

- 概要：moving_averages 追加時に `min_value(periods,2)` 違反が発生し指標を追加できない
- 重大度：中（新指標が使用不能）
- ステータス：RESOLVED
- 検出日：2026-06-10
- 検出経路：UI で移動平均線を追加するとエラー表示（違反 `min_value(periods,2)`）
- 原因：`periods`（FLOAT_LIST=`[20,50]`）に MIN_VALUE 制約を付与したが、`constraint_eval.js:evalMinValue` はスカラ前提で、配列を `[20,50] >= 2`（= `NaN >= 2` → false）と評価し常に違反になる。リスト対応しているのは `RANGE_OPEN` のみ（profit_band の probabilities が使用）。
- 対策：`periods` から MIN_VALUE 制約を除去（共有評価器 JS/Python パリティには手を入れない）。period<2 はバックエンド `add_moving_averages` が描画対象外に除外する（`test_adapter_moving_averages_skips_out_of_range_periods` で担保）ため、UI ヒント `min:2` で誘導し相関制約は付けない。
- 検証：`evaluate(def.params, 既定値)` の違反が 0 件であることを確認。両テストスイート緑。

## ISSUE-003

- 概要：moving_averages の期間を 252 等（静的 pcts 未収載値）に設定すると移動平均線が消える
- 重大度：中（任意期間が使用不能）
- ステータス：RESOLVED
- 検出日：2026-06-10
- 検出経路：UI で期間を 252 に設定すると線が消失（コンソールに `[F3] 系列名不一致のためスキップ name=SMA 252`）
- 原因：F3 系列名照合（`indicator_controller._expectedSeriesNames`）が `seriesNamePattern` の静的 pcts（`5,9,…,200`）だけを期待集合とし、backend が返す `SMA 252` を未知名として破棄していた（任意期間に未対応）。
- 対策：`_expectedSeriesNames`/`_expandPattern` を params 対応に拡張（`bucketsFromParam=ma_types`/`pctsFromParam=periods`・`bucketsUpper`/`pctsInt`）。期待集合を現在の params から動的生成し、任意期間を許容。`_validateSeriesNames`/`_draw` に params を伝搬（3 呼出点）。共有 Python 評価器は不変（F3 はフロント固有）。静的 buckets/pcts は params 未供給時のフォールバックとして温存。
- 検証：period=252 で `_expectedSeriesNames` が `SMA 252` を含み F3 通過。サーバ /compute が `SMA 252`（149 点）を返却。両テストスイート緑（JS 180 / Python 141）。

## ISSUE-004

- 概要：同じ指標を2本追加し、2本目のパラメータを設定してOKすると1本目のパラメータがリセット（汚染）される
- 重大度：高（複数インスタンスのデータ汚染・ユーザ設定の喪失）
- ステータス：RESOLVED
- 検出日：2026-06-11
- 検出経路：UI で同一指標を2本設定し、2本目を編集→OK すると1本目の値が変わる（ユーザ報告）
- 再現条件：永続化された適用済みインスタンスが存在する状態でページをリロード後、同一指標を再追加して編集する（単一セッション内では非再現）
- 原因（2系統）：`instanceId=`${id}#${seq}`` の衝突。`facade.recompute` は `findIndex` で最初の一致を返すため、衝突時は2本目編集が1本目を書き換える。
  - (1) `indicator_controller.restore()`（L266）が `seqCounters: {}` を強制し、seq 採番カウンタを永続化/復元しない（`_persistAll` も保存しない）。リロード後に同一指標を再追加すると `facade.nextSeq` が 1 から再採番し既存と衝突。
  - (2) 上記バグ版が既に同一 `(indicatorId, seq)` を複数 localStorage 保存しており、復元時点で instanceId が重複（カウンタ補正だけでは治らない既存破損データ）。これが「修正後・リロードしても再発」の主因。
- 切り分け根拠：facade/コントローラ実体の再現スクリプトで、(1) リロード後再追加の衝突（2本目編集で1本目が上書き）、(2) 重複 seq=1 で保存された破損データの復元で2本目編集が1本目（length:50→77）を破壊することを確認。修正後はいずれも一意 instanceId で1本目維持。
- 対策：`facade.deserialize` を強化。復元順に重複/不正 seq を「当該指標の最大 seq+1」へ**再採番して instanceId を一意化（既存破損データの治癒・冪等）**し、最終最大 seq まで `seqCounters` を底上げ（永続カウンタ未保存でも自己回復・単調性維持）。共有 Python 評価器・コントローラ・ダイアログ・レンダラは不変。
- 検証：回帰テスト3件追加（applied からのカウンタ復元・永続値との max・重複 seq 治癒）。Web テスト 187 件緑（既存 roundtrip 含む）。再現スクリプトで修正前後の挙動差を確認。
- 注意：既存破損データは復元時に in-memory 治癒され、次回のユーザ操作（apply/recompute/toggle）で `_persistAll` により正しい instanceId が永続化される。即時反映にはブラウザのハードリロード（ES モジュールキャッシュ破棄）が必要。

## ISSUE-005

- 概要：`from common import level_colors` が関数でなくサブモジュールを束縛し、利用6指標のテストが TypeError で失敗
- 重大度：高（profit_adx_needle / profit_arctan / profit_oscillator / profit_oscillator2 / profit_rmm / profit_volatility の lwc_chart・plot が動作不能）
- ステータス：RESOLVED
- 検出日：2026-06-12
- 検出経路：17指標の一斉 pytest 実行（6パッケージで `TypeError: 'module' object is not callable`）
- 原因：`common/level_colors.py` は追加済みだが `common/__init__.py` が `level_colors` 関数を再エクスポートしていない。Python は `from common import level_colors` で属性が無い場合サブモジュールを束縛するため、呼び出し時に TypeError となる（`level_colors.py` の docstring は `from common import level_colors` を公開 API として宣言しており再エクスポート漏れは明示バグ）。
- 対策：`common/__init__.py` に `from .level_colors import level_colors` を追加し `__all__`・docstring の公開 API 一覧へ追記。
- 検証：全17指標パッケージで pytest 緑（計573件 passed・failed 0）。common/tests も17件緑。残課題：`level_colors` 自体の単体テストが common/tests に未整備（利用側6指標のテスト経由でのみ検証）。

## ISSUE-006

- 概要：17指標パッケージの pytest 一斉実行が不可（テストモジュール名・`src` パッケージ名の衝突）
- 重大度：低（各パッケージ個別実行は全緑・CI 一括実行のみ阻害）
- ステータス：OPEN
- 検出日：2026-06-12
- 検出経路：`pytest indigators/<17パッケージ>` の一括指定で 45 件の collection エラー
- 原因：(1) 各 tests/ に `__init__.py` が無く同名 test_core.py 等が衝突（profit_hl_band のみ有り）。(2) 各テストが `sys.path` 挿入＋`from src import` 方式のため、`sys.modules["src"]` が最初のパッケージで束縛され他パッケージと衝突。`--import-mode=importlib` でも (2) は解消しない。
- 対策案：パッケージ毎に pytest を個別起動する CI スクリプト（for ループ）を正とする。import 方式の再設計（src→一意名）は破壊的変更を伴うため実施しない。

## ISSUE-007

- 概要：`.git/index` が「index 2」へ改名され、指標10パッケージに " 2" 付き重複ファイル139個が出現（外部ファイル同期の競合複製と推定）
- 重大度：高（git index 喪失により全ファイルが削除ステージ扱い・pytest が重複テストを二重収集）
- ステータス：RESOLVED
- 検出日：2026-06-12
- 検出経路：コミット完了直後の検証で `git ls-files` が 0 件を返却
- 原因：リポジトリ外部のプロセス（ホスト側ファイル同期と推定）が `.git/index` を「index 2」へ改名し、同時に追跡対象ファイルの「名前 2.拡張子」形式の複製を作成。git は index 不在により空 index を生成し全ファイルが staged-delete 表示となった。
- 対策：「index 2」を `.git/index` へ復名（コミット・作業ツリーは無傷）。139個の重複全数を `cmp` で原本と照合し**全件バイト一致**を確認のうえ削除（原本は git 管理下のため完全可逆）。
- 検証：`git status` 正常化（残差分は .claude 配下のみ）。profit_rsi/profit_stc/profit_volatility のテスト件数が正規値（43/31/39）へ復帰。
- 注意：同一ボリュームで再発し得る。再発時は `.git/index*` の改名有無を最初に確認すること。
