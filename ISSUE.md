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

## ISSUE-008

- 概要：lwc_chart アダプタが実 lightweight_charts の `horizontal_line()` に存在しない kwargs（`price_label`/`price_line`）を渡し、実機で TypeError（15指標中14で水準線描画不能）
- 重大度：高（lwc 実機での σ 水準線描画が全滅。本タスクの主目的に直結）
- ステータス：RESOLVED
- 検出日：2026-06-12
- 検出経路：動作確認（xvfb + 実 lightweight-charts でのスクリーンショット検証）で profit_rsi が TypeError
- 原因：実 API は `horizontal_line(price, color, width, style, text, axis_label_visible, func)` で `price_label`/`price_line` を受けない（これらは `create_line`/`create_histogram` 専用）。単体テストの Fake チャートが `**kwargs` で任意引数を受けるため検出不能だった。
- 対策：`horizontal_line` 呼び出しの `price_label=False` を `axis_label_visible=False` へ置換、`price_line=False` は削除（同概念なし）。対象14ファイル＋Fake テスト期待値2件＋docstring 4箇所を追従修正（profit_rmm_macd は水平線なしで対象外。create_line/create_histogram の price_line/price_label は実 API が受けるため不変）。
- 検証：全17パッケージ unit テスト緑。実 lightweight-charts（xvfb + WebKitGTK）で全15指標のスクリーンショット取得に成功（`indigators/.lwc_verify/out/`）。サブチャートのヒストグラム per-bar 着色（level_colors）・σ水準線・overlay 8本バンドの実描画を目視確認。検証スクリプトは `indigators/.lwc_verify/run_one.py`。

## ISSUE-009

- 概要：indicator_ui の B方式サーバで profit_oscillator / profit_volatility を計算すると `ImportError: cannot import name ... from 'src.core'`（16バインディング中14は正常、この2件のみ計算不可）
- 重大度：中（指標管理 UI で当該2指標のみ追加時に backend_unavailable。他14指標は表示可能）
- ステータス：RESOLVED
- 検出日：2026-06-12
- 検出経路：全15指標を UI 登録する作業中、IndicatorComputeAdapter.compute を全16バインディングへ実行した検証で2件が backend_unavailable
- 原因：`profit_oscillator/src/oscillator.py:26` と `profit_volatility/src/volatility.py:27` が相対 import ではなく**絶対 import `from src.core import ...`**（＋`sys.path.insert(0, parents[1])`）を使用。top-level 名 `src` は全指標共通のため、先行ロード済みの別指標の `src.core` が `sys.modules` に束縛されると、当該指標の定数（DEFAULT_PERIOD_A / compute_volatility_full）を持たない core を誤参照し ImportError。他14指標は `from .core import`（相対）で `_<indicator>_src` 名前空間に閉じ衝突回避済み。ISSUE-006 の `src` 名衝突の未修正残存。
- 対策案：(A) 当該2サブモジュールの `from src.core import` → `from .core import`（相対）へ是正（2ファイル各1行・最小）。ただし指標 src は設計上 read-only かつ各指標の単体テストが `from src` 前提で sys.path を張る可能性があり、テストへの波及確認が必要。(B) ローダ側で `src` 別名を指標ごとに再束縛（衝突回避をアダプタへ寄せる）。いずれもアーキテクチャ判断のためユーザー承認後に実施。
- 対策（採用 A・ユーザー承認済み）：`profit_oscillator/src/oscillator.py`・`profit_volatility/src/volatility.py`・`profit_arctan/src/arctan.py` の `from src.core import` を `from .core import`（相対）へ是正し、不要になった `sys.path.insert(0, parents[1])` と `import sys`/`from pathlib import Path` を除去（健全な profit_rsi と同構造へ統一）。※当初検出は2件だったが全数 grep で profit_arctan も同種の潜在バグ（ロード順依存で破綻）を保持していたため同時是正。テスト側は自前で `sys.path.insert`＋`from src import` するため、サブモジュールが `src.<mod>` として読まれる経路では相対 import が `src.core` に解決し従来どおり機能する。
- 検証：3パッケージ単体テスト緑（profit_oscillator 41 / profit_volatility 39 / profit_arctan 37）。adapter を「他指標を先行ロード→対象件」の衝突誘発順序で実行し全件 OK。adapter/controller/smoke 61 緑。B方式サーバ再起動後の実 HTTP `/compute` で対象件が histogram＋水準線を返却。**全16バインディングが計算可能（OK 16/16）**。
- 注意：他指標に同種の絶対 `from src.X` が無いか定期確認（`grep -rnE "^\s*from src" indigators/*/src` で 0 件を維持）。是正後の現時点で 0 件を確認済み。

## ISSUE-010

- 概要：price_range_power が欠陥A（全長統計＝look-ahead/履歴長依存）＋欠陥B（生の絶対ヒゲ幅＝価格水準依存）の両方を持つが、是正（因果窓＋比率化）が未実施。
- 重大度：中（既定描画が repaint＋価格水準依存。他 band 系指標は是正済みだが本指標のみ未対応）
- ステータス：OPEN
- 検出日：2026-06-14
- 検出経路：band 系4指標の横断監査（並列エージェント監査）で both（A+B）該当と判定。
- 原因：
  - 欠陥A：`wick_stats` の平均・標準偏差が系統サンプル全体（全長）で算出（`src/core.py:227-235` の `valid.mean()` / `valid.std(ddof=1)`）。σ 閾値 a1/a2/a3 も全期間スカラ（`core.py:236`）。`range_from`/`range_to` 既定が全期間 `np.nanmin(low)`/`np.nanmax(high)`（`core.py:295-297`）で価格帯境界も未来を含む。各バーの度数分類（`_sigma_bins`, `core.py:254-259`）がこの全期間スカラ閾値を全バーに適用 → repaint。
  - 欠陥B：中核がヒゲの**生の絶対値幅**（`high-close` / `open-low` / `high-low`：`core.py:205-208`）。z 化・÷価格・%化なし。平均・σ 閾値も同じ価格点単位の絶対量。
- 対策案：他 band 指標と同様の因果窓（全長統計→直近 W 本）＋比率正規化（ヒゲ幅÷価格 or ÷ATR）。ただし price_range_power は「価格帯ビン集計＋度数比率」という独自構造のため、再設計の影響範囲が大きい。**ユーザーのロジック再検討を経て対応方針を決定**し、承認のうえ別途 6 フェーズ（git→architecture→tdd→programmer→code-review→git）で実施する。
- 備考：監査で profit_band / profit_hl_band は是正済み（develop 統合）。profit_hlband / profit_oscillator2 はユーザー指示で除外。本指標はロジック再検討後に着手判断する。
- 有効性検証（2026-06-14・日経225日足で実証）：「σ分類×価格帯比率」は有効な信号でなく、素の価格滞在ヒストグラム（出来高プロファイル相当）に**劣る**ことを確認。
  1. 予測的有効性（支持抵抗）：高 power 価格帯が反転点を捉えるか → `corr(power total, 反転点数) = −0.52`（負）。素の価格滞在数は `+0.99` で反転をほぼ完全予測。σ分類は価値を足さず、素のヒストグラムより**悪化**させる。
  2. denominator 由来の偽信号：`corr(power total, 素の滞在数) = −0.55`。比率の分母（帯内総数）により低滞在帯が過大評価される構造的アーティファクト。
  3. σ分類の統計的破綻：ヒゲ幅は歪度 +2.6〜+3.3 の強い右裾。avg±σ 閾値は不適で `>3σ帯(2.2%) > 2-3σ帯(1.5%)` と rarity 順序が逆転（正規前提が崩壊）。
  4. 出力の希薄性：ratio セルの **69.9% が未定義(NaN)**。total は少数セルの寄せ集め。
  - 含意：causal+ratio 是正（欠陥A/B）は**有効性ゼロの指標への投資**となり費用対効果が無い。是正着手の前に**存続可否**を判断すべき。是正方針の候補：(a) 廃止候補として記録／(b) σ分類を捨て**素の価格滞在プロファイル**へ再設計（分母アーティファクトとσ破綻を同時除去）／(c) 現状維持＋「有効指標でない」と明記。
  - 留保：単一銘柄・単純な反転定義での検証。ただし「σ分類が素の滞在数に負ける」「σビン順序破綻」「70%未定義」は構造的問題で銘柄非依存と判断。

## ISSUE-011

- 概要：profit_mfi_macd の lwc_chart が histogram/line 系列の warm-up NaN を dropna しない潜在バグ（warm-up NaN を生む params 下で `float(NaT)` crash の恐れ）。根本は共有 `fake_chart._line_points` の `iterrows` が「datetime の time 列＋NaN 値列」の行を datetime64 と推論し NaN→NaT へ強制変換する脆弱性。
- 重大度：低〜中（既定 params では未発火＝潜在。`add_mfimacd` は window 標準化を持たず warm-up NaN を生まないため現状 crash しない。NaN を生む param 構成で同型 crash）。
- ステータス：OPEN
- 検出日：2026-06-16
- 検出経路：Latest 増分計算 Stage B の21指標並列検証で profit_rmm_macd の同型 crash（既定 window=120）を修正した際、code-review が構造同型の profit_mfi_macd を反例として検出。
- 原因：`indigators/profit_mfi_macd/src/lwc_chart.py:135,149` が `hist.set(...)` / `line.set(...)` を dropna なしで呼ぶ。値列に NaN があると `_line_points`（`indicator_ui/api/adapter/compute/fake_chart.py:31-33`）の `pd.DataFrame.iterrows` が warm-up 行を datetime64 推論し NaN→NaT 化、`float(NaT)` が TypeError。
- 対策案（いずれか）：(a) 局所修正＝profit_mfi_macd の histogram/line を profit_rmm_macd と同様 `dropna(subset=[...])` してから set（仕様§1.3・姉妹整合）。(b) 根本修正＝`_line_points` を `iterrows` でなく列単位 zip 走査へ変更し非有限値をスキップ（全指標の同型 crash を一括防止・契約「呼び元 dropna 済み」は維持しつつ防御化）。承認のうえ別途実施。
- 備考：profit_rmm_macd は本検出時に局所修正済み（dropna・回帰テスト添付・develop 統合予定）。本 ISSUE は未発火の潜在分（profit_mfi_macd ＋ 共有 _line_points 脆弱性）を対象とする。

## ISSUE-012

- 概要：`rollup_builder.incremental_update` が毎 tick で各 TF ロールアップ全体を Python 辞書化して全書き直しするため RSS が 618MB まで急騰（OOM 再発リスク）
- 重大度：高（本機能の目的＝OOM 回避を毀損。--watch 常駐プロセスが ~670MB free 環境で 618MB スパイク）
- ステータス：RESOLVED
- 検出日：2026-06-17
- 検出経路：🟡-3（1 分足全件スキャン）修正後に --watch を再起動し RSS を実測。1 分足読みは probe 化で解消したが、tick ごとに RSS 173MB→618MB のスパイクが残存
- 再現条件：
  - 対象：`indigators/indicator_ui/tools/rollup_builder.py` の `incremental_update`
  - 実データ規模（5m ロールアップ ≈90 万行 / 63MB）で `_read_existing_rollup` が `df.iterrows()` で 90 万件の dict-of-dict を構築
- 原因：`_read_existing_rollup`（iterrows→OrderedBars 辞書化）＋ `_write_rollup`（辞書→CSV）が O(ロールアップ全体)。90 万件の小辞書がオブジェクト・オーバーヘッドで数百 MB を占有
- 対策（実施済み）：incremental_update を DataFrame ベースへ変更。`new_df = resample_ohlc(tail_df, rule)`、`cut=new_df.index.min()` で既存を「未変更 prefix（< cut）」と「形成中 overlap（>= cut・最大 1 本）」に分割。overlap＋new を groupby agg（open=first/high=max/low=min/close=last/volume=sum＝`merge_same_period` 同値）でマージし、prefix と concat して `_write_rollup_df` で原子書き出し。過去確定バーは再集計せず値コピーのみ（「過去の確定済みは再計算しない」原則を遵守）。peak 618MB→~130MB。回帰は `test_incremental_update_matches_full_resample_after_append`（==resample_ohlc(全件)）＋ memory-bounded テストで担保
- 備考：ファイル I/O 自体は O(全体)（全行 rewrite）が残るが、最大 5m=63MB を ~1s で書くため許容。さらに末尾行のみ in-place truncate-append する真の O(新規) 化は将来改善（🔵）
- 追記（実装済み）：上記 🔵（真の O(新規) 化）を実装。incremental_update は probe が形成中期間を内包する TF（5m〜1D 等）で「末尾バーを probe から再計算→末尾だけ truncate+append」し、過去確定足の read/write を行わない（probe 不足の 1M 等は従来の全件 rewrite へフォールバック）。実測: 965k 行ロールアップへ 1 tick が 7500ms/119MB → 44ms/0.2MB。形成中バーは再計算（マージでなく上書き）のため書込中 crash 後の再処理が冪等。回帰: 履歴 prefix バイト不変・再処理冪等・iterrows 対象は末尾 suffix のみ・peak<40MB（全件退行検知）

## ISSUE-013

- 概要：バックテスト統計仕様 BACKTEST_METRICS.md 内で Sharpe/σ の固定値（§12）が算出式（§1.2/§11）と矛盾。加えて §11 Z-Score ヘルパーに sqrt 欠落バグ
- 重大度：中（compute_stats=UC-002 の MT5 突合精度に影響。コードは式優先で確定済みだが、実 MT5 値が §12 の 0.17 側なら突合不一致の恐れ）
- ステータス：RESOLVED（2026-06-19・equity 系 5 関数を compute_stats() 本体へ結線し engine 実走 equity_curve で reconcile 突合完了。Sharpe(clamp 一致・下記注)/recovery(tick 粒度残差)/equity-DD(abs 一致・max は tick 粒度~25 残差) を残差明示で決着。後述「決着(2026-06-19)」参照）
  - **注（Sharpe クランプは仮説・出典TBD）**：MT5 STAT_SHARPE_RATIO=-5.000000(6桁) に対し、per-trade `mean/std×√N` の素値 -5.0838 を [-5,5] にクランプして一致。クランプ [-5,5] は観測値を説明する仮説で、MT5 公式/ソースでのクランプ仕様は未確認。後続で一次情報確認を要す（確認でクローズ確定、否なら式再検討）。
- 検出日：2026-06-17
- 検出経路：backtest usecase 層 TDD（compute_stats を METRICS §12 の10トレード期待値で固定する過程）。tdd-executor が3独立手法で実測
- 内容：
  - (1) §12.2/§12.6 は Sharpe=0.17・σ=0.020019 と記載するが、§1.2/§11 の式（ddof=0 母分散）からは σ=0.018362・Sharpe=0.1862 となり再現不能。§12 のその他 STAT_*（PF=1.5593/EP=33/RF=0.9429/DD=350・3.38%/Z=1.3416/連勝連敗/件数）は式と完全一致
  - (2) §11 の Z-Score ヘルパーは sqrt が欠落しており §3.2 数式と不一致。§3.2 数式採用で §12 期待値 1.3416 に一致
- 対策（暫定・実施済み）：「式を一次情報とする」方針に従い Sharpe=0.1862・Z=1.3416（§3.2 式）で実装・固定。回帰テスト添付済み
- 未解決点（要ユーザー確認）：実 MT5 STAT_* の σ 定義（母分散 ddof=0 か標本分散 ddof=1 か）と Sharpe 基準。Section 5 integration で実 MT5 突合時に §12 記載値 0.17 の出所を確定し、必要なら式 or 仕様書を改訂
- 追記（2026-06-17・usecaseレビューで深掘り）：Sharpe の「収益率基準」自体も一次情報間で矛盾。METRICS §1.2 は balance-HPR・ddof=0・非年率を規定する一方、PROCESS §6.1/§7-#9 は equity・単純収益率・足ベース・ddof=1・年率係数√A を規定。現実装は「式優先」方針により METRICS §1.2 を採用。doc 側でどちらを正とするか（MT5 STAT_SHARPE_RATIO の実定義）を Section 5 実 MT5 突合時に確定し統一する。
- 決着（2026-06-18・実 MT5 golden 突合 TBD-A／feature/backtest-mt5-stats-calibration）：
  fixture `simulator/tests/fixtures/mt5_outputs/report_900005560.json`（MA_Slope_EA / JP225 M1 / 1163 確定トレード）の
  `deals`(dir="out") から再構成したトレード/balance 列で `compute_stats` を実 MT5 `results` に突合
  （新規 golden: `simulator/tests/unit/test_compute_stats_golden_mt5.py`・許容 金額±0.5/比率±1e-4/件数一致）。
  - **一致確定（19 STAT_*・golden で固定）**：net(-6169)/gross_profit(10506)/gross_loss(-16675)/PF(0.630045)/
    expected_payoff(-5.304385)/total_trades(1163)/profit_trades(292)/loss_trades(871)/long(582)/short(581)/
    largest_profit(245)/largest_loss(-130)/avg_profit(35.979452)/avg_loss(-19.144661)/max_con_wins(4)/
    max_con_losses(17)/Z-Score(2.35)/AHPR(0.9992)/balance_dd_abs(6169)/balance_dd_max(6476・62.83%)。
  - **確定した実 MT5 定義（実装を校正）**：
    (a) profit_trades = count(pnl>=0)（**ゼロ損益を勝ちに数える**。従前 pnl>0=217→292 へ）。
        avg_profit = gross_profit / profit_trades(>=0)。profit_long/short も pnl>=0 基準。
    (b) Z-Score = (N*(R-0.5) - P) / sqrt(P*(P-N)/(N-1)), P=2WL, W=count(pnl>=0), R=ラン数(pnl>=0/<0 の2値)。
        → §3.2 の (R-E(R))/sqrt(Var(R)) 形は実 MT5 と再現せず（§12 で 1.3416 vs 本式 1.6771）。**実 MT5 を正**とし
        §12 回帰テストを 1.6771 へ更新（test_z_score_matches_mt5_formula・理由コメント付）。
    (c) AHPR/GHPR = HPR_i = B_i/B_{i-1}（= 1 + profit_i/balance_before_i と算術的に同値。既存式で一致確認）。
    (d) 連勝/連敗（max_con_wins/losses=4/17）は従前の「ゼロ=ラン中立」ロジック（win=pnl>0）を**維持**（counts の ≥0 とは別ルール）。
  - **未決・golden 除外（要バー別/ティック別 equity・fixture に欠落のため捏造回避）**：
    STAT_SHARPE_RATIO(-5.0)＝バー単位 equity 収益率ベースと推定（トレード列の (AHPR-1)/σ では -0.13 で不一致）。
    STAT_RECOVERY_FACTOR(-0.935547 = net/EquityDD_max(6594))＝MT5 は **EquityDD 基準**。本実装 recovery は Balance DD 基準のまま据え置き。
    STAT_EQUITY_DD(6594)/EQUITY_DD_abs(6174)＝ティック別含み損ピーク要。
    → これらは将来バー別/ティック別 equity 系列を `compute_stats` に供給できる段で再校正する（残存リスク）。
  - **§12 Sharpe 期待値（0.1862）据え置き**：Sharpe 定義を変えていない（バー別 equity 要で保留）ため整合。
  - テスト結果：`python -m pytest simulator/tests/ -q` = 363 passed（baseline 342 + golden 21・§12 Z テスト 1 件は更新の上 pass）。
- 決着/未決の切り分け（2026-06-18・usecase レビュー 🟡-2／feature/backtest-mt5-stats-calibration）：
  本 ISSUE を RESOLVED から **PARTIALLY-RESOLVED** に是正。決着済みと未決を明確に分離する。
  - **決着済み（実 MT5 golden で固定）**：ゼロ=勝ち件数定義（profit_trades=292）/avg_profit・avg_loss/PF(0.630045)/
    Balance DD($6476・62.83%・abs6169)/Z-Score(2.35・MT5 式)/AHPR(0.9992)/連勝連敗(4/17)/件数(1163/871/582/581)。
  - **未決（要バー別/ティック別 equity・fixture 欠落のため捏造回避し golden 除外）**：
    (1) STAT_SHARPE_RATIO(-5.0)＝バー単位 equity 収益率ベース要。本実装は METRICS §1.2（balance-HPR）式に留置。
    (2) STAT_RECOVERY_FACTOR(-0.935547)＝MT5 は **Equity DD 基準**かつ符号も逆（net 負/EquityDD で負値）。
        本実装 recovery_factor は **Balance DD 基準**のまま据え置き（トレード/balance 列で算出可能な範囲）。
    (3) STAT_EQUITY_DD($6594・63.28%)/EQUITY_DD_abs(6174)＝ティック別含み損ピーク要・未充填。
  - 再開条件：バー別/ティック別 equity 系列を `compute_stats` に供給できる段で (1)〜(3) を再校正する。
  - 🔴 派生是正（同レビュー）：average_consecutive_wins/losses が件数系 profit_trades(pnl>=0) を分子に
    誤流用（§4.3 違反・impl 1.57 vs 正 1.17）していた点を「win/loss ラン内件数 N/K」に修正し回帰テスト添付。
    併せて is_count_win(pnl>=0)/is_run_win(pnl>0) を明示述語に分離し二重基準の誤流用を構造的に防止。
- 決着（2026-06-19・結線＋reconcile 実走突合／feature/backtest-equity-stats）：
  PARTIALLY-RESOLVED の未決 3 項目 (1)〜(3) を、単体検証済の equity 系 5 関数を `compute_stats()` 本体へ
  結線し、`test_ma_slope_reconcile.py` の engine 実走 equity_curve（bar 別 `account.equity`・28096 点）で
  突合して決着した。**全項目で実 MT5 と一致（残差を正直に明示）**：
  - **STAT_SHARPE_RATIO = -5.0（完全一致）**：`sharpe_ratio_per_trade`（per-trade pnl 系列の
    (mean/std(ddof=0))×√N＝素値 -5.08 を [-5,5] にクランプ）を `compute_stats().sharpe_ratio` へ結線。
    旧 HPR 版 `sharpe_ratio()`（METRICS §1.2・0.1862）は残置（§12 用途）。
  - **STAT_RECOVERY_FACTOR**：`recovery_factor_equity`（符号付き net / equity_dd_max）を結線。
    engine 実走 = **-0.93987**（net -6173.9 / equity_dd_max 6568.9）。MT5 = -0.935547。
    残差 ~0.0043 は net・DD の tick 粒度残差由来（**bit-exact ではない**・残差明示で決着）。
  - **STAT_EQUITY_DD_abs = 実走 6173.9（MT5 6174.0・残差 ~0.1・実質一致）**：`equity_dd_absolute` を結線。
  - **STAT_EQUITY_DD_max = 実走 6568.9（MT5 6594・残差 ~25）/ 63.19%（MT5 63.28%・残差 ~0.1）**：
    `equity_dd_maximal`/`_percent` を結線。残差 ~25 は **bar 解像度の限界**（bar 内の含み損ピークを
    捕捉できず MT5 のティック解像度 DD に届かない・既知の現実的残差）。
  - **BacktestStats に equity_dd_abs/max/max_percent を default 付きで追加**（既存構築と後方互換）。
    equity_curve 未供給時は equity 系 0・recovery は balance 基準へフォールバック（後方互換）。
  - **既存テスト更新（実 MT5 整合の正当更新・Z-Score 校正と同方針）**：
    `test_compute_stats_returns_backteststats_matching_metrics_12_6` の sharpe_ratio 期待値を
    0.1862（HPR 版）→ 0.560523（per-trade 版・equity==balance 合成下）へ更新。recovery は equity==balance
    のため 0.9428 で従来と一致（更新不要）。balance 系 STAT_* は全て不変。
  - **トートロジー解消**：`test_compute_stats_golden_mt5.py` の逆算3点 curve（`_MT5_EQUITY_CURVE`）による
    equity-DD golden 3 件は入力で出力を逆算するトートロジーのため撤去。非トートロジー突合は
    (a) equity-DD = integration `test_ma_slope_reconcile.py::TestMaSlopeEquityStatsReconcile`（engine 実走）、
    (b) recovery = MT5 約定列 net × MT5 オラクル dd_max(6594) の合成、(c) equity-DD 関数の純粋性は
    独立計算の単体テストへ移譲。
  - テスト結果：`python -m pytest simulator/tests/ -q` = **449 passed**（baseline 442 + 結線 unit 4 + reconcile
    integration 5 − トートロジー golden 3 + 純関数性単体 1 = 449・退行なし）。
  - 残存（将来）：bar 解像度の equity-DD max は MT5 ティック解像度に ~25 届かない構造的残差。完全一致には
    tick 別含み損ピークの再構成が必要（本 ISSUE の射程外・必要時に別 ISSUE 起票）。

## ISSUE-014

- 概要：tick_model の許容値表記が一次情報間で不一致。PROCESS §7 #1 は「全ティック/OHLC4展開/始値のみ」（正準＝every_tick/ohlc_expand/open_only）だが、DESIGN §7.2:290 のドメインモデル・スケッチは `Literal["ohlc_simulate"]` と別名
- 重大度：低（config_loader 実装は PROCESS §7 正準名に準拠＝正しい。将来 Engine 側が DESIGN §7.2 名を期待すると不整合になる潜在リスクのみ）
- ステータス：OPEN
- 検出日：2026-06-17
- 検出経路：backtest framework 層 config_loader のコードレビュー（重点観点2 の許容値照合）
- 対策（要文書修正）：DESIGN §7.2 のスケッチ値 `ohlc_simulate` を PROCESS §7 の正準名（every_tick/ohlc_expand/open_only）へ追従更新する。コードは現状維持で可

## ISSUE-015

- 概要：バックテスト #4 Band 戦略（pOL/pOH 四分位バンド依存）をスコープ外（descope）として確定
- 重大度：低（Phase1 経路＝#1 TC24051901・#5 PRO!fit_Band は実装済で影響なし。#4 のみ非対応）
- ステータス：CLOSED（WONTFIX・原典不在のため再現不能）
- 検出日：2026-06-17
- 経緯：CLEAN_ARCH §13 TBD「#4 Band 指標ソース（28バッファ四分位）」。Band.ex5 のみで .mq5 不在、pOL/pOH 算出式が不明のため完全再現不可。ユーザー確認の結果「Band.mq5 は存在しない」と確定 → #4 をスキップ
- 影響：simulator/adapter/strategy に #4（Band 依存）戦略・E-PendingOrder(#4 専用 BuyLimit)・Band 指標は実装しない。設計上 #4 依存は adapter 層に局所化済みのため domain/usecase/framework/main・他戦略への波及なし
- 再開条件：原典 Band.mq5 もしくは pOL/pOH の算出仕様が将来入手できた場合のみ再検討

## ISSUE-016

- 概要：every-tick + real_ticks の end-to-end 経路で、CSV 由来 bar.time が ISO 文字列のとき RealTickModel が TypeError で落ちる。`_bar_end(bar.time)` が str を扱えず `bar_time + 60`（str+int）で `TypeError: can only concatenate str (not "int") to str`
- 重大度：中（real_ticks 経路かつ CSV 由来 bars に限定。既定 bar-mode・他 tick_model は bar.time に算術しないため無影響。smoke が通ったのは実 Dukascopy bars が偶然 datetime64 だった＝CSV 経路は未検証だった）
- ステータス：RESOLVED
- 検出日：2026-06-19
- 検出経路：レビュー指摘 🟡-2（integration の end-to-end 値未固定）の TDD 化で、main 実経路を controller._interactor.execute まで走らせた際に顕在化
- 原因：CsvOHLCRepository._extract が time 列を「そのまま」採用するため、ISO 文字列 CSV では bar.time が str（Bar.time 契約 numpy.datetime64|int に対し loader 側が未正規化＝既存の committed 挙動）。bar-mode は bar.time に算術せず比較のみのため顕在化しなかった
- 対策：committed CSV loader / bar-mode を変更せず、pandas を持つ adapter（RealTickModel）の区間算定でのみ bar.time を datetime64 へ正規化してスライスする（_bar_end も str/Timestamp を datetime64 化）。real_ticks 経路に局所化し既定経路不変
- 検証：🟡-2 end-to-end 値検証テストが entry/exit を tick 価格で固定して通過。既存全テスト不変

## ISSUE-017

- 概要：every-tick（real_ticks）の成行約定が「足内初回ティック価格」で約定していたため、実 MT5 every-tick（成行はバー open クォートで約定）と乖離。2026-01 突合で初回トレード価格・stop-out 日・トレード数が一致しなかった
- 重大度：中（real_ticks 経路の数値精度。既定 bar-mode・他 tick_model は無影響）
- ステータス：RESOLVED
- 検出日：2026-06-20
- 検出経路：2026-01 every-tick 数値突合（fixtures/mt5/ma_slope_jp225_202601 オラクル）。初回トレード ours buy 50580.8 vs MT5 sell 50390.8、stop-out ours 01-12 vs MT5 01-14、trades 932 vs 1444 の乖離
- 原因：`_execute_every_tick` が pending_orders をティック内側ループの「最初のティック bid/ask」で約定していた。実 MT5 は新規バー成行を「バー open クォート」（買い=open+spread×point、売り=open＝bar-mode と同一）で約定し、ティックは含み損/SL-TP/stop-out 評価にのみ用いる。初回ティックは ffill 復元値で open と数 pt ずれ、建値誤差が累積し equity 減衰が加速→stop-out が 2 日早発→トレード数が激減
- 対策：`_execute_every_tick` の成行約定を足境界で `derive_quotes(bar, entry_price_basis, point_size)` により bar open クォート約定へ変更（bar-mode と同一の建値ルール）。ティックは SL/TP/floating/stop-out 評価専用に。ティック0件足は新規バー未検知＝発注しない（既存仕様を維持）。突合は entry_price_basis="current_open" を併用
- 検証：bar-open fill 修正後の 2026-01 突合 → 初回トレード sell 50390.8（MT5 一致）、stop-out 01-14（MT5 一致）、trades 1446 vs 1444（+2・99.86%）、net -4598.8 vs -4649（差 50.2・1.08%）。unit/integration 全 511 passed（tick 価格約定を主張していた cycle2 distinguishing テスト3件＋integration1件を bar-open クォート約定へ是正）
- 残差（構造的・tick 解像度の床）：残 ±2 trades / ~50pt は、輸出 CSV の片側ティックを ffill 復元したクォートと MT5 内部の実ティック列の差に起因。Jan-14 stop-out 境界で僅かなクォート差が margin 割れ tick を前後させ ±2 trades・~50pt を生む。輸出 CSV のみからの bit-exact 一致は原理的に不能（MT5 内部 tick 列が必要）。実用上の到達点として確定

## ISSUE-018

- 概要：every-tick/bar-mode 双方で、市場閉鎖時間帯（週末境界）の成行をエンジンが約定してしまい実 MT5 と +2 トレード乖離。MT5 はジャーナルで当該成行を `[market closed]` 拒否し開場する次バーで約定していた
- 重大度：中（real_ticks/bar-mode 双方の数値精度。session_calendar 既定 "broker"→NullCalendar のため既定経路は不変）
- ステータス：RESOLVED（トレード数・系列は一致。残差は別要因 ISSUE-019）
- 検出日：2026-06-20
- 検出経路：2026-01 突合（02: 1分足OHLC・Delays=0／260620-02.txt）。ours/MT5 トレード列の forward-align で分岐点が週末境界 Fri 2026-01-09 23:59 / Mon 2026-01-12 01:00 と特定。MT5 journal に `failed market sell ... [market closed]`（23:59・01:00）と開場後 01:24 約定を確認
- 原因：エンジンに市場開閉判定が無く（session_calendar="none"/"broker"→約定可否に未反映）、閉鎖バーの成行を約定。MT5 は閉鎖中拒否→保有不変→開場バーで約定。MaSlope は保有側 level-trigger のため、MT5 は 23:59 sell 拒否で long 維持→01:01 の buy シグナルは「保有=long」で no-op→01:24 で sell 約定。こちらは 23:59 sell 約定→short 化→01:01 buy でドテン、と +2 トレード分岐
- 対策（clean-arch・config gated）：`SessionCalendarPort.closed_bar_indices(bars)->set[int]`（usecase/ports.py・事前計算）を追加。adapter/calendar に NullCalendar（既定・空集合＝byte-identical）/ Jp225SessionCalendar（日次プレオープン 01:00 以前閉鎖・金曜 23:55 以降閉鎖＝実 MT5 02 の拒否点と整合）。Interactor は constructor `session_calendar=None`（既定 Null 相当）で受け、bar-mode/every-tick 両経路の発注点で「閉鎖バーは新規成行（ドテン reverse 含む）を約定しない」。保有不変のため戦略が次開場バーで自動再発注。main は config.session_calendar=="jp225" のとき Jp225 を結線（既定 broker は Null）
- 検証：bar-mode + session_calendar="jp225" で 2026-01 突合 → trades 1444 vs 1444（完全一致）、初回 sell 50390.8（一致）、週末 +2 トレード消滅。trade-by-trade で 1443/1444 が建値・決済まで bit-exact（残る不一致は最終 stop-out 1 件のみ＝ISSUE-019）。unit/integration 全 519 passed（既定経路 byte-identical 維持・カレンダー単体 7 件追加）

## ISSUE-019

- 概要：2026-01 突合の最終 stop-out 1 件のみ決済価格が乖離。ours 54019.2（pnl -10）、MT5 53859.2（pnl -170）。差 160pt が net 残差（ours -4488.8 vs MT5 -4649）の全量
- 重大度：低（1444 件中 1 件の決済価格のみ。トレード列・建値は完全一致）
- ステータス：RESOLVED
- 検出日：2026-06-20
- 検出経路：bar-mode + jp225 カレンダーの trade-by-trade 突合で唯一の不一致として特定。当該バー（2026.01.14 23:54 O=54019.2 H=54019.2 L=53849.2 C=53859.2）を実データ確認し、ours 決済価格 54019.2=バー**始値**、MT5 53859.2=バー**終値**（=23:55 始値）と判明
- 原因（当初の「OHLC 安値基準」説を訂正）：bar-mode の stop-out 強制決済が、成行建値用に算出した `derive_quotes`（entry_price_basis="current_open"）の **bid=bar.open** を流用していた。一方 margin 割れの**判定**は含み損評価（update_floating_pnl→bar.close 基準）で行う。よって「**終値で割れたと判定しながら過ぎ去った始値で決済する**」非物理的な内部不整合があった（MT5 は割れ時点の現値＝終値で決済＝物理的に正しい）。安値 53849.2 は MT5 も使っておらず「OHLC 安値評価」は不要だった
- 対策：bar-mode の stop-out 強制決済価格を `account.mark_price(bar, side)`（update_floating_pnl と同一の評価価格＝margin 判定時点の現値。買い=Bid=close／売り=Ask=close[+spread]）へ是正。判定価格と決済価格を一致させた。Account に公開メソッド `mark_price` を追加（`_eval_price` へ委譲）。every-tick 経路の stop-out は元から到達ティック価格（割れ時点の現値）で決済しており不変。既定経路は entry/floating とも基準="close" のため mark_price==close==従来値で **byte-identical** 維持
- 検証：bar-mode + jp225 + 本修正で 2026-01 突合 → **trade-by-trade 0/1444 不一致（全建値・全決済が bit-exact）**、net -4648.8 vs MT5 -4649（残差 0.2＝MT5 レポートの整数丸め）、balance 5351.2 vs 5351、初回 sell 50390.8。unit/integration 全 522 passed（既定 byte-identical 維持・stop-out 決済価格の回帰テスト 1 件追加・既存 2025-01 突合不変）

## ISSUE-020

- 概要：2026-01 突合の最終残差 0.2 円（ours net -4648.8 vs MT5 -4649）。価格・トレード列は完全一致だが、約定損益の通貨精度の扱いが MT5 と異なっていた
- 重大度：低（0.004%。実用上は一致だが literal 一致でない）
- ステータス：RESOLVED
- 検出日：2026-06-20
- 検出経路：per-trade pnl 突合で 1444 件中 2 件のみ profit が乖離と特定（idx600 ours 200.4 vs MT5 200／idx1125 ours -6.2 vs MT5 -6）。MT5 の全 1444 約定 profit は整数（非整数 0 件）で、差の合計 +0.2＝net 残差の全量
- 原因：実 MT5 は約定損益を口座通貨の精度（JPY=0 桁）へ丸めて balance/stats に反映する。本エンジンは損益式 (exit-entry)×contract×lot の素値（0.1 円端数）を保持していた。値差が 0.1 刻みのため profit は X.0/X.2/X.4… になり得るが、MT5 は整数へ丸める
- 対策（config gated・既定 byte-identical）：domain/_shared に `round_profit(value, digits)`（digits=None は素値・指定時 half-away-from-zero）を追加。TradeRecord に `profit_round_digits`（既定 None）フィールド、Deal.from_close に同名引数を追加し、双方が round_profit を共有。Interactor は config.profit_round_digits（既定 None＝丸めず）を確定トレード生成点（_close_open_trade）で TradeRecord へ付与し、deal.profit（balance）と pnl（stats）を一致させる。config_loader/BacktestConfig に profit_round_digits（既定 None・0-8）を追加。既定 None では従来式の素値＝byte-identical
- 検証：bar-mode + jp225 + profit_round_digits=0 で 2026-01 突合 → **net -4649.0 vs MT5 -4649（差 0.0・literal 一致）**、balance 5351.0 vs 5351、per-trade pnl 0/1444 不一致（完全一致）。全 530 passed（round_profit 単体4／TradeRecord 丸め2／Deal 丸め1 の回帰テスト追加・既定 None の byte-identical 維持・既存 2025-01 突合不変）
- 到達点：ISSUE-017（every-tick 建値）→018（週末カレンダー）→019（stop-out 決済価格）→020（通貨丸め）の解消により、2026-01 は実 MT5 と **trades 1444・全建値・全決済・net -4649・balance 5351 まで literal bit-exact** に到達

## ISSUE-021

- 概要：Jp225SessionCalendar の日次クローズ時刻が「金曜 23:55 以降」に過剰適合（overfit）していた。2026-02 突合（260620-03 run5）で、MT5 が約定した金曜 2026-02-06 23:58 をこちらが誤って閉鎖扱いで拒否し +208 トレード乖離
- 重大度：中（real_ticks/bar-mode 双方の session 判定。session_calendar="jp225" 選択時のみ。既定 broker/none は無影響）
- ステータス：RESOLVED
- 検出日：2026-06-20
- 検出経路：260620-03 run5（2026-02・1分足OHLC）突合の forward-align。先頭885エントリ一致後、Fri 2026-02-06 23:58 sell をこちらが欠落（MT5 は約定）。journal に `2026.02.06 23:59:00 [market closed]`・23:59 約定 0 件・23:58 約定 2 件を確認し、クローズは「23:59（毎日）」で 23:58 は開場と判明。当初 ISSUE-018 の friday_close=23:55 は 2026-01 単一事象（01-09 23:59 拒否）への過剰適合だった（23:55-23:58 の金曜約定が 2026-01 に無く誤りが露見しなかった）
- 原因：Jp225SessionCalendar が `weekday==Friday and mins>=1435(23:55)` を閉鎖にしていた。実 MT5 の日次セッションは [01:01, 23:58]（00:00-01:00 プレオープン閉鎖・23:59 クローズ閉鎖）で曜日非依存
- 対策：Jp225SessionCalendar を「mins < 61(01:01未満) または mins >= 1439(23:59以降) を閉鎖」（毎日同一・金曜固有ロジック撤去）へ修正。daily_open_minute=61 / daily_close_minute=1439。session_calendar.py の _FRIDAY 定数と weekday 判定を削除
- 検証：2026-02 run5 突合 → trades 886 vs 886（完全一致）・全886エントリ bit-exact・初回 buy 53680.7・stop-out 2026-02-09 一致。2026-01 突合は net -4649/balance 5351/0-of-1444 を維持（退行なし）。全532テスト pass（カレンダー単体テストを 23:59 日次クローズ基準へ更新）
- 残差（別要因）：2026-02 は net -5011 vs MT5 -5021（差 10）が残る。原因は stop-out の決済価格が「割れバーの open クォート」（MT5: Mon 2026-02-09 01:00 open 57612+spread45=57657）に対しこちらは close 基準 mark_price（57647）で、週末ギャップ（open≠close）でのみ顕在。2026-01 は stop バーが open==close のため一致していた（ISSUE-022 として切り出し）

## ISSUE-022

- 概要：2026-02 突合（260620-03 run5）の最終 stop-out 決済価格が net 残差 10 を生む。MT5 は割れバーの open クォート（Mon 2026-02-09 01:00 open 57612+spread45=57657）で決済、こちらは close 基準 mark_price（57602+45=57647）。週末ギャップ（open≠close）でのみ顕在
- 重大度：低（1件の決済価格・net 0.2%）。2026-01 は stop バーが open==close で一致していたため未顕在
- ステータス：RESOLVED
- 検出日：2026-06-20
- 検出経路：run5 突合で全886エントリ一致後、stop-out 決済のみ 10 乖離。割れバー 2026-02-09 01:00 の O=57612/C=57602（spread450）と MT5 決済 57657 から、MT5 が open+spread（=open の Ask）で決済と判明。ISSUE-019（close 基準）は 2026-01 で open==close だったため区別できず close と結論していた過少決定だった
- 原因：実 MT5 1分足OHLC は O→H→L→C の最初の pseudo-tick（open）で margin を評価し、open が割れた保有玉を open クォートで強制決済する。こちらは close 基準（update_floating_pnl(bar)→mark_price）のみで判定/決済していたため、週末ギャップで open≠close のとき 10 ずれた
- 対策（config gated・既定 byte-identical）：bar-mode ループのバー先頭に「open 基準 stop-out 先行判定」を追加。`config.stop_out_at_open`（既定 False）True 時のみ、`derive_quotes(bar, current_open)` の (open Bid / open+spread Ask) で含み損を評価し、割れたら open クォート（買い=Bid=open / 売り=Ask=open+spread）で強制決済。後段の close 基準判定は残すため、open 非割れ・bar 内割れは従来どおり close 決済。every-tick 経路は到達ティック価格で元から open 相当に正確のため不変。models/config_loader に stop_out_at_open(既定False) 追加
- 検証：current_open+bid_ask+close_and_halt+jp225+profit_round_digits=0+stop_out_at_open=True の単一 config で **2026-02 net -5021 vs -5021（literal一致）・balance 4979・trades 886・全886エントリ bit-exact**、かつ **2026-01 net -4649/balance 5351/0-of-1444 を維持**（2026-01 は open==close のため open 判定でも不変）。既定 stop_out_at_open=False で全534テスト pass（byte-identical・stop-out at open の回帰テスト2件追加）
- 到達点：2026-01 と 2026-02 の両月を、同一 config で trades・全建値・全決済・net・balance まで literal bit-exact に到達

## ISSUE-023

- 概要：indicator_ui の Web チャートで時間軸（タイムフレーム）を移動/変更したとき、メインチャートと各インジケーターの画面更新がバラバラ（非同期・段階的）なタイミングで行われる。利用者には「メイン → 指標1 → 指標2」と順に切り替わって見える
- 重大度：低（表示の体感品質。計算結果・データ整合性に影響なし）
- ステータス：RESOLVED
- 検出日：2026-06-20
- 検出経路：`indicator_ui/web/js/adapter/front/indicator_controller.js` の `setTimeframe`/`recomputeAllApplied` を精査。(1) L296 で先に `renderer.setCandles()` がメインのみ即描画。(2) L313-318 の `recomputeAllApplied` が適用済み指標を直列ループで「`await` compute → 即 `remove`+`_draw`」する。各 `await` でイベントループに制御が戻り、ブラウザが中間状態を1指標ずつ描画するため段階的に見える
- 原因：計算（async）と描画（renderer 呼び出し）が `recomputeInstance` 内で密結合し、指標ごとに「計算→即描画」を繰り返す。`await` を跨いで描画するため中間ペイントが発生する。エージェント提案の Promise.all 並列化は不採用（`facade.recompute` が呼び出し時 `this._state` を `cloneState` し最後の代入が勝つため generation の lost update を生む。`this._lastSeries` も共有フィールドで上書きされる）
- 対策（indicator_ui/web のみ・描画ライブラリ/API 変更なし）：`recomputeInstance` を「計算フェーズ `_computeInstance`（async・state 更新・series を job に退避）」と「描画フェーズ `_renderInstance`（同期）」に分離。`recomputeAllApplied` は直列で全指標を計算（state 競合なし）→ `await` を挟まない単一同期パスで全描画→ persist/legend は最後に1回。`setTimeframe` は candles 取得後、メイン系列差し替え（setCandles）を `preRender` として同じ同期バッチに含める。既存契約（setCandles 1回・timeframe/limit 伝播・`isRecomputing()` 全期間 true・全指標再計算）は保持
- 検証：web 全 253 テスト pass（既存 252＋回帰1）。回帰テスト `setTimeframe batches all renders after every compute resolves (ISSUE-023 regression)` は (1) 計算完了前に `setCandles`/`remove`/`renderLine` が呼ばれない (2) すべての compute が描画より前に並ぶ（compute-all→render-all）を検証。旧実装では (1)(2) 双方で fail する非空テスト

## ISSUE-024

- 概要：StopEntryProbe_EA（両建て逆指値プローブ）の MT5 突合（260620-2604-02・2026-04）で、bit-exact に到達しない。トレード数 ours 834 vs MT5 10100、net ours -4770 vs MT5 +9990。先頭1件は完全一致だが2件目以降の再アーム entry が約10ずれ約定頻度が乖離
- 重大度：中（実装した StopEntryProbe 戦略・OCO は単体動作・初回約定 bit-exact だが、突合は未達。既存経路は無影響＝524 unit 全 pass）
- ステータス：RESOLVED（**完全一致**：trades 10100=10100・net 9990=9990・balance 19990=19990・建てイベント lockstep **10100/10100=100.00%**）
- 検出日：2026-06-20
- 検出経路：`_reconcile_2604_02.py` で xlsx Deals と trade-by-trade 突合。初版は stale straddle 再投函バグで 27632 trades（arm 175 回・TP 27361）。`_armed_balance` で「balance 変化＝約定→決済完結」を検知し破棄する修正で 834 trades へ収束。なお先頭 [0] は entry 52984.8/exit 52964.8/SL/-20 が MT5 と完全一致
- 原因（構造）：MT5 ジャーナル（260620-2604-02.txt）と M1 OHLC の突合で、MT5 はバー内のティック（制御点）単位で OnTick が走り、ポジションがフラットになった「その秒のクォート」で即再アームすると判明。例：bar 01:01(O52969.8/H53019.8/L52939.8) で 01:01:20 BUY約定52984.8→01:01:40 SL52964.8→同 01:01:40（L 制御点 Bid=52939.8/Ask=52944.8）で即再発注 BuyStop52954.8。当エンジンは `on_new_bar` をバー境界でのみ呼ぶため、バー内の決済ティックのクォートで再アームできず（次バー open を使う）、参照価格が約10ずれ約定頻度が低下（834≪10100）
- 対策（実施・承認済み）：per-tick 再アームをエンジンへ追加。(1) `StrategyPort.on_tick(bar_index,bid,ask,account)`（既定 no-op）を追加。(2) config `pending_persistent`（gated・既定 False）True で resting をバー境界でリセットせず約定まで保持し、tick ループで「保有0・resting 0」のティックに `on_tick` を呼び当該ティッククォートで即再装填。(3) `StopEntryProbe` をステートレス化（`on_new_bar`→[] / `on_tick` が両建て装填・engine が呼ぶ条件で持続/再アーム制御）。(4) 同一ティック両建てのOCOタイブレーク `_oco_ordered`（下落→sell_stop 先・上昇→buy_stop 先）を carried 評価に追加
- 真因2（同一ティック両建て＝ヘッジ・xlsx で実証）：当初 OCO を「1本約定で即 break＝兄弟取消」にしたため残差±10が出た。xlsx Deals 01:05:30 に **buy in 52985.8 と sell in 52955.8 が同時約定→01:09:30 に両方 SL -20** とあり、MT5（OANDA hedging 口座）は「1ティックの bid-ask 帯が両 stop を跨ぐ広 spread/doji バー」でサーバが OnTick 前に**両 trigger 分を全約定＝両建て成立**（CancelOpposite は“約定後に残った pending”のみ取消すため同時約定は取消せない）と判明。さらに両建て玉は**証拠金が相殺（hedged margin）**されるため stop-out しない（単純加算だと偽 stop-out→halt で 5 trades に激減した）
- 対策2（実施）：(A) OCO を「同一ティックで trigger した stop は全約定し、約定が起きたら trigger しなかった残 pending のみ取消」へ修正（`_oco_ordered` タイブレークは不要となり撤去）。(B) config `hedged_margin`（gated・既定 False）追加＝stop-out 判定の証拠金を「買い計・売り計の大きい側」とし反対玉を相殺（同量両建ては実質ノーマージン）
- 真因3（残差96.1%→99.99%・2点）：(i) **ドジ連鎖の極値到達順**＝当エンジンは「直前足もドジだと momentum 不明→安値先(olhc)既定」だったが、MT5 は**最後の非ドジ足の方向を連鎖跨ぎで継続**する。tick model でドジ足は prev を上書きしない様修正＝方向反転クラスタ（#1/#4/#6/#10 等）が解消し 99.83%→99.98%。(ii) **閉鎖バーでの resting 約定**＝tick ループの resting 約定が `closed_bars` を見ておらず、23:59 等の閉鎖足で偽約定（MT5 は閉鎖時間帯で約定拒否）。tick ループ約定を `bar_index not in closed_bars` でガード＝件数・net が完全一致（10100/9990）
- 検証：`pending_lifecycle+oco+persistent+hedged_margin+stop_out_at_open+jp225+profit_round_digits=0+current_open+bid_ask` で **trades 10100=10100・net 9990=9990・balance 19990=19990（件数/損益/残高 完全一致）**、建てイベント lockstep **10099/10100（99.99%）**。既存 backtest unit 534 全 pass（doji連鎖/閉鎖バー回帰2本追加）。**2603-01（12787/12787）・2604-01（1770/1770）も bit-exact 維持**（doji変更は連鎖時のみ作用し既存突合に無影響）
- 真因4（最後の1件＝100%到達）：唯一の不一致 idx4888（04-14 01:01）をトレースし特定。両者が同一の先行 sell（建値57654.1@23:37・SL57674.1）を保有し、**04-14 に pre-open バー `01:00`（spread=250＝病的に広い）が存在**。当該足の Ask高値=57659.1+25=57684.1 が SL を超えるため当エンジンは 01:00 で SL 決済→再アームが 1 バー先行。**01:00 は jp225 閉鎖バー（pre-open）で MT5 は市場閉鎖中 SL/TP を処理しない**ため MT5 の sell は 01:01 まで生存。当エンジンの `closed_bars` ガードは新規約定・再アームのみ抑止し、**保有玉の SL/TP・stop-out は閉鎖バーでも処理**していた非対称が真因
- 対策4（実施）：閉鎖バーでは tick ループ・open-tick SL/TP を実行しない（`tradable_ticks = [] if bar_index in closed_bars`＋open-tick SL/TP に `closed_bars` ガード）。市場閉鎖中は OnTick が走らない実 MT5 に整合。pending_mode 以外は closed_bars 空で全バー対象＝従来不変
- 検証（最終）：**trades 10100=10100・net 9990=9990・balance 19990=19990・建てイベント lockstep 10100/10100（100.00%・分岐0）＝完全 bit-exact 達成**。既存 backtest unit 535 全 pass（doji連鎖/閉鎖バー約定/閉鎖バーSLTP の回帰3本追加）。**2603-01（12787/12787）・2604-01（1770/1770）も bit-exact 維持**（閉鎖バーSL/TPガードは両突合に無影響＝閉鎖足で保有玉SL/TP発火が無かった）
- 設計監査（重要・対策4の過剰修正を是正）：対策4は閉鎖バーで SL/TP も stop-out も止めていたが**過剰**と判明。bar-mode 突合（2603・MA EA）に同ガードを適用すると balance が乖離（5177→5182）し、実測で **2603 の唯一の stop-out が 01:00 pre-open バーで発火し MT5 と一致**していた＝**MT5 は閉鎖バーでも stop-out（ブローカーのリスク清算）を行う**。対して SL/TP（顧客注文）はトレードセッション外で発火しない（2604-02 で実証）。**正しい設計＝閉鎖バーでは「新規約定・ペンディング fill・SL/TP」は不可・「stop-out と含み損評価」は継続**。every-tick は `tradable_ticks=[]` を撤回し tick ループを常時回して SL/TP のみ `bar_closed` でスキップ（fills/再アームは既存 `closed_bars` ガード・stop-out/equity は継続）。bar-mode も H ブロック（SL/TP）のみ `closed_bars` ガードし stop-out（L228/L359）は発火させる。両経路で挙動を一致
- 検証（監査後・最終）：**全5突合が bit-exact／100%**＝2603（842/5177）・03（886/4979）・2603-01（12787/15666）・2604-01（1770/5390）・2604-02（10100・net9990・lockstep100%）。backtest unit **536 全 pass**（stop-out が閉鎖バーで発火する回帰テスト追加）
- 申し送り（今後のテスト観点）：トレードセッション・ゲートは現状 ~9 箇所の `closed_bars` 条件分岐に**散在**（単一の `is_tradable(bar)` 抽象は無い）。新規約定経路を足す際はガード漏れに注意。jp225 カレンダーは時刻固定の近似（実セッション設定そのものではない）。不変条件「閉鎖バー：fills/SL-TP 不可・stop-out 可」は test_stop_entry_probe.py の3テスト＋bar-mode 突合 2603（01:00 stop-out）で担保
- 実装済み（本 issue・既存経路 byte-identical）：`StopEntryProbe` 戦略、engine OCO（`pending_oco`・同時約定はヘッジ）＋持続/per-tick再アーム（`pending_persistent`）＋hedged margin（`hedged_margin`）、tick model のドジ連鎖 momentum 継続、閉鎖バーのトレードセッション・ゲート（fills/SL-TP 不可・stop-out 可／bar-mode・every-tick 一貫）、`StrategyPort.on_tick`、`build_interactor` 配線、`_reconcile_2604_02.py`（建てイベント lockstep 突合）、`test_stop_entry_probe.py`＋`test_tick_model.py`（回帰計13本）

## ISSUE-025

- 概要：売買マーカー上部の価格ラベル（lwc marker の `text`）にオンマウスすると、当該ペア以外の売買マーカー（およびペア外ローソク）が減光する。ユーザーは「価格ラベルは矢印グリフと別」と認識しており、ラベル hover での発火を不具合と判断
- 重大度：中（v8 hover 減光は仕様どおり動作。発火範囲が想定（矢印グリフのみ）より広い UX 不整合。既存テスト 308/308・突合は無影響）
- ステータス：RESOLVED（ユーザー決定＝価格ラベル非表示。`load()` の lwc サブセット抽出で `text` を除外＝ラベル非表示＋ヒット領域を矢印/円グリフのみへ縮小。ブラウザ実証：ラベル消失・グリフ hover で減光発火・ラベル/余白 hover では無反応。renderer 35/35・全体 309/309 緑。設計 §14 新設・§13.3 訂正）
- 検出日：2026-06-21
- 検出経路：ユーザー報告（スクショ ss2026062193635.jpg）。価格ラベル onmouse 時に非ペアマーカーが減光
- 原因（実証）：価格ラベルは lwc series marker の `text`（例 "SL 71265.5 (-50)"）で、矢印グリフと**同一 marker・同一 `id`/`hoveredObjectId`**。lwc v5.2.0 は marker の**テキスト範囲もヒット領域に内包**するため、ラベル上 hover でも `hoveredObjectId` がセットされ v8 の減光が発火する。ブラウザ実測：ローソク足が無い上部ラベル帯（y≈45・価格 76000-88000）hover でマーカー列輝度 32.43→19.34（-40.4%）＝ラベル単独で発火を確認。設計 §13.3「マーカーから離れた同時刻の価格帯では発火しない」と実挙動が矛盾
- 対策案（いずれも UX/仕様判断＝要承認）：
  - 案A：非ペアマーカーの減光（§10.2）を廃止しローソク足減光（§12）のみ残す（ラベル hover でも他マーカーは暗くならない／実装小）
  - 案B：価格を marker `text` から外し矢印グリフのみを hover 対象化、価格は別 UI（凡例/クロスヘア読取欄）へ（§13.3 の意図に忠実だが表示再設計＝実装大）
  - 案C：現挙動を許容し §13.3 の誤記述（離れた価格帯では発火しない）を訂正（実装最小）

## ISSUE-026

- 概要：売買マーカー（矢印/円グリフ）にオンマウスした際、当該売買ペアの取引明細ステートメントをポップアップ表示する機能を追加する（仕様追加）。表示項目は 利益 / 取引日時 / 取引価格 / 取引数量 / 決済日時 / 決済価格 / 決済数量 の 7 項目
- 重大度：低（新規機能追加・既存挙動は不変。rapid-prototype で試作・要 UX 確認）
- ステータス：RESOLVED（試作スコープ＝ユーザー決定反映・検証済。productionization は別途依頼）
- 検出日：2026-06-21
- 検出経路：ユーザー仕様追加指示（/rapid-prototype）
- 発火条件：売買マーカーのグリフ hover（ISSUE-025 で text 除去済＝ヒット領域はグリフのみ。`hoveredObjectId="t{i}:..."` 駆動＝v8 と同一トリガを共用）
- 仕様⇔データの差分（実証・要承認）：表示 7 項目のうち、現行 `trade_markers.json` の `pairs` が保持するのは `entry/exit{time,price}` と `side/win` のみ。**利益（profit）と数量（volume）が presenter（`adapter/presenter/trade_markers.py` の `_pair_record`）で欠落**している
  - 利益：`pairs` には無いが（exit-exit）×sign×lot×contract で厳密導出可（先頭6件で marker text の (±) と完全一致を確認。lot=0.1・contract=10＝1.0/point）。決済マーカー text にも (+37) 等として既出
  - 数量：エクスポート config の `lot_size=0.1`（固定）が実数量。pairs に未保持
  - 取引数量＝決済数量：MT5 往復は同量決済のため両者同値（volume）。別数量の部分決済は当エンジンに無い
- 対策案（試作で採用）：(1) presenter `_pair_record` に `profit`（pnl）と `volume` を追加（恒久修正）。(2) 既存 JSON は再バックテスト不要で直接 enrich（試作の即時確認用）。(3) フロント `TradeMarkersRenderer._onCrosshair` に「highlight 中ペアの明細ポップアップ表示／非 highlight で非表示」を追加（既存 v8 hover 経路に相乗り・新規 fetch なし）
- 残論点（要ユーザー確認）：日時の TZ（UTC/ローカル）／数値の桁・通貨単位（利益の単位・円/pt）／ポップアップの配置（カーソル追従 vs マーカー固定）・スタイル／部分決済の有無
- ユーザー決定（2026-06-21・/rapid-prototype）：日時＝**JST（UTC+9）**／利益＝**数値のみ**（単位なし）／配置＝**マーカー固定**（カーソル追従しない）
- 仕様改訂（2026-06-21・ユーザー）：**日時（YYYY/MM/DD）と時間（HH:MM:SS）を別行に分離**＝計 9 項目（利益／取引日時／取引時間／取引価格／取引数量／決済日時／決済時間／決済価格／決済数量）。`_fmtTime` を `_fmtDate`/`_fmtClock`（JST）へ分割し検証済（取引 2026/06/16・09:14:00 等）
- 試作結果（ステータス＝RESOLVED・試作スコープ）：
  - 実装：(1) presenter `_pair_record` に `profit`/`volume` 追加（恒久）。(2) `trade_markers.json` を再バックテスト不要で enrich（profit 厳密導出・volume=lot_size 0.1）。(3) `TradeMarkersRenderer` に hover 明細ポップアップ追加（`_updatePopup`/`_ensurePopup`/`_popupHtml`/`_fmtTime`(JST)/`_fmtNum`/`_positionPopup`）＝既存 v8 hover 経路に相乗り・新規 fetch/購読なし・`document` 不在時 no-op
  - 検証（playwright + 実 lwc + 実 JSON・使い捨てハーネス）：#0 BUY 利益+37 緑／#3 SELL 利益-58 赤で 7 項目すべて表示。日時 JST 09:14:00 等。マーカー離脱で非表示。スクショ 2 枚をユーザー提示済
  - 回帰：web renderer/wiring 41 pass／presenter unit 28 pass（profit/volume 保持の回帰テスト 1 本追加・`_DuckRecord` に volume 付与）
  - 残（productionization・本 issue 範囲外＝要依頼）：実バックテストでの `trade_markers.json` 再生成（presenter は対応済）／`out/prototype.html` バンドルへの同期／実 served アプリ（index.html・B方式）でのグリフ実 hover 通し確認（試作は `_onCrosshair` 直叩きで等価検証）

---

## ISSUE-027: 週次ボラバンド戦略 金曜引け強制手仕舞いが既存エンジンの on_position_check 未配線と衝突
- **重大度**: High（設計レビューで Blocker 検出 → 非破壊代替で解決）
- **ステータス**: RESOLVED
- **検出**: spec-reviewer-executor（基本設計レビュー）。`run_backtest.py` に `on_position_check` 呼出 0 件（grep 実証）、時間ベース強制決済機構なし。仕様§3.1-7 金曜引け強制手仕舞いが C2（既存無改変）と両立不能。
- **原因**: 既存エンジンの戦略フックは on_init/on_new_bar/on_tick のみ配線。週単位の強制 close 機構は end_of_test（最終足・pending_mode 限定）と stop_out のみ。
- **対策（非破壊・エンジン無改変）**: 週次戦略を **週単位セグメント実行**（bars=各週の最初の取引日寄り〜最後の取引日引け）とし、`end_of_test` 清算（run_backtest.py:888-910・pending_mode 限定で建玉を最終足 close で強制決済）を金曜引け手仕舞いに充てる。
  - 検証(b) 同一バー S/T 両到達 → 既存 `sltp_tie="sl"`（config_loader.py:46）でストップ優先＝仕様§2.6 一致。
  - 検証(c) 週初/金曜が非取引日 → セグメント先頭/末尾バーが自動対応＝仕様§2.6 一致。
  - 検証(d) market ロング＋SL=S/TP=T の OCO 監視はエンジン既存 SL/TP 経路で充足。
- **結果**: エンジン破壊的変更ゼロ。詳細設計で UC-WV2 を「週単位セグメント orchestration（run_is_oos 同型の DIP 注入）」として確定。

---

## ISSUE-028: freeze_last（凍結標準化窓）が本番未接続＝死蔵フラグ。全ティック分析エンジンで解消する
- **重大度**: Medium（機能欠陥ではない。追加済み機能の実需未確定＝YAGNI 条件）
- **ステータス**: IN_PROGRESS（Phase2 設計提示・ユーザー承認待ち）
- **検出**: architecture-executor（freeze_last レビュー・2026-06-28）。`grep freeze_last=True` で本番コンシューマ 0 件（回帰テスト4本のみ）。「死蔵フラグ化」を条件付き合格の解消条件として明示。
- **背景/命題**: 目的は売買戦略立案のための検証分析。指標本体の足内(ティック粒度)推移＝正当なシグナル（実証：1足803ティックで価格×EMA接点11回／間引き2-46点では1しか捕捉できず10/11取りこぼし＝間引きは接点シグナルを破壊）。確定足のみでは接点を見落とす。
- **対策（Phase2 設計・提案）**:
  - 新規アクター `prototype_260626-01/analysis/`（本番コア read-only 消費・viz と分離）。
  - 機構：全足を安価スキャン→**跨ぎ判定**（足の高安／本体レンジが対象水準を跨ぐ足のみ）→候補足だけ `freeze_last=True` で全ティック評価。接点/クロスを全解像度抽出（接点だけ精密・他は安価＝計算リソース削減・ロスレス）。
  - 標準化窓は1足1回固定（freeze_last＝確定足の直前W本で標準化）。
  - 出力：接点/クロスイベント（JSONL/CSV）＋サマリ。対象＝移動平均(価格×MA接点)＋標準化窓6指標(本体×σ水準)。範囲＝1銘柄・指定期間（既定 直近Nバー）。
- **残論点（承認待ち）**: 配置・出力形態・対象指標/水準・評価範囲。
- **関連**: freeze_last 実装＝commit 143c297（develop マージ 91cbc2d）。作業ブランチ feature/tick-analysis-engine。

---

## ISSUE-029: 1分足の再生バグ＋足内形成の全時間足対応（足内データ窓が「1日固定」）
- **重大度**: High（1m 再生が誤動作。日足以外で足内形成が破綻）
- **ステータス**: IN_PROGRESS（増分1=1m〜1D 完了／増分2=1W・1M・モードUI連動 残）
- **検出**: ユーザー（1分足再生で挙動異常を確認・2026-06-28）＋コード確証。
- **原因**: `prototype_260626-01/web/js/replay.js:399` の `buildStream` が足内データを常に `/intraday?start=cd.time&end=cd.time+DAY_SECS`（1日固定）で取得している。1D は「足の期間＝1日」で正しいが他足で破綻：
  - 1m：1分足なのに丸1日分のティック/m1 を流す（誤形成）。
  - 1W/4h：期間に対し1日窓が不足/超過。
  - 「1分OHLC」モードは m1 前提で 1m 足を細分できない（ティックのみ有効）。
- **対策（設計骨子・提案）**:
  1. 窓を時間足の期間へ一般化：`end = cd.time + duration(tf)`（1m=60…1h=3600…1D=86400…1W=7日/1M=暦月は要配慮）。
  2. サブ解像度＝1段細かい系列：1m→ティック／5m〜1h→m1／4h→m1(or 15m)／1D→m1・ティック／1W・1M→日足。
  3. モードを時間足相対化：実ティック/全ティック合成=全足／1分OHLC=tf≥5mのみ／始値・数学=全足。
  4. 性能：上位足(1W/1M)はティック/m1 を全量流さず粗いサブ解像度+cap で抑制。
  5. backend `do_intraday(start,end)` は区間受理済。フロントが正しい end とサブ解像度を渡す設計＋上位足のサブ系列供給を追加。
- **残論点（要確定）**: duration(1W/1M) の暦・取引週の扱い／各 tf のサブ解像度確定／モード UI の tf 連動（無効モードの非表示）／上位足の点数 cap。
- **関連**: 日足は既存実装で約9割完成。本件は日足概念の水平展開＋1m修正。Phase2(ISSUE-028 分析エンジン)とは別作業。
- **進捗（2026-06-28・増分1）**: 足内データ窓を `cd.time+durationSecs(tf)` に一般化（replay.js）、backend `do_intraday` を `[start,end)` で tick 窓フィルタ＋日跨ぎ走査に修正（proto_server.py）。実証: 1m窓=分内ティック（216点）／1h=60 m1／1D=従来。実機 1m 足が分内27点で形成（旧=日全体800）。1D 非回帰（verify_form_modes 緑）。残=1W/1M の暦・サブ解像度・モードUI連動（増分2）。

---

## ISSUE-030: 足内形成の上位足（1W/1M）対応＋モードUIの時間足連動（ISSUE-029 増分2）
- **重大度**: Medium（増分1で 1m〜1D は解消済。残＝上位足の暦/巨大ペイロードと UI 連動）
- **ステータス**: IN_PROGRESS（2a=1W/1M形成＋モードUI連動 完了／2b=日足サブバー意味付け 任意残）
- **検出**: ISSUE-029 増分1 完了に伴う残作業の分離（2026-06-28）。
- **背景**: 増分1で足内データ窓を `cd.time+durationSecs(tf)` に一般化し 1m〜1D を解消（固定秒 TF_SECS）。1W/1M は固定秒で表せず（暦/取引週）、サブ解像度に m1/tick を全量使うとペイロード巨大。モード UI は全 tf で同一だが 1m 等では縮退するモードがある。
- **対策（設計骨子）**:
  1. **1W/1M の期間長**：固定秒でなく `marketdata.resample` の周期ラベル（W-FRI / ME 等）で `[floor(t,tf), 次境界)` を算出（足内窓＝その期間）。
  2. **サブ解像度を上位足で粗く**：1W/1M は**日足（resampled）を足内サブバー**に（1W≈5本/1M≈20本）。ticks/m1 の全量は使わない（巨大）。中位足(4h)は m1。
  3. **性能**：上位足は粗いサブ解像度＋点数 cap でペイロード/描画を抑制。backend `do_intraday` は日跨ぎ走査対応済だが、上位足では日足サブバー供給経路を追加。
  4. **モードUIの tf 連動**：各 tf で有効モードのみ出し分け（例 1m は「1分OHLC」「全ティック合成」等を非表示/無効＝縮退。実ティック/始値/数学は全 tf）。
- **残論点（要確定）**: 1W/1M の duration 算出（resample ラベル流用の可否）／上位足サブ解像度の確定（日足 vs m1）／モード有効集合の定義と UI（非表示 vs 無効グレーアウト）。
- **関連**: ISSUE-029（増分1 完了・commit cb05183）。ブランチ feature/replay-all-timeframes。
- **進捗（2026-06-28・増分2a）**: ①足内窓を「次足の開始時刻」へ（暦/取引週も正確・固定秒不要）②backend `do_intraday` の m1 をサーバ側 cap（`_cap_m1_rows` 1500・極値保持／1D≤1440 無変更・1W6597→1502/1M27895→1503）③モードUIの tf 連動（1m で 1分OHLC/全ティック合成を非表示＋real_ticksへ退避）。実証: 1W足が期間内ティック(n=801)で形成・1m モード非表示・1D 非回帰(verify_form_modes 緑)。残(2b・任意)=上位足のサブ解像度を「日足サブバー」に意味付け（現状は capped m1＝機能的に妥当）。

---

## ISSUE-031: replay_ui backend — 足内 mid 算出/外れ値除去の orchestration が adapter に在る
- **重大度**: Low（依存方向違反ではない・設計整理）
- **ステータス**: OPEN
- **検出**: 因果リビール再生バックエンド arch レビュー（2026-07-04・🟡-1）。
- **背景**: `usecase/intrabar_window.py` は `window_port.load_ticks()` を素通しし、mid=(bid+ask)/2＋窓フィルタ＋外れ値除去（本質不変 E-4）の呼び出しが `adapter/intrabar_window_repository.py` に在る。domain `tick_mid_series.mid_series` 自体は純化済だが、tick 源差替のたび各 adapter が mid_series を再結線＝本質ルールが adapter ごとに分散し得る。
- **対策（提案）**: Port を `load_raw_ticks(start,end)->[(sec,bid,ask)]` へ変え usecase 側で `mode=='real_ticks'` 時のみ domain `mid_series` を適用（mode ゲートは usecase 既存＝軽量性不変）。
- **関連**: replay_ui バックエンド増分（branch feature/contact-scan-replay）。

## ISSUE-032: replay_ui backend — 外れ値閾値 0.3 の二重定義（用途別だが同値）
- **重大度**: Low
- **ステータス**: OPEN
- **検出**: arch レビュー（🔵-3・2026-07-04）。
- **背景**: `domain/tick_mid_series.OUTLIER_THRESHOLD`（足内 mid 外れ値）と `adapter/_m1_repair.M1_OUTLIER_THRESHOLD`（M1 日内補正）が各 0.3。アルゴリズムは別物だが値・意図（±30%）同一で source が分岐＝値乖離リスク。
- **対策（提案）**: 単一 source-of-truth へ集約、または「別用途で独立の定数」である旨を両所へ明記して意図を固定。
- **関連**: replay_ui バックエンド増分。

## ISSUE-033: replay_ui backend — 未消費の抽象（E-3 window / ContactScanPort）のフロント確定時精査
- **重大度**: Low（YAGNI）
- **ステータス**: OPEN
- **検出**: arch レビュー（YAGNI 削除候補・2026-07-04）。
- **背景**: `domain/intrabar_window.window`（足境界→窓算出 E-3）と `replay_ports.ContactScanPort` は backend に production caller 不在（テストのみ）。窓はフロント replay.js が算出し `/intraday` に start/end で渡す設計、接点は次フェーズ想定＝将来仮説が根拠。
- **対策（提案）**: フロント増分で「消費者が生じるか」確定し、生じなければ削除。窓をサーバ側算出する usecase に倒す選択肢も検討。
- **関連**: replay_ui フロント増分で判断。

## ISSUE-034: replay_ui backend — df 往復の列名 lower()/float() 強制が暗黙契約
- **重大度**: Low（現状安全）
- **ステータス**: OPEN
- **検出**: code レビュー（🔵-1・2026-07-04）。
- **背景**: `adapter/causal_compute_gateway._df_to_bars` が全列を `str(c).lower()`＋`float(row[c])` へ強制。現状は源 CSV 列が小文字＋compute の case-insensitive アクセスで安全（SMA round-trip 同値テスト合格）。非数値列・大文字前提指標が将来入ると `float()` 例外/列名不一致。
- **対策（提案）**: 「OHLCV 数値列前提」を docstring 明記、または非対象列を保存扱いにするガード追加。
- **関連**: replay_ui バックエンド増分。

## ISSUE-035: replay_ui backend — 静的配信のパストラバーサル判定が prefix 一致のみ（proto 継承）
- **重大度**: Low（web_dir 既定 None＝静的配信オフ）
- **ステータス**: OPEN
- **検出**: code レビュー（🔵-3・2026-07-04）。
- **背景**: `framework/serve_replay.py` の静的配信が `str(fp).startswith(str(web_dir))`。区切り無し prefix のため `web_dir="/a/web"` で `/a/webevil` が通過しうる（proto_server と同一弱点）。既定 web_dir=None で影響は低いが、フロント配信有効化時に露見。
- **対策（提案）**: `os.path.commonpath([fp, web_dir]) == str(web_dir)` もしくは末尾セパレータ付き比較へ。
- **関連**: replay_ui フロント増分（静的配信有効化）時に対応。

## ISSUE-036: replay_ui backend — /candles 非tick分岐の過剰直列化＋失効 docstring 参照
- **重大度**: Low
- **ステータス**: OPEN
- **検出**: code レビュー（🔵-4/🔵-5・2026-07-04）。
- **背景**: (a) `serve_replay` が `/candles` の非 tick 軽量経路も `_HEAVY_LOCK` で直列化（proto は tick のみ施錠・出力不変の過剰直列化）。(b) `domain/tick_mid_series` 等の docstring が本 worktree 不在の `contact_scan.tick_window.window_ticks` を bit 一致対象と引用（実挙動は proto `do_intraday` tick 経路で検証済）。
- **対策（提案）**: (a) 非 tick 軽量経路を施錠外へ、または保守的直列化の意図をコメント明記。(b) 参照を「proto_server.do_intraday tick 経路」へ更新。
- **関連**: replay_ui バックエンド増分。

## ISSUE-037: replay_ui frontend(再生層) — controller への結合＋View fallback の堅牢化
- **重大度**: Low（挙動非差・parity 由来）
- **ステータス**: OPEN
- **検出**: 再生層(INC-F2) arch/code レビュー（🔵・2026-07-04）。
- **背景**: (a) `web/js/replay.js` が `controller._timeframe`/`_recentBars` の private を直接参照＋`applyIndicator`/`removeInstance` を実行時 monkeypatch（syncBoundary ラップ）。プロト replay.js の忠実移植由来で依存方向違反ではないが結合が強い。(b) `replay_view.readSpeed/readMode` は要素欠落時 NaN→既定退避（clampSpeed→1/real_ticks）。プロトは `null.value` で throw。現行 index.html では rp-speed/rp-mode 常設のため到達不能。(c) `syncSpeedUI` の `clampSpeed(parseFloat())` はプロトの `+value` と [0,1] 範囲で等価。
- **対策（提案）**: (a) controller 側に public accessor / フック（onApplied 等）を設け private 参照・monkeypatch を解消。(b)(c) 現行 DOM では非到達＝現状維持可。厳密忠実化するなら proto 準拠へ寄せる。
- **関連**: replay_ui フロント増分（INC-F2）。

## ISSUE-038: indicator_ui — indicator_controller.js(994行) SRP違反（View分離）
- **重大度**: Medium（設計整理・正しさ影響なし。監査は「依存方向違反0・破壊的変更不要」と明言）
- **ステータス**: RESOLVED（2026-07-05・ユーザー承認で実施）。純DOM描画を `IndicatorLegendView`（adapter/front・replay へ symlink 共有）へ抽出、controller は view-model 組立＋コールバック配線のみ委譲。ハンドラ/状態/永続化/reveal・gear seam は残置（未変更・diff で確認）。回帰ゼロ: present web 622/624（既知2fail除外・新規21）・replay web 162/162・api 486・replay py 144。code-review 承認可（🔴0・byte不変/subclass温存を実証）。両アプリのブラウザ目視合格（present 凡例/ダイアログ/指標+MP描画・replay MP4モード・console0）。コミット 55494ef+b59df20。
- **検出**: architecture-executor クリーンアーキ徹底監査（🔴-1・2026-07-05）。
- **背景**: `indigators/indicator_ui/web/js/adapter/front/indicator_controller.js` に変更軸の異なる3責務が同居＝(1)計算オーケストレーション(`applyIndicator`/`recomputeInstance`/`recomputeAllApplied`/`setTimeframe`) (2)View描画(`bind`:787/`_renderLegend`:893/`_renderDialogList`:865/`_openDialog`:838/`_onGear`:945/`_onGearMarketProfile`:339) (3)永続化(`_persistAll`/`restore`/`_toJson`)。**単一ソース共有**（present 直接＋simulator/replay_ui が symlink 共有＋`ReplayIndicatorController` が `_mpParams`/`removeInstance`/`recomputeInstance`/`_recomputeMarketProfile` override・`_onGear`/`_onGearMarketProfile` 継承）のため、View分離は **19指標＋MP4モード＋replay の両アプリ同時回帰リスク大**。特に `_onGearMarketProfile` の reveal seam（`_untilTime`＋`enterBar`＋`isTicklive()` ガード・直近修正済）は View と controller 状態に跨り分離困難。
- **対策（提案）**: 純DOM描画（凡例/ダイアログ構築）を `IndicatorLegendView`(adapter/front) へ抽出し、ハンドラ(`_onGear`/`_onGearMarketProfile`)・状態・永続化・orchestration・subclass override は controller に温存。**回帰ゲート必須**（present web ≥601/603＝既知2fail除外・replay web 162/162・両アプリboot・MP4モード/replay目視・present byte不変）。**中間案**＝最も純粋なDOMヘルパのみ小さく抽出（低リスク・効果限定）。**見送り案**＝working維持・本Issueで既知化。
- **関連**: 同監査で 🔴-2(api/domain 死蔵誤表示) は doc明記で解消済(branch `refactor/indicator-ui-arch-remediation` 03136b0・develop未マージ)。監査総評=依存方向・技術隔離は合格。working develop=ff90583 安定。

## ISSUE-039: indicator_ui — market_profile_controller に usecase相当ロジック堆積（Interactor抽出）
- **重大度**: Low〜Medium
- **ステータス**: RESOLVED（2026-07-05・案A＝現状維持で決着。architecture-executor 精査の結論）。**抽出しないのが正解**: `handle_market_profile(...)→(status,body)` は既に BaseHTTPRequestHandler 非依存の Application Service 境界で、present(server.py)＋replay(bridge経由 MarketProfilePort)が消費中＝**usecase 抽出の目的は既に達成済**。提案の ProfileCompute/DwellCompute ポート化はドメイン数学の逆立ち(YAGNI違反)、CandleLoaderPort も JForex 実装着手まで時期尚早。新 usecase 層は既存分離の二重化＋byte契約リスクのみ増やす。依存方向違反は新規・既存とも0。将来 JForex ライブ着手時に CandleLoaderPort 1本のみ追加(案C)。コード変更なし。
- **検出**: 同監査（🟡-1・2026-07-05）。
- **背景**: `api/adapter/controller/market_profile_controller.py`(349行) に HTTP非依存の Application Business Rules が堆積＝`to`/`from` 時間窓フィルタ(:251/:311)・`_resolve_n_bins`(:72) barw→bins・`_handle_dwell`(:277) src分岐・`_cap_sessions`(:62) 応答整形。`compute_controller.py`(117行) は薄く妥当（Controller+Interactor 折り畳み許容）＝問題は market_profile のみ。
- **対策（提案）**: `MarketProfileInteractor`（窓適用/bin決定/src分岐/session整形）を抽出し、controller はクエリ解析と error翻訳に縮小。過剰な usecase 層新設は不要（YAGNI）。
- **関連**: indicator_ui backend。単独対応可。

## ISSUE-040: indicator_ui — SRP整理3件（DIルート/dwellキャッシュ/chart_renderer内部分割）※低優先
- **重大度**: Low
- **ステータス**: IN_PROGRESS（(a) RESOLVED・(b)(c) OPEN）。**(a) 完了(2026-07-05)**: チャート操作(swipe scrub/価格pan/wheelズーム/dblclick reset/2Dドラッグ)を `ChartInteractionController`(adapter/front) へ抽出、composition_root_front を配線専用に縮小(454→328行)。回帰ゼロ(unit11/11・present web633・replay162/162)、code-review 承認可(🔴0・byte不変を triangulation 実証)、ブラウザ目視合格(wheel/drag/dblclick・canvas健全・console0)。develop 4fc43af マージ・push 済(92ca8fc+8a7237c)。**(b) 完了(2026-07-05)**: dwell ディスクキャッシュを `DwellRollupStore`(adapter/compute) へ分離、`market_profile_dwell.py` は集計数学を残し委譲(622→569行)。公開API/出力 byte 不変(parity 実証・黄金値は改変前採取)、api 486→504・replay 144 緑、code-review 承認可(🔴0・triangulation)、develop マージ(0271b75+81da98e)。**(c) chart_renderer 内部分割(PriceScaleController)は保留＝監査自身が「任意・低優先／隔離境界の価値は高く分割不要」と明記。実需(その領域の機能追加)が出た時に対応**。
- **検出**: 同監査（🟡-2/🟡-3/🟡-4・2026-07-05）。
- **背景**: (a) `web/js/adapter/front/composition_root_front.js` L242-381(~140行) が pointer swipe スクラブ/縦価格パン/wheel価格ズームの**振る舞い**を実装＝DIルートに配線以外が混入。(b) `api/adapter/compute/market_profile_dwell.py`(622行) が集計ロジックとディスクキャッシュ Repository(`_save_day_rollup`:284/`_load_day_rollup`:316/署名) の同居（変更軸が別）。(c) `web/js/adapter/front/chart_renderer.js`(998行) は lwc隔離という単一軸は妥当だが内部で系列描画/価格ズーム座標数学(`handlePriceWheel`:353/`panPriceByPixels`:408)/クロスヘアDTO(`_buildReadoutDto`:872)が混在。
- **対策（提案）**: (a) `ChartInteractionController` 抽出・root は配線のみ。(b) 日次rollupを `DwellRollupStore`(Gateway) 分離し Output境界越し注入。(c) 価格スケール操作を `PriceScaleController` へ内部分割（隔離境界の価値は高く任意・低優先）。
- **関連**: 各 SRP整理。低リスク・低優先。🔵(market_profile.py の TPO/POC/VA を domain へ／properties_dialog 分割／frontend framework層物理配置) は将来余地。

## ISSUE-041: MP 統一成長モデル（Model A）— 全時間足成長×tf/mode 窓パラメータ化（ISSUE-029/030 の上に積む）
- **重大度**: Medium（機能拡張・因果/非退行が要件）
- **ステータス**: RESOLVED（Phase3-5 実装完了・feature/replay-play-mp-coupling・push 前／ブラウザ目視は依頼者実施）。**採番注記**: 依頼は「ISSUE-033」だが同番は既存（replay backend 抽象精査）。CLAUDE.md 採番規則「既存最大+1」に従い ISSUE-041 とした（既存 Issue は不変）。**設計内緊張の解決**: Phase3 の sessions 成長機構は当初「accumulator でタイル成長」だったが、reference 定義の sessions 描画（共有グリッド `this._profile.bins`＋各日 tpo[] 整列）と accumulator の自前グリッドが不整合＝silent 視覚退行リスクのため停止報告→依頼者が機構A（backend refresh(to,sessions)・無改変）を承認。Phase4 の normal 窓「当日→全期間」変更＋Phase5 の reveal ゲート isTicklive→_growing 移行（replay normal が refresh→push 成長へ）は gate1/gate3 として依頼者承認済。
- **背景**: MP のセッション集計窓が replay_market_profile_actor.js `_buildFormingArgs` に 1D 決め打ち `Math.floor(effNow/86400)*86400`（当日始まり）で残存＝全時間足で「当日1D窓」に固定され、5m/1h 等の bar-period 成長にならない。reveal 窓（stream.js intrabarWindow）とは別物（reveal は流用・触らない）。present は from 省略で全期間 base。
- **設計（詳細版 arch・厳守）**: 新 domain 値 `GrowthWindow`（両 app 共有・market_profile/web/js/domain/）に窓写像を隔離。`forCurrent(mode,tf,cursor)→{from,to,formingStart}`＝normal:from=null(全期間)/formingStart=period_start(tf)、sessions:from=session_start(暦日86400)/formingStart=period_start(tf)。不変条件 to<=cursor（未来リーク禁止）・formingStart<=to。backend `period_start_unix(now,tf)`・`frm` 受理は既存流用（backend 無改変）。
- **進捗**: **Phase3-5 完了（commit 7f6bc5c / 26f1227 / b630c2c / 2740292）**。
  - **[Phase3 基盤]** `GrowthWindow` domain（growth_window.js＋test 13 ケース）＝1D=86400 隔離・全 tf period_start 写像・cursor 欠損の窓不成立・不変条件（to<=cursor/formingStart<=to）を固定。純関数・無改変・回帰ゼロ。
  - **[Phase3 sessions 因果成長・機構A]** 基底 `_isIncremental()` に `&& !_sessions` を追加し growing×sessions を refresh(to,sessions) へ分岐（review🔵4 の破綻＝forming 単一プロファイルを sessions 描画へ被せる新到達状態を解消）。present getContext は growing×sessions のみ to=cursor(最新足) 送出（未来リーク禁止・他は byte 不変）。当日タイルは backend の因果 sessions 分割で育て過去日静的。DwellAccumulator/sessions primitive 無改修。TDD 3 ケース（sessions→refresh／normal→forming 非波及／未来リーク golden）。
  - **[Phase4 全時間足成長]** replay `_buildFormingArgs` の 86400 決め打ちを `GrowthWindow.forCurrent(mode,tf,cursor)` 委譲へ（normal→from=null=全期間 base）。growth_window を replay domain へ symlink 共有。全 tf の bar-period 成長は backend forming_ticks の period_start_unix(now,tf) が担う。**1m/全tf 実機計測（実 tick 2013-04-03 JP225・1194 ticks）**: forming 窓は bar-period（1m<=60s/1h<=3600s/1D<=86400s・period_start 一致）、成長本数 1m=890(304 bars,max5/bar)・5m=1130・15m=1171・30m=1181・1h=1186・4h=1191・1D=1193＝**全 tf GROWS（1m 非成長は反証）**。
  - **[Phase5 ticklive 撤去×成長軸移行]** catalog mode ENUM から 'ticklive' セグメント撤去（4→3 モード）。reveal push ゲートを isTicklive()→isGrowingPush()（成長軸）へ移行（基底 reveal seam・replay actor・replay controller）。replay composition に mpGrowthResolver:()=>true 注入で reveal=常時 growing 活性化。**replay normal は refresh→push 成長（enterBar・全期間 base+bar-period forming）へ**（gate1/3 承認）。sessions は refresh(to,sessions)。成長エンジンは grow 軸で存続。present バンドル out/prototype.html 再生成（review🔵3 stale 解消）。4 モード/ticklive dispatch テストを成長軸へ更新（見せかけ緑禁止）。
- **非退行（全緑）**: MP module web 197・replay web 183・present web 506/508（既知2fail=replay_analysis/timeline_player・MP 非関連）・market_profile api 190・indicator_ui api 342・replay py 144。DwellAccumulator/sessions primitive 無改修・prototype_* 無改変・技術スタック不変。
- **要ブラウザ目視（依頼者実施）**: (1) replay normal 再生が全期間累積＋現在足 forming で育つ（当日でない）。(2) sessions 当日タイルが reveal 前進で因果成長・過去日静的。(3) 1m/全tf で forming が育つ。(4) ticklive gear 非表示。(5) 特に replay normal の push 成長で描画欠落が無い（enterBar が _growing=true で自己ガードを通過することに依存）。
- **関連**: ISSUE-029（1D固定窓・増分1完了）・ISSUE-030（1W/1M粗サブ解像度）。ブランチ feature/replay-play-mp-coupling（Phase0-2 承認済・push しない）。

## ISSUE-042: replay_ui MP — ページ読込 restore 経路で完成形フラッシュが再発（cursor 未確定の refresh が全期間へ委譲）
- **重大度**: Medium（視覚バグ・因果契約違反＝未来リーク）
- **ステータス**: RESOLVED（fix/mp-restore-cursorless-flash 実装完了・回帰テスト旧コード Red 実証・replay web 194/194・MP module web 204/204 green・architecture-executor 違反なし・code-review 🔴0 条件付き承認／ブラウザ目視は依頼者実施）
- **検出**: 依頼者報告（2026-07-06）「リプレイモードのフラッシュのバグが修正されていない」。e371271（Fix#2 完成形フラッシュ撲滅）後も再現。
- **背景**: index.html は `controller.restore()` を `setupReplay()`（`controller._untilTime` を設定する唯一の場所）より**前**に実行する。前回セッションで MP が可視のまま永続化されていると、restore → `_applyMpParams`（growing=true）→ `setEnabled(true)` → `refresh()` が `getContext().to === undefined` で走り、`ReplayMarketProfileActor.refresh()` の `cursor != null` ガードを抜けて基底 refresh（to 無し＝**全期間・完成形プロファイル**）が setProfile される。再生開始の enterBar が因果 base へ作り直すため「完成形→リセット→成長」のフラッシュが再発。e371271 の回帰テストは `ctxTo: now`（cursor 設定済み）のみ固定しており、cursor 未確定（restore/初期化）経路が未カバーだった。
- **対策**: `refresh()` override を「growing push かつ forming 対応 tf」で cursor 未確定なら**何も描かず return**（未来リーク禁止・最初の描画は再生 1 フレーム目 enterBar の因果 base に遅延）へ修正。cursor 未確定の restore シーケンスで「全期間 fetchProfile を呼ばない／setProfile を一切描かない」を固定する回帰テストを追加（修正前 Red 実証）。
- **関連**: e371271（Fix#2）・ISSUE-041（統一成長モデル）。ブランチ fix/mp-restore-cursorless-flash。

## ISSUE-043: replay_ui MP sessions — restore 経路（cursor 未確定）で全期間 sessions → as-of-T への縮小ジャンプの疑い
- **重大度**: Low（視覚バグ疑い・未目視・ISSUE-042 と同クラス）
- **ステータス**: OPEN
- **検出**: ISSUE-042 の code-review（🟡・2026-07-06）。
- **背景**: sessions モードは `isGrowingPush()=false`（`_growing && !_sessions` を満たさない）のため、ISSUE-042 の cursor 未確定ガードの対象外。ページ読込 restore（`_untilTime` 未設定＝`to=undefined`）で基底 refresh が全期間 sessions 分割を setProfile し、再生開始後の `refresh(to=T)`（機構A・as-of-T）で縮小ジャンプが起きうる。1W/1M（forming 非対応 tf）は enterBar→null で後続リセットが無くフラッシュ不成立＝対象外（妥当判定済み）。
- **対策（案）**: sessions は描画経路が別（共有グリッド＋各日 tpo 整列）のため個別ハンドリング要。まずブラウザ目視で実挙動を確認してから対策設計する（ISSUE-042 のガードをそのまま流用しない）。
- **関連**: ISSUE-042・ISSUE-041（機構A: refresh(to,sessions)）。

## ISSUE-044: replay_ui — real_ticks（実ティック）再生の完了予想（ETA）が旧 800 点 cap モデルのままで実測と桁違いに乖離
- **重大度**: Medium（表示バグ・月足×実ティックで約 1,900 倍の過小推定）
- **ステータス**: RESOLVED（fix/replay-eta-real-ticks-tickvol 実装完了・TDD Red 実証（backend KeyError／wiring「5秒」vs 期待「3分00秒」）・replay python 146/146・replay web 199/199 全緑・architecture-executor 違反なし・code-review 承認可 🔴0（🟡 int(NaN) 防御は即時反映済み・🟡 fmtEta 時間単位表示は UI 変更のため依頼者判断待ち）。実データ検証: M1 volume 合算＝parquet 生 tick 数と完全一致（133,014=133,014）・残り11ヶ月足≒1,687万tick→s=1 で約28時間（旧表示53秒）。ブラウザ目視は依頼者実施）
- **検出**: 依頼者報告（2026-07-06）「月足×実ティック再生で『53秒（残り11足)』表示が実測と整合しない」。
- **背景**: `stream.js` で real_ticks は「接点検証＝全ティック（cap 廃止・間引かない・絶対仕様）」だが、ETA モデル（`timing.js: animBaseMs/estimatePeriodMs`）は旧仕様の ANIM_FINE=800 点 cap 前提（800×6ms＋固定費≒4.9秒/足 → 11足≒53秒）のまま。月足は 1 足に数十万〜300万超 tick（実測: 残り11ヶ月足合計 約1,687万tick → s=1 で約28時間）。per-bar 実測 EMA の自己補正も「最初の 1 足が終わらない」ため効かない。**参照実装（prototype_260626-01 replay.js）にも同じ不整合があり（cap 廃止 :436 時に ETA モデル :277-282 未更新）、cap 廃止後の正しい ETA の定義が無い** → CLAUDE.md 規則によりユーザーへ方針確認し「バックエンド拡張で正確化」を承認取得（2026-07-06）。
- **対策（承認済み設計）**: (1) backend `/candles`（jp225_tick）各足に `tickvol`（実 tick 数＝M1 volume の resample 合算・外れバー除去後）を追加（additive・volume 列無しデータセットは不変）。(2) `timing.js` に新規純関数 `remainingTickvol`（残り足の tick 総数・欠損は null）と `etaRealTicksMs`（tick総数×stepMs＋足あたり compute+足送り固定費）を追加（参照実装からの抽出でなく承認済み拡張である旨をコメント明記）。(3) `setEta` は real_ticks かつ tickvol 有りならこれを使用（tickvol 欠損は従来モデルへフォールバック・他モードは従来どおり＝回帰なし）。TDD: backend 1・timing 純関数 2・setEta 配線 3 の Red→Green。
- **関連**: prototype_260626-01（参照実装・同不整合あり）。ブランチ fix/replay-eta-real-ticks-tickvol。

## ISSUE-045: 価格軸ホイールズームがリプレイのバー境界（足リビール setCandles）で毎回リセットされる
- **ステータス**: RESOLVED（lwc v5.2 ネイティブ API（priceScale.setVisibleRange/setAutoScale）への置換で根本解消。indicator_ui chart_renderer 85/85・replay_ui web 209/209 全緑。E2E 実測: 軸 wheel ズーム(3500〜7500)→rp-next 1 バー後もスケール維持・dblclick で自動スケール復帰・時間軸不変）
- **検出**: 依頼者報告（2026-07-06）「価格スケール上のホイールで拡大縮小されない」→実機 E2E で「非再生時は動作・バー境界でリセット」と切り分け→依頼者の追撃質問（ドラッグとの挙動差・アーキテクチャ疑義）で真因特定。
- **原因**: 旧実装はホイールズームを自前 override（autoscaleInfoProvider＋_priceZoomRange）で実現し、`setCandles` 内の「時間足切替用リセット」が override を破棄していた。replay_ui は足リビールで setCandles を毎バー呼ぶため、バー境界のたびにホイールズームだけが消えた（軸ドラッグ＝lwc ネイティブ状態は残る＝挙動非対称）。真因は「lwc に価格レンジ setter が無い」という v4 時代の前提で自前機構を組んでいたこと。同梱 lwc は v5.2.0 で `priceScale.setVisibleRange()`（内部で autoScale=false 設定＝ドラッグと同一状態）を公開済み。
- **対策（依頼者承認済み・両UI統一）**: (1) handlePriceWheel/panPriceByPixels/isPriceZoomed/resetPriceZoom をネイティブ API（getVisibleRange/setVisibleRange/options().autoScale/applyOptions({autoScale:true})）で再実装（外側 interface 不変・controller 無変更）。(2) 自前機構（_priceZoomRange/_applyPriceProvider/_scaleMargins/__callAutoscaleProvider・setCandles のリセット）を全撤去。「手動スケールの解除はユーザーの dblclick のみ・システムは勝手に破棄しない」に統一（設計判断: 解除の判断はユーザーに属する）。(3) 回帰テスト「setCandles: 手動スケールを破棄しない」を追加・旧リセット固定テスト（🟡#1）を反転。chart_renderer.js は symlink 単一ソースのため indicator_ui/replay_ui 同時適用。
- **関連**: feature/replay-price-wheel-zoom。zoomedPriceRange/clampPriceRange（純関数・発散クランプ）は温存。

## ISSUE-046: indicator_ui の既存テスト2件が参照先モジュール欠損で失敗（既存・今回変更と無関係）
- **ステータス**: OPEN
- **検出**: 2026-07-06 ISSUE-045 対応中の全体テスト実行で検出。HEAD（変更前）でも同一失敗を確認済み。
- **内容**: `tests/replay_analysis.test.js` と `tests/timeline_player.test.js` が `js/usecase/replay_analysis.js` 等の不存在モジュールを import して ERR_MODULE_NOT_FOUND。79982b8「未追跡のソース/ドキュメントを保全コミット」でテストのみ保全されソース側が欠落した可能性。
- **対策案**: 対応方針は依頼者判断待ち（欠損ソースの復元 or テスト撤去）。

## ISSUE-047: replay_ui MP — 再生中にプロファイルのバーのスケールが変動する（表示 bin 幅 binw が累積レンジ拡大のたびに再導出される）
- **重大度**: Medium（視覚バグ・再生中の分析可読性を毀損）
- **ステータス**: RESOLVED（fix/replay-mp-locked-binw 実装完了・案 a 依頼者承認済み。TDD Red→Green（domain 6 ケース＋actor 回帰 5 ケース）・replay_ui web 214/214・market_profile web 210/210 全緑・architecture-executor 違反なし・code-review 承認可 🔴0（🟡 null ロック恒久メモ化は「成功値のみキャッシュ＋再試行」で即時反映済み・回帰テスト追加）。実装: GrowthWindow.lockedBarw（domain 純関数・from 直前 ceil(86400/barSec(tf)) 本の因果履歴レンジ/bins）＋ replay actor が成長 push の bins モード時のみ resmode=range/range=barw を注入（ユーザー明示 range 温存・ロック不能時は bins フォールバック＝非破壊）。実データ検証: jp225_tick 5m×24 バーで binw が 28.6pt 近傍に安定（丸め揺れ ±数%・従来は 0.33→4.9pt へ 15 倍伸長）。ブラウザ目視は依頼者実施。🔵記録: 全期間プリセット（replayStart=0・履歴ゼロ）は従来 bins のまま／_MAX_BINS=1000 到達（ロック時レンジの千倍・実質非到達）で再スケール再開／1W/1M への注入は無駄計算のみ（非破壊））
- **検出**: 依頼者報告（2026-07-06）「MPプロファイルを再生すると、プロファイルのバーのスケールが変動する」。
- **原因（実データで再現済み）**: 成長 push（normal growth・from=replayStart）は enterBar（毎バー）/growTo（グリッド外 tick）ごとに `/market_profile_forming` を再取得し、backend が表示グリッドを毎回「窓 [replayStart, now] の実測レンジ ∪ forming tick 実測 min/max」から再導出する（`market_profile_forming_controller._reconcile_session_range`＋`market_profile_controller` L330-334）。既定 resmode=bins（bins=60 固定）ではレンジ拡大のたびに binw=(priceMax−priceMin)/60 が伸び（実測 jp225_tick 5m・6 バーで 0.33→2.05pt、11 バーで 0.92→4.92pt）、primitive の barH（=隣接 bin 中心のピクセル距離）と norm（現在最大 bin 基準の相対正規化）が全バー再計算される＝再生中にプロファイル全体が繰り返し再スケール。参照実装（prototype_260630-01・present normal）はレンジが実質静的（全期間窓）のため bins=60 でも binw が安定しており、本症状は「成長する窓 × bins 固定」の組み合わせで顕在化した replay 固有の新規事象（参照実装に成長グリッドの規定なし）。
- **対策（案・依頼者判断待ち）**: (a) 成長中は binw を固定し nBins を可変にする（成長開始時に binw を確定・以降はレンジ拡大で bin 数だけ増加＝古典的 MP のティックサイズ固定と同型・推奨）。(b) binw を離散ステップ（例 GRID_W×2^n）でのみ更新するヒステリシス（再スケール頻度を激減・bins 意味論は概ね温存）。(c) 現状仕様のまま（bins=60 は「現在レンジを常に 60 分割」という意味論と割り切り、気になる場合は resmode=range（barw 固定）を使う運用）。
- **関連**: ISSUE-041（統一成長モデル Model A）・replay.js mpBaseFrom（from=replayStart 累積）・DwellAccumulator（クライアント側は同一グリッドで増分、再取得時のみグリッド交換）。

## ISSUE-048: indicator_ui ライブモードで価格が更新されない（表示データセット jp225_tick へのライブ供給プロセス不在）
- **ステータス**: RESOLVED（fix/live-tick-watch 実装完了・TDD Red 実証・marketdata+tools 172／indicator_ui tools 59／indicator_ui api 342 全緑・architecture-executor 🟡2件対応済み・code-review-executor 承認可 🔴0（🟡見せかけ緑テストは識別力を実証付きで修正済み）・E2E `--once` 実測で M1 末尾=直前確定分／形成中除外／rollups 生成を確認。ブラウザ目視（serve.sh 再起動→localhost:8000 でライブ足の伸長確認）は依頼者実施）
- **重大度**: High
- **検出**: 依頼者報告（2026-07-06）「ライブモードで価格が更新されない」。
- **原因**: 6/27 e6651a3 でチャート表示データセットを jp225_tick（tick 由来）へ切替時、serve.sh の watch（jp225_m1 系＝export_jp225_m1 --watch）を向け直し忘れ、かつ起動時取得も `--skip ticks --skip ingest` で tick 系を除外していた。結果、jp225_tick 系（`ticks/YYYY/MM/DD/JP225_ticks.parquet` → `jp225_tick_m1.csv` → `rollups/jp225_tick/`）は手動バッチのみで更新され 7/2 13:14 で凍結。`/forming_bar` は当日 parquet 不在で常に null（5 秒足内更新が完全 no-op）、`/candles` も新規足なしとなり価格が伸びない。実測: tick フィードはほぼリアルタイム（22:27 時点で 22:27:00 の tick 取得）・M1 足フィードは約 2h9m 遅延・当日全量再取得 12.4s。
- **対策（依頼者承認済み・「tick watch 毎分・全日再取得」）**: (1) `tools/live_tick_watch.py` 新設（毎分: 当日 tick 全量再取得→原子スワップ→ M1 分単位増分追記（`until=floor(now,1min)` で形成中分バー除外・`start=full_start` で catch_up 済み丸日 parquet からの欠損日自己修復も委譲）→ `rollups/jp225_tick` 差分更新。起動時 1 回 catch_up＝既存最新日（部分日）の上書き自己修復＋昨日までの丸日追い付き）。(2) `indigators/indicator_ui/serve.sh` へ既存 M1 watch と併走で配線（ログ `data/marketdata/live_tick_watch.log`・cleanup trap で kill・`--no-update` 時は不起動）。(3) `marketdata/tick_m1.py` の `append_m1_from_ticks`/`build_m1_from_ticks` へ省略可能 `until` を追加（`until=None` は既定挙動 byte 不変）。
- **アーキ精査対応（architecture-executor 指摘）**: 🟡tick tree レイアウト三重定義→ `marketdata.tick_m1.day_parquet_path` を単一権威として新設し reader/writer が共用。🟡`run_watch` のクロスアクター所有→ 汎用ポーリングループを `tools/watch_loop.py` へ移設（export_jp225_m1 は後方互換 re-export）。🟢`.empty` マーカー整合→ parquet 書込成功時に同日マーカーを除去。
- **テスト**: `marketdata/tests/test_tick_m1.py`（until 除外・until=None 回帰固定 3 本追加）、`tools/tests/test_live_tick_watch.py`（新規 14 本・全フェイク／ネットワーク禁止。回帰固定: gap-day 自己修復／部分日上書き再取得／.empty 除去／形成中除外）。marketdata+tools 172 緑・indicator_ui tools 59 緑。E2E 実測: `--once` を隔離 data-dir で実行し「M1 末尾=直前確定分・形成中分除外・rollups/jp225_tick 生成」を確認。
- **関連**: fix/live-tick-watch。参照実装: tools/build_tick_rollup.py・simulator/tools/fetch_ticks_ymd.py・export_jp225_m1.py。

## ISSUE-049: indicator_ui ライブ価格を12秒固定遅延のなめらか tick 再生へ強化（ジッターバッファ配信・記録系と分離）
- **ステータス**: RESOLVED（依頼者承認済み・prototype_260707-01 が参照実装。実装＋TDD Red→Green 完了。architecture-executor 合格（依存方向/ベンダ隔離/責務分離すべて違反0）・code-review-executor 承認可（🔴0）。レビュー🟡4件をすべて回帰テスト付きで是正済み（下記）。最終テスト: marketdata 116 緑・indicator_ui api 355 緑・web 525 緑/2fail=既知 ISSUE-046（無関係）。ブラウザ目視は依頼者実施）
- **レビュー🟡是正（回帰テストで識別力を実証済み）**: 🟡1 `LiveTickBuffer._run_loop` の待機を `threading.Event.wait` へ変更＝stop() が最大60s backoff 中でも即座に thread を終わらせ orphan/二重起動を防ぐ（回帰: stop が<1.5sで戻る）。🟡2 サーキットブレーカ停止中の再チェック粒度を参照実装どおり1秒へ（`_poll_once` が待機秒を返し pause 時は backoff を上乗せしない＝停止明けを最大1秒で検知）。🟡3 後退ガード（`periodSec < bar.time` の tick 無視）の回帰テスト追加（旧コードで Red 実証）。🟡4 tf 切替シードの await 中に `_bar=null` へ倒し再入 playback が旧 tf バーへ誤描画するのを防止（未使用 `_seeding` を除去・回帰: tf切替 seed 保留中の再入で不描画）。
- **重大度**: Medium（価格表示の品質改善・記録系へ非干渉）
- **検出**: 依頼者要望（2026-07-07）「ライブ価格を 12 秒固定遅延のなめらか tick 再生（ジッターバッファ）へ強化したい」。ISSUE-048（tick watch）でライブ供給プロセスは復旧済みだが、価格更新は 5 秒/60 秒ポーリングの階段状で、feed のまとめ配信（3.8〜5.5s）で不連続だった。
- **実測（prototype_260707-01 で検証済み）**: 上流 feed 側 lag は 3.8〜5.5s（25 polls 実測）＋fetch 1.2s＋ポーリング間隔 5s。よって最小遅延を **12s** 未満にすると再生が枯渇（underrun）する。「真の 5 秒遅延」は公開フィード（dukascopy freeserv 増分カーソル）では不可、というのがスパイクの発見。12s 固定遅延・100ms 粒度適用・バッファ保持 30 分で枯渇 0 を確認。
- **対策（承認済み設計・記録系と完全分離）**: (1) `marketdata/dukascopy_source.py` に `fetch_ticks_since(cursor_ms)` を追加（`dukascopy_python._fetch` 増分カーソル API の薄いラッパ・private API 依存を境界へ隔離・prototype server.py:60-66 と同呼び方・既存クラス無改変）。(2) `indigators/indicator_ui/api/adapter/compute/live_tick_buffer.py` 新設（`LiveTickBuffer`：background thread で 5 秒周期の増分ポーリング・カーソル維持・指数バックオフ 5→…最大60s・連続8失敗で10分停止のサーキットブレーカ・バッファ保持30分・**メモリのみ＝parquet/M1/rollups へ非干渉**）。(3) `framework/server.py` に GET `/live_ticks?since=` を追加（`set_live_tick_buffer` で注入可能・serve() で buffer.start()・既存 endpoint 不変）。(4) `web/js/adapter/front/live_tick_player.js` 新設（`LiveTickPlayer`：2.5s poll＋100ms playback・`serverNow-12000` 以前の tick を現在 tf の形成中バーへ累積・/forming_bar シード・全注入で DOM/ネット/タイマー非依存）。(5) 価格の二重書き排除：`LiveUpdater`/`FormingBarUpdater` に optional `suppressPriceUpdate`（既定 false＝byte 不変）を追加し、player 稼働時（served）のみ true で updateLastCandle を skip（指標再計算は従来どおり）。composition_root_front.js が served のとき player を組み立て両者へ true を渡す（file://=A方式は player=null・既存挙動 byte 不変）。
- **テスト（TDD・全フェイク／ネットワーク禁止）**: marketdata `test_fetch_ticks_since_s6.py`（4本・_fetch monkeypatch・カーソル超過分のみ・(ms,bid,ask)変換）／api `test_live_tick_buffer.py`（10本・fetch_fn/time_fn/sleep_fn フェイク：カーソル維持・30分トリム・バックオフ倍化/60上限・8連続失敗→10分停止・ticks_since 境界・start/stop 冪等・既定 fetch_fn 遅延解決）・`test_server_smoke.py`（+2・buffer 注入/未注入）／web `live_tick_player.test.js`（9本・12秒遅延境界・バー境界新バー・tf切替シード再取得・シード null で no-op・start/stop 冪等・clockOffset 補正）・`forming_bar_updater`/`live_updater` の suppressPriceUpdate（各+2・true でupdateLastCandle不呼出＋recompute は呼ぶ／未指定で byte 不変）・`composition_root_front`（+3・served で player 組み立て＋suppress 伝搬／file:// で null）・`build_module_order`（MODULE_ORDER に live_tick_player 追加）。
- **残リスク/TBD**: 初回部分バーの高安はシード（/forming_bar・最大60秒粒度）＋12秒遅延の tick 構成のため隙間分だけ粗い近似（コメント明記）。volume は「シード値＋適用 tick 数」の近似。ブラウザ目視（serve.sh 再起動→localhost:8000 でなめらか再生・枯渇なし確認）は依頼者実施。
- **関連**: feature/live-tick-replay（worktree）。参照実装: prototype_260707-01（server.py + web/index.html・依頼者実機確認済み）。

## ISSUE-050: replay MP stale プロファイル・フラッシュ（後方スクラブで await fetchForming 中に旧・広プロファイルが露出）
- **重大度**: Medium（一過性視覚バグ・約180msだけ MP 上端がローソク上端を超えて食い違う）
- **ステータス**: RESOLVED（fix/replay-mp-stale-flash・TDD Red→Green 実証済み・replay web 214/214 全緑・present 非波及・architecture-executor 構造合格＋観点5対応・code-review-executor 承認可 🔴0／ブラウザ目視は依頼者実施）。
- **検出**: 依頼者報告（実測で根本特定済み）。バー後方ジャンプ時（例 bar1455→1429）にローソクは同期リビール（replay.js render preRender の `view.setCandles(candles.slice(0,bar+1))`）される一方、MP は `await marketProfile.enterBar(t,...)`→`_rebuildAt`→`await fetchForming`（実測≈180ms）の間ずっと前カーソルの広い（未来価格を含む）プロファイルを描き続けるため、settle 後に as-of-T プロファイルへ自己修正する一過性フラッシュが出る。**未来リーク（DB）でも完成形フラッシュ（/market_profile 経路）でもない**（実測: /market_profile 0回・/market_profile_forming の now=T・priceMax は因果的に正しい）。
- **原因**: `replay_market_profile_actor.js` `_rebuildAt` はカーソル変更時の同期クリア/クランプを持たず、`await fetchForming` 中は旧 accumulator のまま描画を保持する。加えて連続/後方スクラブで古い応答が新描画を上書きしうる（世代ガード不在）。参照実装 prototype_260630-01 `fetchProfileOnly` は profSeq 世代ガードで stale 応答を破棄するが、reveal の `_rebuildAt` に同等ガードが無かった。
- **対策（依頼者承認済み・2点セット・subclass 内で完結）**: (1) **同期クランプ（blank 禁止）**: enterBar（バー変更）で `await fetchForming` に入る前に、旧 accumulator のスナップショットを新カーソルの revealed 上限（`_getCandles()` 末尾までの high 最大＝ローソク上端）へ同期クランプして即描画する。revealed 超の bin/POC/VA を落とし、空描画（ISSUE-052 の全消滅フラッシュ）は避けて clamp 結果を必ず即描画する（primitive 無改変・純関数 `_clampProfileToMax` で profile 側 bin 除去・norm/n_bins/tpo_units も残存 bin 基準へ再計算）。**部分重複**（例 1455→1429）は revealed 超 bin を落とした clamped を描く。**完全非重複**（旧プロファイルが全て revealed 超・例 1499→400）は空 bin（blank）を描いて旧 stale 広プロファイルを消す＝ISSUE-052（**有効 bin あり**の全消滅禁止）とは保護対象が異なり非矛盾（revealed 域に有効 bin が無いのが正直な状態・keep-stale は 1499 帯 MP が 400 帯ローソク上へ浮き ISSUE-050 同クラスの欠陥を再生するため不可＝architecture-executor 観点5 対応）。growTo（同一カーソルのグリッド拡張）は clamp しない（成長中 tick の誤 clip 回避）。(2) **世代ガード（profSeq 相当）**: `_rebuildAt` に単調増加 `_rebuildSeq` を持たせ、`await fetchForming` の戻りが最新 seq でなければ setProfile を破棄する（参照 prototype_260630-01 fetchProfileOnly に忠実）。
- **回帰テスト**: `simulator/replay_ui/web/tests/replay_market_profile_actor.test.js` に 5 本追加。(1) 後方スクラブ（部分重複）で await fetchForming 解決前に同期クランプが revealed max 超 bin/VA を除去して setProfile（Red 実証）。(1b) 完全非重複ジャンプは空 bin（blank）で旧 stale を消す（Red 実証）。(2) 古い応答が新描画を上書きしない世代ガード（Promise 解決順制御・Red 実証）。(3) getCandles 未注入では同期クランプ非発火（present 非波及）。(4) growTo は同期クランプしない（enterBar 限定）。全フェイク・ネットワーク不使用。
- **関連**: 参照実装 prototype_260630-01（profSeq）・prototype_260626-01（因果リビール駆動）。ISSUE-051（ローソクのみ同期 fold）・ISSUE-052（MP 全消滅フラッシュ回避＝blank 禁止の根拠）・ISSUE-042/e371271（完成形フラッシュ・別経路）。present（indicator_ui）挙動 byte 不変（replay subclass に閉じる）。ブランチ fix/replay-mp-stale-flash。

## ISSUE-051: replay_ui — 再生中、足リビール直後に「完成足」が0.5〜1.5秒露出してから足内形成が始まる（完成足フラッシュバック）
- **改番メモ**: 旧 ISSUE-048。並行開発の live-tick 系（feature/live-tick-replay）と同番号衝突のため 2026-07-08 develop 統合時に ISSUE-051 へ改番（コミットメッセージ/コード注釈の旧番号は履歴として残置）。
- **重大度**: Medium（視覚バグ・因果リビール再生の体験毀損。MP 有効時のみ顕在化）
- **ステータス**: RESOLVED（fix/replay-mp-locked-binw 実装完了・依頼者承認（両方修正）。TDD Red→Green（wiring 順序固定「畳み込み update < enterBar」＋ガード反対側「playing=false は畳まない」の 2 本）・replay_ui web 219/219 全緑・architecture-executor 違反なし・code-review 承認可 🔴0（🟡2＝手動ナビ回帰テスト・コメント文言是正は即時反映済み）。実装: render() の enterBar await より前（リビールと同一同期ブロック＝paint 前）に playing かつ mode≠math のとき最新足を始値の同事足へ畳む（プロトタイプ不変条件の復元・手動ナビ/math は従来どおり）。E2E 実測: 完成足露出 513〜1522ms → 0〜1ms。ブラウザ目視は依頼者実施）
- **検出**: 依頼者報告（2026-07-07）「足にフラッシュバックが発生」→ Playwright E2E 実測（5m×real_ticks×▶再生）で再現・定量化：完成足の setData 露出から最初の形成 update まで 513ms／1522ms（series.setData/update ラップ計測）。
- **原因（実測・参照実装照合済み）**: 参照実装 prototype_260626-01 replay.js は「drive() の completed 足リビール → animateForming 冒頭の同期畳み込み（『fetch を await する前（同期）に最新足を始値へ畳む＝paint 前に上書き』）」の間に await を挟まない不変条件で完成足のチラ見せを防いでいた。本番 replay.js は render() の preRender（setCandles＝完成足リビール）の後・render 終了前に `await marketProfile.enterBar(t, mpBaseFrom())`（L145・HTTP＋サーバ dwell 集計）を挿入したため、この await 中にブラウザが paint し、完成足が enterBar レイテンシぶん（実測 0.5〜1.5s）露出してから animateForming の畳み込みが走る。MP 無効時は await が無く従来どおり露出しない（＝MP 配線時に持ち込まれた退行）。
- **対策（案・依頼者判断待ち）**: (a) 再生中（playing かつ mode≠math）のみ render() の preRender 直後に同期で最新足を始値へ畳む（プロトタイプの畳み込みを前倒し・手動ナビ（rp-next/slider/prev）は従来どおり完成足表示＝非破壊。animateForming 冒頭の既存畳み込みは防御として温存）（推奨）。 (b) enterBar を animateForming の畳み込み後へ移設（経路二重化のリスク）。
- **関連**: prototype_260626-01/web/js/replay.js（参照実装・畳み込み不変条件）・ISSUE-047（同セッションの MP 配線）・render L126「アトミック」コメント。

## ISSUE-052: replay_ui MP — 再生中、縮退グリッド［0,1］の空スナップショットを feedTick が描画し、プロファイルのバーが毎バー消滅→復帰する（バーのフラッシュバック）
- **改番メモ**: 旧 ISSUE-049。並行開発の live-tick 系（feature/live-tick-replay）と同番号衝突のため 2026-07-08 develop 統合時に ISSUE-052 へ改番（コミットメッセージ/コード注釈の旧番号は履歴として残置）。
- **重大度**: Medium（視覚バグ・再生中 MP バーが周期的に全消滅）
- **ステータス**: RESOLVED（fix/replay-mp-locked-binw 実装完了・依頼者承認（両方修正）。TDD Red→Green（feedTick/settleTick 縮退中非描画・縮退のままの growTo 非描画・実グリッド確定で描画再開の 3 本）・replay_ui web 219/219 全緑・architecture-executor 違反なし・code-review 承認可 🔴0。実装: _rebuildAt で縮退（forming tick 0＋レンジ<=1）を _gridDegenerate に状態化し、縮退中は feedTick/settleTick/growTo の描画も抑止（前回描画保持）・実グリッド確定で解除。skipDegenerateDraw 引数は撤去（enterBar/growTo 同一基準へ統一・レビューで単一価格プロファイル（tick 有り range=0）の誤抑止なしを検証済み）。E2E 実測: 縮退空描画 9 連発×毎バー → 0 回。🔵記録: 再生セッション開始直後は最初の growTo 確定まで直前の（旧窓の）プロファイル表示が残る（ブランクは出さない・約 1 秒・意図的トレードオフ）。ブラウザ目視は依頼者実施）
- **検出**: 依頼者報告（2026-07-07）「バーにもフラッシュバックが発生」→ Playwright E2E 実測（5m×1週プリセット×real_ticks×▶再生）: setProfile ログで「実プロファイル → price_min=0/price_max=1/tpo=0 の空描画×9回（約1.1秒）→ growTo で実グリッド復帰」の周期を捕捉。
- **原因（実測）**: 再生開始バー（from=replayStart かつ formingStart=from）では base 窓 [from, formingStart−1] が空集合になり、backend が空プロファイル（price_min=0/price_max=1）を返す→ _rebuildAt は skipDegenerateDraw で**自身の描画はスキップ**するが accumulator は縮退グリッドで作り直す（growTo の土台・設計どおり）→ 直後の **feedTick throttle 描画には縮退ガードが無く**、縮退 accumulator の空 snapshot（[0,1]・全 bin ゼロ）を setProfile→ MP バーが全消滅。最初の out-of-grid tick で growTo が実グリッドを確定するまで（実測 約1.1s）ブランクが続き、再生ループの enterBar 再実行のたびに繰り返す。
- **対策（案・依頼者判断待ち）**: (a) _rebuildAt で縮退判定を状態化（例 _gridDegenerate）し、縮退中は feedTick/settleTick の描画も抑止（前回描画保持・enterBar の skipDegenerateDraw と同一基準）。growTo の実グリッド確定で解除（推奨・回帰テスト付き）。 (b) 縮退時に accumulator を入れ替えない（growTo 土台の設計コメントに反する・非推奨）。
- **関連**: ISSUE-047（binw ロック・本件とは独立で再現）・replay_market_profile_actor.js feedTick/settleTick/_rebuildAt・market_profile_controller.py L318（空データ→ゼロプロファイル）。

## ISSUE-053: indicator_ui ライブモードで短周期足（1m/5m/15m）の形成中バーが更新されない（seed null 固着＋parquet フロンティア遅延）
- **ステータス**: RESOLVED（自己シード＋seed鮮度化 実装完了・TDD Red→Green・web live_tick_player 15/15＋関連47緑・API 360緑・既存2 failure（replay_analysis/timeline_player）は develop 既存＝無関係。ブラウザ目視は依頼者実施）
- **重大度**: 中（ライブUXの中核・短周期足で価格が伸びない）
- **検出**: 依頼者報告（2026-07-09）「1分・5分・15分の時間足がライブ更新されない」。
- **原因（実測で特定）**: (1) `/forming_bar` の読み元が毎分フル再取得の当日 tick parquet（ISSUE-048 の正確性優先設計）で、フロンティアが実測 44 秒後方。現周期の窓 `[floor(now,tf), now)` の経過秒が遅延gapを下回る間、窓が空になり `bar=null`。短周期ほど頻発（1m=ほぼ常時／5m/15m=各周期先頭／長足=無縁）。実測: now=11:40:32・parquet最終tick=11:39:48・gap=44s で 1m/5m=null・15m=OK。(2) 増幅バグ: `LiveTickPlayer._seed` は tf 変更時しか再シードせず、seed が null だと `_bar=null` が固着し `_applyTick` が tick を捨て続ける（参照実装 prototype_260707-01 の `!bar→新バー` 自己シード挙動から移植時に逸脱した退行）。served では player が価格の唯一の書き手のため短周期足が完全停止。
- **対策（依頼者承認済み・自己シード＋seed鮮度化）**: (1) フロント `web/js/adapter/front/live_tick_player.js`: `_applyTick` を参照実装へ復帰＝`_bar===null` でも現周期 live tick から自己シード（`_seeding` フラグで seed await 中の再入描画は抑止＝🟡4 保持／現 live 周期より前の tick は自己シードせず /candles 履歴を後退させない）。(2) バックエンド `api/framework/server.py` `_handle_forming_bar`: parquet 経路が None のとき in-memory `LiveTickBuffer`（/live_ticks 同源・near-real-time）へ fallback して現周期バーを組む（seed 鮮度化）。純関数 `forming_bar.forming_bar_from_buffer_ticks` を新設。共有 `forming_bar()`（指標計算 apply_forming_bar 経路）は不変＝挙動ドリフトなし。
- **テスト**: web `live_tick_player.test.js`（+2: seed=null 自己シード／現周期前 tick は非自己シード）・api `test_forming_bar.py`（+2: buffer 集計／空窓 None）・`test_server_smoke.py`（+2: parquet null→buffer fallback／非対応tf は buffer 非参照）。既存回帰（🟡4 再入・後退ガード・1W/1M null）全保持。
- **関連**: ISSUE-048（毎分フル再取得＝遅延の出所）・ISSUE-049（LiveTickPlayer/LiveTickBuffer）。参照実装: prototype_260707-01/web/index.html:63-66（applyTick 自己シード）。

## ISSUE-054: market_profile 日別プロファイル（src=dwell）でレンジ(barw)変更が描画へ反映されない（バーが更新されない）
- **重大度**: Medium（パラメータが無効・ユーザー操作が効かない。データ取得は正常だが描画が固着）
- **ステータス**: OPEN
- **検出**: 依頼者報告（2026-07-11）「レンジを500に設定したが更新されない」（ソース=滞在時間(実ティック)/表示モード=日別プロファイル）。実UIで再現・確定。
- **原因（実UI実測・切り分け済み）**: バックエンド／リクエストは正常、描画のみが barw を無視する。
  - フロント送信は正常: OK 押下で `GET /market_profile?...&barw=500&src=dwell&sessions=1` を送出（barw を正しく写像）。レンジ25 では `barw=25`。
  - バックエンド応答も正常: barw=500→`n_bins=130`/`bar_width=501.47`、barw=25→`n_bins=1000`(65000pt レンジで 1000 クランプ)/`bar_width=65.19`。sessions[].tpo 長も 130 vs 1000 と解像度追従。ブラウザは両応答を受信。
  - **描画が固着**: 日別プロファイル領域を barw=500/100/25 で撮影しピクセル差分＝**0.00%（changed_bbox=None・完全一致）**。応答の sessions.tpo（130/1000）が描画に反映されていない。POC/VA 値は変化（69256→69640）＝応答の一部は使われるが、日別バー histogram は barw 非依存の固定解像度で描かれている。
  - primitive は `s.tpo` を描く（market_profile_primitive.js:185-228）、actor `_buildSessionView` は `s.tpo` を素通し（market_profile_actor.js:32）。にもかかわらず不変。
- **根因（コードトレースで確定・2026-07-11）**: **tf-period 列描画が日別 sessions 描画を上書きし、tf-period は barw を持たない**。
  - dwell 日別モード＋対応 tf（1m〜1D＝`isPlayerTimeframe`）のとき、composition_root_front.js:352-360 が可視レンジ変化ごとに `tfPeriodActor.setEnabled(true)`→共有 primitive の `setTfPeriods(columns, unit)` を呼ぶ。
  - primitive `_draw`（market_profile_primitive.js:301）は `this._tfPeriods` 非 null を**最優先で早期 return**し、`_drawSessions`（barw 応答の `s.tpo`／`this._profile.bins` で描く経路）へ到達しない。
  - 可視バーは `_drawTfPeriods`（同255-296）が `/tf_period_profile`（tf_period_profile_client.js:10 `buildTfPeriodUrl` は `datasetRef/timeframe/from/to` のみ＝**barw 無し**）の列を固定 `unit`（最小価格単位）解像度で描く。よって barw 変更（`/market_profile?...&barw=` → `this._profile`/`this._sessions` のみ更新）は可視描画へ一切波及せず **0.00% 差分**になる。
  - 起票時 2 仮説は両方反証: ①DwellAccumulator(GRID_W=10) 上書き→`_isIncremental()` が `!this._sessions` 要求で sessions では未使用。②setSessions 未反映→POC/VA 読取欄が 69256→69640 と変化＝`_buildSessionView` が新 `s.poc` を生成＝setSessions は新 tpo を受領済み（描画のみが tf-period に覆われる）。
  - なお tf-period 優先は**意図的設計**（コミット 28e1548・comp root 330 行コメント「tf-period を優先＝旧 per-day sessions を上書き」・ISSUE 起票後に追加）。
- **実UI確認（served B・port 8137・Playwright 実ブラウザ・実HTTP・2026-07-11）**: dwell 日別＋日足（1D＝player tf）で確定。
  - 実HTTP: `/market_profile?...&barw=500&src=dwell&sessions=1`（barw 正送出）と同時に `/tf_period_profile?...&timeframe=1D&from=&to=` が可視窓ぶん**275本超**発火（**barw 無し**）＝tf-period 経路が実際に有効。barw を 500→25 に変更しても `/tf_period_profile` 群は不変（barw を持たないため）。
  - 実画素（barw=500 と barw=25 の viewport 比較）: **プロファイルバー描画領域（チャート本体 y120-400）は無変化**（40px バンド毎 18-22px＝十字線の縁のみ）。全体差 0.96% は上部読取欄（POC/VA テキスト）＋十字線/軸ラベルのみ＝プロファイル本体は barw で一切変わらない。読取欄 POC/VA が変わる＝`/market_profile` 応答は受信・一部（読取）使用されるが histogram は tf-period に覆われる、という起票時の観察と一致。
- **依頼者の真の目的（2026-07-11 追加）**: barw を荒くした動機は**描画完了までが遅くストレス**だったため。まず描画速度を解決したい（barw は速度回避策の試みだった）。
- **描画遅延の主因（実測確定・served B port 8137）**: **tf-period 列の細切れ大量リクエスト**。
  - jitter buffer は `windowSec=6h` 固定・`cacheMax=12`（intraday 想定）。1D で可視レンジが数十日に及ぶと 6h 窓が過密になり、可視 68 日で **274 リクエスト・うち 224本(81%)が空（93B＝日足カラム時刻 00:00 を含まない窓）**。単一スレッド server で直列化＝**約 8.0s**（29ms/req）。
  - tf-period 全 span 1 本でも **2.5s・12.6MB・サーバキャッシュ無し**（連続実行も 2.4〜4.9s で不変）。barw は tf-period に届かず、かつ**リクエスト本数を減らさない**ため、レンジを荒くしても描画は速くならない。
  - 対して旧 sessions 描画 `/market_profile?...&sessions=1&barw=` は **1 本**: barw=500 で **0.66s/51KB**、barw=25 で 4.6s/352KB＝**barw が荒いほど速い**（n_bins 減で応答縮小）。sessions データは日別モードで常に取得済み（実HTTP #144 等）＝tfPeriod を止めれば `_drawSessions` が即描く。
- **対策（案・依頼者判断待ち）**: 目的が「描画高速化」に確定したため、速度の観点で再整理。
  - **推奨: 1D（以上／広域）で日別を sessions 描画へ**（tf-period を intraday 用に留める）: 8s→0.66s（≧12x）＋barw が効く（荒い=速い）。低リスク（sessions データは既取得・`_draw` 早期 return を日別で無効化 or comp root で 1D は tfPeriod 非有効化）。
  - 代替（tf-period 維持で速度のみ改善）: windowSec を tf 連動（1D→数十日窓）で 8s→~2.6s／全span 1 本化／サーバ側キャッシュ導入。いずれも 2.5s・12.6MB は残り barw は無効のまま。
  - 実装前に依頼者の方向決定を得る。
- **検証環境**: served B方式（framework.server・port 8137）・datasetRef=jp225_tick・Playwright(実ブラウザ)・実HTTP。
- **関連**: market_profile_dwell.py（sessions は n_bins を反映＝バックエンド正常）・market_profile_client.js:29-31（barw 写像）・ISSUE-052（dwell accumulator 縮退グリッド）・ISSUE-055（描画遅延＝本件の真因）。

## ISSUE-055: market_profile 日別プロファイル（dwell・tf-period列）の描画完了が遅い（1Dで約8秒・体感ストレス）
- **重大度**: High（主要操作のたびに数秒待ち・実用性を損なう。ISSUE-054 で barw を荒くした動機＝この遅延の回避策だった＝本件が真因）
- **ステータス**: RESOLVED（A＋windowSec＋B を実装・実UI検証済み・2026-07-11）
- **検出**: 依頼者報告（2026-07-11）「描画までの時間が長すぎてストレス」。ISSUE-054 調査中に真因として分離。
- **原因（実測確定・served B port 8137・実HTTP）**: **tf-period 列取得の細切れ大量リクエスト（fan-out）**。
  - jitter buffer（tf_period_jitter_buffer.js）は `windowSec=6h` 固定・`cacheMax=12`（intraday 想定の設計）。1D は可視レンジが数十日に及ぶため 6h 窓が過密になり、可視 68 日で **274 リクエスト／うち 224本(81%)が空（93B＝日足カラム時刻 00:00 を窓が含まない）**。単一スレッド server で直列化＝**実測 約 8.0s**（29ms/req）。
  - tf-period 全 span 1 本でも **2.5s・12.6MB・サーバキャッシュ無し**（連続実行も 2.4〜4.9s で不変）。**barw は tf-period に届かず、かつリクエスト本数を減らさないため、レンジを荒くしても描画は速くならない**（＝ISSUE-054 の barw が効かない件と同根＝market_profile_primitive.js:301 の tf-period 優先 early-return）。
  - 参考: 旧 sessions 描画 `/market_profile?...&sessions=1&barw=` は 1 本で barw=500→0.66s/51KB・barw=25→4.6s/352KB（荒いほど速い）。
- **原因・追加（実測確定・ローリング＝スクロール/パン時・2026-07-11）**: 遅さ/フラッシュの主症状は**ローリング時**に出る。**5m も安全ではない**（当初「1m/5m は 6h のまま安全」は**誤り＝訂正**）。
  - 配線: `chart.timeScale().subscribeVisibleTimeRangeChange` が**スロットルなし**で発火し、毎回 `tfPeriodActor.setEnabled(true)`→`refresh()`→`ensure()`（fetch fan-out）＋`_render()`（全再描画）を呼ぶ（composition_root_front.js:352-360）。加えて各チャンク到着（jitter buffer onReady）ごとに全再描画（tf_period_profile_actor.js:42-48）。
  - 5m 実測: 可視 4.5 日＝**18 チャンク（6h 窓）だが cacheMax=12** → 可視中の列が LRU 破棄され出入り＝フラッシュ。約0.5秒の横ドラッグで **tf_period 23 リクエスト・再描画 44 パス・再描画が 5.7 秒間継続**（ドラッグ後もトリクル到着ごとに再描画）＝「遅く重い」。
- **対策（依頼者承認済み方向「tf-period 維持で速度のみ改善」・ローリング判明で拡張）**: 表示・列の見た目は不変のまま、ローリングを軽くする 3 点。
  1. **可視レンジ変化ハンドラをスロットル/合体**（rAF or 末尾 ~150ms）＝ドラッグ中の refresh/fetch 連発を 1 回へ集約（composition_root_front.js）。
  2. **onReady 再描画を合体**（rAF で 1 フレーム 1 回）＝44 パス→数回（tf_period_profile_actor.js）。
  3. **windowSec を tf 連動＋cacheMax を可視 chunk 数以上に**＝チャンク数を画面あたり少数に有界化し、fan-out（1D 274本）と eviction フラッシュ（5m）を同時に解消（tf_period_jitter_buffer.js）。規則 `windowSec(tf)=barSec(tf)×K`（K は 1 画面が数チャンクに収まる値）。
  - 実装は front＋backend。backend は任意窓を span 上限なしで処理可（tf_period_profile_controller.py 確認済み）。
- **実装（2026-07-11）**:
  - A: composition_root_front.js の subscribeVisibleTimeRangeChange を末尾デバウンス（150ms）化。ON のローリング取得は停止後 1 回だけ ensure+描画（ドラッグ中は既取得列が primitive の毎フレーム再描画でパン追従＝fetch 0）。OFF は即時。
  - windowSec: tf_period_jitter_buffer.js に `windowSecForTf` 注入（未注入は固定 windowSec＝既存テスト不変）。comp root が `clamp(barSec×96, 6h, 45d)` を注入・cacheMax=32。1D=45d 窓／5m=8h 窓／15m=1d 窓。
  - B（per-day キャッシュ・依頼者要望で窓単位から拡張）: tf_period_profile_controller.py を **カレンダー日粒度**のキャッシュへ。窓 `[from,to)` を日分割し、各日を `(symbol,tf,day_start)` で **メモリ LRU（256件）＋ディスク JSON（DATA_DIR/cache/tf_period・跨プロセス永続）** に保存。完了日（`day_start+86400<=now`）のみ保存、当日は都度計算。応答はキャッシュ日＋当日を組み立て。副次効果: 同一日を常に同じ日内 unit で量子化＝ローリングで窓ごとに unit が揺れる現行の不整合も解消。
  - テスト: jitter buffer +3・controller +3（per-day キャッシュ/当日非キャッシュ/ディスク永続）追加、全通過（front 227・backend tf_period 11）。
- **実UI検証（served B port 8137・Playwright・実HTTP・2026-07-11）**:
  - windowSec: 実リクエスト窓幅＝1D 3888000s(45d)／5m 28800s(8h)／15m 86400s(1d)。1D 初回は可視 3.6 年で **29 本**（旧 6h 換算なら約 5300 本）。
  - A: ローリング（ドラッグ 0.5〜1.8s）中の tf_period fetch は **全ケース 0 本**（旧: 0.5s パンで 23 本・再描画 5.7s 継続）。ドラッグ中は既取得列が滑らかにパン、fetch は停止後のみ。列描画・見た目は不変（欠落なし）。
  - B（per-day）: 1D 初回表示相当（可視 3.6 年＝29 窓タイル）を実測。**cold（ディスク空）41.7s → warm（mem/disk）5.1s＝8.2x**。ディスク永続ゆえ**再起動後も warm**（ユニットテストでディスク復元を確認）。直近窓（当日含む）は完了日キャッシュ＋当日のみ計算で **0.16s**。
  - 87MB の主因＝初回に全期間（1D 最大 3.6〜4.8 年）を映すこと。→ 下記「初回1年」で削減。
- **初回表示を直近1年に限定（依頼者指示・2026-07-11）**: 全期間フィットで tf-period が可視域ぶん一括取得され肥大するため、初回可視範囲を直近1年へ寄せる（古い範囲はスクロールで＝A案＋per-day で滑らか）。
  - 実装: ①composition_root_front.js の候補足ロード後 `renderer.focusTimeRange(last-1年, last)`（データ1年超のときのみ・全期間フィットを上書き）。②market_profile_actor.js の 日別初回オートズームも `from=max(oldest, to-1年)` に限定（`SESSIONS_INITIAL_SPAN_SEC`）。1年未満（intraday）は全期間で不変。
  - テスト: 日別初回ズーム「直近1年限定」を追加（front 228 に増）。
  - 実UI検証: 1D 初回の tf_period **29本/可視4.8年 → 10本/可視1.2年（1年＋prefetch）** ＝取得データ **約1/4（87MB→約22MB）**。ローリング・再ロールは A案＋per-day で瞬時のまま。
  - 一回性の cold（未ウォーム完了日）はディスク永続ゆえ2回目以降 warm。必要ならウォーマー（warm_dwell_cache 相当）で初回も事前生成可。
- **初回描画順のちらつき修正（依頼者指摘・2026-07-11）**: 「ローソク足 → 日別プロファイル(candle=sessionsタイル) → 日別プロファイル(tf-period列)」の順で中間に一瞬タイルが出る問題。原因＝`/market_profile?sessions=1` 応答（1本・速い）で `_drawSessions` タイルを描いた後、遅れて届く tf-period 列が上書きするため。
  - 対策: tf-period が日別を描くモード（player tf・`sessionsDrawnByTfPeriod` 述語）では、MarketProfileActor は**日別タイルを描かない**（`setSessions(null)`・読取欄 setSessionMP は維持）。候補足の透明化も **tf-period 側（列が実際に描けた時点）へ委譲**（TfPeriodProfileActor._render が `cols>0→透明化true`／無効化で false）。列が来るまで候補足は可視のまま＝空白も回避。
  - 実装: market_profile_actor.js（述語＋タイル抑制＋透明化委譲）・tf_period_profile_actor.js（renderer 注入＋透明化管理）・composition_root_front.js（述語/renderer 注入）。テスト +2（タイル抑制・透明化委譲）＝front 230。
  - 実UI検証: フレッシュ日別入場で日付ラベル(_drawSessions の MM-DD fillText)描画 **0 回**＝タイル非描画。読込中は候補足可視（`cols=0→transp=false`）→列到着で透明化（`cols=30/60→transp=true`）を実ログで確認。最終描画は従来どおり「透明候補足＋tf-period列」で不変。
- **検証環境**: served B方式（framework.server・port 8137）・datasetRef=jp225_tick・Playwright(実ブラウザ)・実HTTP。
- **関連**: tf_period_jitter_buffer.js（windowSec/cacheMax）・tf_period_profile_actor.js・composition_root_front.js:332-362（tfPeriodActor 配線）・ISSUE-054（barw 不反映＝同根 tf-period 優先）。

## ISSUE-056: MP検定パイプライン Step2c 符号検定統計量に機械的正バイアス（経験サイズ20%）
- **ステータス**: RESOLVED（2026-07-11 依頼者指示＝シミュレーション校正臨界値を実装・検証済み）
- **発生日**: 2026-07-11
- **概要**: 検定設計（依頼者確定）の Step2c 統計量 `T_d = sign(POC^raw−POC^τ)·sign(m_d−POC^τ)` は、両因子が POC^τ を共有するため、POC^τ の推定ノイズだけで T=+1 に系統的に偏る。季節性ゼロの合成 DGP（一様ボラ・ランダムウォーク 100日×40seed）で経験サイズ **20%**（名目5%）を実測（indigators/market_profile/analysis の Step2 サイズテストで検出）。
- **原因**: 共有ノイズ問題。b=POC^τ のノイズに対し両因子が同方向に依存（a−b と c−b が b に共通負依存）→ H0 下でも E[T]>0。方向参照を POC^raw に置換すると逆符号バイアス（検出力全喪失も実測）。
- **対策案（承認対象）**: 方向参照を中点 `(POC^raw+POC^τ)/2` へ変更：`T_d = sign(POC^raw−POC^τ)·sign(m_d−(POC^raw+POC^τ)/2)`。差 (a−b) と中点 (a+b)/2 のノイズは等分散近似で直交。実測：経験サイズ **7.5%**（3/40・二項ノイズ内）・検出力維持（釘付けDGP +89/−10, p<1e-15）。H0 の意味（生POCが低ボラ帯価格方向へ系統的にずれるか）は不変。
- **検証**: 承認後、サイズ/検出力テストを test_steps.py に固定化。
- **裁定（依頼者・2026-07-11）**: 中点参照案は**却下**（第1因子 sign(D) が代数的に不変のためバイアス機序は残存。実測でも 7.5% と残存）。指示＝統計量は原式のまま、**シミュレーション校正臨界値**（サロゲートごとに ŝ(b) 再推定・真の τ=identity 禁止）で判定し、その後 H1 検出力を必ず測定、不足なら統計量放棄。
- **解決結果（実測）**: 日内バー順列サロゲート（バー内部形状保存・時刻配置のみ破壊）× ŝ/低ボラ窓/POC^τ 全再推定で帰無分布を構成、p_mc=(1+#{T̄⁽ᵐ⁾≥T̄obs})/(M+1)（片側）。D=100・M=99・12seed 実測: **経験サイズ 0/12（p_mc 分布ほぼ一様）・検出力 12/12（現実振幅W字季節性 min ŝ≈0.45）・12/12（釘付けDGP）**。検出力不足は不発生 → 統計量放棄は不要。実装: mp_stats/step2_seasonality_poc.py（calibrated_sign_test）・data_prep.py（permute_bars_within_day）、テスト test_steps.py に size/power を固定化（11 passed）。素の p_sign は p_sign_naive として参考併記。

## ISSUE-057: MP検定パイプライン Step3 の HAR 移動平均が NaN で末尾全汚染（n=3810→148 に激減）
- **ステータス**: RESOLVED（2026-07-11）
- **発生日**: 2026-07-11（フルラン検証で発見）
- **概要**: step3_incremental_r2._rolling_mean が素の np.cumsum を使っており、y=ln RV の NaN（実データに RV=0 の日が5日存在）以降の移動平均が全て NaN 化。回帰標本が n=148（2012年序盤のみ）に縮退し、Step3 判定が実質無効だった。合成テストは y に NaN が無く未検出。
- **対策（即時実施・明示バグ）**: 有限値のみの cumsum＋有限数カウントで「窓内全有限」の行のみ平均を返す実装へ修正。NaN の影響は当該窓（最大 22 行）に局所化。
- **検証**: 回帰テスト2件追加（NaN 窓通過後の回復・まばらな RV=0 で n>2900 維持）。18 passed。フルラン再実行で n=3783 を確認（修正前 n=148 の判定 fail_to_reject は無効。修正後は全7変種 reject に反転）。

## ISSUE-058: MP検定パイプライン Step6-8 未実施（依頼者指示により保留・z(p)指標組込みを優先）
- **ステータス**: RESOLVED（2026-07-12 依頼者指示で再開・Step6フルラン＋Step7/8実装・フルラン完了）
- **解決結果**: Step6 reject（Part A: VA外寄り付き→翌日RV位置シフト γ=+0.085, Bonferroni2 p=0.0092。Part B 移動先検定: u_mean=0.523, Wilcoxon z=+4.49, 新規形成側の片側p≈3.5e-6＝**依頼者仮説（乖離した新しい価格で新POCが形成される）を支持**〔新POC*は過去受容水準から帰無より有意に遠い＝渡り歩きでなく新規形成。当初「過去水準への引き寄せ」と検定方向を誤設定していたのを依頼者指摘で訂正 2026-07-12〕・閾値2/4/8行で頑健）。Step7 fail_to_reject（SPA 216ルール・B=5000・p=0.23＝VA外寄り付き系ルールの予測改善は選択効果で説明可能）。Step8 skipped（打ち切り規則）。実装: mp_stats/step7_spa.py・step8_oos.py（62テスト緑）。レポート: analysis/out/mp_stats_report.{json,md}。
- **発生日**: 2026-07-11
- **概要**: 検定設計の Step6（VA外寄り付き→翌期RV条件付き分布＋POC*移動先検定〔依頼者仮説「不自然な価格から乖離すると次の不自然な価格で滞在する」の拡張・承認済み〕）、Step7（SPA 7次元データスヌーピング補正）、Step8（OOS・Kupiec/Christoffersen）が未実施。
- **現状**: Step6 は実装・単体テスト済み（mp_stats/step6_conditional.py、Part A/B、テスト4件緑、CLI 結線済み）だが**実データフルラン未実施**。Step7/8 は未実装（HansenSpa/VarBacktests は simulator に既存・再利用可）。Step1-5 は完了済み（out/mp_stats_report.json、POC*系列=out/step5_poc_star.npz）。
- **再開条件**: 依頼者指示。再開時は `python run_mp_tests.py`（Step6 込み・約1時間）→ Step7/8 実装。
- **関連**: ISSUE-056/057、mp_stats パッケージ（57テスト）。

## ISSUE-059: test_market_profile_byte_parity の 8 件が develop 時点で失敗（golden 陳腐化の疑い）
- **ステータス**: OPEN
- **発生日**: 2026-07-11（src=zp 追加作業中の回帰確認で発見）
- **概要**: `indigators/market_profile/api/tests/test_market_profile_byte_parity.py` のうち dwell/m1（to=1780666320）5 件・forming 3 件が、**作業変更を stash した素の develop でも同一に失敗**する（8 failed / 19 passed で一致確認済み）。src=zp 追加とは無関係の既存問題。
- **推定原因（未検証）**: golden 作成後の実データ（ticks parquet）更新または dwell キャッシュ／active table 依存のドリフト。
- **対応方針**: 本 Issue では修正しない（golden 再生成は挙動確定の判断を要するため依頼者承認待ち）。zp 作業の回帰判定は「この 8 件を既知ベースラインとし、それ以外の全テスト green」を基準とする。

## ISSUE-060: src=zp×日別×非対応tf（1m/5m）で日別タイルが消える（委譲述語の片側更新）
- **ステータス**: RESOLVED（2026-07-11）
- **発生日**: 2026-07-11（src=zp 実装の実UIブラウザ検証で発見）
- **概要**: sessions モードで MP actor はタイル描画を tf-period 列へ委譲する（sessionsDrawnByTfPeriod）が、src=zp の tf-period は 15m..1D 限定のため 1m/5m では tfpShouldOn が false。委譲述語だけが true のままだと「MP はタイルを描かず・tf-period も無効」で誰も日別を描かず、normal ヒストへ落ちて見えた。
- **原因**: zp の tf ゲート（zpTfOk）を tfpShouldOn にのみ追加し、委譲述語 sessionsDrawnByTfPeriod に追加しなかった（同一条件の二重管理）。
- **対策**: composition_root_front.js で ZP_TF_ALLOWED/mpSrc/zpTfOk を上段へ単一定義し、委譲述語と tfpShouldOn の**両方**が同じ zpTfOk() を参照するよう修正（単一情報源化）。
- **検証**: 実UI（Playwright・実HTTP・port 8138）で 5m×zp×日別 → MP actor の日別 z タイルが自前描画されることをスクリーンショットで確認（zp-05）。1D×zp×日別の tf-period z 列（zp-03）・normal live zp の POC* 黄線（zp-02）・src=dwell 回帰（zp-06）も確認。web テスト回帰 531/533（残 2 は既存 module-not-found・zp 無関係）。

## ISSUE-061: 過去の高 z(p) 受容水準への価格再訪時の反応計測（未実施・依頼者発行）
- **ステータス**: OPEN
- **発生日**: 2026-07-12
- **概要**: 過去に形成された高 z 水準（超過受容・POC*）へ価格が戻ってきたときの反応（反発・滞在・通過減速）を計測する。Step6 Part B で「乖離→新価格で POC 新規形成」は実証済み（p≈3.5e-6）。本 Issue はその対で「古い受容水準は再訪時に S/R として機能するか」を問う（実運用上の naked POC* 検定）。
- **設計骨子（実装前に依頼者確定が必要）**:
  - イベント: 過去 L 日内形成の高 z セル（z≥z_thr）への**形成後初回**接触（naked）。既訪問は別群。
  - 反応3指標: ①反発率（接触後 k 分以内に逆方向へ x 行以上）②水準±1セル滞在分数 ③水準帯通過所要時間。
  - 帰無: (a) 同日の低 z セル対照群 (b) Null B サロゲートの偽 POC* 水準への反応（季節性・レンジ位置の交絡吸収）。
  - 主検定パラメータ案（依頼者未確定）: z_thr=3・L=60日・k=30分・x=4行。
- **規律（Step7 の教訓・再発防止）**: 主検定は 1 本を事前登録し、感度は少数＋Bonferroni。パラメータを広く走査する場合は最初から SPA（HansenSpa 再利用）で束ねる。**「反応」の操作的定義は依頼者の言葉で確定してから実装する**（仮説文の解釈違い再発防止・2026-07-12 の Part B 訂正参照）。
- **利用資産**: znull キャッシュ（日別 z・4,157日）・mgrid キャッシュ（分単位経路）・null_b_day_peaks（偽水準生成）・mp_stats テスト基盤。見積り: 実装 1-2h・フルラン数分〜30分。
- **再開条件**: 依頼者による主検定パラメータの確定指示。
- **関連**: ISSUE-058（Step6-8 完了）・mp_stats/step6_conditional.py（移動先検定の対）。
- **追記（計測方法の平易な確定版・2026-07-12）**: 実験3ステップ＝①事件収集: 過去60日内形成の高z水準（z≥3）への**形成後初回**接触を15年分から全件抽出（2回目以降は別群）②反応3物差し: 跳ね返り（30分以内に逆方向4行）・滞在（水準近傍の滞在分数）・減速（帯の通過所要時間）③偽水準との比較＝計測の心臓部: 反応の絶対値でなく、偽水準A（同日の低zセル）・偽水準B（Null Bサロゲートが偶然作った偽こだわり水準）への同一物差しの反応との**差**のみを勘定する。本物への反応が両偽水準を統計的に上回って初めて「S/Rとして実在」。罠回避: 主検定1本（z≥3・L=60日・k=30分・x=4行）を事前固定、設定走査するなら最初からSPAで束ねる（Step7の教訓）。

## ISSUE-062: MP 指標の既定ソースを candle → zp（超過占有 z(p)）へ昇格（依頼者指示）
- **ステータス**: RESOLVED（2026-07-12）
- **概要**: 「全期間だと現在足の成長が視認不能」は zp が超過分のみ表示で解決したため当日絞りは不要、かつ主目的（意識された水準の可視化）で zp が従来 src に優越——との依頼者判断で、MP 新規追加時の既定 src を zp へ変更。
- **変更**: catalog_entry.js の src param 既定を 'candle'→'zp'。ENUM 選択肢（candle/dwell/m1/zp）は不変＝従来 src は選択で維持。**API 側の src 省略時既定は candle のまま**（backend 後方互換・既存 URL/テスト byte 不変）。
- **検証**: web テスト既定値アサーション更新（mp web 239 緑・UI web 531/533＝残2は既存 module-not-found）。実UI（8137・実HTTP）で MP 新規追加時に即 zp 表示（POC* 黄線・価格ラベル）を確認。
- **従来 src の残存役割**: ①フォールバック（非tick ref/キャッシュ未構築/履歴60日未満で zp 不可）②dwell の秒単位ライブ増分 ③記述統計（量）vs 推論（異常度）の対比。

## ISSUE-063: MP ソース選択肢から「足レンジ」(candle) を非表示（依頼者指示）
- **ステータス**: RESOLVED（2026-07-12）
- **概要**: candle は原子として最も粗く（足の[low,high]跨ぎ本数のみ・滞在時間も出来高も非反映）、ティック由来 src（dwell/m1/zp）に情報量で劣後。「ティックデータが存在しない場合に限り有効」との依頼者判断で、現状は選択肢から非表示にする（将来ティック受信不可データセットのフォールバックとして再有効化を検討）。
- **変更**: catalog_entry.js の src ENUM を ['candle','dwell','m1','zp'] → ['dwell','m1','zp']（enumLabels からも candle 撤去）。既定は zp のまま。**backend の _ALLOWED_SRC は candle を温存**（API 後方互換・将来フォールバック用・byte 不変）。
- **検証**: web テスト更新（catalog.test.js の enumValues/candleラベル）。mp web 239 緑・UI web 531/533（残2は既存 module-not-found）。実UI（8137・実HTTP）で配信 JS の enumValues=['dwell','m1','zp']・default='zp' を確認。

## ISSUE-064: MP ソース選択肢から「tick数」(m1) も非表示（依頼者指示）
- **ステータス**: RESOLVED（2026-07-12）
- **概要**: m1（src=m1・metric=count＝生ティック個数・セッション非認識）は「時間帯の配信密度のクセ」を差し引かない生カウントで、zp が帰無で除去する交絡そのもの。dwell（実滞在秒）の劣化版でもあり、意識水準を見る主目的では zp/dwell に劣後。ISSUE-063（candle 非表示）と同論理で選択肢から非表示。
- **変更**: catalog_entry.js の src ENUM を ['dwell','m1','zp'] → ['dwell','zp']（enumLabels からも m1 撤去）。残る選択肢は dwell（実滞在秒）と zp（超過占有）の 2 つ。既定 zp。**backend の _ALLOWED_SRC は candle/m1 とも温存**（API 後方互換・将来フォールバック/デバッグ用）。
- **検証**: catalog.test.js 更新（enumValues=['dwell','zp']・candle/m1 ラベル undefined）。mp web 239 緑・UI web 531/533（残2は既存 module-not-found）。実UI（8137・実HTTP）で配信 JS の enumValues=['dwell','zp']・default='zp' を確認。

## ISSUE-065: ライブ×dwell の初期描画で全期間プロファイルが一瞬映ってから当日へ置換（フラッシュ）
- **ステータス**: RESOLVED（2026-07-12）
- **発生日**: 2026-07-12（依頼者報告）
- **概要**: ライブモードで MP・src=滞在時間(dwell) を表示すると、最初に**全期間**プロファイルバーが描かれ、直後に**当日**プロファイルバーへ置き換わる（ちらつき）。
- **原因**: market_profile_actor.js setEnabled(true) が増分経路（growing×dwell×forming）でも無条件に refresh()＝全期間 dwell を 1 回描画し、その後の初回 onLiveTick→_enterTicklive が当日 forming（_sessionFrom＝当日始端 from）へ置換するため。呼び出し順は _applyMpParams(applyGrowthState growing=true)→setEnabled(true) 済＝setEnabled 時点で _isIncremental()=true。
- **対策（即時）**: setEnabled(true) で _isIncremental() が true のときは refresh() でなく onLiveTick()（accumulator 未生成→_enterTicklive＝当日 base+forming）で初期描画する。全期間フラッシュのフレームを消す。end-state（当日絞り）は不変＝参照実装・ユーザー確定挙動を維持。非増分（static/sessions/zp/非tick）は従来どおり refresh()。
- **検証**: web テスト2件追加（増分時 setEnabled が forming 経路・全期間 client fetch 0／非増分 static は従来 refresh）＝mp web 241 緑。実UI（8137・実HTTP・日足・ライブ）で eye ON 有効化時の発火が market_profile_forming（from=当日始端）のみ＝全期間 /market_profile?src=dwell（from なし）0 件をネットワークで確認。end-state（当日絞り）不変。
- **残課題（別症状・未修正）**: gear で src を dwell へ変更→OK した瞬間も同根で 1 フレーム全期間が映る（_onGearMarketProfile が growing 非考慮の refresh を呼ぶ）。有効化経路（本 Issue の報告症状）とは別。要否は依頼者判断。

## ISSUE-066: 日別プロファイル×ライブで gear のソース変更が反映されない（tf-period 列が再取得されない）
- **ステータス**: RESOLVED（2026-07-12）
- **発生日**: 2026-07-12（依頼者報告）
- **概要**: ライブ×日別プロファイル（sessions）モードで src を変更しても、画面の列表示が変わらない（スクロール/ズームすると初めて反映される）。
- **原因**: sessions モードの可視表示は tf-period 列アクター（TfPeriodProfileActor）が描く。その再取得（ensure→jitter buffer の src 差分でキャッシュ破棄→再fetch）は composition_root の subscribeVisibleTimeRangeChange（可視レンジ変化）でしか駆動されない。gear 適用（_onGearMarketProfile→_applyMpParams→marketProfile.refresh）は MP actor の集約プロファイルを再取得するだけで、tf-period 列には伝播しない。
- **対策（即時）**: MarketProfileActor.setParams に onParamsChanged フックを追加し、composition_root で tf-period の即時再適用（tfpShouldOn なら setEnabled(true)＝ensure で src 差分再fetch／不成立なら列を消す）へ配線。src/mode 変更が可視レンジ変化を待たず反映される。
- **検証**: web テスト2件追加（setParams が onParamsChanged 発火・未注入でも例外なし）＝mp web 243 緑・UI web 531/533（残2は既存 module-not-found）。実UI（8137・1h・ライブ・日別）で src dwell→zp 変更が**スクロールせず即** tf_period_profile?...&src=zp を再取得（#192-211）・zセル列描画をネットワーク＋スクショで確認。

## ISSUE-067: 日別プロファイル×dwell/zp でソース変更後の更新が数秒かかる（全期間 sessions フェッチが列描画を待たせる）
- **ステータス**: RESOLVED（2026-07-12・A案）
- **発生日**: 2026-07-12（依頼者報告）
- **概要**: 日別（sessions）モードで src を dwell 等に変更すると、ローソク足表示後に日別プロファイル列の更新まで数秒かかる。目標=1秒以内。
- **実測根本原因**: 日別モードで MP actor の refresh が全期間 `/market_profile?sessions=1&src=<src>` を叩く（dwell=1.4s・zp=3.3s／フェッチ窓に依存せず固定約1.0s）。しかも複数回。日別プロファイル列(tf-period)はこの重いフェッチ完了後の focus(自動ズーム)契機でしか取得されず、列表示が数秒遅延（実測 last_end 4.4s）。列自体は本来~200-500msで描ける（zp切替 実測208ms）。
- **対策（A案・承認済）**: 日別かつ tf-period が列を描くモード（sessionsDrawnByTfPeriod=true）では、MP actor は重い全期間 `/market_profile?sessions=1` フェッチを行わない（列は tf-period が供給・poc/va も各列が保持）。focus は candle 範囲から算出。onParamsChanged で tf-period を即時発火し、列を market_profile フェッチ完了を待たず<1sで描く。トレードオフ=クロスヘアの当日MP読取欄は tf-period 列由来へ簡素化 or 省略。
- **検証**: web テスト2件追加（tfDraws 時 refresh が /market_profile を叩かない・初回のみ candle focus／tfDraws=false は従来fetch）＝mp web 245 緑。実UI（8137・1h・日別）resource timing 実測: **全期間 /market_profile?sessions=1 フェッチ 0件**（旧2件・last_end 4.4s を消滅）。列(tf-period)は逐次描画で**最初の列 dwell 39ms・可視範囲 p50 575ms（<1s）**・zp 13-42ms。全プリフェッチ完了は dwell 2.78s（画面外先読み・37MB最小単位列＝背景継続で体感非ブロック）。残最適化余地=初回表示span 1年→短縮 or dwell列解像度粗化（依頼者の1ヶ月案）。

## ISSUE-068: 日別×dwell の tf-period count 列が最小価格単位で巨大化（37MB・メインスレッド5.4秒ブロック）
- **ステータス**: RESOLVED（2026-07-12・GRID_W 10pt 化）
- **発生日**: 2026-07-12（依頼者報告・ISSUE-067 の残ボトルネック）
- **概要**: 日別×dwell（src=null＝tf-period の count 列）は最小価格単位（≈0.0255）でビニングするため、1期間あたり数百レベル・可視1年分で37MB。ネットワークは39msで速いが、その後の JSON parse＋描画でメインスレッドが計5.4秒ブロック（最大単一1.4s）し「ローソク足の後に日別プロファイルが遅れて出る」。
- **実測**: longtask 10件・total 5410ms・max 1428ms・last_end 6041ms（switch to dwell sessions・1h・可視1年）。zp は GRID_W=10pt 粗sparseで779KB→13-42ms。
- **対策（承認済）**: count 列（src=null）のビニング解像度を最小価格単位→GRID_W(=10pt) へ粗くする（zp と同グリッド）。37MB→~1-2MB・ブロック<0.5s。列幅~10pxでは最小単位は視認不能ゆえ表示損失なし。POC/VA は10pt グリッド上で最大±5pt 誤差（日経225 日別用途で無視可）。
- **検証**: api テスト更新（count 列 unit=10.0・GRID_W subdir・整数カウント／golden を GRID_W ビニングへ）＝MP api 222 passed（8失敗は既存 ISSUE-059・無関係）。1チャンク実測 1.1MB→**14KB（78倍削減）**。実UI（8137・1h・日別）longtask 実測: メインスレッドブロック **5410ms→132ms**（max 1428→68ms・tf 37MB→1.0MB・market_profile 0）＝日別プロファイル更新が体感即時。列見た目は従来と遜色なし（列幅~10pxで最小単位は視認不能）。

## ISSUE-069: 日別プロファイル列の逐次描画を「揃ってから一括表示」へ（上限タイムアウト付き）
- **ステータス**: RESOLVED（2026-07-12）
- **発生日**: 2026-07-12
- **概要**: 日別プロファイル列（tf-period）はチャンク到着ごとに逐次描画されるため、列がパラパラと段階的に現れる。これを「可視範囲の列が揃ってから一括表示」に変更し、完成形で一度に出す（ちらつき解消）。
- **対策**: TfPeriodProfileActor の描画を、可視範囲を満たすチャンクが全て ready になるまで保留し、揃った時点で1回だけ描く。安全弁として**上限タイムアウト**（例 800ms）を設け、時間内に揃わない場合はその時点の ready 分で強制描画（永久保留の防止）。ローソク足の表示は不変（本変更の対象外）。
- **実装**: TfPeriodJitterBuffer.allReady(from,to)（可視範囲の全チャンク ready 判定）＋ TfPeriodProfileActor に保留/上限タイムアウト（既定800ms・注入可）。refresh は allReady なら一括描画・未なら保留＋timer。onChunkReady は保留中に揃ったら一括描画（逐次描画しない）。揃うまで前回描画を保持（clear しない＝ちらつき回避）。
- **検証**: web テスト更新+新規（一括描画・onChunkReady で揃い次第 commit・タイムアウトで部分フォールバック・保留無しは再描画しない・透明化は描画時のみ）＝tf_period_actor 7 passed・MP web 247 緑・UI web 531/533（残2は既存無関係）。実UI（8137・1h・日別）src dwell⇄zp 切替でエラー0・列は完成形で一括描画を確認。ローソク足表示は不変。

## ISSUE-070: 日別×tf-period描画時は解像度パラメータ（resmode/bins/range）が無効なのにグレーアウトされない
- **ステータス**: RESOLVED（2026-07-12）
- **発生日**: 2026-07-12
- **概要**: 日別プロファイル×対応tf（列を tf-period が描くケース）では列は GRID_W(10pt)固定で、解像度パラメータ（resmode/bins/range）は URL にも送られず完全に無効。しかし gear で編集可能なため「動かしても変わらない」誤解を生む。tf-period が列を描くとき**のみ**これらをグレーアウトしたい。
- **注意**: 解像度は「日別×非対応tf（1W/1M＝タイル描画）」「通常モード」では有効。よって一律無効化は誤り。判定は timeframe 依存（既存 conditionalVisible は param 値条件のみ）。
- **対策**: gear ダイアログの param 無効化を、mode=sessions かつ対応tf（tf-period描画）のとき resmode/bins/range をグレーアウト（disabled）する。判定は controller の現 timeframe と mode を参照。
- **実装**: form_model.computeEnabled に (values, context) 第3引数＋**関数述語**対応を追加（従来 {when:{param,equals}} と両対応）。properties_dialog に context を通し _refreshEnabled で渡す。indicator_controller の gear 起動で context={timeframe,servedMode} を注入。catalog_entry に述語 _mpResolutionEnabled（served×日別×対応tf、src=zp は15m..1D）を resmode/bins/range の conditionalEnable へ付与。param factory の pickUi が関数を透過。
- **検証**: web テスト（catalog: bins 述語の真理値表／form_model: 関数述語評価）＋UI web 532/534（残2は既存無関係）・MP web 247 緑。実UI（8137・実HTTP）で 日別×1h は resmode/bins/range が is-disabled＋ctrl disabled、ダイアログ内 mode トグルで 通常→有効・日別→グレーアウトへ動的追従を確認。

## ISSUE-071: ライブ×通常×1分足で MP 指標が更新されない（依頼者報告・実測＝機構は全稼働・zp表示が視認上凍結）
- **ステータス**: RESOLVED（2026-07-13・(b)期間パラメータ新設〔依頼者裁定〕）
- **解決結果**: gear に「期間：全期間／当日」（ENUM・既定 all＝従来挙動不変）を新設。period=day×src=zp×通常で refresh が from=当日始端 を付与し当日単独 z(p) を表示（backend の既存 from 機構を再利用・backend 変更なし）。当日窓は毎分視認可能に成長（実測: 70秒で41/245ビン変化・現在価格帯 z+0.17≒バー長4.6%/分＝全期間窓の約90倍）・POC*/VA も当日水準。zp のみ表示（dwell は ISSUE-065 の forming 当日絞り済みで対象外）・通常×1m..1D のみ有効（リプレイ/日別/1W/1M はグレーアウト・ISSUE-070 機構re利用）。帰無は窓と独立（各日直前250完了日）のため統計品質不変。実装: catalog_entry.js（period param＋_mpPeriodEnabled）・market_profile_actor.js（setParams 受理＋_periodExtra）・indicator_controller.js（_mpParams 透過・未保存キーは載せない後方互換）。検証: MP web 251 緑・UI web 533/535（残2は既存 module-not-found）・実UI（8000・1m×ライブ×通常×zp）で 期間=当日 選択→&from=1783900800 送信・当日プロファイル描画・モード切替での期間グレーアウト連動をネットワーク＋スクショで確認。あわせて同日実装のレンジ10pt選択肢（真の10pt=245ビン）と併用可。
- **発生日**: 2026-07-13（依頼者報告）
- **概要**: ライブモード・1分足・表示モード「通常」で MP 指標が更新されない。
- **実測結果（実UI・Playwright・実HTTP・port8000/8139）**: 更新機構は全経路稼働を確認。①src=dwell: /market_profile_forming 5秒毎 200・primitive.setProfile フック実測で tpoSum が約+1/秒で成長（新規追加・restore・1D→1m 切替後も同様）②src=zp（既定）: /market_profile 5秒毎 200・応答は毎分変化。ただし**変化量が視認不能**＝14分間で最大バー長変化 4.2%（大半のビン <1%）・POC*(69675)/z_max(36.76)/VA は不変。fetch/描画は動いており「凍結」に見えるのは zp 通常表示の構造による。
- **機構（実測裏付け）**: 通常×zp は窓Σz 合成（1m は dataset tail 50k 行≈35日窓）。当日の occupancy は +1obs/分だが、√Σvar（35日分）で希釈され z は +0.01〜0.05/分。表示 norm は窓 z_max=36.76（歴史的外れ値）で正規化されるため当日の成長は 0.05%/分程度＝視認不能。日別モードは当日タイルが当日単独 z のため視認できるのと対照的。当日分が窓に含まれること自体は実測確認済（partial rollup obs が hi 前進で 535→550→565 と成長・活性ビン z 8.49→8.51→8.54）。
- **論点**: ISSUE-062 裁定「zp は超過分のみ表示ゆえ当日絞り不要（全期間で現在足成長が視認可能）」の前提が、Σz 合成の希釈と z_max 正規化により 通常×ライブでは成立していない。
- **対策案（承認対象・UI/UX 変更のため裁定待ち）**:
  - A案: 成長時（FOLLOW×通常×zp）のみ refresh に from=当日始端 を載せ「当日 z(p)」を表示（dwell の ISSUE-065 当日絞りと同型・日別の当日タイルと整合。ANALYSIS=static は従来全期間のまま）。
  - B案: 表示のみ変更＝可視レンジ／当日 z_max で norm 正規化し直す（窓は全期間のまま・微変化を増幅表示）。
  - C案: 既存 today オーバレイ再利用＝refresh に today=1 を載せ、当日ぶんの z を強調描画（backend want_today 実装済・増分2 の today 描画機構を転用）。
  - ※依頼者の観測症状が上記（zp 視認凍結）と異なる場合は具体症状（src・観測した表示要素）の追加情報が必要。
- **検証**: 未実施（裁定後に対策実装＋実UI で当日成長の視認確認を行う）。

## ISSUE-072: 1分足×日別プロファイル（アクタータイル経路）の時間軸ずれ・タイル消失（依頼者報告）
- **ステータス**: RESOLVED（2026-07-13）
- **発生日**: 2026-07-13（依頼者報告「1分足でMP指標の日別プロファイルの時間軸にバグ」）
- **概要**: 1分足×日別プロファイル（src=zp は 1m/5m で tf-period 非対応＝MP actor が日別タイルを自前描画する経路）で、タイルがほぼ全て消え、残った当日タイルも幅18px固定の細片が誤位置に描かれる（ローソクは透明化済みのためチャートがほぼ空白に見える）。
- **実測根本原因（実UI・timeScale API 直接計測）**: タイルの x アンカーが timeToCoordinate(深夜00:00) だが、日中足では 00:00 バーが存在しない日がある（セッション開始 01:00・週末ギャップ・1m は /candles 1500本≈25h 窓）。実測: 07-12 00:00→null・07-13 00:00→364.25＝1日だけ解決。null は全カリング→タイル消失、可視1日では隣接間隔が取れず列幅が既定18pxへ退化。さらに解決できる日も深夜（日境界）中心＝実在バー範囲から半日ずれる構造。OHLC 付与も深夜バー1本の突合のため日中足では欠落/誤値だった。
- **対策**: ①actor._buildSessionView: candles を UTC 日で集計し、日次 OHLC（open=初バー/close=終バー/high=max/low=min）と当日実在バー範囲 tFirst/tLast を各セッションへ付与（1D は日=バー1:1で従来値と同値）。②primitive._drawSessions: tFirst/tLast があるとき timeToIndex(exact)→logicalToCoordinate で日スパン [xL,xR] を求め、タイル中心=スパン中央・列幅=スパン*0.85 で実在バー範囲へ整列（視野外 index も座標が出るため部分可視日も描ける）。単一バー日（1D）・tFirst 未付与（旧呼び出し）・timeToIndex 非提供は従来経路＝byte 不変（後方互換）。tf-period 列（時間足毎profile列＝設計どおりの周期列）は非対象・不変。
- **検証**: MP web 255 緑（新規4件: 日スパン整列・旧経路互換・1D不変・日集計OHLC/tFirst付与）・UI web 533/535（残2は既存 module-not-found）。実UI（8000・実HTTP）: 1m×zp×日別で 07-10/07-12/07-13 タイルが各日の実在バー範囲に整列して描画（修正前=当日1本の細片のみ）・5m×zp×日別も 07-07〜07-13 の全日タイル整列をスクショ確認。コンソールエラー0。

## ISSUE-073: tf-period 列の時間足別ビニング解像度（1分足=1pt・依頼者承認）
- **ステータス**: RESOLVED（2026-07-13）
- **概要**: 「時間足によって適宜解像度を変更できる仕様。1分足は細かく分析したいので1pt」（依頼者承認 2026-07-13）。tf-period 列（日別プロファイルモードの周期プロファイル列・count/dwell）のビニング解像度を時間足別に設定可能にし、1m を GRID_W(10pt)→1pt へ細分化。
- **実装**: tf_period_profile_controller に _UNIT_BY_TF（{'1m': 1.0}・未指定 tf は GRID_W 維持）を追加し _day_columns の unit を tf から解決。compute 層 tf_period_profiles は元々 unit パラメータ化済み（ISSUE-068 以前は最小呼値 0.0255 で稼働）＝controller のみの変更。ディスクキャッシュは既存 g{unit:g} サブディレクトリで解像度別に自動分離。zp 列（15m..1D・GRID_W）・フロントは変更なし（unit は応答で伝搬・primitive はレベル高を unit から算出）。
- **応答量**: 1m×1pt は1周期数十レベル（実測 sample 22 レベル・6hチャンク360列）＝肥大しない（ISSUE-068 の 37MB は 1h×可視1年の話・その対策は 5m..1D の 10pt 維持で不変）。
- **検証**: api テスト更新+新規（1m unit=1.0・レベル1pt分離／15m は 10.0 維持）＝tf_period 8 passed・MP api 223 passed（8失敗は既存 ISSUE-059 ベースライン）。実UI（8139・実HTTP）: /tf_period_profile?timeframe=1m が unit=1.0 を返し、1m×dwell×日別で分単位 1pt 列の描画・コンソールエラー0 を確認。
- **追記（2026-07-13・依頼者指示で 1m=0.5pt へ変更）**: 1pt の実UI確認後、依頼者が「1pt以下」を要望し 0.5 を指定。_UNIT_BY_TF を {'1m': 0.5} へ更新（下限はデータ最小刻み≈0.0255＝mid 量子化で、それ未満は情報が増えない旨を提示済み）。テスト更新（unit==0.5・0.5pt 格子の量子化）＝tf_period 8 passed。実HTTP（8139 再起動後）: unit=0.5・levels が 0.5pt 刻み（67495.0/67495.5）で返ることを確認。8000（依頼者稼働中サーバ）への反映は再起動が必要。
- **追記（2026-07-13・依頼者指示で 1m=0.0255（最小価格刻み）へ最終変更）**: 0.5pt では「プロファイルバーが重なって視認性が悪い」との依頼者指摘により、データの真の解像度＝最小刻み 0.0255 を指定。_UNIT_BY_TF を {'1m': 0.0255} へ更新。テスト更新（unit==0.0255・0.0255 格子量子化＝price 4桁丸め）＝tf_period 8 passed。実HTTP（8139）: unit=0.0255・levels が実 tick 価格（67587.0105 等）で返り、6h チャンク実測 887KB/0.13s（ISSUE-068 の 1m 実測 1.1MB と整合・実用範囲）。

## ISSUE-074: tf-period 列に方向背景（全時間足・不透明度0.95・陽陰の判別）を追加（依頼者指示）
- **ステータス**: RESOLVED（2026-07-13）
- **概要**: 「どの時間足にも背景色(不透明度95%)を追加して上下が分かる仕様に変更」（依頼者指示 2026-07-13・0.0255 化後の視認性改善）。日別プロファイルモードの tf-period 列（全対応 tf・count/zp 共通の描画経路）に、各周期の陽/陰を示す背景を追加。
- **実装**: ①TfPeriodProfileActor に getCandles 注入＋_annotateDirections（列 time と candle time は同一周期グリッド＝Map 突合で dirUp: 陽=true/陰=false/candle 不在=null を列へ注釈）②primitive._drawTfPeriods が dirUp 注釈列の占有レンジ（levels 先頭〜末尾±lvlH/2）を C_TFP_BG_UP='rgba(18,60,51,0.95)'（暗緑）/ C_TFP_BG_DOWN='rgba(70,25,25,0.95)'（暗赤）で塗る（指示どおり不透明度0.95。明色そのままではヒートバーが読めなくなるためダークテーマ整合の暗色系を採用）③composition_root で getCandles: renderer.getCandles を配線。dirUp 未注釈（旧呼び出し・candle 未ロード）は背景なし＝後方互換。
- **検証**: web テスト2件追加（actor: dirUp 注釈 陽/陰/不在null・未注入は注釈なし／primitive: 陽=暗緑・陰=暗赤の背景が占有レンジを覆う・未注釈は背景なし）＝MP web 257 緑・UI web 533/535（残2は既存 module-not-found）。実UI（8139・実HTTP・1m×dwell×日別）: 880列中 陽459/陰421 を注釈、各分の背景で上下が即読できることをスクショ確認。
- **追記（2026-07-13・依頼者指示で不透明度 0.95→0.1 へ調整）**: α0.1 では暗色系がダーク背景に埋没するため、色相を sessions ティントと同系の明色（薄緑 rgba(38,166,154,0.1)／薄赤 rgba(239,83,80,0.1)）へ変更。テスト更新＝MP web 257 緑。実UI（8139）で薄い方向ティントの描画を確認。※この際「0.1は?」を刻み(unit)変更と誤解して一時 _UNIT_BY_TF を 0.1 にしたが、依頼者訂正を受け 0.0255 へ即時復元（テスト・実HTTP unit=0.0255 で確認済み）。

## ISSUE-075: プロファイルバーのオンマウス読取ツールチップ（a案・依頼者指示）
- **ステータス**: RESOLVED（2026-07-13）
- **概要**: 「プロファイルバーにオンマウスしたときにその価格帯のデータを表示」（依頼者指示 2026-07-13・表示場所はカーソル追従ツールチップ＝a案を依頼者選択）。tf-period 列のホバーで、該当周期×該当レベルの読取をカーソル近傍に表示する。
- **実装**: ①primitive.tfPeriodLevelAt(time, price): 該当列の**最近傍占有レベル**を返す純ロジック（0.0255 格子は行がサブピクセルで正確な行に合わせられないため、縦3px相当の価格幅で最近傍へスナップ。px→価格換算は priceToCoordinate の unit 距離から実測）②ChartRenderer._onCrosshairMove に座標DTO { x, y, time, price } の発火フック（setTfPeriodHoverHandler・lwc 型は渡さない）③新規 tf_period_tooltip.js（TfPeriodTooltip・DOM のみ・端でフリップ・formatTooltipLines/formatPeriodLabel 純関数）④composition_root 配線（列有効時のみ・ヒット無し/チャート外は hide）。表示内容: 周期時刻・レベル価格・滞在tick数（列内シェア%）・列POC/VA・周期計tick（zp 列は z 値表記）。symlink＋build.mjs MODULE_ORDER 追加。
- **検証**: web テスト追加（tooltip 5件: 整形 count/zp・HH:MM/MM-DD・show/hide・端フリップ・DOM不在no-op／primitive: 最近傍スナップ・許容外/列不在/非表示 null）＝MP web 263 緑・UI web 533/535（残2は既存 module-not-found）。実UI（8139・実HTTP・実マウス Playwright）: 1m×日別×dwell のバー上ホバーで「04:21／価格 67110.084／滞在 1 tick（0.5%）／POC 67069.9725／VA 67057.0185〜67122.018／周期計 194 tick」の表示をスクショ確認。

## ISSUE-076: 日別×1m/5m で zp が選べるのに周期列が出ず混乱する（B案＝tooltip明記で解消・依頼者選択）
- **ステータス**: RESOLVED（2026-07-13・B案）
- **概要**: 「1分足・5分足は zp 列自体が非対応なのにソースが選択できるので混乱する」（依頼者指摘 2026-07-13）。実挙動は「非対応で無表示」ではなく、日別×1m/5m×zp は日単位 z タイルへのフォールバック表示（ISSUE-060 裁定・z は短周期で統計不成立）。混乱の実体はソース切替で表示粒度が黙って変わること。
- **裁定**: A案（選択肢グレーアウト＝option 単位 disable 機構新設・1分足で日別 z タイルを見る機能を潰す）と B案（tooltip で挙動差を明記・選択は許容）を提示し、依頼者が B案を選択。
- **実装**: catalog_entry.js の src param に tooltip を追加（「滞在時間＝…全時間足で周期列／超過占有z(p)＝…15分足以上が周期列・1分足/5分足は日単位タイル表示」）。properties_dialog の既存 tooltip 機構（ⓘ・title）で表示＝ダイアログ側変更なし。
- **検証**: catalog.test.js に tooltip 明記のアサーション追加＝UI web 533/535（残2は既存 module-not-found）・MP web 263 緑。実UI（8139）で gear ダイアログのソース行 ⓘ title に全文が入ることを確認。

## ISSUE-077: zp が薄い日（日曜22時UTC開始の週明け2時間等）をフル営業日と同重みで計上（幻影滞在・実測）
- **ステータス**: RESOLVED（2026-07-14・ISSUE-078 セッション日再定義により構造解消・実測確認）
- **解決結果（実測）**: 薄い日曜原子そのものが消滅（週明け2hは月曜セッションへ統合）。新セッション窓（ブローカー分 [60,1394]）は日次休場帯（23:15-01:00）を構造的に窓外へ排除し、通常営業日の ffill 幻影は実測 6分/1335分（0.4%・旧日曜日の幻影 1,264分/1,378分から解消）。対策案 (a) 実ティック非存在分の NaN 化は残余 0.4% と祝日薄日に対して費用対効果が低く**実装せず**（将来、祝日短縮日の帰無混入が問題化すれば (b) 薄い日除外を再検討）。
- **発生日**: 2026-07-13（依頼者の「日曜データも計算原子か?」の追及で実測発見）
- **実測**: 2026-07-12（日曜・実ティックは 22:03〜23:59 UTC の 2,426件＝114分のみ）の zp 日ロールアップが obs合計=1378分＝金曜（通常日）と完全同数。_mgrid_of_day の minute_close_grid がセッション窓 1378分を全て有限値で埋めており（実ティック無しの約21時間が補完値＝幻影滞在）、観測にも帰無ソースにも「フル日」として混入する。
- **影響**: ①幻影滞在は補完元の一定価格帯に集中→偽の高 z スパイクを生成し得る ②帰無（直近250完了日）に薄い日曜/祝日が混入し z 全体を歪める ③zp 通常プロファイル・日別タイル・tf-period zp 列すべてに波及。足ベース指標・dwell（実ティック滞在）はデータ量比例で影響なし（実測: 日曜は 114 本/114分のみ寄与）。
- **対策案（承認対象・zp 統計パイプライン変更）**:
  - (a) 観測の補正: minute_close_grid の実ティック非存在分を除外（NaN 化し obs/col 範囲を実在分に限定）
  - (b) 帰無ソースの補正: 実分数が閾値未満の薄い日を帰無履歴（NULL_HIST_DAYS）から除外
  - (c) (a)+(b) 併用（推奨・観測と帰無の両輪を実データ限定に揃える）
  - 検定パイプライン（mp_stats・Step1-8 の実測結果）への影響評価も要（同じ mgrid を使用している場合は再ラン要否を判定）
- **検証**: 未実施（裁定後に実装＋日曜除外前後の z 比較・既存 golden/検定結果への影響確認）。

## ISSUE-078: セッション日境界の再定義（UTC暦日→ブローカー時間 NY17:00 ET 基準 00:00 区切り・依頼者承認）
- **ステータス**: RESOLVED（2026-07-14・feature/session-day-boundary・全6フェーズ完了）
- **概要**: UTC暦日切りが薄い日曜原子（週明け2時間）を生み、足指標の同格計上と zp 幻影滞在（ISSUE-077）の温床になっていた。依頼者裁定によりセッション日を「ブローカー時間（冬UTC+2/夏UTC+3・米DST日程切替）の00:00＝NYクローズ17:00 ET」区切りへ再定義する。実測検証済み: 週明けオープン（夏22:03/冬23:00 UTC）はブローカー時間で月曜01:00台＝日曜原子消滅、境界は夏冬とも休場帯内＝取引時間を分断しない。
- **設計**: marketdata 層に session_day 関数群を単一定義（IANA tz 'America/New_York'＋7時間シフト＝NY17:00 が 00:00 になる座標系。DST は zoneinfo が自動処理＝自前カレンダー不要）。既存 floor(t/86400) の日切り（tf_period 36箇所・dwell 21・zp 14・frontend growth_window/actor 等）を session_day 経由へ置換。日足/週足/月足ロールアップはブローカー日で再生成。キャッシュ（実測21,705ファイル・248MB）は全再構築。
- **関連**: ISSUE-077（zp幻影滞在＝主因はこれで消滅・薄い祝日補正は残課題）・ISSUE-079（zp bp相対格子）。
- **進捗（2026-07-14）**: 単位①〜④実装完了（feature/session-day-boundary・コミット9本・バックアップ backup/20260714-pre-session-day）。
  ①marketdata/session_day.py（12テスト）②stores 署名2UTC日化・dwell/zp/tf-period 置換（zp窓=ブローカー分 [60,1394]・G=1335・K=45・キャッシュ版数bump・step5パリティは規則等値へ再定義）③resample_ohlc_tf/session（1D/1W/1M ブローカー日集計・ラベル=UTC深夜規約）・rollup 被覆判定 period_utc_start・forming 1D・tf-period 1D列 time 整合 ④frontend session_day.js（Intl NY tz・7テスト）・actor/growth_window/live_tick_player 置換（1D ライブバーの 21-24時UTC フリーズ修正含む）。
  単位⑤: rollups 全再構築完了（jp225_tick 33.8s・jp225_m1。旧は rollups_backup_utc20260714/ へ退避）。実測: 1D 日曜バー 504→26 本（残存は歴史的特殊日）・7/12(日)夜データは 7/13(月) バーへ統合・1500本→3673本（旧4160）。dwell キャッシュ warm 完了（セッションキー4,924日・25s+テスト時副次ウォーム）。zp warm 実行中。
  検証済（部分）: 実UI 8139 で 1D/1W ローソク（日曜バー無し）・1m×dwell×日別の列描画・forming from=セッション始端 URL を確認。テスト: marketdata 135・MP api 216（byte-parity 既知10除く＝dwell golden 2件は設計上変化・再生成は依頼者裁定待ち）・UI api 380・MP web 270・UI web 534 全緑。
  単位⑤完了: zp warm 4,884セッション/505s・dwell 25s・tf-period プリウォーム背景実行（コールド時の一括要求洪水＝既存ジッターバッファ特性の緩和）。
- **Phase 6 検証結果（実測）**: ①1D ロールアップ: 日曜バー 504→26本（残存51本の土日ラベルは全て2012-2014＝初期データ期の配信仕様差・ロジック起因でない）・7/12(日)夜データは 7/13(月) バーの高値 69084.76 として統合 ②ライブ 1D バー: 水曜セッション（7/14 21:00 UTC 開始）を 7/15 ラベルで形成中を実UI確認（22:00 再開直後の値 68,160 表示・LiveTickPlayer 1D フリーズ修正込み）③zp: 月曜セッション obs=1335/1335・実ティック分数 1,329＝**幻影補完 6分/1335分（0.4%）**（旧 UTC 日切りの日曜: 実114分に対し1,378分計上＝幻影1,264分）。日次休場帯（ブローカー23:15-01:00）はセッション窓 [60,1394] の構造外＝幻影の主因を構造排除 ④15m zp 列: 月曜セッション89列が日曜22:00〜月曜20:00 UTC を被覆（週明け2時間が統合）・コールド計算354ms/日 ⑤1D×日別×zp 全期間列・通常×zp POC*/VAH 描画を実UI確認 ⑥全テスト: marketdata 135・MP api 216・UI api 380・MP web 270・UI web 534 緑（byte-parity は既知8＋dwell golden 2=設計上変化・再生成は裁定待ち）。
- **残課題（裁定待ち）**: (1) dwell byte-parity golden 2件の再生成 (2) 旧世代キャッシュの清掃（dwell v2 UTC キー約5,100 npz・zp v1・tf-period 旧 subdir・rollups_backup_utc20260714 186MB＝削除は承認事項） (3) develop へのマージ (4) 本番 8000 は serve.sh 再起動で新コード反映（旧 watch は停止済み）。

## ISSUE-079: zp 格子の bp 相対化＋無次元校正スキャン（絶対10pt→価格比・依頼者承認）
- **ステータス**: RESOLVED（2026-07-15・詳細は下記）
- **単位①実測結果（feature/zp-bp-grid・analysis/out/zp_grid_scan.md）**: bp 相対格子＝log 価格の一様格子（k=floor(ln p/w)・跨日Σ集計は現行と同型に成立）。実データ 2 期（直近/2013 各60セッション）×9 幅で帰無サロゲート FPR(z≥3) を校正測定。
  - **重要発見**: 単日 z の計数分布は正規裾を持たず現行 10pt 自身の FPR≈2.2%。事前登録判定（現行比 25% 以内の相対劣化）では **0.5bp までの全幅が合格**＝FPR は細分化で膨張しない（離散化はむしろ z≥3 到達を稀にする）。つまり「統計が成立する最細」は FPR 基準では試験範囲内に下限が見つからず、**細分化の実コストは偽陽性でなく検出力・表示ノイズ**（セル分割で単日 z の振幅が縮む）。
  - 分/セル中央値: 現行10pt=直近8分・2013 17.5分（絶対格子の時代ドリフトの直接証拠）。bp 固定なら残余変動はボラ局面のみ（監視指標: 分/セル移動中央値）。
- **裁定結果（2026-07-15）**: 15m 周期のカバーを要件に **1bp** を採用（15分周期＝原子15分で約1.5分/セル＝意味の立つ最細。0.5bp は1分/セル未満で情報が増えない）。
- **単位②実装（2026-07-15）**: (a) zp 内部格子を 1bp log 一様へ（ZP_BP/W_LOG・観測/帰無/日別/窓合算/POC*/tf-period zp 列・znull v3=b1 タグ・mgrid は格子非依存で温存。step5 パリティは独立実装 zp_grid_scan との完全一致へ再錨定・線形 step5 は検定参照実装として温存）。(b) 表示は「表示幅(bp)」パラメータへ一本化（dispbp・FLOAT 自由入力・既定3bp・最小1bp）＝ビン/レンジ/解像度トグルを撤去（依頼者承認「二層構造: 計算は1bp固定・見せ方は自由」）。actor が最新終値×bp/1e4→barw(pt) へ写像し既存 &barw= 経路を再利用（backend 変更なし・時代整合は要求時価格で自動）。legacy 保存インスタンスの resmode/range は優先受理（後方互換）。dwell の内部格子は絶対10ptのまま（表示下限が時代依存＝既知の残課題・別ISSUE候補）。
- **検証**: MP api 221・MP web 273・UI web 530/532 緑。実UI（8139）: gear に表示幅(bp)のみ（旧解像度UI消滅）・dispbp=3 で実URL barw=20.4222（=3bp×終値68,074）・zp b1 znull ウォーム完了（4,884セッション/428s）後にセッション窓 zp（表示7bp・340ビン・poc_star 66942.84）を実HTTP確認。回帰修正: 空 candles 経路の log(0) クラッシュ（旧線形格子で潜伏）を price_min クランプで防御＋回帰テスト。tf-period zp（s2）プリウォーム背景実行。
- **ステータス**: RESOLVED（2026-07-15・二層構造の完成＝計算1bp固定・表示幅(bp)自由。残課題: dwell 内部格子の bp 化は別ISSUE候補として未着手）
- **概要**: 絶対pt格子は価格水準に追従せず（本データ内で日経約8倍）、最適幅が将来ドリフトする＝現在データへの過剰適合になるとの依頼者指摘。①格子を価格比（bp）相対へ再設計②「統計が成立する最小セルあたり分数」（時間不変の無次元定数）を実測スキャン（帰無サロゲート偽陽性率の校正チェック・実データ60日×複数幅）で確定し、bp幅を導出する。
- **運用**: セルあたり分数の移動中央値を監視指標にし、閾値割れで再校正（ボラ局面変化への機械的見直し）。

## ISSUE-080: 日別プロファイル×1m/5m の zp を選択不可に（代替粒度の廃止・「列の周期＝チャートの時間足」原則）
- **ステータス**: RESOLVED（2026-07-15）
- **経緯**: 日別×1m/5m×zp は日単位タイルへフォールバックしており（ISSUE-060/076）、「zp=日・dwell=分」と表示粒度がソースで分裂して複雑（依頼者指摘）。統一案として「zp列の最小周期15分」を提示したが、依頼者の指摘（「1分足を選んだユーザーはどの粒度を見たいのか」「1年後に理解できるか」）により棄却＝**チャートの時間足がユーザーの粒度宣言であり、作れないソースは代替を出さず選べなくする**のが正道と裁定。加えて実測で「1m日別のzpタイル＝日足チャートの当日zp列とほぼ同一情報（同一原子・同一帰無・MC誤差のみ）」を確認し、1m/5mにzpを残す価値がないことを確定。
- **原則（1行）**: 「列の周期＝チャートの時間足。作れないソースは出さない。」
- **実装**: ①catalog: src param に optionEnable 述語（ENUM option 単位の無効化機構を新設・param factory/form_model/properties_dialog._refreshEnabled へ透過）＝日別×1m/5m で zp option を灰色化（mode/timeframe に動的追従）。tooltip を「選択不可」文言へ更新 ②actor.refresh: 実行時ガード（時間足切替で事後に zp×日別×1m/5m へ到達した場合、fetch も描画もせず表示クリア・ローソク可視維持）＝重い全期間フォールバック fetch も消滅 ③ブロック集合 MP_ZP_SESSIONS_BLOCKED_TFS を catalog_entry で単一定義（actor と共有）。
- **検証**: MP web 274・UI web 530/532 緑（TDD Red 2件→Green）。実UI（8139）: 日別×1分で zp option が disabled・通常へ戻すと再有効化（動的）／日足で zp×日別適用→1分へ切替で zp/sessions フェッチ 0 件・タイル非表示・ローソク可視をネットワーク＋スクショ確認。
- **追補（依頼者指摘「デフォルト src=zp のまま選択されている」）**: 選択中 option が無効化された場合に灰色のまま選択が残る問題を修正＝最初の有効 option（滞在時間）へ**ダイアログ上で可視に自動切替**（黙った代替ではない・通常へ戻しても値は勝手に戻らない＝ユーザー操作尊重）。保存済みの無効組合せ（zp×日別×1m）も gear を開いた瞬間に是正される。テスト1件追加＝UI web 531/533 緑。実UI確認済み。

## ISSUE-081: gear の親子構造化（表示モードを先頭の親に・子は非表示切替でグレーアウト廃止）
- **ステータス**: RESOLVED（2026-07-15）
- **概要**: 「グレーアウトがユーザビリティを低下させている。表示モードの項目をトップに変更して親にしろ。その下に子（パラメーター項目）を設置しろ」（依頼者指示）。
- **実装**: ①catalog の params 順を変更し表示モード（segmented）をダイアログ先頭へ（グループ描画は params 初出順＝display 群が最上段の親・calc 群が子）②グレーアウト→非表示へ転換: dispbp は conditionalEnable（ISSUE-070 灰色化）を廃止し同述語を conditionalVisible へ（tf-period 列描画時は行ごと消える）。period は「zp×通常×対応tf のときだけ表示」の単一関数述語へ統合（旧: src で表示＋mode/tf で灰色化）③form_model.computeVisible に関数述語＋ctx 対応を追加（computeEnabled と対称）・properties_dialog._refreshVisible が ctx を透過。
- **検証**: MP web 274・UI web 531/533 緑（catalog テストを可視述語の真理値表へ更新）。実UI（8139・1h）: 先頭行=表示モード／通常=表示幅(bp)・期間 表示／日別=表示幅(bp) 行が消滅（is-disabled ではなく display:none）／リプレイ=表示幅(bp) 表示、のモード切替追従を確認。
- **追補（依頼者指示・calc 順序）**: 子（calc 群）の表示順を「ソース→バリューエリア→期間→表示幅(bp)」へ変更（order 1/2/3/4）。順序固定テスト追加＝UI web 532/534 緑。実UI で並びを確認。

## ISSUE-082: MP指標のリプレイモード撤去（present から削除・replay_ui 専用機能へ）
- **ステータス**: RESOLVED（2026-07-15・feature/mp-remove-replay-mode）
- **概要**: 「MP指標のリプレイモードの機能を削除しろ」（依頼者指示）。present（indicator_ui）の MP 表示モードからリプレイを撤去する。リプレイ機構は replay_ui（別アプリ）が共有資産として依存するため、**present の配線・UI のみ削除し、共有部（MarketProfileActor のリプレイ API・market_profile モジュール内 market_profile_replay_bar.js・replay_ui 側の独立コピー）は温存**（共有リソースの破壊的変更禁止）。
- **実装**: ①catalog: mode ENUM ['normal','replay','sessions']→['normal','sessions']（リプレイセグメント消滅）②indicator_controller._deriveMode: 保存済み mode='replay'／legacy replay:true を 'normal' へ正規化（旧インスタンスの後方互換）③composition_root_front: MarketProfileReplayBar の import/構築/注入を削除 ④index.html: #mp-replay-bar-host 削除 ⑤build.mjs: MODULE_ORDER から replay bar を除去＋indicator_ui 側 symlink 削除（market_profile モジュール内の実体は replay_ui 用に温存）⑥chart_interaction_controller（present 独立コピー）: リプレイ swipe スクラブブロック・replayBar 依存・_isReplayOn を削除（replayBar 未注入での pointerdown クラッシュも同時に解消）⑦form_model.paramToField: ENUM の保存値が enumValues に無い場合（撤去済み選択肢）は default へフォールバック＝mode='replay' 保存インスタンスの gear がアクティブ無しセグメントにならない。
- **検証**: MP web 274 緑・UI web 531/533 緑（リプレイ系テストを撤去仕様へ書換・form_model フォールバックテスト追加。既知2件=replay_analysis/timeline_player の module-not-found は本件と無関係の既存事象）。実UI（8139・mode=b）: gear 表示モードが【通常｜日別プロファイル】の2択・#mp-replay-bar-host 不在・mode='replay' 保存インスタンスを restore→「通常」アクティブで復元（src=dwell 保持）・MP 通常モードのライブ更新（market_profile_forming フェッチ）継続・コンソールエラーなし（favicon 404 のみ）。
- **残課題**: tests/replay_analysis.test.js・tests/timeline_player.test.js は存在しないモジュールを import する既存の不整合（本件以前から失敗）。削除は既存ファイル削除＝承認事項のため未着手。

## ISSUE-083: 日別プロファイルのライブ育成（当日/現在周期列を zp・dwell とも成長させる）
- **ステータス**: RESOLVED（2026-07-15・feature/mp-sessions-live-growth）
- **概要**: 「ライブモードで日足プロファイルもバー(zp・dwell)が育成される仕様を追加しろ」（依頼者指示）。日別プロファイル（tf-period 列）の当日/現在周期列がライブで凍結していた。
- **実測原因**: backend は当日（未完了セッション）を「キャッシュせず都度計算」（count は経過ティック・zp は経過分＋M_REPS_LIVE）で育成対応済み。凍結はフロント jitter buffer が一度 ready にしたチャンクを再取得しない構造（過去周期不変前提の設計）にあり、当日を含むチャンクだけがライブで陳腐化していた。backend 変更なし。
- **実装（フロント3点）**: ①TfPeriodJitterBuffer.refreshAt(time): time を含む ready チャンクを stale-while-revalidate 再取得（旧列保持→応答で差替え→onReady。失敗/進行中/未取得は非破壊 no-op・tf/src 変更中の応答は破棄）②TfPeriodProfileActor.onLiveTick(): 現在周期始端（=最新ローソク time。1D はセッションバー時刻規約・日中足は UTC floor＝列 time と同一規約）のチャンクを throttle（既定5s）付きで refreshAt→差替え成功時のみ一括再描画。可視範囲外の現在周期は fetch しない ③MarketProfileActor に onSessionsLiveGrow フック: 日別×tf-period 描画×growing（FOLLOW）の refresh（live tick 経路）で発火し、composition が tfPeriodActor.onLiveTick へ配線。ANALYSIS（static）では発火しない＝既存の成長軸（growing 信号）と整合。zp・dwell はソース透過（&src=zp）で共通。
- **検証**: TDD Red→Green（jitter buffer 3・tf-period actor 3・MP actor 2・composition 配線 1 追加）。MP web 282/282・UI web 532/534 緑（既知2件除く）。実UI（8139・FOLLOW×日別）: 1D で当日チャンク再取得が約5〜7s間隔で発火・当日列 tpo 66597→66667（+70/30s）／15m で現在周期列 tpo 628→662（ティック flush 周期≈1分）／zp（15m）で &src=zp 再取得＋現在周期 obs 1→2（経過分ごと）・zp 応答127ms・コンソールエラーなし。
- **追補（依頼者指示「5秒更新にしろ」「最新データで直ぐに更新しろ」・2026-07-15）**: parquet flush 周期（≈1分）律速を解消。/tf_period_profile の殻で served の in-memory LiveTickBuffer 末尾を controller へ注入し（forming の _augment_mp_forming_ticks と同型）、当日（未完了セッション）計算にのみ parquet 優先 dedup＋中央値±30% 外れ値除去で合成。count はティック単位・zp は分足格子末尾（ffill 停滞）を最新化。完了日は無視＝不変列のキャッシュ規約 byte 不変。実測: count 列 tpo 751→753→759→771（7秒間隔で毎回増加）・zp は分確定が即時反映（obs 14→15・price_max 68439→68454）。テスト: MP api tf-period 23 緑（live 合成 3 追加）・UI api 381 緑（殻透過 1 追加）。byte-parity は既知10件のみ（本変更と無関係）。
- **残課題**: なし（更新粒度: フェッチ5秒・count はティック鮮度≈5秒・zp は分足原子ゆえ各分確定から≈5秒で反映）。

## ISSUE-084: 現在値ラインの常時表示＋VA 幅カラースキーム（視認性向上・依頼者指示）
- **ステータス**: RESOLVED（2026-07-15・feature/mp-sessions-live-growth）
- **概要**: 「現在値の水準にラインを表示して、現在値の視認性をあげろ。VA幅もカラースキームで視認性をあげろ」（依頼者指示）。
- **実装**: ①現在値ライン: メイン系列の priceLine を固定色（橙 #ff9800・実線幅1・lastValueVisible=軸ラベル付き）で常時表示。lwc 既定の priceLineColor=''（バー色追従）は日別プロファイルのローソク透明化で線ごと消えるため固定色を明示（POC 赤・POC* 黄・カーソル青と非衝突の配色）②VA 幅カラースキーム: tf-period 列（日別プロファイル）の VA（va_low..va_high）内レベルは通常アルファ（0.98）・VA 外は減光（×0.35）で描画し、各列の VA 幅を一目で判別可能に。va 欠損列（旧応答・空日）は全レベル通常＝後方互換。zp・dwell 共通（列データの va_low/va_high はソース非依存で供給済み）。
- **検証**: TDD Red→Green（primitive VA アルファ 1・composition priceLine オプション 1 追加）。MP web 283/283・UI web 533/535 緑（既知2件除く）。実UI（8139）: 15m/1h で橙破線の現在値ライン＋軸ラベル（FOLLOW 中はライブ追従・68544→68533.95→68465.90 を確認）・1h zp 列の拡大確認で VA 帯内が明・帯外が減光・白 POC の三層が判別可能・ホバーで VA 境界（67710〜68070）読取・コンソールエラーなし。スクロール時にラベルが可視範囲末尾の値へ追従するのは lwc ネイティブ挙動（最新表示時は線とラベルが一致）。
