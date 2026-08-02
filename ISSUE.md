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
- **ステータス**: RESOLVED
- **検出**: 因果リビール再生バックエンド arch レビュー（2026-07-04・🟡-1）。
- **背景**: `usecase/intrabar_window.py` は `window_port.load_ticks()` を素通しし、mid=(bid+ask)/2＋窓フィルタ＋外れ値除去（本質不変 E-4）の呼び出しが `adapter/intrabar_window_repository.py` に在る。domain `tick_mid_series.mid_series` 自体は純化済だが、tick 源差替のたび各 adapter が mid_series を再結線＝本質ルールが adapter ごとに分散し得る。
- **対策（提案）**: Port を `load_raw_ticks(start,end)->[(sec,bid,ask)]` へ変え usecase 側で `mode=='real_ticks'` 時のみ domain `mid_series` を適用（mode ゲートは usecase 既存＝軽量性不変）。
- **関連**: replay_ui バックエンド増分（branch feature/contact-scan-replay）。
- **対応（2026-07-31・提案どおり実施）**: `IntrabarWindowPort` を `load_ticks(start,end) -> [(sec, mid)]` から **`load_raw_ticks(start,end) -> [(sec, bid, ask)]`** へ変え、domain E-4（mid 算出＋窓フィルタ＋外れ値除去 `tick_mid_series.mid_series`）の適用を **usecase 側の 1 か所**へ寄せた。
  - `mode=='real_ticks'` のゲートは usecase に既存のため**軽量性は不変**（他モードは tick を読まない）。
  - 外れ値しきい値は adapter のコンストラクタ引数から `IntrabarWindowRequest.outlier_threshold` へ移した（本質ルールのパラメータは本質を適用する層が持つ）。adapter から `tick_mid_series` への依存は消滅し、責務が「保管形式（parquet の日別レイアウト）→ 素の観測値」の変換に閉じた（偶有的性質のみ）。
- **これで何が防げるか**: tick 源を差し替えるたびに各 adapter が `mid_series` を再結線する必要がなくなる。結線漏れが静かに「外れ値除去なしの mid 列」を生む経路が構造的に消える。
- **挙動不変の実証（実データ A/B）**: `jp225_tick` の 2026-07-30 00:00 UTC から 1 時間（**生ティック 190,901 件**）で、旧経路（adapter 内で `mid_series` 適用）と新経路（usecase で適用）の結果が **20,631 件で byte 一致**。
- **検証**: `simulator/replay_ui` **187 passed**。Port 契約の変更に伴い、テストのフェイク 8 件を新契約（`load_raw_ticks` が `(sec, bid, ask)` を返す）へ更新し、adapter の統合テストは「**外れ値も窓外も落とさない**＝整形しない契約」を固定する形に書き替えた。

## ISSUE-032: replay_ui backend — 外れ値閾値 0.3 の二重定義（用途別だが同値）
- **重大度**: Low
- **ステータス**: RESOLVED
- **検出**: arch レビュー（🔵-3・2026-07-04）。
- **背景**: `domain/tick_mid_series.OUTLIER_THRESHOLD`（足内 mid 外れ値）と `adapter/_m1_repair.M1_OUTLIER_THRESHOLD`（M1 日内補正）が各 0.3。アルゴリズムは別物だが値・意図（±30%）同一で source が分岐＝値乖離リスク。
- **対策（提案）**: 単一 source-of-truth へ集約、または「別用途で独立の定数」である旨を両所へ明記して意図を固定。
- **関連**: replay_ui バックエンド増分。
- **対応（2026-07-30・提案の第 2 案「独立である旨を明記」を採用）**:
  - ISSUE-032 が指摘した重複相手 `adapter/_m1_repair.M1_OUTLIER_THRESHOLD` は**モジュールごと削除済みで現存しない**（grep 0 件）。旧コメントが参照していた `proto_server` も同様。
  - 現存する同値定数は `marketdata.outlier_policy.OUTLIER_THRESHOLD`（0.3）だが、**統合しない**と裁定した。理由: (1) 対象が別物（本定数はバー内 tick の mid 系列に対する中央値ベース除去、marketdata 側は確定足 OHLC のクランプ）、(2) 層が別（replay_ui の domain 層から データ取得基盤 marketdata へ依存させると domain → infrastructure の逆流になる）。
  - `tick_mid_series.py` のコメントを実態へ改め、独立の定数である理由を明記した（値の一致は偶然であり一方の調整が他方へ波及してはならない旨）。
- **検証**: `simulator/replay_ui` 198 passed。

## ISSUE-033: replay_ui backend — 未消費の抽象（E-3 window / ContactScanPort）のフロント確定時精査
- **重大度**: Low（YAGNI）
- **ステータス**: RESOLVED
- **検出**: arch レビュー（YAGNI 削除候補・2026-07-04）。
- **背景**: `domain/intrabar_window.window`（足境界→窓算出 E-3）と `replay_ports.ContactScanPort` は backend に production caller 不在（テストのみ）。窓はフロント replay.js が算出し `/intraday` に start/end で渡す設計、接点は次フェーズ想定＝将来仮説が根拠。
- **対策（提案）**: フロント増分で「消費者が生じるか」確定し、生じなければ削除。窓をサーバ側算出する usecase に倒す選択肢も検討。
- **関連**: replay_ui フロント増分で判断。
- **実測（2026-07-30）**: フロント増分は完了しており、消費者の有無が確定した。
  | 対象 | 本番消費者 | 判定 |
  |---|---|---|
  | `replay_ports.ContactScanPort` | **0 件**（全体 grep でヒット無し＝**既に削除済み**） | 決着 |
  | `domain/intrabar_window.window` | **0 件**（`tests/unit/test_intrabar_window.py` からのみ） | 削除候補（YAGNI 確定） |
  - `usecase/intrabar_window` は `serve_replay.py:219` から使われており**別物**（こちらは現役）。削除候補は `domain` 層の窓算出関数のみ。
  - 設計どおりフロント `replay.js` が窓を算出し `/intraday` へ `start/end` で渡しているため、サーバ側の窓算出は消費者が生じなかった。
- **ステータス**: 既存ファイル（`domain/intrabar_window.py`）の削除は承認事項のため**削除は未実施**。承認待ち。
- **対応（2026-07-30・ユーザー承認のうえ提案どおり削除）**: 消費者が生じないことが確定したため、`simulator/replay_ui/domain/intrabar_window.py`（59 行）と `tests/unit/test_intrabar_window.py`（79 行）を削除した。
  - `usecase/intrabar_window` は窓を**受け取る**だけで算出しない（`request.start` / `request.end`）。窓の算出はフロント `replay.js` が行い `/intraday` へ渡す設計であり、サーバ側の算出関数は最後まで呼ばれなかった。
  - `ContactScanPort` は既に消滅済み（実チェックアウトで grep 0 件。残存 24 件は `.claude/worktrees/agent-*` の古い作業ツリー内のみ）。
- **検証**: `simulator/replay_ui` **187 passed**（削除した 11 件ぶん減・他は全緑）。

## ISSUE-034: replay_ui backend — df 往復の列名 lower()/float() 強制が暗黙契約
- **重大度**: Low（現状安全）
- **ステータス**: RESOLVED
- **検出**: code レビュー（🔵-1・2026-07-04）。
- **背景**: `adapter/causal_compute_gateway._df_to_bars` が全列を `str(c).lower()`＋`float(row[c])` へ強制。現状は源 CSV 列が小文字＋compute の case-insensitive アクセスで安全（SMA round-trip 同値テスト合格）。非数値列・大文字前提指標が将来入ると `float()` 例外/列名不一致。
- **対策（提案）**: 「OHLCV 数値列前提」を docstring 明記、または非対象列を保存扱いにするガード追加。
- **関連**: replay_ui バックエンド増分。
- **対応（2026-07-30・提案の「docstring 明記」を採用）**: `_df_to_bars` に契約を明示した。
  - 列名は `str(c).lower()` へ正規化する（大文字は保持されない）
  - 値は `float64` へ強制する（非数値列は例外になる）
  - すなわち本経路は **OHLCV 相当の数値列のみ**を運ぶ
  - 現状安全な理由（源 CSV が小文字・compute が case-insensitive）と、破れる条件（非数値列／大文字前提の指標）も併記した。
- **ガード追加は見送り**: 破れる指標が現存せず（YAGNI）、追加すると全 compute 経路へ分岐が入るため。契約違反時は `float()` が例外で落ちる＝沈黙しない。
- **検証**: `simulator/replay_ui` 198 passed。

## ISSUE-035: replay_ui backend — 静的配信のパストラバーサル判定が prefix 一致のみ（proto 継承）
- **重大度**: Low（web_dir 既定 None＝静的配信オフ）
- **ステータス**: RESOLVED
- **検出**: code レビュー（🔵-3・2026-07-04）。
- **背景**: `framework/serve_replay.py` の静的配信が `str(fp).startswith(str(web_dir))`。区切り無し prefix のため `web_dir="/a/web"` で `/a/webevil` が通過しうる（proto_server と同一弱点）。既定 web_dir=None で影響は低いが、フロント配信有効化時に露見。
- **対策（提案）**: `os.path.commonpath([fp, web_dir]) == str(web_dir)` もしくは末尾セパレータ付き比較へ。
- **関連**: replay_ui フロント増分（静的配信有効化）時に対応。
- **対応（2026-07-30・調査の結果、本体は是正済みと判明。テストのみ補完）**:
  - `replay_ui` の静的配信は `framework/static_file_server.py` へ抽出され、`resolve()` 後の `Path.is_relative_to`（区切り境界一致）で CWE-22 を封じ済み。docstring に `startswith` が危険な理由（接頭辞共有の兄弟へ逸脱できる）まで明記されている。回帰テスト `tests/unit/test_static_file_server.py::test_prefix_sibling_traversal_is_rejected` が攻撃ケース（`replay_web_SECRET`）を固定している。
  - 全体 grep で `startswith(str(...))` による経路判定は**残存 0 件**。
  - **一方 `unified_ui/router.py`（実際に 8000 で配信される側）は防御は正しい（`os.sep` 付き比較）が回帰テストが無かった**ため、同じ攻撃ケースを追加した（`unified_ui/tests/test_router.py`）。
- **検証設計の補足**: 当初 生の `..` だけでテストを書いたが、`_serve_static` 手前の `rel.startswith("..")` で弾かれ **realpath ガードまで到達せず空虚**だった（ガードを弱める変異を検出できなかった）。`web_root` 内から外を指す **symlink** 経路へ組み直し、変異注入で「機密が漏洩した」を検出できることを実証した。
- **検証**: `unified_ui` 15 passed。

## ISSUE-036: replay_ui backend — /candles 非tick分岐の過剰直列化＋失効 docstring 参照
- **重大度**: Low
- **ステータス**: RESOLVED
- **検出**: code レビュー（🔵-4/🔵-5・2026-07-04）。
- **背景**: (a) `serve_replay` が `/candles` の非 tick 軽量経路も `_HEAVY_LOCK` で直列化（proto は tick のみ施錠・出力不変の過剰直列化）。(b) `domain/tick_mid_series` 等の docstring が本 worktree 不在の `contact_scan.tick_window.window_ticks` を bit 一致対象と引用（実挙動は proto `do_intraday` tick 経路で検証済）。
- **対策（提案）**: (a) 非 tick 軽量経路を施錠外へ、または保守的直列化の意図をコメント明記。(b) 参照を「proto_server.do_intraday tick 経路」へ更新。
- **関連**: replay_ui バックエンド増分。
- **対応（2026-07-30）**:
  - **(b) 失効参照の是正**: `contact_scan.tick_window.window_ticks` は**現行ツリーに存在しない**（全体 grep 0 件。`simulator/usecase/contact_scan` は現存するが `tick_window` を持たない）。「参照実装と bit 一致」の主張は根拠を失っているため撤回し、実際に挙動を固定している `tests/unit/test_tick_mid_series.py` を指すよう `domain/tick_mid_series.py` と `adapter/intrabar_window_repository.py`（2 箇所）を書き換えた。
  - **(a) 過剰直列化は意図として明記（据え置き）**: 非 tick の軽量経路も同じ錠の内側にある点を、理由付きでコメント化した。緩めない理由: `/candles` は timeframe により resample の有無が実行時に決まり呼び出し前に軽量判定できない（判定を足すと分岐の二重管理になる）／並行化の利得が未実測。緩めるなら所要時間の実測と OOM 耐性の確認を先に行う。
- **検証**: `simulator/replay_ui` 198 passed。

## ISSUE-037: replay_ui frontend(再生層) — controller への結合＋View fallback の堅牢化
- **重大度**: Low（挙動非差・parity 由来）
- **ステータス**: RESOLVED
- **検出**: 再生層(INC-F2) arch/code レビュー（🔵・2026-07-04）。
- **背景**: (a) `web/js/replay.js` が `controller._timeframe`/`_recentBars` の private を直接参照＋`applyIndicator`/`removeInstance` を実行時 monkeypatch（syncBoundary ラップ）。プロト replay.js の忠実移植由来で依存方向違反ではないが結合が強い。(b) `replay_view.readSpeed/readMode` は要素欠落時 NaN→既定退避（clampSpeed→1/real_ticks）。プロトは `null.value` で throw。現行 index.html では rp-speed/rp-mode 常設のため到達不能。(c) `syncSpeedUI` の `clampSpeed(parseFloat())` はプロトの `+value` と [0,1] 範囲で等価。
- **対策（提案）**: (a) controller 側に public accessor / フック（onApplied 等）を設け private 参照・monkeypatch を解消。(b)(c) 現行 DOM では非到達＝現状維持可。厳密忠実化するなら proto 準拠へ寄せる。
- **関連**: replay_ui フロント増分（INC-F2）。
- **対応（2026-07-31）**:
  - **(a-1) private 参照は既に解消済みだった**（コードで確認）。`controller._timeframe` / `_recentBars` は ISSUE-181 で `TimeframeController` へ実体を移した際に **getter/setter の互換アクセサ**として明示公開されており（「旧 host フィールド面」とコメント済み）、`replay.js` の読み書きは正規の面を通っている。本 Issue 起票時の指摘はこの時点で失効していた。
  - **(a-2) monkeypatch を購読スロットへ置換（実施）**: `IndicatorController.setAppliedObserver(fn)` を新設し（`setTimeframeObserver` と同型の規律）、`applyIndicator` / `removeInstance` を「薄いラッパ＋内部実装（`_applyIndicatorInner` / `_removeInstanceInner`）」に分けて完了後に 1 回通知する。`replay.js` は monkeypatch と `patched` 配列による原状復帰を捨て、購読登録／`destroy()` での解除に置き換えた。
    - monkeypatch の何が問題だったか: (1) 差し替え順序に依存して壊れる (2) 復元漏れが静かに残る (3) subclass の override（`ReplayIndicatorController.removeInstance`）と二重に噛む。
    - **通知位置は monkeypatch 時代と同一**にした。未知 id で適用が no-op のときも通知する（従来は呼び出しごとに後処理が走っていたため）。
  - **(b)(c) は現状維持**（起票時の判定どおり）。`readSpeed/readMode` の要素欠落フォールバックと `clampSpeed(parseFloat())` は、現行 `index.html` が `rp-speed`/`rp-mode` を常設するため**到達不能**であり、プロト忠実化のためだけに挙動を変える利得がない。
- **検証**: 回帰テスト 5 件を追加（適用/削除の完了後に 1 回・通知時点で状態が確定済み・未知 id でも通知・null で解除・購読者不在で落ちない）。**変異注入**（通知を外す）で 3 件が失敗することを確認。`indicator_ui/web` **953 passed** / `replay_ui/web` 267 passed / `market_profile/web` 311 passed。実 UI（リプレイ 8281）で指標の追加・削除を実行し、凡例 1→2→1・コンソールエラー 0 を確認した。

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
- **ステータス**: RESOLVED
- **検出**: ISSUE-042 の code-review（🟡・2026-07-06）。
- **背景**: sessions モードは `isGrowingPush()=false`（`_growing && !_sessions` を満たさない）のため、ISSUE-042 の cursor 未確定ガードの対象外。ページ読込 restore（`_untilTime` 未設定＝`to=undefined`）で基底 refresh が全期間 sessions 分割を setProfile し、再生開始後の `refresh(to=T)`（機構A・as-of-T）で縮小ジャンプが起きうる。1W/1M（forming 非対応 tf）は enterBar→null で後続リセットが無くフラッシュ不成立＝対象外（妥当判定済み）。
- **対策（案）**: sessions は描画経路が別（共有グリッド＋各日 tpo 整列）のため個別ハンドリング要。まずブラウザ目視で実挙動を確認してから対策設計する（ISSUE-042 のガードをそのまま流用しない）。
- **関連**: ISSUE-042・ISSUE-041（機構A: refresh(to,sessions)）。
- **目視・実測（2026-07-31・リプレイ UI 8281・対策案どおり「まずブラウザで実挙動を確認」を実施）**: **縮小ジャンプは起きない。疑いは否定される。**
  - 手順: MP を日別プロファイル（sessions）で有効化 → **ページ再読込**（＝ restore 経路・`_untilTime` 未設定）→ カーソル移動で `refresh(to=T)` を発火。`setSessions` の呼び出しごとにセッション数と日付範囲を記録した。
  - restore 直後: `n=60`（2026-05-11 〜 2026-07-31）。
  - カーソルを 25 バー戻す間の `setSessions`: `n=60`（05-08〜07-30）→ `n=60`（05-07〜07-29）→ `n=60`（05-06〜07-28）。
  - すなわち as-of-T は「全期間から縮小」するのではなく、**セッション数を 60 に保ったまま窓がスライド**する。restore の全期間表示と as-of-T の間に本数差が生じないため、ISSUE-042 と同クラスのフラッシュは成立しない。
- **対応**: コード変更なし（対策不要）。ISSUE-042 のガードを流用しないという判断も維持する（そもそも縮小が無いため）。
- **⚠ 検証手法の失敗と是正**: 最初は `rp-play` を押して計測したが、再生が始まっておらず（ボタン表記が `▷` のまま・表示も不変）`setSessions` が 1 回も呼ばれない**空虚な計測**だった。`rp-next` / `rp-prev` による確実なカーソル移動へ切り替えて確定させた。
- **検証**: `simulator/replay_ui` 187 passed / `replay_ui/web` 267 passed（計測用プローブは撤去済み）。

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
- **ステータス**: RESOLVED
- **検出**: 2026-07-06 ISSUE-045 対応中の全体テスト実行で検出。HEAD（変更前）でも同一失敗を確認済み。
- **内容**: `tests/replay_analysis.test.js` と `tests/timeline_player.test.js` が `js/usecase/replay_analysis.js` 等の不存在モジュールを import して ERR_MODULE_NOT_FOUND。79982b8「未追跡のソース/ドキュメントを保全コミット」でテストのみ保全されソース側が欠落した可能性。
- **対策案**: 対応方針は依頼者判断待ち（欠損ソースの復元 or テスト撤去）。
- **対応（2026-07-30・調査の結果、対処不要と判明）**: 現行コードから当該テストも参照先モジュールも**消滅済み**。
  - `indigators/indicator_ui/web/tests/` に `replay_analysis.test.js` / `timeline_player.test.js` は存在しない。
  - `js/usecase/replay_analysis.js` / `timeline_player.js` も存在しない。
  - 残存は旧プロトタイプ `prototype_260626-01/web/tests/` のみで、維持対象のテストスイートには含まれない。
  - 現行 web スイートは 932 passed / 0 failed（ERR_MODULE_NOT_FOUND なし）。
  ⇒ 「欠損ソースの復元 or テスト撤去」の判断待ちだったが、**撤去済み**として決着。

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
- **ステータス**: RESOLVED
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
- **対応（2026-07-31）**: tf-period 列を**描画時に barw 幅へ束ねる**（取得・キャッシュ・API は一切変更しない）。
  - **なぜ描画時か**: tf-period 列は測定としては最小価格単位で保持する（粗いビンで測ると分布が退行して見えるアーティファクトを持ち込むため。[[short-tf-profile-not-degenerate]]）。一方「レンジ」はユーザーが選ぶ**表示解像度**である。両者を分離し、束ねるのは描画とホバー読取のみとした。`/tf_period_profile` にパラメータを足さないためディスクキャッシュのキー設計にも影響しない。
  - 実装: 純関数 `aggregateLevelsToBins(levels, binWidth)`（価格 0 起点の絶対格子＝列間で行がずれない）／`MarketProfileHistogramPrimitive.setTfBinWidth()`／`TfPeriodSink` に 1 面追加／`MpFetchParams.barw()`（明示 range と dispbp 写像後の range を 1 か所で解決）／composition root の `syncTfBinWidth()`。
  - 描画とホバー読取は**同じ行**を見る（`_effectiveLevels` / `_effectiveRowWidth` を共有）。片方だけ束ねるとカーソル位置と描画行がずれる。POC は束ねたとき「poc を含むビン」を POC 色にする。
- **UI 名称の変化（起票時との差）**: 起票時の「レンジ(pt)」は現行 UI では **「表示幅(bp)」** になっており、`MpFetchParams.dispExtra()` が最新終値から `barw = close × bp/1e4` へ写像している（ISSUE-079 の二層構造）。欠陥は同一で、写像後の barw が tf-period 経路へ届いていなかった。
- **検証（実 UI・ライブ 8001・日別プロファイル）**: 描画行数を直接計測し、表示幅(bp) に**単調反応**することを確認した。
  | 表示幅(bp) | 1 列あたり描画行数（中央値） | 最大 |
  |---|---|---|
  | 3 | 4 | 19 |
  | 40 | 1 | 3 |
  | 120 | 1 | 2 |
  - **⚠ 検証手法の失敗と是正**: 当初 canvas のピクセルハッシュで A/B しようとしたが、**修正前でも 4/11 の canvas が変化**した。ライブ更新（tick・forming bar）が毎秒 canvas を書き換えるため、この指標では分離できない。描画行数の直接計測へ切り替えて確定させた。
  - 単体テスト 9 件を追加（束ねの合算・絶対格子・昇順・非正/空/非有限の防御・後方互換・同値再設定で再描画しない）。`indicator_ui/web` 944 passed / `market_profile/web` 311 passed / `replay_ui/web` 267 passed。
- **ISP 契約の更新**: `TfPeriodSink` は 2 面 → 3 面。最小性テストは「面数の固定」ではなく「宣言した面がすべて実利用されること」を意味するため、実利用箇所（`syncTfBinWidth`）を明記して更新した。

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
- **ステータス**: RESOLVED
- **発生日**: 2026-07-11（src=zp 追加作業中の回帰確認で発見）
- **概要**: `indigators/market_profile/api/tests/test_market_profile_byte_parity.py` のうち dwell/m1（to=1780666320）5 件・forming 3 件が、**作業変更を stash した素の develop でも同一に失敗**する（8 failed / 19 passed で一致確認済み）。src=zp 追加とは無関係の既存問題。
- **推定原因（未検証）**: golden 作成後の実データ（ticks parquet）更新または dwell キャッシュ／active table 依存のドリフト。
- **対応方針**: 本 Issue では修正しない（golden 再生成は挙動確定の判断を要するため依頼者承認待ち）。zp 作業の回帰判定は「この 8 件を既知ベースラインとし、それ以外の全テスト green」を基準とする。
- **決着（2026-07-30・再実測）**: **27 passed（3 連続緑・0.6s）**。当時失敗していた `forming:*` および `to=1780666320` 系を含む全 27 件が通る。
- **解消したコミット（特定済み）**: `d3aad36`（2026-07-15・ISSUE-089 対応）「byte-parity を決定論の合成世界へ移行」。
  - 真因は推定どおり実データ側だった: 実 `jp225_tick` の 1m 原子ストアが**ローリング保持**（窓左端が壁時計とともに前進）するため byte 固定が原理的に不能だった。
  - 対処は golden 再生成ではなく、jp225_tick 系 12 ケースへ合成世界（`mp_parity_world`）を注入して**決定論化**（16s→0.6s）。
- **本 Issue 側の作業は無し**（別 Issue の対応で解消）。クローズが漏れていた記録を是正する。

## ISSUE-060: src=zp×日別×非対応tf（1m/5m）で日別タイルが消える（委譲述語の片側更新）
- **ステータス**: RESOLVED（2026-07-11）
- **発生日**: 2026-07-11（src=zp 実装の実UIブラウザ検証で発見）
- **概要**: sessions モードで MP actor はタイル描画を tf-period 列へ委譲する（sessionsDrawnByTfPeriod）が、src=zp の tf-period は 15m..1D 限定のため 1m/5m では tfpShouldOn が false。委譲述語だけが true のままだと「MP はタイルを描かず・tf-period も無効」で誰も日別を描かず、normal ヒストへ落ちて見えた。
- **原因**: zp の tf ゲート（zpTfOk）を tfpShouldOn にのみ追加し、委譲述語 sessionsDrawnByTfPeriod に追加しなかった（同一条件の二重管理）。
- **対策**: composition_root_front.js で ZP_TF_ALLOWED/mpSrc/zpTfOk を上段へ単一定義し、委譲述語と tfpShouldOn の**両方**が同じ zpTfOk() を参照するよう修正（単一情報源化）。
- **検証**: 実UI（Playwright・実HTTP・port 8138）で 5m×zp×日別 → MP actor の日別 z タイルが自前描画されることをスクリーンショットで確認（zp-05）。1D×zp×日別の tf-period z 列（zp-03）・normal live zp の POC* 黄線（zp-02）・src=dwell 回帰（zp-06）も確認。web テスト回帰 531/533（残 2 は既存 module-not-found・zp 無関係）。

## ISSUE-061: 過去の高 z(p) 受容水準への価格再訪時の反応計測（未実施・依頼者発行）
- **ステータス**: RESOLVED（主検定は決着・副次 2 指標は定義待ちで未実施）
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

### 実施（2026-07-31）— 主検定の結論: **naked POC* は S/R として機能しない**

- **実装**: `mp_stats/step9_naked_revisit.py`（既存キャッシュ `znull` / `mgrid` を読むのみ・再計算なし）。
  - 行→価格の写像を実測で確定: **`price = exp(k · grid_w · 1e-4)`**（対数価格 1e-4 格子）。400/400 日で行域が当日の分足レンジを包含することを確認。
  - 走査対象: znull と mgrid が**ともに非空**の **3,564 営業日**。
- **事前登録どおりの主検定 1 本**（z_thr=3・L=60 日・k=30 分・x=4 行）。事件＝形成後**初回**接触（naked）。
- **結果**:
  | 群 | 事件数 | 跳ね返り率 |
  |---|---|---|
  | 本物（高 z セル・z≥3） | 48,833 | **30.80%** |
  | 偽水準 A（同日の低 z セル・z≤0.5） | 381,121 | **28.24%** |
  - 単純プール差 = **+2.55pt**（2 標本比率 z = +11.76）
  - **日単位クラスタ（同日内で対にした差）= −1.27pt（t = −2.54・2,850 日・差が正の日は 42.4%）**
- **結論**: プール比較は **Simpson のパラドックス**で符号が逆転する。事件は同一日に何十件も生じて独立でないため、有効標本は事件数ではなく**日数**であり、正しい比較は同日内の対である。同日内で見ると本物は偽水準 A を**上回らない**（むしろわずかに下回る）。ISSUE-061 の判定規準は「本物への反応が**両**偽水準を統計的に上回って初めて S/R として実在」であるから、偽水準 A に勝てない時点で**判定は否定**であり、偽水準 B の実装を待たずに結論が確定する。
- **⚠ 途中で犯した単位の誤りと是正**: 最初 `x=4 行` を znull の**セル幅**（対数 1e-4 ≒ 0.9pt @9,000）で解釈し、4 行 ≒ 3.6pt として測ったところ両群とも 72% 台に張り付いた（30 分あればほぼ常に到達＝検定が飽和）。`行` は Step5/Step6 と同じ**日レンジ 1/40**（`DEPART_ROWS=4` のコメントに「日レンジの 10%」と明記）であり、是正して 30.8% / 28.2% を得た。
- **接触判定の是正**: セル幅が細いため「セル内に入る」だけでは 1 分で跨いだ再訪を系統的に取りこぼす。「セル内 **または** 水準を跨ぐ（符号反転）」へ変更した。
- **未実施（依頼者の操作的定義待ち）**: 反応 3 物差しのうち **滞在**（水準近傍の滞在分数）と **減速**（帯の通過所要時間）は、`水準近傍` / `帯` の幅が未確定。本 Issue 自身が「**『反応』の操作的定義は依頼者の言葉で確定してから実装する**（仮説文の解釈違い再発防止）」と定めているため、推測で幅を決めず未実装とした。**偽水準 B（Null B サロゲート）**も同様に未実施だが、上記のとおり主検定の結論は変わらない。
- **検証**: 判定部品の単体テスト 10 件（行→価格写像・セル内接触・跨ぎ接触・未到達・x 行の要否・接近方向の取り違え防止・判定不能・k 分の窓・行単位が日レンジ/40 であること）。**10 passed**。

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

## ISSUE-085: zp の VA 水準異常（_value_area の int 切り捨てで VA が全域化）＋VA 表現をラインへ変更
- **ステータス**: RESOLVED（2026-07-15・feature/mp-sessions-live-growth）
- **経緯**: ISSUE-084 の VA 可視化に対し依頼者指摘「日足プロファイルの zp の VA 帯のカラースキームが適用されていない」「dwell は減光されているがラインで表現しろ」「VA の水準がおかしくないか?」。
- **実測原因（2件）**: ①表現の問題: zp 列の levels は sparse（z>0 のみ）で VA が可視レベル全域を覆う列が多く（実測: 直近 1D 3列中2列で VA外レベル=0）、「VA 外減光」方式では判別対象が存在しなかった ②計算バグ: `market_profile._value_area` が `int(tpo[i])` で重みを切り捨てるため、float の z（大半が1未満）が全て 0 になり累積が閾値へ届かず**全ビン採用＝VA が列レンジ全域へ拡大**（再現: z=[0.9]*4 → 全域）。カウント系（整数 TPO）は影響なし。影響範囲は zp の全 VA（通常プロファイルの VAH/VAL・日別タイル・tf-period 列）。
- **修正**: ①_value_area の累積を float 化（整数入力は同値＝byte-parity 維持・既知10件のみ不変を確認）②tf-period 列の VA 表現を「VA 外減光」→「VAH/VAL 境界ライン（灰・列幅）」へ変更（zp の sparse 構造でも常に描ける・レベルバーは減光しない）③完了日 zp 列のディスクキャッシュは旧 VA を含むため世代 bump（s2→s3・削除なし）。C_VA_LINE は依頼者調整で α0.3。
- **追補（依頼者指示「境界ラインは削除、背景のみ減光（バーは減光しない）」）**: VA 表現を境界ライン→**方向背景の2トーン**へ変更。VA 帯 [va_low,va_high] は通常 α0.1・VA 外の占有レンジは減光 α0.04（陽=緑/陰=赤の色相は不変・レベルバーは減光しない・ラインなし）。va 欠損列は単一背景・dirUp 未注釈列は背景なし（従来仕様）。テスト書換 1・実UI（1D×日別×zp）で2トーン背景を確認。
- **検証**: TDD Red→Green（_value_area float 2・primitive 表現 1）。MP api 全緑（byte-parity 既知10のみ）・MP web 283・UI web 533/535。実測: 1D zp 直近3列の VA幅/レンジ 0.32/0.40/0.53・VA内 z シェア 0.89〜0.93（修正前は全域＝1.0）。実UI（8139・1D×日別×zp）: VA ラインが質量帯（明色バンド）を正しく括り、疎な裾には掛からないことをスクリーンショットで確認。

## ISSUE-086: 全時間足でパラメータ統一（1W/1M＝セッション日次ロールアップのバケット列）
- **ステータス**: RESOLVED（2026-07-15・feature/mp-unify-tf-params）
- **概要**: 「日足と週足と月足でパラメータの設定項目が違う」→「原子は揃っているのでロールアップで対応できないのか」→「いつも通りの開発フェーズで全時間足でパラメータを統一しろ」（依頼者指示）。時間足による gear 項目の欠落（1W/1M で期間・日別列・zp が不成立）を、セッション日原子のロールアップで解消した。
- **設計**: 週/月バケット規約は rollup（marketdata.resample）の参照実装に厳密一致: 1W=W-FRI（週=[土..金]ブローカー日・ラベル=金曜）/1M=ME（ラベル=暦月末）・列 time=ラベルの UTC 深夜（バーと同一）。①marketdata/session_day.py に session_period_label/period_session_labels/next_period_label を追加（resample_ohlc_tf とのラベル一致を合成データで検定）②count バケット列＝日次 1D 列（既存 s1 キャッシュ再利用）の価格キー加算・poc/va は _value_area_sparse で再計算 ③zp バケット列＝z は加算不可のため日次 {obs,mean,var}（_zp_day_rollup＝znull キャッシュ再利用・独立日でモーメント加算可）を絶対 log 格子の k 空間で合成し z を再計算（compute_zp_profile の窓合成と同一規約）。当日は live buffer 合成グリッドで都度計算（ISSUE-083 追補と同鮮度）④完了バケットは mem→disk（{tf}/s1/g10・{tf}/s3/zp）。
- **フロント統一**: _MP_PLAYER_TF/_MP_ZP_TF・ZP_TF_ALLOWED・tf-period 有効述語へ 1W/1M を追加（LiveTickPlayer の isPlayerTimeframe とは分離）。期間（period）の tf 制限を撤廃（通常モードのみ条件・「当日」窓は tf 独立）。1W/1M バー time はラベル＝未来日になり得るため _periodExtra は now クランプで現在セッションへ写像（回帰テスト付き）。バケット tf の jitter チャンク窓は 45 日上限を外し 96 周期/チャンク（実測: 週足全期間表示のリクエスト 192→9・LRU スラッシング解消）。
- **検証**: marketdata 139（ラベル規約 4 追加・参照実装一致含む）・MP api 226・MP web 284・UI web 533/535 全緑。実UI（8139）: 週足×日別×zp で週列（2トーン VA 背景・白 POC・ホバー読取 z+0.62/VA 66942〜68548）・月足列・週足×通常で mode/src/va/period/dispbp 全5項目表示＝1D と同一（統一達成）・1W count/zp 応答 0.16〜0.28s・当日週バケットのライブ育成は既存 onLiveTick 経路がそのまま機能（列 time=最新バー time）。

## ISSUE-087: システム全体アーキテクチャ調査（アーキテクチャエージェント・依頼者指示）
- **ステータス**: RESOLVED（2026-07-15・feature/issue-087-arch-fixes で対応完了。残課題は下記追補）
- **総合判定**: 不合格（構造的リスク 3 件・改善推奨 4 件。最下層 marketdata の依存healthは健全＝内側→外側の逆流 0 件・上流 import 0 件）
- **🔴 構造的リスク**:
  1. **MP backend が indicator_ui の `adapter` パッケージへ裸名依存（sys.path 注入前提）**: market_profile_controller.py:28 ほか production 4 ファイルが `from adapter.compute import ...` を server.py:33-41 / _indicator_ui_bridge.py:43-45 の sys.path.insert で解決。indicator_ui 側の再編で MP が無言破壊されるリスク。推奨: 共有純粋物（ERROR_STATUS・tf 秒長等）は marketdata へ降ろし、残りは MP 側 Output Boundary（protocol）＋Composition Root 注入（DIP）。
  2. **tf→秒長／許可 tf 集合が 5 箇所以上に散在（単一情報源違反・実ドリフト有）**: _TF_BAR_SEC（mp controller）・TF_BAR_SEC（actor.js/growth_window.js）・TF_SECONDS（live_tick_player.js）・TIMEFRAME_RULES（resample.py）・index.html の tf ボタン（**30m が UI に欠落＝既に不一致**）。actor.js は bundler の top-level const 衝突で growth_window を import できず再宣言。推奨: tf メタ単一定義（Python=marketdata.resample / JS=tf_meta.js domain 1 個）＋HTML ボタン生成＋bundler の名前空間化。
  3. **セッション日規則・VA 定義が Python/JS 二重実装（同期は手写しスポット値のみ）**: session_day.py ↔ session_day.js（同期検定は JS テストのハードコード 2 値のみ）、_value_area ↔ dwell_accumulator.js valueArea＋VA_PCT=0.70 散在。推奨: Python から golden fixture 生成→JS テストが読む生成同期（最小）／規則の単一言語化（望ましい）。
- **🟡 改善推奨**: ①server.py が /candles・/forming_bar で controller 層を飛ばし marketdata/compute 直参照（殻へ業務分岐が漏出。handle_x 純関数へ統一を推奨）②スライス間レイヤ不統一（replay_ui=5層完備 / indicator_ui api=usecase 欠落 / MP api=2層のみ。最低限の命名規約明文化）③sys.path 実行時 insert が結線機構（3 系統。正規パッケージ化＋main 結線へ）④dwell/zp キャッシュに世代 GC 戦略なし（パラメータ変更で旧世代ディレクトリが増殖＝既知の清掃残件と同根。世代マニフェスト＋GC ツール推奨）。
- **🔵 将来検討**: symlink 共有は Windows/tar/CI で無言破壊リスク（共有 domain の shared/ パッケージ昇格）。過剰抽象なし（YAGNI 健全）・lightweight-charts/pandas/zoneinfo/HTTP 殻の隔離は概ね良好。
- **変更局所性の実測**: 新指標追加=スライス内で局所（良好）／**新時間足追加=最低 5 箇所**（欠如・🔴-2）／新データソース追加=局所（良好）。

## ISSUE-088: 徹底コードレビュー結果（コードレビューエージェント・依頼者指示）
- **ステータス**: RESOLVED（2026-07-15・feature/issue-088-review-fixes で対応完了。詳細は下記追補）
- **対象**: ISSUE-086（全時間足統一・レビュー時は作業ツリー差分＝現在はコミット済み）＋ISSUE-083〜085（ライブ育成・VA 修正・2トーン背景）。深度=完全（設計/ロジック/品質の3段階）。
- **判定**: 🔴必須修正 **なし**（データ破壊・セキュリティ・明白バグ不検出）。**条件付きマージ可**＝CI 条件（byte-parity 10 件 RED）の解消が承認の必要条件。
- **確認済み（精査の上問題なし）**: 週/月ラベルの整列性（resample・candle・forming と UTC 深夜で一致・年跨ぎ/閏/DST 境界を独立実測）／zp モーメント加算の独立性と k 空間整合／live_ticks が完了日キャッシュを汚さない保証／LiveTickBuffer の lock 安全性／入力 whitelist 検証（パストラバーサル不成立）。
- **🟡 推奨修正**:
  1. **byte-parity 回帰スイート 10 件 RED（develop 由来の既存事象）**: エージェント実測で m1（整数経路）の golden 相違＝固定窓 parquet の**データドリフト**起因と確定（ISSUE-085 の _value_area float 化・ISSUE-086 起因は棄却）。対処案: golden 再生成（依頼者裁定待ちの既知残件＝ISSUE-078 記載の dwell golden 2 件を含む）または合成データ注入でデータ非依存化。
- **🔵 改善提案（バックログ）**:
  1. DST 切替週（3月/11月）を含む週/月ラベル×resample 一致テストの追加
  2. jitter buffer refreshAt: tf/src 変更交差時に _refreshing ガードが交差削除され得る（無害・二重фetch の帯域微増。finally で tf/src 照合を推奨）
  3. tf-period zp のディスク世代タグ（s3）が _ZP_CACHE_VERSION 非連動（将来 bump 時の陳腐化リスク。世代タグへ織り込み推奨）
  4. _DAY_MEM（上限256）が 1M 長期走査で LRU スラッシュの可能性（バケット/日次の LRU 分離 or 上限引き上げ）
  5. next_period_label(1M)・_bucket_completed の直接単体テスト追加（年跨ぎ・閏 2 月）
- **残存リスク（エージェント申告）**: forming 週バーの実UI水平整列は実UI未確認（→ISSUE-086 の実UI検証で週足列・ツールチップ・現在値ライン整列は確認済み）。

## ISSUE-087/088 対応記録（依頼者指示「087対応後に088対応・いつもの開発フェーズ絶対遵守」）
- **ステータス**: RESOLVED（2026-07-15・feature/issue-087-arch-fixes → feature/issue-088-review-fixes）
- **ISSUE-087 対応（🔴3件＋🟡3件実施）**:
  1. 🔴-1 裸依存解消: tick ref・floor 規則・期間始端・now 解決・ERROR_STATUS を最下層 marketdata（tf_meta.py/api_contract.py）へ移設。market_profile_api は marketdata のみ参照（indicator_ui 側は再エクスポートで互換維持）。裸 `adapter` import の回帰ガードテスト追加。
  2. 🔴-2 tf メタ単一化: JS の TF_BAR_SEC/TF_SECONDS 4重定義を domain/tf_meta.js へ集約（IIFE 連結の const 衝突も解消）。Python は marketdata/tf_meta.py。UI に欠落していた **30分ボタンを追加**（実ドリフト解消・実UIで 30m 列/ライブ稼働確認）。
  3. 🔴-3 生成同期: tools/gen_js_parity_golden.py が DST 切替・週/月/年跨ぎ 160 境界点のセッション日規則・VA（整数/float）・tf 秒長を fixture 生成し、JS テストが session_day.js/valueArea/tf_meta.js との一致を網羅検定。Python 側に fixture 鮮度ガード（規則変更時の再生成を CI 強制）。
  4. 🟡-1 薄殻化: /candles・/forming_bar を handle_candles/handle_forming_bar（純関数・3段フォールバック含む）へ抽出し殻を縮小（テスト5件）。🟡-2: .doc/LAYERING_CONVENTIONS.md にレイヤ役割対応・依存規則を明文化。🟡-4: tools/cache_gc.py（現行世代をコード定数から導出し孤児列挙・実測 179MB/16 エントリ・削除は --delete 明示＋承認後のみ）。
  - **残課題（承認待ち）**: 🟡-3 sys.path 正規パッケージ化（技術スタック変更＝承認事項として LAYERING_CONVENTIONS.md に記載）／GC の実削除実行／🔵 symlink 共有の shared パッケージ昇格。
- **ISSUE-088 対応（🟡1＋🔵5 全件実施）**:
  1. 🟡-1: byte-parity golden を再生成スクリプト（tools/regen_mp_byte_parity_golden.py・クエリ集合不変）で現行データ基準へ更新。既知 10 件（データドリフト 8＋ISSUE-078 設計変更の dwell 2）のみ更新され 27/27 緑＝CI 承認条件を回復。
  2. 🔵-1: 週/月ラベル×resample 一致テストへ米 DST 切替区間（2026春・2025秋）を追加 🔵-2: jitter buffer refreshAt の finally を tf/src 世代照合（交差削除防止）🔵-3: tf-period zp ディスク世代を _ZP_CACHE_VERSION 連動（s3/zp→s3/zp-v3）🔵-4: _DAY_MEM 256→1024 🔵-5: next_period_label（年跨ぎ・閏2月）・_bucket_completed 境界の単体テスト追加。
- **検証**: marketdata 146・MP api 229＋byte-parity 27・UI api 386・MP web 287・UI web 533/535 全緑（既知2件除く）。実UI（8139）: /candles・/forming_bar・1W zp 列の応答正常・30分ボタン追加動作・コンソールエラーなし。

## ISSUE-087 追補（🟡-3 完了・キャッシュ実削除実施・2026-07-15 依頼者承認）
- **sys.path 正規化**: venv site-packages への .pth 登録（tools/install_dev_paths.py・editable install は venv に setuptools 不在＋オフラインのため不採用＝site 標準機構で代替）。固有名パッケージ（marketdata/market_profile_api）を全プロセスで恒久解決し、ライブラリ 8 ファイル（marketdata 2・indicator_ui adapter/compute 5・MP dwell 1）から実行時 insert を撤去。entry（server.py・replay bridge）は自スライスの汎用名パッケージ（adapter 等＝スライス間で名前衝突するため .pth 不可）のみ結線し、.pth 未登録環境へのフォールバック（自己完結起動）を温存。検証: PYTHONPATH なし pytest 386 緑・env -i サーバー起動と /candles・/market_profile・/tf_period_profile 応答確認。
- **キャッシュ孤児削除**: tools/cache_gc.py --delete 実行（179MB・16 エントリ→孤児ゼロ確認）。バックアップ類（rollups_backup_utc20260714 等）は対象外のまま（禁止事項）。

## ISSUE-089: byte-parity golden が数時間で再赤化＝応答への実時刻依存の混入（レビュー診断の更正）
- **ステータス**: RESOLVED（2026-07-15・feature/issue-089-causal-leak・真因3層＋窓ローリングを実測特定し根絶）
- **実測**: ISSUE-088 で golden 再生成（27/27 緑）した数時間後、同一固定クエリ（to=1780666320 固定・過去窓）の dwell/m1/forming 6 件が再び不一致。同一プロセスで dwell profile の同一 price の tpo が 2431→1754 へ変化＝**過去窓固定でも応答が現在時刻/現在データに依存**している。
- **含意**: ①ISSUE-088 🟡-1 の「parquet データドリフト」診断は不完全（過去ティック不変は実証済み＝ドリフトでは説明不能）②golden 再生成では恒久解決しない ③to= は replay の as-seen-at-t 窓であり、現在データが過去時点の応答へ影響するなら**因果リーク（未来参照）の疑い**＝replay 検証の信頼性に関わる。
- **対処案（裁定待ち）**: (a) 依存経路の特定（レンジ導出・active table・正規化のどこが now を参照するか実測）→ 因果リークなら修正 (b) byte-parity は合成データ注入でデータ/時刻非依存化。

## ISSUE-089 対応記録（真因の実測特定と根絶・2026-07-15）
- **実測で特定した真因（4層・いずれも「データドリフト」ではない）**:
  1. **active table の先勝ちメモ**: `_active_table` が symbol のみキーでメモ化し、プロセス内で最初に触った要求の窓の活動表が以後の全要求に流用→境界日 partial・新規保存 npz へ**プロセス履歴依存**の値が焼き込まれていた。
  2. **表のリクエスト窓依存**: 日次ロールアップの表がリクエスト t1 アンカーのため、どの要求が最初にその日を計算したかでキャッシュ値が変わる（歴史日へ現在の活動地図を適用するアナクロニズムでもあった）。
  3. **新旧コード併走のファイル書き合い**: キャッシュパスに版数が無く、旧コード常駐サーバ（8139/8000）と新プロセスが同一 npz を v3⇄v4 で相互上書き（監視中に実発生を確認）。
  4. **1m 原子ストアのローリング保持**: `load_candles('jp225_tick')` の先頭（実測 t0≈2週間前）が壁時計と共に前進し、固定 `to` でも集計窓の左端が動いて全ビンが再分配される＝実データ依存の byte 固定は原理的に不能。
- **修正**: ①表メモを (symbol, 窓) キー化＋日次/部分ロールアップの表を「その日の属する月初アンカー（過去120日・因果・**日の純関数**）」で内部導出（回帰テスト付き）②dwell _CACHE_VERSION 3→4・**キャッシュパスへ版数を組み込み**（`<sym>/v4/g10/`＝世代間のファイル奪い合いを構造排除・旧サーバ併走も安全）③warm のスキップを存在チェック→版数/署名検証付きロードへ④byte-parity の jp225_tick 系 12 ケースを決定論の**合成世界**（tests/mp_parity_world.py・regen ツールと注入器共有）へ移行し golden 再生成。
- **as-seen-at-t への含意**: `to` 窓の右端（未来リーク）は健全（クランプ済み）。非再現性の実体は上記 1〜4＝修正後は「過去窓固定クエリ＝不変応答」（キャッシュ有無・要求順序・併走プロセスに非依存）。
- **検証**: dwell v4 全再構築（4,884 セッション/125s）。byte-parity 27/27 を3連続・フルスイート 258 を2連続で同一緑（旧: 実行毎に 4〜6 件が変動赤）。parity 実行 16s→0.6s。marketdata 146・UI api 386 緑。実UI: /market_profile dwell 正常応答（v4 値）。
- **注記**: 日次 dwell 値は表セマンティクス変更（月初アンカー）により v3 と微差（歴史日ほど妥当方向）。本番 8000 は旧パス（g10 直下）を読むため再起動まで従来どおり動作（干渉なし）。

## ISSUE-090: 週足・月足の日別プロファイル表示崩れ（旧 tf 列の残留・依頼者報告）
- **ステータス**: RESOLVED（2026-07-15・fix/tfp-stale-columns-on-tf-switch）
- **症状**: 週足/月足の日別プロファイルで、各週/月の位置に「週間隔÷7 幅の細い列」が描かれ本来の太い列にならない（依頼者スクリーンショット）。
- **実測原因**: tf-period 列の再取得/再描画が「可視レンジ変化イベント（デバウンス付き）」にのみ配線されており、**可視レンジが変わらない時間足切替**（週→日→週 等）ではイベントが発火せず、旧 tf の列（日次 n=1338 を実測）が新 tf のチャートへ残留。週足バーの合間に lwc の座標補間で日次列が並び「細い列」に見えていた（列幅=隣接列間隔の中央値×0.85＝日次間隔で算出されるため）。
- **修正**: controller の時間足オブザーバ（tradeMarkers と同じフック）から refreshTfPeriodNow() を明示発火（切替直後に ensure/再描画・非対応状態なら列消去）。配線回帰テスト追加。
- **検証**: UI web 534/536 緑。実UI（8139）: 再現手順（週→日→週）で切替後の列が週次等間隔・ホバー読取も週次（周期計 446,714 tick）・日次残留なしをスクリーンショット確認。月足も正常。

## ISSUE-091: 依存方向・DIP 徹底度のアーキテクチャ監査（4系統並列調査・全指摘 file:line 実証済み）
- **ステータス**: RESOLVED（2026-07-16・feature/issue-091-dip-tier1・Tier1（依頼者承認スコープ）対応完了。残件（Tier2・裁定要）は ISSUE-092 へ移管）
- **調査方法**: architecture-executor 4系統並列（simulator／indicator_ui／market_profile／marketdata+common 被依存全数）で import 文を実読。主要指摘 8 点はメイン会話で実コード再検証済み（憶測ゼロ）。prototype_* は対象外。
- **総括**: 依存規則（内側→外側 import 禁止）は全系統で遵守・循環なし・共有ライブラリ production の逆依存ゼロ。**DIP の徹底度は系統間で大きな格差**——simulator はポート内側所有＋合成ルート集約の模範構造、market_profile_api は境界インターフェース皆無で計算コアが I/O 具象直結。

### 🔴 高（DIP 不成立・依存方向違反）
1. **market_profile_api: 境界インターフェース全面欠落**。パッケージ全体（tests 除く）で ABC/Protocol/abstractmethod が grep 0 件（実測確認済み）。依存逆転が型契約として不成立で、差し替え・モック境界が不在。
2. **dwell/zp 計算コアが marketdata I/O 具象へ module-level 直結**。`compute/market_profile_dwell.py:53-54`・`compute/market_profile_zp.py:52-53` が `marketdata.paths`／`marketdata.tick_m1.day_parquet_files`（parquet 列挙・読取）を直接 import（`session_day` は純業務規則で許容）。ストレージ形式・配置変更が計算コアへ二重波及。対処案: `TickWindowPort`（Output Boundary）を compute 側に定義し parquet 実装を adapter へ隔離・entry point で注入。

### 🟡 中（境界欠落・責務混在・越境）
3. **simulator report_ui が主スライスの private シンボルを越境 import**。`report_ui/tools/export_report_payload.py:18` が `from simulator.main import _ema_series, build_interactor`（Composition Root＋非公開関数へ直結）。主スライスのリファクタで無警告破壊。`contacts_export.py:18` の公開 usecase 直 import（低）も併記。
4. **indicator_ui: データ層に Output Boundary 無し**。`adapter/compute/dataset.py:27`／`rollup_store.py:21`／`tail_reader.py:18` は marketdata 具象の別名（`sys.modules` 同一化＝`dataset.py:30`）で、抽象の縫い目ではない。usecase 層も不在で業務手順が `compute_controller.py:43-117` に収斂（Input Boundary 未形成）。なお内向き依存規則自体は py/js とも全域遵守・合成は server.py 集約で良好。
5. **market_profile_api: 永続化 I/O が compute/controller 内に同居**。`compute/market_profile_dwell_store.py:118`・`compute/market_profile_zp_store.py:80` の npz 原子書込が compute 層内、`controller/tf_period_profile_controller.py:105,121` に日次キャッシュ read/write 直書き（実測確認済み）。
6. **mp_stats 統計コアが simulator の adapter 具象へ直結**。`analysis/mp_stats/stats_core.py:23,27`・`step7_spa.py:37`・`step8_oos.py:27` が `simulator.adapter.validation`（HansenSpa/VarBacktests）を境界なしで import。結線も sys.path 副作用依存（`mp_stats/__init__.py:15-16`）。
7. **指標 src（ドメイン層）が I/O ローダ直結**。`indigators/profit_band/src/loader.py:21` が `marketdata.ohlc_csv_loader` を直接 import。

### 🔵 低（構造負債）
8. **marketdata tests の simulator 逆依存**。`marketdata/tests/test_ticksource_s2.py:159,256-257,277`（production は逆依存ゼロを実証済み）。単体 CI で collection error 要因＝契約テストは利用者側へ移設が対処案。
9. **common の計算/表示混載（SRP）**。純粋価格計算（applied_price）と表示定数（level_colors/LEVEL_LINE_WIDTH）が同一パッケージ。
10. **sys.path 実行時 insert の残存**（指標 src 多数・`server.py:38-49`・analysis 系）＝.pth 統一が未完で結線の真実源が二重。
11. **indicator_ui domain 層が配信経路から未配線**（`api/domain/__init__.py:6-15` 自認・production 参照ゼロ）＝空境界。controller の marketdata 直参照（`compute_controller.py:25`）と shim 経由（`candles_controller.py:12`)の経路不一致も併記。

### 模範例（正の参照・今後の基準）
- **simulator**: ポート内側所有（`usecase/ports.py:11`・`optimize_ports.py:9` 等）→ adapter 実装（`adapter/repository/ohlc_csv.py:25`）→ 合成は `main/__init__.py:27-51` 集約。domain/usecase の外側参照 0 件（実測確認済み）。replay_ui は主スライスから完全独立。
- **marketdata**: `port.py:31-60` の `CandleSource`/`TickSource`（runtime_checkable Protocol）を `simulator/adapter/repository/marketdata_source.py:21` がポート経由で消費＝DIP の模範消費例。

## ISSUE-091 追補（システム全体レベルの DIP 調査・2026-07-15）
- **調査範囲**: サブシステム内部（本体で調査済み）に対し、今回は①パッケージ間依存グラフと契約所有権②プロセス間・データ境界（HTTP 契約・共有ファイルストア・bridge・プラグイン）。2系統並列調査＋主要指摘 8 点をメイン会話で実コード再検証済み。
- **定量**: パッケージ間 production 代表エッジ 15 本中、抽象/契約経由 3（20%）・具象直結 12（80%）。被依存最多の marketdata への依存で安定抽象（port.py）経由は 1 エッジのみ＝**システムとして「安定抽象への依存」（SAP/SDP）は不成立**。
- **契約所有権の判定**: 上位方針が抽象を所有する DIP 正配置は simulator 系のみ——`usecase/ports.py:47` の自前 `MarketDataPort` を `adapter/repository/marketdata_source.py:58-68` が実装し `marketdata.CandleSource`（下位公開抽象）を DI 注入＋Candle→Bar 変換（ACL）で従属させる二重ポート構造（模範）。replay_ui も自前 `CausalCandlePort`/`CausalComputePort`＋gateway 隔離で consumer 側 DIP 成立。一方 MP/indicator_ui は下位具象へ従属し自前ポート不在。`marketdata/api_contract.py` は ERROR_STATUS（error→HTTP status 表）のみの下位所有共有カーネル（2 消費者・妥当）。

### 🔴 高（システム横断）
A1. **mp_stats（上位方針）→ simulator.adapter.validation の private 直結**。`analysis/mp_stats/stats_core.py:23-27` が `_pw_block_len`／`_stationary_bootstrap_indices`（private）・`norm_cdf` を、`step7_spa.py:37` が `HansenSpa` を直接 import（実測確認済み）。別アプリの adapter 層（不安定・偶有）に統計コアが側方依存＝安定度逆転・独立変更不能。対処案: 統計プリミティブを中立の共有核（common 配下等）へ抽出し双方が依存。
A2. **replay backend のエラー契約分岐**。`serve_replay.py` は `ERROR_STATUS` を import せず（grep 0 件・実測確認済み）、`internal` を **400** で返す（:184,328。正典 `api_contract.py` は internal→500）。全例外を `validation` へ丸め（:330）、ボディも `{error:{type,message}}` のみで正典の `ok/generation/violations` を欠く。同一エンドポイント（/compute・/market_profile）が経路により異なる契約を返す。対処案: nested error 整形を純関数として marketdata へ一元化し 3 殻（server.py・MP controller・serve_replay）が共用。

### 🟡 中
A3. **tf_period 非 zp 経路のキャッシュ世代が手書きリテラル**。`tf_period_profile_controller.py:181` は `s1` 直書き（zp 経路 :243 は `_ZP_CACHE_VERSION` 連動・実測確認済み）。アルゴリズム変更時の bump 忘れで ISSUE-089 と同型の「新旧コードが同一 JSON を相互上書き」（8000/replay 併走）が再発しうる。対処案: `_TFP_CACHE_VERSION` 定数新設・パス連動（dwell/zp と同規律）。
A4. **replay bridge が indicator_ui 内部モジュール構成へ踏み込み**。`_indicator_ui_bridge.py:45-67` が sys.path 挿入で `adapter.compute` 内部（`latest_dispatch.full_compute` 等）と MP controller 関数を直 import。replay_ui 自体は Port＋gateway で隔離済み（consumer 側は健全）だが、import 面が indicator_ui のパス規約・私的構成に密結合。対処案: indicator_ui 側に安定公開 Facade を定義し bridge はそれのみ import。
A5. **プラグイン契約の 4 面分散**。指標追加時に back `call_binding._TABLE`＋指標 src シグネチャ（inspect 実行時解決）＋front `catalog.js`＋`latest_meta.py` の同期が必要で、param 既定値の front/back 整合はテスト固定依存。対処案: param schema の単一情報源を `_TABLE` に置き front は取得（/catalog）へ寄せる。

### 🔵 低
A6. **HTTP schema の言語境界二重化**（JS/Python 各所ハードコード）＝構造的に不可避。ERROR_STATUS 一元化＋parity テストで現状許容（codegen 導入は現状オーバースペック）。
A7. **tools の private import**。`tools/gen_js_parity_golden.py:26` が `market_profile._value_area` を直接参照（実測確認済み）。公開 API 昇格か controller 経由へ。
A8. **`marketdata.port.TickSource` の抽象消費者ゼロ（YAGNI）**。本番で型注入する消費者 0・実装は Dukascopy 単一で tools は具象直呼び（実測確認済み）。MP のティック読取は port を迂回し tick_m1 直結（責務違いのため流用不可）。ingest enabler② の設計意図が現役なら条件付き維持、なければ削除候補。
A9. **MP dwell の Python/JS 二重実装**は golden parity テスト同期で担保（py_parity_golden.test.js / test_js_parity_golden_fresh.py）＝現状妥当な設計判断として記録のみ。

### 総括（システム全体の DIP 判定）
方向性（内→外 import 禁止・循環なし・共有ライブラリの逆依存ゼロ）は全域健全。しかし DIP＝「上位方針による抽象所有」が成立しているのは simulator 本体と replay_ui の consumer 側のみで、パッケージ間エッジの 80% が具象直結、プロセス間契約も ERROR_STATUS 以外は暗黙同期。**「安定への依存」は達成、「安定"抽象"への依存」は未達**が結論。優先対処は A1（安定度逆転）と A2（契約分岐）、次いで A3（ISSUE-089 同型リスクの残存）。

## ISSUE-091 対応記録（Tier1 実施・2026-07-16・依頼者承認スコープ）
- **実施内容**（全て挙動保存・公開名は互換 alias/再エクスポートで温存）:
  - **A1（🔴）**: 統計核（HansenSpa・PW ブロック長・定常ブート・VarBacktests・norm_cdf）を中立共有核 `common/stats_boot.py` へ抽出。simulator/adapter/validation は再エクスポート化・mp_stats の simulator import を全廃（private 越境の根絶）。レビューで develop 版との数値 exact 一致を実証。
  - **#1/#2（🔴）**: MP compute に `TickStorePort`（Protocol・compute 所有）＋ `gateway/marketdata_tick_store.py` を導入し、dwell/zp の marketdata I/O 直結（tick_m1/paths）を逆転。`day_parquet_files` はモジュール委譲関数としてテスト monkeypatch 経路を温存。回帰ガード（compute の I/O 具象 import 禁止）追加。session_day は純業務規則として許容を明文化。
  - **A2（🔴）**: `nested_error`（status 翻訳＋nested ボディの純関数）を `marketdata/api_contract.py` に単一定義し 3 殻（server.py・MP controller・serve_replay）が共用。serve_replay の旧 proto 由来独自形を正典準拠へ是正（internal→500・ValueError→validation・ok/generation/violations 付与）。フロントは `!response.ok` 分岐＋`error.type/message` 参照のため吸収（実証済み）。旧分岐を固定していたテスト 2 件を正典期待へ更新。
  - **A3（🟡）**: tf_period 非 zp ディスク世代を `_TFP_CACHE_VERSION` 定数連動へ（手書き `s1` 排除・v1=従来パスと byte 同一＝キャッシュ無効化なし）。版数連動の回帰テスト追加。
  - **#3（🟡）**: `simulator.main._ema_series` を公開 `ema_series` へ昇格（旧名 alias 温存）。report_ui の private 越境 import を解消。
  - **A7/#8（🔵）**: `value_area` 公開昇格（tools は公開 API 参照へ）／marketdata tests の simulator 依存 3 テストを `simulator/tests/unit/test_marketdata_tick_contract.py` へ移設（最下層の単体 CI collection error 要因を排除）。
- **検証**: スイート別実行（スライスの汎用名パッケージ衝突のため分離実行が正規手順）で marketdata 143・simulator 869・replay 146・report_ui 155・MP api 265（byte-parity 27 含む）・MP analysis 66・UI api 386 全緑。MP web 287 緑・UI web 534/536（既知 2 件のみ）。既存失敗 6 件（→ISSUE-093）は develop 基準でも同一＝本変更起因ゼロを worktree 比較で実証。
- **コードレビュー**: code-review-executor 承認（🔴/🟡 ゼロ・🔵 2 件は対応不要の記録: tick_store 遅延合成の理論上の初回競合＝無状態のため実害なし／エラー契約変更は意図的是正）。

## ISSUE-092: DIP 残件（ISSUE-091 Tier2・大規模リファクタ・裁定待ち）
- **ステータス**: RESOLVED（2026-07-16・全8項目実施完了＝裁定3項目は依頼者承認「全部実施」。7エージェント並列/直列実装・統合レビュー承認・詳細は下記対応記録）
- **Tier2（実装方針は確定・規模大のため分割実施を推奨）**:
  1. indicator_ui のデータ層 Output Boundary 導入（marketdata 具象の sys.modules 同一化 shim ＝ dataset/rollup_store/tail_reader を抽象の縫い目へ）・usecase 層の切り出し（handle_compute の Input Boundary 化）。
  2. replay bridge（_indicator_ui_bridge）の import 面を indicator_ui 安定公開 Facade 1 点へ縮約（latest_dispatch 等内部モジュール直結の解消）。
  3. プラグイン契約の単一情報源化（param schema を call_binding._TABLE に集約し front catalog.js は取得へ）。
  4. MP store 群（dwell/zp store）の compute→adapter 移設・tf_period controller のキャッシュ I/O 分離。
  5. sys.path 実行時 insert の残存一掃（common の .pth 登録・指標 src の insert 撤去）。
- **裁定待ち（削除・共有再編を伴うため依頼者判断が必要）**:
  6. common の計算/表示分割（applied_price 系 vs level_colors/LEVEL_LINE_WIDTH）。
  7. indicator_ui api/domain 層の削除可否（配信経路から未配線・production 参照ゼロ）。
  8. marketdata.port.TickSource の削除可否（抽象消費者ゼロ・ingest enabler② の設計意図が現役なら維持）。

## ISSUE-093: 既存テスト失敗 6 件（ISSUE-091 検証中に発見・本変更とは無関係）
- **ステータス**: RESOLVED（2026-07-16・fix/issue-093-stale-tests・全6件＝テスト陳腐化と実測確定・実装は正）
- **実測**: develop（ea1a8b5）基準の worktree でも同一に失敗＝以前から存在する failing tests。
  1. `common/tests/test_level_style.py::test_level_line_width_value_is_1`（1 件）: テストは `LEVEL_LINE_WIDTH == 1` を期待するが実体は 2（`common/level_style.py` の docstring は「視認性のため 2px に設定」と 2 を意図）。テストと定数のどちらが正か裁定要（実装意図は 2 が正に見える＝テスト陳腐化の疑い）。
  2. `indigators/indicator_ui/tools/tests/test_rollup_builder.py` の 5 件（stream_build 1D/1W/1M・incremental 1D・vectorized）: api を PYTHONPATH に載せた単独実行でも develop で同一失敗。rollup_builder と現行 resample 実装の乖離か、テスト前提の陳腐化かは未調査（原因調査から要着手）。
- **含意**: この 5+1 件は各スイートの一括集計に含まれない実行構成（tools/tests は 'adapter' 解決に api パスが必要）のため見逃されてきた可能性。CI の実行構成の明文化も対処案に含める。

## ISSUE-093 対応記録（真因の実測特定と修正・2026-07-16）
- **真因（全6件とも「実装は正・テストが陳腐化」を git 履歴と実測で確定）**:
  1. **level_style（1件）**: `LEVEL_LINE_WIDTH` は 2026-06-28 のコミット 5dabbdb（依頼者コミット・「視認性向上のため 1→2」と本文明記）で意図的に変更済み。テスト（==1 期待）の同時更新が漏れていた。→ テストを意図値 2 へ更新（テスト名も value_is_2 へ）。
  2. **rollup_builder（5件）**: コミット f0584f1（ISSUE-078 単位③）で rollup の規則源が `resample_ohlc_tf`（1D/1W/1M＝セッション日集計）へ移行した際、marketdata 側テスト（test_session_resample 等）のみ更新され、本ファイルの oracle（旧 plain `resample_ohlc`＋TIMEFRAME_RULES）が未追随＝1D/1W/1M の境界がセッション日 vs UTC 日でずれ恒常失敗（41 行 vs 40 行を実測）。→ 全 oracle を現行規則源 `marketdata.resample.resample_ohlc_tf` へ更新（規則の再実装なし・5m/1h は両規則が同値のため実質不変）。
- **見逃しの構造要因も是正**: tools/tests は `adapter` 解決に api パスが必要で**単独実行では collection error**＝失敗が集計に載らなかった。conftest.py に自スライス api ルートの結線を追加（server.py/bridge と同じ「自スライスのエントリが自分の root を結線する」規約）。単独実行 59 件・api 併走 445 件とも収集・実行可能になった。
- **検証**: common 19 緑・UI tools 単独 59 緑・UI api＋tools 併走 445 緑（いずれも実測）。修正はテスト・conftest のみ＝プロダクションコード変更ゼロ。

## ISSUE-092 対応記録（全8項目実施・2026-07-16・エージェント分担開発）
- **実施方式**: 依頼者指示「各自エージェントが担当して実装」に従い、programmer-executor 7 体で分担。並列判定（変更ファイル集合の非交差）により Wave1=6 体並列（worktree 隔離・専用ブランチ）・Wave2=1 体直列（③は server.py が①と交わるため①マージ後）。統合は親会話がブランチ毎に検証マージ。
- **実施内容**（各項目とも挙動保存・既存テスト無変更緑・新規ガードテスト付き）:
  - **①** indicator_ui に usecase 層新設（`compute_indicators` 純関数・`DatasetPort` Protocol・`adapter/gateway/marketdata_dataset`）。compute_controller は Controller+Presenter の薄殻へ縮退（公開名・monkeypatch 経路不変）。＋23 テスト。
  - **②** `adapter/compute/__init__.py` を安定公開 Facade 化（`full_compute`/`latest_compute` 明示再エクスポート・契約 docstring）。replay bridge の import 面を Facade 1 点へ縮約（`latest_dispatch` 直参照根絶・ガードテスト付き）。
  - **③** 指標 param 既定値を `catalog_schema.PARAM_DEFAULTS`（Python・_TABLE 19 指標）で単一情報源化し `GET /catalog` で配信。front はサーバ由来スキーマを overlay・フェッチ失敗時は静的値フォールバック（オフライン耐性・UI 実効値不変）。back/front 同期は golden JSON を双方向テストで固定。back 内の `_DEFAULT_SAMPLES` 二重定義も schema へ一本化。
  - **④** MP store 群（DwellRollupStore/ZpStore）を compute→gateway へ git mv（旧パスは identity 一致の互換シム）。tf_period controller の日次 JSON I/O を `gateway/tf_period_disk_cache` へ分離（monkeypatch 経路・パス・原子書込は不変）。byte-parity 27 緑 3 連続。
  - **⑤** 指標 src の repo根 sys.path.insert 27 件撤去（.pth 恒久解決へ）。sibling 指標解決の parents[2] 13 件と standalone フォールバック 1 件は理由付きで温存。
  - **⑥** common の表示系（level_colors/level_style）を新パッケージ `common_view` へ分離（SRP）。common は後方互換再エクスポート温存・production 消費者 20 ファイルを直参照へ更新。
  - **⑦** indicator_ui api/domain（未配線・production 参照ゼロを grep 三形式で再実証）と専用テスト 2 ファイルを削除（9 ファイル・1805 行）。frontend の web/js/domain は別物＝不変。
  - **⑧** `marketdata.port.TickSource` Protocol を削除（抽象消費者ゼロの YAGNI）。具象 DukascopyTickSource・CandleSource は温存。Protocol 存在系テスト 5 件削除・挙動テスト 7 件温存。
- **統合検証で検出・即修正した回帰 1 件**: `/tf_period_profile` の既定ディスク root が撤去済み `_mpd._paths` を参照し実 HTTP で 500（AttributeError）。既存テストが全て `_TFP_CACHE_ROOT` 注入で既定経路を通らず未検出だった。TickStorePort 経由へ是正＋既定経路の回帰テスト追加（fix/issue-092-tfp-default-root）。**実 UI・実 HTTP 検証の重要性を再確認**（スイート緑のみでは不十分・依頼者厳命どおり）。
- **検証**: スイート別最終実行で全緑 — marketdata 138・simulator 869・replay 148・report_ui 155・MP api 270（byte-parity 27 含む）・MP analysis 66・UI api+tools 397・common/common_view 22・MP web 287・UI web 543/545（既知 2 件のみ）。実UI（8141 起動・Playwright）: チャート正常描画・/catalog 200 取得・/compute・/candles・/market_profile・/tf_period_profile 応答正常・コンソールエラアなし（favicon 404 のみ）。
- **コードレビュー**: 統合レビュー（code-review-executor・完全深度）承認。🔴/🟡 ゼロ。🔵 3 件をバックログとして記録: (1) gateway 配置のスライス慣行差（ネスト vs フラット）を ADR に明文化 (2) 指標 src 単体実行は venv（.pth）前提である旨を開発ガイドへ明記 (3) catalog 同期テストに front 余剰 param の対称アサート追加。

## ISSUE-092 追補（統合レビュー 🔵 バックログ 3 件の対応・2026-07-16）
- **🔵-1/🔵-2**: `.doc/LAYERING_CONVENTIONS.md` を更新——gateway 配置慣行（indicator_ui=adapter/gateway ネスト・MP=gateway フラット・共通規律 3 点）と import 解決の前提（.pth 恒久解決・指標 src 単体実行は登録済み venv 前提・entry point のみフォールバック可）を明文化。あわせて ISSUE-092 で陳腐化した記述（usecase 欠落・sys.path 3 系統・依存規則の Facade 例外/catalog 権威）を現状へ整合。
- **🔵-3**: `catalog_schema_sync.test.js` に param 名集合の双方向対称アサートを追加（front 固有の余剰 param を検出。全 19 指標で対称成立を実測済み）。
- **検証**: UI web 544/546 緑（既知 2 件のみ）・同期テスト 2/2 緑。

## ISSUE-094: SRP（アクター単一性）のアーキテクチャ監査（4系統並列調査・主要指摘は実コード再検証済み）
- **ステータス**: RESOLVED（2026-07-16・全項目実施＝依頼者承認「🔴＋🟡＋🔵全部」。6 エージェント並列実装・統合レビュー条件付き承認→条件（規約文書反映）充足済み。残る裁定 1 件は ISSUE-095 へ。詳細は下記対応記録）
- **調査方法**: architecture-executor 4系統並列（simulator／indicator_ui／market_profile／共有層 marketdata+common+tools）。判定基準は「モジュールはただ一つのアクター（変更要求の主体）に責任を負う」——各サブシステムでアクターカタログを実コードから帰納し、複数アクターの変更が同一ファイルを取り合う箇所のみを違反と認定（規模・関数数だけでは違反としない）。重大指摘 6 点はメイン会話で実コード再検証済み。DIP 系・ISSUE-091/092 対処済み事項は除外。
- **総括**: 外周（EA=1ファイル・Presenter=1形式・Repository=1データ源・純 compute・gateway）はアクター単一性が徹底され模範的。**混在は各サブシステムの「中核の交差点」に集中**——実行エンジン（run_backtest）・巨大 compute（dwell）・controller の集計エンジン化（tf_period）・front の司令塔（indicator_controller.js）。また共有層に「同一アクター仕様の二重実装（手動同期）」という別型の SRP 破れが 5 件ある。

### 🔴 高（複数アクターの実証済み混在）
1. **simulator/usecase/run_backtest.py（924 行）: 5 アクター同居**。bar-mode 決定論仕様・実ティック実行仕様・ペンディング注文ライフサイクル（MT5 約定順）・マージン清算・セッション判定が単一 Interactor に凝集。`:157-160` で real_ticks と pending_lifecycle が同一メソッド `_execute_every_tick` へ合流し、OCO 取消（:799-803）等のペンディング変更が実ティック equity 経路を同時に危険に晒す（実測確認済み）。対処案: PendingLifecycleEngine・SessionGate の抽出、hedged_margin の Account 側メソッド化。
2. **market_profile dwell/zp/tf_period の中核 3 ファイル**。(a) `market_profile_dwell.py`: 集計数学＋キャッシュ運用＋因果＋データ供給＋セッション認識の 5 アクター同居——`_CACHE_VERSION=4` のコメント自体が「active table 窓キー化（セッション認識）の変更がキャッシュ版数 bump を強制した」実波及の記録（実測確認済み）。(b) `market_profile_zp.py`: 帰無分布生成（統計仕様）と配信整形（表示仕様）の同居＋同一統計定義が analysis/step5_null_b.py と二重実装（パリティテスト縛りを docstring 自認）。(c) `tf_period_profile_controller.py`（669 行）: controller 名目で z 統計を直計算（:300-311・実測確認済み）＋表示解像度＋LRU＋ライブ合成の 5 アクター収束。対処案: セッション認識の `session_activity.py` 抽出・帰無カーネルの api/analysis 共有一元化・`_day_columns_zp` 系の compute 層移送。
3. **共有層: 外れ値ポリシーが異なるアルゴリズムで二重実装**。同一アクター（データ品質・±30%・2025-08-26 JP225 不良値対策）の補正が、書込側 `cleaning.py:35`（median([o,h,l,c]) 基準）と読取側 `dataset.py:110-111`（min/max(open,close) エンベロープ基準）で**別の式**（実測確認済み）。閾値・基準変更時に片方修正漏れで書込時と読取時の外れ値定義が乖離する。対処案: `outlier_policy.py` へ単一化し両者は委譲へ。
4. **indicator_ui/web/js/adapter/front/indicator_controller.js（1058 行）: 4 アクター同居**。指標管理 UI に MP のスキーマ知識（`_mpParams`/`_deriveMode`/`_applyMarketProfile` 等 約250 行・実測確認済み）・時間足取得・ライブ再計算が凝集し、`restore()` が 3 アクター分岐の交差点。対処案: MP 委譲一式を MarketProfileActor 側へ抽出・TimeframeController 分離。

### 🟡 中（二重定義・漏出）
5. **report_ui/usecase/build_report_payload.py**: レポート表示形状＋IS/OOS 合否方法論（`_verdict` 閾値直書き）＋特定実験の所与（`_META_PARAMS`＝StopEntryProbe 固定・:27-29 実測確認済み）の 3 者同居。汎用ビルダが 1 実験に固着。対処案: AssessmentPolicy 分離・実験固有値の引数化。
6. **CSV スキーマの手動同期二重定義**: `tick_m1.py:42-44` と `rollup.py:51-52` に同一 `_HEADER`/`_DATE_FMT` リテラル（「一致させること」コメントで人手保証・実測確認済み）。対処案: csv_schema.py へ単一化。
7. **dataset.py に品質/供給/性能の 3 アクター集中**（クランプ・whitelist/JSON 整形・mtime キャッシュ/経路分岐）。「キャッシュには生を保存し返却時にクランプ」の不変条件が改修で破られやすい構造。
8. **serve_replay.py / server.py の殻に業務漏出**: replay 殻は API ルーティング＋静的配信セキュリティ＋性能直列化の同居、indicator_ui 殻は MP forming の tick 合成（`_augment_mp_forming_ticks`）とライブ注入が「薄殻」宣言に反して常駐。対処案: StaticFileServer 分離・augment の MP 側移設。
9. **datasetRef 台帳の 4 断片化**（DATASET_WHITELIST・_OUTLIER_CLAMP_REFS_SET・_ROLLUP_REFS・TICK_REFS）: 新銘柄追加時に 4 箇所整合が必要。対処案: ref 記述子レジストリへ統合。
10. **週/月ラベル規則の二重表現**（resample.py の W-FRI/ME ⇔ session_day.py の手書き暦算術・テスト担保依存）と **domain/account.py への執行クォート規約漏出**（`_eval_price` の bid/ask basis 分岐＝MT5 校正アクターが最内 Entity を変更する）。

### 🔵 低（記録・裁定向け）
11. **api_contract の再評価**: 依存方向は合法（ISSUE-091「低懸念」）だが、アクター観点では HTTP 契約の所有者は配信殻であり marketdata のどのアクターでもない。中立共有パッケージへの移設は境界衛生の改善候補（緊急性なし）。
12. その他: compute_stats の METRICS 版/MT5 校正版の並存・cache_gc の MP private 直結・`_min_unit` 孤児化・tools の `_DEFAULT_FULL_START` 二重定義・VA/活発秒の Python↔JS 二重実装（パリティ縛りは現実解として維持妥当）・common_view/level_colors docstring の旧パス例。

### 模範例（正の参照）
- **1 変更主体=1 ファイル**: simulator の EA（adapter/strategy/*）・Presenter（markdown/html/json）・Repository（データ源別）。
- **純カーネル**: tf_period_profile.py・market_profile.py（集計仕様のみ）・tail_reader.py・resample.py（「唯一の規則源」）・applied_price.py・compute_indicators.py（usecase 純関数）。
- **描画↔集計分離**: web/js の domain（dwell accumulator）と adapter/front（primitive）の物理分離。

## ISSUE-094 対応記録（全項目実施・2026-07-16・6 エージェント並列実装）
- **実施方式**: programmer-executor 6 体並列（worktree 隔離・変更ファイル集合の非交差で並列判定）。E1 実行エンジン／E2 MP 中核／E3 marketdata／E4 front／E5 report・stats／E6 殻＋api_contract。全項目とも挙動保存（決定論・MT5 golden parity・byte-parity 27・report.json byte 一致）を絶対条件とし、各エージェントが原子コミット単位で全緑を確認。
- **実施内容（アクター分離の新構造）**:
  - **E1（🔴-1/🟡-10b）**: run_backtest から `session_gate.py`（セッション判定）・`pending_lifecycle.py`（trigger/OCO/クォート規約）を抽出、hedged_margin を `Account.hedged_margin_level`（口座不変ルール）へ、執行クォート規約を `_execution.resolve_eval_quote`（usecase）へ移送し `Account._eval_price` を除去。887→896 緑・2 連続同一。部分残: SL/TP 決済ループ等は口座副作用と密結合のため温存（byte 優先）・`update_floating_pnl(bar)` の basis 分岐は既存テスト参照のため非推奨シムで温存。
  - **E2（🔴-2/🔵）**: dwell のセッション認識を `session_activity.py` へ、zp⇔step5 の帰無サロゲート核を `null_b_kernel.py` へ一元化（同一実体 import・パリティテスト無変更緑）、tf_period controller の集計エンジンを `tf_period_columns.py` へ移送（669→約410 行）、`cache_layout.current_layouts()` 公開契約で cache_gc の private 直結を解消、孤児 `_min_unit` 削除。byte-parity 27 緑 3 連続。部分残: moments/z/POC* の完全共有は ISSUE-079 の格子分岐（log vs 線形）により挙動差が出るためスキップ（サロゲート核のみ一元化）。
  - **E3（🔴-3/🟡-6,7,9,10a/🔵）**: 外れ値ポリシーを `outlier_policy.py` へ集約（閾値単一源）。**両式の実測**: 全 TF 走査で乖離 4 バー（jp225_tick 1h/4h の二相バー＝median 式の明白な誤検出・エンベロープ式は保全）→ 式統一は挙動変更を伴うため 2 戦略同居に留め**裁定事項として上申（→ISSUE-095）**。CSV スキーマ `csv_schema.py`・ref 台帳 `dataset_registry.py`・供給キャッシュ `serving_cache.py` を単一化/分離、週月ラベルは `resample.period_label_naive` へ単方向委譲（40 万点 byte 一致実測）。marketdata 138→162 緑。
  - **E4（🔴-4）**: indicator_controller.js 1058→809 行。`market_profile_params.js`（純関数）・`market_profile_controller.js`（MP 駆動）・`timeframe_controller.js`（時間足）へ抽出、セッション日 OHLC 集計を domain 純関数 `session_ohlc.js`（MP 側実体＋symlink 共有）へ。既存テスト 0 変更・新規 39 ケース。
  - **E5（🟡-5/🔵）**: `AssessmentPolicy`（IS/OOS 合否・閾値注入可）・`ReportMeta`（実験所与の引数化）で build_report_payload を EA 非依存の純写像へ。compute_stats を `metrics_spec.py`／`mt5_parity.py` へ参照仕様分離。**report.json 5,958,171 byte の cmp 完全一致を実証**。
  - **E6（🟡-8/🔵-11）**: `StaticFileServer` 抽出（トラバーサル防御保存を専用テストで固定）、MP forming の tick 合成を MP 側 `augment_forming_payload` へ移設（殻は buffer を渡すだけ）、HTTP 契約を中立 `api_shared/http_contract.py` へ移設（marketdata/api_contract は互換再エクスポート）。
- **インシデント記録**: 並列作業中に E5 が共有チェックアウトの HEAD を一時 detach し、親のマージ 3 件（E1/E6/E4）が detached HEAD 上に積まれ develop ref が未前進となった。系譜が直系だったため fast-forward で復旧（コミット消失なし）。教訓: worktree 隔離エージェントには「共有チェックアウトの HEAD 操作禁止」を明示すべき（次回のエージェント規約へ反映）。
- **検証**: 統合後の全スイート再実行で全緑 — simulator+report_ui 1092・replay 155・MP api+analysis 355（byte-parity 27 含む）・marketdata 162・UI api+tools 397・common 系 22・MP web 287・UI web 583/585（既知 2 件のみ）。実UI（8144・Playwright）: 週足切替（TimeframeController 経路）・market_profile 適用と描画（MarketProfileController 経路・POC*/VAH 線・凡例操作）正常・コンソールエラーなし。
- **統合レビュー**: code-review-executor（完全深度）条件付き承認→条件充足済み。🔴 ゼロ。🟡-1（LAYERING_CONVENTIONS へ api_shared 配置規約を反映）＝本コミットで対応。🔵 2 件（install_dev_paths docstring＝対応済み／session_ohlc symlink の CI 健全性チェック＝ISSUE-095 へ）。縫い目の数値/identity 同一性（rng 消費順・hedged margin 合算順・キャッシュ/契約オブジェクト同一性）はレビュー側で独立実測済み。

## ISSUE-095: ISSUE-094 残件（裁定 1 件＋バックログ）
- **ステータス**: RESOLVED（2026-07-16・全5項目対応完了。裁定1件は「エンベロープ式へ統一」で確定・実装済み。3エージェント並列＋メイン直接対応）
- **対応記録（全5項目・2026-07-16）**: 項目1【裁定＝エンベロープ統一】`outlier_policy.py` の median/envelope 2戦略同居を単一エンベロープコアへ一本化（median 撤去・取得/供給の両経路が同一コアへ委譲）。実測で二相バー4本（jp225_tick 1h/4h・2025-08-26 の 15k/42k 跨ぎ）のみ median が幻の中間値(28787.5 等)へ潰していたのを保全へ変更、それ以外は byte 不変（byte-parity 27 維持・median 固定テストを回帰ガードへ正当更新）。項目2 Account の basis 分岐を完全除去（mark_price 死コード削除・シム同値テスト2件[test_account/test_eval_quote_resolution]を本番経路検証へ再設計・hedged margin 同一サイド複数玉テストを変異試験付きで新設・本番コード diff ゼロ・golden 緑）。フィールド floating_pnl_basis/point_size は本番構築が使うため inert 化して残置（物理削除は別タスク申し送り）。項目3 A方式バンドルの DIM_ALPHA 二重宣言を解消（実 collision は pair_lines[0.15]/market_profile[0.30]＝ISSUE 記述の trade_markers は `_DIM_ALPHA` で既に非衝突。pair 側を共有定数 `PAIR_DIM_ALPHA` へ抽出）＋追跡下の out/prototype.html を再生成（node --check 構文OK）。項目4 analysis→api の CI PYTHONPATH 前提を LAYERING_CONVENTIONS.md へ明文化。項目5 web/js 単一ソース共有 symlink（16件）の健全性チェックテストを新設。検証: marketdata 173・MP api 296（byte-parity 27）・simulator 967×2・UI web 595/597（既知2件のみ）緑。
1. **【裁定】外れ値補正式の統一是非**: 現在は `outlier_policy.py` 内に 2 戦略同居（acquisition=median[o,h,l,c]式・serving=min/max(open,close) エンベロープ式）。実測乖離は jp225_tick 1h/4h の二相バー 4 本のみで、median 式が実在しない中間値へ潰す誤検出＝エンベロープ式が妥当に見えるが、median 式には「単一の不正 open/close も補正できる」利点があり優劣は入力空間全体では断定不能。統一する場合は既存テスト 2 系（各式を byte 固定）の更新＝挙動変更を伴うため依頼者裁定が必要。
2. E1 残: `Account.update_floating_pnl(bar)`/`mark_price` の basis 分岐完全除去（test_account.py の更新解禁が前提）。同一サイド複数玉 hedged margin の明示的統合テスト追加。
3. E4 残: `out/prototype.html`（A方式バンドル）の再生成＋既存 `DIM_ALPHA` 二重宣言（trade_markers_renderer/pair_lines_primitive 由来・HEAD 以前から）の是正。
4. E2 残: analysis→api の新結合（step5→null_b_kernel）が CI で PYTHONPATH に api を要する点の明文化（本番 venv は .pth で自動解決）。
5. レビュー 🔵: session_ohlc.js の symlink 健全性チェックの CI 化（symlink 非対応環境対策）。

## ISSUE-096: ライブ足更新中の時間足切替で "Cannot update oldest data" 過渡エラー（ブラウザ全UI動作確認で発見）
- **ステータス**: RESOLVED（2026-07-17・対処案②＝renderer 後退ガードで是正・実UI検証済み）
- **対応記録（2026-07-17）**: 対処案②を採用。`ChartRenderer.updateLastCandle` に**実系列末尾（_lastBar.time）基準の後退ガード**を追加（`candle.time < _lastBar.time` の stale live tick を skip・新周期 base へも混ぜない）。従来の後退ガードは player 内 `_bar` 基準で系列差替（setTimeframe→setCandles で _lastBar が新周期末尾へ更新）を捕捉できなかった。時刻は本境界で unix 秒 number のため数値比較で安全。同/新 time は従来どおり反映＝byte 不変。回帰テスト新設（backward time は series.update を呼ばない・同/新 time は呼ぶ）。web 597 緑・実UIで足切替 15 回連続でもコンソールエラー 0・描画正常を確認（過渡エラー再現せず）。
- **発見経緯**: ISSUE-094 完了後のブラウザ全UI動作確認（指標管理UI・replay・report を Playwright で全操作クリック）中に、indicator_ui（8145）で時間足を 30分へ切替えた瞬間にコンソールエラー 1 件を観測。
- **症状**: `Error: Cannot update oldest data, last time=... new time=...`（lightweight-charts の series.update が現系列末尾より古い time のバーで呼ばれた）。スタックは `ChartRenderer.updateLastCandle (chart_renderer.js:578)` ← `LiveTickPlayer._applyTick (live_tick_player.js:222)` ← `_playback (:134)`。
- **切り分け（実測）**: `live_tick_player.js` は ISSUE-094 で未変更（`git log 4b5731a..HEAD -- live_tick_player.js` 空）＝本 SRP リファクタの回帰ではない。E4 の setTimeframe 抽出（timeframe_controller）は byte 挙動不変で、ライブ player との協調（切替時の停止/再シード）は抽出前後で同一。既知のライブ tick 自己シード領域（ISSUE ~065 系・RESOLVED・当時ブラウザ目視は依頼者実施）に潜在していた競合が顕在化したもの。
- **推定原因**: 時間足切替（candles 再取得→メイン系列差替）と、独立ポーリングするライブ tick player（2.5s poll / 100ms playback）の間に停止/再シードの同期がなく、切替直後にインフライトの live tick が旧周期の `_bar.time` で `updateLastCandle` を呼び、差し替え後の新系列末尾より古い time となって lwc が拒否する。`_applyTick` の後退ガード（`periodSec < this._bar.time` で return）は player 内部の `_bar` を基準にするため、系列側が差し替わったケースを捕捉できない。
- **影響**: 過渡的（チャートは 30分足を正常描画・以降の操作も正常）。lwc は update を拒否するのみでクラッシュしない。他の時間足切替では未再現＝インフライト tick のタイミング依存。
- **対処案（裁定・別実装向け）**: setTimeframe のバッチ（recomputeDepth ガード区間）に入る時に live player を一時停止し、系列差替後に新時間足で再シードしてから再開する。または updateLastCandle 側で「系列末尾 time 未満のライブ足は skip（後退ガードを player の _bar でなく実系列末尾で判定）」する防御を追加。
- **検証記録（正常動作確認済みの操作）**: ①indicator_ui（8145）: 時間足9種・ライブ ON/OFF・メニュー4タブ・カテゴリ5種・検索・お気に入り登録・moving_averages 追加/歯車(パラメータ/スタイル/可視性)/期間変更 OK/表示トグル/削除・market_profile 追加/日別プロファイル切替/週↔日切替(ISSUE-090 回帰なし)/zp 描画(POC*/VAH 線) 全正常。②replay_ui（8146）: 1足送り/戻し・速度×0.25〜×1・bar スライダー・▶再生/⏸停止(bar 前進)・再生追従・表示左右端・レンジ4種・最新足更新モード・時間足・price_range_power 適用(バンド線描画) 全正常。③report_ui（8770）: IS/OOS 区間トグル・6タブ(比較判定/取引明細/ヒートマップ/グラフ/サマリー/用語)・接点ボタン・FAIL 判定バッジ(AssessmentPolicy)・3チャート(価格/Balance/Drawdown) 全正常。コンソールエラーは本 ISSUE-096 の 1 件と favicon 404 のみ（8144 への ERR_CONNECTION_REFUSED は検証中に停止した旧サーバへ旧タブがポーリングした残骸＝実欠陥でない）。

## ISSUE-097: OCP（オープン・クローズドの原則）のアーキテクチャ監査（4系統並列調査・実コード実測に基づく）
- **ステータス**: 一部 RESOLVED（2026-07-16 起票／🔴-1・🔴-2・🟡-3〜10・🔵-20 対応済み／🟡-11＝第2銘柄 YAGNI 据置・🔵群＝安定閉集合/YAGNI 据置）
- **対応記録（🔴-2・2026-07-17）**: tf_period_profile_controller の src 2値ディスパッチを記述子表 `_SRC_DESCRIPTORS`（src→{day_fn, bucket_fn, allowed_tfs, day/bucket unit フォールバック}）へ集約。handle_tf_period_profile 内 6 箇所に散在した src 分岐（src 検証・zp の tf ゲート・bucket_fn・bucket unit 既定・day_fn・day unit 既定）を表参照へ置換し、新ソース＝1エントリ追加で閉じる構造へ縮退。unit フォールバックは呼出時評価に合わせ callable 保持。MP api 296 緑（byte-parity 27 含む）＝応答 byte 不変。残 🟡-11（銘柄/instrument 分散）は稼働中の第2銘柄が存在せず YAGNI 上据置（実要求化まで先行実装しない）。
- **対応記録（🔴-1・2026-07-16）**: market_profile の src ディスパッチを `SourceDescriptor` 登録表（`_SOURCE_DESCRIPTORS`／`_SOURCE_REGISTRY`）へ集約し、`_ALLOWED_SRC`/`_ATOM`/`_SRC_METRIC` を表からの導出値（同一値・同一順序＝byte 不変）に、if 連鎖を handler 参照の table-driven dispatch に置換。新ソース追加＝表への1エントリのみへ縮退。byte-parity golden 27×3 緑・MP api 296・web 287 緑。回帰ガード（導出値一致・handler 解決）新設。feature/solid-r2-mp-source-descriptor をマージ。🔴-2（tf_period の src 分岐）は記述子形状が異なるため無理に同一表へ入れず別タスクへ申し送り。
- **対応記録（W2・🟡 一部・2026-07-16）**: 独立・安全・高価値の 🟡 を programmer-executor 並列で是正（挙動保存）。🟡-3=build_interactor の ea_name type-switch を `_EA_FACTORIES` レジストリ化（ProFitBand を1エントリで生成可能化・WeeklyVolBand 構築の二重化を共有ファクトリへ一元化）。🟡-4=serve_replay の例外→HTTP 分類を中央翻訳器へ集約し `/market_profile`・`/market_profile_forming` の ValueError→validation 欠落を是正（正典契約へ統一）。🟡-5=tick_model 許容値の三分散（config_loader Literal・_TICK_MODELS・real_ticks 別分岐）を `TICK_MODEL_REGISTRY` へ単一化。🟡-6/-7=latest_meta の per-indicator if 連鎖と call_binding.invoke の price_range_power identity 分岐を `_BindingSpec` の宣言フィールド/preprocess フックへ一元化。🟡-10=`_TF_BAR_SEC` 重複を marketdata.tf_meta.TF_BAR_SEC 参照へ。検証: simulator 962×2・replay 171・UI api+tools 430×2・MP api 296（byte-parity 27）・web 583/585 緑。
- **対応記録（W3・🟡-8/-9・🔵-20・2026-07-16）**: front の MP ソース能力述語（`=== 'zp'` 散在）を domain 単一定義の**能力記述子** `mp_source_capability.js`（`{id,label,selectable,incremental,hasPeriodWindow,supportedTfs,blockedSessionTfs,poc,showLabels,tfPeriodSrc}`）へ集約。market_profile_actor/catalog_entry/market_profile_primitive と indicator_ui の composition_root_front（ZP_TF_ALLOWED/zpTfOk/getSrc）を記述子参照へ統一し、production から `=== 'zp'`/`!== 'zp'` 述語が消滅（新ソース＝1エントリで閉じる）。全 tf での記述子↔旧リテラル一致を回帰固定。MP web 300・UI web 583/585 緑・挙動 byte 不変。未着手: 🟡-11（第2銘柄=YAGNI）／🔵群（安定閉集合・YAGNI）。A方式バンドル out/prototype.html は build.mjs 更新済み・生成物ゆえ再生成が必要（挙動不変）。
- **調査方法**: architecture-executor 4系統並列（①simulator 本体＋replay_ui/report_ui/simulator配下tools ②indigators/indicator_ui の api＋web/js ③indigators/market_profile の api＋analysis＋web/js ④共有層 marketdata/common/common_view＋リポジトリ直下tools）。評価軸＝新指標・新EA(戦略)・新datasetRef(銘柄)・新時間足・新Market Profileソース(dwell/zp以外)・新レポート出力形式・新ティックデータ源(Dukascopy以外)・新エラー種別/HTTP契約の追加が、既存のどのファイルの修正を強制するかを実測。OCP smell の主対象＝追加のたび編集を強要する if/elif・type-switch・==定数比較の分岐、抽象化点を欠いたハードコードのテーブル/レジストリ、追加のたび複数箇所の同期編集を要する構造。DIP監査(ISSUE-091/092)・SRP監査(ISSUE-094)で既に対処または記録済みの拡張点（module_loader/_TABLEプラグイン境界・catalog_schemaによる指標param単一情報源化・datasetRegistry統合・戦略/Presenter/Repositoryの1バリアント=1ファイル構造・null_bカーネル一元化・outlier_policy/csv_schema/serving_cache/resample委譲・api_shared/http_contract中立化）は既知として除外し新規発見に集中。prototype_* は対象外。
- **総括**: 🔴（既存挙動破壊を強制する複数箇所同期編集）は market_profile の src ディスパッチ 2 件に限定。simulator・indicator_ui・共有層は 🔴 該当なし＝Composition Root 型の DI 選択分岐や、稼働中の第2バリアントが実在しない箇所（YAGNI 上未実装が妥当）に留まる。共通するsmellのパターンは「①ハードコードされた許可集合＋実処理分岐の並行テーブル化（3個以上に分散）」「②値表の重複定義（同一値の2箇所以上での再宣言）」「③フロント側でのソース/指標名==比較の散在」の3型。

### 🔴 高（複数ファイル同期編集を実測で確定・稼働中バリアントで実証済み）
1. **market_profile_controller の src ディスパッチ（if-chain＋並行ハードコードテーブル3個）**: 許可集合 `_ALLOWED_SRC`（`indigators/market_profile/api/market_profile_api/controller/market_profile_controller.py:46`）・metric 表 `_SRC_METRIC`（:48）・atom 表 `_ATOM`（:50-55）・実処理 if 連鎖（:240 `if src_val in ("dwell","m1")`／:246 `if src_val == "zp"`／:252 else=candle）に分散ハードコード（実測確認済み）。3つ目のソース種別追加は同一ファイル内 4 箇所の同期編集＋`_handle_zp`（:358-414・約55行）相当の新規ハンドラ関数追加が強制される。対処案: `SourceDescriptor{id, atom, metric, allowed_refs, handler}` の登録表へ集約し、`_ALLOWED_SRC`/`_ATOM`/`_SRC_METRIC` はその表から導出、追加は表への1エントリ追加のみへ縮退。
2. **tf_period_profile_controller の src 2値ディスパッチ（三項演算子が3値を構造的に表現不能）**: `indigators/market_profile/api/market_profile_api/controller/tf_period_profile_controller.py:381`（`if src is not None and src != "zp"`）・:405（`_bucket_columns_zp if src == "zp" else _bucket_columns`）・:433（`_day_columns_zp if src == "zp" else _day_columns`）・:420/:445（unit フォールバック三項）・:71（`_ZP_TF_ALLOWED`）・:388（tf ゲート）で実測確認済み。3つ目のソースは二値三項では表現できず、6箇所の書き換え＋`_day_columns_foo`/`_bucket_columns_foo` 新規追加が必須。対処案: src→`{day_fn, bucket_fn, allowed_tfs, unit_fallback}` のディスパッチ表を導入し、追加を表エントリ1件へ縮退。

### 🟡 中（新バリアント追加のたび複数箇所への分岐追記・二重管理を強制）
3. **[simulator] build_interactor の ea_name type-switch**: `simulator/main/__init__.py:334-374` に `if ea_name == "MA_Slope_EA"` 以下5分岐（else=既定TC）。1分岐が「data repo・DataFrame ローダ・registry ビルダ・strategy」の4関心事を束ね、新EA追加は import 行(41-45)・専用registryビルダ関数・elif分岐の3箇所同期編集を要する。実証: `adapter/strategy/pro_fit_band.py:44` の `ProFitBand` が未登録のため生成不能（grep で生成箇所0件＝閉じた分岐の実例）。`WeeklyVolBand` は `main/__init__.py:363` と `tools/run_weekly_vol_band_cli.py:73` の2箇所で構築知識が二重化。対処案: `ea_name -> Callable[..., (strategy, registry, market_data)]` のファクトリレジストリ導入。
4. **[simulator] serve_replay の例外→HTTP契約分類の重複コピー**: `simulator/replay_ui/framework/serve_replay.py:193-196,209-212,232-233,252-253,268-273` の各ハンドラに `except ValueError→validation / except Exception→internal` の分類が個別コピーされ、/market_profile・/market_profile_forming で ValueError→validation 分岐が欠落（実測差異確認済み）。新エラー種別追加は最大5ブロックの同期編集を要し漏れが生じやすい。対処案: 例外→`(status, type)` 変換を中央翻訳関数へ集約。
5. **[simulator] tick_model 許容値の2ファイル分割**: `framework/config_loader.py:42-44`（`Literal["every_tick","ohlc_expand","open_only","real_ticks"]`）と `main/__init__.py:54-58`（`_TICK_MODELS` dict）が二重管理、さらに `real_ticks` は `_TICK_MODELS` dict には無く `main/__init__.py:399` の別分岐で処理（三分散の実例・実測確認済み）。新ティックモデル追加は config_loader と main のクロスファイル同期編集を要する。対処案: 単一レジストリ化し許容値をそこから導出。
6. **[indicator_ui] latest_meta の per-indicator if 連鎖**: `indigators/indicator_ui/api/.../latest_meta.py:53-63` に `if compute_id == "moving_averages"` / `if compute_id == "price_range_power"` のハードコード分岐。archetype 分類が call_binding._TABLE（catalog_schema で単一化済みの既定値とは別軸）と別ファイルに分散し、新指標へ latest 増分の恩恵を効かせるには本関数の編集が必須（未登録は安全既定 full+K=1 に落ちるため正しさは壊れない＝🔴でなく🟡）。対処案: archetype/min_window/trailing_k を `_BindingSpec` へ移設し一元宣言。
7. **[indicator_ui] call_binding.invoke 内の指標名特別扱い**: `call_binding.py:299` に `if self.compute_id == "price_range_power" and "interval" in kw:` という指標固有前処理が汎用 `invoke` 内へ直書き（実測確認済み）。別指標が同種前処理を要すると invoke 本体の if が増える。対処案: `_BindingSpec` に `preprocess` フックを追加し invoke から指標名判定を排除。
8. **[indicator_ui] Market Profile ソースの `=== 'zp'` 散在**: `composition_root_front.js:264`（`ZP_TF_ALLOWED`）・:267（`mpSrc() !== 'zp'`）・:398（`getSrc`）・:422（`zpTfOk()`）の3-4箇所に zp 固有の対応時間足・透過判定が散在（実測確認済み）。新MPソース導入は同ファイル複数箇所への `=== '<newsrc>'` 分岐追記を要する。対処案: ソース→{対応tf集合, tf-period透過可否} の能力テーブルを domain 側に配置。
9. **[market_profile] フロントのソース能力述語の3ファイル散在**: `market_profile_actor.js:315,649,737`（増分判定・期間窓・zp×sessions ブロック）・`catalog_entry.js:18,24,34,131,139-142,155`（`_MP_ZP_TF`・`MP_ZP_SESSIONS_BLOCKED_TFS`・enum・optionEnable・conditionalVisible）・`market_profile_primitive.js:501,507`（POC*/ラベル）に `=== 'zp'` 直書きが散在（実測確認済み）。新ソース追加は3ファイル約10箇所の同期編集を要する。対処案: `{id, label, incremental, hasPeriodWindow, supportedTfs, blockedSessionTfs, poc}` のソース能力記述子を単一定義し各ファイルは参照のみに統一。
10. **[market_profile] tf→秒テーブルの重複定義**: `market_profile_controller.py:57` の `_TF_BAR_SEC` が `marketdata/tf_meta.py:32` の `TF_BAR_SEC` と値がバイト同一の重複定義（controller が tf_meta を import せず自前コピーを保持・実測確認済み）。加えて tf 追加は `tf_period_profile_controller.py:51`（`_TF_SECONDS`）・:67（`_UNIT_BY_TF`）・:71（`_ZP_TF_ALLOWED`）・`catalog_entry.js:17-18` にも波及。対処案: `market_profile_controller._TF_BAR_SEC` を撤去し `tf_meta.TF_BAR_SEC` を唯一源として参照。
11. **[共有層] 銘柄/instrument のハードコード分散（2銘柄目のティック取得パイプライン追加時）**: `marketdata/dukascopy_source.py:24,103,150,185`（`JP225` 定数直書き）・`tick_m1.py:51-52`（`_DEFAULT_SYMBOL`/`_DEFAULT_REF`）・`tools/acquire_marketdata.py:40-42,308`（`SYMBOL`/`TICK_PARQUET_NAME`）・`tools/build_tick_rollup.py:49`・`tools/live_tick_watch.py:52,87-89` に symbol/instrument が分散し `dataset_registry.py:29-44` の `DatasetDescriptor` は symbol/instrument フィールドを持たない（実測確認済み）。2銘柄目実装時は5ファイル超の同期編集を要する。現状は稼働中の第2銘柄が存在しないため YAGNI 上 🔴 ではなく 🟡（実要求化まで先送り可）。対処案: 実要求化時に `DatasetDescriptor` へ symbol/vendor_instrument を追加し tools 側は registry 参照で解決。

### 🔵 低（変更頻度が低い・影響が局所的・YAGNI 上現状据置可）
12. **[simulator] _make_session_calendar の if 分岐**: `main/__init__.py:74-88`（`if session_calendar_key == "jp225"` else NullCalendar）。生成側1箇所に影響限局。対処案: calendar_key→実装のレジストリ化。
13. **[simulator] _present_outputs の出力形式ハードコード**: `main/__init__.py:459-461` が JsonPresenter＋MarkdownPresenter を固定生成。`adapter/presenter/html.py` の HtmlPresenter は未結線。実行時選択の要求が現状不在のため固定は YAGNI 上妥当（将来リスクとしてのみ記録）。
14. **[simulator] IntrabarWindowRepository の ref/symbol ハードコード**: `replay_ui/adapter/intrabar_window_repository.py:83`（`if ref == "jp225_tick"`）・:114（`"JP225_ticks.parquet"` パス固定）。1 repository=1データ源の模範構造内での軽微な逸脱。対処案: symbol/ファイル名の注入パラメータ化。
15. **[simulator] serve_replay の ref バイパス定数**: `replay_ui/framework/serve_replay.py:118`（`ref != "jp225_tick"` で validation を分岐スキップ）。新tick系ref追加で本条件の編集を要する。対処案: 「validationスキップ対象ref」の判定を is_known_ref 側の属性へ寄せる。
16. **[indicator_ui] indicator_compute_adapter の指標名集合**: `indicator_compute_adapter.py:21`（`_TIME_REQUIRED`）・:25（`_EMPTY_SERIES_INDICATORS`）が指標名をハードコードし front catalog の `timeRequired` と二重管理。対処案: `_BindingSpec` へ移設。
17. **[indicator_ui] _fitter_factory の type-switch**: `call_binding.py:150-155`（`if name == "ols"` / `if name == "tgp"` / else raise）。影響は1関数内に閉じる。対処案: `{name: factory}` の dict レジストリ化。
18. **[indicator_ui] 新時間足追加の front 散在**: `catalog.js:215-218,242`（`MA_TIMEFRAME_LABELS`・enumValues）・`composition_root_front.js:272,378,264`（`isTfPeriodTimeframe`・`windowSecForTf`・`ZP_TF_ALLOWED`）に tf 分類述語が分散（bar秒自体は tf_meta.js に単一化済み）。対処案: tf メタへ `bucketing`/`tfPeriodEligible` 属性を持たせ述語を一元化。
19. **[indicator_ui] front catalog.js の REGISTRY**: `catalog.js:397-402` は新指標ごとに const 定義＋配列1行追加で足りる加算的レジストリ（OCP上ほぼ許容範囲）。ただし IndicatorDef（系列名・placement・param UI）が back の src 実装と構造的に二重管理される点は残る（catalog_schema が単一化したのは既定値のみ）。
20. **[market_profile] primitive の zp 専用描画分岐**: `market_profile_primitive.js:501`（`src === 'zp' ? C_POC_STAR : C_POC_LINE`）・:507（zp のみラベル描画）。新ソースが zp 同様の特殊描画を要する場合のみ影響（指摘9の能力記述子に `poc`/`showLabels` を含めれば解消）。
21. **[共有層] TF_BAR_SEC が TIMEFRAME_RULES と別立ての並行テーブル**: 時間足キー集合の唯一源は `marketdata/resample.py:30-40` の `TIMEFRAME_RULES`（9キー）だが `marketdata/tf_meta.py:32-35` の `TF_BAR_SEC` が同じ9キーをリテラル再定義（`tests/test_tf_meta.py:42` が集合一致を assert し同期漏れはテストで検出される）。対処案: floor足のbar秒を `to_offset(rule)` から導出し、1W/1M のみ明示上書きすることで導出値化。
22. **[共有層] ティック raw スキーマ（bidPrice/askPrice）の直接結合**: `tick_m1.py:49`（`_TICK_COLUMNS`）・:79（`_ts_and_mid` が raw 列直接参照）・`dukascopy_source.py:168-218` が raw 列名のまま返す。ティック用ポート抽象は YAGNI で撤去済み（ISSUE-092⑧）につき現状1ベンダでは妥当。2つ目ベンダ実装時のみ「raw→正準列」正規化層の新設を要する。
23. **[共有層] session_day のカレンダー足 if 分岐**: `session_day.py:136,140`（`period_session_labels`）・:154,157（`next_period_label`）が1W/1Mを手書き暦算術のif/elifで分岐（`session_period_label` 自体は resample へ委譲済みだが本2関数は未委譲）。1W/1Mは事実上の閉集合のためYAGNI上許容。
24. **[共有層] applied_price のディスパッチャ if 連鎖**: `common/applied_price.py:134-150` が `AppliedPrice` 8種をif連鎖でtype-switch。MQL `ENUM_APPLIED_PRICE` を写す安定・閉じた集合で変更頻度は低い。対処案: `dict[AppliedPrice, Callable]` レジストリ化で分岐箇所のみ解消可。

### 模範例（OCP 遵守として機能している構造・正の参照）
- **HTTPエラー契約の単一情報源**: `api_shared/http_contract.py:15-22` の `ERROR_STATUS` dict＋`:33` `.get(error_type, 500)` フォールバック。新エラー種別は1行追加で拡張でき3殻（indicator_ui/market_profile/replay）は無改変（評価軸「新エラー種別/HTTP契約」はこの構造下では概ね良好）。
- **call_binding `_TABLE` + module_loader**: `(compute_id, variant) -> _BindingSpec` の宣言テーブル＋遅延loaderにより大半の指標追加が1レコード追加で閉じる（latest_meta/adapter指標名集合など周辺の未統合部分のみ🟡🔵として残存）。
- **causal_compute_gateway の委譲**: `simulator/replay_ui/adapter/causal_compute_gateway.py:37-52` が datasetRef/timeframe/指標/variant の妥当性を indicator_ui bridge へ委譲し、新datasetRef・新指標追加でsimulator側の編集ゼロ。
- **AssessmentPolicy の閾値注入**・**domain例外の1種別=1クラス**（simulator）、**dataset_registry統合**・**csv_schema単一化**・**時間足集合の反復導出**（`build_tick_rollup.py:67-71`等がTIMEFRAME_RULESから導出し並行テーブルを作らない）（共有層）は、いずれも新バリアント追加が1箇所の追記で閉じるレジストリ/委譲構造として機能。
- **front のエラー種別素通し**: `compute_error.js`/`compute_http_client.js:66-69` は `switch(error.type)` を持たず新エラー種別追加がfront分岐を強制しない。

### 裁定不要・実装フェーズへの申し送り
上記🔴2件・🟡9件・🔵12件は分離方針案を併記済み。着手時は各件を1バリアント=1ファイル/1レジストリへの委譲としてSRP監査(ISSUE-094)と同様の並列実装体制（変更ファイル集合の非交差判定によるworktree隔離並列）を推奨。11番（銘柄/instrument分散）は稼働中の第2バリアントが存在しないためYAGNI上、実要求化まで先行実装しないことを推奨。
## ISSUE-098: LSP（リスコフの置換原則）徹底監査（4系統並列調査・全指摘 file:line 実証済み）
- **ステータス**: 一部 RESOLVED（2026-07-16 起票／🔴-1 対応済み・残りは OPEN）
- **対応記録（🔴-1・2026-07-16）**: `ReportPresenterPort`（3メソッド1 Port）を形式別の単一メソッド Port（`MarkdownReportPort`/`HtmlReportPort`/`JsonReportPort`）へ分割し、各 Presenter を自形式 Port の単独実装へ変更、NotImplementedError スタブ基底 `_BasePresenter` を撤去（LSP 不成立を解消）。集約 `ReportPresenterPort` は参照元非破壊のため後方互換 ABC として温存。形式専用 Interactor 3 種を追加（ISP 是正）。本番 `main/__init__.py:459-460` は具象直接呼びで不変・出力 byte 不変。壊れた契約を固定していたテスト（test_usecase_ports.py:84-87／test_presenters.py の subtype アサート6行）を新契約へ再設計更新（出力検証は無変更）。simulator 937×2 緑。feature/solid-r1-presenter-port-split をマージ。ISSUE-099🟡-1（同一の ISP 指摘）も本対応で解消。
- **対応記録（W2・🟡 一部・2026-07-16）**: 🟡-2（MaSlope）＝`on_new_bar` 経路の NotImplementedError（SL/TP≠0 の暗黙事前条件）を撤去し、`on_init` で SL/TP>0 を検出し `ConfigError` で起動前に明示拒否＝fail-fast 化（LSP 是正）。正常系（SL/TP=0）は byte 不変・兄弟戦略は無変更。🟡-5（profit_band 例外多重）＝素 ValueError の二意味翻訳を adapter 層の profit_band 専用境界＋`_VALUE_ERROR_TRANSLATORS` レジストリへ隔離し、汎用計算経路から指標名＋日本語メッセージ片依存を除去（ISSUE-097🟡-5 と同一対応）。🟡-6（call_binding identity 分岐）は ISSUE-097🟡-7 で対応済み。simulator 962×2・UI 430×2 緑。
- **対応記録（W3・🟡-3/-4・2026-07-16）**: CandleSource 契約の非対称を是正。実測で「本番/テストの全 OHLC ファイルに重複 time ゼロ＝潜在的差分」を確認した上で、CSV を Dukascopy と対称に time 一意化（後勝ち）し `port.py` の Protocol docstring に事後条件（time 厳密昇順・一意／不在は空list／不正 time は ValueError）を明文化。実データは全て time 一意のため一意化は no-op＝出力 byte 不変（SHA256 一致で実証）。🟡-4 は両実装が既に malformed→ValueError で対称のため契約明文化のみ（never-firing な翻訳器追加は YAGNI ゆえ不採用＝証拠ベース）。marketdata 170×2・simulator 953×2 緑。残り🔵群は未着手（YAGNI/安定閉集合）。
- **調査方法**: architecture-executor 4系統並列（①simulator本体＋replay_ui/report_ui/tools ②indicator_ui api+web/js ③market_profile api+analysis+web/js ④共有層 marketdata/common/common_view+リポジトリ直下 tools）。判定基準は「基底型（ABC/Protocol/ダックタイピング契約）を使うコードは、そのサブタイプ・実装に差し替えても正しさを壊されないか」。prototype_* は対象外。DIP監査（ISSUE-091/092）・SRP監査（ISSUE-094）で既知の依存方向違反・アクター混在・境界インターフェース欠如は重複指摘しない（純粋に置換可能性のみを評価）。各系統エージェントは着手前に上流前提（依頼が想定する「共通基底の存在」等）を実コードで検証し、前提が実在しない箇所（market_profile の Store/controller/検定コアには実は共通基底が存在しない等）は「LSP 対象外」として正しく除外した。
- **総括**: 全4系統中、**明確な LSP 不成立（🔴）は simulator 系統に 1 件**（`ReportPresenterPort` の 3 メソッド契約を各 Presenter サブクラスが 1/3 しか履行せず、残り 2 メソッドは基底 `_BasePresenter` の `NotImplementedError` スタブへ落ちる）。他 3 系統（indicator_ui／market_profile／共有層）は 🔴 ゼロで、いずれも「契約に明記されない暗黙の振る舞い」を巡る 🟡 中程度の非対称（事前条件強化・事後条件/例外契約の兄弟間非対称）に留まる。simulator 系統の `ResultSinkPort`（3メソッドPort＋差分のみoverride）や marketdata の `CandleSource` Protocol（構築時パラメータ隔離）は模範的な置換可能設計として記録。

### 🔴 高（LSP 不成立・置換すると壊れる）
1. **`ReportPresenterPort`（simulator）: 3メソッド契約を各実装が 1/3 のみ履行**。契約定義は `simulator/usecase/ports.py:161-174` で `present_markdown`/`present_html`/`present_json` の3つすべてが `@abc.abstractmethod`（`simulator/tests/unit/test_usecase_ports.py:84-87` が3メソッド必須を固定テストで担保）。基底 `_BasePresenter`（`simulator/adapter/presenter/_base.py:20-27`）は3メソッドすべてを `raise NotImplementedError` で実装するスタブ。`MarkdownPresenter`（`markdown.py:51`）は `present_markdown` のみ・`JsonPresenter`（`json.py:20`）は `present_json` のみ・`HtmlPresenter`（`html.py:54`）は `present_html` のみを override し、残り2メソッドは基底の NotImplementedError を継承する。破綻シナリオ: 消費者 `GenerateReportInteractor`（`simulator/usecase/generate_report.py:18-28`）は `ReportPresenterPort` 1つを受け取り3経路すべてを委譲する設計のため、`MarkdownPresenter` を注入して `generate_html()` を呼ぶと `NotImplementedError` が送出されクラッシュする。緩和状況（実測）: 本番 `simulator/main/__init__.py:459-460` は当該 Interactor を経由せず各形式に一致する具象メソッドを直接呼ぶため現状は未発火＝潜在的・構造的違反であり稼働中バグではない。対処案: ISP に沿って `ReportPresenterPort` を形式別の単一メソッド Port（`MarkdownPresenterPort`/`HtmlPresenterPort`/`JsonPresenterPort`）へ分割し、各 Presenter は自 Port のみ実装する（`ResultSinkPort` 階層＝`simulator/adapter/repository/result_sink.py:33-69` と同形が模範）。分割までの暫定策は `GenerateReportInteractor` を形式別3 Interactor へ分離し注入も形式専用にする。

### 🟡 中（契約の暗黙依存・事前条件強化・事後条件/例外の兄弟間非対称）
2. **`MaSlope`（simulator）: `StrategyPort.on_new_bar` に暗黙の事前条件（SL/TP=0）を強制**。契約（`simulator/usecase/ports.py:104-106`）は config に関する制限を宣言していないが、`ma_slope.py:92-93` は `cfg["stop_loss_points"]!=0` または `take_profit_points!=0` のとき `_stop_level_unsupported()`（`:104-108` で `NotImplementedError`）を送出。兄弟戦略（`ma_slope_pending.py`・`stop_entry_probe.py`）は同一 config で正常動作するため `MaSlope` だけ置換不能。対処案: `on_init` 時点で SL/TP>0 を検出し起動前にドメイン例外で明示拒否する（`on_new_bar` 戻り値経路での NotImplementedError を撤去）、または SL/TP>0 を正式サポートし契約を完全充足させる。
3. **`CandleSource`（marketdata）: 重複 time の事後条件が Dukascopy/CSV 間で非対称**。Dukascopy 実装（`dukascopy_source.py:82,89,97`）は `time` をキーに `dict` 格納し同一 time は後勝ちで一意化（docstring `:77` に明記）。CSV 実装（`csv_source.py:41-66`）は範囲内の全行を append し time でソートのみで**重複排除なし**。Protocol 契約（`marketdata/port.py:35-37`）は「time 昇順・データなしは空list」のみを規定し一意性を明記しないため、Dukascopy 側の追加保証（time 一意）に暗黙依存する利用側コードを CSV 実装へ差し替えると重複バーの二重計上・index 重複エラーを起こしうる。対処案: `CandleSource` の契約に重複time の扱いを明記し両実装を一致させる。
4. **`CandleSource`（marketdata）: 不正データ時の例外送出条件が非対称**。CSV（`csv_source.py:49-52`）は `time` 列が非 epoch のとき `ValueError` を fail-fast 送出。Dukascopy（`dukascopy_source.py:158-165`）はデータ内容に対して例外を送出せず空listで応答。Protocol docstring（`port.py:35`）は「例外を投げない全域関数」を示唆する記述のみで例外契約が未定義のため、Dukascopy 前提で書かれた無例外前提の利用側を CSV に差し替えると `ValueError`/`FileNotFoundError` が未捕捉伝播しうる。対処案: `CandleSource` の契約に送出しうる例外型を明記し、Dukascopy 側も不正データ検出時に同一例外型へ翻訳する。
5. **`profit_band`（indicator_ui）: 素の `ValueError` を2意味に多重定義し、adapter が identity+メッセージ文字列照合で分岐**。`indicator_compute_adapter.py:25`（`_EMPTY_SERIES_INDICATORS`）・`:42`（メッセージ文字列 `"バケット"` 照合）・`:61-70`（`_translate_value_error`）・`:99-101`（`KeyError` の compute_id 別振り分け）。他の指標プラグインが「型で識別可能な例外」という暗黙契約に沿う一方、`profit_band` だけ「必須バケット空」と「normalize不正」を同一の素 `ValueError` で投げるため、汎用計算経路が一様に扱えず特定プラグイン名＋日本語メッセージ片への依存が生じている。対処案: adapter層に `profit_band` 専用の翻訳境界を1箇所に閉じ込め、メッセージ照合をそこへ隔離する。
6. **`call_binding.invoke`（indicator_ui）: 汎用呼出経路内で `price_range_power` のみ identity 分岐**。`call_binding.py:299` — `if self.compute_id == "price_range_power" and "interval" in kw:` により、他指標には適用されない interval 自動適応が汎用パス内に温存され「`_TABLE` の全エントリは一様に置換可能」という前提が破られる。対処案: interval 適応を `_BindingSpec` のテーブル駆動フック（`pre_invoke` 等）に昇格し compute_id 直書き分岐を除去する。

### 🔵 低（将来リスク・軽微な非対称・テスト衛生）
7. **Composition Root の具象 `isinstance` 分岐**（`simulator/main/__init__.py:383`）: `MarketDataPort` 型変数を `CsvOHLCRepository` で具象判定し差し替え。Composition Root は具象を知ってよい層のため LSP 違反ではないが将来の結線複雑化リスクとして記録。
8. **`run_weekly_segments`（simulator）: 注入ポート戻り値を `isinstance` で正規化**（`run_weekly_segments.py:175-193`）。戻り型が Protocol化されず `WeeklySegmentOutcome | BacktestStats | None` を許容するため型で契約が保証されない。対処案: 戻り値契約を単一型に固定するか Protocol化。
9. **`FakeChart` ファミリ（indicator_ui）のダックタイピング契約が部分充足**（`fake_chart.py:104-113`）。`FakeHorizontalChart` は `legend()`/`create_line`/`create_histogram` を持たない。本番経路は単一 `FakeChart` に統一済みで現状不活性だが、将来 `legend()` を呼ぶ指標が誤って注入されると `AttributeError`。
10. **`DukascopyTickSource`（marketdata）の基底喪失**: `TickSource` Protocol は ISSUE-092 で撤去済みのため単独具象化（`dukascopy_source.py:168-218`）。将来再抽象化時に契約再定義が必要（現状は比較対象サブタイプなしで LSP 対象外）。
11. **`_FakeStore`（market_profile テスト）の部分実装**（`test_tf_period_profile_controller.py:159-161`）: `TickStorePort`（3メソッド）のうち `data_dir()` のみ実装。現テストは安全だが他経路へ流用すると `AttributeError`。対処案: 3メソッド完備の共有フェイク（`test_tick_store_port.py:35`）へ寄せる。
12. **Store 群（market_profile）の将来共通化リスク**: `DwellRollupStore`（`load_day_rollup`）と `ZpStore`（`load_mgrid`/`load_null`）はメソッド名不一致・別 identity の `CACHE_MISS` 番兵（`dwell_rollup_store.py:52`／`zp_store.py:35`）を独立保持。現状は無問題だが将来共通基底へ昇格する際は番兵とメソッド名規約の統一が先決。
13. **`ReplayMarketProfileActor`（simulator/replay_ui）の事後条件条件付き弱化**: 基底 `MarketProfileActor.refresh()`（`market_profile_actor.js:724`）は描画副作用を必ず起こす契約だが、サブクラス（`replay_market_profile_actor.js:80,100`）は growing-push 初回フレームで無描画。present/replay は composition root で型固定され多態差し替え箇所が無いため現状非破綻（設計コメント `:19-21` が意図を明記）。基底 JSDoc への契約明記のみ推奨。

### 模範例（正の参照・今後の基準）
- **`ResultSinkPort` 階層**（`simulator/adapter/repository/result_sink.py:33-69`）: 共通実装2メソッド＋差分1メソッドのみ abstract という「H-1 と同形の3メソッドPort」でありながら全実装が契約を完全充足。H-1 の是正先例として直接転用可能。
- **`StrategyPort.on_tick`／`SessionCalendarPort`（simulator）**: 既定 no-op／NullCalendar を基底が正当な契約として提供し非対応サブクラスは継承のみで安全。
- **`TickStorePort`↔`MarketdataTickStore`（market_profile）**・**`DatasetPort`↔`MarketdataDatasetGateway`（indicator_ui）**: Protocol の全メソッドをシグネチャ完全一致で実装、`runtime_checkable` の isinstance も実証済み。
- **`PairPrimitiveBase`↔各 Primitive 実装**（indicator_ui／market_profile 共通基底）: `_draw(target)` のみが override 対象の Template Method 正準形。
- **`CandleSource` Protocol の呼出面設計**（marketdata）: ベンダ固有パラメータを構築時に隔離し `fetch_candles(start,end)` の引数を両実装で完全一致させることで事前条件強化を構造的に防止（M-3/M-4 の非対称は契約未記載の暗黙振る舞いのみに起因）。
- **ドメイン例外階層**（`simulator/domain/exceptions.py:24-93`）: 全引数任意キーワード化＋サブクラスが `__init__` を override せず継承、事前条件強化なし。

### 残存スコープ（次調査への申し送り）
- `ReplayIndicatorController`（`simulator/replay_ui/web/js/adapter/front/replay_indicator_controller.js`）の基底 override 契約整合は indicator_ui 担当の範囲外のため未検証。
## ISSUE-099: ISP（インターフェース分離の原則）徹底度のアーキテクチャ監査（4系統並列調査・全指摘 file:line 実証済み）
- **ステータス**: 一部 RESOLVED（2026-07-16 起票／🟡-1 対応済み・残りは OPEN）
- **対応記録（🟡-1・2026-07-16）**: `ReportPresenterPort` の形式別 Port 分割で対応済み（ISSUE-098🔴-1 と同一の収束点・feature/solid-r1-presenter-port-split をマージ）。太った3メソッド Port を単一メソッド Port 3種へ分割し未使用メソッドへの型依存を排除、`_BasePresenter` の NotImplementedError スタブを撤去。simulator 937×2 緑・出力 byte 不変。
- **対応記録（W2・🟡-2・2026-07-16）**: `VolBandRepositoryPort`（save/save_all/get/all_week_ids の4メソッド）を `VolBandWriterPort`（save_all）／`VolBandReaderPort`（get）へ read/write ロール分離。書込クライアント estimate_weekly_band は Writer・読取クライアント run_weekly_segments は Reader を受ける形へ。production 呼び出し0件の save 単体・all_week_ids は削除（旧 all_week_ids 契約固定テストは同カテゴリで削除）。具象は両ロール統合実装のまま充足。simulator 962×2 緑。
- **対応記録（W3・🟡-3/-4/-5・2026-07-16）**: 🟡-3/-4=front controller の host 全体依存を**加法的**に是正——`indicator_controller.js` に `TimeframeHost`（11面）/`MarketProfileHost`（19面・実測で18→19に補正）のロール契約を @typedef＋凍結記述オブジェクトで明文化し、timeframe/market_profile controller がそのロール契約に依存する形へ宣言変更。共有基盤 IndicatorController は無改変で構造的に契約充足（symlink 継承先 ReplayIndicatorController も無改変で充足＝双方向テストで固定）。差分はコメント/JSDoc のみで実行時変更ゼロ。🟡-5=`MarketProfileHistogramPrimitive` の god interface を ISP ロールファサード（`ProfileSink`／`TfPeriodSink`）へ分離し各 actor へ必要面のみ注入（attach 点は単一維持）。UI web 591/593・MP web 300 緑・挙動 byte 不変。残り🔵は未着手（YAGNI）。
- **調査方法**: architecture-executor 4系統並列（①simulator本体+replay_ui/report_ui/tools ②indigators/indicator_ui の api+web/js ③indigators/market_profile の api+analysis+web/js ④共有層 marketdata/common/common_view+リポジトリ直下 tools）。評価基準は「Protocol/ABC/クラスの公開面がクライアントの実利用に対して大きすぎないか」＝ISP固有の切り口とし、既知の DIP（ISSUE-091/092）・SRP（ISSUE-094）指摘とは非重複であることを各エージェントが upstream-input-validation で個別実証済み。prototype_* は対象外。実ディレクトリ名は `indigators/`（指示文中の `indicators` 表記の実体）。
- **総括**: 🔴高の ISP 違反は**系統横断でゼロ**（4系統とも太った Protocol/ABC の未実装メソッド強制は限定的または不在）。🟡中5件・🔵低4件を検出。paternが2種に集約: (a) バックエンド Port が read/write 等の異ロールを1つに束ね一部クライアントが未使用メソッドへ型依存する、(b) フロントエンド front/adapter が「host オブジェクト丸ごと」を受け取り必要面はごく一部という広依存パターン（indicator_ui/market_profile 双方で共通・共有ベースの symlink 制約により即時分割は困難）。

### 🟡 中
1. **simulator: `ReportPresenterPort` が3メソッドを1つの Port に束ね、各実装が使わない2メソッドを `NotImplementedError` で埋める**。定義 `simulator/usecase/ports.py:161-174`（`present_markdown`/`present_html`/`present_json`）。`_BasePresenter`（`simulator/adapter/presenter/_base.py:20-27`）が3メソッド全てを NotImplementedError 既定にし、`MarkdownPresenter`（`markdown.py:48-58`）は `present_markdown` のみ、`HtmlPresenter`（`html.py:51-67`）は `present_html` のみ、`JsonPresenter`（`json.py:17-26`）は `present_json` のみ実装。実クライアント `simulator/main/__init__.py:459-460` は各具象を1メソッドのみ呼ぶ。3メソッド全てを要求する `GenerateReportInteractor`（`usecase/generate_report.py:18-28`）は production 未結線（main 参照0件・testsのみ）。対処案: `MarkdownReportPort`/`HtmlReportPort`/`JsonReportPort` への分割・`_BasePresenter` の NotImplementedError スタブ廃止。
2. **simulator: `VolBandRepositoryPort` が read/write ロール未分離**。定義 `simulator/usecase/vol_band_ports.py:29-33`（`save`/`save_all`/`get`/`all_week_ids` の4メソッド）。書込専用クライアント `estimate_weekly_band.py:125` は `save_all` のみ、読取専用クライアント `run_weekly_segments.py:156` は `get` のみ使用（実測）。`save`単体・`all_week_ids` は production 呼び出し0件（tests/自己呼び出しのみ）。対処案: `VolBandWriterPort`（save_all）と `VolBandReaderPort`（get）へロール分離、未使用の `save`/`all_week_ids` は削除。
3. **indicator_ui web/js: `TimeframeController` が host（`IndicatorController`）の広い内部面に依存**。利用側 `web/js/adapter/front/timeframe_controller.js:15-76` が host の11メンバー（`_timeframe`/`_recomputeDepth`/`_loadCandles`/`_datasetRef`/`_renderer.setCandles`/`recomputeAllApplied`/`_state.uiState`/`_persistAll`/`_timeframeObserver`/`_el.timeframeBtns`/`_recentBars`）のみ使用するのに対し、`IndicatorController`（`indicator_controller.js:38-799`）の公開面は約40メソッド＋20超フィールド。ロール専用の狭い `TimeframeHost` ポートが未定義。共有ベース `IndicatorController` は `replay_ui` へ symlink 単一ソースで継承される制約（`timeframe_controller.js:6-11` に明記）があり即時分割は困難なため🟡に抑制。対処案: 時間足ロール専用の最小契約（getter/setter: timeframe・recomputeDepth・datasetRef・recentBars、method: loadCandles・setCandles・recomputeAllApplied・persistUiState・notifyTimeframeObserver・syncButtonsEl）を `TimeframeHost` として抽出。
4. **indicator_ui web/js: `MarketProfileController` も同型の host 全体参照（指摘3よりさらに広い18メンバー）**。`web/js/adapter/front/market_profile_controller.js:24-247` が `_marketProfile`/`_mpParams`/`_mpModeResolver`/`_mpGrowthResolver`/`_state`/`_isMarketProfile`/`_catalog`/`_paramsObject`/`_meta`/`_persistAll`/`_renderLegend`/`_defaultVariant`/`_datasetRef`/`_document`/`_withParams`/`_defaultParams`/`_mode`/`_untilTime` の18メンバーに依存。同一の symlink 単一ソース制約（`market_profile_controller.js:7-13`）により🟡に抑制。対処案: `MarketProfileHost` ロールインターフェースの抽出（指摘3と一括で共有ベース設計見直しの候補）。
5. **market_profile web/js: `MarketProfileHistogramPrimitive` が2つの無関係クライアント役割を1クラスへ束ねる（god interface）**。クラス定義 `web/js/adapter/front/market_profile_primitive.js:74`、公開面 `setSessions:98`/`setTfPeriods:105`/`tfPeriodLevelAt:117`/`setSnapshot:170`/`setProfile:176`/`setCursorTime:183`/`setVisible:189`。`MarketProfileActor` は `setProfile`/`setVisible`/`setSnapshot`/`setSessions`/`setCursorTime` のみ使用し `setTfPeriods`/`tfPeriodLevelAt` は0回、逆に `TfPeriodProfileActor`/`TfPeriodTooltip` は `setTfPeriods`/`tfPeriodLevelAt` のみで他5メソッドは0回（実測・排他的サブセット）。単一 `ISeriesPrimitive` の attach 点は1つで `_draw`（primitive.js:430）が `_tfPeriods` 有無で描画モードを排他分岐する構造的制約あり。対処案: 同一 primitive の上にロール別ファサード（`ProfileSink`={setProfile,setVisible,setSnapshot,setSessions,setCursorTime} と `TfPeriodSink`={setTfPeriods,tfPeriodLevelAt}）を定義し各 actor には必要ファサードのみ注入（attach は単一のまま役割依存だけ分離）。

### 🔵 低
6. **indicator_ui web/js: MarketProfile actor の optional-method 群を `typeof` で逐次防御（部分実装インターフェースの兆候）**。`market_profile_controller.js:57,73,90,92,136,159,187-190,243` で `typeof host._marketProfile.X === 'function'` ガードが `applyGrowthState`/`onLiveTick`/`enterBar`/`isGrowingPush`/`isEnabled`/`detach`/`refresh` に対し多用。コメント（同ファイル L238-240）に「present の full actor は `onLiveTick` を持つが replay の slim actor は持たない」と明記＝実装体ごとにメソッド部分集合が異なる緩い契約。actor 実装体自体（present/replay 双方）は当該調査の担当範囲外のため stub 有無は未検証、暫定分割案として必須コア（setParams/setEnabled/refresh）と present 固有拡張（onLiveTick/enterBar/isGrowingPush/applyGrowthState）へのロール分離を記録に留める。
7. **market_profile api: `TickStorePort` Protocol にクライアントが利用しないメソッドへの型依存**。定義 `api/compute/tick_store_port.py:19`（`day_files:22`/`read_ticks:26`/`data_dir:30` の3メソッド）。`tf_period_profile_controller.py:104` は `data_dir` のみ（1/3）、`market_profile_zp.py:68,261` は `day_files`/`data_dir`（2/3・`read_ticks` 不使用）、`market_profile_dwell.py:73,104,167` のみ3メソッド全使用（実測）。唯一の実装 `MarketdataTickStore` は単一・読み取り専用で凝集しており複数実装の実在もないため（YAGNI）分割は必須でなく記録に留める。分割案（参考）: `DataRootProvider`(data_dir) と `TickReader`(day_files, read_ticks) へのロール分離。
8. **market_profile web/js: `MarketProfileActor` が renderer ホストの広い面に依存（確度低・範囲外）**。`web/js/adapter/front/market_profile_actor.js` の renderer 呼び出し6種（`setCandleTrim:179,557`/`setRightMarginFraction:457`/`setSessionMP:489,743`/`setCandleTransparency:493,715,745,824`/`focusTimeRange:515,801`/`setUserInteraction:566`）に対し `TfPeriodProfileActor` は `setCandleTransparency` 1種のみ使用（実測）。ただし renderer（ChartRenderer）の全公開面定義は本調査の担当範囲外（indicator_ui/composition root 側）で全面突合は未実施、かつ全呼び出しが `typeof` ガード付き duck-typing のため「未実装メソッドの強制」という ISP の要件は不成立。確定違反ではなく依存幅の観測として記録。
9. **共有層: `marketdata/dataset.py` の再エクスポート facade が3関心事（検証/供給/resample規則）を単一公開面に混載**。`dataset.py:41`（`DATASET_WHITELIST`）/`:54`（`OUTLIER_CLAMP_THRESHOLD`）/`:81-85`（`resample_ohlc`/`TIMEFRAME_RULES`/`is_known_timeframe` 再エクスポート）/`:96`（`is_known`）/`:144`（`load_dataframe`）/`:184`（`load_candles`）。検証のみ利用のクライアント `simulator/replay_ui/main/composition_root.py:76`（`is_known` のみ）、resample規則のみ利用の `indigators/indicator_ui/tools/export_jp225_m1.py:47-48`（`resample_ohlc`）、供給+検証利用の `market_profile_controller.py:211,215,254` と互いに素な部分集合利用（実測）。resample規則の狭い一次ポート `marketdata/resample.py` が既に独立して存在し（`dataset.py:78-85` のコメントが規則源の分離を明記）狭い代替経路を選択可能なため強制依存ではなく🔵に留める。分割案（参考・非推奨実施）: resample専用クライアントは `from marketdata import resample` の狭い経路へ寄せる（dataset.py 側の再エクスポートは後方互換 shim として温存・削除は非推奨）。

### 総合判定
🔴高は0件。🟡中5件（うち3・4は共有ベースの symlink 制約により即時分割困難・1・2・5は独立実装のため分割可能）・🔵低4件（うち8は範囲外につき確定違反ではなく観測）。全指摘は分割方針案（設計判断）の提示に留め、実装（Port/インターフェース改変）は破壊的変更・既存契約改変を伴うため承認後の別タスクとする。
## ISSUE-100: SRP（単一責任＝アクター単一性）第2巡アーキテクチャ監査（全指摘 file:line 実証・自己レビュー済み）
- **ステータス**: RESOLVED（2026-07-16 起票／🟡-1・🔵-2 を 2026-07-17 是正）
- **対応記録（🟡-1・2026-07-17）**: MT5 執行クォート規約 Ask=Bid+spread×point の三重＋αインライン化を単一プリミティブ `_execution.mt5_bid_ask(base, *, spread, point)->(bid, ask)` へ集約。derive_quotes（base=open）・resolve_eval_quote（base=close）・pending_lifecycle.tick_quote（base=tick 価格・import 委譲）＋成行 entry_price(:35) の各サイトを委譲（演算順 spread*point 保持で byte 一致）。MT5 parity/execution/pending/eval_quote＋実ティック/stop_probe/ma_slope 突合の 78 件緑（byte 不変）。規約再校正時に 1 箇所修正で全経路へ反映される（従来の同期ハザード解消）。
- **対応記録（🔵-2・2026-07-17）**: build_report_payload._contract_notes の "SL200/TP500pts" 直書きを実行時 ea_params["sl_points"/"tp_points"]（trades.sl/tp 導出と同一源）からの動的埋め込みへ変更（別 EA 再利用時の契約ノート陳腐化を解消・「EA 非依存の純写像」主張と整合）。現行 run は 200/500 で byte 不変。report_ui 171 緑（オラクル/byte-parity 含む）。
- **調査方法**: architecture-executor によるシステム全体（simulator 本体＋replay_ui/report_ui/tools・indicator_ui api+web/js・market_profile api+analysis+web/js・共有層 marketdata/common/common_view/api_shared＋直下 tools）の第2巡監査。事前に ISSUE-091/092/094/095/097/098/099 の既知指摘・対応記録を精読し重複を除外、**是正後の現行コードに対する新規発見・是正の副作用・取りこぼし**のみを認定。判定基準は第1巡（ISSUE-094）と同一＝「複数アクターの変更要求が同一ファイルを取り合う箇所のみ違反（規模・行数だけでは違反としない）」。全指摘は prompt-validation-workflow（Pre-mortem）＋upstream-input-validation による自己レビューを通過（一次候補 6 件を重複/非違反/是正済として棄却）。prototype_* は対象外。
- **総括**: 第1巡是正は概ね堅実で外周のアクター単一性は維持。新規は 2 件のみ——いずれも第1巡 🔴 中核（run_backtest／build_report_payload）の是正過程で**新設または見落とされた同一アクター多重表現**。両件とも局所的・挙動保存（byte 不変）で是正可能。

### 🟡 中（是正の副作用・執行/PnL 経路の同期ハザード）
1. **MT5 クォート規約（Ask=Bid+spread×point）が実行/評価の 3 関数へ三重インライン化**（アクター＝MT5 校正）。`simulator/usecase/_execution.py:70`（bar-mode 約定クォート・base=open）・`:89`（含み損益評価クォート・base=close）・`pending_lifecycle.py:40`（every-tick クォート・base=tick price）、補助的に `_execution.py:35`。第1巡 🟡-10 の E1 是正が `Account._eval_price` から規約を除去した際、移送先で 3 つ目の実体（`pending_lifecycle.tick_quote`）を新設し規約が 3 箇所に散在（コード自身が `pending_lifecycle.py:38`「inline 版と同一」・`run_backtest.py:784`「derive_quotes と対称に」と同一性を自認・束ねる単一プリミティブ不在は Grep 実証）。MT5 スプレッド規約の再校正時に 3 関数の手動同期が必要で、1 箇所漏れると bar-mode・floating・every-tick の各経路で執行/評価価格が乖離する。対処案: `mt5_bid_ask(base, *, spread, point)` の単一プリミティブへ委譲（演算順を保てば byte-identical・golden parity で回帰固定）。
2. （🔵 低）**汎用レポートビルダに StopEntryProbe 実験固有値（SL200/TP500）が残存ハードコード**（E5 是正の取りこぼし）。`simulator/report_ui/usecase/build_report_payload.py:321` の `_contract_notes()` 内に「SL200/TP500pts」が直書きされ、同一値は `report_meta.py:30` で既に ReportMeta アクターがパラメータ化済み（重複表現）。E5 対応記録の「EA 非依存の純写像」という主張と現行コードが不整合。別 EA（異なる SL/TP）で再利用すると `_contract_notes[2]` のみ SL200/TP500 のまま出力され契約ノートが静かに陳腐化（`report_presenter.py:54` 経由の出力到達を実証・現行単一実験では無害）。対処案: SL/TP 値を含むノート行を ReportMeta 注入へ移す。

### 模範例（SRP 遵守の正の参照）
- `simulator/adapter/controller.py:25`（BacktestController＝単一入口アクター）／`assessment_policy.py`＋`report_meta.py`（合否方法論と実験所与の独立アクター分離）／`tf_period_columns.py`（キャッシュ非依存の純集計へ分離・controller は薄いキャッシュラッパーへ縮退）／共有層 `csv_schema`・`outlier_policy`（095 でエンベロープ式一本化）・`dataset_registry`・`tf_meta`（手動同期二重定義の解消済み）。

### 自己レビュー記録（要点）
- Pre-mortem F1「M-1 は base が正当に異なる 3 関数の過大認定では」→ base（open/close/tick）の差異は正当だが ask 側 `+ spread * point_size` は 3 者同一かつコメントで同一性自認＝規約部分の三重表現として成立（base 引数化で吸収可能）。
- Pre-mortem F2「既往と重複では」→ 094 🟡-10 は `Account._eval_price`（E1 で除去済み）対象・`tick_quote` は E1 新設で記述外。094 🟡-5 は `_META_PARAMS` 対象で `_contract_notes` を含まず。重複なし。
- 棄却 6 件: run_backtest 実行モデル併存（=094 🔴-1 residual 明記済み）／dwell 集計+キャッシュ併存（=094 🔴-2 residual）／zp 帰無分布+整形同居（同）／tf_period_columns の横断 import（単一アクター内部依存＝非違反）／MP src ディスパッチ（=097 🔴-1 対応済み・OCP 軸）／共有層二重定義（是正済み）。
- 残存スコープ: JS 側（market_profile_controller.js/timeframe_controller.js）の同一アクター二重実装の網羅検証は symlink 単一ソース制約下（ISSUE-099 🟡-3/-4 記録範囲）のため後続調査へ委譲。

### 裁定不要・実装フェーズへの申し送り
🟡-1 を優先（執行/PnL 経路の同期ハザード・byte-identical 委譲で是正可）。🔵-2 は ReportMeta 注入化のみ。いずれも破壊的変更・既存契約改変を伴わない委譲リファクタであり、実装は承認後の別タスクとする。
## ISSUE-101: OCP（オープン・クローズドの原則）第2巡アーキテクチャ監査（是正7系統の実コード再検証＋新規探索・自己レビュー済み）
- **ステータス**: RESOLVED（2026-07-16 起票／🔵-1 を 2026-07-17 是正・唯一の新規指摘）
- **対応記録（🔵-1・2026-07-17）**: optimize/walk_forward CLI の目的関数・探索アルゴリズム許容集合の三重宣言を解消。`simulator/usecase/optimize_strategies.py` に唯一の登録表 `OBJECTIVE_REGISTRY`（pf/net/sharpe/recovery→Objective）と `SEARCH_ALGOS`（grid/random）を新設し、`optimize_cli._build_objective_port` のインライン dict と両 CLI の argparse `choices` をすべて本表から導出（`list(OBJECTIVE_REGISTRY)`／`list(SEARCH_ALGOS)`）。新目的関数追加＝表への 1 エントリで両 CLI に閉じる。両パーサの choices が従来と同一集合・同一順序（pf/net/sharpe/recovery・grid/random）であることを実測固定。simulator 該当テスト 52 件緑（CLI パース含む）。
- **調査方法**: architecture-executor によるシステム全体の第2巡監査。ISSUE-097 の既知指摘（🔴2・🟡9・🔵12）と対応記録を精読して重複を除外し、(a) 是正済み 7 系統（SourceDescriptor／_EA_FACTORIES／例外中央翻訳／TICK_MODEL_REGISTRY／_BindingSpec latest_meta+preprocess／mp_source_capability.js）が本当に「1エントリ追加で閉じる」かの実コード再検証、(b) 第1巡が対象外だった領域の新規探索、を実施。全指摘・全検証は file:line 実証、prompt-validation-workflow＋upstream-input-validation の自己レビューを通過（一次候補 7 件を重複/YAGNI/言語跨ぎ不可避/正当な純増として棄却）。prototype_* は対象外。
- **総括**: **合格**。是正 7 系統はすべて「新バリアント追加＝表への 1 エントリ（＋handler/factory の純増）で閉じる」構造に到達しており、取り漏らし・是正が導入した新 smell はゼロ。新規発見は第1巡が調査対象に含めなかった最適化系ツール CLI の値表重複 1 件（🔵）のみ。

### 🔵 低（新規発見 1 件）
1. **[simulator/tools] 最適化目的関数・探索アルゴリズムの許容集合が 2 CLI＋1 ディスパッチに三重宣言**。単一情報源は `simulator/tools/optimize_cli.py:69-82` の dict レジストリ `{"pf","net","sharpe","recovery"}→Objective`（正当）だが、argparse `choices` が `optimize_cli.py:195` と `walk_forward_cli.py:193` に並行リテラル化（walk_forward_cli.py:23 が `_build_objective_port` を import 共有しているのに choices だけ再宣言）。同型で search_algo も `optimize_cli.py:62`（if/else）・`:191`・`walk_forward_cli.py:189` に分散。新目的関数（例 calmar）追加時に 2 つの choices リストの同期編集を怠ると片方の CLI で拒否される（ISSUE-097 総括 smell パターン②「値表の重複定義」に該当）。🔵 根拠: 中核ディスパッチはレジストリ化済み・安定閉集合・research ツール 2 本に局所化。対処案: `OBJECTIVE_REGISTRY`/`SEARCH_ALGOS` を usecase 側で単一定義し、両 CLI の `choices=` を `list(OBJECTIVE_REGISTRY)` から導出。

### 是正済み 7 系統の再検証結果（すべて成立・正の参照）
- **SourceDescriptor**（`market_profile_controller.py:76-89,498-521`）: `_SOURCE_DESCRIPTORS` が唯一源・`_ALLOWED_SRC`/`_SRC_METRIC`/`_ATOM` は内包表記で導出・src 分岐は `_SOURCE_REGISTRY[src].handler` の table-driven dispatch。旧 if 連鎖消滅。
- **_EA_FACTORIES**（`main/__init__.py:388-394,476-477`）: dict＋既定フォールバック。EA 許容集合の並行テーブルなし（`__main__.py` に choices なし）。`run_weekly_vol_band_cli.py:74` も共有ファクトリ利用で二重構築解消。
- **例外中央翻訳**（`serve_replay.py:56-73`）: `_error_response` 単一定義・全 5 ハンドラが委譲・ValueError→validation 欠落是正済み。
- **TICK_MODEL_REGISTRY**（`tick_model_registry.py:56-80`）: 4 モデル単一表・`config_loader.py:46` は `Literal[TICK_MODEL_IDS]` で導出参照・`requires_real_ticks` フラグで分岐導出（三分散解消・別リテラル残存なし）。
- **_BindingSpec**（`call_binding.py:210-227,323-336,371-373`）: latest_meta/preprocess 宣言フィールド化・`latest_meta.py:52-56` は if 連鎖なし・invoke から compute_id 直判定排除。
- **mp_source_capability.js**: `_DESCRIPTORS`（:45-70）唯一源・production の `=== 'zp'`/`!== 'zp'` 述語は grep 0 件（コメント/テストのみ）・POC 星/ラベルも記述子集約。

### 自己レビュー記録（棄却 7 件の要点）
front `_ZP_SUPPORTED_TFS` と back `_ZP_TF_ALLOWED` の同値並行（言語跨ぎ不可避＋既往 🔴-2/🟡-8/-9 記録済み）／`_MP_PLAYER_TF` 9-tf リテラル（=🔵-18/-21 既録）／call_binding `output_kind` 残置フィールド（追加を強制しないデッドメタ＝OCP 非該当）／`_EXIT_MAP`（複製なし・語彙は trade_record._EXIT_REASONS に単一化済み）／`_build_search_port` の 2 値 if/else（Composition Root 正当・choices 重複のみ 🔵-1 へ内包）／`_dispatch_dwell` の dwell/m1 共有（新 handler 追加＝OCP 遵守の純増）／config_loader の spread_model 等 Literal（安定閉集合・YAGNI）。
- 残存リスク: 共有層（common/common_view/api_shared/marketdata）の未記録 smell 網羅探索は第1巡網羅済みの前提で本巡の重点外。ISSUE-097 🔴-2（tf_period src ディスパッチ）・🟡-11（第2銘柄 YAGNI）は OPEN のまま既往管轄。

### 裁定不要・実装フェーズへの申し送り
新規 🔵-1 は任意対応（レジストリ単一化のみ・挙動不変）。優先度は既往 ISSUE-097 🔴-2 の残件対応が上位。
## ISSUE-102: LSP（リスコフの置換原則）第2巡アーキテクチャ監査（是正5件の実コード再検証＋残存スコープ検証・自己レビュー済み）
- **ステータス**: 一部 RESOLVED（2026-07-16 起票／🟡-1・🔵-2 を 2026-07-17 是正・🔵-3 は記録のみ据置）
- **対応記録（🟡-1・🔵-2・2026-07-17）**: 🟡-1＝`CandleSource` の volume 事後条件非対称を是正。`marketdata/csv_source.py:61` を `pd.isna` ガードで Dukascopy（`dukascopy_source.py:88`）と対称化し、volume 列在・セル NaN の欠損を 0.0 補填（列不在のみ 0.0 だった非対称を解消）。**実データ実測**: 実 OHLC CSV 全 34 ファイルで volume NaN 0 件を確認→本ガードは実データ上 no-op（byte 不変）。契約テスト新設（CSV セル NaN／Dukascopy NaN とも volume=0.0 を固定）。🔵-2＝`marketdata/port.py` の `CandleSource` 契約 docstring に「volume は常に有限・欠損は 0.0」と「永続実体不在時の実装固有 I/O 例外（CSV=FileNotFoundError／構成不整合の即時失敗）」を明記し例外契約の網羅欠落を補完。marketdata 175 件緑。🔵-3（ReplayIndicatorController 戻り値 widening＝非破綻）は記録のみで対応不要。
- **調査方法**: architecture-executor によるシステム全体の第2巡監査。ISSUE-098 の既知指摘（🔴1・🟡5・🔵7）と是正記録（🔴-1 Presenter 分割／🟡-2 MaSlope fail-fast／🟡-3/-4 CandleSource 契約明文化／🟡-5 profit_band 隔離／🟡-6 call_binding フック化）を精読して重複を除外し、是正の実コード再検証＋第1巡「残存スコープ」に申し送られた `ReplayIndicatorController` の必須検証を実施。全指摘は file:line 実証、prompt-validation-workflow（Pre-mortem）＋upstream-input-validation の自己レビューを通過（一次候補 4 件を棄却）。prototype_* は対象外。
- **総括**: 第1巡是正はいずれも現行コードで正しく成立し、NotImplementedError スタブ・部分履行の再導入なし。ただし **CandleSource の是正明文化に取り漏らし 1 件**（volume の NaN 事後条件非対称＝🟡）と契約記述の例外型網羅欠落 1 件（🔵）を検出。必須検証対象 `ReplayIndicatorController` は基底 override 契約を全て充足（LSP 遵守・非破綻）。

### 🟡 中（新規・是正明文化の取り漏らし）
1. **`CandleSource` の `Candle.volume` 事後条件が Dukascopy/CSV 間で非対称**。契約 `marketdata/port.py:19-21` は「抽出元が値を持たない場合は 0.0 で補う」を全実装対称の事後条件として文書化。Dukascopy 実装は充足（`dukascopy_source.py:88` — `v = 0.0 if pd.isna(raw_v) else float(raw_v)`）だが、CSV 実装（`csv_source.py:61`）は volume **列は存在するがセルが NaN** の場合に `pd.isna` ガードがなく `volume: nan` を返す（列不在時のみ 0.0）。「volume は常に有限 float」に依存する利用側（Candle→Bar 写像・volume 集計）を CSV 実装へ差し替えると NaN が下流へ伝播（Dukascopy では起きない）。第1巡 🟡-3/-4 の W3 是正は time の順序・一意性・ValueError のみ対称化しており volume 補填は未対応＝取り漏らし。実データでの manifestation（実 CSV に NaN セルが実在するか）は**未実測・未検証**（LSP 判定は文書化済み事後条件の非対称の存在で成立）。対処案: `csv_source.py:61` を `pd.isna` ガードで Dukascopy と対称化し、NaN volume ケースの契約テストを `test_candlesource_contract.py` に固定。

### 🔵 低
2. **`CandleSource` 契約明文化が `FileNotFoundError` を網羅せず例外契約が非対称のまま**（是正の取り漏らし・新規指摘ではなく第1巡 🟡-4 本文で言及済みの型）。`port.py:38-48` の W3 明文化は ValueError のみ対称化し、`csv_source.py:40`（`pd.read_csv` のファイル不在→FileNotFoundError）を契約記述から落とした。Dukascopy は同例外を送出しえない。CSV パスは構築時固定でファイル不在＝デプロイ不整合の即時失敗のため実害限定。対処案: docstring に実装固有 I/O 例外の存在を明記または利用側の捕捉境界を定義。
3. **`ReplayIndicatorController` の戻り値型 widening（記録のみ・非破綻＝第1巡残存スコープの検証完了）**。基底 `indicator_controller.js:482-490`/`:493-499`（`toggleVisible`/`removeInstance`＝void）に対しサブクラス `replay_indicator_controller.js:136-143`/`:147-154` は MP インスタンス時に値を返す（事後条件の強化）。呼出側（基底 :827/:831）は戻り値を捨てるため破綻せず LSP 遵守。

### 模範例（LSP 遵守の正の参照・現行確認済み）
- Presenter 形式別 Port 分割（`ports.py:161-197`）: 部分履行スタブが構造的に生成不能＝🔴-1 完全是正を実証。
- `ResultSinkPort` 階層（`result_sink.py:33-69`）／`MaSlope` fail-fast（`ma_slope.py:45-58`・旧スタブ撤去確認）／profit_band 翻訳レジストリ隔離（`indicator_compute_adapter.py:60-90`）＋`pre_invoke` フック（`call_binding.py:93`）。
- `ReplayIndicatorController` の reveal seam（`replay_indicator_controller.js:57-59`）: 基底 `_extraComputeFields` override＋`undefined` 不送信 gate で present byte 不変の置換可能設計。

### 自己レビュー記録（要点）
- Pre-mortem「是正記録を所与採用し volume 非対称を見落とす（Type-B 追従）」→ csv_source/dukascopy_source を直接 Read し非対称を実証（🟡-1 計上）。「FileNotFoundError を新規と誤計上」→ 第1巡 🟡-4 既記載を確認し取り漏らし（🔵）へ格下げ。「戻り値 widening を 🔴 と過大評価」→ 呼出側が戻り値を捨てることを実証し 🔵 記録のみ。
- 棄却 4 件: MaSlope on_init 事前条件（=🟡-2 是正そのもの・fail-fast 許容）／集約 ReportPresenterPort への単一形式注入（型上成立せず・`test_report_ports_split.py:60-61` が not issubclass を固定）／ReplayMarketProfileActor.refresh 無描画（=🔵-13 既録）／`enterBar`/`isGrowingPush` 基底不在疑い（`market_profile_actor.js:279` に実在＋typeof ガード縮退で非破綻）。
- 残存リスク: 🟡-1 の実データ manifestation は未実測＝是正着手時に実測で確定させる。

### 裁定不要・実装フェーズへの申し送り
🟡-1 は 1 行ガード追加＋契約テスト固定の低リスク是正（実データ実測とセットで実施）。🔵-2 は docstring 追記のみ。🔵-3 は対応不要（記録のみ）。
## ISSUE-103: ISP（インターフェース分離の原則）第2巡アーキテクチャ監査（是正5件の実測再検証＋新規探索・自己レビュー済み）
- **ステータス**: RESOLVED
- **調査方法**: architecture-executor によるシステム全体の第2巡監査。ISSUE-099 の既知指摘（🟡5・🔵4）と是正記録（🟡-1 Report ports 分割／🟡-2 VolBand read-write 分離／🟡-3/-4 TimeframeHost・MarketProfileHost ロール契約／🟡-5 ProfileSink・TfPeriodSink ファサード）を精読して重複を除外し、是正後の契約が実利用と乖離していないかをクライアント別実利用メソッド集合の Grep 実測で突合。新規探索（ResultSinkPort・DatasetPort・CandleSource・replay_ports/marker_ports/report_ports・共有層）も実施。prompt-validation-workflow＋upstream-input-validation の自己レビューを通過（一次候補 7 件を棄却）。prototype_* は対象外。
- **総括**: **合格（新規 🔴0・🟡0・🔵0）**。第1巡是正 5 件は全件現行コードで成立し、宣言した狭い契約が実利用と乖離していないことを実測で確認。特に TimeframeHost（11面）・MarketProfileHost（19面）は controller の `host.X` 参照集合と**完全一致**し、`host_role_contract.test.js:87-99` の三方向テスト（依存面⊆契約／契約⊆実利用＝最小性／host面⊇契約＝充足）で過大契約を構造的に排除。ProfileSink（5面）／TfPeriodSink（2面）は `composition_root_front.js:261-282,397-416` で本番結線され各 actor が排他サブセットを維持。

### 是正 5 件の再検証実測（すべて成立）
- **🟡-1**: `ports.py:161-186` の 1 メソッド Port 3 種＋形式別 Interactor（`generate_report.py:43-69`）は各 1 メソッドのみ型依存。集約 `ReportPresenterPort` を受ける `GenerateReportInteractor` は 3 メソッド全実利用（:33,:36,:39）＝集約依存は正当。
- **🟡-2**: `vol_band_ports.py:29-41` で Writer（save_all）/Reader（get）の 1 メソッド分離済み・未使用 `save`/`all_week_ids` 撤去済み。
- **🟡-3/-4**: `timeframe_controller.js` の host 参照 11 面・`market_profile_controller.js` の host 参照 19 面が契約と完全一致（各 file:line 実測）。
- **🟡-5**: `market_profile_actor.js` は ProfileSink 面のみ・`tf_period_profile_actor.js:69,168`＋hover（`composition_root_front.js:416`）は TfPeriodSink 2 面のみ＝排他維持。

### 模範例（ISP 遵守の正の参照）
- TimeframeHost/MarketProfileHost（`indicator_controller.js:44-108`）＝契約と実利用の完全一致を CI が保証する理想形。
- 形式別 Report Port＋Interactor／`replay_ports.py`（1〜2 メソッドの単一ロール Protocol 群）／`marker_ports.py`・`report_ports.py`（単一メソッド境界の明示的別 Port 化）。

### 自己レビュー記録（棄却 7 件の要点）
- `ResultSinkPort`（3 メソッド ABC）: production クライアント 0 件（`main/__init__.py:566-568` は具象直接呼び）＝「未使用面への強制依存」が原理的に不成立。ISP でなくデッドポート（DIP/YAGNI）の論点として記録のみ。
- `DatasetPort`: 唯一クライアント `compute_indicators.py` が 3 メソッド全利用（:114,:120,:126）＝健全。
- `candles_controller.py:28-34` の dataset facade 部分利用＝既往 🔵-9 の追加ウィットネス（新規性なし）／MP actor typeof ガード 10 箇所＝既往 🔵-6 同一／renderer 広依存＝既往 🔵-8 同一／`CandleSource`・marker/report/replay ports＝単一ロール凝集で分割余地なし／共有層 common・common_view・api_shared＝Protocol/ABC 定義ゼロ（grep 0 件）で ISP 対象外。
- Pre-mortem「是正済み記録への追従で実測突合を省く」→ 3 辺（依存⊆契約・契約⊆実利用・本番結線）を独立実測して遮断。
- 残存リスク: MP actor 実装体（present/replay）のメソッド部分集合差の正式インターフェース化要否（🔵-6 内部）と、個別指標パッケージ（profit_* 等の _Chart/_Line Protocol 群）は未監査＝後続判断。

### 裁定不要・実装フェーズへの申し送り
新規是正対象なし。既往 🔵-6/-8/-9 は据置（YAGNI）のまま。ResultSinkPort のデッドポート整理（撤去 or 結線）は DIP 側の裁定事項として ISSUE-104（DIP 第2巡）の結果と併せて判断することを推奨。
- **クローズ（2026-07-30）**: 本エントリは「第2巡監査の結果、新規違反 0 件＝検証完了」の**記録**であり、対応を要する未解決事項を含まない（🔴0・🟡0・🔵0）。OPEN のまま残っていたのは起票時の取り違え。RESOLVED へ是正する。

## ISSUE-104: DIP（依存関係逆転の原則）第2巡アーキテクチャ監査（Tier1/Tier2 是正後コードの再検証＋新規探索・自己レビュー済み）
- **ステータス**: RESOLVED（2026-07-16 起票／🟡-1・🟡-2 を 2026-07-17 是正・🔵-3 は観点記録のため対応不要）
- **対応記録（🟡-1・2026-07-17）**: common（計算・安定層＝numpy のみ依存）→ common_view（表示・可変層）の後方互換再エクスポート（level_colors/LEVEL_LINE_WIDTH）を撤去し、安定度逆転（安定→不安定の SDP 違反）を解消。production 消費者は全て common_view 直参照へ移行済み（実 import 0 件を実測）。表示定数の唯一の公開元を common_view に一本化。テスト 7 ファイルを common_view 直参照へ更新＋逆依存撤去の回帰固定（common.__all__ に表示定数が無いことを assert）。common 20・profit_* 各緑。
- **対応記録（🟡-2・2026-07-17）**: compute_controller._present と candles_controller._error が正典 nested_error（api_shared.http_contract）を消費せず手組みしていた暗黙同期を解消。両者を nested_error 委譲へ。_present は既に同形（violations:[] 込み）で byte 不変。candles._error は violations 欠落を解消（nested_error 基底＋candles 固有 series:[] 合成）＝additive な形統一。ERROR_STATUS 直参照撤去。indicator_ui api 371 緑。🔵-3（合成点不在の観点記録）は違反でないため対応不要。
- **調査方法**: architecture-executor によるシステム全体の第2巡監査。ISSUE-091（Tier1 是正済み）・092（Tier2 裁定待ち）の全項目と 094/097/098/099 の対応記録を精読して重複を除外し、(a) 是正済み構造（api_shared/http_contract・common_view 分離・mp_source_capability.js・TickStorePort/DatasetPort・stats_boot 共有核）の所有権・依存方向の再検証、(b) 是正リファクタが副次導入した新規の綻び探索、を実施。依存方向（内→外 import・循環）と抽象所有権の両方を Grep/Read で実測。prompt-validation-workflow（Pre-mortem）＋upstream-input-validation の自己レビューを通過（一次候補 6 件を棄却）。prototype_* は対象外。
- **総括**: Tier1/Tier2 是正は DIP 中核（MP の I/O 具象直結・mp_stats の simulator private 越境・エラー契約分岐・indicator_ui の Output Boundary 欠落）を確実に解消し、内→外 import 違反は本巡ゼロ・循環なし・抽象所有権は概ね正配置。新規発見は是正の副次負債 2 件（🟡）＋観点記録 1 件（🔵）。

### 🟡 中（是正リファクタの副次負債）
1. **`common`（安定・本質）が `common_view`（不安定・偶有）へ逆依存（安定度逆転・ISSUE-092⑥ 分離の骨抜き）**。`common/__init__.py:41` の後方互換再エクスポート `from common_view import LEVEL_LINE_WIDTH, level_colors` により、純価格計算＋統計核（numpy のみ依存の安定層）が表示仕様層（ISSUE-093 で LEVEL_LINE_WIDTH 1→2 変更実績のある可変層）へ一方向依存（循環はないが安定→不安定の SDP 違反）。`import common` が transitive に表示層をロードし、092⑥ が企図した計算/表示のアクター分離がパッケージ依存レベルで無効化。**production 消費者は全て common_view 直参照へ移行済み**（profit_*/src の lwc_chart.py・plot.py 全件を grep 実証）で、`from common import level_colors/LEVEL_LINE_WIDTH` の残存はテスト 7 ファイルのみ（profit_rmm/oscillator/arctan/adx_needle/volatility/oscillator2 各 tests＋common/tests/test_level_style.py:13）。092⑥ の「再エクスポート温存」は当時 production 20 ファイル未移行が前提であり、現時点では撤去が YAGNI 上も妥当。対処案: `common/__init__.py:40-41,54-55` の再エクスポートを撤去しテスト 7 ファイルを common_view 直参照へ更新（production 影響ゼロは実証済み）。
2. **indicator_ui の一部 controller が単一定義 `nested_error` を消費せず nested ボディを手組み（契約の暗黙同期）**。正典は `api_shared/http_contract.py:25-38` の `nested_error()` で、MP controller（market_profile_controller.py:134）・serve_replay（:73）・indicator_ui server.py（:121）・catalog_controller（:27）は委譲済みだが、`compute_controller.py:48-57`（`_present`）は `ERROR_STATUS.get`＋手組み dict でボディ形を複製（ISSUE-092① の Presenter 新設時に発生）、`candles_controller.py:15-23`（`_error`）は形状も乖離（`violations` 欠落・`series:[]` 追加）。正典の形が変わると compute/candles だけ静かに乖離する暗黙同期。対処案: `_present` 失敗分岐を `nested_error(...)` 委譲へ置換、`_error` は `nested_error` の body を基底に `series:[]` を合成し `violations` 欠落を解消（既存 byte 固定テストで回帰確認）。

### 🔵 低（違反ではなく観点記録）
3. **Output Boundary（DatasetPort/TickStorePort）が Composition Root で注入されず内側層の遅延自己合成のみで具象化**。`indicator_ui/api/usecase/dataset_port.py:44-51`・`market_profile/api/.../compute/tick_store_port.py:44-51` の `set_*` 注入呼出は production ゼロ（grep 実証・テストのみ）。docstring「合成はエントリポイントの責務」と実態が乖離（simulator/replay_ui のような合成集約が indicator_ui/MP に不在）。モジュールレベル脱結合という DIP 中核は達成済み（関数内 import は 091🔵/092① で受容済みパターン）のため違反認定せず。任意対処: server.py 起動時に `set_dataset_port(MarketdataDatasetGateway())` を 1 行結線（現状維持も可）。

### 模範例（是正済み構造の検証結果・すべて正配置を実証）
- mp_stats 安定度逆転解消（`stats_core.py:24-28`→`common/stats_boot`・simulator 参照ゼロ・spa/var_backtests も同核 re-export）／MP compute の I/O 隔離（compute 所有 `TickStorePort` 経由・parquet/paths 直結消滅）／HTTP 契約の中立所有（api_shared 唯一実体・marketdata/api_contract.py:11 は後方互換 re-export へ降格）／serve_replay 契約統一（nested_error 直参照）／indicator_ui usecase 純化（DatasetPort＋注入協調子のみ）／JS 層の依存方向（domain/usecase→adapter/framework の import ゼロ・mp_source_capability.js は domain 正配置）／report_ui private 越境解消（export_report_payload.py:19 は公開 API 参照）。

### 自己レビュー記録（要点）
- Pre-mortem「🟡-1 は 092⑥ と重複では」→ 092⑥ の記録は分離の事実のみで逆依存＝安定度逆転の指摘は不在（grep 実証）、かつ production 消費者ゼロは是正完了後の新状態＝非重複。「🟡-2 は DRY で DIP 外では」→ 対象 smell「プロセス間契約の暗黙同期」に明示該当＝scope 内。「🔵-3 の過大認定」→ 違反でなく観点記録と明示ラベリングで回避。
- 棄却 6 件: compute_controller の `from marketdata import dataset`（adapter→共有ライブラリは合法方向・monkeypatch シーム温存）／ポートの関数内 gateway import（091🔵/092① 受容済み・🔵-3 に集約）／replay bridge の MP handle_market_profile 直 import（MP 配信殻の公開契約・091 A4 scope 外。MP 側に安定 Facade が無い非対称は将来負債候補として記録のみ）／mp_stats の sys.path 挿入（091#10 既記録・分析 standalone 用）／replay bridge の sys.path.insert（092⑤ が entry point フォールバック可と明文化済み）／indicator_ui ERROR_STATUS 分岐疑い（api_shared からの re-export を実証＝分岐なし）。
- 残存リスク: 本監査は静的 import 依存・抽象所有権のみ実測。実行時 DI 差し替え・byte parity・実 HTTP 応答は未実測（🟡-2 修正時は byte 固定テストで回帰確認が必要）。

### 裁定不要・実装フェーズへの申し送り
🟡-1（再エクスポート撤去＋テスト 7 件更新・production 影響ゼロ実証済み）と 🟡-2（nested_error 委譲化）は低コスト・挙動保存で是正可能。ISSUE-103 申し送りの ResultSinkPort デッドポート整理（撤去 or 結線）は本 Issue の 🔵-3 と同カテゴリ（合成点の扱い）として一括裁定を推奨。
## ISSUE-105: 全インジケーターのブラウザ実UI動作確認（第2巡・B方式サーバー実HTTP経路・Playwright実測）
- **ステータス**: RESOLVED（2026-07-17 起票／🟡-2 修正済み・🔴-1 は環境要因と判明し棄却・🔵-3/-4 対応済み）
- **対応記録（🔵-3・🔵-4・2026-07-17）**: 🔵-3＝moving_averages の計算時間足 enum に '30m'（30分）を追加（MA_TIMEFRAME_LABELS＋param enum values）。backend は marketdata.TIMEFRAME_RULES に 30m を持ち対応済み（実測）。🔵-4＝web/index.html のタイトルを「プロトタイプ A方式」から mode 中立の「プロトタイプ」へ（本 HTML は served=B方式／file://=A方式 の両用のため）。A方式バンドル専用の build.mjs は A方式表記のまま据置。実UIでタイトル反映・30分追加を確認。web 597 緑。
- **対応記録（🔴-1 再分類＝環境要因・2026-07-17）**: 起票時の「復元インスタンス無描画」を実測で再検証した結果、**復元コードの不具合ではなく検証サーバーのティックデータ鮮度（環境要因）**と確定。決定的証拠: 制御された localStorage（mode=normal・src=zp・1D）で復元すると全期間ヒストグラム＋POC* 46162.04＋VAH 73095.31 を完全描画し参照 iss-ui-sweep-mp-1d.jpeg と一致（ui-fix-restore-zp-1d.jpeg）＝**復元処理自体は正しい**。起票時の無描画は (a) 復元 params が sessions+dwell（tf-period 委譲）だったこと、(b) `--skip ticks` 起動で tick parquet が 2026-07-15 22:52 で終端し「当日」の実ティックが無いこと、の複合。dwell forming base の当日窓 [today_start, formingStart=today_start) が空になる機序を実測（`/market_profile_forming?from=今日&base=1`→priceMin=0.0/priceMax=1.0、`from=昨日`→最終実ティック 67695-67791＝起票時のマゼンタ線）。新規追加は既定 src=zp（非増分→全期間 refresh）で全描画するため、起票時の「復元=無描画／新規=全描画」差は **src/mode 差＋ティック鮮度**であり復元バグではない。フレッシュ dwell も同一環境で当日窓が空になり同挙動（ui-fix-fresh-dwell-today.jpeg）。→ 🔴 を棄却（実データ環境では復元は正常）。
- **対応記録（🟡-2 修正済み・2026-07-17・feature/issue-105-zombie-pane）**: 真因は `indicator_controller.recomputeAllApplied` フェーズ1 の `await _computeInstance` 中に凡例 close（removeInstance）が入ると、`_computeInstance` が **await 前スナップショット由来の `result.state` を無条件代入して除去済みインスタンスを「復活」させる**こと（フェーズ2 でこれが再描画され凡例行の無いゾンビペインが残留・ライブ購読継続）。`_computeInstance` に**競合削除ガード**を追加（await 後の live state で在席確認・除去済みなら `facadeRemove(result.state, id)` で復活を防ぎ accepted:false を返す）＋フェーズ2 描画直前の在席ガード（保険）。回帰テスト新設（gated compute の await 中に removeInstance→除去済みは `_renderInstance` を通さないことを固定）。indicator_ui web テスト緑（既存の replay_analysis/timeline_player 2 件失敗は本変更前から存在する無関係な欠損モジュール参照）。実UI無回帰確認（RSI 追加→15m 足切替で正常再描画→削除でペイン完全消滅・孤児なし・コンソールエラー0）。
- **（起票時記録・以下は上記対応で更新）**
- **検証方法**: B方式サーバー（framework.server・port 8000・データ更新スキップ起動）＋Playwright 実ブラウザで、カタログ全 19 指標＋market_profile（プロファイルタブ）の計 20 指標を実 UI・実 HTTP 経路のみで検証（compute 直叩き・合成データ不使用）。各指標: 追加→描画確認（スクリーンショット）→削除。代表指標で設定ダイアログの param 実反映（tgp_btlm maxbars 100→150 で系列延長＋凡例値変化、moving_averages 期間 9→50 で MA 値 67,720→68,564 に変化）を確認。時間足切替（5分→15分→30分→日→月）を指標表示のまま実施。MP は 通常/日別 × dwell/zp × 1D/1M/30分/5分 を検証し、リポジトリ内の参照スクリーンショット（iss-ui-sweep-mp-1d.jpeg 等）と照合。
- **総括**: 20 指標すべて正常描画・param 反映・時間足追従を確認（正常系は合格）。ただし **MP の状態復元インスタンスが全モードで無描画になる機能バグ 1 件（🔴）** と、**指標削除時のゾンビペイン（間欠レース）1 件（🟡）** を発見。🔵 2 件（MA 計算時間足の 30分欠落・タイトル表記）。

### 🔴 高（機能バグ・毎回のページ読込で発生）
1. **market_profile の状態復元インスタンスが全モードで無描画（フルプロファイル取得を一度も発行しない）**。ページ読込時に localStorage から復元された MP インスタンスは、1D/1M×通常×zp でヒストグラム・POC*・VAH が一切描画されず（実測スクリーンショット ui-r2-mp-normal-1d.jpeg）、日別モードのタイルも描画されない。サーバーログ実測: 復元インスタンスは `GET /market_profile?...&from=<当日>`（成長ウィンドウ）と `/tf_period_profile`（200・columns データあり）を約 2 秒間隔で繰り返すのみで、**全期間フル取得（from なし）を一度も発行しない**（ログ全件 grep で from なしは検証用 curl の 1 件のみ）。同一条件で**インスタンスを削除→新規追加すると即座に正常描画**（ui-r2-mp-fresh2-1d.jpeg＝参照 iss-ui-sweep-mp-1d.jpeg と一致: 右端アンカーヒストグラム＋POC* 46162.04＋VAH）。日別×zp×1D のタイル、dwell のセッションヒート着色、日別×5分での zp 選択不可（option disabled）も新規インスタンスでは全て正常。推定原因（未検証・推論）: 成長状態（growing）を含む永続状態の復元経路が初回フル fetch をスキップし growing-only ループに入る（applyGrowthState 系）。対処案: 復元経路でも初回は必ずフル取得→以後 growing 増分に移行させる。回帰固定: 「復元インスタンス追加後に /market_profile（from なし）が 1 回以上発行される」ことのテスト。
### 🟡 中（間欠・レース）
2. **指標削除時にペイン＋系列が残留する「ゾンビペイン」（間欠）**。ライブ更新中に Oscillator2 を削除し直後に別指標（OsiMA）を追加した際、凡例行は消えたがペインと系列（oscillator2_lc/rci）が残留（ui-r2-osc2-residue.jpeg）。残留ペインは**ライブ更新を受け続け**（凡例値 -2.179→2.904 に変化＝購読未解除）、凡例行が無いため UI から削除不能（ページ再読込でのみ解消）。その後、単独削除・同一手順の再試行 2 回では再現せず＝**非決定的（レース条件）**。推定原因（未検証・推論）: 削除時に in-flight の compute/live 応答が完了後に系列を再アタッチ、または削除処理と live 更新の競合。対処案: インスタンス削除時に世代トークン等で in-flight 応答を無効化し、live 購読の解除を削除処理の同期部で保証する。
### 🔵 低
3. **moving_averages の計算時間足 enum に「30分」が欠落**。チャートの時間足ボタンには 30分 が存在するが、MA 設定の「時間足」選択肢は `['chart','1m','5m','15m','1h','4h','1D','1W','1M']`（catalog.js:242・MA_TIMEFRAME_LABELS:215-218）で 30m のみ欠落。バックエンドは 30m 対応済み（marketdata TIMEFRAME_RULES・forming_bar.py:8 は 1m/5m/15m/30m/1h/4h/1D を明記）。意図的除外か欠落かは仕様未定義のため**要裁定**。欠落なら enum への 1 エントリ追加のみ。
4. **ページタイトルが「プロトタイプ A方式」固定**（web/index.html:6）。B方式サーバー（ライブ計算）配信時もタブ表示が A方式のまま。表記のみの不整合。

### 検証で棄却した候補（誤検出の排除）
- **RMMMACD の系列ラベル「RMMWMACD」**: typo 疑いだったが、MQL 参照実装の `SetIndexLabel(1, "RMMWMACD")` 準拠（profit_rmm_macd/src/lwc_chart.py:49-51）＝正当。
- **右端の孤立足（直前終値から約 1,900pt 乖離）**: m1 履歴が 2026-07-15 22:52 UTC（67,716）で終端しているのに対しライブ forming bar は 65,5xx（実測: jp225_m1.csv / jp225_tick_m1.csv の末尾行）。検証サーバーをデータ更新スキップで起動した**環境要因**であり製品バグではない（実際の相場下落は 1D 足で連続的に確認できる）。
- **コンソールの過去エラー（port 8144/8145 の ERR_CONNECTION_REFUSED・"Cannot update oldest data"）**: 前セッションの残留ログ。後者は既知の ISSUE-096（OPEN）。本セッション（port 8000）のページでは全操作を通じてコンソールエラー 0 件。

### 正常確認一覧（全 20 指標・実測）
tgp_btlm（mean/q5/q95）／profit_band（nOH/nOL/pOH/pOL バンド群）／price_range_power（水平レベル線）／moving_averages（MA・param 反映）／market_profile（新規インスタンスで全モード）／ADXNeedle／ArcTan／MFI（mfi+mfi_ma）／RSI（rsi+rsi_ma）／STC／Oscillator／Oscillator2（lc+rci）／OsiMA／RMM／Volatility／HLBand（オーバーレイ）／HLBandSep（別ペイン）／MFIMACD（3系列）／RMMMACD（3系列）／RSIMACD（3系列）。時間足切替（5分/15分/30分/日/月）で全て再計算・再描画、エラーなし。日別×5分の zp 選択不可制約も仕様どおり動作。

### 証跡
ui-r2-mp-normal-1d.jpeg（🔴 復元インスタンス無描画）／ui-r2-mp-fresh2-1d.jpeg（新規インスタンス正常＝参照一致）／ui-r2-osc2-residue.jpeg（🟡 ゾンビペイン）ほかスクリーンショット一式（リポジトリ直下 ui-r2-*.jpeg）。
## ISSUE-106: ライブ更新が休止をまたぐと確定足が恒久的に歯抜けになる（時系列データ欠落）
- **ステータス**: RESOLVED（2026-07-17 起票・同日修正。回帰テスト 15 件で固定＝chart_renderer_live_resync 10 件＋live_updater 5 件。web 613 中 611 緑・既存 2 失敗（replay_analysis/timeline_player）はクリーンツリーでも再現＝本変更と無関係。実 UI 無回帰確認 ui-fix-daily-after-tick.png: 日足 7/17 まで連続描画・ライブ tick 60 秒後も正常・コンソールエラー 0）
- **事象**: 日足チャートで 7/15・7/16 の確定足が描画されず、直近足（7/17 の現在足）だけが離れて表示される（ユーザー報告スクリーンショット ss20260717222153.jpg）。データファイル（rollups/jp225_m1_1D.csv・jp225_tick/jp225_tick_1D.csv）と `/candles` API は 7/17 まで完全（実測）。ページ新規読込では全足描画（実測 ui-check-daily.png）＝表示層のみの欠落。
- **原因（実コード確認済み）**: ライブ差分経路が「新規足は常に 1 本（＝配列末尾）」を暗黙前提にしている。`live_updater.js` の `_tick` は `/candles` 全件を取得しながら **末尾 1 本だけ** を `renderer.updateLastCandle` へ渡し、途中の確定足を捨てる。さらに served（B方式）では LiveTickPlayer が価格の唯一の書き手（suppressPriceUpdate=true）で現在足しか書かない。タブ休止（PC スリープ・バックグラウンドタイマー抑制）や更新停止で足境界を 2 本以上またぐと、間の確定足は誰も挿入せず、lightweight-charts の `series.update` は末尾より古い time を挿入できない（ISSUE-096 後退ガードでも skip）ため、リロードまで恒久的な歯抜けになる。
- **対策**: `ChartRenderer.resyncMissedCandles(candles)` を新設（サーバー正の取得配列に「実系列末尾より新しい足が 2 本以上」または「既知範囲内の未保持 time（穴）」を検出したときのみ setData 全置換で再同期。fitContent は呼ばずズーム保持。置換前末尾＝player の最新値が新データ末尾以上の time なら復元し価格巻き戻しを防止）。`LiveUpdater._tick` は毎 tick これを呼ぶ（suppressPriceUpdate でも欠落補完は実施）。通常運転（差分 0〜1 本）は従来経路のまま挙動不変。回帰テスト新設で固定。
## ISSUE-107: 4h 足に 2025-08-26 の外れ安値（~15,100 帯）が残存 — 供給系の外れ値遮断の設計不整合
- **ステータス**: RESOLVED（2026-07-17 起票・同日修正。marketdata 182 緑（新規 5 件含む）・replay_ui 169 緑・indicator_ui api＋MP api 667 緑（旧前提テスト 1 件を新正常へ更新）。実 UI 確認 ui-fix-107-4h-aug26-zoom.jpeg: 4h チャート 2025-08 下旬に 15k スパイク無し・オートスケール正常）
- **事象**: ユーザー報告「4h 足で 2025-08-26 の安値に外れ値。日足は修正されているのに 4h 足は修正されていない」。実測: jp225_tick 4h の 08-26 04:00 バー close=15144.4・08:00 バー open=15144.9・low=15098.5（当日実勢 ~42,400 から約 -64%）。
- **原因（実コード・実データ確認済み）**: Dukascopy 配信欠損により 2025-08-26 06:34〜09:09 UTC の連続 153 分バー（数千ティック）が ~15,100 帯の不正値（jp225_tick_m1.csv に混入・jp225_m1 系は無影響）。供給系の読取時クランプ（outlier_policy.clamp_ohlc_envelope）は「open/close は外れにくい」前提のバー内エンベロープ式のため、①不良ランがバー境界をまたぎ open/close 自体が不正になった 4h/1h バーを素通しし、②不良ランに完全に包含され全 4 値が不正なバー（1h×3 本等）は原理的に補正不能。日足だけ open/close が正常で low がクランプされ「修正されて見えた」（クランプ痕 low=min(open,close) の擬似値）。参照実装（prototype_260626-01 proto_server）は「日内 close 中央値±30% 超の M1 行を除去→resample」で全時間足が一貫清浄になる設計であり、replay_ui 系（_m1_repair.repair_day_outliers）には移植済みだったが、indicator_ui 供給系（tick_m1→rollup→dataset）には未移植＝**読取経路により補正品質が異なる設計不整合**。なお ISSUE-095 項目1 の「二相バー 4 本は保全」裁定は、当該バーの open/close を正値と誤認した前提に基づくもので、その 4 本こそ本不良ランの境界バーだった（前提の訂正・エンベロープ式自体は serving 安全網として妥当なので存置）。
- **対策**: ①参照実装と同一式の `outlier_policy.repair_day_outliers`（日内 close 中央値±30% 超の M1 行除去・純粋・冪等）を新設。② M1 素材化の単一漏斗 `tick_m1._clean_m1_day` を新設し build/append 両経路の日別集計直後に適用（以後の再構築・増分追記で不良ランは再混入しない。ticks_to_m1 は純粋集計のまま）。③ jp225_tick_m1.csv から不良 153 行を除去（parquet からの新パイプライン再集計と CSV 残行の完全一致を事前実証＝除去は本番再構築と同値。バックアップ jp225_tick_m1.csv.bak-utc20260717）。④ rollup.stream_build で全 8 TF を再生成し原子的入替（旧物は rollups_backup_utc20260717/。差分は 2025-08-26 関連バーのみ＝全 TF 実測）。live_tick_watch は停止せず周期間隙で入替（毎周期ディスク再読の自己修復設計を利用・入替後の継続追記を実測確認）。⑤ 検証: 全 TF serving 実測で乖離 ≤5.7%（正常変動）・4h 当日 low=42,134.97・実 HTTP /candles・実 UI スクショで清浄確認。旧前提テスト（日足 low がクランプ痕になる）を新正常（実ヒゲ供給）へ更新。
- **申し送り（未対応・小）**: ライブ形成中バー（forming_bar_from_ticks / LiveTickBuffer）はティック単位の日内中央値フィルタ未適用のため、今後同種の配信欠損が発生した当日中は形成中バーにのみ一時的に不正値が出うる（確定 M1 化の時点で除去される）。必要なら proto_server の窓内 mid 中央値フィルタ（±30%）を移植する。
## ISSUE-108: 全体表示（自動スケール）で本体縦ドラッグの価格パンが効かない — 陳腐化ゲートによるユーザビリティ低下
- **ステータス**: RESOLVED（2026-07-17 起票・同日修正。変更対象テスト 30/30 緑・web 全体 612/614 緑（残 2 失敗は ISSUE-106 記載の既存失敗＝本変更と無関係）。実 UI（B方式実HTTP・Playwright）で全体表示から下/上ドラッグとも平行移動を実測（ui-108-before-drag / after-drag-down / after-drag-up.png）・価格軸 dblclick で自動スケール完全復帰・コンソールエラー 0・空白露出/拡大縮小誤認の不具合は再現せず）
- **事象**: ユーザー報告「価格スケールによってドラッグで上下移動できるスケールとできないスケールがある。移動させたいのに移動できない」。実コード確認: `chart_interaction_controller.js` が本体縦ドラッグの価格パンを `renderer.isPriceZoomed()`（手動スケール中）のみに制限している。
- **原因（実コード・履歴確認済み）**: cf1c32d で完全自由 2D ドラッグを実装後、d37941d で「全体表示で縦パンすると空白露出＝拡大縮小に見える不具合」を理由にズーム中限定へ後退した。この不具合は旧 override 実装（`_priceZoomRange`＋autoscale provider 差し替え＋`autoScale:true` 併用）のアーティファクト。その後 6a61c54 で lwc v5.2 ネイティブ API（`setVisibleRange`＝`autoScale:false` へ遷移）に全面置換され、不具合の発生機構自体が消滅したが、ゲートだけが残存した。
- **対策（ユーザー裁定 2026-07-17: 全体表示でも不具合なく縦パンできる仕様にする）**: ゲートを撤去し、本体縦ドラッグは常時 `panPriceByPixels`（初回パンでネイティブに手動スケールへ遷移・span 不変・clampPriceRange で発散防止・価格軸 dblclick で自動スケール復帰は既存のまま）。回帰テスト更新＋実 UI（B方式実HTTP経路）でドラッグ実測確認。
## ISSUE-109: インジケーター設定「スタイル」タブが全インジケーターで機能していない（UI のみ実装・適用経路未実装）
- **ステータス**: RESOLVED（2026-07-17 起票・同日修正。対策案①〜⑥を全実装。回帰テスト 25 件新設（properties_dialog_styles 14＋chart_renderer_series_styles 8＋indicator_controller_styles 3）・web 全体 639 中 637 緑（残 2 失敗は ISSUE-106 記載の既存失敗＝無関係）。実 UI 実測（B方式実HTTP・Playwright）: MA 赤/線幅4/dashed が OK で即時反映（読み取り欄・軸ラベルも追従）・リロード復元・時間足切替で維持・再表示は実値表示・RSI の rsi_ma 個別非表示＋eye OFF→ON でも維持（AND 合成）・profit_band は系統 4 行（nOH/pOL/pOH/nOL・実色初期値）で nOH 一括変更が全構成系列へ反映・market_profile はパラメータータブのみ表示。スクショ ui-109fix-1〜10。テストが実装中の実バグ（controller _toJson の styles 欠落＝リロード消失）も検出し修正済み）
- **事象**: ユーザー報告「設定の『スタイル』項目が機能していない」。実測（B方式実HTTP・Playwright）: ①moving_averages の MA を赤/線幅4/dashed に変更し OK → チャートは青/細線/実線のまま（ui-109-ma-style-edited.png / ui-109-ma-after-ok.png）②ダイアログ再表示で編集値は #2962ff/1/solid に巻き戻り＝保存すらされない ③profit_rsi（pane 型）も赤指定 OK 後も緑のまま（ui-109-rsi-after-ok.png）④実際は緑描画なのにタブ表示は青 #2962ff＝表示値も実描画と乖離 ⑤profit_band は実系列 28 本に対しタブは「(動的系列)」1 行のみ（ui-109-band-style-tab.png）⑥market_profile はダミー系列 1 行（TPO 描画は primitive でありスタイル行自体が無意味・ui-109-mp-style-tab.png）。
- **影響範囲（構造的欠陥＝ダイアログ共通実装のため全指標に該当・20 指標）**: catalog.js 全 19（tgp_btlm / profit_band / price_range_power / moving_averages / profit_adx_needle / profit_arctan / profit_mfi / profit_rsi / profit_stc / profit_oscillator / profit_oscillator2 / profit_osi_ma / profit_rmm / profit_volatility / profit_hl_band / profit_hlband / profit_mfi_macd / profit_rmm_macd / profit_rsi_macd）＋ market_profile（catalog_entry.js・同一 PropertiesDialog 使用）。
- **原因（実コード確認済み・4 欠落の複合）**:
  1. **適用経路の完全欠落**: `properties_dialog.js` `_buildStylePane`（L590-640）は色/線幅/線種入力を `_styleState` に収集するが、リポジトリ全域で `_styleState` を読む箇所が 0 件。`_onOkClick`（L835-844）はパラメータタブの `values` と `variant` のみを `onApply` へ渡し、スタイル編集値は捨てられる。可視性タブの `_visibilityState` も同様に未消費（隣接欠陥）。
  2. **初期表示値の欠落**: タブ初期値は `SeriesDef.colorRule/width/style` 由来だが、catalog.js の全 SeriesDef でこれらは未設定（null）＝常にプレースホルダ #2962ff/1/solid を表示。実描画色は compute 応答ペイロードの `p.color/p.width/p.style`（chart_renderer.js `_renderSeries` L765-773）で決まり、両者に接続が無い。
  3. **renderer 側 API の欠落**: ChartRenderer に生成済み指標系列のスタイルを後から変更する手段（系列単位 applyOptions 相当）が無い（系列生成時 options のみ）。
  4. **動的系列の行展開欠落**: dynamic 系列は `seriesNamePattern` 展開前の 1 行「(動的系列)」で表示され、個別系列を指定できない（profit_band 28 本等）。
- **設計上の位置づけ（参照仕様確認済み）**: 内部設計_パラメータ設定ダイアログ.md §6.1 はスタイルタブの責務（SeriesDef 単位の色/線幅/線種・適用先 renderLine の color/lineWidth/lineStyle・applyOptions で再計算不要・OK 時は recompute→スタイル適用の順）を定義済み。ただし「詳細フォームはスコープ外」「プロトタイプ実装範囲は要確認（Q-5/A-4）」のまま UI 骨格（コメントに「最小可」）だけが実装され、適用経路が未実装のまま放置された＝機能しない UI が全指標に露出している状態。
- **対策案（全 6 項実施済み 2026-07-17・ユーザー着手指示）**: ①`_onOkClick` で `_styleState`/`_visibilityState` を読み `onApply` へ styles/visibilities として同梱 ②ChartRenderer に系列キー単位の `applySeriesStyle`（lwc `series.applyOptions`）と系列単位 `setVisible` を追加 ③スタイルタブ初期値を実描画スタイル（compute ペイロード保持値）から構築し表示乖離を解消 ④dynamic 系列は展開済み実系列名で行を生成（profit_band は系統粒度に畳む案＝仕様 §6.1 の要確認事項をユーザー裁定で確定要）⑤market_profile はスタイルタブ非表示または MP 専用項目へ差替（ダミー行の露出をやめる）⑥編集値の永続化（AppliedInstance への保存）。適用順は仕様どおり recompute→スタイル適用。
## ISSUE-110: ISSUE-109 実装のアーキテクチャ監査指摘（architecture-executor 徹底調査・🔴1/🟡2/🔵4）
- **ステータス**: RESOLVED（2026-07-17 起票・同日修正。🔴-1＝`facade.reconcileSeriesStyles` 新設＋`_applyStoredStyles` で実系列名と突合し stale キーを剪定（実系列集合が空のときは判定不能＝剪定しない防御付き）。🟡-1＝bucket 畳み込みを純関数 `form_model.buildSeriesStyleRows` へ抽出（接頭辞は pattern.template から導出＝書式ハードコード排除・dialog は表示既定のみ）。🟡-2＝`_applyDialogResult` 抽出＋「params/variant 無変更かつ styles のみ」判定時は recompute を省略し applySeriesStyle 直適用＋persist の高速経路。🔵-1＝getSeriesStyles 呼出へ typeof ガード統一。回帰テスト 9 件追加（計 646 中 644 緑・残 2 は ISSUE-106 記載の既存失敗）。実 UI 実測: スタイルのみ OK で /compute リクエスト 0 件・q_high 0.95→0.90 で stale キー btlm_q95 が localStorage から剪定（styles=null 化）・残存系列 btlm_q5 のスタイルは params 変更後も維持（スクショ ui-110-1〜3）。🔵-2/3/4 は将来検討として残置）
## ISSUE-111: histogram 系列（ADXNeedle 等）のスタイルタブに線幅/線種の設定項目が表示される（描画種別との不整合）
- **ステータス**: RESOLVED（2026-07-17 起票・同日修正。対策①〜④実施＋調査中に発見した追加不整合も是正: histogram はバー別着色（data[].color）が series 色より優先されるため色変更が見た目に反映されなかった → applySeriesStyle の色指定時に既存データ全バーの color を上書き置換し、以後の setData/updateSeriesTail 流入点も userColor へ写像（ライブ差分の新バーだけ元色に戻る不整合を遮断・色未指定時は素通し＝既定挙動不変）。回帰テスト 9 件追加（計 654 中 652 緑・残 2 は ISSUE-106 記載の既存失敗）。実 UI 実測（B方式・Playwright）: ADXNeedle のスタイルタブは色のみ表示（線幅/線種入力なし・DOM 検査 hasWidth:false/hasSelect:false）・赤指定で全バー＋軸ラベル即時変色・リロード復元でも赤維持・黄へ再変更も即時反映（スクショ ui-111-1〜5））
## ISSUE-112: ヒート配色 histogram とユーザー色上書きの優先関係（ユーザー裁定: ヒート絶対優先）
- **ステータス**: RESOLVED（2026-07-17 起票・同日修正。対策①〜④実施: userColor 全塗り替え機構（ISSUE-111 導入）を撤去し setData/updateSeriesTail は素通しへ復帰・styleMeta/getSeriesStyles/buildSeriesStyleRows に heat フラグ追加（histogram かつ data[].color 保持で true）・heat 行は色ピッカー非表示＋「ヒート配色（自動）」注記（CSS .prop-style-heat 追加）・色 patch は renderer 側でも heat histogram に対し無視（二重防御）・可視性トグルは heat でも有効。非 heat histogram の色変更は options.color で従来どおり有効。回帰テスト: renderer 5 件＋dialog 4 件を新裁定へ更新/追加（計 659 中 657 緑・残 2 は ISSUE-106 記載の既存失敗）。実 UI 実測: ADXNeedle のスタイルタブは色ピッカーなし・「ヒート配色（自動）」表示・バーはヒート配色（緑赤）維持（スクショ ui-112-1〜3））
## ISSUE-113: 時間足切替で手動価格スケール（拡大/縦パン）が移動先の時間足へ引き継がれる
- **ステータス**: RESOLVED（2026-07-17 起票・同日修正。`TimeframeController.setTimeframe` の切替確定直後に `renderer.resetPriceZoom()` を呼ぶ（同一足 no-op ではリセットしない・renderer 非対応 Fake は防御 no-op）。`setCandles` は不変（replay リビール毎バー呼出のため）＝陳腐化していた「dblclick が唯一の解除点」注記も現仕様（dblclick または時間足切替）へ更新。回帰テスト 3 件新設（timeframe_view_reset）。実 UI 実測: 日足で価格軸ホイール拡大→4時間へ切替→自動スケール（全体表示）へ復帰（スクショ ui-113-1/2））
- **事象**: ユーザー報告「価格スケールを拡大後、時間足を移動すると拡大した価格スケールが移動先に引き継がれる。ユーザビリティが悪い」。実コード確認: 手動スケール（軸ドラッグ/ホイール/縦パン＝`setVisibleRange` で lwc 内部 `autoScale=false`）は setData 全置換でも lwc 自身が保持する設計（6a61c54）で、`TimeframeController.setTimeframe` は価格スケールを触らないため切替先へそのまま持ち越される。
- **ユーザー裁定（2026-07-17）**: 時間足を移動した場合、表示（価格スケール）はリセットする仕様に変更。
- **対策**: `setTimeframe` の切替確定後（同一足の no-op ガード通過後）に `renderer.resetPriceZoom()`（`autoScale:true` 復帰・既存 API）を呼ぶ。手動スケールの解除点は従来「価格軸 dblclick のみ」→「dblclick または時間足切替」へ変更。`setCandles` には触れない（replay_ui の足リビールが毎バー呼ぶため・注記も更新）。回帰テスト追加＋実 UI（拡大→切替→自動スケール復帰）で確認。
## ISSUE-114: チャート右端に余白がなくストレスフルな表示（最新足が右端に張り付く）
- **ステータス**: RESOLVED（2026-07-17 起票・同日修正。対策①〜④実施: BASE_RIGHT_OFFSET_BARS=5 を ChartRenderer 生成時に timeScale へ適用・`setCandles` の fitContent 直後に scrollToRealTime で余白反映（順序を回帰テストで固定）・`setRightMarginFraction` の復元先を 0→常設 5 へ変更＋プロファイル余白は max 合成（旧仕様期待の既存テスト 1 件も新仕様へ更新）。回帰テスト 4 件新設（timeframe_view_reset）。web 全体 664/666 緑（残 2 は ISSUE-106 記載の既存失敗）。実 UI 実測: 初期表示・時間足切替後とも最新足の右に余白を確認（スクショ ui-114-1・ui-113-2）
- **追記（2026-07-18）**: バー数指定の欠陥が判明し ISSUE-115 で px 基準へ是正。
## ISSUE-115: 右端余白がバー数指定のため全体表示で実質不可視（ISSUE-114 の是正不足）
- **ステータス**: RESOLVED（2026-07-18 起票・同日修正。`_syncRightOffset` 単一権威で幅 5% 比率の px 一定余白へ変更（bars=width×frac÷barSpacing・小数適用）・可視範囲購読でズーム時も再計算（±0.01 バーの同値スキップでループ防止）・適用点は生成時/setCandles fitContent 直後/setRightMarginFraction/可視範囲変更の 4 点・MP プロファイル余白は比率 max 合成。回帰テスト 3 件新設＋既存 2 件更新（timeframe_view_reset 10 件緑・全体 666 中 664 緑＝残 2 は ISSUE-106 記載の既存失敗）。実 UI 実測: 日足全体表示で明確な余白（幅 ~5%）・時間軸ズームイン後も px 幅維持・1時間切替後も余白あり（スクショ ui-115-1〜3）。旧仕様の不可視の証跡は ui-114b-1/2）
## ISSUE-116: 仕様追加 — 過去へ遡った後に最新足へ戻る「最新のバーまでスクロール」ボタン（ホバー時のみ表示）
- **ステータス**: RESOLVED（2026-07-18 起票・同日実装。新規 `scroll_to_latest_button.js`（DOM アダプター・SRP/DIP 準拠）＋`renderer.isLatestBarVisible()` 新設＋composition root 配線＋CSS＋A方式バンドル MODULE_ORDER 登録（build_module_order テストが登録漏れを検出→即修正）。回帰テスト 10 件新設（表示条件 4 象限・pointerleave・クリック・防御・isLatestBarVisible 3 態）。web 全体 677/679 緑（残 2 は ISSUE-106 記載の既存失敗）。実 UI 実測: 最新表示中はホバーしても非表示・過去へドラッグ後ゾーン外非表示・右下ホットゾーンで » 表示・クリックで最新足へ復帰（右余白維持）＋ボタン非表示（スクショ ui-116-1/2））
- **追記（2026-07-18・ユーザー指示）**: 配置をチャートペイン上の右20%×下50% 位置へ・形状を円→角丸3px の四角へ変更（CSS のみ）。座標系は left:80%/top:50% 起点＝ボタン全体がホットゾーン内側に入る配置（right/bottom 起点だとゾーン外に落ち、ボタンへ乗ると隠れて押せないため）。実測: ボタン位置 x=幅×0.8・y=高さ×0.5・border-radius 3px・ボタン上ホバーで表示維持・クリック復帰正常（ui-116-3）。
- **追記2（2026-07-18・ユーザー指示）**: 配置をホットゾーン右下（ペイン右下・right:90px/bottom:50px＝価格軸・時間軸に重ならないオフセット）へ変更・サイズ x2（28→56px・フォント 15→30px）。実測: 56×56・角丸3px・ゾーン内のためボタン上ホバーでも表示維持・クリック復帰正常（ui-116-4）。
- **追記3（2026-07-18・ユーザー指示）**: 復帰スクロール速度 x2。lwc 既定アニメ（約 1000ms・速度指定不可）の代わりに `renderer.scrollToRealTime({speed})` を拡張し、speed>1 は自前イージング（ease-out cubic・1000/speed ms＝500ms）で `scrollToPosition(pos, false)` を毎フレーム刻む。» ボタンのみ speed:2 を指定（ライブ追従 catch-up の既存呼出しは speed 省略＝lwc 既定のまま挙動不変）。必要 API 欠落時は既定へフォールバック。回帰テスト 2 件追加（計 679/681 緑）。実測: クリック後 600ms 時点で最新足へ到達済み（ui-116-5）。
## ISSUE-117: 仕様追加 — 時間足を選択できるドロップダウン UI（TradingView 風カテゴリメニュー）
- **ステータス**: RESOLVED（2026-07-18 起票・同日実装。index.html にメニュー DOM（カテゴリ 分/時間/日・9 足）・開閉制御のみの新規 `timeframe_menu.js`・`syncButtons` にトリガーラベル同期を追加（`_el.timeframeMenuLabel`）・composition root 配線・MODULE_ORDER 登録・CSS。選択・active 同期は既存 `[data-timeframe]` 一括配線に相乗り＝新規経路なし。回帰テスト 7 件新設（開閉/項目選択クローズ/見出しクリック非クローズ/外側クローズ/防御/ラベル同期 2）。web 全体 686/688 緑（残 2 は ISSUE-106 記載の既存失敗）。実 UI 実測: トリガー「日 ▾」→メニュー開（現在足ハイライト）→「1時間」選択でチャート切替＋メニュー閉＋ラベル/ボタン row 同期＋外側クリック閉（スクショ ui-117-1/2））
- **追記（2026-07-18・ユーザー指示「旧UIを削除」）**: 旧・時間足ボタン row（.tb-timeframe・9 ボタン）と専用 CSS を撤去。選択・active 同期・A方式無効化はドロップダウン項目の `[data-timeframe]` が同一 bind() 配線で担うため機能影響なし（syncButtons のラベル取得元もメニュー項目へ自然に移行）。テスト 686/688 緑（変化なし）。実 UI 実測: 旧 row 消滅（DOM 0 件）・ツールバーは「NI225 | 4時間 ▾ | ライブ | インジケーター」・メニュー経由の切替/ラベル/ハイライト同期正常（ui-117-3/4）。
## ISSUE-118: 仕様変更 — ライブモードの切替を「ライブボタンのクリックのみ」に限定（自動遷移の削除）
- **ステータス**: RESOLVED（2026-07-18 起票・同日実装。`LiveFollowController` から自動遷移一式（subscribeVisibleRange 配線・_onRangeChange・_suppressAutoOff・_lastAtRightEdge）を削除・再FOLLOW の catch-up は無条件 scrollToRealTime に簡素化。テストを新仕様へ全面更新（自動遷移/抑制機構の回帰テスト群を「クリック以外で切り替わらない」検証へ置換・15 件）。`モード定義一覧.md` §2 と index.html 注記も同時更新。web 全体 678/680 緑（残 2 は ISSUE-106 記載の既存失敗）。実 UI 実測: 過去へ大きくドラッグしてもボタン点灯・背景維持（自動 ANALYSIS 発生せず）→手動クリックで ANALYSIS（消灯＋tint）→再クリックで FOLLOW＋最新足へ catch-up＋背景復元（スクショ ui-118-1〜3））
## ISSUE-119: ライブモードへ戻っても背景 tint が復元されない（既定背景の参照エイリアシング）
- **ステータス**: RESOLVED（2026-07-18 起票・同日修正。捕捉時に `{ ...base }` の浅いコピーで snapshot 化し lwc 内部 options と切り離し。参照エイリアシングを再現する回帰テスト（options() が内部参照を返し applyOptions が in-place 再帰マージする実 lwc 相当 fake）を追加＝修正前は fail・修正後 pass。web 全体 679/681 緑（残 2 は ISSUE-106 記載の既存失敗）。実 UI 画素実測: FOLLOW rgb(19,23,34)=#131722 → ANALYSIS rgb(27,26,36)=#1b1a24 → 復帰 rgb(19,23,34)=#131722 へ復元（ui-119-1））
## ISSUE-120: replay の MP 成長ゲートが src 能力を無視し、zp（全期間）が dwell 当日窓へ差し替わる（present とのゲート規則非対称）
- **ステータス**: RESOLVED（2026-07-18 起票・同日修正。`ReplayMarketProfileActor` の `refresh` と `_rebuildAt`（enterBar/growTo の共通実体）へ present と同じ能力ゲート `mpSourceCapability(src).incremental` を追加し、非増分 src（zp）は再生中も基底 refresh（as-of-T・src 維持・from なし＝全期間）へ委譲。replay domain へ `mp_source_capability.js` symlink を追加（growth_window と同パターン）。回帰テスト 3 件新設（zp は refresh/enterBar/growTo とも forming 0 件・fetchProfile が src=zp/to=T/from なし・dwell は従来どおり forming＝対称性）。replay スイート 224/230 緑＝新規 3 件含む全て緑・残 6 件は HEAD（全変更 stash 退避）でも fail する既存失敗（ISSUE-121 起票）。実 UI 実測（replay 8280・Playwright）: MP に src=zp 適用後・1足ステップ後とも `/market_profile?src=zp&to=…`（from なし）のみ発火し forming 0 件・プロファイルは全価格帯（~20k〜72k）に展開し POC* 表示＝全期間 as-of（スクショ ui-120-1/2））
- **結論**: ベースチャート基盤（ChartRenderer・symlink 共有）は同一。差は **MP アクターの成長経路ゲート**にある。
- **調査事実（実コード）**:
  1. backend `_handle_zp`（market_profile_controller.py）は candles 全範囲 [t0,t1] を窓とし to/from で切り出す＝**全期間 as-of を因果的に計算可能**。zp は `period` param（当日/全期間）も持つ。
  2. **present（ライブ）**: 成長ゲート `_isIncremental()` が **src 能力（mp_source_capability の incremental）を参照**。zp は `incremental:false` のため FOLLOW 中でも forming 経路に入らず常に refresh（全期間 as-of・period 尊重）＝zp 全期間が表示される。
  3. **replay**: 成長ゲート `isGrowingPush()` ＝ `_enabled && _growing && !_sessions` で **src 能力を見ない**。再生中は refresh が enterBar→forming 経路へ倒れ、`_buildFormingArgs` が **src='dwell' を強制**（MP-04: forming の原子は dwell 固定）＋ GrowthWindow が **from=当日始まり**（視認性優先のユーザー確定裁定）。
  4. 帰結: replay 再生中に zp/全期間を選んでも、実際に描かれるのは **dwell 原子の当日窓プロファイル**＝「zp の全期間が当日しか表示されない」ように見える（zp ですらない）。replay でも static（成長 OFF）は super.refresh() ＝全期間 as-of-T が出る。
  5. 「当日窓」自体は参照設計（prototype の古典的セッション MP・ユーザー確定）だが、それは**増分ソース（dwell）の forming 経路の設計**であり、非増分と能力表に明記された zp へ適用するのは present との規則非対称＝設計不整合。
- **対策案（未着手）**: replay の成長分岐（`ReplayMarketProfileActor.refresh` または `isGrowingPush`）へ present と同じ src 能力チェック（`mpSourceCapability(this._params.src).incremental`）を組み込み、非増分 src（zp）は再生中も基底 refresh（as-of-T 全期間・period param 尊重）へ委譲する。回帰テスト＋実 UI（replay で zp/全期間が as-of-T 全期間で描かれる）で確認。
## ISSUE-121: replay_ui テストスイートに既存失敗 6 件（ISSUE-120 検証中に発見・本変更と無関係を stash 実測で確認）
- **ステータス**: RESOLVED（2026-07-25 修正・検証済み。全 6 件テスト陳腐化と実証し、実装（参照実装）へ追随更新。replay_ui web 252/252 緑・回帰ゼロ）
- **内訳**: ①`replay_market_profile_actor.test.js` の 5 件 — GrowthWindow「当日始端」の期待値が UTC 0 時（86400 floor）のまま、実装は ISSUE-078 のセッション日（NY17:00 ET 基準＝実測 21:00 UTC）へ移行済み＝**テスト期待値の陳腐化**（例: expected 1782950400 / actual 1782939600・差 3h）②`indicator_controller.test.js` の 1 件 —「MP apply: setParams へ bins='60'」の期待に対し undefined（MP 既定 params の供給経路変化にテスト未追随の疑い・要調査）。
- **実測での分類（2026-07-25）**: 5 件は「①当日始端の陳腐化 3 件（`_buildFormingArgs merges now`/`replay override wins`/`enterBar ticklive fallback`）」＋「②描画駆動点の陳腐化 2 件（`regression: setEnabled...`/`regression(recurring flash)`）」に分かれる。②は当日始端でなく、replay の pull/push 分離（`onLiveTick` override が growing push を no-op 遮断・最初の描画は enterBar 駆動＝ISSUE-127/129 設計）にテスト未追随。①´`indicator_controller.test.js` の 1 件は実装欠陥ではなく `bins` 廃止→`dispbp` 一本化（ISSUE-079）の陳腐化（実測: setParams 実値 `{va:0.7,src:zp,period:all,dispbp:3,mode:normal}`）。
- **是正（テストのみ・実装/挙動無改変）**: ①当日始端 3 件を `Math.floor(t/86400)*86400`→`sessionDayStart(t)` へ／②描画駆動 2 件を「setEnabled 後に再生 1 フレーム目相当の `enterBar(now)` を呼び、増分 src（dwell）で forming 因果 base を検証」へ書き換え（検証意図＝完成足フラッシュ無しは保持）／①´`bins==='60'`→`dispbp===3` へ。検証: replay_ui web 250→252 緑（回帰ゼロ）。
## ISSUE-122: 第1段実装 — 共有 UI 部品（時間足ドロップダウン・»ボタン）を replay_ui へも配線（単一ソース化）
- **ステータス**: RESOLVED（2026-07-18 実装。ユーザー指示「第1段の実装しろ」＝リプレイ×ライブ統合検討の段階案第1段）
- **実装**: ①replay adapter/front へ `timeframe_menu.js`・`scroll_to_latest_button.js` の symlink 追加（indicator_ui 実体＝単一ソース）②replay composition root へ両コンポーネントの install 配線 ③replay index.html の旧時間足ボタン row を present と同型の tf-menu ドロップダウン DOM へ置換（replay の対応 8 足＝30m なし）。CSS は replay の app.css が indicator_ui へ symlink 済みのため自動共有。
- **検証**: replay スイート 224/230 緑（差分なし・残 6 は ISSUE-121 の既存失敗）・indicator_ui スイート 679/681 緑（無影響）。実 UI（replay 8280）: 旧 row 消滅・「日 ▾」→カテゴリメニュー開閉/現在足ハイライト/1時間選択で切替＋ラベル同期・過去ドラッグ後の右下ホバーで » 表示→クリックで最新（revealed 末尾）へ復帰（スクショ ui-122-1〜3）。
## ISSUE-123: ライブ／リプレイ間のコード重複の徹底調査（ユーザー指摘「重複＝参照でなく値渡しの可能性」）— 値渡し 3 件を実証
- **ステータス**: RESOLVED（2026-07-18 起票・同日是正（ユーザー指示「値渡しの設計を修正しろ」）。①chart_interaction_controller: 共有版へ `isVerticalPanBlocked` 述語注入オプションを追加し replay コピーを削除→symlink 参照へ統一（旧コピー固有の MP リプレイ中ゲートは replay composition root が注入＝挙動保存。**ISSUE-108 常時縦パンが replay へ自動伝播**）②composition root: チャート生成（createChart オプション＋メイン系列）と updatePaneHeight（byte 同一コピーだった）を共有ヘルパ `chart_bootstrap.js` へ抽出し両ルートが参照（**present のみだったクロスヘア Normal・現在値ライン橙 ISSUE-084 も replay へ自動伝播**）③tf-menu: DOM 生成を共有 timeframe_menu.js へ移し（groups 注入・既定 present 9 足／replay は 8 足注入）、両 index.html は空マウント 1 行のみ＝項目集合の二重管理を解消。replay の対応 symlink（chart_interaction_controller/chart_bootstrap）追加・A方式 MODULE_ORDER 登録。テスト: timeframe_menu 10 件（DOM 生成仕様へ全面改定）・replay interaction 8 件（共有版＋ゲート注入仕様へ改定・常時縦パン伝播を固定）。indicator 682/684 緑・replay 224/230 緑（残はいずれも既知の既存失敗）。実 UI 実測: present＝JS 生成 9 足メニュー/切替/ラベル同期/常時縦パン無回帰・replay＝8 足メニュー・**全体表示からの縦パンが動作（ISSUE-108 伝播）**・現在値ライン橙表示（ISSUE-084 伝播）（スクショ ui-123-1〜3））
## ISSUE-124: 再生が進まない（zp 選択時・ISSUE-120 是正の性能副作用）— 毎バー 1.3 秒の as-of 再計算を driver が同期待ち
- **ステータス**: RESOLVED（2026-07-18 起票・同日修正。`_rebuildAt` の非増分分岐を `_scheduleNonIncrementalRefresh()`（fire-and-forget＋直列 coalesce: busy 中の再要求は pending 1 個に畳み、完了後に実行時点の getContext().to＝最新カーソルで 1 回だけ再実行・例外時も busy 解放＝自己回復）へ変更し driver の await を即時解放。gear/静止時の refresh() 直呼びは従来どおり await（挙動不変）。回帰テスト更新（同期連打 3 発→fetch 1〜2 回への coalesce・最新カーソル勝ちを固定）。replay スイート 224/230 緑（残は ISSUE-121 の既存失敗）。実 UI 実測: 修正前 zp 再生 1 バー/10 秒 → 修正後 4 バー/10 秒＝ベースライン（dwell 3 バー/10 秒・足内アニメーション律速）と同等へ回復・コンソールエラー 0・ステップ/スクラブ正常（ui-124-6））
- **再現（実測）**: MP src=zp の状態でスライダ中央→再生 → 速度 1.0 でも 1 バー約 1.3 秒しか進まない（bar 750→751 に 5 秒・コンソールエラー 0）。ステップ操作・スクラブ自体は正常。
- **原因（実コード確認）**: replay.js driver は完成足フラッシュ防止設計のため毎バー `await marketProfile.enterBar(t)`（L157）する。ISSUE-120 是正で非増分 src（zp）の enterBar は基底 refresh（as-of-T 全期間 zp・実測 1.2〜1.3 秒/回）へ委譲するようにしたため、**この重い再計算が毎バーの同期待ちに入り再生スループットが崩壊**（従来 dwell forming は軽量で顕在化せず）。
- **対策**: `_rebuildAt` の非増分分岐を fire-and-forget＋最新 coalesce（busy 中の再要求は pending 1 個に畳み、完了後に最新カーソル＝getContext().to で 1 回だけ再実行）へ変更し、driver の await を即時解放する（前回描画保持＝非破壊・zp プロファイルは計算が追いつき次第 as-of 更新）。gear/静止時の `refresh()` 直呼びは従来どおり await（挙動不変）。回帰テスト更新＋実 UI で再生スループット回復と zp 追随更新を確認。
- **調査方法**: replay_ui/web 全ファイルを symlink（参照）/実体に分類し、実体のうち indicator_ui と同名のものを diff で定量化。
- **結果①（参照・健全）**: 32 ファイルが symlink 単一ソース（adapter 22・domain 6・usecase 3・css 1・vendor 1）。ブラウザ側は静的サーバの dual-root フォールバック（shared_js_root=indicator_ui/web）も参照を補完。
- **結果②（実体・正当な固有差分）**: replay_boundary_dim / replay_indicator_controller / replay_market_profile_actor / replay_view / replay.js / replay/{state,stream,timing} / data/sample_data.js ＝基底を参照する subclass・再生純ロジックで重複なし。
- **結果③（値渡し＝コピー重複・ドリフト実証あり）**:
  1. **chart_interaction_controller.js**（replay 123 行・diff 54 行）: 意図的コピー（スワイプ除外・replayBar 依存除去・_isReplayOn ゲート追加）。**ISSUE-108（常時縦パン）が未伝播**＝replay は旧仕様「価格ズーム中のみ縦パン」のまま——ユーザー仮説（値渡し→挙動乖離）の実証。
  2. **composition_root_front.js**（replay 293 行 vs present 622 行）: 合成ルート自体はアプリ固有が正当だが、チャート生成オプション・pane 高供給・readout 配線等の共通ブロックが複製。今回の第1段で配線 2 行を手動追加した事実自体が値渡しの証跡。
  3. **index.html**（共通 96 行）: ツールバー/オーバーレイ骨格の複製。tf-menu ブロックも手動複製（8 足差分つき）＝今後も変更のたび二重編集。
- **対策案（未着手・要承認）**: ①chart_interaction_controller を symlink 参照へ統一（present 版は 082 でスワイプ撤去済み＝コピーが除外したかった部分は既に無い。残差の _isReplayOn 縦パンゲートは constructor オプション注入で吸収）＝ISSUE-108 も自動伝播 ②composition root の共通ブロックを共有ヘルパ（chart_bootstrap 等）へ抽出し両ルートが参照 ③tf-menu 等の共有 UI DOM を JS（timeframe_menu.js）側で生成する方式へ変更し index.html の二重管理を解消。
- **事象**: ユーザー報告「ライブモードに戻った場合、背景色に変化がない」＝ANALYSIS の紫 tint（#1b1a24）が FOLLOW 復帰後も残る。
- **原因（実コード確認）**: `chart_renderer.setAnalysisTint` は既定背景を初回呼出時に `chart.options().layout.background` から捕捉するが、**オブジェクト参照のまま保持**している。lwc の applyOptions は内部 options オブジェクトへのマージ（同一オブジェクトの書き換え）であるため、tint ON 適用時に捕捉済み基準オブジェクトの color 自体が `#1b1a24` へ書き換わり、復元が「tint 色 → tint 色」の無変化になる。
- **対策**: 捕捉時に浅いコピー（`{ ...base }`）で snapshot 化し、内部オブジェクトと切り離す。参照エイリアシングを再現する回帰テスト（options() が内部参照を返し applyOptions が in-place マージする fake）を追加。実 UI ではチャート canvas の画素色を実測して tint→復元の色変化を確認。
- **旧仕様**: 手動クリックに加え、可視範囲購読による自動遷移（右端離脱→自動 ANALYSIS／右端復帰→自動 FOLLOW・EPS=1 バー・programmatic scroll 抑制 _suppressAutoOff 付き）。
- **対策**: `LiveFollowController` から自動遷移一式を削除（install の subscribeVisibleRange 配線・_onRangeChange・_suppressAutoOff・_lastAtRightEdge）。手動 ANALYSIS→FOLLOW の catch-up scroll は無条件 `scrollToRealTime()`（抑制機構が不要になるため簡素化）。`renderer.subscribeVisibleRange` 自体は右余白同期等と独立の汎用 API のため存置。関連テスト（自動遷移系）を新仕様へ更新・`モード定義一覧.md` と index.html 注記も同一コミット内で更新。実 UI 確認（過去へスクロールしても FOLLOW/背景維持・クリックのみで切替・再クリックで catch-up）。
- **仕様**: ツールバーに現在足を表示するトリガーボタン（例「日 ▾」）を追加し、クリックでカテゴリ分け（分/時間/日）のドロップダウンを開閉。項目選択で時間足を切替（既存ボタン row と同一経路）・現在足をハイライト・外側クリック/再クリックで閉じる。既存の時間足ボタン row は存置（「追加」指示のため）。
- **スコープ**: 対象はバックエンド対応済みの 9 足（1m/5m/15m/30m/1h/4h/1D/1W/1M）。参考画像にあるティック/秒/レンジ/カスタム足はロールアップ供給系が未対応のため対象外（必要なら別途承認）。
- **設計**: メニュー項目に既存の `data-timeframe` 属性を付与＝`bind()` の一括配線（クリック→setTimeframe）と `syncButtons()` の is-active 同期にそのまま乗せる。新規 JS は開閉制御のみの `timeframe_menu.js`（DOM アダプター）。トリガーラベルは `syncButtons` が現在足ボタンの表記から更新（`_el.timeframeMenuLabel`）。A方式バンドル MODULE_ORDER 登録・CSS 追加・回帰テスト＋実 UI 確認。
- **仕様（ユーザー指示の確定）**: ①過去へスクロールした状態から現在（最新足）へ一発で戻る UI を追加 ②常時表示ではなく、特定の範囲へのマウスオーバー時のみ表示。
- **設計**: 新規 adapter/front コンポーネント `ScrollToLatestButton`。ホットゾーン＝チャート右 20%×下 50% 領域。表示条件＝ホットゾーン内ホバー **かつ** 最新足が可視範囲外（`renderer.isLatestBarVisible()` 新設・`getVisibleLogicalRange().to >= 末尾index` で判定・判定不能は安全側で非表示）。クリック＝`renderer.scrollToRealTime()`（常設右余白 rightOffset を尊重して復帰）→非表示。pointerleave で非表示。ボタンは「»」＋ title「最新のバーまでスクロール」。composition root で配線・CSS 追加。回帰テスト＋実 UI 確認。
- **事象**: ユーザー再報告「チャートの右側に余白を表示したい」。実測: ISSUE-114 の余白は rightOffset=5 **バー**指定であり、日足の初期表示は fitContent で 1500 本を幅 ~710px に収める＝barSpacing ≈ 0.47px。よって余白は 5×0.47 ≈ **2〜3px で視認不能**（ライブ 70 秒後も同様・ui-114b-1/2）。
- **原因**: lwc の rightOffset は論理バー単位で、ズーム倍率（barSpacing）に比例して px 幅が変動する。バー数固定では全体表示（微小 barSpacing）で余白が消え、拡大時は過大になる。
- **対策**: 余白を**チャート幅比率（BASE_RIGHT_MARGIN_FRACTION=0.05＝幅 5%）基準**へ変更。`_syncRightOffset()`（単一権威）が bars = width×frac ÷ barSpacing を小数のまま適用し、`subscribeVisibleLogicalRangeChange` 購読でズーム変化時も px 幅一定へ再計算（同値スキップでループ防止）。MP プロファイル余白とは frac の max 合成（既存 0.30 は従来どおり優先）。適用点: 生成時・setCandles（fitContent 直後）・setRightMarginFraction・可視範囲変更。回帰テスト更新＋実 UI（初期表示・ズーム時の余白維持）確認。
- **事象**: ユーザー報告「チャートの右端に余白を設けたい。現状は余白がなくストレスフル」。実コード確認: `createChart` の timeScale オプションに rightOffset 指定がなく（lwc 既定 0）、`setCandles` は `fitContent()` で全データを幅いっぱいへフィット＝最新足が常に右端へ張り付く。rightOffset 機構自体は MP プロファイル右マージン（`setRightMarginFraction`・幅 30% 等）でのみ一時利用され、解除時に 0 へ復元される。
- **対策**: ①ChartRenderer に基準右オフセット（BASE_RIGHT_OFFSET_BARS=5 バー・TradingView 相当の常設余白）を導入し、生成時に timeScale へ適用 ②`setCandles` の `fitContent()` 後に `scrollToRealTime()` を呼び余白を反映 ③`setRightMarginFraction` の復元先を 0→基準値へ変更・プロファイル余白は max(計算値, 基準値)（MP 余白と常設余白の整合）④FOLLOW（ライブ追従）の `scrollToRealTime` は rightOffset を尊重するため新足でも余白維持。回帰テスト追加＋実 UI 確認。
- **背景**: histogram 系はバー別着色（data[].color・値に応じた緑→赤のヒート/level_colors 配色）仕様を持つ。ISSUE-111 の是正でユーザー色指定時に**全バーの data[].color をユーザー色へ上書き置換**する実装にしたため、ヒート配色が破壊される。
- **ユーザー裁定（2026-07-17）**: **ヒートモード（バー別ヒート配色）を絶対に優先する**。ユーザー色上書きでヒート配色を潰してはならない。
- **対策**: ①renderer: histogram のユーザー色によるデータ全塗り替え機構（userColor・setData 置換・setData/updateSeriesTail の流入点写像）を撤去。バー別色を持つ histogram（heat）は色 patch を無視（ヒート維持）。バー別色を持たない histogram は series options.color が素で効くため上書き機構は不要 ②styleMeta/getSeriesStyles に heat フラグ（データにバー別色あり）を追加 ③ダイアログ: heat 行は色ピッカー自体を出さず「ヒート配色（自動）」と明示（機能しない設定項目を露出しない＝ISSUE-111 と同一原則）④可視性トグルは従来どおり有効。回帰テスト更新＋実 UI（ADXNeedle）確認。
- **事象**: ユーザー報告「ADXNeedle 系は棒グラフ表示なのに、設定項目はラインの設定項目」。実コード確認: `form_model.buildSeriesStyleRows` が行モデル構築時に系列の `kind` を落とし、`properties_dialog._buildStylePane` が全行一律に色＋線幅＋線種の 3 入力を生成する。histogram 系列に線幅/線種を指定しても `chart_renderer.applySeriesStyle` は histogram に色のみ適用（lineWidth/lineStyle を送らない設計）のため、**効果のない設定項目が露出**している。
- **影響範囲**: histogram 系列を持つ指標（profit_adx_needle ほか PF_HIST 使用指標: Oscillator/Oscillator2/OsiMA/RMM/MFIMACD/RMMMACD/RSIMACD 等）。
- **対策**: ①`buildSeriesStyleRows` の行に `kind` を保持 ②スタイルタブは kind='histogram' の行に色のみ表示（線幅/線種入力を生成しない）③`_collectStyleChanges` は未生成入力を安全にスキップ ④def フォールバック行にも kind を伝搬。回帰テスト追加＋実 UI（ADXNeedle）で確認。
- **前提**: 依存方向違反 0 件（domain←usecase←adapter の内向き依存は健全）。以下は変更局所化・状態整合の指摘。
- **🔴-1 動的系列の params 変更で保存済み styles の系列名が実系列と乖離（無反映・stale 永続蓄積・意図せぬ復活）**:
  - 該当: `indicator_controller.js` `_onGear.onApply`（styles を旧系列名でマージ→直後の recompute で系列が改名されうる）＋`_applyStoredStyles`（旧名は renderer 側 no-op）＋`facade.setSeriesStyles`（上書きのみ・stale キー剪定なし）。
  - 対象指標（実コード確認済み）: tgp_btlm（q_low/q_high 変更で `btlm_q5`→`btlm_q10` 等に改名）・profit_band（probabilities 変更で系列集合が変化）。※監査の例示 moving_averages は誤り（静的 4 系列 MA/Smoothing/Upper/Lower・catalog.js:248）で棄却し訂正。
  - 影響: (a) スタイルと当該 params を同一 OK で変更するとスタイルが黙って失われる (b) 旧名キーが AppliedInstance.styles に恒久残留し永続化され続ける (c) params を元に戻すと残留スタイルが意図せず復活する。
  - 対策案: `setSeriesStyles` または `_applyStoredStyles` に「現在の実系列名集合に存在しないキーの剪定（reconciliation）」を追加。少なくとも recompute 後に実系列へ再マップ不能な styles キーを破棄し永続汚染を止める。
- **🟡-1 bucket 畳み込み（系列名→系統の逆引き）が DOM アダプタ（properties_dialog._seriesRows）に配置され、系列命名規約の知識が `_expandPattern`（indicator_controller）と二重化**: template 書式（`'{bucket} {pct}%'`）変更時に 2 箇所の同期修正が必要＝変更局所化違反。対策案: 畳み込みを純関数として usecase（form_model.js 等）へ抽出し命名規約の単一ソースを共有、dialog は行 view-model を受け取るだけにする。
- **🟡-2 スタイルのみ変更でも必ず full recompute（/compute 往復＋系列 remove/redraw）が走る**: `applySeriesStyle` の設計目的（§6.1 再計算不要の即時反映）が OK 経路で未活用。compute 失敗時にスタイル変更も巻き添えで失われる不要な結合。対策案: 「params/variant 無変更かつ styles のみ」判定時は recompute を省略し `applySeriesStyle` 直適用＋persist。
- **🔵（将来検討・4 件）**: ①`getSeriesStyles` 呼出の typeof ガード欠如（`applySeriesStyle` 側と防御非対称）②horizontal_line 系列のスタイル編集は対象外のまま（機能ギャップ・整合自体は保たれている）③styleMeta（派生キャッシュ）と AppliedInstance.styles（単一権威）の整合が「系列再生成直後に _applyStoredStyles を呼ぶ」暗黙不変条件依存＝構造的強制なし ④generation 競合 reject 時に styles がメモリ残留・未 persist（次回 redraw まで非表示の稀ケース）。
- **監査で確認済み（問題なし）**: 依存方向・domain 純度・styles の永続往復・可視性 AND 合成の規則一致・onApply 第3引数の後方互換（MP ほか）・replay_ui への波及（独立コピーは存在せず symlink 共有ベースの継承で自動追随）・`_drawLatest` 末尾差分経路とスタイルの整合・YAGNI。
## ISSUE-125: リプレイ×MP zp×期間「当日」で日内推移が描けない — 1D は当日確定形が最初から表示（経過分析不能・日内ルックアヘッド）
- **ステータス**: RESOLVED（2026-07-18 実装・検証済み）
- **是正結果（実装＝対策案①②③のとおり・承認済み y）**: backend は `/market_profile` に任意 `asof`（UNIX 秒）を追加し `_handle_zp` が `compute_zp_profile(..., now=asof)` で「現在時刻」をカーソル秒に上書き（省略時は実時計＝後方互換）。経路は present server（server.py）と replay 鎖（serve_replay→usecase→gateway→bridge）の両方に透過。フロントは共有 client/actor に asof seam（基底は常に空＝present 不変）を追加し、replay actor が enterBar/feedTick の revealed tick 秒を単調前進（後退スクラブは enterBar で巻き戻し）で保持、ISSUE-124 coalesce 機構で zp 再計算を発火。
- **検証（2026-07-18）**: (1) 実 HTTP: 同一 from/to の 1D×zp が asof=+3h/+9h/+15h で tpo_units=2/362/722 と線形成長・md5 全相違、asof 省略時は従来どおり 1335（全日）。(2) 実 UI（Playwright・8281・2025-08-26 セッション再生）: `&asof=` がリビール tick 秒で単調前進する要求が coalesce 間隔で連続発行され全 200、同一バー再生中に POC が 42532.22→42443.00 へ更新・プロファイル histogram が視認可能に成長＝日内推移の経過分析が可能に。コンソールエラーは favicon 404 のみ（無関係）。(3) テスト: JS 新規 5 件（asof 透過・単調・後退巻き戻し・static 非付与・dwell 非干渉）＋Python 新規 3 件（asof→now 上書き・省略時実時計・不正 asof 無視）全通過。replay web 229 通過/6 失敗（全て既知 ISSUE-121・HEAD ベースラインと同一）、indicator_ui web 682 通過/2 失敗（既知モジュール欠落）、market_profile api pytest 299 全通過。A方式バンドル（out/prototype.html）再生成済み。
- **事象**: ユーザー報告「リプレイモードの MP 指標 zp で当日を選択したときの更新が、当日の推移が分からない。当日結果が表示されるだけで経過分析検証ができない」。
- **原因（実測 2026-07-18・実 HTTP 8281）**: `GET /market_profile?src=zp&timeframe=1D&from=<セッション始端>&to=<日内カーソル>` は、to を日内 3 点（2025-08-26 セッション: 00:00/06:00/12:00 UTC）で動かしても応答が byte 一致（md5 同一・tpo_units=1335＝全セッション分）＝当日確定形が固定表示。実体は 2 段: ①client の as-of カーソル `to` はバー時刻粒度＝1D では当日中ずっと同一値（当日バー label）②controller `_handle_zp` が集計窓終端を `win_to = t1 + bar_sec`（1D=86400）で最終足の全期間へ拡張するため、境界日が完全日 `_zp_day_rollup`（確定 z）に落ちる。よって日足バーが reveal された瞬間に当日確定 z が描かれ、以降不変＝日内ルックアヘッド。なお 15m では to がバーごとに進み応答も毎バー変化（md5 相違・実測）＝バー粒度の as-of 推移は既に成立している（バックエンド `_zp_partial_rollup` は分カラム粒度のサブ日窓集計を実装済み）。
- **対策案（未着手・要承認）**: ① backend `/market_profile` に任意パラメータ `asof`（UNIX 秒・forming 系の `now` と同規約）を追加し、zp（必要なら dwell も対称）の窓終端を `min(t1 + bar_sec, asof)` へクランプ→境界日は既存 `_zp_partial_rollup` の分粒度 as-of に自然に落ちる（省略時は現行挙動＝後方互換）② replay actor: 非増分 src の refresh（`_scheduleNonIncrementalRefresh`）へ driver の revealed tick 秒（enterBar/growTo/feedTick の now）を asof として透過し、足内 tick でも throttle＋既存 coalesce（ISSUE-124 機構）で再計算を発火＝1D でも当日 z が分粒度で成長 ③ 更新周期は zp 再計算実測 1.2〜1.3 秒/回に coalesce されるため再生スループットへの影響なし（ISSUE-124 と同型）。
## ISSUE-126: ISSUE-125 是正の仕様乖離 — asof を「再生中のみ」に勝手に限定し、静止カーソルで当日確定形が表示される（指示は「ライブモードと同じ仕様」）
- **ステータス**: RESOLVED（2026-07-18 是正・検証済み）
- **是正結果**: replay actor `_asofExtra()` のゲートを「zp × カーソル存在中は常に付与」へ是正（成長中=リビール tick 秒 `_asofSec`／静止=主機能 as-seen-at-t の単一時計 `getContext().to`＝untilTime を参照。カーソル未確定・増分 src は従来 URL 不変）。主機能・基底 actor・present・backend は無改変（基本設計: 現在時刻の定義を主機能の T に一元化し、zp は参照のみ＝OCP）。誤仕様を固定していた static テストを正仕様（asof=T 付与）へ書き換え、cursor 不在・dwell 不変の 2 件を追加。
- **検証（2026-07-18 実UI・8281）**: 静止スクラブ bar 1221（2025-08-26）で再生なしに `&asof=1756166400`（=T）付き要求が発行され応答は部分形 tpo_units=2（従来は確定形 1335 の先出し）。再生開始で asof がリビール秒で前進し成長継続（全 200・スループット正常）。restore 直後（cursor 未確定）は asof 非付与＝従来。JS スイート 231 通過・失敗は既知 ISSUE-121 の 6 件のみ（回帰ゼロ）。
- **事象**: ユーザー動作確認で「最初に当日結果分が表示される」。静止カーソル設置時の初回描画が asof なし＝実時計判定となり、過去日が完了日扱いで当日確定形（全日 z）を表示。承認された指示は「ライブモードと同じ仕様」（ライブは静止表示でも常に経過分の部分形）であり、asof の付与を isGrowingPush()（再生中）に限定したのは実装者の無承認スコープ縮小＝仕様乖離。
- **対策案**: replay actor の asof 付与ゲートから isGrowingPush() 条件を外し、「リプレイカーソルが存在する間は常に」zp の fetch に asof（リビール済み末端秒）を付与する（ライブ完全対称: リプレイ期間の時計＝現在時刻）。主機能（共有基底 actor・present・backend・他 src）は無改変で、replay 側 seam（_asofExtra/_asofSec）の参照のみで実現する。静止テスト（asof 非付与を主張）は正仕様（付与）へ書き換える。
## ISSUE-127: リプレイしても当日確定形が表示される（ISSUE-126 後の残存不具合・ユーザー報告）— 小数秒 asof の破棄＋partial rollup キャッシュ毒
- **ステータス**: RESOLVED（2026-07-18 是正・検証済み）
- **是正結果**: ① replay actor `_asofExtra()` が asof を Math.floor で整数秒化（契約 UNIX 秒準拠・送信側是正）② `_zp_partial_rollup` のキャッシュ読み出しを `hi<=now`（現要求の now で窓完了）のときのみにゲート（完了窓 roll は now 非依存で共有安全・未完了窓は都度計算＝ライブ当日と同一規約）。
- **検証（2026-07-18）**: (1) 実 HTTP（再起動後 8281）: 事象再現ペア to=1756252800&from=1756242000 で asof 5 点 → tpo_units=1/2/165/788/1216 と単調成長・md5 全相違。毒順（実時計 1335 → asof）でも 165 と非汚染。(2) 実 UI（1分OHLC 再生＝報告条件）: asof が全て整数で単調前進・UI 発行 URL の応答が部分形（187→1328）。(3) テスト: Python 新規 1 件（毒順回帰・修正なしで失敗確認済み）＋JS 新規 1 件（小数秒 floor）。市場プロファイル pytest 300 全通過・replay web 232 通過（失敗は既知 ISSUE-121 の 6 件のみ）。
- **事象**: ユーザー動作確認「リプレイしても当日分が表示される」。実測再現: `to=1756252800&from=1756242000` で asof を日内 5 点に振っても応答 byte 一致（tpo_units=1335=全日）。
- **原因（実測・コード確認）**: 複合 2 件。①最新足更新=1分OHLC 等の合成 tick は小数秒を生み、client が `asof=1756262637.6237624` を送出。controller `_parse_to` の str 分岐が `isdigit()` のため小数文字列で None → 実時計 → 全日確定形（契約は UNIX 秒=整数）。② `market_profile_zp._zp_partial_rollup` のメモ化キーが `("partial",symbol,lo,hi)` で now 非依存。①や実時計要求が全日 roll をメモ化すると、同 (lo,hi) の以後の asof 要求がキャッシュヒットで全日形を返し続ける（キャッシュ毒・読み出し時に now 妥当性検査なし）。
- **対策案（明示バグ・即時実施）**: ① replay actor `_asofExtra()` が asof を Math.floor で整数秒化して送出（契約準拠・送信側是正）② `_zp_partial_rollup` のキャッシュ読み出しを「現要求の now で窓完了（hi<=now）のときのみ」にゲート（完了窓 roll は now 非依存で安全・未完了窓は都度計算=ライブ同一）。
## ISSUE-128: zp as-of で「未来セッションの最初の 1 分」が混入（max(1, elapsed) の下限）— 未訪問価格帯バーの一因（ユーザー報告起点）
- **ステータス**: RESOLVED（2026-07-18 是正・検証済み）
- **事象**: ユーザー報告「まだ価格形成されていない部分にバーが形成される」。実測（2026-07-08・安値形成前 asof）: 応答の norm>0 bin はすべて訪問済み帯の内側＝主表示は因果的に正。ただし tpo_units が経過分＋1 になる余剰を確認（例 60 分経過で 62）。
- **原因（実測・コード確認）**: `_zp_day_rollup`/`_zp_partial_rollup` の `col_hi = max(1, min(G, elapsed))` は now が当日開始前（未来セッション）でも下限 1 を与え、窓末尾に食い込む次セッションの最初の 1 分（ffill 現物価格）を観測へ混入させる（as-of 因果違反）。ライブでは未来日が窓に入らないため潜在していた。
- **対策（明示バグ・即時実施）**: 両 rollup に「now < day_start は寄与なし（None）」ガードを追加（当日セッションの寄り付き 1 時間の max(1,·) 挙動＝ライブ既存仕様は不変）。
- **対応日**: RESOLVED（2026-07-18）。検証: 8/26 asof=+4h の tpo_units 62→61（余剰 1 分消失・残る 1 は now を含む進行中の分＝ライブ同一）。7/8 安値形成前 asof で未訪問帯の norm>0 bin=0 維持。pytest 301 全通過（未来日 None ガードの回帰テスト追加）。
## ISSUE-129: 単一時計化 — asof/now の二重時刻を廃止し「リプレイの現在時刻 = to（リビール秒粒度）」へ統一（依頼者承認 y・2026-07-18）
- **ステータス**: RESOLVED（2026-07-18 実装・検証済み）
- **設計**: 基本設計として時計を一元化。`to`（as-seen-at-t の T）がリプレイの現在時刻そのもの。candle 切断（time<=to）はバー粒度でも秒粒度でも同一集合＝主機能の挙動不変のまま、zp は backend が now=to として読む（境界日はライブ同一の経過分クランプ＝日内推移）。ライブは to なし＝実時計＝従来どおりで、as-of 概念の追加実装が構造的に不要になる（ISSUE-125〜127 の asof パラメータ・now 上書き・二重時刻の不整合クラスを根絶）。
- **実装**: backend: controller から asof_ts を撤去し `_handle_zp` が `now=to_ts` を読む（旧 asof 受信は無視＝無害）。server.py／serve_replay→usecase→ports→gateway の asof 透過を撤去。frontend: client の &asof= 撤去、基底 actor の seam を `_clockExtra()`（no-op）へ改称、replay actor は成長 push 中の zp のみ fetch の to をリビール秒（`_clockSec`・floor 整数）へ細粒度化（静止は ctx.to がそのまま時計＝上書き不要）。
- **検証（2026-07-18）**: (1) 実 HTTP: to のみで日内 as-of 推移（tpo_units=1/164/787/1215・md5 全相違）、旧 asof 付与は無視（to と同値）。(2) 実 UI: 静止スクラブ＝to=バー time で部分形、再生中＝to がリビール秒で単調前進（全 200・asof パラメータ消滅・スループット正常）。(3) テスト: pytest 301（market_profile api）＋171（replay_ui）全通過、JS: market_profile web 300 全通過・replay web 232 通過（失敗は既知 ISSUE-121 の 6 件のみ）・indicator_ui web 682 通過（既知 2 件のみ）。バンドル再生成済み。
## ISSUE-130: リプレイ×MP zp でバーが表示されない日がある（日曜ほか 492 バー）— チャート足と backend dataset の 1D 足集合の不一致（ユーザー報告）
- **ステータス**: RESOLVED（2026-07-18 案 A 実装・検証済み・依頼者承認）
- **是正結果（案 A: 足集合の統一）**: ① replay `causal_candle_repository` の時間足化を正典単一入口 `resample_ohlc_tf`（marketdata・1D/1W/1M=セッション日集計＝dataset rollup と同一規則）へ委譲（bridge へ read-only 公開追加・tickvol 集約は維持）② `intrabarWindow` の 1D をセッション窓 [sessionDayStart(cd), sessionDayStart(next)) へ（日曜夕 tick は月曜バーの窓先頭で再生）③ replay actor: 1D バー入場（enterBar）の zp 時計をラベル（セッション 3h 経過点）でなくセッション始端へ写像（窓先頭 3h の先出し防止・growTo/feedTick は実リビール秒のまま）。session_day.js を replay domain へ symlink 追加。
- **検証（2026-07-18）**: (1) 足集合: replay /candles 1D が 4163→3676 本になり dataset と**集合完全一致**（差分 0・日曜バー消滅）。(2) 実 UI（8281・4/27 月曜バー再生）: from=to=セッション始端（日曜 21:00 UTC）から to がセッション終端（月曜 21:00）まで tick 前進し全 200、旧・日曜相当時点（日曜夕+3.5h）で norm>0=15 bin・tpo_units=34＝**従来空だった期間が描画**。バー中途のセッション切替の従来の歪みも消滅（1 バー=1 セッション日に整列）。(3) テスト: replay pytest 172 全通過（日曜畳み込みの回帰テスト追加）・replay web 235 通過（失敗は既知 ISSUE-121 の 6 件のみ・1D 窓テストをセッション窓仕様へ更新・actor 1D 時計テスト追加）。
- **事象**: 特定の日でプロファイルが全く描画されない（例: 2026-04-26 日曜バー・実測 tpo_units=0・グリッド range=1.0-2.0＝空 candles フォールバック）。
- **原因（実測）**: リプレイのチャート足（tick 直リサンプル・カレンダー日 4163 本）には日曜バー等が存在するが、backend dataset の 1D（3671 本）には存在しない（日曜夕データは月曜バーへ畳まれる集計規約・実測: dataset 月曜 O=59874.5=日曜 21:00 始値）。不一致は 492 本（日曜 479＋祝日等 13）。カーソルがこれらのバー上にあるとき、MP の当日窓 [セッション始端, to] に dataset candle が 1 本も入らず `_handle_zp` の空 candles 分岐（price 0/0→グリッド 1.0-2.0・寄与ゼロ）に落ち、プロファイル空＝バー非表示。src=candle でも同窓は bins=0（同根）。
- **対策案（要承認・いずれか）**: A) 足集合の統一（基本設計・本筋）: replay /candles の 1D を dataset と同一集合にする（チャートから日曜バーが消える＝UI 変更）。B) zp の当日窓を candle 非依存化: candles 空でも from/to から窓を構成し価格レンジを as-of tick 範囲から導出（zp のみ・candle 主機能不変・日曜セッションも描ける）。C) 暫定: 空 candles 時に from フィルタを外し直近 candle からレンジ借用（最小変更・窓不整合が残る）。
## ISSUE-131: 価格データ配信の完全同一設計 — リプレイ自前足生成を全廃し dataset（ライブ単一権威）へ完全委譲（依頼者承認・2026-07-18）
- **ステータス**: RESOLVED（2026-07-18 実装・検証済み）
- **設計**: `CausalCandleRepository` の jp225_tick 特例（tick M1 CSV 実行時リサンプル＋独自外れ値補正 `repair_day_outliers`）を全廃し、全 ref・全時間足を `dataset.load_dataframe`（ライブと同一の配信路: 事前生成ロールアップ／1m 末尾 50,000 行 tail・clamp 外れ値補正・mtime キャッシュ）へ委譲。リプレイ固有の追加は tickvol（ISSUE-044 ETA 用・dataset DataFrame の volume 列を additive に写すのみ）だけ。ISSUE-130 で導入した replay 側 resample_ohlc_tf 呼び出しも撤去（bridge_loader 注入で単体テスト可能化＝MarketProfileGateway 同型）。
- **検証（2026-07-18）**: (1) 実測: /candles が 1D=3676・15m/1h=50,000 本すべて dataset と**集合・OHLC 値相違 0**・tickvol 全バー付与。(2) 実 UI: スクラブ→再生→ETA 表示（tickvol 経路）正常・コンソールエラー 0。(3) replay pytest 171 全通過（repository テストを fake bridge 委譲検証へ全面書き換え）。
- **効果**: 足の集合・値・外れ値補正・鮮度管理が全時間足で構造的にライブと同一（設計一致）。残る相違はリプレイの本質差（reveal 切断・tick 再生・as-of 時計）のみ。
## ISSUE-132: /intraday の m1 供給を dataset へ委譲 — 配信路の重複を完全削除（依頼者承認・2026-07-18）
- **ステータス**: RESOLVED（2026-07-18 実装・検証済み）
- **背景（監査 2026-07-18）**: ISSUE-131 後も `IntrabarWindowRepository`（/intraday の m1 素材）だけが生 CSV 全読み＋独自外れ値補正（`_m1_repair.repair_day_outliers`）＋独自 mtime キャッシュの第二経路で、M1 の供給・補正・キャッシュが二重実装だった。
- **是正結果**: ① marketdata/dataset.py に additive API `load_atom_window(ref, start, end)` を新設（全期間原子=_load_base_dataframe・clamp 補正=_clamp_outlier_bars・csv mtime キーの clamp 済みキャッシュ＝供給/補正/キャッシュの単一権威。末尾有界 load_dataframe と異なり任意過去窓へ届く。live サーバは不使用＝D-2 メモリ有界化の不変条件は不変）② IntrabarWindowRepository の m1 を全 ref 本 API へ委譲（jp225_tick 特例・tick_m1_csv・m1_repair・独自キャッシュを撤去。tick parquet フィードはリプレイ固有として現状維持）③ `_m1_repair.py` を削除（利用ゼロ化・しきい値 0.3 の二重定義も解消）④ bridge の resample 系 export 4 件（利用ゼロ）と composition_root の tick_m1_csv 結線を撤去。
- **検証（2026-07-18）**: (1) 実 HTTP: /intraday が過去窓（2025-08-26＝m1 tail 50k の外）で m1=1133 行・直近窓で ticks=159k/m1=1316 行とも ok。(2) 実 UI: 過去日（2025-05-21〜）の 1分OHLC 再生正常・コンソールエラー 0。(3) テスト: dataset.load_atom_window 新規 4 件・intrabar/candle repository テストを委譲仕様へ書換え。replay 170・market_profile api 301・indicator_ui api 375 全通過。
- **残る言語間ミラー（意図的・対で維持）**: session_day py↔js（IANA tz 単一権威）・cap 間引き py↔js（proto bit 一致ペア）。
## ISSUE-133: [SOLID/SRP] MP 計算コア 2 ファイルに複数アクター（数学・キャッシュ協調・運用 CLI・tick I/O）が同居（アーキテクチャ調査 2026-07-18）
- **ステータス**: RESOLVED（2026-07-18 実装・検証済み）
- **調査方法**: architecture-executor によるシステム全体調査（本体系のみ・全所見 file:line 実読で裏付け・自己レビューで裏付け不足候補を棄却済み）。
- **所見（重大度順）**:
  - 【高】`indigators/market_profile/api/market_profile_api/compute/market_profile_zp.py`（692 行）: 統計コア（`minute_close_grid` L109・`compute_zp_profile` L507 等）＋キャッシュ協調（`_MGRID_CACHE` 等 L271-277・`_zp_day_rollup` L358）＋運用バッチ CLI（`warm_zp_cache` L652・`__main__` argparse L682-692）の 3 アクター同居。定量担当の数学変更と運用担当の warm/キャッシュ変更が同一改変面を共有。
  - 【高】`market_profile_dwell.py`（604 行）: 上記 3 系に加え tick I/O 解析（`_load_window_ticks` L154-184 が parquet 読込・tz 変換・外れ値除去まで compute 内で実施）の 4 アクター同居。tick 格納スキーマ変更と dwell 統計変更が同一ファイル。
  - 【中】`simulator/replay_ui/web/js/replay.js` `setupReplay`（L38-484）: 再生制御・ETA 表示（`setEta` L199）・足内アニメ（`animateForming` L363）・MP tick-live 成長駆動（`pushGrowTo` L340 等）が単一クロージャに混在（MP 変更が再生制御の回帰面）。
  - 【中】`replay_market_profile_actor.js`（499 行）: 増分 push 成長（dwell 系）と非増分 as-of coalesce（zp 系）の 2 戦略を `_rebuildAt`/`feedTick` の二重分岐で内包。
  - 【中】`market_profile_controller.py`: `_handle_dwell` L360-385 と `_handle_zp` L436-455 に窓確定ロジック（to/from 切り出し・レンジ・barw→n_bins）がほぼ同型複製。
- **対策案（要承認）**: zp/dwell は純数学 kernel・キャッシュ協調・warmer CLI（→tools/）の分離、`_load_window_ticks` の gateway 移設。replay.js は MP 駆動の独立ドライバ抽出。controller は `_resolve_window` 単一化。actor は Strategy 注入化。
- **棄却済み候補（自己レビュー）**: `marketdata/dataset.py`・`framework/server.py`・`intrabar_window_repository.py`（委譲徹底済み・単一アクター）、`tick_m1.py`/`rollup.py`（単一の data-engineering アクターに凝集）。
- **是正結果（2026-07-18・挙動変更ゼロのリファクタリング）**: ① 統計コア（純数学）を分離＝`market_profile_zp_kernel.py`（分グリッド化・観測占有・Null B モーメント・fine z・POC*・sessions エントリ＋格子定数）／`market_profile_dwell_kernel.py`（セッション認識滞在秒積分・固定グリッドロールアップ・GRID_W）。本体 zp/dwell は全公開シンボルを再エクスポートし呼出面・monkeypatch 面を温存（cache 協調関数は bare name で呼ぶ）。② 運用バッチ warmer を分離＝`market_profile_{zp,dwell}_warmer.py`。CLI（argparse）を `tools/warm_market_profile_cache.py` へ移設。本体 `warm_*_cache` は import 面温存のための薄い遅延委譲に縮退。③ tick I/O 解析（parquet 復号・tz 正規化・外れ値除去・sort）を `TickStorePort.load_window_ticks` へ移設し `MarketdataTickStore` に実装（compute 側 `_load_window_ticks` は単一注入点を温存する薄い委譲）。④ controller の窓確定を `_resolve_window`（+`_ResolvedWindow` DTO）へ単一化（`_handle_dwell`/`_handle_zp` の同型複製を排除）。⑤ replay.js の MP tick-live 成長駆動を `js/replay/mp_growth_driver.js` へ分離（growInFlight・pushGrowTo・settleGrowTo・enterBar/onFormingTick/settleMath/settleBar・await 順序と coalesce 意味論は不変）。⑥ actor の増分 push（dwell）/非増分 as-of coalesce（zp）の 2 戦略を `INCREMENTAL_PUSH_STRATEGY`/`AS_OF_COALESCE_STRATEGY` として抽出しコンストラクタ注入化（`_rebuildAt`/`feedTick` は能力ゲートで戦略選択のみに縮退・feedTick 側の非対称ゲートも温存）。
- **検証結果（2026-07-18）**: pytest 全通過＝market_profile api 301・indicator_ui api 375・replay 170。JS（replay_ui）235 pass / 6 fail（6 は本 Issue と無関係の既存失敗＝session-day-start 由来・リファクタ前後で同一集合＝新規失敗ゼロ）。indicator_ui prototype.html は replay_ui JS を非バンドル（再生成で byte 差分ゼロ＝実測）。移設シンボルの本体再エクスポート／薄い委譲で public import 面を温存（`zp.minute_close_grid`・`mpd.warm_dwell_cache`・`mpd._load_window_ticks`・GRID_W・tf_period の `_zp.*` 等の消費者は無変更で解決＝grep 実証）。
## ISSUE-134: [SOLID/OCP] 種別台帳の属性不足によるハードコード分岐の散在（カレンダー tf・series kind・MP モード）（アーキテクチャ調査 2026-07-18）
- **ステータス**: RESOLVED（2026-07-18 実装・検証済み）
- **是正結果（2026-07-18・承認済み実装）**:
  - 【中・是正①カレンダー tf】`marketdata/resample.py` に `TfDescriptor(NamedTuple){rule, floorable, calendar}` の単一台帳 `TF_DESCRIPTORS` を新設。`TIMEFRAME_RULES`（dict[str,str|None]・名称/型/内容/挿入順を温存した互換ビュー）・`SESSION_TFS`（calendar 導出）・`CALENDAR_LABEL_TFS`（新設・calendar かつ非 floorable 導出）を台帳から導出値化。`tf_meta.NON_FLOORABLE_TF` を floorable 導出へ（名称温存＝forming_bar import 非破壊）。`session_day.next_period_label` の 1M 翌月末手書き算術（pandas ME offset の二重表現）を規則源 `resample.period_label_naive` への委譲へ置換。JS 側 `replay_market_profile_actor.js` の `_FORMING_UNSUPPORTED_TF` は言語跨ぎミラーとして対応関係をコメント明示（py↔js 対で維持）。委譲の byte 一致を月末/年跨ぎ/閏 2 月で実証（`test_next_period_label_delegation.py`）・台帳導出整合を `test_tf_descriptor_ledger.py` で固定。
  - 【中・是正②series kind】`indicator_ui/web/js/domain/series_kind.js` に能力台帳 `SERIES_KINDS{tailUpdatable, seriesType, appliesLineStyle, supportsHeat, overlayReadout, editableLineStyle, renderRoute}`＋未知 kind フォールバック＋`seriesKind()` を新設。`indicator_controller.js`（isTailUpdatable・_draw の kind 別 filter→renderRoute 単一走査）・`chart_renderer.js`（系列定義/lineStyle/heat/overlay 読取/スタイル適用の各分岐）・`properties_dialog.js`（heat 表示/線幅線種編集可否）の raw kind 比較を台帳参照へ置換。各能力は旧 boolean 比較と 1:1 一致。build.mjs MODULE_ORDER 追加・simulator 共有 symlink 追加。
  - 【中・是正③MP モード】`market_profile/web/js/domain/mp_display_mode.js` に表示モード台帳 `MP_DISPLAY_MODES{isNormal, splitByDay, transition}`＋未知 mode フォールバック＋`mpDisplayMode()` を新設。`growth_window.forCurrent`（セッション窓）・`catalog_entry`（tf-period 列/期間 param/src option）・`market_profile_actor._applyMode`（mode 別 if 連鎖→transition 駆動 switch・遷移本体逐語温存・未知 mode の normal 吸収を台帳集約）の raw mode 比較を台帳参照へ置換。`_replayExtra` の 'rolling' は replayBar の anchor モード（別 enum）と明示し表示モード台帳へは含めず（混同回避）。build.mjs MODULE_ORDER 追加・indicator_ui/simulator 共有 symlink 追加。
  - 【低・現状維持】`call_binding.py` の `if self._kind == "btlm"` 分岐・`simulator/main/__init__.py` の `strategy_params` フラット dict は YAGNI 判定済みのため不変更。良例（`dataset_registry.REGISTRY`・`_EA_FACTORIES`・`call_binding._TABLE`）も不変更。
  - **挙動不変の実証**: pytest 全通過（marketdata 190〈+12 新規〉／market_profile api 307／indicator_ui api 375／simulator 1326）。JS: market_profile web 304〈+4 新規・0 fail〉／indicator_ui web 686 pass〈既知 2 失敗のみ・新規失敗ゼロ〉／simulator replay_ui web 235 pass〈既知 6 失敗のみ〉。out/prototype.html 再生成（is-②③追加分のみ・IIFE 構文検証済み）。public import 面（TIMEFRAME_RULES 等価/順序・SESSION_TFS・NON_FLOORABLE_TF・`load()`/バンドル symbol）を grep＋test で無変更確認。台帳属性↔旧比較の 1:1 一致・消費各ファイルの raw 比較不在を test で構造的に固定。
- **調査方法**: ISSUE-133 と同一（全所見 file:line 実読・自己レビュー済み）。registry 化済みの良例（`dataset_registry.REGISTRY`・`_EA_FACTORIES`・`call_binding._TABLE`）は棄却済み。
- **所見（重大度順）**:
  - 【中】カレンダー tf（1W/1M）の派生属性が registry 化されず 5 箇所以上で再導出: `marketdata/resample.py:53`（`SESSION_TFS`）・`:99-106`・`:121`、`marketdata/session_day.py:136-141`・`:150-161`（月末算術を手書き再実装＝`resample.py:123` の pandas ME offset と同一規則の二重表現）、`marketdata/tf_meta.py:28`（`NON_FLOORABLE_TF`）・`:61`、`replay_market_profile_actor.js:32`（`_FORMING_UNSUPPORTED_TF`＝言語跨ぎ複製）。新カレンダー足追加時に全箇所同時修正。
  - 【中】系列出力種別（line/histogram/horizontal_line）分岐がフロント散在: `indicator_controller.js:115`・`:259-261`、`chart_renderer.js:825`（三項ハードコード）・`:833,849,856,1092`、`properties_dialog.js:674`。新 series 種別追加＝3 ファイル同時修正。
  - 【中】MP 表示モード enum（normal/sessions/replay/ticklive/rolling）分岐が 3 層に散在: `market_profile_actor.js:230-265`・`:161`、`growth_window.js:124`、`catalog_entry.js:40,55`。モード追加実績あり（replay/sessions→ticklive/rolling）で変更軸は実在。
  - 【低】`call_binding.py:362` `if self._kind == "btlm"` の呼出規約分岐（単一種のため YAGNI 観点で現状維持も許容）。
  - 【低】`simulator/main/__init__.py:443-459` `strategy_params` フラット dict に各戦略専用 param を混載。
- **対策案（要承認）**: `TIMEFRAME_RULES` を `TfDescriptor{rule, floorable, calendar}` へ昇格し membership set を導出値化・`next_period_label` は `period_label_naive` へ委譲。フロントに kind→描画レジストリ、domain に MP mode descriptor を各 1 つ新設。
## ISSUE-135: [SOLID/LSP] `MarketDataPort.load` の source_ref 事前条件・例外契約が実装間で非対称（composition root が isinstance で補償）（アーキテクチャ調査 2026-07-18）
- **ステータス**: RESOLVED（2026-07-18 実装・検証済み）
- **是正結果**:
  - 【高】source_ref 一意化（LSP）: `MarketDataSourceRepository.load` の `start, end = source_ref` アンパックを撤去。取得窓 (start,end) を構築時パラメータ `window`（keyword-only 必須）へ隔離し、`load(source_ref, ...)` は path 系 3 実装と対称に source_ref を未使用化（path 文字列を受理・置換可能）。`simulator/main/__init__.py` は `load_source` の型別作り分け（`load_source = marketdata_window`）を除去し、全実装へ `data_path` を統一して渡す（委譲 repo 構築時に `window=marketdata_window` を注入）。残る isinstance は委譲経路の DI 選択のみ（LSP 補償でなく composition root の実装選択）。
  - 【中】例外契約対称化: `MarketDataSourceRepository.load` の `fetch_candles` 呼び出しを try/except で包み、fetch 段の失敗（永続実体不在の I/O 例外・CandleSource の fail-fast ValueError 等）を生例外でなく `DataError` へ翻訳（元例外を `__cause__` に chain・path 系 `read_csv_or_data_error`/`ohlc_parquet` と同一のメッセージ+context 規約）。写像段の domain 検証（OHLCInvalidError/TimeOrderError）は try 外で送出を維持（`frame_to_bars` と対称）。
  - 【低】`TickModelPort.ticks_of` docstring に「空列許容（全実装で対称の事後条件・RealTickModel は区間 0 件で空列）」を明記（コード変更なし）。`MarketDataPort.load` docstring にも source_ref/例外の対称契約を明文化。
- **検証結果**: TDD（Red 10 件失敗 → Green 全通過）。simulator 全 1320 passed（baseline 1317＋契約対称性テスト 3 件・replay 約 170 含む）。市場データ委譲のバイト一致 oracle・委譲結線回帰・StopEntryProbe 非委譲回帰いずれも通過（挙動変更ゼロ）。market_profile api＋indicator_ui api 679 passed（回帰なし）。
- **健全性確認（違反でないと判定・不変更）**: `mp_source_capability.js` 能力ゲート・`ReplayMarketProfileActor` 継承 seam・`CandleSource` 事後条件対称は健全のため未変更。
- **調査方法**: ISSUE-133 と同一（ポート定義と全実装の突き合わせ・自己レビュー済み）。
- **所見（重大度順）**:
  - 【高】`simulator/usecase/ports.py:47-53` `MarketDataPort.load(source_ref, ...)` の source_ref が実装 4 種で非対称: CSV パス（`ohlc_csv.py:50`）・TSV パス（`ohlc_mt5_csv.py:72`）・parquet パス（`ohlc_parquet.py:27`）に対し `marketdata_source.py:64-68` のみ `(start, end)` タプルをアンパック。`simulator/main/__init__.py:485-497` が `isinstance(market_data, CsvOHLCRepository)` で分岐し `load_source` を作り分け＝置換不能を型判別で補償。相互差し替えで ValueError/TypeError。
  - 【中】同 Port の例外契約非対称: path 系 3 実装は I/O 失敗を `DataError` へ翻訳（`_ohlc_frame.py:35-43`・`ohlc_parquet.py:30-34`）するが `marketdata_source.py` は try/except なしで生 `FileNotFoundError` が漏出（`marketdata/port.py:51-55` が固有 I/O 例外送出を明記）。
  - 【低】`TickModelPort.ticks_of`（`ports.py:137-143`）: 合成 2 実装は常に非空、`RealTickModel`（`tick_model.py:161-189`）のみ空列を返しうる（Port に非空保証の記述なし・データ実態差の可能性あり）。
- **健全性確認（違反でないと判定）**: `mp_source_capability.js` の能力ゲートは isinstance 判別でなく能力記述子＋Null Object＝LSP 健全。`ReplayMarketProfileActor extends MarketProfileActor` は設計済み seam（`_clockExtra` 等）による Template Method＝安全。`CandleSource` は事後条件対称の模範。
- **対策案（要承認）**: source_ref の型を Port で一意化（パス解決は各実装の構築時パラメータへ隔離）し isinstance 分岐を除去。`MarketDataSourceRepository.load` で DataError へ翻訳し例外契約を対称化。TickModelPort は docstring に空列許容を明記。
## ISSUE-136: [SOLID/ISP] `TickStorePort` の混載と `_indicator_ui_bridge`/dataset 具象の太い依存面（アーキテクチャ調査 2026-07-18）
- **ステータス**: RESOLVED（2026-07-18 実装・検証済み）
- **調査方法**: ISSUE-133 と同一（ポート全メソッド×クライアント呼び出しを Grep+Read で実測マトリクス化・自己レビュー済み）。
- **所見（重大度順）**:
  - 【高〜中】`tick_store_port.py:19-32` `TickStorePort`（day_files/read_ticks/data_dir）が「キャッシュ基点」と「tick ファイルアクセス」を混載。実測: dwell=3/3、zp=2/3（read_ticks 未使用＝否定側 Grep 0 件で裏取り済み）、`tf_period_profile_controller.py:104`=1/3（data_dir のみ）。3 クライアント 3 分化。
  - 【中】`simulator/replay_ui/adapter/_indicator_ui_bridge.py:74-81` の SimpleNamespace（6 メンバ）: intrabar/causal_candle は dataset のみ（1/6）だが `load()` が MP controller 2 本を無条件 eager import＝dataset-only クライアントが MP の import 健全性に巻き込まれる。
  - 【中】具象 `marketdata/dataset.py` の約 5 面を各クライアントが port 迂回で subset 利用（causal_candle=2 面・intrabar=`load_atom_window` のみ・candles_controller=3 面）。`DatasetPort` は 3 メソッドのみで `load_candles`/`load_atom_window` 利用者に狭い抽象が不在。
- **対策案（要承認）**: `DataRootPort(data_dir)`＋`TickReaderPort(day_files, read_ticks)` へ分割。bridge を `load_dataset()`/`load_compute()`/`load_mp_handlers()` の粒度別アクセサへ分割。dataset は `RefValidationPort`/`OhlcSupplyPort` 等の用途別 port 化。
- **棄却済み候補（自己レビュー）**: `DatasetPort` 自体（唯一の port 型消費者 `compute_indicators.py` が 3/3 使用）、`replay_ports.py` 各 Port（usecase と 1:1 で全メソッド使用＝良例）、JS `MarketProfileClient`（forming 分離済み）。※`ContactScanPort`（`replay_ports.py:131`）は実装・呼出ゼロのデッドポート（ISP でなく YAGNI 観点の削除候補として記録）。
- **是正結果（2026-07-18・承認済み実装）**:
  - 【高〜中・是正】`compute/tick_store_port.py` の太い `TickStorePort` を役割別に `DataRootPort`（`data_dir`）＋`TickReaderPort`（`day_files`/`read_ticks`/`load_window_ticks`）へ分割。狭い getter `data_root()`/`tick_reader()` を追加し、単一の注入シーム（`set_tick_store`/`tick_store`/`_STORE`）へ委譲（挙動・自己完結起動を温存）。`TickStorePort` は両者の合成 Protocol として名を温存（既存消費者・既定具象 `MarketdataTickStore` 非破壊）。クライアント再結線: dwell/zp→`tick_reader()`（tick 読取のみ）、`tf_period_profile_controller`/`gateway/composition`（既定 root provider）→`data_root()`（基点のみ）。実測マトリクスに基づく最小分割（dwell 2/2・zp 1/1・tf_period 1/1・composition 1/1 使用）。
  - 【中・是正】`_indicator_ui_bridge.py` の無条件 eager import する `load()`（6 メンバ）を粒度別アクセサ `load_dataset()`（dataset のみ）／`load_compute()`（dataset＋計算 Facade）／`load_mp_handlers()`（MP controller のみ）へ分割。sys.path 準備は共通 `_ensure_paths()` に集約し byte 等価を維持。全 6 非テスト消費者を再結線（causal_candle/intrabar/composition_root→`load_dataset`、causal_compute→`load_compute`、market_profile/forming gateway→`load_mp_handlers`）。`load()` は全面束ねの後方互換 API として温存（既存テスト非破壊）。dataset-only 経路が MP controller を eager import しないことを subprocess クリーン interpreter で実証。
  - 【中・是正】`simulator/replay_ui/adapter/dataset_ports.py` に役割別 狭いポート `RefValidationPort`（`is_known`/`is_known_timeframe`）＋`OhlcSupplyPort`（`load_dataframe`/`load_atom_window`）を新設。dataset 具象の subset 利用クライアント（causal_candle/causal_compute/intrabar/composition_root）を狭いポート型経由の依存へ置換（`marketdata.dataset` が両ポートを構造的に満たすことを `runtime_checkable` isinstance で実測・挙動不変）。棄却済み候補（`DatasetPort` 自体・`replay_ports.py`・JS `MarketProfileClient`・`ContactScanPort` デッド削除）は不変（触らず・YAGNI）。
  - **回帰ガード追加**: `test_tick_store_port.py` に `test_ports_are_split_by_role_isp`（役割別 isinstance の独立成立）／`test_narrow_getters_share_single_injection_seam`／`test_isp_clients_depend_on_narrow_getters`（狭い getter 依存・太い直参照不在の grep）。`test_bridge_isp_split.py`（新規）に各アクセサの面・後方互換 `load()`・`test_load_dataset_does_not_eager_import_mp_controllers`（subprocess 遮断実証）・dataset 具象の狭いポート適合。ISSUE-137 の依存方向ガード（`test_store_gateway_layering.py`）は非退行。
  - **挙動不変の実証**: pytest 全通過（market_profile 307〈+3〉／indicator_ui 375〈不変〉／simulator 全 1326〈+6〉・うち replay_ui 176）。public import 面（`load()`・`TickStorePort` 名・`set_tick_store`/`tick_store`/`_STORE` 注入シーム）は grep＋テストで無変更確認。
## ISSUE-137: [SOLID/DIP] compute（方針層）が永続化 Store 具象を module-level 生成（TickStorePort と非対称・Port 抽象欠落）（アーキテクチャ調査 2026-07-18）
- **ステータス**: RESOLVED（2026-07-18 実装・検証済み）
- **調査方法**: ISSUE-133 と同一（import 全数調査＋Read 確認・TYPE_CHECKING/テスト専用 import は除外済み・自己レビュー済み）。
- **所見（重大度順）**:
  - 【高】`market_profile_zp.py:50`（`from market_profile_api.gateway.zp_store import ZpStore`）・`:259`（`_STORE = ZpStore(...)`）、`market_profile_dwell.py:53`・`:102`（`_STORE = DwellRollupStore(...)`）: compute（内側・方針層＝`tick_store_port.py` docstring で自認）が gateway 具象を module-level で直接 new＝composition root 迂回。同ファイルの tick I/O は `TickStorePort`＋`set_tick_store()` で逆転済みなのに永続化 Store のみ非対称。`test_store_gateway_layering.py` は I/O プリミティブ不在のみ検証し依存方向は素通し。
  - 【中】内側 Port の既定フォールバックが外側具象を名指し遅延 import: `dataset_port.py:48`（`MarketdataDatasetGateway`）・`tick_store_port.py:48`（`MarketdataTickStore`）。`set_*()` 注入シームあり・自己完結起動の意図明示済みの緩和済みトレードオフだが、composition root の責務が内側へ漏出。
  - 【低】`tf_period_profile_controller.py:28` の gateway 具象直 import（外側同士の水平結線＝Dependency Rule 違反ではない・YAGNI 上維持可）。→ **現状維持（触らない）**。
- **健全性確認（違反でないと判定）**: compute→`marketdata.session_day`/`tf_meta` は I/O 非依存の最内側共有カーネル＝適法。simulator の adapter→bridge→具象 peer は adapter 層内で許容。composition_root は全層 import＋Port 注入の教科書的 DI。過剰抽象（YAGNI 違反）は未検出。
- **対策案（要承認）**: compute に `StorePort`（Output Boundary）を新設し ZpStore/DwellRollupStore に実装させ `set_zp_store()`/`set_dwell_store()` 注入へ（TickStorePort と同規律に統一）。Port 既定フォールバックの具象名指しは composition root へ移設（併せて `market_profile_zp_store.py`/`market_profile_dwell_store.py` の互換再エクスポートシム撤去を検討）。
- **是正結果（2026-07-18・承認済み実装）**:
  - 【高・是正】compute に Output Boundary `compute/store_port.py` を新設（`ZpStorePort`/`DwellStorePort` Protocol＋`set_zp_store()`/`set_dwell_store()` 注入シーム＋`zp_store()`/`dwell_store()` getter＋`zp_cache_miss()`/`dwell_cache_miss()`）。既定具象の合成は composition root `gateway/composition.py`（`default_zp_store()`/`default_dwell_store()`/`default_tick_store()`）へ集約。`market_profile_zp.py`/`market_profile_dwell.py` は module-level の `ZpStore(...)`/`DwellRollupStore(...)` 直 new と gateway store 具象の module-level import を撤去し、永続化 I/O を getter 委譲へ統一（設定 provider は compute の module 変数を call-time に読むクロージャ＝monkeypatch 経路と byte 出力を温存）。CACHE_MISS 番兵は gateway 具象クラス属性で identity 一致を維持。
  - 【中・是正】`tick_store_port.py`（MP）・`usecase/dataset_port.py`（indicator_ui）の既定フォールバックを composition root（`gateway/composition.default_tick_store` / `adapter/gateway/composition.default_dataset_port`）への遅延委譲へ移設。ポート本体から具象クラス名（`MarketdataTickStore`/`MarketdataDatasetGateway`）を排除。自己完結起動（注入なし・composition root 非経由でも getter が既定を遅延合成）は温存。
  - 互換再エクスポートシム: `market_profile_zp_store.py`/`market_profile_dwell_store.py` は利用実績あり（`test_market_profile_zp_store.py:178` / `test_market_profile_dwell_store.py:18` / `test_store_gateway_layering.py:63-64` の 3 系統が旧 import パスを利用）＝**利用ゼロを実証できず維持**（撤去せず）。
  - 依存方向ガード追加: `test_store_gateway_layering.py` に `test_compute_has_no_module_level_gateway_store_binding`（compute の module-level gateway store import/new 禁止・関数本体の遅延フォールバックは許容・再エクスポートシムは対象外）／`test_default_store_wiring_lives_in_gateway_composition`（既定結線が gateway composition に集約）／`test_store_port_injection_round_trip`（set_*/未注入の getter 挙動）を追加。
  - **挙動不変の実証**: pytest 全通過（market_profile 304 / indicator_ui 375 / replay 170）。自己完結起動（set_* 注入なし・composition root 非経由の直接 compute import）で `zp_store()`/`dwell_store()` が既定具象を返し `_CACHE_MISS` が gateway クラス属性と identity 一致することを clean interpreter で実証。public import 面（旧 import パス・互換シム identity）を grep＋import smoke で無変更確認。
## ISSUE-138: [仕様不整合] リプレイ MP 日別（sessions）プロファイルがライブ同一指示に反しバー粒度でしか育成されない（足内ティック育成なし）（2026-07-19）
- **ステータス**: RESOLVED（案C 実装完了・2026-07-19）
- **事象**: ライブ（present）の日別プロファイルはティック到着で足内育成されるが、リプレイは バーリビールごとの to=バー時刻 as-of 再集計のみ＝足内で伸びない。ユーザー指示「リプレイはライブと同一挙動」に対する不整合。
- **原因（実測確定・推測なし）**:
  - ライブの足内育成の実体は tf-period 経路のみ（`tf_period_profile_controller.py:181-196` の `_merge_live_tail`、供給源＝served in-memory `LiveTickBuffer`、`server.py:334-335`）。自前描画経路（`handle_market_profile`）はライブでも live_ticks 非対応＝バー粒度。
  - リプレイサーバ（`serve_replay.py`）は `/tf_period_profile`・`/live_ticks` を配信せず、tf-period actor もリプレイ composition root で未配線 → 「front 配線のみ」では不成立。
  - backend 足内 as-of 対応状況: zp=対応済み（`_handle_zp` now=to、分粒度）／dwell=未対応（now 非伝搬）／candle=OHLC ゆえ原理的に足内不可。
  - front: 足内 tick 秒（`replay.js:399-400` secs[i]）と単一時計 `_clockExtra`（`replay_market_profile_actor.js:394-400`）は存在するが `isGrowingPush()`（非 sessions）ゲートで sessions から遮断。
- **対策案（要承認・いずれか）**:
  - 案A: ライブ厳密一致（tf-period 経路一式をリプレイへ移植: `/tf_period_profile` エンドポイント＋リプレイ tick 源による live tail 合成＋front 配線）。
  - 案B: 自前描画経路の一致のみ（リプレイの refresh トリガーを毎ティック化。ライブ自前描画と同一＝見た目はバー粒度成長）。
- **案C副作用調査結果（2026-07-19 実測・要旨）**: front は replay 専用ファイルのみで成立（基底 `market_profile_actor.js` は symlink 共有ゆえ無改変、`_clockExtra` seam で to 上書き可）。src 別: zp=backend 無改変で足内成立（now=to 対応済み・当日キャッシュ書込なし）／dwell=now=to では足内化せず（窓端 t1+bar_sec がバー粒度、`market_profile_dwell.py:381`）、足内化には共有 backend の窓端変更が必要＝present byte 変化リスク（opt-in 遮断が前提）／candle=OHLC ゆえ原理的に不可。要対処: throttle 追加（ライブ 5s 規約に一致）・逆スクラブ時の `_clockSec` 巻戻し（いずれも replay ファイルで回避可）。
- **是正実装（案C・2026-07-19）**: `replay_market_profile_actor.js` のみ変更（基底 md5 不変 `73ce37db…`／driver・replay.js・composition・backend Python・present すべて無改変）。(1) `_usesAsOfClock()` 述語を追加し「日別 sessions（機構A）または 成長 push×非増分 src（zp）」を非増分 as-of coalesce 戦略へ振り分け（!_sessions のとき従来ゲート `isGrowingPush()&&!incremental` に厳密一致＝非 sessions 1 byte 不変）。(2) `feedTick`/`_clockExtra` のゲートを本述語へ統一（sessions も単一時計 `_clockSec` を足内前進＋to へ細粒度化）。(3) `onLiveTick`（reveal seam onLiveRecompute が毎バー呼ぶ as-of refresh のバー入場点）で sessions のみ `_clockSec` をリビールフロンティア（`getContext().to`）へ直接巻き戻し＝後退スクラブの stale 未来時計を遮断（zp enterBar 巻き戻し規約と同義）。throttle は追加せず既存 coalesce（`_scheduleNonIncrementalRefresh` の busy/pending・latest-wins）に委譲（依頼確定・案C(c)）。dwell/candle は backend 制約で従来のバー粒度を維持（front は正しく細粒度 to を送るが zp のみ足内成長＝非退行）。テスト: `replay_market_profile_actor.test.js` に 4 ケース追加（sessions 前進・coalesce 発火・逆スクラブ追随・非 sessions 回帰ガード）。replay_ui JS 239 pass（既存 6 fail は sessionDayStart NY ET を naive-UTC で期待する既存テスト不備＝本件無関係・不変）／replay_ui Python 176 pass。
## ISSUE-139: [表示] market_profile 表示時にチャートペイン上端へ小さな数字列が横一列に canvas 描画される（2026-07-19 実UI検証中に発見）
- **ステータス**: RESOLVED（2026-07-25・切り分けの結果「バグでなく既存の意図的な日付ラベル」と確定。ユーザー裁定「現状維持（許容）」＝仕様確認済みで CLOSE。コード変更なし）
- **事象**: リプレイ UI（日足・日別プロファイル選択状態）で、ペイン上端に 2 桁程度の数字列が全幅にわたり描画される。btlm_trail 検証中に発見したが、MP の 👁 非表示で消失し btlm_trail 非表示では消えない＝**発生源は market_profile**（canvas 描画・DOM ではない実測）。
- **未確定**: 発生条件（ズーム・モード依存）と起源（既存挙動か ISSUE-138 是正の影響か）は未切り分け。ISSUE-138 変更はトリガー粒度のみで描画コード無改変のため既存挙動の可能性が高い（未検証・要バイセクト）。
- **切り分け結果（2026-07-25・読み取り専用調査＋git 実証）**: 発生源は `indigators/market_profile/web/js/adapter/front/market_profile_primitive.js:347-349`（`_drawSessions`）の**セッション各列の営業日ラベル「MM-DD」描画**（`ctx.fillText(all[i].date.slice(5), left+3, 4)`・`textBaseline='top'`・y=4＝ペイン上端）。数字＝日付（TPO 数/価格ではない）。sessions（日別プロファイル）モード限定で、日足は 1 ローソク=1 営業日ゆえ列が密集し全幅の数字帯に見える（ズームで見え方が変わるだけ・描画は視野内列のみ）。**起源＝既存挙動で確定（ISSUE-138 非由来）**: 当該描画は cf59215（2026-07-02 sessions 初版）以来不変、ISSUE-138 是正 13d9201 は `replay_market_profile_actor.js` の 2 ファイルのみ変更で primitive.js 無改変（`git show`/`git blame` 実証）。＝**バグではなく意図的な日付ラベル**（密集時の視認品質のみ課題）。対応（間引き/削除/位置変更）はいずれも **UI 変更＝要承認**。修正候補: (A)密集時のみラベル間引き（隣接間隔 < ラベル幅でスキップ）(B)ラベル opt-in 化/削除。裁定を仰ぐ。
## ISSUE-140: [表示] btlm_trail の読取専用系列（β・σ・被覆率）の価格軸ラベルが軸下部に露出する（2026-07-19 ユーザー報告）
- **ステータス**: RESOLVED（2026-07-19・commit 8f29355）
- **事象**: 価格軸レンジに 0 付近が入る状態（手動スケール等）で、価格軸下部に btlm_trail_sigma/beta/coverage の軸ラベルが表示される（ユーザー実UIスクリーンショットで確認・表示モード=ライン）。
- **原因（実測・是正）**: `/compute` payload は正常（readout_only 付与済み）。`lastValueVisible`/`priceLineVisible` は既に全系列 false（chart_renderer L808-809）だった。露出ラベルは系列 *名*（=`series.title`・"btlm_trail_sigma"等）で、lightweight-charts bundle 実装上 title ラベルは lastValueVisible とは独立に価格軸へ描画される（軸レンジが系列値域を含むと露出）。当初仮説（lastValueVisible 未無効化）は実コードで否定。
- **対策（実施済）**: readout_only 写像へ `title=''`（名前ラベル抑止＝根本）＋ `crosshairMarkerVisible=false` を追加。`lastValueVisible`/`priceLineVisible=false` も明示（globals 変更耐性）。autoscale 契約 `() => ({ priceRange: null })` は bundle 消費コード（ki.Ph）で範囲寄与なしと確認済＝現状維持。renderer テスト追加・bundle 再生成。
- **検証**: web 686 pass（既存死滅2のみ）・renderer 単体で title/lastValueVisible/priceLineVisible/crosshairMarkerVisible=false を固定。実ブラウザ最終確認は親会話。
## ISSUE-141: [仕様不整合] btlm_trail 経験分位の窓が設計書「当該バー除外」に対し実装は当該バーを含む（2026-07-19 検出）
- **ステータス**: RESOLVED（2026-07-19）
- **事象**: 設計書 `BTLM_TRAIL_BASIC_DESIGN.md` §4.3/§7.2 は経験分位を「当該バー除外の直近 N 本」で定義（実証時の被覆率 88.6% も同手法で測定）。実装 `_empirical_quantile_causal` は `deviations[start:t+1]`＝当該バーを含む。外れ値分位（q_out）実装時にエージェントが矛盾として検出・報告。
- **影響**: 因果性・非リペイントは維持されるが、当該バー自身の乖離が自分を判定する分位に混入（N=500 で約 1 ランク＝軽微だが、実証済み手法・設計書定義と不一致）。
- **対策（実施済）**: `_empirical_quantile_causal` の窓を `deviations[max(0,t-N):t]`（当該バー除外＝`d_{t-N}..d_{t-1}`）へ是正。経験分位バンド本体・外れ値分位（q_out）の両方に適用（規約一致）。設計書 §4.3 は現行どおり（F-08 の当方追記のみ「当該バー除外」へ整合）。
- **検証**: 自己参照遮断テストを追加（source=open で mean を close 変更から独立させ、当該バーの乖離のみ変更→当該バーの帯/off が不変・後続バーは変化を固定）。btlm_trail 35 pass・api 387 pass・web 686 pass（既存死滅2のみ）。commit で是正。
## ISSUE-142: [リプレイ/退行] 時間足切替の replay 再駆動が共有メニュー化で断線し、分・時間足で指標系列の末尾が欠落（2026-07-19 ユーザー報告）
- **ステータス**: RESOLVED（2026-07-19・実UI検証済み）
- **事象**: リプレイ UI で日足以外（1m〜4h）へ切り替えると、btlm_trail 等の指標系列が当日 00:00（旧時間足バーの時刻）で途切れ、以降のローソクに描画されない。日足は無影響。ユーザー実UIスクリーンショットで報告。
- **原因（実測確定）**: `replay.js` の時間足クリック結線が旧静的ボタン前提の `.tb-interval` セレクタのまま。ISSUE-122/123（c1389e1）の共有 TimeframeMenu 化でトリガー（`.tb-interval`・`data-timeframe` なし）と項目（`.tf-menu-item`・`data-timeframe` あり）に分離されたため、(1) メニューを開くトリガークリックで `loadTimeframe(undefined)` が走り `/candles?timeframe=undefined`→500・`candles=[]`・slider max=0＝再生駆動が破壊、(2) 項目クリックは共有 `[data-timeframe]` 配線（setTimeframe→recompute）のみが処理し、`untilTime` が旧時間足の最終駆動バー時刻（日足=当日 00:00）のまま `/compute` へ送信→ `truncate` が 00:00 以降の足を計算から切断。日足はバー時刻が常に 00:00 のため `untilTime` 陳腐化が可視化されない（＝「日足だけ問題ない」）。実ブラウザで /compute body `untilTime:1784246400`（2026-07-17 00:00）と 500 応答を実測確認。計算層（btlm_trail compute）は時間足非依存で正常（1D/1h/5m とも df 末尾まで出力・実測一致）。
- **対策（実施済）**: `replay.js` の結線セレクタを `.tb-interval` → `[data-timeframe]`（共有 bind と同一）へ是正。トリガーへの誤結線が消え、項目クリックで従来どおり 60ms 遅延の `loadTimeframe(tf)` 再駆動（candles 再取得・slider 再設定・present バーへ drive＝untilTime 更新）が復元される。replay 専用ファイルのみ変更（共有 base・present は無改変）。
- **検証（実測）**: 実UI（Playwright・実HTTP・稼働中サーバ 8280）で確認。(1) 1h 切替後 `timeframe=undefined` の 500 消失・slider 上限 1499 復元・`untilTime=1784318400`（2026-07-17 20:00＝1h 最終バー）で /compute 送信・btlm_trail 全系列（mean/q5/q95/off_hi/off_lo＋読取3種）が 20:00 まで到達（スクショ fix-1h-midreplay.png: bar 1389 リビール途中でも最前線まで追従）。(2) 5m 切替でも untilTime=2026-07-17 20:10（5m 最終バー）・右端まで描画（fix-5m.png）。(3) 1週プリセット→1足送りの再駆動も正常。replay_ui web `node --test`: 236 pass / 9 fail＝修正前ベースラインと完全一致（既存不備・本件無関係）。計算層は無改変（1D/1h/5m とも compute 単体で df 末尾一致を事前実測済み）。

## ISSUE-143（btlm_trail_marod σ/分位バンドの分位線が視認できない）
- **ステータス**: RESOLVED（2026-07-20・実UIスクショ確認済み）
- **事象**: オシレーターペインで MAROD の分位バンド（q5/q95）が「表示されていない」とユーザー報告。
- **原因（実測確定）**: 2点。(1) ユーザー稼働サーバ（ポート8000・08:05起動）が新バンド実装の**前の旧コード**のままで、`/compute` が `btlm_trail_marod` 本体1本のみ返す（σ・分位とも不在）＝要サーバ再起動。実測: 8000 `/compute` line series=['btlm_trail_marod']・`/catalog` marod keys=color/maxbars/source（window_n/q_low/q_high 無し）。(2) 新コードでも分位線の色が `rgba(120,120,180,0.9)`（青紫系）で MAROD 本体（紫 rgba(123,104,238)）とほぼ同系＋点線細線のため、描画されていても視認不能。実測: 実UI pixel走査で quantile=3312px 描画済み（＝存在するが見えない）。
- **対策（実施済）**: `btlm_trail_marod/src/lwc_chart.py` の `_COLOR_QUANTILE` をシアン `rgba(38,198,218,1)`、`_COLOR_SIGMA` を明橙 `rgba(240,160,70,1)` へ変更し、MAROD（紫）/σ（橙）/分位（シアン）を判別可能に。計算層・系列名・パラメータは無改変（色のみ）。
- **検証（実測）**: 新コードサーバ(8100)実UIで q5/q95 のシアン点線がペインを横断表示されることをスクショ確認（marod-fixed.png）。marod 全21テスト・binding 4テスト緑。**残対応: ユーザーの 8000 サーバ再起動で新コード反映が必要**（コード修正のみでは稼働中プロセスに未反映）。

## ISSUE-144（btlm_trail_marod バンドが 0% 基準に対し非対称で“おかしく見える”）
- **ステータス**: REVERTED（2026-07-20・ユーザー要請で修正前へ復旧）。「q5」は分位5%水準のラベルにすぎず（値ではない）、非対称の符号付き経験分位表示で問題ないとの結論に基づき、0中心・対称化（下記対策）を撤回。core を修正前（分位＝符号付き経験分位／σ＝ローリング平均±mult·σ）へ戻した。色（ISSUE-143）は維持。
- **事象**: MAROD の基準は 0%（乖離ゼロ）なのに、分位バンド（q5/q95）が符号付き MAROD の経験分位で左右非対称に表示され「0中心の対称バンドでない」＝おかしく見えるとユーザー指摘（バグ扱い）。σ帯もローリング平均中心で 0 中心でなかった。
- **原因**: 初版はゼロ基準を反映せず、分位＝符号付き MAROD の (q_low, q_high) 経験分位、σ帯＝ローリング平均 ± mult·σ（いずれも 0 中心でない）。
- **対策（実施済）**: 両バンドを **0 中心・対称** へ是正（`btlm_trail_marod/src/core.py`）。(1) 分位: `±X`, X=`|MAROD|` の被覆率 `coverage=q_high−q_low`（既定0.90）分位。(2) σ: `0 ± mult·σ`（σ=ローリング標本標準偏差, ddof=1）。パラメータ集合（q_low/q_high/window_n）・系列名・カタログ契約は無改変（core 計算のみ変更）。σ の 0 中心化は以前の mean±2σ 選択をユーザーの 0 基準原則へ合わせる変更。
- **検証（実測）**: jp225 日足/15m で `lo == −hi`（対称）を最大差 0.00e+00 で確認。marod 全21テスト緑（対称性テスト追加）。実UI スクショで 0 中心対称の帯を確認予定。

## ISSUE-145（リプレイでオシレーター btlm_trail_marod の足内更新粒度が足と異なり「途中経過が見えない」）
- **ステータス**: RESOLVED（2026-07-20・実原因＝足内更新対象への未登録。修正実施済み・実UI検証は下記）
- **実原因（確定）**: 再生中、最新足は tick 単位（足内 forming）で更新されるが、MAROD オシレーターは足確定時に最終値へジャンプするだけで途中経過が見えない（ユーザー報告：「最新足とオシレーターの更新粒度が違う／最新結果しか表示せず過程を検証できない」）。原因は `simulator/replay_ui/web/js/adapter/front/replay_indicator_controller.js` の足内更新対象集合 `INTRABAR_FORMING_IDS` に `btlm_trail_marod` が未登録で、`recomputeFormingLatest`（形成中バーの末尾点差分再計算）の対象外だったこと。
- **対策（実施済）**: `INTRABAR_FORMING_IDS` に `'btlm_trail_marod'` を追加（1行）。再生中に MAROD 線の末尾点が足内 tick で追従し過程を可視化。σ/分位バンドは当該バー除外の因果窓ゆえ非リペイントで据え置き（profit_* 6 指標と同一規約）。
- **迷走記録（正直）**: 当初「リプレイ計算窓が bar+1 でライブと乖離＝ローリング warmup 不足」と誤診し `replay.js` の計算窓を固定1500へ変更したが、これは履歴スパンの話で足内更新粒度とは無関係＝的外れ。反実仮想（limit=61）でも 1D ではバンドが 57/61 有限で「当日だけ」を再現できず誤診が露見。当該窓変更は撤回（revert）済み。
- **検証（実UI・実HTTP・実 jp225_tick）**: リプレイ中間 bar 800 で本番メソッド `recomputeFormingLatest` を形成中バーの終値を変えて実行し、MAROD 末尾点が追従（終値31820→MAROD1.135／32085→1.943／32350→2.751）＝足内 tick で過程が可視化されることを確認。修正前は確定値2.598に固定。replay_ui web `node --test` 236 pass/9 fail＝修正前ベースラインと一致（回帰ゼロ・失敗9件は既存 MP/catalog 系で本件無関係）。`replay.js` の誤診修正は完全 revert（git 差分ゼロ）。

## ISSUE-146（indicator_ui web の replay_analysis / timeline_player テストが参照先モジュール不在で常時失敗）
- **ステータス**: RESOLVED（2026-07-25・ユーザー裁定「テスト2本を撤去」。復元元が git 全履歴に不在で実装復元不能のため案(b)採用）
- **是正（2026-07-25）**: 参照先 usecase 不在の `replay_analysis.test.js` / `timeline_player.test.js` を削除。検証: indicator_ui web 742/742 緑（2 件失敗が消滅・回帰ゼロ）。
- **追加調査（2026-07-25・git 全履歴）**: `js/usecase/timeline_player.js` / `replay_analysis.js` は **git 全履歴（`--all`）に一度も存在しない**（テスト 2 本のみ 79982b8「未追跡ソース保全コミット」で入り、対応 usecase 実体は未コミット）。現行コードに同機能の別名実装も無し（grep 実測）。⇒ 対策案(a)「モジュール復元」は復元元が存在せず、実体はテストから起こす**新規実装＝スコープ外**になる。現実解は(b)テスト 2 ファイル撤去（＝既存ファイル削除＝要承認）。裁定を仰ぐ。
- **事象**: `indigators/indicator_ui/web/tests/replay_analysis.test.js` と `timeline_player.test.js` が `ERR_MODULE_NOT_FOUND`（`js/usecase/replay_analysis.js` / `timeline_player.js` が存在しない）で常時失敗する（`npm test` 716 件中 2 件）。
- **実測（2026-07-21）**: git stash による HEAD 復元状態でも同一 2 件が失敗＝ma_marod 追加とは無関係の既存問題。両テストは保全コミット 79982b8（未追跡ソースの一括コミット）で入ったが、対応する usecase モジュールはコミットされていない。
- **対策案**: (a) 対応モジュールの実体を復元コミットする、または (b) 参照先が存在しない 2 テストを撤去する。いずれもユーザー裁定が必要（承認待ち）。

## ISSUE-147（replay_ui web の catalog 系テスト 3 件が指標数 20 の陳腐化した期待値で常時失敗）
- **ステータス**: RESOLVED（2026-07-25 修正・検証済み。実カタログ実測＝23 に追随。replay_ui web 全緑・回帰ゼロ）
- **是正（2026-07-25・テストのみ）**: 実測（`listForView`）で all=23／indicator=22／strategy=0／profile=1 を確定し、`catalog_client.test.js`（listIndicators 20→23）・`facade.test.js`（listForView all 20→23・indicator タブ 19→22）を更新（indicator_ui 側 catalog テストと同期）。新規 3 指標（btlm_trail/btlm_trail_marod/ma_marod）はいずれも indicator タブ。
- **事象**: `simulator/replay_ui/web/tests` の `listIndicators returns the 20 registered indicators` / `UC-01 listForView: empty filter returns all 20` / `UC-01 listForView: filters by tab` が失敗する（期待 20＝基本4+profit_*15+market_profile）。実カタログは btlm_trail / btlm_trail_marod / ma_marod 追加後で 23 指標（indicator_ui 側テストは 23 で更新済み・全通過）。
- **実測（2026-07-21）**: 今回のイベント分位共有化はパラメータ・系列定義のみで指標数は不変＝本件と無関係。残る 6 件の既存失敗は ISSUE-121 記録済み（sessionDayStart 期待値不備ほか）。
- **対策案**: replay_ui 側の 3 テストの期待数を 23（indicator_ui カタログと同期）へ更新する。実施はユーザー裁定（承認待ち）。

## ISSUE-148（チャートで過去へ遡って時間スケールを拡大すると最新足の位置へ戻る）
- **ステータス**: RESOLVED
- **事象**: 過去へスクロールした状態でホイールズーム（時間スケール拡大）すると、表示が最新足の位置までジャンプする（ライブ・リプレイ共通。実 UI で再現確認: 2022–2024 表示→ズーム→右端が 2026-07 最新足へ）。
- **原因**: `chart_renderer.js` の右端余白同期 `_syncRightOffset`（ISSUE-114/115）が可視範囲購読（ズームで barSpacing 変化）から `timeScale.applyOptions({rightOffset})` を再適用する。lightweight-charts では rightOffset の適用が「最新足基準へのスクロール位置設定」として働くため、過去閲覧中の再適用が最新足へのジャンプになる。
- **対策（実施済み 2026-07-22）**: `_syncRightOffset` に過去閲覧ガードを追加——`timeScale.scrollPosition() < -0.5`（最新足が右端より先＝過去閲覧中）のときは rightOffset を適用しない。右端復帰（スクロール/FOLLOW）で購読が再発火し余白は再同期される。chart_renderer は replay へ symlink 共有のため両 UI 同時に修正。
- **検証**: 単体テスト追加（過去閲覧中スキップ・右端復帰で適用）＋実 UI 再現手順の再実行でジャンプ消失を確認。chart_renderer.test.js 全通過。

## ISSUE-149（オシレーター系インジケーターを更新すると pane の位置（並び順）が変わる）
- **ステータス**: RESOLVED
- **事象**: 設定ダイアログ（⚙）でパラメータを変更して OK すると、当該指標の pane が最下段へ移動する（実 UI 再現: btlm_trail_marod（上）＋ma_marod（下）で btlm_trail_marod を更新→ma_marod の下へ移動。ライブ・リプレイ共通）。
- **原因**: 再計算の redraw 経路（`indicator_controller.js` `_renderInstance`）が「全除去（`remove`＝`removePane`）→再描画（`addPane` で末尾へ新規追加）」のため、更新のたびに pane がチャート最下段へ付け直される。
- **対策（実施済み 2026-07-22）**: `chart_renderer.remove()` に `keepPane` オプションを追加——pane・watermark・slot を温存して系列のみ除去し（scaleHost/priceLineHost は初期化）、redraw は `_ensurePane` の既存 pane 再利用により同じ位置へ再生成される。再計算経路のみ `remove(id, { keepPane: true })` に変更（完全削除・ゾンビ掃除は従来どおり全除去）。共有ファイルのため両 UI 同時に解消。
- **検証**: 単体テスト追加（keepPane で pane 数・同一実体・位置維持／既定は従来どおり除去）＋実 UI 再実行で並び順維持を確認（リプレイ 2 pane・ライブ recompute）。web 全 718 中 716 通過（残 2 件は ISSUE-146 既知）。

## ISSUE-150（ライブ: オシレーター pane の価格軸手動スケールが 60 秒再計算で解除される）
- **ステータス**: RESOLVED
- **事象**: btlm_trail_marod 等の pane の右価格軸をドラッグして手動スケールにしても、再計算（remove+redraw）で自動スケールへ戻る。**ライブ（60 秒周期再計算）・リプレイ（足送りその場計算）の両モードで実 UI 実測・同一挙動を確認（2026-07-22）＝モード間の設計差異なし（共有経路の同一欠陥）**。メイン価格軸の手動スケールは lwc が保持し解除されない＝pane との非対称。
- **原因（推定・要実証）**: 再計算 redraw は pane 内の全系列を除去→再追加する。pane の priceScale の手動状態は系列全除去で失われる（メインは mainSeries が除去されないため保持される）。
- **対策案**: redraw 前に当該 pane の priceScale 状態（autoScale/visibleRange）を退避し、再追加後に復元する。ISSUE-149 の keepPane 機構に退避/復元を追加する形で実装可能。承認後に着手。

## ISSUE-152（indicator_ui/serve.sh のデータ watch がシステム python3 起動で即死し確定足が伸びない）
- **ステータス**: RESOLVED
- **事象**: serve.sh がデータ更新（acquire_marketdata）と watch 2 種（export_jp225_m1 --watch / live_tick_watch）を `python3` で起動するため、pandas を持たないシステム python3 環境では ModuleNotFoundError で即死し、確定足・tick 素材が伸びない（＝ライブでチャート/指標が更新されない複合要因。venv が PATH にあるシェルからの起動では顕在化しない環境依存バグ）。
- **対策（実施済み 2026-07-22）**: 3 箇所の `python3` を既定義の `"$VENV_PY"`（pandas 入り venv）へ変更。
- **検証**: 修正後の serve.sh 起動で watch 2 プロセスの生存とログ正常を確認。

## ISSUE-151（ライブ 1 分足で btlm_trail（非登録・帯系指標）が数バー更新されない）
- **ステータス**: RESOLVED
- **事象**: ライブ 1 分足でローソクは進むのに btlm_trail の mean/バンドが数バー前で停止する（ユーザー報告・スクリーンショットあり）。
- **原因（複合 3 層）**: ①統一設計移行時、バー確定の検知が LiveUpdater（60 秒タイマー）のみで、tick 駆動の末尾差分再計算と衝突すると isRecomputing スキップで確定イベントを取り落とし飢餓する（非登録指標は full 再計算でしか動かないため停止して見える）。②検証用サーバの生起動（framework/server.py 直接）がデータ watch を欠き確定足が伸びない（→ISSUE-152 で serve.sh 側も修正）。③臨時ポート乱立で新旧コード・新旧プロセスの画面を混視。
- **対策（実施済み 2026-07-22）**: (1) LiveTickPlayer に onBarClose フック（tick の期間ロールオーバー＝バー確定で即 full 再計算要求）。(2) controller に requestFullRecompute（coalesce/pending・forming 完了時に必達ドレイン＝取り落とし構造の排除）。(3) LiveUpdater は補完網へ格下げ（新確定足検知時に requestFullRecompute 要求）。(4) ポートをライブ=8000/リプレイ=8280 に固定・serve.sh 起動を必須化（メモリに恒久記録）。
- **検証**: 単体テスト（onBarClose 発火・coalesce・pending ドレイン・バー境界 full）全通過＋実 UI（8000・1 分足）で btlm_trail/btlm_trail_marod が最新バーまで追従・継続更新を確認。

## ISSUE-153（ページ読込直後に適用した指標が restore に上書きされ「描画だけ残る孤児」になる）
- **ステータス**: RESOLVED
- **事象**: ページ読込〜復元（restore）完了までの数秒間にダイアログから指標を適用すると、compute・描画は行われるが直後の restore が `_state` を保存済みスナップショットで丸ごと置換するため、適用した instance が状態・凡例から消える。系列だけがチャートに残り（孤児描画）、以後どの再計算（バー確定 full・足内末尾差分・60 秒補完）にも乗らず凍結する。ISSUE-151 の「btlm_trail が更新されない」の主要因（どの指標・どの時間足でも発生。ライブ/リプレイ共通の共有経路）。
- **原因**: `restore()` が `this._state = deserialize(storage)` の全置換で、読込後に適用済みの in-memory instance を保持しない。apply と restore の順序保証がなかった。
- **対策（実施済み 2026-07-22）**: restore 実行中 Promise（`_restoreInFlight`）を導入し、`applyIndicator` は復元完了を待ってから適用する（競合排除・適用は必ず復元後の状態に積まれる）。
- **検証**: 単体テスト（復元中の適用は完了待ち）＋実 UI（8000・1 分足）で復元後適用 → `btlm_trail#1` の永続化・凡例登録・バー確定 full 再計算への参加を確認。

## ISSUE-151 追補（2026-07-22 深夜）: バー確定検知の二重化と飢餓の完全排除
- **経緯**: 修正後もユーザー環境で「足内の孤立末尾バーのみ動き、確定バーが埋まらない」症状が継続（末尾差分は稼働・バー確定 full のみ不発＝第 1 検知経路 LiveTickPlayer.onBarClose がセッション状態により沈黙するケース）。
- **追補対策（実施済み）**: ①第 2 検知経路: FormingBarUpdater が /forming_bar の period（bar.time）前進＝バー確定で requestFullRecompute を要求（player 死亡セッションでも駆動）。②LiveUpdater の検知を isRecomputing 非依存化（旧: 再計算中は tick 全体スキップ＝高頻度 forming 下で 60 秒検知が連続被弾し飢餓。新: 検知＋full 要求は毎 tick 必達・価格反映のみ回避）。
- **検証**: 単体（period 前進発火・再計算中でも検知継続）＋実 UI 継続観測。

## ISSUE-151 追補2（2026-07-22 深夜）: 一時障害での full 要求喪失と stale 点によるバッチ中断
- **経緯**: ユーザー DevTools 実ログで 2 欠陥を確認。①`requestFullRecompute 失敗: Failed to fetch`＝バー確定 full がフェッチ一時障害（単一スレッドサーバへの多重クライアント負荷・応答切断 ERR_CONTENT_LENGTH_MISMATCH）で捨てられ、当該バーの確定描画が欠落。②`Cannot update oldest data` 多発＝バー確定直後の full 再描画と確定前発行の latest 応答の交錯で stale 点が lwc 例外を投げ、末尾更新バッチ全体が中断（残り系列まで未更新＝停止に見える）。検証用ブラウザの同時アクセスが負荷要因だった点も特定（以後、ユーザー使用中のサーバへ計測ページを並走させない）。
- **対策（実施済み）**: ①full 失敗は pending に保持し次の外部トリガ（forming 完了ドレイン・約 2〜5 秒後）で再試行（即時自己リトライなし＝恒久障害時のタイトループ防止）。②updateSeriesTail は点単位 try/catch で stale 点のみ破棄しバッチを最後まで適用。
- **検証**: 単体（失敗→pending→再試行・即時リトライなし）全通過。web 726 中 724（既知 2 件のみ）。

## ISSUE-154（ライブ再計算のサーバ処理が遅くシングルスレッドが飽和・表示が累積遅延する）
- **ステータス**: RESOLVED
- **事象**: 1 分足 1,500 本で marod 系 1 リクエスト 0.84 秒。tick 駆動再計算×適用指標数でシングルスレッドサーバ（rpy2 制約で threading 不可）が飽和し、処理待ち行列で全指標の表示が数バー累積遅延（ユーザー実環境で発生・検証環境は指標数が少なく境界内＝再現差の正体）。
- **原因（プロファイル実測）**: ①最大: 系列 JSON 直列化が pandas iterrows（0.64 秒/リクエスト）②因果ローリング分位の純 python ループ（~0.4 秒）③イベント分位のバー毎 numpy 呼出（~0.25 秒）＋バンド二重計算・非描画 _all の計算。
- **対策（実施済み 2026-07-22 深夜・全て出力完全一致）**: ①fake_chart._line_points を配列一括変換へ ②_rolling_causal_fast（sliding_window_view＋nan 集約・満杯窓ベクトル化、部分窓のみ従来ループ。ループ版との全一致を回帰テストで恒久固定）③イベント分位を「観測数ごとの水準テーブル」方式へ（水準は確定観測数のみに依存する性質を利用）④アダプタからバンド受け渡し＋include_all=False（戻り値の形は不変）。
- **効果（実測）**: 1 サイクル（4 指標 latest）2.2 秒 → **0.94 秒**（marod 系 0.84→0.28-0.31 秒）＝飽和解消・累積遅延の構造消滅。
- **検証**: 数値パリティ全通過（btlm_trail_marod 23 / ma_marod 33 / api 398 / common 20）＋サーバ実測。

## ISSUE-155（ページ起動が遅い・ローディングのまま表示されないことがある）
- **ステータス**: RESOLVED
- **事象**: ブラウザ起動〜チャート表示が遅く、まれにローディングのまま進まない（ユーザー報告・目標 1 秒以内）。
- **原因**: ライブサーバが単一スレッド HTTPServer（rpy2/R のスレッド非安全対策）で、重い /compute の背後に起動時の静的 JS 数十件・/candles まで直列に並ぶ。別タブが tick 駆動再計算を流していると起動リクエスト群が数秒〜実質ハングまで遅延（実測: compute 稼働中の静的 JS 取得が秒単位）。
- **対策（実施済み 2026-07-22 深夜）**: ThreadingHTTPServer 化＋重い計算（POST /compute・market_profile 系 GET）だけを専用ワーカースレッド 1 本へ直列送致する _ComputeWorker を導入。rpy2/R は常に同一ワーカースレッドから呼ばれ旧実装と同じ安全性（スレッド親和）を保ちつつ、静的配信・/candles・/live_ticks・/catalog は並行応答。リプレイサーバは既に ThreadingHTTPServer のため変更なし。
- **効果（実測）**: compute 3 並行の最中でも静的 JS 4ms・/catalog 1ms・/candles 261ms（自身のコストのみ）。実ブラウザの起動〜ローソク表示 144/133/132ms（3 回連続・ライブ稼働状態での再読込）＝目標 1 秒を大幅達成。api 398 テスト全通過。

## ISSUE-156（マルチスレッド化の拡張 B/H/A/C：ワーカー分離・リプレイ統一・計算プール・クライアント並列）
- **ステータス**: RESOLVED（実装完了・効果は項目により実測どおり）
- **実施内容（2026-07-23 未明）**:
  - **B**: ライブサーバの Market Profile 系 GET を専用ワーカー `_MP_WORKER` へ分離（MP の重い zp 計算が指標 /compute の待ち行列を塞がない。MP 内部は従来どおり単一スレッド直列）。
  - **H**: リプレイサーバの heavy 経路（candles resample／compute／intraday）を `_HeavyWorker`（専用スレッド 1 本）経由に統一。従来のロック直列では rpy2/R のスレッド親和性（常に同一スレッドからの呼び出し）が未保証だった。heavy_lock 注入 API・ロック意味は温存（ワーカー内で取得）。ライブ（ISSUE-155）と同一設計。
  - **A**: ライブ /compute を tgp_btlm（rpy2）＝専用ワーカー固定・他指標＝ThreadPoolExecutor(3) に分離。安全化として module_loader に import ロック（半構築モジュール観測・二重 exec 防止）、marketdata/serving_cache に粗粒度 RLock（重複ビルド・torn-read 防止）を追加。
  - **C**: クライアント recomputeFormingTails を Promise.allSettled 並列化（個別失敗が他指標を道連れにしない）。
- **実測（正直な結果）**: A/C のスループット効果は **無し**（4 指標 latest: 逐次 0.86s vs 並列 0.91s＝GIL 支配。計算の python ループ区間が直列化されるため）。B/H は待ち行列分離・rpy2 安全性の確実化として有効（レイテンシ隔離）。起動 151ms・全テスト既知ベースライン（api 398 / replay py 176 / web 724+236）・コンソールエラー 0。
- **結論**: これ以上のスループット向上には multiprocessing が必要だが、DataFrame 直列化コストと複雑性から**非推奨**（現状の 1 サイクル ~0.9s・duty ~40% で実用十分）。
- **対策（実施済み 2026-07-23）**: keepPane 除去時（chart_renderer.remove）に pane 価格軸の手動レンジ（autoScale=false 時の getVisibleRange）を slot へ退避し、_renderSeries の系列再追加後に setVisibleRange で復元（lwc 内部で autoScale=false 再設定＝軸ドラッグと同一状態）。自動スケール中は退避しない＝挙動不変。ライブ・リプレイ共有ファイルのため両モード同時解消。
- **検証（2026-07-23 実 UI）**: 実ブラウザで最下段 pane 右軸を実ドラッグ→確定足 full 再計算（毎分）をまたいで軸描画の画素一致を確認（dragChangedAxis=true / axisPreservedAcrossFull=true）。ユニット回帰 2 件追加（web 728 pass・既知失敗のみ）。

## ISSUE-157（ライブ: 指標更新が数分〜のち全指標同時に停止する（ローソクは前進継続）・間欠再発）
- **ステータス**: RESOLVED（クロック駆動設計へ再設計・2026-07-23）
- **事象（2026-07-23 朝 ユーザー報告 2 回）**: 開きっぱなしのページで、ある時点から全指標（btlm_trail・marod 系）の描画が同一バーで停止し、ローソクだけ前進する。F5 で回復。サーバは全 200・データ 2 系統（tick/M1）フレッシュ・新規ページは毎分追随（実測）＝クライアント側の間欠停止。
- **原因（構造欠陥として特定・当該事象での実証は監視中）**: /compute の fetch にタイムアウトが無く、応答が返らない要求が 1 本でもあると requestFormingRecompute の `_formingBusy` ラッチが永久に解放されない（recomputeFormingTails の Promise.allSettled が全 settle 待ち）。以降の forming/full 要求はすべて pending に畳まれたまま実行されず、全指標更新が凍結する。ローソク・forming_bar・live_ticks の各ポーラーは reentrancy ラッチを持たず自己回復するため、ローソクだけ前進する＝症状と完全一致。
- **対策（実施済み 2026-07-23）**:
  1. compute_http_client に AbortController タイムアウト 30s（本文読取ストールも中断）。タイムアウトは ComputeError(network) へ翻訳され、既存 pending 機構が自動再試行する（凍結→最大 30s の一時停止に転換）。
  2. busy ラッチのウォッチドッグ（BUSY_WATCHDOG_MS=90s・最後の砦）: busy 保持が異常長時間なら次要求時に強制解放して実行を通す（未知の非解決 await でも自己復旧）。
- **検証**: ユニット 2 件追加（タイムアウト abort→network 翻訳・正常系不変）。web 728 pass / replay web 236 pass（既知失敗のみ）。実 UI はユーザー同等構成（ズーム＋pane 手動スケール）で 14 分監視＝正常系の非退行を確認（compute 鮮度 0〜5s・描画右端メイン＝pane 一致・エラー 0）。事象自体は間欠のため監視中に再現せず＝当該事象での実地実証は未（原因は症状と一致する唯一の構造欠陥として特定）。再発時は最大 30s（ウォッチドッグ経路でも 90s）で自己回復する設計。再発報告があれば console の ISSUE-157 警告有無で経路を確定できる。
- **再設計（2026-07-23・ユーザー裁定「30 秒で自己回復する時点で設計失格」を受け恒久設計へ）**: タイムアウト復旧（緩和策）を廃し、更新機構を「ラッチ待ち」から**クロック駆動**へ全面変更。requestForming/FullRecompute は要求フラグを立てて _drive() を呼ぶだけで、**実行中要求の完了を一切待たない**（await をゲートにしない）。各ポーラー（LiveTickPlayer 2.5s/100ms・FormingBarUpdater 5s・LiveUpdater 60s）が自走クロックとなり、STALL_DEADLINE_MS=10s を超えた試行はハングとみなして無視し新試行を発行。遅延応答は per-instance generation の latest-wins が破棄（競合安全）。isRecomputing() も時限化（深さカウンタのハング残留でゲートが恒久閉鎖しない）。**凍結という吸収状態が構造的に存在しない**（ローソク側ポーラーと同一の自己回復構造）。fetch 30s タイムアウトは資源掃除として残置（生存機構ではない）。全時間足・ライブ/リプレイ共通（共有ファイル）。
- **検証（2026-07-23）**: ユニット: 新設計 7 件（coalesce 維持・full 必達維持・isRecomputing 衝突回避維持・**ハング中でも新試行発行**・時限ゲート自己開放）を含め web 730 pass / replay web 236 pass（既知失敗のみ）。実 UI: 3.3 分監視で compute 160 回・コンソールエラー 0・全指標が最新確定足を追随（毎分）。

## ISSUE-158（リプレイ再生が遅すぎる：①変換ベクトル化＋②一括リビール）
- **ステータス**: RESOLVED（2026-07-23 実装・実 UI 検証済み）
- **事象**: リプレイのバー送りが 1 ステップ数秒（体感で遅すぎる・ユーザー報告）。実測で 1 compute=1.45s、うち 69%（1.17s）が「全履歴 50,000 行を Python 行ループ（df.iloc/iterrows）で dict 化→直後に 48,500 行を廃棄」する変換だった（指標計算自体は 0.13s）。マルチスレッド化は無効（GIL 実測 0.9x・rpy2 安全のため重処理は単一スレッド必須）。
- **対策①（変換ベクトル化・挙動完全同一）**: causal_compute_gateway._df_to_bars と causal_candle_repository の行ループを列単位ベクトル化。旧実装を参照実装としてテスト内に凍結し完全一致を固定（test_plain_bars_vectorized.py・9 件）。実測: 変換 1,165ms→21ms（55 倍）、compute 1.7s→0.25〜0.32s。
- **対策②（一括リビール・値同一を実測ゲート）**: 再生開始時に全レンジを 1 回計算し、バー送りは t 以下への同期スライス描画のみ（per-step HTTP 廃止）。リプレイの per-step は limit=bar+1＝左端固定窓（candles[0] 起点）のため、因果指標では「全レンジ 1 回計算の各バー値」＝「バーごとその場計算値」が厳密に成立。実データ検証: btlm_trail / btlm_trail_marod / ma_marod / moving_averages の全系列（evq 4 系列・hlines 含む）で 444 比較点 max_dev=0。登録リスト CAUSAL_REVEAL_IDS（replay 専用・fail-closed＝未検証指標は従来経路）で管理。gear/削除/時間足切替は基底を無効化し次フレーム再構築（世代ガードで遅延応答破棄）。足内更新（最新足更新モード）は対象外＝従来どおりその場計算（粒度・値とも不変）。
- **検証**: replay web 243 pass（+7 新規）/ live web 730 pass / replay py 185 pass（+9 新規）＝既知失敗のみ。実 UI（8280 実再生・指標 3 適用）: **ステップ計算 31ms（改修前は数秒）・12.05 bars/s**（速度プリセット上限側）・足内更新モード共存確認（55ms・エラー 0）・コンソールエラー 0（favicon 404 は既存）。リプレイサーバ再起動済み。

## ISSUE-159（SOLID 是正 🔴-2: chart_renderer 協働子抽出時に series_kind 台帳消費契約テストが一時 fail）
- **ステータス**: RESOLVED（2026-07-23）
- **事象**: SeriesDrawer 抽出（series_drawer.js）で能力分岐（seriesKind 参照）を全て移設した結果、series_kind.test.js「registry is the only kind ledger」が chart_renderer.js に要求する `from '../../domain/series_kind.js'` の import 残存契約を破り 1 件 fail（web 731→730）。
- **対策（実施済み）**: chart_renderer.js に seriesKind の import を契約の固定点として復帰（能力分岐の実体は SeriesDrawer へ委譲済み・raw kind 文字列比較なしの契約は両ファイルで維持）。テストファイルは 1 行も変更していない。
- **検証**: web 733 tests / 731 pass / fail 2（既知: replay_analysis・timeline_player のみ）・replay web 252 / 243 pass / fail 9（既知のみ）・node build.mjs 成功＝ベースライン完全一致。

## ISSUE-160（SOLID 監査 改善提案 1→4 の実装：振る舞い完全不変リファクタリング）
- **ステータス**: RESOLVED（2026-07-23 実装・全スイート＋実 UI 検証済み）
- **実施内容（監査報告の優先度順・すべて挙動不変）**:
  1. **🔴-3 スレッド親和性の宣言化**: server.py の `indicatorId=="tgp_btlm"` 名指し分岐を廃し、call_binding `_BindingSpec.thread_affinity: "dedicated"` 宣言＋ `requires_dedicated_worker()` 参照へ（殻から計算知識を排除・OCP）。回帰テスト 4 件（宣言集合が旧ハードコードと完全一致を固定）。
  2. **🔴-4 MP 述語の台帳化**: `_isMarketProfile` の具象名直判定を usecase 能力台帳 `actor_driven_ids.js`（ACTOR_DRIVEN_COMPUTE_IDS）へ移譲（series_kind と同型・新アクター駆動指標は台帳 1 行で追加可能）。replay symlink・build.mjs 台帳へ登録。
  3. **🔴-1/🔴-2 フロント 2 大クラスの責務抽出**: indicator_controller から `UpdateScheduler`（update_scheduler.js・クロック駆動 1:1 移植・STALL_DEADLINE_MS 再 export で互換維持）、chart_renderer（1463→783 行）から `ScaleController`／`CandleFeed`／`SeriesDrawer`（公開 API・export・コンストラクタ署名 1 バイト不変・chart_renderer 既存テスト無変更で green＝公開面互換の証明）。
  4. **🟡-10 バンドプリミティブの common 抽出**: `common/marod_bands.py` 新設（rolling_causal/fast・quantile_bands・sigma_band・outlier_event_quantiles を btlm_trail_marod core から移設・例外文言込み同一）。btlm core は委譲へ縮退（公開 API・既定値不変）、ma_marod は兄弟具象への動的ロード依存を廃し common 直参照（DIP 対称化）。
- **検証**: 全スイート green＝api 402（+4）/ replay py 185 / btlm 23 / ma_marod 33 / common 20 / web 731＋243（既知失敗のみ）。両サーバ再起動後の実 UI：ライブ compute 正常（15s で 28 回・エラー 0）・リプレイ 11.9 bars/s・ステップ 40ms・エラー 0。
- **未実施（監査指摘のうち対象外）**: 🟡-1/2（狭い Port 実注入・ChartRenderer のロール Port 分割）・🟡-3（合成ルートの業務ロジック抽出）ほか 🔵 群＝提案のみ（推奨順 1→4 の範囲で完了）。

## ISSUE-161（ライブ: 指標ラインが周期的に 1〜3 バー遅れ・オシレーター歯抜け→約 2 分で追いつく反復）
- **ステータス**: RESOLVED（ストリーミング化 2026-07-23）
- **事象（2026-07-23 19:47 JST ユーザー報告）**: ライブでラインの更新が遅れ、オシレーターは最新足付近が歯抜け（連続バー→空白→最新 1 本）。約 2 分で正常に戻り、また遅れる反復。
- **原因（実測で特定・凍結バグ ISSUE-157 とは別問題）**: ローソクの最新部はブラウザが tick から直接合成するのに対し、指標はサーバの M1 確定足 CSV からのみ計算される。この M1 供給が周期的に遅れる（実測 10:50:44 時点で最終行 10:48＝約 2 バー遅れ）。差分のバーが「ローソクはあるが指標値が無い」＝歯抜けとして現れ、M1 追記が追いつくと回復する。tick 取得は live_tick_watch --interval 60（60 秒バッチ）のため、確定足の供給遅延は構造的に最大 ~2 分。
- **増悪要因（推論・未実証と明示）**: 本日 serve.sh を複数回再起動した際、データ watch（export_jp225_m1 / live_tick_watch）が一時多重起動し書き込み競合した可能性。10:41 に 1 組へ収束していることをプロセス実測で確認済み（現在は健全）。
- **対策案（承認待ち）**: ①現状（watch 1 組）で遅延分布を実測監視し、多重起動が原因だったかを切り分ける（推奨・まず観察）②tick 取得間隔 60s の短縮（取得 API 負荷とのトレードオフ＝ユーザー判断）③serve.sh に watch 多重起動ガード（既存プロセス検出）を追加。
- **訂正**: 当初記載の「M1 変換が別プロセスで位相ずれ」は誤り（M1/rollup は live_tick_watch 内で連鎖済み）。真因は「取得だけが 60 秒周期の当日全量再取得（1 回 ~10 秒）」で、参照実装（prototype_260707-01 _poll_loop＝増分カーソル 5 秒）から本番だけが乖離していたこと。
- **対策（実施済み）**: live_tick_watch に --stream を新設（参照実装踏襲: 増分カーソル・厳密>cursor・バックオフ・サーキットブレーカ・5 秒周期）。fetch_ticks_since に with_volumes 追加（既定不変・既存呼出非干渉）。分確定は猶予 12 秒後に M1/rollup 連鎖（末尾 tick 未着のまま確定バーを焼かない）。30 分毎の当日全量再取得で自己修復。出来高単位差（1e6 倍）は正規化（M1 はティック数集計のため計算無影響）。serve.sh を --stream へ切替。
- **検証**: tools 63 pass（+3）。実測: tick 遅延 4〜7 秒（旧 最大 ~70 秒）・M1 確定はバー終了+16〜32 秒（旧 最大 ~2 分）・取得エラー 0。実 UI 90 秒監視: compute 182 回・指標最終値の鮮度 30 秒（最新確定バー）・コンソールエラー 0。

## ISSUE-162（ライブ: 分境界直後の歯抜け・ライン途切れの恒久解消＝閉周期の tick 合成橋渡し）
- **ステータス**: RESOLVED（2026-07-23 実装・実測検証済み）
- **事象**: ストリーミング化（ISSUE-161）後も、バー確定から M1 焼き込み（+12 秒猶予）までの十数秒間、「確定済みの前バーが空白・形成中バーだけ点灯」の歯抜けとライン途切れが分境界ごとに発生。
- **設計（ユーザー承認）**: 表示と確定の分離。閉じた直後のバーは足内更新（mode='latest'）経路が実 tick の完結窓から合成して途切れなく描き続け、最終値は M1 焼き込みで一度だけ確定（非リペイント厳守・売買トリガーは確定値のみ）。更新粒度・ライブ/リプレイ機構は無変更。
- **実装**: apply_forming_bar を拡張——df 末尾と形成中周期の間の欠落閉周期を forming_bar_from_ticks の完結窓で合成注入（固定長 tf のみ・tick 無し周期は捏造しない・最大 5 本・tick 系 ref 限定）。形成中バー None（境界直後の tick 未着）でも閉周期合成は独立実行。latest_compute に min_tail（additive・注入バー数ぶん末尾切り下限を拡張）。
- **是正過程で捕捉した不具合**: (1) 末尾切り trailing_k が合成バーを応答から切落とし（min_tail で解消）(2) 形成中 None の巻き添え早期 return（分離で解消）(3) ref ゲート漏れで sample データへ実 tick 混入（is_tick_ref ゲートで解消・テスト済）。
- **検証**: api 405 pass（+3 gap-fill テスト）・replay py 185 pass。実測: 分境界 +4 秒の 3 連続試行すべて「系列が現在分まで 60 秒間隔で連続・欠落ゼロ」（12:54/12:55/12:56）。クライアント無変更＝F5 不要（サーバ側のみ）。

## ISSUE-163（ライブ: 時間足切替後にオシレーター pane が全高ブロック状に潰れる）
- **ステータス**: RESOLVED（2026-07-23 実装・実 UI 再現/検証済み）
- **事象（ユーザー報告 22:00 JST）**: 時間足切替後、marod 系 pane のヒストグラムが全高のブロック状に潰れ、ラインもクリップして異常表示。
- **原因（実 UI 再現で確定）**: ISSUE-150 の pane 手動スケール保持（keepPane 退避/復元）が**時間足切替をまたいで**旧レンジを復元していた。1m で作った狭い手動レンジ（例 ±0.1 相当）が 30m の値域（±4）に適用され、全系列がクリップ＝全高ブロック化。メイン価格軸には ISSUE-113（ユーザー裁定: 切替で手動スケールをリセット）が既適用で、pane 側だけ裁定に反していた。
- **対策**: ChartRenderer.resetPaneScales() を新設（全 pane の退避破棄＋autoScale=true）。TimeframeController.setTimeframe の ISSUE-113 リセット直後に呼ぶ（メイン軸と同裁定へ統一）。ISSUE-150 の保持は「同一時間足での再計算」に限定＝本来の意図どおり。
- **検証**: 再現手順（1m で pane 軸を狭レンジ手動化 → 30m 切替）で修正前クリップ・修正後 ±4 自動スケール正常表示を実 UI 確認。web 732 pass / replay 243 pass（既知失敗のみ）。

## ISSUE-164（ズーム操作への自動介入の全廃＝「指示なき追加実装」の抜本掃除・ユーザー裁定）
- **ステータス**: RESOLVED（2026-07-23 実装・全数監査・実 UI 検証済み）
- **裁定（ユーザー 2026-07-23）**: 「単一機能（右端常設余白）の指示に対し、実装が勝手な自動補正（ズームのたびに余白 px を一定へ再適用する購読）を同梱していた。この副作用が『過去へ遡って拡大すると右端へ戻る』ジャンプ（ISSUE-148 系）の根本原因であり、148 の修正はガードで隠しただけだった。ビューを動かしてよいのはユーザーの明示イベントのみ。ユーザビリティを下げる自動介入はやめろ」。
- **対策**:
  1. 可視範囲変化（ズーム/ドラッグ）→ rightOffset 再適用の購読を撤去（chart_renderer 構築子）。余白の適用点は明示イベントのみ＝初期表示・時間足切替（setCandles）・MP 余白率変更・最新足へ戻る操作。ズーム中の余白 px 一定性は保証しない（仕様として明文化）。旧仕様のテストは新裁定のテスト（ズーム非反応）へ置換。
  2. **全数監査**（専用エージェント・全ビュー操作 API の起点を経路追跡）: 補正型の自動ビュー書換えは本件撤去後 **残存ゼロ** を確認。他の呼出はすべて明示操作起点（A 判定）。例外は ISSUE-150 の pane スケール同値復元のみ（ユーザー自身のレンジをそのまま戻す・ビューは動かない・除去すると 150 再発のため温存）。
  3. 掃除: 撤去済み機能を指す陳腐化コメント 2 箇所を是正・呼出ゼロの死メソッド focusRecentBars を削除（対応する死テストも削除）。
- **検証**: web 731 pass / replay 243 pass（既知失敗のみ）・build 成功。実 UI（クロスヘア日時を判定器に採用）: 右端 12:28 → 過去 22:58 へスクロール → ズーム後 23:35（過去のまま） → **バー確定跨ぎ 75 秒後も 23:35 で不動**＝ジャンプ消滅。なお前回の「修正後も再現」は検証側のジェスチャ誤り（時間軸右ドラッグ＝右端まで縮小する操作）による誤判定と特定した。
- **教訓（記録）**: 指示された単一機能に「気を利かせた自動補正」を同梱しない。症状をガードで隠さず原因を除去する。

## ISSUE-165（時間足切替が 1 秒超過＝指標 /compute のフロント直列実行×サーバ飽和の複合）
- **ステータス**: RESOLVED（2026-07-23 実装・実 UI 検証済み）
- **結果（実 UI 再実測・全時間足掃引）**: 切替クリック→全系列描画 15m 0.54s / 30m 0.62s / 1h 0.70s / **4h 0.75s / 1D 0.32〜0.60s** / 1W 0.19s / 1M 0.09s / 1m 0.65s＝全時間足 1 秒以内を達成（旧 4h 1.13s・1D 1.26s）。並列発行（2 compute 同時開始）を resource timing で確認。コンソールエラーなし・描画健全（series 取り違え/pane 崩れなし）。web 732 pass / replay 243 pass（既知失敗のみ・新規失敗なし）・build 成功。回帰テスト追加（並列発行・series 取り違えなし・generation lost update なし）。
- **事象（ユーザー報告 2026-07-23）**: 時間足切替が遅すぎる。選択後 1 秒以内の表示を要求。
- **実測（実 UI・ライブ 8000・適用 2 指標 btlm_trail_marod + btlm_trail）**: 切替クリック→全系列描画まで 1m 0.66s / 5m 0.52s / 15m 0.56s / 30m 0.67s / 1h 0.35s / **4h 1.13s / 1D 1.26s** / 1W 0.68s。単発 /compute はサーバ負荷時 最大 1.6s を実測。
- **原因（実測で確定）**:
  1. フロントの切替バッチ（recomputeAllApplied フェーズ1）が指標ごと**直列** await（合算課金）。直列必須の理由は state 丸ごと代入の lost update と共有 _lastSeries の取り違えガード。
  2. サーバ側の膨張: 足内更新（tick 駆動 mode=latest）が実質フル計算（marod min_window≒全量）でほぼ連続発火し、compute プール（GIL）が飽和。単独実測 marod 180ms / trail 50ms が、実 UI では 200〜1030ms へ膨張（3〜5 倍）。
  3. 毎分の CSV 書換で resample キャッシュが失効し、直後の切替に読込 30〜160ms が上乗せ。
- **対策（ユーザー承認 y・実施済み）**: フェーズ1 の /compute を並列化する。前提是正として (a) series 受け渡しを共有 _lastSeries から per-call gateway 捕捉へ（取り違え race の恒久解消・既存並列の足内更新経路 ISSUE-156(C) に潜在する同 race も同時解消）、(b) state 反映を丸ごと代入から当該 instance 行のみのマージへ（lost update 恒久解消）。並列時の実測 0.52〜0.65s（4h/1D・ライブ負荷下）＝全時間足で 1 秒以内見込み。描画は従来どおりフェーズ2 同期一括（ISSUE-023 同時更新は不変）。

## ISSUE-166（起動後に UI は表示されるがチャートが表示されないときがある・間欠）
- **ステータス**: RESOLVED（2026-07-23 対策実装・実 UI 検証済み）
- **対策（ユーザー承認: 読込ガード＋再試行）**: 両 UI の入口（index.html）に ensureLwc を追加——window.LightweightCharts 未定義なら cache-bust 付きで最大 3 回再読込（500ms×attempt の待機）し、成功してから bootstrap する。3 回失敗時は console.error で明示し bootstrap しない（未定義のまま createChart で即死する経路を遮断）。自動ページリロードは実装しない（承認範囲どおり）。
- **検証（実 UI）**: Playwright の経路遮断で vendor 初回読込を強制失敗→再試行 1 回で復帰し、ライブ・リプレイともチャート描画/現在値表示まで正常（page error 0）。通常起動（介入なし）も無影響。web 737 pass / replay 243 pass（既知失敗のみ）・build 成功。
- **一次証拠（ユーザー提供 2026-07-23）**: `lightweight-charts.js: net::ERR_CONTENT_LENGTH_MISMATCH` → `chart_bootstrap.js:18 TypeError: Cannot read properties of undefined (reading 'createChart')`（bootstrap 内 createChartWithMainSeries・composition_root_front.js:159 経由）。
- **原因（確定・フロント連鎖）**: vendor/lightweight-charts.js の配信が Content-Length 未満で途中切断→ window.LightweightCharts が undefined のまま bootstrap → lwc.createChart で TypeError → bind()/restore()/更新系の起動前に全滅。静的 HTML/CSS のみ表示され、チャート無し・時間足切替も無反応＝全症状と一致。リトライ・エラー表示が無いため F5 まで回復しない。
- **途中切断のサーバ側トリガ（推定・未検証と明示）**: serve.sh 再起動の際、旧サーバプロセス終了時に daemon スレッドが応答書き込み途中で殺される（ThreadingHTTPServer.daemon_threads=True）レースが最有力。ページ読込と再起動の重なりで間欠発生する頻度感とも整合。
- **次回発生時の採取手順（このセクション参照）**: (1) F12 → Console タブの赤エラーを全文コピー (2) Network タブで赤行（failed/4xx/5xx）の URL とステータス (3) 画面スクショ（チャート領域に軸・目盛が出ているか） (4) serve.sh を起動した端末に Traceback が出ていないか。この 4 点で「フロント配線死（bootstrap 例外）」か「/candles 失敗」かが一意に切り分く。
- **事象（ユーザー報告 2026-07-23）**: 起動後、UI は表示されるがチャート（ローソク）が表示されないことがある。
- **再現試行（実測・現条件では未再現）**: ライブ 8000 で 15 回・リプレイ 8280 で 6 回のページ再読込＝全回 candles 1500 本取得・canvas 描画あり。/candles?timeframe=1m を 80 秒間 251 連打（分確定の非原子追記を跨ぐ）＝全 200。watch ログにエラー 0。
- **コード確認で判明した構造的脆弱点（事実・症状との因果は未確定）**:
  1. B方式の初回 /candles が失敗（非200/ok:false/例外）すると fetchCandles が null を返し**無描画のまま**（フォールバック・リトライ・エラー表示なし）。永続 tf が既定 1D と同一の場合、restore でも再取得されず手動の時間足切替まで空白が続く。
  2. サーバ・コールド起動直後は torn-read フォールバック（直前良好キャッシュ返却）が**キャッシュ不在で機能せず**、読込失敗が即エラー応答になる（serving_cache/rollup_store とも「良好キャッシュが無ければ送出」）。
  3. 1m 経路（原子 tail 読み）は毎回 read_tail 直読みでフォールバック自体が無い。tick_m1 の末尾追記は非原子（tick_m1.py 319 に明記）。
- **ユーザー回答（2026-07-23）**: ライブ・リプレイ両方で発生／serve.sh 起動直後に発生／**時間足切替でも回復しない**。
- **追加再現試行（両サーバをクリーン再起動して起動直後の窓を計装付きで検証・未再現）**: ライブ再起動→直後 8 連続ロード・リプレイ再起動→直後 6 連続ロード＝全回描画あり・window.onerror/unhandledrejection 0 件・失敗リクエスト 0 件。CSV 全 18 ファイル不正行 0（torn 焼き付き無し）。再起動後の watch 稼働・CSV 末尾健全性も確認済み。
- **考察（未検証と明示）**: 「切替でも回復しない」は初回 /candles 失敗（脆弱点 1）単独では説明できない（切替は再取得するため回復するはず）。フロント bootstrap が bind() 前に死ぬ経路（vendor/lightweight-charts.js やモジュール読込失敗→ createChart 例外→全機能死亡）なら症状全体と整合するが、35 回のロード試行では観測されず。発生時の一次証拠（コンソール・ネットワーク・スクショ）が確定に必要。
- **今回の再現試行との条件差（記録）**: ユーザーの実起動は長時間分の増分取得＋初回全量再取得の重負荷・OS コールドキャッシュを伴う可能性。本試行はデータ最新状態の軽量起動だった。

## ISSUE-167: 1分足へ切替えると表示まで長時間フリーズ（jp225_tick_m1 日境界の重複分バー→lwc candlestick 毎フレーム "Value is null"）
- **ステータス**: RESOLVED（2026-07-24 起票・同日修正。TDD Red→Green。web 新規5緑・marketdata 194緑・indicator_ui api 87緑（回帰0／既存 fail 2件=timeline_player/replay_analysis の module 不在＝無関係で本件前から fail）。実 UI（8000・ライブ・指標 btlm_trail_marod 適用・1分）で `Value is null` 0件・candlestick data 1500→1499 厳密増加を実測確認。backend は旧プロセス常駐で重複を返し続ける状態でも front 防壁で 0 クラッシュ＝多重防御を実証）
- **事象**: ユーザー報告「時間足を切り替えても表示までに長時間かかる」。実測: 1分切替で**表示完了まで 31.4 秒**、その間コンソールへ `Value is null`（lightweight-charts）が **~10 件/秒**の持続 flood。5m/1D は正常（定常 0）。
- **原因（実測で層ごと確定）**: 素材 `data/marketdata/jp225_tick_m1.csv` の日境界（前日 23:59:00 UTC）に**同一分の重複行 2 本**（vol 220 と vol 27・異 OHLC）が存在。`/candles?jp225_tick&1m&limit=1500` が idx 283/284 に同一 time `1784851140` を返す（5m以上は resample で融合＝露見せず・limit=400 は当該 bar 未包含）。フロント `CandleFeed.setCandles` は dedupe せず `_mainSeries.setData(arr)` へ直渡し。lightweight-charts は系列 data に**厳密増加 time**を要求するため、重複 time で内部 bar↔time 対応が壊れ、candlestick colorer `a(t(n,s))`（vendor 逆アセンブルで確定）が可視 index の bar アクセサ null を参照して "Value is null" を **rAF ペイントごとに** throw。例外は update/setData の同期呼出ではなく後続 rAF で飛ぶため呼出側 try/catch では捕捉不能。各フレーム描画が中断され新時間足がクリーンに描き切れず長時間フリーズ。**指標非依存**（指標全削除でも 1m は crash）・**backend /compute は全 TF null 0・高速**（無関係）・`/compute` の ERR_CONTENT_LENGTH_MISMATCH は client abort 1 件のみ（無関係）。重複の発生源: `tick_m1.build/append_m1_from_ticks` が日別 M1 を `pd.concat` する際、境界分のティックが複数 parquet に分散すると同一分バーが二重に混じり、concat 後に分 dedupe が無かった（`ticks_to_m1` は単一 parquet 内のみ一意）。
- **対策（3段・発生源＋serving＋front の多重防御）**: (1) **発生源** `marketdata/tick_m1._dedupe_minutes`（index 重複を keep-last で畳む・純粋冪等）を新設し build/append の `concat(...).sort_index()` 直後へ適用＝素材段で分一意を保証（以後の再構築・増分で二重混入しない）。(2) **serving 無害化** `marketdata/dataset._clamp_outlier_bars`（全 ref・全返却経路が通る hygiene 漏斗）に index dedupe(keep-last) を追加＝既存 CSV の重複が残っていても serving でチャート/指標へ渡る前に無害化（データ無断編集は不実施＝非破壊）。(3) **front 防壁** `CandleFeed.dedupeCandlesByTime` を新設し `setCandles`/`resyncMissedCandles` の `setData` 直前に適用＝上流異常が二度とチャートを壊さない厳密増加保証。
- **テスト**: web `candle_feed_dedupe.test.js`（5本: 純関数 keep-last/no-op/後退除去・setCandles/resync 統合が厳密増加）／marketdata `test_tick_m1.py`（+2: `_dedupe_minutes` keep-last/no-op）・`test_dataset_dedupe_index.py`（2本: 全 ref dedupe/no-op）。
- **申し送り（RESOLVED 2026-07-25・ユーザー承認の上で恒久除去実施）**: CSV 実体の重複行を除去し rollup 全再構築（ISSUE-107 と同手順）。実測で重複は **同種の日境界分重複が 3 件**（`2020-05-29 00:00`／`2025-10-01 00:00`／`2026-07-23 23:59`・いずれも vol 小の単発 tick と正常バーの二重）と判明し、範囲承認の上で 3 件とも除去。手順: (a) live_tick_watch(PID 32659) を一時停止（ライブ 8000 は継続）(b) CSV を `_dedupe_minutes` keep-last と同値のテキスト削除で置換（3 行削除のみ・他行バイト完全一致を dry-run 実証・バックアップ `jp225_tick_m1.csv.bak-utc20260725`）(c) `build_tick_rollup --only rollup --full` で全 8 TF 再構築（rollups_backup_utc20260725_jp225_tick へ旧物退避）(d) live_tick_watch 再起動。検証: 5m〜1W の実差分は 3 重複日/週のみ・1M も改行正規化後は 3 重複月のみ（見かけの全月差は旧 1M が LF・新は全 TF CRLF 統一の改行差＝無害）・rollup_state（2026-07-24 20:14）が CSV 最終足と一致し watch 増分継続可・再起動後 0 ticks 間隙で正常。
- **再観測（2026-07-27・REOPEN せず記録のみ）**: ISSUE-172〜187 対応後の実 UI 検証（ライブ 8000・ma_marod 適用済み）で、**ページ読込後の最初の時間足切替（日→5分）で `Value is null` が 1 回だけ発火**。以後の 8 回の切替（日→5分 ×2・15分・1時間・日 ×2・1分を含む）では**0 件**で、10 秒間の監視でも反復 0＝本 ISSUE の「~10 件/秒の持続 flood」とは性質が異なる。サーバ応答は健全（`/candles?5m` は 50,000 本・null 0・重複 time 0・昇順）。描画・フリーズとも実害なし（1 分足も正常表示）。**原因未特定・再現条件不明**のため対策は未実施。再現したら本 ISSUE を REOPEN するか別起票して切り分ける。
- **関連**: ISSUE-107（M1 素材化の外れ値クリーニング漏斗＝本件は同漏斗の「重複」ギャップ）・ISSUE-048/049/053（jp225_tick ライブ供給）・[[jp225-tick-20250826-phantom-run]]（同 CSV の別クラス素材不良）。ブランチ fix/jp225-tick-m1-duplicate-minute。

## ISSUE-168: [整理] ライブ/リプレイ設計差異調査の「未修正・要注意」項目（B 群）の是正（2026-07-25）
- **ステータス**: RESOLVED（2026-07-25・ユーザー指示「B 項目に対応しろ」。実測で各項目を分類し是正/確認）
- **背景**: ライブ/リプレイ設計差異調査で挙げた「未修正・要注意」3 項目（①`_asOf` デッドコード ②CandleFeed dedupe 上流バグ ③リプレイ LiveUpdater 配線）＋既済 1 項目（値渡しドリフト＝ISSUE-123 是正済み）。
- **① `_asOf` デッドコード削除（実施）**: `market_profile_actor.js:133/300-303` の `_asOf` フィールド・`applyGrowthState({growing, asOf})` の `asOf` 分割代入・代入行を削除。実証: `applyGrowthState` の全呼出元（`market_profile_controller.js:66` ほか）で `asOf` を渡す箇所は皆無・読み出しも 0・テスト参照も 0＝完全デッド（ISSUE-129 で単一時計 `to` へ一本化され不要化した「布石」の残骸。稼働中の `_asOfStrategy` は別物で無関係）。挙動不変の非破壊リファクタ。検証: market_profile web 295／indicator_ui web 742／replay_ui web 252 全緑（回帰ゼロ）。
- **② CandleFeed dedupe 上流バグ（→ ISSUE-167 で是正）**: 3 段多重防御は ISSUE-167 で実装済み。残件の CSV 実体重複は本対応で恒久除去（ISSUE-167 申し送り参照＝同種 3 件除去＋rollup 再構築）。
- **③ リプレイ LiveUpdater 配線（確認のみ・対応不要）**: `composition_root_front.js:206` は mode='b' で LiveUpdater を生成するが、`index.html` は `setupReplay()` のみ呼び `liveUpdater.start()` を呼ばない（129 行コメント明記・実測で呼出不在）。60 秒ポーリングは起動せず＝設計どおりで問題なし（当初の「start 呼出未確認」懸念を解消）。

## ISSUE-169: [既知限界] 統合UIトグルで既存 document スコープリスナが線形蓄積・無波及制約下の既知限界（2026-07-25）
- **ステータス**: RESOLVED
- **背景**: ライブ/リプレイ統合UI（`unified_ui/`・ルータ方式・既存モジュール無編集厳守）のモードトグルで、`unified_root.js` の teardown は `#mode-ui` サブツリーを pristine innerHTML へ復元し、**要素スコープ**の `bind()` リスナ（indicator_controller.js:900-951 が張る click/input）は新ノード置換で根絶する。
- **限界**: 既存無編集モジュールが **document/body スコープ**へ張るリスナは innerHTML 復元では除去できず残存する。実証: `timeframe_menu.js:94-95` が `new TimeframeMenu().install()`（＝mount 毎）で `doc.addEventListener('click', () => this._setOpen(false))` を removeEventListener 無しで登録＝**トグル毎に document click リスナが +1 蓄積**（線形）。
- **影響**: 軽微・有界。各リスナは `_setOpen(false)`（ドロップダウン閉）の冪等操作のみで副作用は実質無。DOM ノードは pristine 置換で解放されるためリーク源は当該クロージャのみ。
- **完全根絶の条件**: `timeframe_menu.js` 等の既存モジュールへ removeEventListener／dispose を追加する改変が必要＝無波及制約（`indigators/**`・`simulator/**` byte 不変）に抵触するため本スコープでは不可。将来、統合を正式機能化する際の別承認課題（既存改変を伴う恒久対処）。
- **前提の失効（2026-07-31）**: 本 Issue が「恒久対処は不可」とした根拠は**無波及制約**（`indigators/**`・`simulator/**` byte 不変）だったが、本セッションで当該ツリーを承認のうえ改変しているため制約は既に失効している。よって恒久対処を実施した。
- **実測（実 UI・統合 8000・`#enter-replay` を 6 往復）**: **document の click リスナは 0 増 0 減**（モードは replay/live を 6 回正しく往復）。起票時に記録された「トグル毎に +1 蓄積」は**現行コードでは再現しない**。
  - 理由（コードで確認）: `unified_root.js` は現在「**単一 chart を 1 回だけ生成し、live root の bootstrap を 1 回呼ぶ**」設計であり、モードトグルでツールバーを再 mount しない。起票時（2026-07-25）の「`#mode-ui` を pristine innerHTML へ復元する teardown」方式から変わっている。
- **対応（構造的な再発防止として実施）**: 蓄積が再現しなくなった今も、`install()` が再度呼ばれれば同じ欠陥が復活する。共有ヘルパ `menu_document_close.js` を新設し、**install 時に同一 document・同一キーの前回リスナを自分で外す**（自己修復）＋ `dispose()` を提供した。呼び出し側が本モジュールを知らなくても蓄積は「document × キーあたり 1 個」に有界化する。対象は `timeframe_menu.js` と `chart_template_menu.js`（同じ欠陥を持っていた）。
- **⚠ 途中で検出した自分の不具合（既存ガードによる）**: ヘルパを両ファイルへ複製したところ、
  1. `pair_dim_alpha_single_source.test.js` が **A方式バンドルでの `const` 二重宣言（SyntaxError）** を検出。→ 単一モジュールへ抽出して解消。
  2. `build_module_order.test.js` が **`MODULE_ORDER` 未登録**（バンドルでシンボル未定義）を検出。→ 両メニューより前へ登録して解消。
- **検証**: 回帰テスト 4 件を追加（10 回 install で 1 個に有界・最後の install が生き残る・`dispose()` で 0 個・`removeEventListener` 非対応 document でも落ちない）。**変異注入**（前回ぶんを外さない）で該当 2 件が失敗することを確認。`indicator_ui/web` **948 passed**。

## ISSUE-170: [既存不具合] replay_mp_wiring の ISSUE-048 前後関係テストが常時 fail（本変更前から）（2026-07-26）
- **ステータス**: RESOLVED
- **事象**: `simulator/replay_ui/web/tests/replay_mp_wiring.test.js` の「during play, the revealed bar is collapsed to its open BEFORE the MP enterBar await (no completed-bar flash — ISSUE-048)」が fail する（`再生リビール時に始値畳み込み update が発火する` で AssertionError）。他 260 件は緑。
- **本変更（リプレイバー UI 刷新）との無関係を実証**: 変更一式を `git stash -u` して HEAD の状態で当該ファイルのみ実行 → **同一テストのみ fail（5 pass / 1 fail）**。すなわち本変更以前から存在する既存 fail。
- **推定原因（未検証・推論）**: テストの fake が `renderer` に `updateLastCandle` を持たない一方、`ReplayView.updateForming` は `renderer.updateLastCandle` を呼ぶ（try/catch で握り潰す）ため、期待している `mainSeries.update` イベントが記録されない。ライブ同一経路化（renderer 経由へ一本化）の際に fake が追随していない可能性。プロダクトコード側の退行かテスト fake の陳腐化かは未確定。
- **対応方針**: 本件はリプレイバー UI 刷新のスコープ外のため未修正。修正時は「畳み込みが enterBar の await より先」という ISSUE-048 の不変条件を維持したまま fake を実経路（renderer.updateLastCandle）へ合わせるか、プロダクト側の退行有無を先に実測で確定する。
- **原因の確定（2026-07-30・実測）**: **テスト fake の陳腐化**であり、製品側の退行ではない。
  - 製品は `replay.js` → `ReplayView.updateForming` → `renderer.updateLastCandle` へ**意図的に一本化**済み（ライブ同一経路化・`replay_view.js:78-87` の docstring に明記）。`mainSeries.update` の直呼びは経由しない。
  - テストの fake は `renderer: { setCandles() {} }` で `updateLastCandle` を持たず、`updateForming` の `try/catch` が例外を握り潰すため、期待していた `mainSeries.update` イベントが記録されなかった。
- **対応**: fake に `updateLastCandle` を追加し、**観測点を実経路へ合わせた**。ISSUE-048 の不変条件（畳み込みが `enterBar` の await より先）は変更していない。
- **併せて判明**: 対になる `manual navigation ... does NOT collapse` テストも同じ fake を使っており、**以前は空虚に通っていた**（記録されないので「畳み込み 0 件」が常に成立）。同様に是正した。
- **検出力の実証（2 方向）**:
  | 変異 | 結果 |
  |---|---|
  | 畳み込みを `enterBar` の後ろへ移す | `during play ... BEFORE the MP enterBar await` が fail |
  | `playing &&` ガードを外す | `manual navigation ... does NOT collapse` が fail |
  ⇒ 6/6 pass。両テストとも実効性を持つようになった。
- **残る観察（未対応）**: `ReplayView.updateForming` の `try/catch (_e) { noop }` が例外を無条件に握り潰すため、本件のような結線断が沈黙する。撤去は他呼出元への影響評価を要するため本件では触れていない。

## ISSUE-171: 統合UI が「Service Worker を有効化できないため起動を中止しました」で起動不能（SW 再登録直後の未制御＋リロード1回制限）（2026-07-26）
- **ステータス**: RESOLVED（2026-07-26 起票・同日修正。TDD Red→Green。unified_ui web 42 緑（新規3含む）。実 UI（8000・Chrome）で「リロード済みフラグ有り＋SW 登録解除」状態からの起動成功・console error 0 を実測）
- **事象**: ユーザー報告のコンソール `mode_ui_view.js:49 [unified_root] Service Worker を有効化できないため起動を中止しました。…`（`unified_root.js:138` の main が return＝UI 起動せず）。直前に別件（ISSUE 無し・`/available_days` 404）の対処として DevTools で SW を登録解除→再読込していた。
- **原因（コード読取で確定）**: `unified_ui/web/js/sw_client.js:registerServiceWorker` が (a) `navigator.serviceWorker.ready` 解決直後の `controller` だけで制御下判定していた。`ready` は「アクティブな登録がある」時点で解決するため、SW 側 activate の `clients.claim()` が届く前は `controller===null` になり得る（競合）。(b) 未制御時の救済は `sessionStorage['unified_sw_reloaded']` による**セッション内 1 回限りのリロード**で、フラグは成功後も解除されない。よって同一タブで一度でもフラグが立った後に SW 登録解除・更新で再び未制御になると、リロードもされず即 false＝**タブを閉じるまで永続的に起動中止**。
- **対策**: `sw_client.js` に (1) `waitForController(CLAIM_WAIT_MS=3000)` を追加し、`ready` 後に未制御でも `controllerchange`（＝clients.claim 到達）を上限つきで待ってから判定（リロード不要でチラつき無し）。(2) 制御下に入れた時点で `RELOAD_GUARD` を `removeItem`＝次に未制御化しても再度 1 回だけリロードできる。フェイルクローズ（claim 来ず・フラグ有り→false）と無限リロード防止は維持。
- **テスト**: `unified_ui/web/tests/sw_client.test.js` +3（ready 直後未制御→claim 後 true・リロード無し／フラグ有り＋claim→true かつフラグ解除／claim 来ず＋フラグ有り→false かつリロードしない）。
- **関連**: `/available_days` 404 は本件とは別（旧 SW 常駐で `sw_rewrite.js` の `available_days` 追加が未反映だったもの。サーバ側は `/replay/available_days` 200 を実測確認済み）。

## ISSUE-172: [要注意/潜在破壊] cache_gc が現行世代 dwell キャッシュを孤児として削除対象に列挙（配置記述子の二重情報源ドリフト）（2026-07-26）
- **ステータス**: RESOLVED（2026-07-27）
- **対応結果**: `CacheLayout` の生成責務を各 Store（`DwellRollupStore` / `ZpStore` / tf-period）へ移し、`cache_path()` と同一式から `gen_depth` / `current` を導出。`cache_layout.current_layouts()` は 3 所有者の `layout()` を集約するだけに縮退（ハードコード 0）。テストは固定値 assert を廃し `cache_path(...).relative_to(root).parts` との整合を全レイアウトへ課す形へ変更。`market_profile_dwell.py` の旧形 docstring も訂正。
- **実測（dry-run・`--delete` 未実行）**: 修正前＝現行世代 `JP225/v4` が孤児列挙・旧 `g10` が温存（**判定が完全逆転**）。修正後＝`v4` 非列挙・旧 `g10` のみ孤児。zp znull / tf-period は前後とも孤児 0。market_profile 309→ 全通過（byte-parity 含む）。
- **ISSUE 記述の訂正**: 「世代 dir 2 段＝`gen_depth=3`」を字義どおり採ると旧 `g10` を列挙できない（`JP225/g10` の subdir 数が 0 のため）。GC の掃除単位を版数 dir とし `gen_depth=2, current={v4}` を採用した。
- **残存**: `_CACHE_VERSION` を上げずに `GRID_W` だけ変えると版数 dir 配下の旧格子 dir が回収されない（従来も同様＝退行ではない）。恒久解は記述子の多階層化だが、`cache_gc` の入れ子孤児の重複排除が先に必要。
- **事象（コード実測で確定）**: dwell ロールアップの実配置は `indigators/market_profile/api/market_profile_api/gateway/dwell_rollup_store.py:88-89` により `<root>/<sym>/v{version}/g{grid_w}/<day>.npz`（世代 dir が `v4` と `g10` の 2 段＝深さ 3）。一方 `market_profile_api/cache_layout.py:58-60` は `gen_depth=2` かつ `current=frozenset({f"g{GRID_W:g}"})`＝深さ 2 の `v4` を `{"g10"}` と照合する。`tools/cache_gc.py:67-69,90` は `gen.name not in current` の dir を孤児とみなし `shutil.rmtree` する。
- **影響**: `<dwell_root>/JP225/v4` が丸ごと削除対象に列挙される。発火時は全期間 dwell キャッシュ喪失（再ウォームは per-day parquet 逐次読込＝数千日ぶん）。zp znull 側は `<root>/znull/<sym>/b<bp>/` で `gen_depth=2` が正しく、dwell のみ不整合。
- **未検証**: 実ディスク上に `<dwell_root>/JP225/v4/` が現存するか（`ls` 未実施）。ISSUE.md 内に「孤児ゼロ確認」の実行記録があるため、実環境で既に発火したか否かは未確定。**コード上の不整合のみ確定**。
- **原因（SRP 違反）**: 「ディスク配置」というひとつの決定を `dwell_rollup_store.cache_path` と `cache_layout.current_layouts` の 2 箇所が独立に所有している。`market_profile_dwell.py:251` の docstring も旧形 `<root>/<symbol>/g<GRID_W>/...` のまま。`tests/test_cache_layout.py:38` は `gen_depth == 2` を固定値で assert しており実配置と突き合わせないため検出できない。
- **対応方針**: `CacheLayout` の生成責務を各 Store（`DwellRollupStore` / `ZpStore` / `tf_period_disk_cache`）へ移し、`cache_path()` と同一の式から `gen_depth` / `current` を導出させる。`cache_layout.current_layouts()` は各 Store の `layout()` を集約するだけにする。テストは固定値 assert をやめ、`cache_path(sym, day).relative_to(cache_root()).parts` の長さと `gen_depth` の整合を assert する形へ変更する。
- **関連**: ISSUE-091/092/094（tf-period キャッシュ世代）・ISSUE-184（本件を含む SOLID 監査の総括）。

## ISSUE-173: [設計] 参照実装 profit_band が PORTING_GUIDE §2 の 2 規約を未達＝判定基準が逆転（2026-07-26）
- **ステータス**: RESOLVED（2026-07-27・案 a をユーザー承認）
- **事象（実測）**: `indigators/PORTING_GUIDE.md:8-9` は `profit_band` を「本書の原則はすべてこの実装で実証済み」の参照実装と宣言するが、同 `:46-47` の 2 規約を profit_band 自身が満たしていない。
  - 「層境界は `typing.Protocol`（`@runtime_checkable`）で定義」→ `profit_band/src/lwc_chart.py` に Protocol 定義が **0 件**（裸のダックタイピング）。他 9 パッケージは `@runtime_checkable Protocol` を実装済み。
  - 「numpy 配列は `__post_init__` で `writeable=False`」→ `profit_band/src/core.py:26-41` の `DistanceSamples` は frozen のみで `__post_init__` を持たない。他 9 パッケージは `setflags(write=False)` 実装済み。
- **影響**: 「参照実装が基準に従っていない」状態のため、以後の移植者がどちらを模倣すべきか判定できない。実際に参照先の連鎖が発生している（`profit_hlband/src/core.py:196` は「profit_stc 準拠」、`profit_hl_band/src/core.py:178` は「profit_hlband 準拠」と記述し、profit_band を参照していない）。
- **対応方針（いずれかの選択が必要＝承認事項）**:
  - 案 a（推奨）: `profit_band/src/lwc_chart.py` に `_Line` / `_Chart` Protocol を追加し、`DistanceSamples` に `__post_init__`（`setflags(write=False)`）を追加して参照実装を規約へ合わせる（後発 9 パッケージが既に採る形）。
  - 案 b: 規約側を「Protocol 推奨・ダックタイピング可」「配列不変化は DTO が層をまたぐ場合に限る」と緩和し `PORTING_GUIDE.md` §2 を改訂する。
- **備考**: PORTING_GUIDE §2 の最重要含意（core が pandas/matplotlib/lightweight-charts を非 import・出力アダプタが `lightweight_charts` を非 import）は監査対象 26 パッケージ**全数で遵守**されている。本件は残る 2 規約に限定。
- **対応日**: RESOLVED（2026-07-27・**案 a をユーザー承認**）
- **対応結果**: `profit_band/src/lwc_chart.py` に `@runtime_checkable` な `_Line` / `_Chart` Protocol を追加（模倣元＝`btlm_trail/src/lwc_chart.py:34-41`。`profit_stc` 形は `horizontal_line` を要求するが profit_band は非使用のため線のみの形を採用）。`DistanceSamples.__post_init__` で BUCKETS 6 件を `setflags(write=False)`（模倣元＝`price_range_power/src/core.py:157-161`）。配列への書き込みは repo 内 0 件を Grep で事前確認。profit_band 28 passed。
- **関連**: ISSUE-184、ISSUE-187（同じ profit_band の `_resolve_times` 系列名乖離＝未裁定）。

## ISSUE-174: [設計/DIP] core 層 13 本が sys.path を改変し、自身が解決できない repo 根パッケージへ依存（2026-07-26）
- **ステータス**: RESOLVED（2026-07-27・案 a ＋案 a-2 をユーザー承認）
- **事象（実測）**: 以下の `src/core.py` が最内層で `sys.path.insert` を実行する — `profit_adx_needle:44`, `profit_arctan:49`, `profit_mfi:38`, `profit_mfi_macd:45`, `profit_oscillator:51`, `profit_oscillator2:42`, `profit_osi_ma:26`, `profit_rmm:48`, `profit_rmm_macd:54`, `profit_rsi:46`, `profit_rsi_macd:47`, `profit_stc:43`, `profit_volatility:56`。
- **不整合の核心**: 挿入先は `parents[2]`＝`indigators/` のみだが、`from common import typical_price`（`profit_rmm:60`・`profit_rsi:50`・`profit_rsi_macd:51`・`profit_rmm_macd:66`・`profit_oscillator:53`・`profit_arctan:51`）の `common` 実体は repo 根 `/workspaces/app/common/`。`indigators/common` は Glob 実測で不在。**repo 根を sys.path に入れる `src/*.py` は `profit_band/src/loader.py:17-19` の 1 件のみ**。同様に `common_view` を import する 13 箇所（`lwc_chart.py` 9・`plot.py` 4）もどれも repo 根を通さない。
- **影響**: 当該パッケージの import 可否が「cwd が repo 根であること」という暗黙の外部状態に依存し、パッケージが自身の依存解決に責任を持てていない。`indigators/` 配下に `conftest.py` / `pytest.ini` / `pyproject.toml` は不在（Glob 実測）で、`PYTHONPATH` 設定は `indicator_ui/serve.sh` のみ。テスト側の補填も順序が後（`profit_oscillator/tests/test_core.py:23` で `from src import core` を実行した**後**の `:25` で `parents[3]` を挿入）。
- **未検証**: 実際に `pytest` 実行で ImportError が再現するかは未実施（静的構造としての不整合のみ確定）。
- **対応方針（承認事項＝共有モジュール移動を伴う案を含む）**:
  - 案 a: repo 根に `pyproject.toml` の `[tool.pytest.ini_options] pythonpath = ["."]` を置き、`indigators/` 直下に `conftest.py` を追加して解決点を単一化。各 core の `sys.path.insert` 13 本を削除し素の import にする。
  - 案 b: `common` / `common_view` を `indigators/` 配下へ移し `parents[2]` 挿入だけで自己解決可能にする（他パッケージへの波及大）。
- **対応日**: RESOLVED（2026-07-27・**案 a ＋案 a-2 をユーザー承認**）
- **本文の事実誤り 2 件を訂正**:
  1. 「cwd が repo 根であることに依存」は**誤り**。真の供給源は venv の `.pth`（`lightweight-charts-python-main/.venv/.../jp225_chart_paths.pth`＝`/workspaces/app` と `market_profile/api` を登録。生成元 `tools/install_dev_paths.py`）。**repo 根は cwd に関係なく常に sys.path にある**ため、実環境で ImportError は再現しない（`.pth` を除いた条件でのみ FAIL を実測＝潜在）。
  2. 「`common_view` を import する 13 箇所（`lwc_chart.py` 9・`plot.py` 4）」は実測 **20 箇所**（14・6）。
- **対応結果**: repo 根に `pyproject.toml`（`[tool.pytest.ini_options] pythonpath = ["."]` のみ。`[project]` / `[build-system]` / 依存宣言は置かない）と `indigators/conftest.py` を新設し pytest 経路の解決点を単一化。**案 a 単独では本番が壊れる**ことを実測で確定したため（`indigators/` を sys.path に載せる供給源が 13 本の insert だけであり、削除すると `cd api` 起動の本番ロードが `ModuleNotFoundError`。しかも `indicator_ui/api` の pytest は rootdir 経由で `conftest.py` を読むため**本番が壊れていても緑になる**）、案 a-2 として `adapter/compute/call_binding.py` に冪等な `_ensure_indigators_on_path()` を新設し、実行時の解決点をロード境界へ一本化した（同ファイルが元々 docstring で宣言していた設計）。13 本の `sys.path.insert` を削除。
- **検証**: 36 スイート 3311 件で件数・合否の差分 0 行／本番ロード 13/13・全 24 パッケージ成功／`.pth` を除いた fresh clone 条件でも 13/13 成立／`serve.sh` のツール経路 OK。sys.path の重複登録も 13→1 に改善。
- **副次対応**: 13 本の `demo.py`（standalone entry point）へ `indigators/` の登録を追加。A/B 実測で「core から削除すると demo.py が `No module named 'moving_averages'` で壊れる」ことを検出したため、最内層から entry point へ移設した（既存慣行 `profit_adx_needle/analysis/adx_step1_causality.py:17-18` と同型）。
- **未実施**: `profit_band/src/loader.py` の repo 根挿入は寄せられない（`lwc_demo.py` は call_binding も pytest も経由しないため単一解決点の射程外。削除すると `No module named 'marketdata'` を実測）。
- **関連**: ISSUE-176（同種の DIP 違反の別形態）・ISSUE-184。

## ISSUE-175: [潜在バグ] profit_rmm と profit_rmm_macd の複製コードが NaN 伝播で既に挙動乖離（2026-07-26）
- **ステータス**: BLOCKED（2026-07-27・元 MQL 不在で裁定不能。裁定保留をユーザー承認）
- **事象（実測）**: `profit_rmm_macd/src/core.py:97-192, 201-302`（`_series_avg` / `_series_std` / `oscillator_span` / `rolling_span` / level_count 合算パイプライン）は `profit_rmm/src/core.py:80-181, 262-317` の複製。同 `core.py:214-216` の docstring は「verbatim 複製」と主張するが、**実測では `profit_rmm_macd/src/core.py:293-299` に span の NaN 伝播ブロックがあり、`profit_rmm/src/core.py:298-317` には存在しない**＝既に分岐している。
- **影響**: 同一と宣言された 2 実装が異なる値を返し得る。片方だけ修正すると乖離が拡大する。同一アクター（funLevelCount / スパン定義の変更者）が 2 ファイルの同時変更を強いられる（SRP / OCP / LSP の複合違反）。
- **未確定（要調査）**: どちらが正解かは**未確定**。元 MQL（`PRO!fitRMM.mq4` / `PRO!fitRMMMACD.mq4`）を未読のため、NaN 伝播の有無どちらが移植元の挙動かを判定できていない。
- **対応方針**: 1. 元 MQL 2 本を読み、NaN 伝播の正解を確定する（**この裁定が先**）。2. 確定後、共通部を `profit_system`（または新規の統計共有カーネル）へ 1 本化し、両 core は再公開のみにする。
- **対応日**: BLOCKED（2026-07-27・**裁定保留をユーザー承認**。コード変更なし）
- **裁定不能の確定**: `PRO!fitRMM.mq4` / `PRO!fitRMMMACD.mq4` は **`find / -iname "*.mq4"` でファイルシステム全体に 0 件**。参照実装が存在しないため、NaN 伝播の正解を裁定できない。ユーザー厳命（実証なき憶測で進めない）に従い、**どちらへも寄せず現状の挙動を維持**した。
- **追加実測**: 乱数 400 バー＋完全フラット 200 バーの 2 データセットで `compute_rmm(window=120).level_count` と `compute_rmm_level_count(window=120)` を比較 → **数値差 0 件**。両者が分かれ得るのは「非有限 span が存在し、かつ 4 採点のいずれも NaN を生まないバー」に限られ、当該入力は未確認。**乖離は構造上のみで、確認済みの範囲では出力は一致**。
- **実施したこと（記録の是正のみ）**: `profit_rmm_macd/src/core.py` の「verbatim 複製」という虚偽記述 3 箇所（module docstring・含む構造・関数 docstring・セクションコメント）を実態へ訂正し、未裁定である旨を明記。計算ロジックは 1 行も変更していない。58 passed。
- **再開条件**: 元 MQL 2 本の入手。
- **関連**: ISSUE-179（同種のコピペ重複群）・ISSUE-184。

## ISSUE-176: [設計/DIP] btlm_trail_marod・ma_marod の core が兄弟パッケージを絶対パスで動的ロード（並列ロック欠落）（2026-07-26）
- **ステータス**: RESOLVED（2026-07-27）
- **事象（実測）**:
  - `btlm_trail_marod/src/core.py:57` `_BTLM_TRAIL_SRC = Path(__file__).resolve().parents[2] / "btlm_trail" / "src"`、同 `:61-80` でファイルパスから動的ロード。
  - `ma_marod/src/core.py:79` `_MOVING_AVERAGES_CORE = _INDIGATORS_DIR / "moving_averages" / "src" / "core.py"`、同 `:86-102` で同様。
  - いずれも `importlib` / `sys` / `pathlib` を core 層が保持し、抽象も注入点も持たない。
- **影響 1（DIP・SRP）**: 最も抽象度の高い層が兄弟パッケージのファイルシステム配置に直接依存。`btlm_trail/` を移動・改名すると core が壊れる。core が「計算式の変更者」に加え「ディレクトリ配置・モジュール解決方式の変更者」の 2 アクターを負う。
- **影響 2（並列ロック欠落）**: 同一関心の共有実装 `indicator_ui/api/adapter/compute/module_loader.py:25,39-43` は ISSUE-156 対策の `threading.Lock` を持つが、**両コピーともロックを欠く**。`_load_btlm_trail()` / `_load_moving_averages()` は compute 呼出時に遅延実行され（`core.py:100` / `:149`）、compute は `indicator_ui/api/framework/server.py:84` の `ThreadPoolExecutor(max_workers=3)` 上で走る＝初回同時ロードの競合が構造的に露出している。
- **未検証**: 並列 compute 下でのレース実発生は未再現。証拠は「ロックの不在」と「並列実行経路の存在」の 2 点のみ。
- **対応方針**: 1. 暫定（低リスク）: `_load_*` を `module_loader.load_package/load_module` へ委譲するだけで並列リスクは解消する。2. 恒久: core に `TrendLine` 等の Protocol を置いて注入形へ変え、組み立てを Composition Root（`indicator_ui/api/adapter/compute/call_binding.py`）へ移す。`common/marod_bands.py:9-11` が「兄弟具象への依存＝DIP 半成立を common 抽出で対称化」として一度是正した手順と同型（MA・トレンド線側に未適用のまま残存）。
- **対応日**: RESOLVED（2026-07-27・ステップ 1・2 とも実施）
- **対応結果**: ISSUE.md の暫定案（`indicator_ui` の `module_loader` へ委譲）は**採らなかった**。採ると indigators の core 層（最内層）→ indicator_ui の adapter 層（外層）という新規の依存逆流を作り、DIP 違反を別の DIP 違反に置き換えるだけになるため。代わりに `common/module_loader.py` を新設（`common/marod_bands.py` の抽出手順と同型）し、両 core をそこへ委譲。さらにステップ 2 として core に `TrendLineReference` / `MovingAverageReference` の `@runtime_checkable` Protocol と `set_*_reference` 注入点を新設し、未注入時は従来の動的ロード（ロック付き）へフォールバックする形にした（既存呼出元の挙動は完全不変）。
- **レースの実再現に成功（ISSUE 本文の「未検証」を解消）**: 支配的な障害は重複ロードではなく**半構築モジュールの観測**（公開関数が未定義＝呼出時 AttributeError）。`switchinterval=1e-6` × 3 スレッド（`server.py` の `max_workers=3` 相当）で 5 回中 5 回 RACE（`missing_attrs_example=['resolve_source','rolling_ols_window_end']`）。16 スレッドでは重複ロードも観測。**修正後は 16 実行すべて OK**。
- **重要な発見**: `indicator_ui` 版と同形の実装（ロック前に `sys.modules` を素引きする二重チェック）では**半構築観測を防げない**（並置比較で 7/8 vs 0/8）。`sys.modules[name] = module` は相対 import 解決のため exec **前**に登録する必要があり、その登録をロック外の高速経路が読むため。`common` 版は「exec 完了まではキャッシュ命中と見なさない」`_LOADING` ゲートを追加し、`RLock` で入れ子ロードのハングも回避した。→ **ISSUE-185 として別起票し、後に是正済み**。
- **検証**: bit-for-bit 一致（`btlm_trail_marod` 276 配列 296.7 万要素／`ma_marod` 1158 配列 1244.8 万要素、差 0）。btlm_trail_marod 30 passed／ma_marod 43 passed／common 26 passed。
- **残存**: core が兄弟パッケージのパスを `Path` で保持するフォールバック経路は残存。完全な解消は Composition Root（`call_binding.py`）からの注入が前提。
- **関連**: ISSUE-156（module_loader の並列ロック）・ISSUE-174・ISSUE-184・**ISSUE-185（本件の過程で発見した indicator_ui 版の同種欠陥）**。

## ISSUE-177: [潜在破綻/LSP] market_profile の _CACHE_MISS が import 時に既定具象へ束縛され、Port 代替実装で番兵判定が破綻（2026-07-26）
- **ステータス**: RESOLVED（2026-07-27）
- **事象（実測）**: `market_profile/api/market_profile_api/compute/market_profile_zp.py:128` `_CACHE_MISS = zp_cache_miss()` は module import 時に 1 回だけ評価され、既定具象 `ZpStore.CACHE_MISS` を束縛する。判定は `:169, :239` の identity 比較 `if disk is not _CACHE_MISS`。`market_profile_dwell.py:122` も同型。
- **原因**: `compute/store_port.py:29` は `CACHE_MISS: ClassVar[Any]` を Protocol の一部として宣言し、`set_zp_store()`（`:97-101`）は `isinstance` 検査なしで任意の実装を受理する。Port 準拠だが `ZpStore` 派生でない実装を注入すると、その実装が返す `CACHE_MISS` が `_CACHE_MISS` と identity 不一致になる。
- **影響**: `_mgrid_of_day`（`:168-172`）が**キャッシュミス番兵を実データとして受理**し `_MGRID_CACHE` に格納、`closes, open_d = grid`（`:252`）で TypeError。`set_zp_store` / `set_dwell_store` は `market_profile_zp.py:56` / `market_profile_dwell.py:60` から再エクスポートされた公開注入 API のため、既定具象派生以外の注入が全て潜在破綻。
- **現状**: 既存テスト（`tests/test_store_gateway_layering.py:115-119,137` が `_FakeZp.CACHE_MISS = object()` を注入）は `_mgrid_of_day` を通らないため緑。**潜在**であり実発火は未確認。
- **対応方針**: 1. module 定数 `_CACHE_MISS` を削除し、比較箇所（zp:169,239 / dwell:289）を `disk is not zp_cache_miss()` の call-time 評価へ変更。2. `set_zp_store` / `set_dwell_store` / `set_tick_store` に `isinstance(store, ZpStorePort)` ガードを追加（`@runtime_checkable` は付与済み）し、Protocol を宣言だけでなく強制にする。
- **対応日**: RESOLVED（2026-07-27・方針 1・2 とも実施）
- **対応結果（方針 1）**: module 定数を削除し call-time 評価へ。**ISSUE 未記載の第 4 の比較箇所** `compute/market_profile_dwell_warmer.py:64` を発見し同様に修正。再現テストを新規追加し **Red→Green を実出力で確認**（Red 時に ISSUE の予測どおり `TypeError: cannot unpack non-iterable object object` が実発火＝「潜在」が実害であることを実証）。
- **対応結果（方針 2）**: 3 setter に `isinstance` ガードを追加。導入時に既存 fake が Port を満たしていないことが判明（`_FakeZp` は 9 要求中 2 個・`_FakeDwell` は 6 要求中 2 個）。**テストを緩めるのではなく fake を Port 準拠の in-memory 実装へ拡充**し、既存テストに往復 assert を追加して強化した。ガードは CPython 非公開属性 `__protocol_attrs__` に依存しない形へ自己修正済み（欠落列挙はメッセージのみ・判定は `isinstance`）。
- **検証**: 334 passed（byte-parity / layering / cache_layout を含む）。本番結線は setter を経由しない（呼出元 0 件）ことを Grep で実証。`cache_layout.current_layouts()` の `layout()` 呼び出し（ISSUE-172）と両立。
- **残存**: `layout()` は方針どおり Port 契約外のため、`layout()` を持たない Port 準拠の代替 Store を注入すると `current_layouts()` が `AttributeError`（ガードの対象外＝設計上の意図的な範囲外）。
- **関連**: ISSUE-184。

## ISSUE-178: [設計] market_profile の層間 DTO が不変化されておらず、プロセス内キャッシュと呼出元が同一配列を共有（2026-07-26）
- **ステータス**: RESOLVED（2026-07-27）
- **事象（実測）**: `market_profile` 配下で `writeable` / `setflags` の出現は **0 件**（PORTING_GUIDE:47 の「numpy 配列は `__post_init__` で `writeable=False`」未実装）。frozen dataclass は 4 件のみで全て controller / cache_layout 側＝**compute↔gateway 境界の DTO は 0 件**。実際に層を跨ぐのは生 dict（`market_profile_dwell_kernel.py:63` の `{"kmin","dwell","cnt"}`、`market_profile_zp.py:275` の `{"kmin","obs","mean","var"}`）とタプル `(secs, mids)`。
- **影響**: `market_profile_zp.py:241` `_NULL_CACHE[key] = disk` / `market_profile_dwell.py:291` `_DAY_CACHE[key] = disk` はプロセス内キャッシュへ**参照を格納**し、そのまま呼出元へ返す。`tf_period_columns.py:271-273` は `obs_sum[off:...] += r["obs"]` で読み出す。可変配列がプロセス全体で共有されており、in-place 更新が 1 箇所でも混入すればキャッシュが汚染される。
- **未検証**: 現時点で in-place 更新を行う箇所は発見していない（構造的リスクの指摘であり、実害の発生は未確認）。
- **対応方針**: `compute/` に `@dataclass(frozen=True)` の `DayRollup(kmin, dwell, cnt)` / `ZpRollup(kmin, obs, mean, var)` / `TickWindow(secs, mids)` を定義し、`__post_init__` で `arr.setflags(write=False)` を課す。Store の save/load・`_day_rollup`・`_zp_day_rollup`・`_rollup_ticks`・`load_window_ticks` の戻り値をこれに揃える。
- **対応日**: RESOLVED（2026-07-27）
- **対応結果**: `compute/rollup_dto.py` を新設し `DayRollup(kmin,dwell,cnt)` / `ZpRollup(kmin,obs,mean,var)` / `TickWindow(secs,mids)` を frozen dataclass ＋ `__post_init__` の `setflags(write=False)` で定義（模倣元＝`price_range_power/src/core.py:157-161`）。`_rollup_ticks` / `_day_rollup` / `_zp_day_rollup` / `load_window_ticks` と Store の save/load、`tf_period_columns` の読み出しを DTO へ統一。
- **前提確認の結果**: in-place 書き込み箇所は **0 件**（`tf_period_columns` の `obs_sum[...] += r.obs` は `obs_sum` が呼出元所有の自前確保、`r.obs` は右辺のみ）。DTO 化による dtype 変換も 0 件（元から float64）。
- **検証**: 323 passed。`.npz` の byte 等価を 3 系統（メンバ名／各メンバの dtype・shape・生 bytes の SHA-256／日時フィールドを零化した raw bytes）で確認し dwell・zp とも BYTE-EQUIVALENT。`load_window_ticks` も値・dtype が HEAD と完全一致。
- **ISSUE 記述の訂正**: 「`controller/tf_period_columns.py`」の実パスは `compute/tf_period_columns.py`。
- **残存**: `_freeze` は `np.asarray` が同一オブジェクトを返す場合に呼出元の配列を in-place で read-only 化する（参照実装 `price_range_power` と同じ性質）。全 7 構築サイトで入力が新規配列であることを確認済みだが、既存配列から DTO を構築する新規コードには注意が必要。`compute` 内シム `_load_window_ticks` は範囲外 consumer（`analysis/mp_stats/tick_dwell_check.py:84`）があるため 2 値タプルのまま残置。
- **関連**: ISSUE-173（同規約の参照実装側の未達）・ISSUE-184。

## ISSUE-179: [設計/OCP] 横断コピペ重複（loader 11・_resolve_times 約18・norm_ppf/OLS・MA 写像）（2026-07-26）
- **ステータス**: RESOLVED（2026-07-27・一部は挙動差のため意図的に未統合）
- **事象（実測）**:
  - `loader.py` が 11 パッケージで重複（`profit_adx_needle:24-64`・`profit_osi_ma:24-64`・`profit_hlband:25-71`・`tgp_btlm:25-66`・`price_range_power:24-64` ほか）。共有実装 `marketdata/ohlc_csv_loader.py:16-57` が既に存在し `profit_band/src/loader.py:21` は shim 化済みなのに未使用。**`profit_hlband:61-62` のみ空 CSV ガードを持ち既に乖離**。
  - `_resolve_times`（PORTING_GUIDE §5 の時刻解決順序）が約 18 箇所へ複製（`profit_band/src/lwc_chart.py:66` ほか全指標パッケージ）。戻り値型も `moving_averages` のみ `list`、他は `pd.Series` で非一貫。
  - `norm_ppf` の Acklam 係数 20 個と分岐しきい値 `0.02425` が完全一致（`tgp_btlm/src/core.py:124-174` ↔ `btlm_trail/src/core.py:50-77`）。OLS 窓当てはめも同様（`tgp_btlm/src/reference.py:46-69` ↔ `btlm_trail/src/core.py:99-122`）。
  - `_SOURCE_TO_APPLIED` 8 択写像・`_MA_FUNCS` 種別写像・`_FROM_ZERO` warm-up 規約が `moving_averages/src/lwc_chart.py:37-57` ↔ `btlm_trail/src/core.py:38-47` ↔ `ma_marod/src/core.py:66-75,83,105-113` の 3 重複製。
  - `market_profile`: as-of 経過分クランプ規則が 3 箇所（`market_profile_zp.py:257-259`・`tf_period_columns.py:91-92,136-138`）。tf-period キャッシュ協調が 4 メソッドへ複製（`tf_period_profile_controller.py:141-343`）。
  - `indicator_ui`: tf→秒テーブルが `forming_bar.py:59-62` と `marketdata/tf_meta.py:36-39` で 7 エントリ二重定義。
- **影響**: 単一規約の変更が最大 18 ファイルの同時修正を要求する（拡張ではなく改変＝OCP 違反）。既に 2 箇所で乖離が発生済み（空 CSV ガード・ISSUE-175 の NaN 伝播）。
- **対応方針**: 1. `loader.py` を `marketdata.ohlc_csv_loader` への shim へ統一（空 CSV ガードの採否を先に確定）。2. `_resolve_times` / `_emit_line` / `_Chart`・`_Line` Protocol を `common_view` または新規 `common/lwc_adapter.py` へ 1 本化。3. `norm_ppf` / OLS を `common/` の共有プリミティブへ抽出（`common/marod_bands.py` で実績のある手順）。4. `_SOURCE_TO_APPLIED` を `common/applied_price.py` へ移送。5. tf→秒は `TF_BAR_SEC` から `NON_FLOORABLE_TF` を除外する導出へ置換。
- **対応日**: RESOLVED（2026-07-27・全 5 項目＋market_profile 部を実施。一部は挙動差のため意図的に未統合）
- **本文の事実誤り 3 件を訂正（いずれも実測）**: 1. loader は「11 パッケージ」でなく **17 本＋shim 1 本**。2. 空 CSV ガードは「`profit_hlband` のみ」でなく **`profit_hlband` と `profit_hl_band` の 2 本**。3. `_resolve_times` は「約 18 箇所」でなく **21 箇所**。
- **項目 1（loader・ユーザー承認＝挙動不変の一本化）**: AST 同値ハッシュで同値類 4 種を確定（`load_ohlcv_csv`/OHLCV 6 本・`c.lower()` 5 本・`str(c).lower()` 4 本・空ガード付き 2 本）。乖離は 4 軸（`require=` kwarg / 列名 cast / 空 CSV ガード / 関数名・必須列）。共有実装 `marketdata/ohlc_csv_loader.py` に `read_ohlc_csv_with_policy(...)` を新設して 4 軸をパラメータ化した上位集合とし、17 本を薄い shim 化。**既存公開 API `load_ohlc_csv` はシグネチャ・既定挙動とも不変**。
  - 検証: 19 対象 × 52 観測点 = **1026 点で差分 0**（例外の型・メッセージ文字列・`signature` 文字列・戻り値の dtype/index/`to_csv` の SHA-256 まで比較）。ネガティブコントロール 5 種で 124/94/9/6/18 件の差分検出を実証。
  - 設計上の要点: `read_csv_kwargs` を `**kwargs` でなく位置引数の Mapping で受ける（`**kwargs` だと方針名 `require` が pandas 行きを横取りし、現行の `TypeError` が消える）。関数はモジュール直下の `def` で維持（factory closure 化すると `TypeError` メッセージに qualname が漏れる）。注釈は `TYPE_CHECKING` で導入（クォート注釈は PEP 563 下でシグネチャが変わる）。
- **項目 2（`_resolve_times` ほか）**: `common_view/lwc_adapter.py` を新設（`common/` は「numpy のみ依存」を明示しており pandas を持ち込めないため表示仕様層を選択）。`_resolve_times` 定義 **21→9**（AST 同値の 12 本のみ統合）、系列 Protocol のクラス定義 **20→0**、`_emit_line` **2→0**。残り 9 本は挙動差のため意図的に未統合（7 本は `c.lower()` で非 str 列名に `AttributeError`／`moving_averages` のみ `list` 返し／`profit_band` は DatetimeIndex 経路の系列名が異なる＝**ISSUE-187** として別起票）。`_Chart` は要求メソッドが 7 形に分かれるため共通化せず各パッケージに残置。
- **項目 3（norm_ppf / OLS）**: `common/normal_dist.py` / `common/ols_fit.py` を新設。`norm_ppf` 定義 **2→0**（3 分岐を跨ぐ 14,007 点で `tobytes()` 不一致 0 を確認して統合）。**OLS の leverage は 2 形を分離保持**（端点ベクトル形と einsum 全行形は 3000 試行中 232 件で最終ビット不一致＝統合不可を実測）。
- **項目 4（`_SOURCE_TO_APPLIED`）**: `common/applied_price.py` へ移送。`_MA_FUNCS` は `moving_averages` の `_MA_ON_BUFFER`（`MA_TYPES` の導出元）への別名にして二重情報源を作らず、`_FROM_ZERO` は `core.MA_FROM_ZERO` を単一情報源化。`ma_marod` の `_FROM_ZERO` のみ残置（ISSUE-176 の Protocol 設計＝兄弟具象への静的依存を断つ形と衝突するため。Protocol へ定数を持たせるのは契約変更＝要裁定）。
- **項目 5（tf→秒）**: `forming_bar.py` の `_FIXED_TF_SECONDS` を `TF_BAR_SEC - NON_FLOORABLE_TF` の導出へ置換。事前実測で値・反復順とも既存リテラルと完全一致を確認してから実施。
- **market_profile 部**: as-of 経過分クランプは **ISSUE 未記載の 4 箇所目**（`market_profile_zp._zp_partial_rollup`）を発見。合成形が異なるが `max(1,min(G,e)) == min(G,max(1,e))`（G≥1）で恒等であることを総当たりで実証し、kernel（`market_profile_zp_kernel.asof_col_hi`）へ一本化。tf-period キャッシュ協調 4 メソッドは共通部を `controller/tf_period_cache.py` の `TfPeriodDayCache.resolve(...)` へ抽出し、**LRU 辞書と上限も協働子へ移送**（host の private を触らないことを AST ガードテストで固定）。ISSUE-172 の世代タグビルダを唯一の情報源として維持。
- **検証（全体）**: 678 比較項目 差分 0（項目 2〜5）＋1026 点 差分 0（項目 1）＋54 ケース bit 一致（market_profile 部）。ネガティブコントロールは計 19 種で全て差分検出を実証。全 36 スイート **3445 passed / skip 0 / fail 0**。本番ロード 24/24・standalone 20/20。JS 4 スイートも緑（311 / 749 / 266+既知1 / 42）。
- **検証手順の教訓**: stale pyc により「復元後も差分 6 件」という偽差分を実際に踏んだため、全ハーネスで `__pycache__` 全消去＋`PYTHONDONTWRITEBYTECODE=1` を必須手順とした。対照は `git show HEAD:` ではなく**着手前の作業ツリー全体のスナップショット**（並列作業の未コミット変更が混入するため）。
- **関連**: ISSUE-175・ISSUE-184・ISSUE-185・ISSUE-187。

## ISSUE-180: [設計/OCP] 指標 1 件の追加に 4〜5 ファイル・2 言語の同時改変が必要（2026-07-26）
- **ステータス**: PARTIALLY RESOLVED（2026-07-27・back 側完了で確定。front は現状維持をユーザー承認）
- **事象（実測）**: 指標を 1 件追加するには `indicator_ui/api/adapter/compute/call_binding.py:289`（`_TABLE`）／`api/adapter/compute/catalog_schema.py:28`（`PARAM_DEFAULTS`）／`web/js/usecase/catalog.js:634`（`REGISTRY`）／`api/tests/golden/catalog_defaults.json` の同時改変が必要。足内更新対象なら `web/js/usecase/intrabar_forming_ids.js:13` も加わる。`api/tests/test_catalog_schema.py:23-47` は乖離を検出するが、拡張点の単一化はしていない。
- **併存する良好な実装**: `call_binding.py:263-286` の `_BindingSpec` 宣言テーブルは `thread_affinity` / `time_required` / `latest_meta` / `preprocess` をデータ宣言化済みで、`framework/server.py:254` らは指標名を一切知らない（`test_solid_binding_spec_guards.py:43-108` がガード）。問題は宣言テーブルの適用範囲が params / series 定義まで及んでいないこと。
- **対応方針**: `call_binding._TABLE` を「指標記述子」へ拡張し `params_defaults` / `series_defs` / `intrabar_forming` / `actor_driven` を同一エントリへ集約。`catalog_schema.PARAM_DEFAULTS` は `_TABLE` からの導出関数に置換。front の `catalog.js` は起動時 `GET /catalog` の payload から `IndicatorDef` を組み立て、静的リテラルは最小 fallback のみ残す。指標追加を 1 ファイル 1 エントリへ。
- **対応日**: PARTIALLY RESOLVED（2026-07-27・**back 側完了で確定・front は現状維持をユーザー承認**）
- **対応結果（back）**: `_BindingSpec` に `params_defaults` を追加し `_TABLE` を指標記述子化（全 22 compute_id の param 既定値を同一エントリへ集約）。`indicator_param_defaults()` を新設（deep copy 返却・宣言漏れ/二重宣言を `ValueError` 検出）し、`catalog_schema.PARAM_DEFAULTS` の既定値リテラル 149 行を導出値へ置換。`_DEFAULT_SAMPLES` も `_TABLE` 由来へ変え `catalog_schema` への依存を反転。指標追加手順を `call_binding.py` の docstring に明記。**back 側は「指標追加＝`_TABLE` 1 エントリ」になった**。
- **検証**: 418→418（+新規 6 件で 418）。`golden/catalog_defaults.json` は**無改変**のまま `PARAM_DEFAULTS == json.load(golden)` を確認。`handle_catalog()` の応答を編集前に退避して文字列完全一致（`BYTE_EQUAL: True`。key 順維持のため `_TABLE` のエントリ順を配信順へ揃え、順序をテストで固定）。新規テストの Red 観測（変更前コードに当てて 6 failed）で検出力も実証。import 順反転による循環リスクを 5 経路で実行検証。
- **未実施（front・方針 3）とその理由**: 現行 `/catalog` は `{ok, catalog:{compute_id:{param:default}}}` で **param 既定値しか運ばない**。`IndicatorDef` の構築には `displayNameKey` / `category` / `placement` / 制約式 / `seriesNamePattern` / 日本語ラベル・tooltip が必要だが back 側に情報源がなく、これらを back へ移すのは責務の逆流（`catalog_schema.py` の現行設計注記「純 UI 情報は front に残す」に反する）。**ユーザー裁定により front は現状維持で確定**。front は引き続き `catalog.js`（足内更新対象なら `intrabar_forming_ids.js`）の宣言が必要。
- **実施不能と判明した項目**: `actor_driven` の `_TABLE` 集約は不能（`ACTOR_DRIVEN_COMPUTE_IDS` の唯一の要素 `market_profile` は独立アクター所有で `_TABLE` に登録されていない）。`intrabar_forming` の集約は消費側が module-level `Set` を同期 import しており runtime fetch 化で初期値が空になるため停止。
- **残存**: `indicator_param_defaults()` は import 時評価のため、`_TABLE` に `params_defaults` 未宣言のエントリを足すと `adapter.compute` の import 自体が失敗する（従来はテスト失敗のみ）。fail-fast の強度が上がった点は意図的変更。
- **関連**: ISSUE-184。

## ISSUE-181: [設計/SRP] 神クラス 3 件（indicator_controller.js 1103 行・properties_dialog.js 1057 行・market_profile_actor.js 852 行）（2026-07-26）
- **ステータス**: RESOLVED（2026-07-27・主要アクターの抽出を完了）
- **事象（実測）**:
  - `indicator_ui/web/js/adapter/front/indicator_controller.js:130-1103` — 50 超メソッド。系列名照合(`:260-315`)／描画振分(`:321-380,607-615`)／compute オーケストレーション・並行制御(`:211-239,488-714`)／永続化・復元(`:730-802`)／DOM 配線・ダイアログ・凡例(`:900-1102`) の 5 アクターが同居。
  - `indicator_ui/web/js/adapter/front/properties_dialog.js:39-1041` — ダイアログ枠＋8 種コントロール生成(`:346-618`)＋スタイルペイン(`:648-763`)＋可視性ペイン(`:765-788`)＋検証(`:883-996`)。加えて `_buildControl` の switch(`:323-344`) が OCP 違反（usecase 側 `form_model.js:16-25` はテーブル化済みで adapter だけ取り残し）。
  - `market_profile/web/js/adapter/front/market_profile_actor.js:92-852` — 注入依存 14 個。表示モード遷移／リプレイ・スクラブ／日別タイル＋自動ズーム／tick 逐次成長／チャートレイアウト／URL パラメータ写像の 6 アクター。
- **関連する分割不全**: `timeframe_controller.js:38,55,58,74,76` / `market_profile_controller.js:125,152,171,187` は抽出済み協働子が host の private フィールドを直接代入しており、クラスは分割されたが**状態所有は host のまま＝責務は未分離**。
- **対応方針**: いずれも「状態も一緒に移す」抽出を行う（協働子が host ではなく自身の状態を持つ）。`mp_primitive_roles.js:14-42` の ProfileSink / TfPeriodSink 分割が同手法の成功例。`properties_dialog` はコントロール生成を `CONTROL_BUILDERS` registry（純関数モジュール）へ外出しし `_buildControl` を 1 行へ。
- **対応日**: RESOLVED（2026-07-27・主要アクターの抽出を完了。残置分は下記）
- **対応結果（行数）**: `properties_dialog.js` 1057→770／`indicator_controller.js` 1103→925／`market_profile_actor.js` 852→**496**。
- **新規協働子 9 件（すべて状態ごと移送）**: `property_control_builders.js`（`CONTROL_BUILDERS` 凍結表・`_buildControl` は 1 行委譲）／`series_name_matcher.js`（純関数化）／`indicator_dialog_controller.js`（`_filter` 所有）／`recompute_gate.js`（`_depth`・`_lastStartMs` 所有）／`series_render_router.js`（描画振分）／`indicator_state_store.js`（`_restoreInFlight` 所有）／`mp_replay_scrub.js`／`mp_chart_layout.js`／`mp_fetch_params.js`／`mp_tick_growth.js`／`mp_mode_transition.js`／`mp_session_tiles.js`。
- **分割不全の解消**: `timeframe_controller.js` の `host._timeframe = / host._recomputeDepth += / -= / host._recomputeLastStartMs =` と `market_profile_controller.js` の `host._state =` ×4、計 **8 箇所の host private 直接代入を全廃**。`TIMEFRAME_HOST_CONTRACT` / `MARKET_PROFILE_HOST_CONTRACT` も同時更新。
- **検証**: 4 スイート緑（indicator_ui 749／market_profile 311／replay_ui 266+既知1（ISSUE-170）／unified_ui 42）。**Red 観測**（新規の構造テストを `git show HEAD:` で差し戻した変更前コードに当てて失敗することを実出力で確認）と**変異検出**（抽出後のロジックに意図的変異を入れて既存テストが失敗＝検出力が移設後も及ぶことを実証）を各段階で実施。`node build.mjs` で `out/prototype.html` を再生成（1,330,265 bytes・`node --check` 通過・新規 const/class はバンドル内で各 1 回のみ宣言）。
- **ISSUE-164 領域の保全**: `_applySessions` / `_focusSessionsPending` の抽出時、`setVisibleRange` / `fitContent` / `scrollTo` / `applyOptions` / `autoScale` / `setUserInteraction` / `focusTimeRange` の**実行行を before/after で diff して差分 0 行**を実測。発火順序も逐語コピー。
- **未実施（残タスク）**: 1. `indicator_controller.js` の compute オーケストレーション本体（`applyIndicator` / `recomputeInstance` / `_computeInstance` / `recomputeAllApplied`）は UC-02/03 そのもの＝host の本務と判断し残置（切り出すと `_state` の所有権も動き影響半径が大きい）。2. 凡例・歯車ダイアログ（`_renderLegend` / `_onGear` / `_applyDialogResult` / `_gearRecompute`）は `_legendView` を dialog controller と共有しており View 所有権の再設計が必要。
- **残存リスク**: ブラウザ実 UI での確認は未実施（サーバ起動禁止）。挙動不変の根拠は 4 スイート緑・A/B スモーク・変異検出・バンドル静的検証に限られる。
- **関連**: ISSUE-184。

## ISSUE-182: [設計/ISP・LSP] 公開契約の越境・太いポート・戻り値契約の非一貫（2026-07-26）
- **ステータス**: PARTIALLY RESOLVED（2026-07-27・項目 1〜4 完了。項目 5 は挙動変更のため未実施）
- **事象（実測）**:
  - `profit_adx_needle/src/core.py:54-59` が `profit_system.src.core` の非公開 `_normalize` / `_ps_average` / `_ps_std_ema` / `_unit_conversion` を直接 import。`profit_system/src/__init__.py:30-36` の `__all__` は別の 5 件のみ＝公開面が機能していない。
  - `moving_averages/src/core.py:143,186,227,279,336` の `(rates_total, prev_calculated, begin, period, price, buffer)` 6 引数 out-param 契約。`exponential_ma_on_buffer` の本番呼出 17 本すべてが `prev_calculated=0, begin=0` 固定（Grep 実測）。全クライアントが未使用 2 引数と事前 `np.zeros(n)` 確保を強制される。
  - `market_profile/compute/tick_store_port.py:44-46` の `read_ticks` は外部クライアント 0 件（gateway 自クラス内部とテストのみ）。`compute/store_port.py:26-62` の `ZpStorePort` は 7 メソッド混載（cache_root / mgrid×3 / znull×3 / signature）で、`tick_store_port` が自ら実施した役割別分割が未適用。
  - `indicator_ui/api/usecase/compute_indicators.py:80-89` は 5 依存のうち `DatasetPort` のみ Protocol で、残り 4 つが `Any` / `Callable`＝契約未定義（PORTING_GUIDE:46 と不整合）。
  - LSP: `profit_rsi_macd/src/rsimacd.py:90,125` が `compute_rsimacd(high, high, low, close, ...)` と `open_` にダミーで `high` を渡す。`profit_hl_band/src/hl_band.py:78,103` は `-> dict[str, float]` 宣言に bool `available` を混入。`build_*` の戻り値契約が「指標列のみの新規 DataFrame」8 件 vs 「入力 df.copy() に列追加」4 件で分裂。
- **対応方針**: 1. `profit_system` の 4 関数を public 名へ昇格し `__all__` に載せる（または「これらは公開契約」と明記する）。どちらかに決める。2. `moving_averages` に `ma(price, ma_type, length) -> ndarray` 形の狭いラッパを追加（既存 6 引数版は MQL 1:1 資産として残置）。3. `read_ticks` をポートから削除し gateway の private へ降格、`ZpStorePort` を役割別に 3 分割。4. `FormingBarPort` / `IndicatorComputePort` / `ComputeDispatchPort` を `@runtime_checkable Protocol` で定義。5. `compute_rsimacd` から `open_` を削除、`hl_band_levels` の異種混在を frozen dataclass へ、`build_*` を「指標列のみの新規 DataFrame」へ統一（**4 件は挙動変更＝要承認**）。
- **対応日**: PARTIALLY RESOLVED（2026-07-27・項目 1〜4 完了。**項目 5 はユーザー裁定により未実施**）
- **項目 1（`profit_system` 公開契約）**: 「public 名へ昇格」を採用。根拠 2 点（実測）: (a) `profit_adx_needle/src/core.py` が façade を素通りして `profit_system.src.core` の深い経路を叩いており、「明記」では `__all__` が依然機能しない。(b) 既存の公開命名が MQL 名 1:1 写像（`PS_GetLevelCountValue`→`ps_level_count`）で確立済みで、`ps_average` / `ps_unit_conversion` は同規則の延長。`_normalize`→`ps_normalize` ほか 4 件を改名し façade に載せ、**旧名は同一関数オブジェクトの別名として残置**（`is` 同一性をテストで固定）。越境参照は Grep で全数特定済み。
- **項目 2（`ma` 狭いラッパ）**: `moving_averages` に `_MA_ON_BUFFER` 表・`MA_TYPES`・`ma(price, ma_type, length)` を**追加のみ**で新設（既存 6 引数版は 1 行も変更せず）。呼出元 **16/17 本**を移行。`profit_rmm_macd/src/core.py` の `_ema_from` 1 本のみ意図的に直呼び残置（buffer が `np.full(m, np.nan)` 初期化で、0 初期化だと偽の 0.0 が活性区間へ混入するとコードで明示されているため）。
  - 検証: 実データ 50,000 本 × 4 種別 × 14 長さ = **56 組合せで bit-for-bit 一致**（`period>n` / `period<=1` の退化系含む）。core 層 8 パッケージ・lwc_chart 層 16 指標でも差分 0。**ネガティブコントロール**で `+1e-3` の摂動を core 層 8/8 検出（`+1e-12` で 6/8 なのは PS プリミティブの `NormalizeDouble(_,5)` が吸収するため）＝ハーネスの検出感度を実証。
  - 途中で `RsiResult` / `MfiResult` のローカル変数 `ma` と module-level `ma` のシャドウイングによるバグを自ら混入 → テストで即検出 → `ma_arr` へ改名して修正。
- **項目 3（ISP 分割）**: `read_ticks` は外部クライアント **0 件**（gateway 自クラス内部とテストのみ。ISSUE-133 で窓復号を gateway へ移した際に呼出が消え、Port 上の宣言だけが取り残されていた）を確認して `_read_ticks` へ private 降格。`ZpStorePort` は `ZpCacheRootPort` / `ZpDayInvalidationPort` / `ZpMgridStorePort` / `ZpNullStorePort` の 4 Protocol ＋合成へ分割（`day_source_signature` は `_mgrid_of_day` と `_zp_day_rollup` の双方が呼ぶため `CACHE_MISS` とともに共通基底へ）。狭い getter `zp_mgrid_store()` / `zp_null_store()` を追加。
  - 検証: **メンバ集合と `isinstance` 判定が分割前後で完全一致**（既定具象 3・fake・部分実装 4 の計 7 型で全判定が AGREE）。
- **項目 4（usecase の契約定義）**: `usecase/compute_ports.py` を新設し 5 Protocol を定義。「4 つ目の依存」の実体は `compute_error`（例外**型**として注入され `exc.error_type` / `exc.message` が `ComputeResult` に載る＝独立契約）と実測で特定。ディスパッチは `min_tail`（キーワード専用）が latest のみに存在するため単一 Port に束ねられず 2 Protocol に分割。Port 宣言が具象の実シグネチャと一致することを照合テストで固定（推測を排除）。`isinstance` 強制は挙動不変を優先して入れず、型注釈＋回帰ガードで固定（参照実装 `DatasetPort` と同じ扱い）。fake は Port 準拠へ拡充（テストの緩和は 0 件）。
- **項目 5（未実施・要承認のまま）**: `compute_rsimacd` からの `open_` 削除 / `hl_band_levels` の frozen dataclass 化 / `build_*` の戻り値契約統一（8 件 vs 4 件の分裂）。**ユーザーが「挙動不変の範囲に限定」を選択したため今回は着手していない**。
- **検証（全体）**: indicator_ui/api 426 passed・market_profile/api 344 passed・analysis 66・marketdata 194（いずれも skip 0）。本番ロード 22/22 → 24/24 成功。
- **残存**: `usecase/serve_candles.py` の `forming_bar: Any` は同じ欠陥クラスだが指定範囲外で未着手（7 メソッド面のため別 Port が必要）。`ZpStorePort.cache_root` も Port 経由の外部クライアント 0 件で降格候補だが、メンバ集合不変の要請から合成 Port に残置（降格は `isinstance` の意味論を変えるため要承認）。
- **関連**: ISSUE-184。

## ISSUE-183: [設計/DIP] 依存方向の残穴（usecase→adapter 逆流・compute→gateway 循環・ポートの pandas 漏出）（2026-07-26）
- **ステータス**: RESOLVED（2026-07-27・shim のファイル削除のみ保留）
- **事象（実測）**:
  - `indicator_ui/api/usecase/dataset_port.py:50` が `from adapter.gateway.composition import default_dataset_port`＝**内側が外側を import**（関数スコープの遅延 import）。`tests/test_no_usecase_dependency.py:20` は行頭 import のみを禁止し本箇所を明示的に許容している。
  - `market_profile/compute/market_profile_dwell_store.py:11` / `market_profile_zp_store.py:11` が `from ...gateway.… import *`＝**module-level の内→外**。本番参照ゼロ（テスト 3 ファイルのみ）。`tests/test_store_gateway_layering.py:90` が `_REEXPORT_SHIMS` として免除する既知の穴。
  - `market_profile/compute/store_port.py:118` → `gateway/composition.py:36` → `compute/market_profile_zp.py:54` の遅延 import 循環（Service Locator 化）。
  - `market_profile/compute/tick_store_port.py:40,44` — compute 所有のポート契約が pandas 型で規定（`day_files` の実引数は実測で `pd.Timestamp`、`read_ticks` は "DataFrame 互換" を返す）。DIP でインフラ依存を切ったつもりが型契約で貫通。
  - `market_profile/gateway/composition.py:40,45,62,65` — Composition Root が compute の module private（`_ZP_CACHE_ROOT` / `_ZP_CACHE_VERSION` / `_CACHE_ROOT` / `_CACHE_VERSION`）を読む。永続化設定（偶有的性質）が本質層に居住。
  - `indicator_ui/api/adapter/controller/candles_controller.py:12,28,30,34` — `/candles`・`/forming_bar` が `DatasetPort` を経由せず `marketdata.dataset` を直呼び。DIP 適用が `/compute` のみに限定＝非対称。
- **対応方針**: 1. `dataset_port()` の遅延 import を廃し、`framework/server.py`（真の Composition Root）で起動時に `set_dataset_port(...)` を 1 回呼ぶ。同時に `/candles`・`/forming_bar` の業務手順を usecase へ移し `DatasetPort` 経由へ統一。2. compute 側 shim 2 ファイルはテストの import を gateway へ書き換えたうえで削除し、`_REEXPORT_SHIMS` 免除も撤去（**既存ファイル削除＝要承認**）。3. `TickReaderPort.day_files` の引数を UNIX 秒 int へ変え、`pd.Timestamp` 変換を gateway 内部へ押し込む。4. `_CACHE_ROOT` / `_CACHE_VERSION` を gateway 側へ移送。
- **対応日**: RESOLVED（2026-07-27・6 項目すべて実装。**shim のファイル削除のみユーザー裁定により保留**）
- **項目 1（usecase→adapter 逆流）**: `framework/server.py`（真の Composition Root）で `install_default_ports()` を import 時に 1 回呼ぶ push 形へ反転し、`usecase/dataset_port.py` から adapter への import を撤去。併せて ISP 分割（`RefValidationPort` / `OhlcFramePort` / `CandleSeriesPort` ＋合成 `DatasetPort`（メンバ集合不変）/ `CandleDatasetPort`）。`tests/test_no_usecase_dependency.py` の**明示的許容を撤去**し、正規表現を `^(from|import)` → `^\s*(from|import)` に強化して関数スコープの遅延 import も違反扱いに。変異検査（インデント付き import を注入）で強化の実効性を実証。
- **項目 2（compute 側 shim）**: **ファイル削除は保留**（ユーザーが「挙動不変の範囲に限定」を選択）。テストの import を gateway 直参照へ移行し**消費者側の参照はゼロ**になった。`test_store_gateway_layering.py::test_old_compute_store_paths_reexport` のみ旧パス参照を残置（gateway 直参照に書き換えると `assert GwDwell is GwDwell` の恒真式に退化し契約検証が消えるため）。**次段の承認事項**: shim 2 ファイル＋当該テスト＋`_REEXPORT_SHIMS` 免除を同時撤去する形。
- **項目 3（遅延 import 循環）**: `market_profile_api/__init__.py` をパッケージの Composition Root 化（`install_default_stores()` を 1 回）。Python が submodule より先に親 `__init__` を実行するため**結線漏れが原理的に起こらない**（6 通りの import 起点で検証）。`store_port` / `tick_store_port` は `set_default_*_store_factory` の push 形へ。
- **項目 4（ポートの pandas 漏出）**: `TickReaderPort.day_files` を **UNIX 秒 int 契約**へ。`pd.Timestamp(int(v), unit="s")` の変換を gateway 内部へ押し込み（`unit="s"` 必須＝素の int は ns 解釈）。`gateway/day_bounds.py` を新設し日境界を整数演算化。**呼出元 8 件を全数 Grep で特定して追随**（範囲外 consumer `analysis/mp_stats/tick_dwell_check.py` は `day_files` を使わないことも確認）。`zp_store` / `dwell_rollup_store` は pandas import が不要化したため撤去。実データ 12 窓（最大 164,827 tick）で新旧一致、`utc_day_start` は `pd.Timestamp(s,unit="s").normalize()` と 7 値で一致、誤って `pd.Timestamp` を渡すと TypeError で loud に失敗することも確認。
- **項目 5（永続化設定の移送）**: `gateway/cache_settings.py` を新設し `DWELL_CACHE_ROOT/VERSION` / `ZP_CACHE_ROOT/VERSION` の単一情報源に。compute から `_ZP_CACHE_ROOT` 等の module private を**移送ではなく撤去**（複製を作らない）。
- **項目 6（`/candles`・`/forming_bar`）**: 業務手順を `usecase/serve_candles.py` へ移し `DatasetPort` 経由へ統一。`candles_controller.py` は Controller（`limit`/`now` の文字列解釈）＋ Presenter のみへ縮退。
  - **byte 等価の検証**: 当初は変更前後を別プロセスで採取したが、`live_tick_watch.py --stream` の非原子的追記によるデータドリフトで 10 ケースが不一致になった（→ **ISSUE-186** として起票）。**変更前 handler を写経して同一プロセス・同一データ状態で新旧を交互に呼ぶ**方式へ切替え、**30 ケースで mismatch 0**（正常系・境界・エラー系・パストラバーサル・internal 500・monkeypatch シーム・buffer 第 3 段フォールバックを含む。`json.dumps(sort_keys=True)` の文字列完全一致）。
- **検証（全体）**: 4 スイートがベースライン一致（indicator_ui/api 418／market_profile/api 334／analysis 66／marketdata 194）。`-rs` で skip 0 も確認。
- **挙動差分（1 点・要認識）**: `dataset_port()` と MP の 3 getter は Composition Root 未実行のまま呼ばれると `RuntimeError`（旧: その場で既定を遅延合成）。MP 側は `__init__.py` 結線により構造的に発生し得ない。indicator_ui 側は `framework/server.py` と `api/tests/conftest.py` の 2 箇所で結線済み（実エントリポイントを全数 Grep で確認）。**新規エントリポイントを足す際は結線が必要**。
- **関連**: ISSUE-172（配置記述子）・ISSUE-177・ISSUE-184・ISSUE-186（本件の検証中に発見）。

## ISSUE-184: [記録] indigators 全 26 パッケージ SOLID アーキテクチャ監査の総括（2026-07-26）
- **ステータス**: CLOSED（2026-07-27・記録用エントリ。個別対応は ISSUE-172〜183・185〜187）
- **実施内容**: `indigators/` 配下 26 パッケージ（Python 394・JS 154 ファイル）をアーキテクチャエージェント 5 体で分割監査。判定基準は `indigators/PORTING_GUIDE.md` §2、参照実装は `profit_band`。全指摘を実 Read + Grep で裏付け、ファイル変更ゼロ（読み取り専用）。
- **結果**: 違反 **99 件（高 20 / 中 44 / 低 35）**。全 26 パッケージ中、全項目準拠は **`mql_builtins` / `tgp_btlm` / `price_range_power` の 3 件のみ**。
  - `mql_builtins`: core=numpy のみ・プロジェクト内依存ゼロ・循環なし・`__all__` で公開面限定。
  - `tgp_btlm`: `core.py:82-99` の `BtlmFitter` Protocol で R/rpy2 を最外へ隔離＝**リポジトリ内の DIP 正解形**。
  - `price_range_power`: core / ratio / loader / plot / lwc_chart がアクター 1:1 で分離。
- **全数で遵守されている点**: 26 パッケージすべてで `core.py` が pandas / matplotlib / lightweight-charts を非 import、かつ全 `lwc_chart.py` が `lightweight_charts` を非 import（PORTING_GUIDE §2 の最重要含意）。frozen DTO ＋ `writeable=False` は指標パッケージ側では概ね実装済み（未達は参照実装 profit_band 側＝ISSUE-173）。
- **高重大度の内訳（ISSUE 対応）**: DIP 6 件（172/174/176/183）・OCP 5 件（175/179/180）・SRP 4 件（181）・LSP 3 件（175/177）・不変性/ISP 2 件（178/182）。
- **中/低 79 件**: 個別 ISSUE 化していない。主な内容は 各 `plot.py` の未 Read 分を含む重複、`profit_osi_ma` の DTO 不在、`profit_volatility` の 49 系列レガシー公開 API（本番参照 0）、再エクスポート 2〜3 段、`market_profile` の README/SPEC 不在、`indicator_ui` の lwc 隔離宣言と実態の乖離（`chart_renderer.js:4-7` の「唯一の隔離点」宣言が実測 5 ファイルで破られ、`trade_markers_renderer.js:5` も同時に唯一を主張）など。
- **監査の限界（未検証項目）**: 1. 静的読解のみ。SOLID 違反の実行時影響（ISSUE-174 の ImportError 再現、ISSUE-176 のレース発生、ISSUE-172 の実ディスク状態）は未実測。2. 各パッケージの `tests/` 内部品質、一部 `plot.py` 本体、`market_profile_primitive.js:120-551` の描画本体、`market_profile_zp_kernel.py` 本体は未読。3. 改善提案の挙動不変性（byte 等価）は未検証。適用時は各パッケージの既存テスト（特に `test_market_profile_byte_parity.py` / `py_parity_golden.test.js` の byte 一致縛り）による回帰確認が必須。
- **着手推奨順**: ISSUE-172（即時リスク回避）→ ISSUE-173（基準の確立）→ ISSUE-174/176（依存解決の一元化）→ ISSUE-175（正解の裁定）→ ISSUE-177/178 → ISSUE-179/180/181/182/183。ISSUE-173 以降はいずれもスコープ外の実装変更・アーキテクチャ判断に当たり、着手には承認が必要。

### 対応の総括（2026-07-27・ISSUE-172〜184 の一括対応を完了）
- **完了状況**: RESOLVED 9 件（172 / 173 / 176 / 177 / 178 / 179 / 181 / 183 / 185）・PARTIALLY RESOLVED 2 件（180 は back 側で確定・182 は項目 1〜4）・BLOCKED 1 件（175＝元 MQL 不在で裁定不能）・記録用 1 件（184）。派生して ISSUE-185 / 186 / 187 を新規起票（185 は同時に是正済み）。
- **ユーザー承認事項（7 件）**: 173＝案 a（参照実装を規約へ合わせる）／174＝案 a ＋案 a-2（ロード境界への一本化）／175＝裁定保留・記録のみ／180＝back 側完了で確定・front は現状維持／179 項目 1＝挙動不変の一本化（共有実装をパラメータ化した上位集合＋薄い shim）／185＝今回修正／182・183 の挙動変更・ファイル削除＝挙動不変の範囲に限定。
- **最終テスト状況**: Python 36 スイート **3445 passed / skip 0 / fail 0**。JS 4 スイート緑（indicator_ui 749 / market_profile 311 / replay_ui 266+既知 1 = ISSUE-170 / unified_ui 42）。本番ロード経路 24/24・standalone 20/20。
- **「監査の限界（未検証項目）」の解消状況**: 1. **実行時影響は実測で確定**した。ISSUE-172 は実ディスクに `JP225/{g10, v4}` が併存し**判定が完全逆転**していることを dry-run で確認（未発火）。ISSUE-176 のレースは再現に成功し、支配的障害が重複ロードではなく**半構築モジュールの観測**であることが判明（ISSUE-185 の発見に直結）。ISSUE-174 の ImportError は実環境では venv `.pth` により再現せず（ISSUE 記述の因果が誤り）。2. 未読だった `market_profile_zp_kernel.py` / `market_profile_primitive.js` の周辺は対応の過程で読解済み。3. **挙動不変性は byte 等価・bit-for-bit で実証**した（累計 1700 超の比較項目で差分 0）。
- **検証手法として確立したもの（今後の同種作業で踏襲すべき）**:
  1. **pytest 単独を合格判定にしない**。ISSUE-174 で「`indicator_ui/api` の 418 件が緑でも本番ロードは `ModuleNotFoundError`」を実証。本番相当ロード（`cd api` + `env -u PYTHONPATH` + `_load_src_package`）を必ず併走させる。
  2. **対照は `git show HEAD:` ではなく着手前の作業ツリー全体のスナップショット**（並列作業の未コミット変更が混入するため）。
  3. **stale pyc の排除**（`__pycache__` 全消去＋`PYTHONDONTWRITEBYTECODE=1`）。実際に「復元後も差分 6 件」の偽差分を踏んだ。
  4. **ネガティブコントロール必須**。比較ハーネスに意図的欠陥を注入して検出できることを示さないと、「常に一致」を返す偽陽性ハーネスを合格と誤認する。ISSUE-179 では初版ハーネス（36 観測点）が判定順序の退行を検出できず、観測点 10 種の追加で初めて検出できた。
  5. **Red 観測と変異検出**。新規テストを変更前コードに当てて失敗すること、および抽出後のロジックへの変異で既存テストが失敗することを実出力で示す。
- **ISSUE 本文に含まれていた事実誤り（実測で訂正済み）**: ISSUE-172 の `gen_depth`（3→2 が正）／ISSUE-174 の依存の因果（cwd→`.pth`）と `common_view` 箇所数（13→20）／ISSUE-179 の loader 本数（11→17+shim 1）・空 CSV ガード保持数（1→2）・`_resolve_times` 箇所数（18→21）／ISSUE-178 の `tf_period_columns.py` のパス（`controller/`→`compute/`）／ISSUE-179 の as-of クランプ箇所数（3→4）。**静的読解ベースの監査結果は、着手前に必ず実測で再確認すること。**
- **残タスク（未承認・未着手）**: ISSUE-175（元 MQL 入手待ち）／ISSUE-180 front（`/catalog` スキーマ拡張の是非）／ISSUE-181 の compute オーケストレーション本体・凡例/歯車ダイアログ／ISSUE-182 項目 5（挙動変更 4 件）／ISSUE-183 の shim 2 ファイル削除／ISSUE-186（CSV 非原子追記）／ISSUE-187（profit_band の `_resolve_times` 乖離）／中・低重大度 79 件は未着手のまま。

## ISSUE-185: [不具合/実測再現] indicator_ui の module_loader が ISSUE-156 対策後も半構築モジュールを露出（ロック外の二重チェックが exec 前登録を読む）（2026-07-26）
- **ステータス**: RESOLVED（2026-07-27・ユーザー承認を得て是正）
- **対応結果**: `indicator_ui` 版の独自実装（72 行）を全削除し `common/module_loader.py` への純再エクスポート shim に置換して**動的ロード実装を 1 本化**。両版の API は名前・シグネチャ・戻り値・例外メッセージまで完全一致していたため上位集合化は不要だった。呼出元は `call_binding.py:39,190` の `load_package` 1 経路のみ（全数 Grep。`load_module` の呼出元は 0 件＝未使用の公開 API、旧 docstring が主張する「controller の `_load_loader`」も不成立の陳腐化記述）。
- **レース是正の実測（20 条件＝switchinterval {0.005, 1e-6} × スレッド {3, 16} × 各 5 試行）**: BEFORE は**全 20 試行 RACE**（3 スレッドで半構築 2/3・16 スレッドで 15/16）。AFTER は**全 20 試行 OK**（半構築 0/3・0/16）。CONTROL（`common` 版）も 20/20 OK。本番経路での 6 指標同時ロードも errors=0。
- **副次的な発見**: 旧 `threading.Lock` は**入れ子ロードで自己デッドロック**する（回帰テストで Red を実測）。`common` 版の `RLock` はこれも回避する。
- **検証**: indicator_ui/api 430→437 passed（+7 は新規テストのみ・skip 0）。common 46／btlm_trail_marod 30／ma_marod 43 はいずれも不変。本番ロード 24/24 成功。`.pth` を除いた条件でも `framework/server.py` の自己結線フォールバック経由で `common` が解決することを実測。
- **残存**: adapter 側と `common` 側で別々だったロックが単一 `RLock` に統合されたため、異なるモジュールの**初回ロード同士**が相互に直列化する（正当性への影響なし・初回のみ・デッドロックは実測でなし）。
- **事象（実測で再現成功）**: `indigators/indicator_ui/api/adapter/compute/module_loader.py:25,39-43` は ISSUE-156 対策の `threading.Lock` と二重チェックを持つが、**高速経路がロックを取らずに `sys.modules` を素引きする**。動的ロードは相対 import 解決のため `sys.modules[name] = module` を **exec 前**に登録する必要があり、その未初期化オブジェクトを高速経路が読む。結果、重複 exec は防げるが「公開関数がまだ定義されていないモジュール」を掴む。
- **実測（同一条件での並置比較）**: `indicator_ui` 版＝`distinct=1 / half_constructed_observations=7/8`、`common/module_loader.py`（ISSUE-176 で新設・`_LOADING` ゲート付き）＝`distinct=1 / half_constructed_observations=0/8`。修正前の `btlm_trail_marod` / `ma_marod` の複製実装でも、`switchinterval=1e-6` × 3 スレッド（`framework/server.py:84` の `ThreadPoolExecutor(max_workers=3)` 相当）で 5 回中 5 回 `RACE`（`missing_attrs_example=['resolve_source','rolling_ols_window_end']`）を再現。
- **影響**: 初回 compute が複数スレッドから同時に同一パッケージをロードした場合、呼出時 `AttributeError` になり得る。発火は初回ロード時の競合に限られるため間欠。
- **対応方針**: `common/module_loader.py` の `_LOADING` ゲート方式（「exec 完了まではキャッシュ命中と見なさない」）を正とし、`indicator_ui` 版をこれへ寄せる。ISSUE-179 の「loader 重複の一本化」と同時に行うのが自然。
- **関連**: ISSUE-156（不完全だった当該対策）・ISSUE-176（発見経緯・common 側の正解実装）・ISSUE-179（重複の一本化）。

## ISSUE-186: [不具合] tick CSV の非原子的追記により実データ依存テストが間欠一斉失敗（2026-07-26）
- **ステータス**: RESOLVED
- **事象（実測）**: `indigators/indicator_ui/api` の全スイートが 1 回だけ `13 failed / 405 passed` を記録し、直後から 14 回連続で `418 passed`。再現性なし。
- **原因**: `tools/live_tick_watch.py --stream` / `tools/export_jp225_m1.py --watch` が常駐し、`jp225_tick_m1.csv`（276MB）/ 同 M1 CSV（300MB）へ**非原子的に追記**している。`marketdata/tick_m1.py:336` に「末尾追記は原子化を持たない」と既知として明記済み。読み取り側が部分行を掴むと実データ依存テストが一斉に落ちる。
- **影響**: テスト結果が非決定的になり、回帰判定の信頼性が落ちる。**リファクタリングの挙動不変検証を実データ比較で行う際に偽陽性・偽陰性の両方を生む**（ISSUE-183 では `/candles` の byte 等価比較が実際にこれで 10 ケース不一致となり、同一プロセス並置比較へ切り替えて回避した）。
- **対応方針**: 追記を原子化する（一時ファイルへ書いて `os.replace`、または追記時に排他ロック）。あるいは読み取り側に部分行の検出・再読取を入れる。どちらを採るかは書き手・読み手の性能要件を実測してから決める。
- **関連**: ISSUE-183（発見経緯）。
- **対応（2026-07-31・書き手と読み手の両方）**: 対応方針に挙げた「追記の原子化」と「読み取り側の部分行検出・再読取」の**両方**を実施した。片方だけでは競合が残ることを実測で確認したため。
  1. **書き手（`marketdata/tick_m1.py::_append_m1_csv`）**: `DataFrame.to_csv(fh)` は行を**複数回の write に分けて**流すため torn 窓が広い。本文をメモリ上で組み立ててから **1 回の `write`** で流すよう変更した。本番の追記点はここ 1 か所で、両 watch ツールとも `append_m1_from_ticks` 経由で通る。
  2. **読み手（`marketdata/ohlc_csv_loader.py`）**: 「読取＋整形」を 1 単位として楽観読取＋検証を行う。失敗時に**並行追記の証拠**（末尾が改行で終わっていない／読取の前後でサイズが変化）があれば短く待って読み直し、証拠が無ければ**本物のデータ異常として即時送出**する（欠陥を隠さない・無駄な再読取もしない）。
- **実測（3 秒間、読み手はループで読み続ける・失敗率）**:
  | 構成 | 失敗率 |
  |---|---|
  | 分割 write・対策なし | **32.61%** |
  | 単一 write・対策なし | 0.07% |
  | 分割 write・対策あり | **0.00%** |
  | 単一 write・対策あり（本番構成） | **0.00%** |
- **⚠ 途中で棄却した 2 つの誤った対策（いずれも実測で否定）**:
  1. `on_bad_lines="skip"`（列数ベースの救済）: 32.9% → 34.3% と**改善しなかった**。torn 行は列数が合うことがあり、支配的な失敗は列数ではなく**時刻列のパース**（`time data "2026-01-01 0" doesn't match format`）だったため。
  2. `pd.read_csv` だけを再試行で包む: 最悪ケースの失敗率が 34% のまま**変わらなかった**。失敗は `read_csv` ではなく**後段の時刻変換**で起きるため、包む範囲が誤っていた。
  3. 固定回数（4 回）での打ち切り: 1,773 回中 2 回（0.11%）が送出まで到達した。毎回 torn を踏む確率が残るため、**時間予算（250ms）**方式へ変更して 0% にした。
- **検証**: 回帰テスト 7 件を追加（末尾不完全の O(1) 判定・空ファイル・本物の異常を送出すること・**本物の異常では再試行しないこと**・分割/単一 write 双方での競合下 0 失敗）。**変異注入**（再試行の撤去）で split_write ケースが 440 回失敗することを確認。3 連続緑。`marketdata` 201 passed / `indicator_ui/api` 441 passed。

## ISSUE-187: [仕様裁定待ち] 参照実装 profit_band の `_resolve_times` だけ DatetimeIndex 経路の系列名が他 20 本と異なる（2026-07-27）
- **ステータス**: RESOLVED
- **事象（実測）**: `_resolve_times` の DatetimeIndex 経路で、`profit_band/src/lwc_chart.py` は `df.index.to_series()` を返すため**系列名が `time` にならない**。他 20 本は系列名を `time` に揃える。例外文言も profit_band だけ別。
- **なぜ問題か**: `profit_band` は `indigators/PORTING_GUIDE.md:8-9` が「本書の原則はすべてこの実装で実証済み」と宣言する**参照実装**でありながら少数派である。参照実装を正とするなら他 20 本が誤り、多数派を正とするなら参照実装が誤り。**どちらとも決められないため `_resolve_times` の完全な一本化ができない**（ISSUE-179 項目 2 は AST 同値の 12 本のみ統合し、9 本を挙動差のため未統合として残した）。
- **同種の先行事例**: ISSUE-173 で profit_band が PORTING_GUIDE §2 の 2 規約を満たしていなかった件（案 a＝参照実装を規約へ合わせる、で解決済み）。本件も同じ「参照実装が基準に従っていない」構図だが、**こちらは挙動変更を伴う**点が異なる。
- **対応方針（いずれかの裁定が必要）**:
  - 案 a: 参照実装 profit_band を正とし、他 20 本を profit_band の形へ寄せる（20 パッケージの挙動が変わる）。
  - 案 b: 多数派 20 本を正とし、profit_band を寄せる（profit_band の挙動が変わる。ISSUE-173 と同じ「参照実装側を直す」方向）。
  - 案 c: 系列名の差が下流（lwc への payload・JS 側パリティ）に実影響を持つかを先に実測し、無影響なら差異を許容して PORTING_GUIDE §5 に明記する。
- **未検証**: 系列名 `time` の有無が実際に描画・payload・JS 側の byte 一致縛りへ波及するかは未測定。**裁定の前にこれを実測すべき**。
- **関連**: ISSUE-173（同じ参照実装の規約未達）・ISSUE-179（発見経緯）・ISSUE-184。
- **未検証事項の実測（2026-07-30・裁定の前提として測定。コード変更なし）**: 「系列名 `time` の有無が描画・payload・JS 側の byte 一致縛りへ波及するか」を測定した。**結論: 波及しない。**
  - **消費者の全数調査**: `resolve_times` / `_resolve_times` の戻り値を使う 21 本の `lwc_chart.py` を全て確認した。用法は (a) `pd.DataFrame({"time": times, ...})` の**辞書値**（キー `"time"` が列名を上書きするため `Series.name` は捨てられる）、(b) `times.to_numpy()`（profit_band 自身・名前は消える）、(c) 位置スライス／添字（`moving_averages` の `times[:-1]` / `times[j]`）のみ。**`.name` を読む消費者は 0 件**。
  - **payload 一致の実測**: `index.name` を `None` / `Date` / `time`、さらに tz-aware(UTC) / tz-aware(JST) / 重複あり / 非単調の各ケースで両実装を並置比較。**値・dtype・index はすべて一致**し、差は `Series.name`（多数派 `'time'` 固定 vs profit_band は index 名を継承）のみ。`emit_line` が作る DataFrame と、そこから JS へ渡る JSON は**全ケースで byte 一致**。
  - **例外文言の差**: 文言をアサートするテストは**全体で 0 件**（grep 実測）。
- **裁定への含意**: 差は**観測不能**であるため、案 b（多数派へ寄せる）は当初想定と異なり**挙動変更を伴わない**（＝`_resolve_times` の完全一本化が実行可能）。案 c（差異を許容して PORTING_GUIDE §5 に明記）も同じ実測で正当化できる。
- **ステータス**: 21 パッケージ共有の実装統一はアーキテクチャ判断のため**未実施・裁定待ち**（案 b と案 c のいずれか）。
- **裁定（2026-07-30・案 b を採用しユーザー承認のうえ実施）**: 参照実装 `profit_band` を多数派の形へ寄せ、共有実装 `common_view.lwc_adapter.resolve_times` を import する形に一本化した（ローカル定義を削除）。
  - **選定理由**: 上記実測で差が観測不能＝案 b は当初想定と違い**挙動変更を伴わない**。挙動が同じなら、二重実装を残す案 c より実装が 1 つ減る案 b が優る（[[minimize-cognitive-load]] と同じ規律＝同一概念に複数の実体を作らない）。ISSUE-173 と同じ「参照実装側を直す」方向でもある。
  - **挙動不変の実証（A/B byte 比較）**: 変更前のローカル実装を復元して同一入力に流し、`add_profit_band` が生成する **28 系列すべての payload が byte 一致**することを、`index.name` = None / `Date` / `time` × tz-aware(UTC) の 4 ケースで確認した。
  - 共有実装は `{str(c).lower(): c}` で非文字列列名にも耐えるため、堅牢性はむしろ向上する。
- **検証**: `profit_band` **28 passed**。
- **残件（本 Issue の範囲外）**: `_resolve_times` のローカル定義は 9 本 → **8 本**へ減った（`moving_averages` / `profit_adx_needle` / `profit_hl_band` / `profit_hlband` / `profit_mfi` / `profit_osi_ma` / `profit_stc` / `tgp_btlm`）。これらは ISSUE-179 が「挙動差あり」として未統合に残したもので、個別に差の実測が必要。

## ISSUE-188: [不具合] テンプレート自動適用後に `applied.v1` が空のまま残りリロードで構成が消える（2026-07-28）
- **ステータス**: RESOLVED（2026-07-28 起票・同日修正。実 UI 検証 D-1。TDD Red→Green。live 829 緑（既存 749 無改変全通過）／replay 266 pass・1 fail は ISSUE-048 のみで不変／unified 42 緑）
- **事象（実 UI・統合 8000 で実測）**: 5m（tpl#2 適用済み）で 1m（tpl#1=ma_marod + market_profile を紐付け）へ切替 → 凡例と `uiState` は正しく更新されるのに `live:indicatorUi.applied.v1` が `[]` のまま。リロードで凡例が空になり構成が失われる。
- **原因（フォールト注入で再現確定）**: `ChartTemplateController._applyInstances` が `IndicatorStateStore.rebuildApplied` の失敗を素通しし、手順 5（`activeTemplateId` 更新＋`applied.v1` 永続化・設計書 §5.2）へ到達しないまま reject していた。共有ベースの再構築ループは**非 MP の compute 例外のみ** try/catch で握り、**MP 復元経路（`_mp.restoreInstance` → `actor.setEnabled`）は catch の外**にあるため、MP の失敗が適用全体を中止させる。手順 1 の除去は `removeInstance` × N がその都度永続化するため、中断時点の永続値は空構成 `[]` で確定する。MP 復元を失敗させた再現で「`state.applied`=2 件／`uiState.timeframe`=1m／`applied.v1`=[]」＝報告と同一の症状を再現。報告中の `activeTemplateId`=tpl#1 は**直前に成功した受入基準 2 の実行が残した値**であり、本フローで手順 5 が走った証拠ではない。
- **対応**: 協働子側で再構築の失敗を局所化し、**適用の完遂（手順 5 永続化・手順 6 凡例）を再構築の成否に依存させない**（設計書 §5.6 F-T4「当該 1 件のみスキップし残りの適用と描画は継続する。全体を中止しない」に一致）。共有ベース `indicator_state_store.js` は無改変。
- **回帰テスト**: `tests/chart_template_persistence_integration.test.js` TC-P01〜P03（実 IndicatorController を通した永続化の実データ固定）・TC-P05（MP 復元失敗の注入）。従来の協働子テストは host スタブの `_persistAll` をログ置換していたため永続化内容を検証できておらず、本欠陥を検出できなかった。
- **残存（受容・2026-07-28 レビュー R-4 で裁定）**: 手順 1 の除去が `applied.v1=[]` を先に永続化するため、除去〜適用完了の間（実 UI では compute の HTTP 往復ぶん）にリロード・タブ終了が起きると構成が失われる窓が残る。解消には「除去の永続化を抑止し適用完了時に 1 回だけ書く」＝バッチ除去入口の新設が必要で、S2 の承認範囲外（U5「バッチ除去入口を新設するな」）。**コードレビューで「受容し設計書へ注記・実装は変えない」と裁定されたため本 ISSUE では対応しない**（設計書側の注記は別途）。
- **関連**: ISSUE-189（同時に確定した切替失敗時の不整合）・設計書 `.doc/indicator-management-ui/基本設計_チャートテンプレート.md` §5.2/§5.6。

## ISSUE-189: [不具合] 時間足切替が例外を投げるとテンプレート適用が実行されず全指標が消失（2026-07-28）
- **ステータス**: RESOLVED（2026-07-28 起票・同日修正。実 UI 検証 D-2。lwc 例外そのものの原因究明は未完＝下記「未実証」）
- **事象（実 UI で実測）**: 自動適用の直後（再入防止の窓の外）に次の時間足切替を行うと、時間足ラベル=1分／`uiState.timeframe`=5m（未更新）／`activeTemplateId` 未更新／`applied.v1`=[]／凡例=空 の不整合で終わる。同時に lightweight-charts 由来の `Error: Value is null at xt.Candlestick` が 2 件。
- **原因（再現テストで確定）**: `onTimeframeChange` の (a) 除去 → (b) `await proceed(next)` → (c) 適用 のうち、(b) が throw すると (c) が実行されず「除去済み・未適用」で終わる。観測された「ラベルは新足・`uiState.timeframe` は旧足」は `TimeframeController.setTimeframe` が冒頭で `_timeframe` を進め、`uiState` 更新と永続化を末尾で行う構造（例外時は末尾未実行）と一致する。
- **lwc 例外の出所（未実証・切り分け継続）**: `Value is null`（lightweight-charts candlestick）は**本件変更以前から存在する既知事象**（ISSUE-167 本体＝2026-07-24、および ISSUE-167 の再観測記録＝commit `c2ded05` 2026-07-27「ページ読込後の最初の時間足切替で 1 回だけ発火・原因未特定・再現条件不明」。いずれも本ブランチの祖先コミットで、チャートテンプレートのコードは 1 行も存在しない時点の記録）。ただし**今回観測された個体が同一原因かはブラウザ実測なしには断定できない**。切り分けに必要な手順: develop を 8000 で起動し、テンプレート未使用で同じ「切替直後に再切替」を反復して `Value is null` の発火有無を計測する。
- **対応（§5.4 の順序は不変）**: (b) の失敗を捕捉して警告し、**(c) の適用と永続化を必ず実行**してから元の例外を呼び出し元へ再送出する。順序 (a)→(b)→(c) は設計書 §5.4 のまま（「旧構成を新しい足で計算しない」「二重描画を出さない」の制約に抵触しない）。切替の失敗を指標構成の破棄の理由にしない、という一点だけを保証する。
- **実装せず報告する代替案（設計変更＝要承認）**: 除去を切替の**後**に遅らせる案（(b)→(a)→(c)）は失敗時の消失を原理的に無くすが、切替時に旧構成が新しい足で 1 回計算され、旧指標が一瞬描画される。これは設計書 §5.4「無駄な計算・二重描画を出さない順序」の明示的否定に当たるため**実装していない**。採用する場合は §5.4 の改訂承認が要る。
- **回帰テスト**: `tests/chart_template_persistence_integration.test.js` TC-P04（`proceed` に例外を注入し、構成保持・永続化・例外伝播の 3 点を固定）。
- **関連**: ISSUE-167（`Value is null` の既知事象）・ISSUE-188。

## ISSUE-190: [仕様不一致] テンプレート保存ダイアログの時間足表記がラベルでなくキーだった（2026-07-28）
- **ステータス**: RESOLVED（2026-07-28 起票・同日修正。実 UI 検証 D-3）
- **事象（実 UI で実測）**: 保存ダイアログのチェック文言が `この時間足（1m）に紐付ける` / `（5m）` とキー表記。設計書 §6.2 は「この時間足（例：日）に紐付ける」＝時間足**ラベル**（1分・5分・日・週…）を指定している。
- **原因**: 協働子が `host._timeframe`（キー）をそのままダイアログへ渡していた。ラベルは `timeframe_menu.js` の `groups`（`['1m','1分']` 等）だけが持ち、協働子へ供給されていなかった。
- **対応**: `timeframe_menu.js` に `timeframeLabels(groups = DEFAULT_GROUPS)`（キー→ラベル写像を groups から導出する純関数）を**加法**で追加し、両 composition root から協働子へ注入する。ラベルの定義は既存 `groups` の 1 箇所のみで、キーとラベルの二重定義を作らない（replay の 8 足は既定 groups の部分集合でラベル語彙が同一のため同一写像で足りる）。
- **判断（メニュー行のバッジは変更しない）**: 設計書 §6.2 のメニュー構造図はバッジを `● スイング (1D, 1W)` と**キー表記**で明示しているため、バッジはキーのまま維持した。ダイアログ文言（`（例：日）`）とバッジ（`(1D, 1W)`）で表記規約が異なるのは設計書の記述どおりであり、統一する場合は設計書側の裁定が要る。
- **回帰テスト**: TC-P06（ダイアログへ `5分`/`日` が渡る）・TC-P07（ラベル写像が groups 由来で 9 足を網羅）。

## ISSUE-191: [不具合] 現在表示中の足と同じ時間足項目のクリックでテンプレートが自動適用され構成が置換される（2026-07-28）
- **ステータス**: RESOLVED（2026-07-28 起票・同日修正。code-review-executor 指摘 C-1＝マージブロッカー。TDD Red→Green で確認）
- **事象（レビュー側の実測・当方でも Red 再現）**: 現在足 5m・手動適用の `profit_band` 在席・`bindings={'5m':'tpl#1'}`・`activeTemplateId=null` の状態で時間足メニューの「5分」（＝現在足）をクリックすると、`profit_band` が除去され `tpl#1` の構成へ置換され、永続化まで到達する（リロードしても戻らない）。当方の Red 再現でも `host.log` が `['remove:profit_band#1','proceed:5m','commitState','rebuildApplied:...','persistAll','renderLegend']` となることを確認。
- **原因**: 既存実装の同一性ガード（`timeframe_controller.js:63-65` の `if (!timeframe || timeframe === this._timeframe) return;`）は `proceed` の**内側**にあり、`ChartTemplateController.onTimeframeChange` のデコレータは**その手前で除去・適用を実行する**。よって「切替が発生しないクリック」でも自動適用が発火した。現在足の項目は `disabled` でも `pointer-events:none` でもなく、`indicator_controller.js:787` が全 `[data-timeframe]` へ click を配線するため実 UI から到達可能（A方式のみ disabled）。
- **既存挙動の破壊であること**: 本機能導入前、この操作は完全な no-op だった。設計書 §5.4 発火条件 1「ユーザーの明示的な時間足**切替**である」・「**切替先**の時間足に紐付けが存在し」を満たさない操作であり、加えて §5.3「紐付け操作そのものは構成を変更しない」の保証がクリック 1 回で迂回されていた。
- **対応**: `onTimeframeChange` の冒頭（再入防止の直後）へ同一性ガードを置き、`next` が現在足または falsy なら既存挙動へそのまま委譲する（加法・§5.4 の順序は不変）。
- **回帰テスト**: `tests/chart_template_controller.test.js` TC-C20（現在足クリックで `host.log` が `['proceed:5m']` のみ・構成と `activeTemplateId` が不変）。
- **関連**: ISSUE-188 / ISSUE-189（同一デコレータの他の欠陥）・設計書 §5.3/§5.4。

## ISSUE-192: [不具合] MP 復元の失敗が後続指標を巻き添えにし F-T4「当該 1 件のみスキップ」が不成立（2026-07-28）
- **ステータス**: RESOLVED（2026-07-28 起票・同日修正。code-review-executor 指摘 C-2。TDD Red→Green で確認）
- **事象（Red 再現）**: `instances=[market_profile, ma_marod]`（MP が宣言順の先頭）で MP の `setEnabled(true)` が失敗すると、後続の `ma_marod` の compute 呼び出しが **0 件**・renderer 描画も **0 件**になる。ISSUE-188 の修正で永続化と凡例は救済済みのため、「state と凡例には在席するが系列が描かれていない」不整合として残っていた。
- **原因**: `IndicatorStateStore.rebuildApplied` の再構築ループは**非 MP の compute 例外のみ** try/catch で握り、MP 分岐（`_mp.restoreInstance`）は try の外にある。協働子が配列を 1 回で渡していたため、MP の reject がループ全体を打ち切っていた。
- **対応（共有ベース無改変・公開入口も 1 個のまま）**: 協働子側で `rebuildApplied([inst])` を**1 件ずつ**呼び、各件を try/catch で局所化する。再構築ループの本体はインスタンス間に共有状態を持たない（`_meta` は Map への追加・`_commitLastSeries` は毎回上書きで反復間の読み出しが無い・gateway は呼び出しごとに生成）ことをコード確認済みで、分割しても挙動等価。設計書 §5.6 F-T4／§5.2 例外「当該 1 件のみスキップし、残りの適用と描画は継続する」に一致する。
- **回帰テスト**: `tests/chart_template_persistence_integration.test.js` TC-P08（MP 先頭・MP 失敗で後続 `ma_marod` が計算・描画され、永続化も完遂する）。
- **副次**: 協働子テスト TC-C05 / TC-C11 は「`rebuildApplied` が 1 回の一括呼び出しである」という実装細部に依存していたため、宣言順・再採番・二重適用なしという**意図を保ったまま**アサーションを 1 件ずつの呼び出し形へ更新した。
- **関連**: ISSUE-188（永続化と凡例の救済＝本件の前段）・設計書 §5.2/§5.6。

## ISSUE-193: [テスト不足] 受入基準 3（旧構成を新しい足で計算しない・計算は 1 回のみ）を固定する検証が無い（2026-07-28）
- **ステータス**: RESOLVED（2026-07-28 起票・同日対応。code-review-executor 指摘 T-2）
- **事象**: チャートテンプレートの新規テストに compute 呼び出し回数・計算時間足を検証するアサーションが 1 件も無かった。実挙動は成立していた（レビュー側実測・当方の追加アサーションでも `{id:'ma_marod', tf:'1m'}` の 1 件のみを確認）が、**回帰検出力が無い**状態だった（将来バッチ除去入口を入れて旧構成が新しい足で計算される退行が起きても、既存の協働子テストは pass し続ける）。
- **対応**: 結線ハーネス（`buildWiring`）の fake compute に呼び出し記録（`indicatorId` / `timeframe`）を追加し、TC-P02 へ「旧構成は新しい足で計算されない」「新構成に対して計算は 1 回のみ」「計算は切替後の新しい足で行う」の 3 アサーションを追加した。記録の検出力は TC-P08 の Red（`computeCalls` が `[]` で失敗）で実証済み。
- **関連**: 設計書 §7.4 受入基準 3・§5.4 適用手順ステップ 3。

## ISSUE-194: [設計書の記述誤り] 「replay_ui 側の変更は不要」が新規 usecase モジュールに当てはまらない（2026-07-28）
- **ステータス**: RESOLVED（2026-07-28 起票・同日是正）
- **事象**: 基本設計_期間プリセット.md v0.1.0 §8.1 は E-7（symlink 単一ソース共有）を根拠に「replay_ui 側の変更は不要」と断定していた。実装時、新規追加した `web/js/usecase/period_presets.js` は replay_ui 側に実体が無く、リプレイのページから `../../usecase/period_presets.js` が **404** になることが判明した。
- **原因**: E-7 は *既存* 共有ファイルについての事実であり、**新規モジュールには symlink 作成が別途必要**である。v0.1.0 はこの区別をせず一般化して書いていた（設計書内の過剰一般化）。
- **対応**: `simulator/replay_ui/web/js/usecase/period_presets.js` の symlink を作成。設計書 §8.1 を「既存共有ファイルは変更不要／新規モジュールは symlink が必要」へ是正し、v0.1.1 §12.2-1 に実装で判明した事実として記録した。
- **検証**: 統合 UI（8000）で `/live/js/usecase/period_presets.js`・`/replay/js/usecase/period_presets.js` とも HTTP 200 を実測。リプレイモードの実 UI でプリセット提示（日足 5/21/65/129/258）と `5d` 換算が成立することをブラウザで確認（ページエラー 0）。
- **関連**: 基本設計_期間プリセット.md §8.1・§12.2。

## ISSUE-195: [設計書の記述漏れ] 期間プリセットの変更ファイル一覧に配線・バンドル定義が欠落（2026-07-28）
- **ステータス**: RESOLVED（2026-07-28 起票・同日是正）
- **事象**: 基本設計_期間プリセット.md v0.1.0 §8.1 の変更ファイル一覧に、(1) `indicator_controller.js`（歯車ダイアログへの `context` 供給）、(2) `build.mjs`（A方式バンドルの MODULE_ORDER 登録）が載っていなかった。いずれも欠くと機能が成立しない（前者はプリセット非提示へ退化、後者は A方式でシンボル未定義）。
- **原因**: 設計時に「adapter 側の変更は `property_control_builders` と `properties_dialog` のみ」と見積もったが、`context` の供給元（controller）とバンドル定義を数え落とした。チャートテンプレート ISSUE でも同型の欠落が起きている（v0.1.2 の CSS・ダイアログ view の追記）。
- **対応**: §8.1 の表へ 2 行（配線・バンドル）を追加。あわせて `timeframe_menu.js` を MODULE_ORDER の前方へ移した理由（`properties_dialog` が `timeframeLabels` を参照する／当該モジュールは相対 import を持たない葉のため前方移動は安全）を明記した。
- **検証**: `tests/build_module_order.test.js`（相対 import が MODULE_ORDER に全て登録されていることを構造的に固定）が緑。web 902 緑・api 437 緑。
- **関連**: 基本設計_期間プリセット.md §8.1・§12.2-2。

## ISSUE-196: [不具合] 時間足切替で `Value is null` が発火し指標が更新されなくなる／切替が遅い（ISSUE-167 の未解明個体の真因）（2026-07-28）
- **ステータス**: RESOLVED（2026-07-28 起票・原因特定 → 2026-07-29 抜本対策を実装・実 UI 実測で検証。web 910 緑／replay 266 緑（既知失敗 1 件のみ・本件前から fail）／unified 42 緑／A方式 build 成功）
- **事象**: 統合 UI（8000）で指標を 1 件以上適用した状態で時間足を切り替えると、lightweight-charts が `Error: Value is null` を 2〜3 回 throw する。実害（描画・値の誤り）は観測されない。ISSUE-167 本体（M1 日境界の重複行）は 2026-07-24 に解消済みで、2026-07-27 に「原因未特定・再現条件不明」として再観測記録のみ残っていた個体が本件である。
- **切り分け（実測 2026-07-28）**:
  - 指標 0 件で時間足切替 → **0 件**。指標 1 件（moving_averages）で切替 → **3 件**。
  - live core 単体（`/live/`・SW なし）で同一操作 → **0 件**。統合ページ（`/`）でのみ発火＝**統合固有**。
  - 期間プリセット UI に一切触れない対照実行でも発火＝当該実装とは無関係。
- **機序（vendor 逆アセンブル ＋ 実行時計測で確定）**:
  1. lightweight-charts の `Lh`（`$n` に対する完全一致の二分探索）は、ローソク系列が持たない time-point index を引かれると null を返し、candlestick colorer の `ensureNotNull`（`a()`）が `Value is null` を throw する（スタック: `a` ← `xt.Candlestick` ← `xt.Sh` ← `Ke.xb` ← `Ke.DM`）。つまり**「時間軸の点集合にあるが、ローソク系列には無い index」**が存在すると必ず throw する。
  2. 実行時計測（series API フック）で得た時系列:
     ```
     +0.314s setData Candlestick#1 n=1500 first=1777345200 last=1785268800
             ← preRender (timeframe_controller.js:98) ← recomputeAllApplied ← setTimeframe
     +0.319s ★Value is null
     +0.373s ★Value is null
     +0.374s setData Candlestick#1 (同内容・別 preRender: replay.js:196 ← ReplayView.setCandles)
     +0.378s removeSeries Line#2 → addSeries Line#3 → setData Line#3（新時間足の指標系列）
     ```
  3. **真因**: 統合ページでは時間足切替が `recomputeAllApplied` を **2 バッチ**起動する（base の `TimeframeController.setTimeframe` 経由と、replay 層 `replay.js:196` 経由）。先行バッチは generation ガードで全 job が不採択になり `jobs.length === 0` となるが、`indicator_controller.js:620-624` は **`preRender()`（＝メインローソク系列を新時間足へ差し替え）を実行した後に early return** するため、指標系列は**旧時間足のデータを保持したまま**残る。この間（実測 約 60ms）チャートは「ローソク＝新足の狭い範囲／指標系列＝旧足の広い範囲」という不整合状態にあり、時間軸の点集合は指標系列由来の旧 index を含む。ここで paint / hit-test が走ると 1. の条件が成立して throw する。後続バッチが指標系列を差し替えた時点で不整合は解消するため、実害は残らない。
- **対策案（未実施・要承認）**: `recomputeAllApplied` の `jobs.length === 0` 早期 return を、**preRender の実行前**へ移す（描画すべき指標が無いなら メインローソク系列も差し替えない）。ただし「指標 0 件で時間足だけ切り替える」正常系も `jobs.length === 0` を通るため、単純な順序入替では時間足切替が効かなくなる。適用済み指標が 0 件のときのみ preRender を実行する、あるいは先行バッチ側で二重起動を抑止する等、条件の切り分けが必要。**共有ベース（live/replay 両モードが載る主機能）の変更**であり、影響範囲の評価と承認を要する。
- **⚠「仕様追加が原因か」の検証（2026-07-28・commit 単位の A/B）**: ユーザー報告「仕様追加前は問題が無かった」を受け、**期間プリセット実装の前後**で同一手順（`market_profile` ＋ `ma_marod` を適用 → 1 分足へ切替）を各 3 試行して比較した。
  | commit | 内容 | `Value is null`（3 試行合計） | 時間軸未更新（ローソク未描画） |
  |---|---|---|---|
  | `b2a2e9a` | **期間プリセット実装の前** | **12 件** | 0/3 |
  | `44d505e` | 期間プリセット実装 | 16 件 | 0/3 |
  | `f8178a4` | ＋対策 A | 16 件 | 0/3 |
  ⇒ **`Value is null` は期間プリセット実装の前から発生している**（12 件）。本症状は当該実装が持ち込んだものではない。件数の 12→16 は試行ごとの揺れ（同一 commit 内でも 4／8 と変動）と同程度であり、有意差とは言えない。
- **重篤な現れ（ローソク未描画・時間軸が旧足のまま）**: `market_profile` ＋ 足内更新指標を適用した状態で 1 分足へ切り替えると、時間軸が旧足のまま残りローソクが 1 本も描画されない状態を実 UI で 1 度観測した（スクリーンショット取得）。ただし統制した 3 試行 × 3 commit では 0/3 で再現せず、**発生率が低い競合**である。再現手順は「MP ＋ 足内更新指標 ＋ 1 分足への切替」。
- **対策 A の扱い**: 症状を減らさない（上表）一方で「後続バッチが来なければ `preRender` を抑止したまま candles が差し替わらない」＝ローソク未描画を招きうる副作用を持つ。ブランチ `fix/issue-196-recompute-prerender-atomicity` に温存し、**develop へは入れない**。配信ブランチは `feature/chart-template`（対策 A を含まない）へ戻した。
- **追加実測（2026-07-29・実 UI・統合 8000・ライブ・指標 5 件 = market_profile + ma_marod + btlm_trail + btlm_trail_marod + moving_averages）**: lwc に計装フック（createChart→series の setData/update/setMarkers/removeSeries と例外を時系列採取＋「時間軸に載るが当該系列に無い time」の計数）を入れて日→1分の切替を観測した。
  - クリック → ローソクが 1 分足へ差し替わるまで **5.63 秒**（`/candles` の応答は **1.03 秒**で到着済み＝待ちの実体は「全指標 compute の完了待ち」）。指標 1 件なら 1.84 秒＝**指標構成に比例して遅くなる構造**。
  - その差し替え（`setData`）**の内側で** `Value is null` が throw されることを直接捕捉（`THROW Candlestick#1.setData: Value is null`）。差し替え時点で 21 本の指標系列は旧日足の time（例 `1785283200`）を保持していた。
  - throw が `recomputeAllApplied` を中断（`[replay] 計算エラー`）→ 指標の再描画が行われず旧足のまま固着 → 以後 5 秒クロックの full 再計算も同じ throw で連続失敗（`full 再計算失敗` 3〜5 回／`Value is null` **102〜160 件 / 30〜45 秒**）。悪いケースでは 30 秒経っても全指標が旧足のまま（「指標が表示されない」の実体）。
  - **層の切り分け**: ライブ core 単体（8001・SW/replay 層なし）は同一操作・同一 5 指標で `Value is null` **0 件**。ローソク `setData` と指標の `removeSeries/setData` が**同一同期ブロック**で完了するため。統合 UI ではこのブロックが replay 層の `preRender` で分断される＝統合固有。
- **真因の再定義（実測に基づく）**: lwc は「時間軸に載る time は当該系列にも存在する」ことを要求する（違反時 colorer の `ensureNotNull` が throw）。この不変条件を守る責務がどのコードにも無く、「全指標 compute 完了 → ローソク差し替え → 指標描画」という**非原子な順序**が、(a) 切替の律速（最遅 compute）と (b) 差し替え瞬間の不変条件違反を同時に生んでいた。ISSUE-167 の対策（重複 time の dedupe）は「重複」だけを塞いだ応急防壁で、「欠落」側は無防備だったため別経路で同型の例外が再発した。
- **抜本対策（2026-07-29 実装・ユーザー承認済み。応急防壁＝try/catch や dedupe 追加は行わない）**:
  1. `chart_renderer.clearInstanceData(instanceId)` を新設（加法）: 当該 instance の全系列 data を空にする（系列・pane・スタイル・水準線は温存＝再生成なし）。
  2. `timeframe_controller.setTimeframe`: candles 取得直後に「**全適用指標の系列を空にする → `setCandles`**」を **await を挟まない同一同期ブロック**で実行し、`recomputeAllApplied` へ `preRender` を渡さない。これで時間軸に旧 time が存在する瞬間が構造的に消え、ローソク・時間軸は candles 到着直後に切り替わる（compute 非依存）。
  3. `indicator_controller.recomputeAllApplied`: `preRender` を伴うバッチ（リプレイのリビール経路・世代不採択バッチ）では、**本バッチで描画されない指標の系列を `preRender` の直前に空にする**（同一同期ブロック内）。これで「preRender だけが走るバッチ」も不変条件を破らない。
  4. `chart_renderer.updateSeriesTail`: 空化済み（data 長 0）の系列への遅延末尾差分は捨てる（旧足 time を 1 点だけ復活させる経路を封鎖）。
- **仕様変更点（UI 挙動・記録）**: 時間足切替で「ローソクと全指標を同時に更新する」（ISSUE-023 の一部）を改め、**ローソクを先に（約 1 秒で）差し替え、指標は compute 完了後に一括描画**する。指標同士の同時更新（1 指標ずつバラバラに出ない）は不変。切替直後の数秒は指標が空表示になる（従来は「チャート全体が 5 秒以上旧足のまま」だった）。
- **検証（実 UI・実測 2026-07-29）**: 指標 5 件で全時間足を掃引（1分/15分/1時間/日/5分/1時間）。
  | モード | click→ローソク差し替え | `Value is null` | full 再計算失敗 | 不整合点（軸にあり系列に無い time） |
  |---|---|---|---|---|
  | ライブ | 0.19〜1.22 秒 | 0 | 0 | 0 |
  | リプレイ | 0.09〜0.25 秒 | 0 | 0 | 0 |
  リプレイ再生（一括リビール経路）12 秒: 例外 0・全 15 系列が非空・不整合 0。切替前後のアイドル各 20〜30 秒も `Value is null` 0・`full 再計算失敗` 0（旧: 160 件 / 5 回）。
- **回帰テスト**: `chart_renderer.test.js`（clearInstanceData の空化・系列非再生成・未知 id no-op／updateSeriesTail の空系列スキップと非空系列の従来動作）、`timeframe_controller.test.js`（取得直後 setCandles・preRender=null・空化→差し替えの順序・candles 空時は何もしない）、`indicator_controller.test.js`（preRender 前に「描画されない指標」を空化する／描画される指標は空化しない／ローソクは compute 完了前・指標は完了後一括）。replay 側の同名テストも新仕様へ同期。
- **残件（別 ISSUE）**: 指標ラインが出そろうまでの時間は compute に律速（実測 5.6 秒・`/market_profile` 単発 5.4 秒）＝サーバ側施策は別枠。ISSUE-197（`Cannot update oldest data` の多発）／ISSUE-198（SW 経由 `/live_ticks` の network error）。
- **関連**: ISSUE-167（重複 time の応急防壁＝本件は同じ不変条件の「欠落」側）／ISSUE-023（同時更新仕様の改訂元）／ISSUE-165（compute 並列化だけでは律速が残っていた）／ISSUE-188。

## ISSUE-197: [不具合] ライブ core（8001）で `Cannot update oldest data` が多発する（2026-07-29）
- **ステータス**: RESOLVED
- **事象（実測）**: ライブ core 単体（`http://127.0.0.1:8001/`・SW なし・指標 5 件）で日→1分の切替後 45 秒に、`THROW Line#72.update: Cannot update oldest data, last time=…` が **203 件**発生。統合 UI（8000）側では未観測。
- **切り分け済みの事実**: 例外は `series.update` の同期呼び出しで発生し、`chart_renderer.updateSeriesTail` の try/catch が点単位で握って捨てている（バッチは継続＝可視の実害は未確認）。ISSUE-151 追補2 で「バー確定直後の stale 点」として想定済みの経路だが、203 件は想定頻度を大きく超える。
- **未検証**: 発生源（full 再描画と latest 応答の交錯か／別クロックの重複駆動か）・実害（最新値の欠落有無）。
- **関連**: ISSUE-151（stale 点の無害化）／ISSUE-196（本件の検出契機）。
- **原因（2026-07-30・実UI実測で確定）**: 時間足切替とは無関係の**定常発生**だった。
  - 計測法: `updateSeriesTail` の catch に一時プローブを入れ、ライブ core 8001・指標 5 件・1 分足で 45 秒観測（計測後にプローブは撤去）。
  - 結果は**単一パターンのみ** 798 件（約 18 回/秒）: `n=2 ok=1 ng=1 times=T-60,T before=T after=T`。
    - latest 応答は末尾 **K=2** 点 `[T-60, T]` を返す。
    - しかし系列末尾は既に `T` へ進んでいる（forming/full 経路が先に反映済み）。
    - lightweight-charts の `update` は last より古い time を throw で拒否するため、**毎回 K-1 点が必ず例外になる**。
  - **実害は無い**（未検証項目への回答）: 同一バッチの新しい点 `T` は成功し、`after == before` ＝最新値は欠落しない。問題は「正常動作のさなかに例外が出続ける」こと（コストとログ汚染、および本物の異常が埋もれること）。
  - ISSUE-197 起票時の「日→1分 切替後 45 秒で 203 件」は切替固有の現象ではなく、この定常発生を切替直後に観測したものだった。
- **対応**: `chart_renderer.updateSeriesTail` で、系列末尾より古い点は **比較で判定して `update` を呼ばない**（例外駆動の制御フローを撤去）。捨てる点の集合も更新後の末尾も従来と同一。非数値 time（business day 形式）は比較の意味が自明でないため従来どおり `update` → catch へ倒す。
- **検証**:
  - 回帰テスト 3 件を追加。あわせて**フェイク系列に実 lwc の拒否契約（古い time で throw）を写した**（写さないと「例外をやめた」ことを検証できず空虚なテストになるため）。拒否された点は `_rejected` に記録し、`update` を呼んでいないことを直接判定する。
  - **変異注入**: 事前判定を撤去すると当該テストが失敗することを確認。
  - **実UI再測（同条件 45 秒）**: 例外 **798 → 0 件**、比較でスキップ 2,213 点、コンソールエラー 0、指標 5 件は継続描画。
  - `indicator_ui/web` **935 passed**。

## ISSUE-198: [不具合] SW 経由の `/live_ticks` が network error になる（2026-07-29）
- **ステータス**: RESOLVED
- **事象（ユーザー報告）**: `The FetchEvent for "http://127.0.0.1:8000/live_ticks?since=0" resulted in a network error response: the promise was rejected.`（`sw.js:68`）が複数回。併せて `update_scheduler` の `full 再計算失敗: Failed to fetch` も観測。
- **確認済みの事実（2026-07-29）**: ルータ直叩きでは `/live/live_ticks?since=0` は 200（79KB）、prefix 無し `/live_ticks` は 404。SW は `/live_ticks` を `/live/live_ticks` へリライトする実装で、`proxyRewritten` の `fetch` が reject した場合に当該メッセージになる。当方の Playwright 実測（統合 8000・ライブ）では再現せず。
- **未検証**: reject の実体（起動直後の SW activate 競合／サーバ再起動と重なった接続断／SW 制御開始前の要求）。
- **関連**: ISSUE-171（SW claim 到達待ち）／ISSUE-196。
- **真因の確定（2026-07-31・実 UI 決定実験）**: **ルータ 8000 の一時停止（再起動・瞬断）**。SW のリライト論理の欠陥ではない。
  - 実験: 統合 UI（8000・SW 制御下）で `/live_ticks` を 500ms 間隔で叩き続けながらルータを停止 → 4 秒後に再起動。
  - 結果: **89 回中 8 回が失敗**し、コンソールに `net::ERR_FAILED @ http://127.0.0.1:8000/live_ticks?since=0` が **8 件**。ページ側の例外は `TypeError: Failed to fetch`＝ユーザー報告の `update_scheduler` の「full 再計算失敗: Failed to fetch」と一致する。
  - SW は `event.respondWith(fetch(...))` の失敗を忠実に伝えているだけで、ブラウザがそれを「the promise was rejected」と表示する。**ページは次の poll で自動復帰する**（`fetchLiveTicks` は失敗時 null を返し巻き戻さない）。起票時の 3 仮説のうち「サーバ再起動と重なった接続断」が正しく、「SW activate 競合」「SW 制御開始前の要求」は否定された（SW 制御確立後にのみ発生）。
- **併せて是正したルータ自身の欠陥（実測で発見）**:
  1. **HTTP/1.0 で応答していた**（`protocol_version` 未設定）。1 リクエスト = 1 TCP 接続になり、1 画面で多数の API を並行に叩く本 UI では接続生成が集中する。`_proxy` は上流本体を全読みして自前で `Content-Length` を付与し、`_serve_static`/`_send_simple` も明示するため、全経路が HTTP/1.1 の応答長確定要件を満たす。→ `protocol_version = "HTTP/1.1"`。
  2. **`Date` / `Server` ヘッダが重複していた**（`send_response()` が出す値に加えて上流の同名ヘッダも転送していた。`curl -D -` で実測）。RFC 7231 §7.1.1.2 は `Date` の重複を明確に禁じる。→ 転送対象から除外。
  3. **accept backlog が既定 5**（`listen(5)`）。溢れた SYN は落とされる。→ `RouterServer` サブクラスで 128 へ（stdlib のクラス属性は書き換えない）。
  4. HTTP/1.1 化の副作用として idle な keep-alive 接続がスレッドを保持し続けるため、`RouterHandler.timeout = 65` を追加。
- **⚠ 当初仮説の訂正**: 「backlog 枯渇が network error の原因」という私の仮説は**実測で否定された**。240 同時接続で 38 件失敗したのは 200KB × 240 ＝ 48MB の帯域律速であり、小応答なら **400 同時接続でも全数 200（0.18 秒）**。ブラウザは同一オリジンへ 6 接続までしか開かないため、この経路は元々成立しない。backlog 拡大は初回ロードのバースト耐性としては妥当だが、本件の真因ではない。
- **検証**: 回帰テスト 4 件を追加（HTTP/1.1 と 1 接続 3 リクエスト・Date/Server 非重複・backlog 拡大と stdlib 不変・idle timeout）。**変異注入 3 種すべてで当該テストが失敗**することを確認。`unified_ui` 19 passed。

## ISSUE-199: [不具合] 期間パラメータ欄で入力が既存値へ追記され、確定できないまま旧値が戻る（2026-07-29）
- **ステータス**: RESOLVED（2026-07-29 起票・同日修正。実 UI 実測で検証。web 917 緑）
- **事象（ユーザー報告）**: 「`3h` と入力すると `1803h` と表示される」「パラメータを設定しても元に戻る」。
- **原因（実 UI 実測で確定）**: 期間欄は本数（例 `180`）を表示しており、フォーカス時に全選択していなかったため、クリックしてそのまま打鍵すると**既存値へ追記**される（`180` + `3h` = `1803h`）。結果 `1803h` は 1803 × 1時間 = 108,180 本と解釈され上限（1500 本）超で換算に失敗する。失敗時は仕様どおり代入せず直前の有効値を保持する（F-P1）が、**OK が押せてしまう**ため旧値がそのまま確定し、ユーザーには「設定しても元に戻る」と見えていた。2 つの報告は同一原因である。
- **対策（応急的な入力サニタイズではなく発生条件を消す）**:
  1. `property_control_builders.buildPeriod`: `focus` で全選択。さらに実 UI 実測で「フォーカス済みの欄を再クリックしても `focus` は発火せず追記が再発する」（`180` → `1801803h`）ことを確認したため、**キャレットのみのクリックでも全選択**する（範囲選択中は潰さない）。
  2. 未解決の換算エラーを `ctx.setPendingError(name, message)` でダイアログへ通知し、`properties_dialog._revalidate` が在席中は **OK を無効化**する（§5 F-11 の OK 制御と同じ扱い）。旧値の暗黙確定が構造的に起きない。入力を打ち直すとエラーは解除される。
- **検証（実 UI・1分足・moving_averages）**: クリック→`3h` 打鍵→Enter で **`180`**（追記なし）。意図的な不正入力 `1803h` は OK 無効＋「108180 本はチャートが保持する 1500 本を超えます。」を表示。`6h` へ打ち直すと `352`・OK 有効・保存値 352。単体テスト 5 件を追加（focus/click の全選択・範囲選択時は非選択・エラー登録と解除・代入されないこと）。
- **関連**: 基本設計_期間プリセット.md v0.2.0 §7.3・§6.3（F-P1）／ISSUE-200。

## ISSUE-200: [改善要求] 期間プリセットの選択肢が少ない（2026-07-29）
- **ステータス**: RESOLVED（2026-07-29 ユーザー要求・同日実装。実 UI 実測で検証）
- **事象（ユーザー要求）**: 提示されるプリセットが少なく選べない（v1 は 1分足 3 件・日足 5 件）。
- **原因**: 換算表 v1 の単位が 11 種と粗く（1時間/4時間/1日/1週間/1ヶ月/3ヶ月/6ヶ月/1年/2年/3年/5年）、かつ提示件数の上限が 5 件で打ち切られていた（日足は 2年/3年/5年が候補にありながら出ていなかった）。
- **対策**: (1) 換算表を **v2** へ版上げ。中間刻み 9 単位（2時間/6時間/12時間/2日/3日/2週間/3週間/2ヶ月/9ヶ月）を加法し、`tools/period_presets_measure.py` で**全単位を同一手順・同一定義（§4.1）で再計測**（推測値は入れない）。v1 の定数はコード上に残す（§4.4-3・保存値は本数のため遡って変化しない）。(2) 提示上限を 5 → 14 件へ。(3) 同一本数へ落ちる候補は期間の短い方のみ提示（1D 足の「2日」「3日」はいずれも 2 本のため重複行を出さない）。
- **結果（実測）**: 提示件数は 1m 3→6 / 5m 4→9 / 15m 4→11 / 30m 5→12 / 1h 5→13 / 4h 5→12 / 1D 5→13 / 1W 5→11 / 1M 5→8 件。ポップは `max-height:60vh; overflow-y:auto` のため CSS 変更は不要（実 UI で確認）。
- **副次（記録）**: v2 は v1 と同一手順の再計測のため、提示対象（≤1500 本）のセルは v1 と一致または ±1（例 1h の 1ヶ月 496→495・3ヶ月 1480→1481）。テストの期待値も同時に更新した。
- **関連**: 基本設計_期間プリセット.md v0.2.0 §4.3.1・§6.1／ISSUE-199。

## ISSUE-201: [不具合] 価格更新（ライブ再計算）でインジケーターのパラメータが元に戻る（2026-07-29）
- **ステータス**: RESOLVED（2026-07-29 起票・同日修正。実 UI 実測で検証。web 919 緑）
- **事象（ユーザー報告 → 実測で再現）**: 歯車でパラメータを変えて OK しても、価格が更新されると設定が元に戻る。
- **実測トレース（統合 UI 8000・ライブ・moving_averages の `length` を 9 → 200）**:
  ```
  OK      → /compute(length=200)          ← 新 params で 1 回計算される
  +0.5s   → /compute(length=9)            ← 旧 params のライブ計算が完了・その行が live state へ反映
  以後     → 保存値=9 のまま・/compute も常に 9・歯車の表示も 9
  ```
- **原因（コードで確定）**: `recomputeAllApplied` / `recomputeFormingTails` は**バッチ開始時のスナップショット**から各インスタンスの params を取り出して計算し、`_computeInstance` の完了時に `result.state` の当該行を live state へマージする。マージされる行は**計算開始時点の params を保持**しているため、計算中にユーザーが params を変更すると旧値で上書きされる（同一インスタンスの lost update）。ISSUE-165 は「兄弟インスタンス間」の lost update を是正したが、同一インスタンスに対する旧 params の書き戻しは残っていた。生存機構（2.5〜5 秒クロック）が常に走るライブでは、変更直後に必ずこの窓に当たる。
- **抜本対策（2 点セット・応急的な再適用リトライ等はしない）**:
  1. **params の正はユーザー操作**: 明示操作（歯車 OK / variant 切替 / デフォルト復元）は `commitParams: true` で **await 前に** `_withParams` で live state へ確定する。計算の完了順に依存しない。
  2. **旧設定由来の結果は破棄**: `_computeInstance` は await 直前の行オブジェクトを保持し、完了時に当該行が差し替わっていれば（他の確定・他バッチの反映）`accepted:false` を返して **state も描画も触らない**。次のクロックが新 params で計算し直す。ISSUE-105 の「await 中に除去されたら破棄」と同型の規律。
- **検証（実 UI 実測）**: OK 直後から 90 秒間（tick 更新 78 回・/compute 78 件）で保存値・全 `/compute` の `length` とも **200 のまま**、歯車の再表示も 200。修正前は 0.5 秒で 9 に戻り以後回復しなかった。単体テスト 2 件を追加（await 前確定 / 差し替え時の破棄で state 不変・描画なし）。
- **関連**: ISSUE-165（兄弟インスタンス間の lost update・並列化）／ISSUE-105（await 中の除去ガード＝同型の規律）／ISSUE-199（同じ「元に戻る」と見える別原因＝入力追記と OK 抑止欠如）。

## ISSUE-202: [不具合] 起動時に指標が表示されるまで遅い（復元ループが直列＋MP 待ちで全指標がブロックされる）（2026-07-29）
- **ステータス**: RESOLVED（2026-07-29 起票・同日修正。ユーザー承認 (a)(b)。実 UI 実測で検証。web 919 緑／replay 266 緑（既知失敗 1 のみ））
- **事象（ユーザー報告）**: 最初にチャートが表示されるまで時間がかかりすぎる。指標数が多いと遅いのか。
- **実測（リロード＝実起動と同じ復元経路・統合 UI 8000）**:
  | 指標数 | ローソク描画 | 全指標が出るまで（修正前） | 起動時 compute 回数 |
  |---|---|---|---|
  | 0 | 0.47 s | — | 2 |
  | 1 | 0.51 s | 0.77 s | 3 |
  | 3 | 0.64 s | 1.41 s | 8 |
  | 5（MP 含む） | 0.40〜0.64 s | 2.71 s / 8.32 s（ばらつき） | 16〜17 |
  ⇒ **ローソク表示は指標数に依存しない**（0.4〜0.6 秒）。遅いのは「指標が出そろうまで」。JS 133 件の取得は 0.27〜0.50 秒で無罪。
- **原因（ネットワーク時系列で確定）**: `IndicatorStateStore.rebuildApplied` は `for … await` の**完全直列**で、宣言順の先頭が `market_profile` だとその `/market_profile` 応答（実測 **13.0 秒**: +1.54s 要求 → +14.52s 応答）まで**後続 4 指標の compute が 1 件も発行されない**（+14.52s にようやく `ma_marod` の full が出る）。ISSUE-155（起動の静的配信）・ISSUE-165（時間足切替の compute 並列化）とは別経路で、復元ループは並列化されていなかった。ISSUE-192 は失敗の局所化のみで直列性を変えていない。
- **対策（ユーザー承認 y）**:
  - (a) 非 MP 指標の compute を **並列発行**（`Promise.allSettled`）。並列安全の前提は ISSUE-165 で恒久是正済み（series は per-call gateway 捕捉・state は当該行のみマージ）。
  - (b) MP の復元を **待ち合わせから外す**（fire-and-forget＋失敗は当該 1 件に閉じる＝F-T4 維持）。復元完了は非 MP 指標で決まり、凡例・ダイアログ再描画が MP 応答待ちで止まらない。
  - (c) 描画（`_draw`）は **宣言順に直列化**（描画ゲート）。pane は初回描画時に生成されるため完了順に描くと pane の並びが起動ごとに変わる（ISSUE-149 の並び順保証が壊れる）。compute は並列・描画は宣言順とした。
- **検証（実 UI 実測）**: `/market_profile` と全 4 指標の full compute が **同時刻（+1.66s）に発行**されるようになった（修正前は +14.52s まで待機）。全指標が出るまで 5 件で **2.71→1.16 s / 8.32→2.00 s**、起動時 compute 16〜17→10 件。3 回連続リロードで凡例順・初回描画順が完全一致＝pane 並びは不変。
- **残件（別枠）**: `/market_profile` 自体が 13 秒（サーバ側）。フロント並列化では消えないため、実測付きで別途提案する。
- **関連**: ISSUE-155 / ISSUE-165 / ISSUE-192 / ISSUE-149。

## ISSUE-203: [仕様の矛盾] CVFE `quality_gate = "FAIL"` 経路は §3.3 E07 と §3.3 E08 が両立しない（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: `CVFE_spec_v1.0.md` の以下 3 条が同時に成立しえない。
  1. §3.3 E07：`quality_gate = "FAIL"` のとき「例外を送出せず `measure_id = "PARK"` へ縮退」する。
  2. §4.4：`measure_id ∈ {"RRANGE", "PARK"}` では `J_t = 0`（ジャンプ分離を実行しない）。
  3. §4.5-1／§3.3 E08：説明変数 `x4 = ln(1 + J_t/C_t)` はしたがって恒等的に 0 になり、設計行列のランクは必ず 5（< 6）へ落ち、E08 が `ValueError` を送出する。
  ⇒ **FAIL 経路は必ず例外で終わる**。E07 の「例外を送出せず」という明示保証が実現不能。
- **実測（2026-07-29）**: 凍結率 0.479 の合成系列で `quality_gate = FAIL` / `measure_id = PARK` となり、学習標本 500 本の x4 列の標準偏差は厳密に 0、設計行列のランク 5・条件数 9.94e21。
- **暫定対応（実装側）**: `har.py` の `har_fit` で、x4 が学習標本内で**厳密に定数**のとき当該列を推定から外し `β4 = 0` に固定する。`har_coef` の形状 `(6,)` は §3.2 のまま不変。条件数の悪化や他列の共線性は従来どおり E08 を送出する。
- **要裁定**: 仕様のどちらを正とするか。(a) E07 を優先し §4.5 に「x4 が定数のとき β4 = 0」を明記する（実装の現状）／(b) E08 を優先し FAIL 経路では HAR を実行しない別の縮退（例：`σ̂_OC` を PARK の EWMA とする）を §4.5 に追加する。
- **関連**: ISSUE-205（同じ x4 の退化が標本側の理由でも起きる）。
- **裁定（2026-07-30・ユーザー承認）**: 案 (a) を採用。§4.5-4 に「`x4` が学習標本内で厳密に定数のとき当該列を推定から外し `β4 = 0` に固定する」を明記し、§3.3 E08 に当該例外を追記した（`har_coef` の形状 `(6,)` は §3.2 のまま不変）。
  - 理由: E07 の「例外を送出せず縮退する」は仕様が明示した**保証**であり、これを破る案 (b)（FAIL 経路で HAR を実行しない別縮退）は新しい推定量の設計＝研究課題になる。案 (a) は退化した 1 列を外すだけで残り 5 列の推定を変えず、条件数悪化など他要因の E08 は従来どおり送出する。
- **実装**: 既に暫定対応済みの `har.py::har_fit` がそのまま正式仕様となった（コード変更なし）。
- **検証**: `cvfe` 133 passed / 1 xfailed（残る 1 件は ISSUE-207）。

## ISSUE-204: [仕様の欠陥・実測] CVFE の TSRV が既定 `K = ceil(n^(2/3))` で 8.9% 過小になる（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: 仕様 §9 段階 1 は「σ = 1 の合成 GBM（バー内 1440 ステップ・100,000 バー・シード固定）に対し TSRV の平均が 1.000 ± 0.010」を要求するが、既定 `K = ceil(n^(2/3))` では満たさない。
- **実測（2026-07-29・シード 11・100,000 バー）**:
  | 測定量 | 実測平均 | 仕様の許容 | 判定 |
  |---|---|---|---|
  | RV | 1.0001365 | 1.000 ± 0.005 | 合格 |
  | TSRV | **0.9112100** | 1.000 ± 0.010 | **不合格（−8.88%）** |
- **原因（解析的にも一致）**: K 個のサブグリッドが覆う増分は全 `n` 本のうち `n − K + 1` 本にとどまる（各サブグリッドが端点の増分を落とす）ため `E[RV^avg] = ((n−K+1)/n)·σ²`。仕様 §4.3 の補正係数 `(1 − n̄/n)^(-1)` はノイズ項のみを補正し、この端点欠損 `O(K/n) = n^(-1/3)` を補正しない。`n = 1440` で `n^(-1/3) = 8.83%`（実測 8.88% と一致）。理論値 `((n−K+1)/n − n̄/n)/(1 − n̄/n) = 0.91186`。
- **これは仕様 §10 TBD-1 そのもの**（「`K* = c*·n^(2/3)` の `c*` を原論文で確認する。既定値 `c* = 1` は根拠を持たない。`c*` の誤りは TSRV のバイアスに直結する」・決定者 よしひこ・期限 段階 1 前）。
- **対応**: 推測で式を変更しない。`tests/test_measures.py::test_stage1_tsrv_is_unbiased_on_synthetic_gbm` を `xfail(strict=True)` として実測値ごと固定し、`test_stage1_tsrv_bias_matches_edge_deficit_theory` で原因（端点欠損）を恒久的に固定した。
- **影響範囲**: TSRV は `quality_gate = "DEGRADED"`（`S > 0.50`）のときのみ採用されるため、`PASS`（RV）・`FAIL`（PARK）経路には影響しない。
- **要裁定**: TBD-1。`c*` の値、または端点欠損補正（`× n/(n−K+1)`）を §4.3 に加えるか。
- **裁定（2026-07-30・ユーザー承認）**: **TBD-1 を解決**。バイアスの原因は `c*` ではなく**サブグリッドの端点欠損**であることが解析・実測の双方で確定したため、`K = ceil(n^(2/3))` を維持したまま §4.3 に補正係数 `n/(n−K+1)` を追加した。
  - **実測（σ=1 の合成 GBM・n=1440・100,000 バー・シード 11）**: 補正前 `0.9112100`（端点欠損の理論値 `0.91186` と一致）→ 補正後 **`0.9993468`**。§9 段階 1 の許容 `1.000 ± 0.010` を満たす。
  - `TSRV ≤ 0` 時の代替値 `RV^avg` には補正を適用しない（§4.3 の代替規定は変更しない）。
- **検証**: `xfail(strict)` を解除して通常テスト化。補正が実際に乗じられていることを式と独立に判定する `test_two_scale_rv_edge_correction_is_actually_applied` を追加し、補正前の理論値一致も `test_stage1_tsrv_bias_without_edge_correction_matches_theory` として残した（原因の恒久記録）。

## ISSUE-205: [仕様の欠落] CVFE の HAR は学習窓にジャンプが 1 本も無いと必ず E08 で停止する（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: `measure_id ∈ {"RV","TSRV"}`（ジャンプ分離を実行する経路）であっても、学習標本 `n_har` 本の中で 1 度もジャンプが検出されなければ `J_t ≡ 0` となり `x4 = ln(1 + J_t/C_t) ≡ 0`。設計行列のランクが 5 に落ちて §3.3 E08 が送出され、エンジンが停止する。
- **発生確率**: `jump_alpha = 0.999` の下での検出率は約 0.1%。`n_har = 1500` でも学習窓のジャンプ本数はポアソン平均 1.5 であり、**0 本となる確率は e^(−1.5) ≈ 22%**。合成 GBM（ジャンプ無し DGP）では事実上つねに発生する。
- **実測（2026-07-29）**: 560 バーの合成系列（`measure_id = RV`・Δ* = 5 秒・n = 720/バー）でジャンプ検出は全期間 2 本、学習窓 500 本では **0 本** → E08 送出。
- **原因**: 仕様 §4.5 は `x4` が標本内で変動することを暗黙の前提としており、前提が崩れる場合の規定が無い。§3.3 E08 は「退化した回帰を検出する」ための条項であって、この構造的退化を意図したものとは読めない。
- **暫定対応（実装側）**: ISSUE-203 と同一機構。x4 が学習標本内で厳密に定数なら当該列を外して `β4 = 0` に固定し、`W04_HAR_JUMP_COLUMN_CONSTANT` を WARN 出力する。
- **要裁定**: §4.5 に「x4 が定数のとき β4 = 0 とする」を明記するか、`n_har` の下限をジャンプ検出本数の期待値から導出し直すか。
- **裁定（2026-07-30・ユーザー承認）**: ISSUE-203 と同一。§4.5-4 の規定（`x4` 定数時は `β4 = 0`）が本件も同時に解決する。`n_har` の下限をジャンプ検出本数の期待値から導出し直す案は採らない（`n_har` を増やしても 0 本の確率は 0 にならず、確率的に失敗する仕様になるため）。
- **検証**: 同上。

## ISSUE-206: [仕様の曖昧性] CVFE §9 段階 2 の `ω/σ` の尺度が未定義（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: 仕様 §9 段階 2 は「マイクロストラクチャノイズ（`ω/σ = 0.1, 0.5, 1.0`）を注入」と述べるが、分母の `σ` の尺度を定義していない。
- **2 つの解釈と帰結（実測）**:
  | 解釈 | `ω/σ = 0.1` の S | 判定 |
  |---|---|---|
  | (a) 最細格子 Δ=5 秒の 1 サンプル収益の sd | 0.019 | PASS（3 水準が PASS / PASS / DEGRADED に分離し、閾値 0.10・0.50 と整合） |
  | (b) バー全体の σ（積分ボラの平方根） | 約 14 | DEGRADED（3 水準すべて DEGRADED となり検定が空虚） |
- **採用**: (a)。この解釈の下でのみ `RV(Δ)/IV ≈ 1 + 2(ω/σ)²` となり、§4.1-6 の閾値 0.10 / 0.50 が `ω/σ` の 0.1 / 0.5 / 1.0 と同じ尺度に載る。`tests/test_montecarlo.py` の冒頭に明記した。
- **要裁定**: §9 段階 2 に尺度の定義を追記する。
- **裁定（2026-07-30・ユーザー承認）**: 解釈 (a)（**最細格子 Δ=5 秒の 1 サンプル収益の標準偏差**）を §9 段階 2 に明記した。この解釈の下でのみ `RV(Δ)/IV ≈ 1 + 2(ω/σ)²` となり §4.1-6 の閾値 `0.10`/`0.50` が同じ尺度に載る。解釈 (b) では 3 水準すべてが `DEGRADED` となり検定が空虚になる。

## ISSUE-207: [仕様の内部不整合] CVFE §4.1-6 の DEGRADED 閾値 0.50 は §9 段階 2 の要求を満たさない（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: 仕様 §9 段階 2 は「`ω/σ ≥ 0.5` のとき `"DEGRADED"` 以上を返す」ことを要求するが、§4.1-6 の閾値は `S > 0.50` であり、`ω/σ = 0.5` ちょうどでは届かない。
- **実測（2026-07-29・ISSUE-206 の解釈 (a)・520 バー）**:
  | `ω/σ` | シグネチャ勾配 S | `quality_gate` | §9 段階 2 の要求 |
  |---|---|---|---|
  | 0.1 | 0.0193 | PASS | （規定なし） |
  | 0.5 | **0.4718** | **PASS** | **DEGRADED 以上 → 不適合** |
  | 1.0 | 1.8792 | DEGRADED | 適合 |
- **理論値**: `S = (2 − 1/30)r² / (1 + r²/30)`。`r = 0.5` で 0.4877 であり、閾値 0.50 を構造的に下回る（実測 0.4718 と整合）。
- **対応**: 閾値 0.50 は §10 に根拠が示されていない固定値であり、裁定前に変更しない。`tests/test_montecarlo.py::test_stage2_noise_triggers_degraded_or_worse[0.5]` を `xfail(strict=True)` として実測値ごと固定した。
- **要裁定**: (a) §4.1-6 の閾値を 0.47 未満へ引き下げる／(b) §9 段階 2 の要求を `ω/σ > 0.5` へ改める／(c) `S` の定義を変える。いずれも §9 段階 5（パラメータ事前登録）より前に確定する必要がある。
- **裁定（2026-07-30・ユーザー承認）**: 案 (a)。§4.1-6 の `DEGRADED` 閾値を **`S > 0.50` → `S > 0.45`** へ引き下げた（`0.10 < S ≤ 0.45` が `PASS` の該当行）。
  - 理由: 閾値 0.50 は仕様が根拠を示していない固定値であり、`S(r=0.5) = 0.4877` を計算せずに置かれた可能性が高い。一方 §9 段階 2 の要求は設計意図（ノイズが信号の半分に達したら警告する）である。意図を正とし、理論値 0.4877 を下回る丸い値として 0.45 を採る。案 (b)（要求を `> 0.5` へ緩める）は「`ω/σ = 0.5` ちょうどは検出しない」ことを仕様として認める形になり、案 (c)（`S` の再定義）は §4.1-6 の 3 閾値すべての再校正と段階 2・3 の再実測を要する。
  - 副作用: 実データで `DEGRADED`（＝`TSRV` 採用）に落ちる範囲が広がる。`TSRV` 経路は ISSUE-215 の裁定によりジャンプ分離を行わないため、誤検出の増加は生じない。
- **検証**: `xfail(strict)` を解除。閾値と要求の間に余裕があること（`S_DEGRADED < S(r=0.5) の理論値 0.4877`、かつ実測 `0.4718` も超えること）を式と独立に固定するテストを追加。`cvfe` **135 passed / xfail 0**。

## ISSUE-208: [仕様の過少規定] CVFE §9 段階 2 の DGP では M4 が M3 に勝てない（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: 仕様 §9 段階 2 は「確率ボラ DGP（`ln σ_t` の AR(1)、`φ = 0.98`）上で M4 の QLIKE 平均が M0・M1・M3 のいずれよりも小さい」ことを要求する。しかし当該 DGP は**ジャンプもレバレッジ効果も含まない**ため、M4（HAR-CJ-L）が M3（ジャンプ・レバレッジ項なし HAR）に対して持つ 2 つの追加項に説明対象が存在せず、優位は原理的に生じない（推定分散が増える分わずかに劣る）。
- **実測（2026-07-29・1,500 バー・共通標本 978 本・QLIKE 平均）**:
  | 比較 | M4 | 対抗 | 判定 |
  |---|---|---|---|
  | M0（単純移動平均 20 本） | 0.0105268 | 0.0940 | M4 勝ち |
  | M1（EWMA λ=0.94） | 0.0105268 | 0.0324 | M4 勝ち |
  | M3（項なし HAR） | 0.0105268 | **0.0104954** | **M4 負け（+0.3%）** |
- **実装の妥当性の実証**: ジャンプ（発生確率 5%・6σ）とレバレッジ（中心化した `−min(ρ,0)/σ` 項）を実際に含む DGP では M4 が M3 を下回る（`tests/test_montecarlo.py::test_m4_beats_m3_when_dgp_has_jumps_and_leverage` で固定）。したがって M3 に負ける原因は実装ではなく DGP の過少規定である。
- **対応**: 仕様どおりの DGP での M3 比較を `xfail(strict=True)` として実測値ごと固定した。
- **要裁定**: §9 段階 2 の DGP に「ジャンプ強度」と「レバレッジ係数」を追記するか、M3 比較を段階 2 の合否条件から外すか。
- **裁定（2026-07-30・ユーザー承認）**: §9 段階 2 の DGP に**ジャンプ強度とレバレッジ係数を追加**した（M3 比較を合否条件から外す案は採らない。M4 の追加 2 項が説明力を持つことこそ本エンジンの主張であり、それを検証しない段階 2 は意味を失うため）。
- **水準はグリッド探索で選定（2026-07-30・N_BARS=1500・共通標本 978 本・QLIKE 平均）**:
  | `jump_prob` / `jump_size` / `leverage` | M4 | M0 | M1 | M3 | 判定 |
  |---|---|---|---|---|---|
  | 0.01 / 4σ / 0.10 | 0.15835 | 0.29263 | 0.27103 | 0.17756 | M4 勝ち |
  | **0.02 / 4σ / 0.10** | **0.38888** | 0.49254 | 0.45639 | 0.41633 | **M4 勝ち（採用）** |
  | 0.03 / 4σ / 0.10 | 0.42503 | 0.54516 | 0.49530 | 0.46019 | M4 勝ち |
  | 0.05 / 6σ / 0.10 | 1.64533 | 1.38923 | 1.17773 | 1.71310 | **M4 負け** |
  - **新たに判明した事実**: ジャンプが過大（5% × 6σ）だと §5.1 の代理変数（ジャンプを含む `RV`）がジャンプに支配され、ジャンプを除いた `C_t` を予測する M4 が**構造的に不利**になる（M0/M1 にも負ける）。当初 `0.05 / 6σ` を採ろうとして実測で棄却した。`x4` に説明対象を与えつつ代理変数を支配させない水準として `2% × 4σ` を採る。
- **検証**: `xfail(strict)` を解除。M3 比較を含む M0/M1/M3 の 3 件すべてが合格。v1.0 の DGP では M4 が M3 に勝てないという事実は、改訂の根拠として `test_m4_cannot_beat_m3_on_the_v1_0_dgp_without_jumps_or_leverage` に残した。

## ISSUE-209: [仕様の未定義域] CVFE §4.7-1 のギャップ判定は `delta_star_sec = 0` で全バーが該当する（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: 仕様 §3.2 は `measure_id ∈ {"RRANGE","PARK"}` のとき `delta_star_sec = 0` と定める。一方 §4.7-1 の第 2 条件は「(バー t の最初のティック時刻 − バー t−1 の最後のティック時刻) > 1.5 × `delta_star_sec`」であり、`delta_star_sec = 0` を代入すると「> 0」となる。ティック時刻は §3.1 により狭義単調増加であるから差は必ず正であり、**判定は常に真＝全バーがギャップ保有バー**になる。
- **帰結**: `quality_gate = "FAIL"`（PARK 縮退）の経路では、実際にはギャップの無いバーにも `σ̂_CO,t = sqrt(v_{t−1}) > 0` が加算され、`σ̂_t` が系統的に過大になる。仕様はこの帰結を明示していない。
- **対応**: 仕様の式をそのまま適用し、`gap.py` の docstring と `tests/test_gap.py::test_zero_delta_star_makes_every_bar_a_gap_bar` で帰結を明示的に固定した。
- **要裁定**: `delta_star_sec = 0` のとき第 2 条件を無効化する（第 1 条件のみで判定する）か、RRANGE / PARK でも `delta_star_sec` に実効値を持たせるか。
- **裁定（2026-07-30・ユーザー承認）**: `delta_star_sec = 0` のとき条件 2 を評価しない（条件 1 のみで判定する）。`RRANGE`/`PARK` に実効値を持たせる案は採らない（両測定量はサンプリング格子を用いないため、実効値は意味を持たない）。
  - `delta_star_sec = 0` は「サンプリング間隔を持たない」ことを表す**番兵**であって閾値 0 秒ではない、という読みを §4.7-1 に明記した。
- **影響範囲の実測（2026-07-30）**: 本件の退化は**ティック経路（RRANGE/PARK）限定**だった。チャート UI 経路（`ohlc.py`）は `t_first = nan` を渡すため v1.0 の式でも条件 2 を評価しておらず、実データ A/B（jp225_tick・4,000 バー）で σ̂ 中央値・σ̂_CO>0 率・σ̂ 最大値がいずれも**完全に不変**（5m: 0.1000%・0.058%／1D: 0.9682%・20.535%）。
- **検証**: Δ*=0 で条件 2 を見ないこと、条件 1 は Δ*=0 でも効くことを固定した。

## ISSUE-210: [既存の構造問題] indigators 配下の指標パッケージは同一 pytest セッションで同時実行できない（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: `indigators/<pkg>/tests/test_*.py` は `sys.path.insert(0, <pkg>)` の後に `from src import ...` する規約になっている。この `src` は top-level 名であり、複数パッケージのテストを 1 セッションで収集すると先に import された `src` が `sys.modules` に居座り、後続パッケージが自分の `src` を解決できず `ImportError` になる。
- **実測（2026-07-29・cvfe を含めない対照）**:
  ```
  pytest indigators/btlm_trail/tests indigators/ma_marod/tests
  → ImportError: cannot import name 'DEFAULT_EVENT_AGG' from 'src'
     (.../indigators/btlm_trail/src/__init__.py)
  ```
  ⇒ **cvfe を除いた既存 2 パッケージだけでも衝突する**。本件は cvfe 追加以前から存在する。
- **現行の回避**: 各指標パッケージのテストを個別セッションで実行する（`pytest indigators/cvfe/tests` のように 1 パッケージずつ）。
- **抜本対策案（未実施・要承認）**: 各パッケージの `src` を一意な top-level 名（`cvfe_src` 等）へ改名するか、`indigators/<pkg>/__init__.py` を置いて `from indigators.<pkg>.src import ...` に統一する。**既存 3 パッケージ以上のテストと本番の import 経路（`call_binding` / venv `.pth`）へ波及する**ため、影響範囲の評価と承認を要する。
- **cvfe の扱い**: 既存規約（`from src import`）にそのまま従った。cvfe だけ別規約にしても btlm_trail ↔ ma_marod の衝突は解消せず、規約の不統一を増やすだけであるため。
- **関連**: ISSUE-174（indigators の依存解決点）。
- **裁定（2026-07-30・ユーザー承認）**: **現状維持**。抜本対策（`indigators.<pkg>.src` への統一、または `src` の一意名への改名）は 21 パッケージのテストと本番 import 経路（`call_binding` / venv の `.pth`）すべてへ波及するのに対し、症状は開発時の実行単位の制約に留まり製品欠陥ではない。
- **対応**: 運用規約として `PORTING_GUIDE.md` §7.1「テストは 1 パッケージずつ実行する」を新設し、失敗例（`btlm_trail` + `ma_marod` の同時収集で `ImportError`）と理由（`src` が top-level 名であること）、および一括実行時はパッケージごとに別プロセスを起動することを明記した。テスト規約表にも「テストの実行単位」の行を追加した。

## ISSUE-211: [仕様の欠落] CVFE 学習窓の内側に空バー（E06）があると必ず E08 で停止する（2026-07-29）
- **ステータス**: RESOLVED（2026-07-29 起票・**実装側で解決済み／仕様への追記は未裁定**）
- **事象**: 仕様 §3.3 E06 は「ティック < 2 のバーは `available=False` / `sigma_hat=nan`。**処理は継続**」を明示する。しかし §4.5-1 の説明変数は `C_t` の 22 本遡及平均を含むため、空バーの `C_t = nan` が学習標本の行に混入し、§3.3 E08（非有限値を含む設計行列）で `ValueError` となって処理が停止する。E06 の「継続」保証と直接矛盾する。
- **実測（2026-07-29・N=560 / n_har=500 / t0=522）**:
  | 空バー位置 | 修正前 | 修正後 |
  |---|---|---|
  | 100 / 300 / 520 / 521 | `E08_HAR_SINGULAR`（停止） | 継続（available 38 / 38 / 17 / 16） |
  | 522 / 530（学習窓の外側） | 継続 | 継続 |
  ⇒ 既存テストは学習窓**外側**の 530 のみを置いており本欠陥を検出していなかった。
- **対応（実装）**: `engine.build_training_sample` を新設し、非有限な行（無効バー由来）を学習標本から除外して残りで推定する。除外本数を `W05_HAR_TRAINING_ROWS_DROPPED` として WARN 出力する。一括経路と逐次経路の再学習が同一関数を通る。
- **検証**: `tests/test_errors.py::test_e06_inside_training_window_continues`（空バー位置 100/300/520/521 の 4 ケース）。
- **要裁定**: §4.5 に「無効バーの行を学習標本から除外する」を明記するか、`C_t` の欠測補完（直前値保持等）を定めるか。

## ISSUE-212: [仕様の欠陥] CVFE §3.1 の下限 `N = n_har + 22` では出力が 0 本になる（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: 仕様 §3.1 は `bar_edges` の制約を `N ≥ n_har + 22 = 1522` と定め、例も `(1523,)`（＝N=1522）を挙げる。しかし `t0 = n_har + 22` は「予測を開始できる最初のバー番号」であるため、`N = n_har + 22` ではバー番号 `t0` が範囲外となり `available` が全て `False` になる。
- **実測（2026-07-29・n_har=500）**: `N=522 → available 0` / `523 → 1` / `524 → 2`。1 本以上の出力には `N ≥ n_har + 23` が必要。
- **併発する現象**: 空バー（E06）の `C_t = nan` は 22 本の遡及窓を通じて**後続 22 本**の特徴量も無効化する。仕様 §3.3 E06 は「当該バーの」無効化のみを規定し、この伝播を規定していない。`N` が下限近傍だと 1 本の空バーで `available` が 0 本になりうる。
- **対応**: 下限は仕様どおり据え置き、`available` が 1 本も得られない場合に `W06_NO_AVAILABLE_BARS` を WARN 出力するに留めた（沈黙して空の結果を返さない）。
- **要裁定**: §3.1 の下限を `n_har + 23` へ訂正するか。併せて §3.3 E06 に伝播の扱い（欠測補完の要否）を追記するか。
- **裁定（2026-07-30・ユーザー承認）**: §3.1 の下限を **`N ≥ n_har + 23 = 1523`**（例 `(1524,)`）へ訂正し、§3.3 E01 の条件も `< n_har + 23` へ合わせた。E06 の伝播（空バーが後続 22 本の特徴量も無効化する）は仕様どおり据え置き、`W06_NO_AVAILABLE_BARS` の WARN で沈黙を防ぐ現行動作を維持する。
- **検証**: `cvfe` 133 passed。

## ISSUE-213: [仕様の未定義域] CVFE ギャップ EWMA 初期値が 1 本の `nan` で恒久的に死ぬ（2026-07-29）
- **ステータス**: RESOLVED（2026-07-29 起票・**実装側で解決済み／仕様への追記は未裁定**）
- **事象**: 仕様 §4.7-2 の `g_t = p_open,t − p_close,t−1` は、直前バーが無効（E06 で `p_close = nan`）のとき `nan` になる。§4.7-3 の初期値「先頭 200 本の `g²` の平均」に `nan` が 1 本でも混じると初期値が `nan` となり、以降**全ギャップ保有バーの `σ̂_CO` が `nan`** になって系列全体のギャップ成分が死ぬ。仕様は非有限 `g_t` の扱いを規定していない。
- **到達条件**: §4.7-1 の第 1 条件（`bar_edges[t] − bar_edges[t−1] > 1.5 × bar_interval_sec`）はティックの有無を見ずに `True` を返すため、**バー長が不等間隔で直前バーが無効**な場合に到達する。等間隔バーの合成データでは第 2 条件が `t_last_prev = nan` で `False` になるため再現しない（本件は潜在欠陥として検出）。
- **対応（実装）**: `gap.initial_gap_variance` で非有限な `g²` を平均から除外する。更新側（`GapEwma.update`）には元から `isfinite` ガードがあり、初期値側だけが欠落していた。
- **要裁定**: §4.7 に非有限 `g_t` の扱い（除外／直前値保持）を明記するか。

## ISSUE-214: [仕様と因果性の矛盾] CVFE ギャップ EWMA 初期値がギャップ保有バー 200 本未満のとき将来を参照する（2026-07-29）
- **ステータス**: RESOLVED（2026-07-29 起票・**実装側で因果性を優先して解決／仕様への追記は未裁定**）
- **事象**: 仕様 §4.7-3 は EWMA 初期値を「先頭 200 本のギャップ保有バーの `g²` の平均。200 本未満の場合は存在する全本数の平均」と定め、対象を予測開始バー `t0` より前に限定していない。ギャップ保有バーの総数が 200 本未満の場合、`t ≥ t0` のギャップが初期値に混入し、仕様 §4 柱書（バー `t` の算出に参照可能なのは `bar_edges[t]` より厳密に前のティックのみ）に反する。
- **実測（2026-07-29・ギャップ保有 108 本の構成）**: 修正前は `bar_edges[530]` / `bar_edges[545]` での切詰め再計算に対し `sigma_hat` が bit 不一致（`max|Δσ̂| = 8.9e-9`・相対 9.0e-7）。修正後は bit 一致。
- **検出できなかった理由**: 既存の切詰め不変性テストは `session_sec < bar_sec` の構成であり**全バーがギャップ保有**（559 本）になるため、この経路を構造的に通れなかった。寄り付きを遅らせたバーのみをギャップ保有にするフィクスチャを追加して到達させた。
- **対応（実装）**: 初期値の対象を `t0` より前のギャップ保有バーに限定する。200 本以上が `t0` より前に存在する通常のケースでは先頭 200 本は同一であり結果は変わらない。本数が満たない場合は `W03_GAP_INIT_LOOKAHEAD` を WARN 出力する。
- **検証**: `tests/test_causality.py::test_gap_ewma_init_is_causal_when_fewer_than_200_gap_bars`（修正を戻すと失敗することを実測で確認）。
- **要裁定**: §4.7-3 に「`t0` より前のギャップ保有バーに限る」を明記するか。

## ISSUE-215: [仕様の欠陥・実測] CVFE の TSRV 経路でジャンプ誤検出率が許容の 45〜56 倍になる（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: 仕様 §4.4 の z 統計量の分散 `((π²/4)+π−5)·(1/n)·max(1, TQ/BPV²)` は Barndorff-Nielsen & Shephard (2006) が **RV と BPV の比**について導いた漸近分布である。`quality_gate = "DEGRADED"` の経路では §4.1-6 により `measure_id = "TSRV"` となり、**ノイズ補正済みの TSRV** が `V_t` として同じ式に代入されるが、この場合の帰無分布は妥当しない。
- **実測（2026-07-29・ジャンプを含まない DGP・`ω/σ = 1.0` を注入して DEGRADED へ落とす）**:
  | `bar_interval_sec` | gate / measure | Δ* | n（中央値） | jump_flag 率 | 許容（§9 段階 1） |
  |---|---|---|---|---|---|
  | 21,600 | DEGRADED / TSRV | 300 | 72 | **13.57%** | 0.3% |
  | 43,200 | DEGRADED / TSRV | 300 | 144 | **16.79%** | 0.3% |
  | 3,600 | DEGRADED / TSRV | 300 | 12 | 0.00%（§8 K5 で `n<50` は検定無効） | 0.3% |
  ⇒ RV 経路（`n=1440`）の誤検出率は 0.10% で合格しており、TSRV 分岐に固有の問題である。
- **併発する欠落**: §4.1-6 の DEGRADED 行は `measure_id = "TSRV"` のみを定め `delta_star_sec` を規定していない（§4.2 は `measure_id="RV"` かつ `S ≤ 0.10` にのみ適用）。一方 §3.2 は当該フィールドの値を要求する。**実装は基準間隔 300 秒を選択したが、これは仕様の規定ではない**。
- **対応**: 式・既定値ともに変更せず、`tests/test_jumps.py::test_stage1_false_positive_rate_on_tsrv_path` を `xfail(strict=True)` として実測固定した。TSRV 分岐を実際に通ることを別テストで担保している。
- **要裁定**: (a) TSRV 用の帰無分布を §4.4 に追加する／(b) DEGRADED 経路ではジャンプ分離を行わない（§4.4 の適用条件から TSRV を外す）／(c) §4.1-6 に TSRV の `delta_star_sec` を明記する。**§9 段階 5 の凍結前に必要**。
- **裁定（2026-07-30・ユーザー承認）**: 案 (b) と (c) を採用。§4.4 の適用条件を `measure_id = "RV"` のみへ改め（`TSRV` は `RRANGE`/`PARK` と同じく `C_t = V_t` / `J_t = 0` / `jump_flag = False` へ縮退）、あわせて §4.1-6 の `DEGRADED` 行へ `delta_star_sec = 300` を明記した。
  - 理由: 案 (a)（TSRV 用の帰無分布を導出）は研究課題であり、仕様凍結（§9 段階 5）に間に合わない。誤検出率 13.57%／16.79% は「ジャンプ検定が機能していない」水準であり、機能しない検定を通すより実行しない方が害が小さい。
- **検証**: `xfail(strict)` を解除し、TSRV 経路の `jump_flag` 率が **0.0%** であること、および測定量レベルで `C_t = V_t` / `J_t = 0` が成立することを固定した（TSRV 分岐を実際に通ることも同テストで担保）。

## ISSUE-216: [仕様と因果性の矛盾] CVFE §4.7-1 のギャップ判定は当該バーのティック時刻を要する（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: 仕様 §4 柱書は「バー `t` の `σ̂_t` を算出する際、参照可能な情報は `bar_edges[t]` より厳密に前のティックに限る」と定める。しかし §4.7-1 の第 2 条件は「**バー t の最初のティック時刻** − バー t−1 の最後のティック時刻」を用いる。`t_first ∈ [bar_edges[t], bar_edges[t+1])` であるから、この参照は柱書の制限を満たさない。
- **帰結**: `σ̂_t` は `bar_edges[t]` の時点では確定できず、当該バーの最初のティックが到着するまで待つ必要がある。リアルタイム運用では「バー開始と同時に `σ̂_t` を得る」ことができない。バンド構築（CEB）側の利用形態に影響する。
- **対応**: 仕様の式をそのまま適用し、`tests/test_causality.py` の docstring に事実を明記した。
- **要裁定**: 第 2 条件を「バー t−1 の最後のティック時刻 と `bar_edges[t]` の差」へ改めるか、柱書に例外を明記するか。
- **関連**: ISSUE-209（同じ §4.7-1 の `delta_star_sec = 0` 問題）。
- **裁定（2026-07-30・ユーザー承認）**: 条件 2 の被減数を「バー `t` の最初のティック時刻」から **`bar_edges[t]`** へ改めた（§4.7-1）。柱書に例外を設ける案は採らない（因果律は本仕様の設計原理であり、例外を認めるとリアルタイム運用と CEB 側の利用形態が仕様から読めなくなる）。
  - `bar_edges[t]` は入力として既知、`t_last_prev` はバー `t−1` の確定値であるため、判定はバー開始時点で確定する。`t_first ≥ bar_edges[t]` であるから判定は「ギャップと認めにくくなる」方向へのみ動く。
- **副次的影響（テスト基盤）**: ギャップを作る操作子が「翌バーの寄り遅れ」から「前バーの早仕舞い」へ変わるため、合成フィクスチャに `early_close_bars` を追加した。
- **検証**: `t_first` の値・欠損に依らず判定が一定であることを直接固定するテストを追加。

## ISSUE-217: [仕様の曖昧性] CVFE §9 段階 1 の「5σ ジャンプ」の σ の尺度が未定義（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: 仕様 §9 段階 1 は「既知の大きさのジャンプ（`5σ`）を注入したバーの `jump_flag` 検出率が 90% 以上」を求めるが、`σ` の尺度を定義していない。「バー全体の σ（積分ボラティリティの平方根）」と「1 サンプル収益の σ」では 2 桁近く異なる。
- **採用した解釈**: バー全体の σ の 5 倍（ジャンプ検定文献の慣行と一致）。この解釈での実測検出率は 100%（2,000 バー・n=1440・シード 22）で合格。
- **要裁定**: §9 段階 1 に尺度の定義を追記する。
- **関連**: ISSUE-206（§9 段階 2 の `ω/σ` にも同種の尺度未定義がある）。
- **裁定（2026-07-30・ユーザー承認）**: **バー全体の σ（積分ボラティリティの平方根）**を §9 段階 1 に明記した（ジャンプ検定文献の慣行と一致。この解釈での実測検出率は 100%）。

## ISSUE-218: [仕様と実装経路の乖離] CVFE のチャート UI 経路はティックを受け取れず PARK 縮退で動作する（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: `CVFE_spec_v1.0.md` §3.1 は入力を `ticks`（`(K,2)` の `[unix_time_sec, mid_price]`）と定めるが、`indicator_ui` の計算経路（`call_binding._TABLE` → `add_*(chart, df, ...)`）が渡せるのは **OHLC の DataFrame** のみである。したがって §4.1 の気配品質診断（`RV̄(Δ)` / `ω̂²` / `freeze_ratio` / `S`）は実行できない。
- **現状の動作**: 仕様 §4.1-6 が「高頻度データを使用しない」場合の縮退先として定める `quality_gate = "FAIL"` / `measure_id = "PARK"` をそのまま採用する（`indigators/cvfe/src/ohlc.py`）。§4.3 の `PARK`（`PK_t = (ln H − ln L)²/(4 ln 2)`）は高値・安値のみで算出できるため成立する。測定量より下流（§4.4〜§4.8）は測定量に依存しないため `engine` の関数をそのまま再利用しており、UI 用の別実装は持たない。
- **精度への影響（仕様 §7-6・附録 A）**: `Var(ln σ̂)` は `PARK` 0.08575 に対し `RV`（288 本）0.00174 で **49.3 倍の効率差**がある。本仕様が測定量の高頻度化を設計原理 P1 に掲げた根拠そのものが、UI 経路では効かない。
- **実測（2026-07-29・実 UI・ライブ 8000・NI225 5 分足）**: 指標一覧から `cvfe` を追加 → 別 pane に σ̂ / σ̂_OC / σ̂_CO の 3 系列が描画され、レジェンドに `cvfe 0.093 0.093 0`（% 表示）を確認。コンソールエラー 0 件。再読込後も復元される。
- **恒久対策の選択肢（要裁定）**:
  1. `indicator_ui` の計算経路にティック供給口を新設する（`data/marketdata/ticks/**/JP225_ticks.parquet` が既に存在する）。**共有ベースの変更**であり影響範囲の評価が必要。
  2. UI 表示は PARK 縮退のままとし、高精度が要る用途（CEB v1.1 への `sigma_hat` 供給）はバッチ経路（`compute_cvfe`）に限定すると仕様へ明記する。
  3. 仕様 §3.1 の入力に OHLC 経路を第 2 の正式入力として追加する。
- **関連**: ISSUE-209（`delta_star_sec = 0` のギャップ判定）／仕様 §10 TBD-7（適用時間足）。
- **裁定（2026-07-30・ユーザー承認）**: 選択肢 2。**UI 表示は PARK 縮退のままとし、精度を要する用途はバッチ経路に限定する**旨を §3.1 へ正式に明記した。
  - 理由: UI 表示は概形把握が目的で 49.3 倍の効率差を必要としない。選択肢 1（ティック供給口の新設）は `call_binding` の共有シグネチャ変更を伴い 21 指標パッケージ全体へ波及する。選択肢 3（OHLC を第 2 の正式入力にする）は設計原理 P1（測定量を高頻度データへ移行する）と正面から衝突し §2 の改訂まで必要になる。
  - 明記した内容: OHLC しか渡せない経路は §4.1-6 の FAIL 行に沿って `quality_gate = "FAIL"` / `measure_id = "PARK"` へ縮退する。`sigma_hat` を CEB v1.1 へ供給する用途は `compute_cvfe`（バッチ経路）を用いる。
- **検証**: 実 UI（8001）で cvfe を追加 → 描画・コンソールエラー 0 件を再確認。

## ISSUE-219: [不具合・再現済み] common.module_loader が exec 失敗モジュールをキャッシュし、2 回目以降に壊れたモジュールを配布する（2026-07-29）
- **ステータス**: RESOLVED（2026-07-29 起票・SOLID 全体監査で検出・**本エージェントが独立再現**）
- **事象**: `common/module_loader.py:69-79` の `_exec_into_sys_modules` は `finally` で `_LOADING` を落とすが、`exec_module` が例外を送出した場合でも `sys.modules[name]` に**半構築のモジュールが残る**。以降は `_cached_ready`（:59-66）が「exec 完了済み」と判定して当該モジュールを返すため、2 回目以降の呼び出しは**例外を出さずに壊れたモジュールを配布する**。
- **実測（2026-07-29・再現コード）**:
  ```
  boom.py = "VALUE = 1 / raise RuntimeError('exec 失敗') / VALUE = 2"
  1 回目: RuntimeError: exec 失敗
  2 回目: 例外なし → VALUE=1        ← 壊れたモジュールを配布
  sys.modules に残存: True
  参考 CPython 標準 import: 1 回目 RuntimeError / 2 回目も RuntimeError（毎回失敗）
  ```
  CPython は import 失敗時に `sys.modules` から削除するため、本ローダは**標準機構と挙動が乖離**している。
- **影響**: 指標 src の動的ロード（`ma_marod` / `btlm_trail_marod` の参照実装解決、`call_binding._load_src_package`）で src に構文誤り以外の実行時例外があると、初回だけエラーになり以降は「一部だけ定義された」モジュールで計算が進む。**沈黙した誤計算**になりうる。
- **既知性**: ISSUE-185 は「ロック外二重チェックによる半構築露出（並行性）」であり、本件は exec 失敗時のキャッシュ汚染で**別欠陥**。
- **対策案（未実施・要承認）**: `_exec_into_sys_modules` の `except` で `sys.modules.pop(name, None)` してから再送出する（CPython と同一の後始末）。共有プリミティブの変更のため影響範囲の評価を要する。
- **対応（2026-07-30）**: `_exec_into_sys_modules` に `except BaseException` を追加し、自分が登録した実体のみ `sys.modules` から除去してから再送出する（CPython 標準 import と同一の後始末）。
- **実測（修正後）**: 1 回目 RuntimeError → 2 回目も RuntimeError（`sys.modules` 残存なし）。修正前は 2 回目が例外なしで `VALUE=1` を返していた。
- **回帰テスト**: `common/tests/test_module_loader.py` に 3 件追加（キャッシュ汚染なし・`_LOADING` が残らない・成功時のキャッシュは従来どおり）。**検出力を実証**（修正を戻すと失敗）。
- **⚠ 既存の挙動壁を意図的に更新**: `indicator_ui/api/tests/test_module_loader.py::test_failed_exec_leaves_module_in_sys_modules` は ISSUE-185（ローダ一本化）で「当時の既存挙動を変えていない」ことを固定した壁だった。今回その挙動自体が欠陥と判明したため `test_failed_exec_removes_module_from_sys_modules` へ改め、変更理由を docstring に明記した。ISSUE-185 が担保した他の不変条件（相対 import 解決・成功時キャッシュ・並行安全）は維持している。

## ISSUE-220: [不具合・再現済み] インジケーター追加ダイアログの「★ お気に入り」が常に 0 件になる（2026-07-29）
- **ステータス**: RESOLVED（2026-07-29 起票・SOLID 全体監査で検出・**本エージェントが実行で再現**）
- **事象**: `indicator_dialog_controller.js:49-50` が `data-category="__favorites__"` のセンチネルを **category チャネルにも代入**する。
  ```js
  this._filter.category = c.dataset.category || null;              // ← '__favorites__' が入る
  this._filter.favoriteOnly = c.dataset.category === '__favorites__';
  ```
  受け手の `facade.js:33` は `d.category.nameKey !== category` で除外するため、どの指標のカテゴリも `'__favorites__'` と一致せず**全件が落ちる**。
- **実測（2026-07-29・facade を直接実行）**:
  | 呼び出し | 件数 |
  |---|---|
  | 全件（tab=indicator） | 23 |
  | 現行の呼ばれ方（category='__favorites__' + favoriteOnly=true・お気に入り 2 件登録） | **0 件** |
  | category=null に直した場合 | 2 件（ma_marod / cvfe） |
- **既存テストが検出しない理由**: web 919 件は `listForView` を正しい引数（category=null）で呼ぶ単体テストのみで、`IndicatorDialogController` 経由の結線を通していない。
- **対策案（未実施・要承認）**: `this._filter.category = c.dataset.category === '__favorites__' ? null : (c.dataset.category || null);`。併せて controller 経由の回帰テストを追加する。UI 挙動の変更にあたるため承認を要する。
- **対応（2026-07-30）**: `indicator_dialog_controller.js` でセンチネルを category チャネルから分離した。`FAVORITES_SENTINEL` を export して index.html の `data-category` と単一情報源にした。
- **回帰テスト**: `tests/indicator_dialog_favorites.test.js` を新設（controller 経由で facade まで通し 0 件にならないこと・通常カテゴリと「すべて」への非波及）。**検出力を実証**（修正を戻すと失敗）。
- **実 UI 確認**: お気に入り 2 件登録 → 「★ お気に入り」で **2 件表示**（修正前は 0 件）。

## ISSUE-221: [不具合・確認済み] インジケーター一覧のカテゴリ絞り込みに 2 カテゴリのボタンが無く、24 指標中 12 件が到達不能（2026-07-29）
- **ステータス**: RESOLVED（2026-07-29 起票・SOLID 全体監査で検出・**本エージェントが実測**）
- **事象**: カタログ側のカテゴリ（`catalog.js`）と、サイドバーの静的ボタン（`web/index.html:70-74`）が二重定義で乖離している。
- **実測（2026-07-29）**:
  | カテゴリ | カタログ登録数 | サイドバーのボタン |
  |---|---|---|
  | `cat.technical` | 3 | あり |
  | `cat.statistics` | 1 | あり |
  | `cat.volume` | 8 | あり |
  | `cat.oscillator` | **10** | **なし** |
  | `cat.band` | **2** | **なし** |
  ⇒ **24 指標中 12 件**（cvfe を含む）がカテゴリ絞り込みから漏れる。「すべて」と検索からは到達できるため機能全損ではない。
- **原因**: カテゴリ軸がデータ（`catalog.js`）と静的マークアップ（`index.html`）の 2 箇所に定義され、新カテゴリの指標追加が HTML の同時改変を要する（OCP 違反）。
- **対策案（未実施・要承認）**: サイドバーを `list()` のカテゴリ集合から動的生成する（テンプレートメニュー `#tpl-menu` が既に採る方式）。UI 変更のため承認を要する。
- **対応（2026-07-30）**: カテゴリボタンをカタログから動的生成する方式へ変更した。
  - `usecase/catalog.js` に `categories()` と `CATEGORY_LABELS` を新設（表示名の単一情報源）。
  - `indicator_controller._renderCategorySideItems()` が bind 前に生成（冪等）。
  - **配信される 3 ページすべて**から静的ボタンを撤去（`indicator_ui/web` / `unified_ui/web` / `simulator/replay_ui/web`）。当初 `indicator_ui/web` のみ撤去したところ、実際に配信される `unified_ui/web` の取り残しで**サイドバーが二重表示**になり、実 UI 検証で検出した。
- **回帰テスト**: `tests/catalog_categories.test.js` を新設（全カテゴリ網羅・表示名定義・各カテゴリ 1 件以上到達・合計＝全指標数・**配信 3 ページに直書きが無いこと**）。
- **実 UI 確認**: サイドバーは すべて / ★お気に入り / テクニカル / **オシレーター** / 統計 / 出来高 / **バンド**（重複なし）。各カテゴリの件数は 4 / 9 / 1 / 7 / 2 で合計 23＝`tab=indicator` の全件。

## ISSUE-222: [設計欠陥] indicator_ui の DatasetPort が無検査キャストで、結線漏れが HTTP 500 へ沈黙劣化する（2026-07-29）
- **ステータス**: RESOLVED
- **事象**: `usecase/dataset_port.py:116-123` の `candle_dataset_port()` が `return dataset_port()  # type: ignore[return-value]` と無検査キャストする。`DatasetPort` と `CandleDatasetPort` を ISP で分割したのに注入シームは `set_dataset_port` 1 本しかなく、注入された実装が両インターフェースを満たすか誰も検証しない（LSP/ISP）。
- **実測（監査エージェント）**: `is_known` / `is_known_timeframe` / `load_dataframe` のみを持つ**合法な `DatasetPort` 実装**を注入すると `isinstance(p, DatasetPort)` は True のまま `/candles` が `internal: 'OnlyDatasetPort' object has no attribute 'load_candles'`（HTTP 500）へ劣化する。
- **対策案（未実施・要承認）**: `set_candle_dataset_port` を別シームに分離するか、`candle_dataset_port()` で `isinstance(_PORT, CandleSeriesPort)` を検査し、未充足時は ISSUE-183 の未注入時と対称に `RuntimeError` を送出する。
- **対応（2026-07-30・対策案のうち「面の充足を検査して RuntimeError」を採用）**:
  - **再現を先に実測**（監査エージェントの報告を鵜呑みにせず自分で確認）: `is_known`/`is_known_timeframe`/`load_dataframe` のみを持つ実装は `isinstance(p, DatasetPort)=True` / `isinstance(p, CandleDatasetPort)=False` で、`candle_dataset_port()` は**例外を出さずそのまま返す**。失敗は `load_candles` 呼び出し時の `AttributeError` まで潜伏し、`framework/server.py:265` の総括 catch が HTTP 500 `internal` へ変換する＝**沈黙劣化を確認**。
  - `usecase/dataset_port.py` の `candle_dataset_port()` で `isinstance(port, CandleSeriesPort)` を検査し、未充足は結線漏れとして `RuntimeError` を送出する。ISSUE-183 の**未注入時と対称**（欠落を serving 中へ先送りしない）。
  - シーム分離（`set_candle_dataset_port` 追加）は採らない。注入点が 2 本になると「片方だけ注入」という新しい不整合を生むため、単一シーム＋取得時検査の方が状態空間が小さい。
- **検証**: 回帰テスト 2 件を追加（`tests/test_dataset_port.py`）。DatasetPort だけを満たす合法実装で `RuntimeError`、既定 gateway では素通し（ガードが過剰でないこと）。**変異注入**で無検査キャストへ戻すと `DID NOT RAISE` で失敗することを確認。`indicator_ui/api` **441 passed**。

## ISSUE-223: [仕様変更] CVFE の表示を別 pane オシレータから価格スケール上のバンドへ変更（2026-07-30）
- **ステータス**: RESOLVED（2026-07-30 起票・依頼者指示により実施・**正本仕様への反映は未裁定**）
- **依頼**: 「CVFE をチャートパネルのローソク足にバンドとして表示する仕様に変更」「外れ値だけの POT も追加」（2026-07-30）。
- **仕様との関係（重要）**: 正本仕様 `CVFE_spec_v1.0.md` §1 スコープは「**含まない**：区間バンドの構築（CEB の責務）」と明記しており、本変更は当該スコープの拡張にあたる。**σ̂ の算出（§4.1〜§4.8）は一切変更していない**。バンドは σ̂ からの表示用派生量であり、CEB v1.1 が定める条件付被覆の保証（LR_ind 等）を持たない。
- **バンドの定義（新設・表示仕様）**: σ̂_t はバー t が開く前に確定するため、中心を**1 本前の確定終値**に置く。
  ```
  中心 mid_t = close_{t−1}
  上下       = mid_t · exp(± k · σ̂_t)      k は内側 1.0 / 外側 2.0（可変）
  ```
  対数収益の標準偏差なので価格への写像は指数（比率）。当該バーの値動きでは動かない（非リペイント・`tests/test_bands.py` で固定）。
- **外れ値水準の方式（裁定 2026-07-30）**: 当初 POT（一般化パレート分布・Hosking-Wallis PWM）を自作したが、**リポジトリ内に既存の外れ値水準プリミティブが存在する**ことを確認したため破棄し、そちらを無改変参照する方式へ差し替えた。
  | 選択肢 | 判定 |
  |---|---|
  | `common.event_quantiles.outlier_event_quantiles` を参照 | **採用**（ma_marod / btlm_trail_marod と同一規約・episode declustering 済み・表示規約も単一情報源） |
  | 自作 GPD（POT） | 破棄（repo 初出の新規実装・保守対象増） |
  | `scipy.stats.genpareto` | 不採用（scipy 未インストール・仕様 §6「numpy のみ」違反・技術スタック変更） |
  - 調査実測: `genpareto|GPD|peaks_over|pickands|hill_estimator|tail_index|extreme_value` の grep ヒット **0 件**（`.venv`・worktree 除外）。scipy は `ModuleNotFoundError`。
  - 実装: 標準化残差 `z_t = ln(C_t/C_{t−1})/σ̂_t` → `common.marod_bands.quantile_bands` で因果正常バンド → `outlier_event_quantiles` で典型深度・極端深度 → `mid · exp(evq · σ̂_t)` で価格へ写す。表示は `emit_event_quantile_lines` に委譲。
- **系列（7 本）**: `cvfe_mid` / `cvfe_u1` / `cvfe_l1` / `cvfe_u2` / `cvfe_l2` / `cvfe_evq_{med|ext}_{hi|lo}`。placement は `pane` → `overlay`、カテゴリは `oscillator` → `technical`。
- **実 UI 検証（2026-07-30・ライブ 8000・NI225 5 分足）**: 価格パネル上に 8 本描画。水準の順序が `ext_lo 61,672 < l2 61,891 < l1 61,958 < 価格 62,015 < u1 62,091 < u2 62,158 < med_hi 62,179 < ext_hi 63,118` となり、外れ値水準が 2σ の外側に出ることを実測で確認。コンソールエラー 0 件。
- **テスト**: cvfe 116 passed / 4 xfailed、indicator_ui API 438 passed、web 919 passed。
- **表示形式の追補（2026-07-30・ユーザー指摘「ジグザグで視認性が悪い」）**: バー毎の帯を線で繋ぐことに情報上の意味がほぼ無いことを実測で確認し、**既定を水平ライン（最新水準のみ）へ変更**した。
  - 恒等式 `Δln(上端) = Δln(mid_t) + k·Δσ̂_t` による分解（jp225_tick 5 分足・有効 3,477 本）:
    | 成分 | 分散寄与 | 上端との相関 |
    |---|---|---|
    | 価格成分 `Δln(close_{t−1})` | **100.4%** | **0.924** |
    | σ̂ 成分 `k·Δσ̂_t` | 15.3% | 0.191 |
  - ⇒ 線の傾きが示すのは価格そのものの動きで、σ̂ の情報は帯の**幅**にしかない。加えて各点は別バーに対する独立した 1 期先予測区間であり、点間を結ぶ線分に対応する量が存在しない（移動平均のように連続推移する量を繋ぐのとは異なる）。
  - 中間案として「最新水準のみをチャート幅いっぱいの水平線で 1 組」も試したが、**今日の水準を過去バーへ引き延ばすことになり誤読を招く**（過去バーには当時の別の水準があった）とのユーザー指摘により棄却。
  - **最終形（ユーザー裁定 2026-07-30）**: 各バーの水準を「**そのバーの幅だけの短い水平ダッシュ**」として並べる。バー間を繋がないので傾きが生じず、ドットより接点が読める。`display_mode` は `dashes`（既定）／`bands`（線で繋ぐ・検証用）。
  - **共有描画基盤への新系列種別 `level_dash` を追加**（ユーザー承認済み・下記）。
- **共有描画基盤の拡張（`level_dash`・ユーザー承認 2026-07-30）**: 既存 3 種別（`line` / `histogram` / `horizontal_line`）はいずれも 1 時刻 1 値で、バー幅の水平ダッシュを表現できない。同値 4 値の Candlestick（同事＝実体が潰れて水平線 1 本・幅はローソク足と一致）で描く種別を追加した。
  | 層 | ファイル | 変更 |
  |---|---|---|
  | domain | `domain_models.js` | `SeriesKind.LEVEL_DASH` |
  | domain | `series_kind.js` | 能力台帳へ 1 エントリ（設計上の拡張点） |
  | adapter | `series_render_router.js` | 経路 `level_dash` を追加 |
  | adapter | `chart_renderer.js` | `renderLevelDash` |
  | adapter | `series_drawer.js` | 系列定義に CandlestickSeries ＋ `{time,value}` → 同値 4 値の展開 |
  | api | `fake_chart.py` | `create_level_dash` |
  - **payload 契約は変更していない**。back は既存の `{time, value}` のまま出し、OHLC への展開は表示層の 1 箇所だけで行う（既存 3 種別へ非波及）。
  - 既存 3 種別の 1:1 回帰壁（`tests/series_kind.test.js` の legacy 比較）は**緩めずに維持**し、新種別は別テストで能力値を明示固定した。
  - `tailUpdatable=false`（末尾差分更新は `{time,value}` を `series.update` へ渡す経路で Candlestick と形が合わないため full 再描画のみ）。
  - 旧 duck type（`create_level_dash` を持たない chart）では `create_line` へ落ちる後方互換を実装しテストで固定。
- **主張の強さの調整（ユーザー指摘「水平ダッシュが主張しすぎ」2026-07-30）**: ダッシュの**幅**はローソク足幅へ自動追従するため調整できない。調整可能な軸は**不透明度**である（ユーザー了承）。
  - `dash_opacity` パラメータを新設（既定 **0.5**＝従来の半分・範囲 0.05〜1.0）。全ダッシュ系列の色 alpha に一括で掛かる。
  - 色は `scale_alpha()`（cvfe ローカル）で派生させる。**共有定数 `common.event_quantiles.EVQ_COLOR` は書き換えない**（他 2 指標へ非波及）。書式は `rgba()` / `rgb()` に対応し、解釈できない書式は素通しする。テストで固定。
- **実 UI 検証（2026-07-30・ライブ 8000・NI225 5 分足）**: 各バーにローソク足幅の水平ダッシュが並ぶことを `barSpacing=6` へ拡大して目視確認（既定の `barSpacing=0.5` ではローソク自体が 0.5px 幅のため点に見える）。`dash_opacity=0.5` で価格ローソクの視認性を損なわないことを確認。コンソールエラー 0 件。web 920 passed / API 438 passed / cvfe 124 passed。
- **要裁定**: 正本仕様 §1 のスコープ記述（バンド構築は CEB の責務）を改訂するか、本バンドを「表示専用の派生量であり CEB の被覆保証を持たない」と仕様へ明記するか。


## ISSUE-224: [重大不具合・再現済み] 日足で σ̂ が 1264% と発散する（PARK=0 バーの C_FLOOR クリップが Jensen 補正を暴走させる）（2026-07-30）
- **ステータス**: RESOLVED（2026-07-30 起票・バンド表示の検討中に検出・**本エージェントが原因まで特定**）
- **事象**: OHLC 経路（PARK 縮退）を日足へ適用すると `σ̂` の中央値が **12.64（＝日次 1264%）** になる。同じデータの Parkinson σ 中央値は 0.0087（0.87%）で測定量そのものは正しく、**HAR 予測段で発散している**。
- **実測（2026-07-30・jp225_tick_1D・直近 4,000 本）**:
  ```
  σ̂ 中央値 12.6395   最大 872.86        ← 異常
  Parkinson σ 中央値 0.0087（0.87%）    ← 正常
  har_resid_var s² = 65.79
  har_coef = [11.58, 0.373, -0.251, 2.389, 0.0, -19.62]
  ```
- **原因（連鎖）**:
  1. 日足 3,685 本のうち **71 本（1.9%）が high == low**（無取引日・単一プリントの日）→ `PK_t = (ln H − ln L)²/(4 ln 2) = 0`。
  2. 仕様 §4.5-1 が「`C_t < 1e-16` は `1e-16` にクリップ」と定めるため `ln C = −36.84` になる。他のバーの `ln C` は中央値 −9.49 なので、この 71 本が極端な外れ値になる。
  3. `ln C` の分散が **1.313 →（クリップ行を含めると）15.485** へ跳ね上がり、HAR の残差分散 `s²` が 65.79 に膨張。
  4. 仕様 §4.6 の Jensen 補正 `σ̂ = exp(ŷ/2 + s²/8)` で `exp(65.79/8) = exp(8.22) ≈ 3,720 倍` が乗る。
  ⇒ **仕様が定める 2 つの規定（§4.5-1 のクリップ・§4.6 の Jensen 補正）の組み合わせが、レンジ 0 のバーが存在する系列で破綻する。**
- **時間足による差**: 5 分足では high == low が 4,000 本中 **3 本**のみで実害が小さい（実測 σ̂ ≈ 0.093% で妥当）。**バー長が長いほど無取引日が混入しやすく、日足以上で顕在化する**。仕様 §10 TBD-7（適用時間足）と直結する。
- **対策案（未実施・要承認）**:
  1. `PK_t = 0`（レンジ 0）のバーを §3.3 E06 と同様に**無効バー扱い**にし、測定量にも学習標本にも入れない。レンジ 0 のバーはボラティリティの情報を持たないため、これが最も抜本的。
  2. `C_FLOOR` を系列依存（例: 有効 `C` の下側 0.1% 分位）にする。仕様 §4.5-1 の固定値 1e-16 の改訂が必要。
  3. `s²` を外れ値耐性のある推定量（MAD ベース等）にする。仕様 §4.5-5 の改訂が必要。
  いずれも仕様本文の改訂を伴うため裁定を要する。
- **関連**: ISSUE-218（UI 経路が PARK 縮退である件）／仕様 §10 TBD-7（適用時間足）。
- **対応（2026-07-30・ユーザー承認済み・案 1 を採用）**: `measures_from_ohlc` でレンジ 0 のバー（`PK_t = 0`）を **無効バー**（`valid=False`）にした。仕様 §3.3 E06（ティック < 2 のバーを `available=False` とし処理は継続）と同じ扱い。測定量にも学習標本にも入らない。
- **実測（修正後）**:
  | 時間足 | σ̂ 中央値 | s² | Parkinson σ 中央値 |
  |---|---|---|---|
  | 日足 | **0.9682%**（修正前 1264%） | **0.572**（修正前 65.79） | 0.8823% |
  | 5 分足 | 0.0976%（修正前 0.0977%＝実質不変） | 0.719 | 0.0909% |
- **回帰テスト**: `tests/test_ohlc.py` を新設（レンジ 0 バーの無効化・σ̂ が発散しないこと・レンジ 0 が無い系列で挙動不変）。
- **残る要裁定**: 仕様 §3.3 E06 の定義（ティック < 2）に「レンジ 0」を加える追記。実装は先行して是正済み。

## ISSUE-225: [UX] パラメータの命名が概念単位で統一されておらず認知負荷が高い（2026-07-30）
- **ステータス**: RESOLVED（2026-07-30 起票・同日是正・ユーザー承認済み）
- **事象**: 同一概念「直近 N 本のローリング窓」に対し、**6 通りの呼び名**が混在していた。加えて cvfe ではパラメータ名と系列名が対応せず、設定項目とチャート上の線を突き合わせられない状態だった。
  | 旧ラベル | 該当 |
  |---|---|
  | （ラベルなし） | `maxbars` ×3・`empirical_n`・`atr_period` |
  | 期間 | `length` ×2・`smoothing_length` |
  | 分位の窓 | `window_n` ×2 |
  | バンド内実績率の本数 | `n_cov` |
  | 標準化窓 W（直近本数） | `window`（profit_* 共有） |
  | 学習本数 | `n_har` |
- **ユーザーの指摘（2026-07-30）**: 「認知負荷が重たくなっている原因が分かった。概念は同じだが、命名が統一されていない点だ」「そもそも、なぜ『窓』?」「とにかく認知負荷を軽くしろ」。
- **是正 1（命名の統一）**: 「**移動期間**」へ一本化した。`window` の直訳「窓」は字面から実体を推測できないため廃止。同一指標に窓が 2 つ以上ある場合のみ用途を括弧で付す。
  - 「回帰移動期間」で全部を統一する案は**実測により不採用**。窓 13 個のうち回帰は 4 個（`maxbars` ×3・`n_har`）で、移動平均 3・経験分位 3・その他 3。移動平均の窓を「回帰」と呼ぶと別の誤解を生む。
  - 結果: 移動期間 ×3 / 移動期間（分位）×3 / 移動期間（回帰）×2 / 移動期間（平均）×2 / 移動期間（実績率）×1 / 移動期間（平滑）×1。残る 1 件は `profit_*` の共有ビルダー既定で、呼び出し側が `mfi_period` / `fast` / `slow` と個別に読める名前を持つため対象外。
- **是正 2（cvfe のパラメータ削減）**: 公開パラメータを **14 → 6** に削減した。削った項目はいずれも「既定から動かす根拠が無い」ことを実測または仕様で確認済み。
  | 内部固定にした項目 | 根拠 |
  |---|---|
  | `refit_every=0` | 実測: 1/20/100 いずれも凍結に対し DM 検定で**有意差なし**（p=0.94〜0.97）。毎バー再学習は約 200 倍遅い（0.09s → 17.79s・2,600 本） |
  | `lam_gap=0.97` | 窓開けが無い時間足では効果ゼロ。既定から動かす根拠が仕様 §10 に無い |
  | `q_low` / `q_high` / `window_n` / `q_out` / `k_events` / `event_agg` | 外れ値判定の内部しきい値。**対応する線を持たない**のに「正常バンド」と命名しており混乱の主因だった |
  | `show_outer` / `show_mid` | σ線②は主要 2 本の一方なので常時表示。中心線は既定どおり非表示 |
- **是正 3（名前と系列の対応）**: 残した項目のラベルを、動かす系列名と 1:1 で対応させた（`σ線①の倍率` → `cvfe_u1`/`cvfe_l1` 等）。ツールチップに系列名と実測到達率を明記した。
- **検証**: 実 UI（ライブ 8000）で設定ダイアログが 6 項目になることを確認。コンソールエラー 0 件。web 920 passed / API 438 passed / cvfe 124 passed / ma_marod 43 / btlm_trail 31 / btlm_trail_marod 30 / moving_averages 61。
- **是正 4（色設定の重複解消・2026-07-30）**: パラメータータブの `STYLE` グループにあった `color` を削除した。系列色は「スタイル」タブが**系列ごとに 8 件**持っており、同じ設定が 2 箇所に存在していた。パラメータは **6 → 5** 個になり、パラメーター タブから `STYLE` グループ自体が消えた。初期色は `add_cvfe` の既定値を用いる。
  - 実 UI 確認: パラメーター タブは `CALC`（移動期間）と `DISPLAY`（σ線①②の倍率・外れ値線表示・表示形式・ダッシュの濃さ）のみ。色は「スタイル」タブの 8 系列に一本化。
  - **他指標にも同じ重複が残っている**（`tgp_btlm` / `btlm_trail` / `btlm_trail_marod` / `ma_marod` に `color`、`price_range_power` に `bull_color` / `bear_color`）。本件は cvfe のみの指示のため未着手。要裁定。
- **恒久ルール化**: 本件の教訓をメモリ `minimize-cognitive-load` へ記録した（UI・命名・パラメータは削る判断を先に行う／同一概念に複数の呼び名を作らない／比喩を持ち込まない／根拠のない項目は出さない）。

## ISSUE-226: [不具合・再現済み] cvfe のスタイルで色を変更しても反映されない（2026-07-30）
- **ステータス**: RESOLVED（2026-07-30 起票・同日修正・実 UI 検証済み）
- **事象**: 設定ダイアログの「スタイル」タブで cvfe の系列色を変更しても、チャート上の色が変わらない。エラーは出ず黙って無視される。
- **原因**: ISSUE-223 で追加した系列種別 `level_dash` は `CandlestickSeries` で描画するが、**CandlestickSeries に `color` オプションは存在しない**（着色は `upColor` / `downColor` / `borderUpColor` / `borderDownColor` / `wickUpColor` / `wickDownColor` の 6 経路）。
  - 生成時（`series_drawer._renderSeries`）は 6 経路へ単色を複製していた。
  - 一方 変更時（`series_drawer.applySeriesStyle:347`）は `{ color: meta.color }` を `applyOptions` へ渡すだけで、CandlestickSeries はこれを無視していた。
  - ⇒ **生成時と変更時で色の写像が乖離**していたことが原因。
- **修正**: 色写像を `_levelDashColors(color)` として抽出し、**生成時と変更時の唯一の写像点**にした。乖離が再発しない構造にしてある。
- **検証**:
  - 回帰テスト 4 件を `tests/chart_renderer_series_styles.test.js` へ追加（生成時の 6 経路複製・同値 4 値への展開・`applySeriesStyle` の 6 経路反映・可視性・line 系列への非波及）。
  - **検出力を実証**: 修正を戻すと `applySeriesStyle の色が 6 経路すべてへ反映される（回帰）` が失敗することを確認。
  - 実 UI（ライブ 8000・NI225 5 分足）で `cvfe_u1` を緑へ変更 → チャートのダッシュと価格軸ラベルが即時に緑へ変わることを確認。元の色へ戻して終了。
  - web 924 passed / API 438 passed / cvfe 124 passed。
- **関連**: ISSUE-223（`level_dash` の追加）。

## ISSUE-227: [計測完了] 共変量 POT 設計の必須ゲート 1 — 極値指標 θ の実測（2026-07-31）
- **重大度**: —（設計判断の前提となる実測）
- **ステータス**: RESOLVED
- **背景**: 「RSI 水準を共変量とするリターン裾への POT」設計に対し、着手前の必須ゲートとして θ（極値指標）の実測が要求された。θ < 0.2 なら有効標本不足で設計自体が成立しない。
- **道具**: `common/extremal_index.py` を新設（Ferro & Segers 2003, JRSS-B 65(2) の intervals 推定量）。閾値選択や宣言クラスタリングのパラメータを要さない。
  - **道具の妥当性を先に検証**: θ が解析的に既知の ARMAX（θ = 1 − α）で α = 0 / 0.3 / 0.5 / 0.8 を全て ±0.05 以内で回復。iid で θ ≈ 1。単体テスト 14 件。
- **結果 1（リターン裾・ゲート対象）**: jp225_tick の対数リターン、6 時間足 × 4 閾値 × 上下側の **48 条件すべてで θ ≥ 0.2**。
  - θ̂ の範囲 **0.281 〜 0.736**。時間足が長いほど θ は大きい（1m ≈ 0.31–0.39 → 1D ≈ 0.37–0.71）＝高頻度ほどクラスタ化が強い。
  - **ゲート 1 は通過**。ただし最疎条件（15m/4h の q=0.99 上側）は CI 下限が 0.2 を割り込む。当該条件で設計を組むなら再検討が要る。
- **結果 2（RSI-14 自体・「未測定」への回答）**: 同じ推定量で RSI 系列の超過を測ると **θ̂ = 0.107 〜 0.269** で、16 条件中 14 条件が **0.2 未満**。
  - Wilder 平滑（実質 α = 1/14 の EWMA）による強いクラスタ化が実測で確認された。RSI 自体への POT が棄却された理由 3（系列依存による有効標本の崩壊）は**数値的に裏付けられる**。
  - 例: 1h の RSI > 70 は超過 6,303 件に対し**有効クラスタ 675**（1/9 以下）。素の GPD 当てはめは標準誤差を約 3 倍過小評価することになる。
- **⚠ 途中で犯した誤りと是正（重要）**: 最初 CI を stationary bootstrap の**ブロック長 50** で出したところ、**点推定が CI の外**に出た（例 1m q=0.90 下側: θ̂ = 0.341 に対し CI [0.438, 0.532]）。ブロック長がクラスタ規模より短いと依存が壊れ、θ̂ が**独立側（1）へ系統的に偏る**ためである。ブロック長を変えて偏りを実測し是正した:
  | ブロック長 | 10 | 50 | 200 | 1000 | 5000 | 20000 |
  |---|---|---|---|---|---|---|
  | 点推定との差 | +0.359 | +0.146 | +0.048 | +0.013 | +0.006 | +0.002 |
  - 是正後（block = N/20）は全条件で偏り < 0.04（超過 37 件の最疎条件を除く）。**この誤りはゲートを甘く見せる方向**（θ を大きく見せる）だったため、放置すると設計を誤って通していた。
- **未実施**: ゲート 2（ForwardStop による閾値選択の自動化）、ゲート 3（検出力計算）。ゲート 1 が通ったため次に進める状態にある。

## ISSUE-228: [計測完了] 共変量 POT 設計のゲート 2（自動閾値選択）・ゲート 3（検出力）（2026-07-31）
- **重大度**: —（設計判断の前提となる実測）
- **ステータス**: RESOLVED
- **道具（新設）**: `common/gpd.py`（GPD の MLE・Anderson–Darling 適合度・パラメトリック
  ブートストラップ p 値・ForwardStop・共変量 GPD・尤度比検定・検出力）。scipy 非依存。
  `common/extremal_index.py` に intervals declustering を追加（θ̂ と整合する宣言方式）。
  - **道具の妥当性を先に検証**: GPD MLE は既知 (ξ, β) = (0, 1) / (0.2, 2) / (0.4, 0.5) / (−0.15, 1.5) を
    回復。適合度検定は真の GPD を 20 回中 ≤4 回しか棄却せず、対数正規は棄却。共変量 GPD は
    既知 γ1 = 0.35・ξ = 0.20 を回復。単体テスト計 81 件。
- **ゲート 2（ForwardStop・α = 0.05・宣言クラスタリング後の負リターン超過）**:
  | tf | 採択 q | 閾値 u | 棄却数 | クラスタ C | θ̂ | ξ̂ | β̂ |
  |---|---|---|---|---|---|---|---|
  | 5m | 0.75 | −0.00047 | 3 | 8,375 | 0.670 | +0.160 | 0.00071 |
  | 1h | 0.75 | −0.00108 | 3 | 10,070 | 0.806 | +0.197 | 0.00175 |
  | 4h | 0.70 | −0.00147 | 2 | 5,606 | 0.886 | +0.169 | 0.00343 |
  | 1D | 0.70 | −0.00448 | 2 | 1,030 | 0.931 | +0.106 | 0.00860 |
  - ξ̂ は **+0.10〜+0.24 で閾値によらず安定**（重い裾・Fréchet MDA）。GPD 近似が広い範囲で成立する典型的な兆候。
  - **⚠ 自分の初回実行が誤り**: 最初 q ∈ [0.90, 0.99] の格子で回したところ **ForwardStop が一度も棄却せず**、格子の下端 q=0.90 がそのまま採択された。これは選択ではなく**格子の人工物**である。下方へ延長すると q ≤ 0.70 が棄却され（p = 0.005〜0.035）、採択は q = 0.75 に移った。閾値選択を自動化する目的は「都合の良い閾値を選んだ」批判に答えることなので、格子の下端が binding のまま報告していれば目的を果たさなかった。
- **ゲート 3（検出力・α = 0.05・実データの標本数と RSI 分布をそのまま使用）**:
  効果量 γ1 ＝「RSI が 1SD 上がったときの log β の変化」。
  | tf | C | γ1=0（size） | 0.05 | 0.10 | 0.15 | 80% 到達 |
  |---|---|---|---|---|---|---|
  | 5m | 8,372 | 5.5% | 99.5% | 100% | 100% | **0.05** |
  | 1h | 10,094 | 5.5% | 99.5% | 100% | 100% | **0.05** |
  | 4h | 5,602 | 3.5% | 88.5% | 100% | 100% | **0.05** |
  | 1D | 1,026 | 6.0% | 32.0% | 86.5% | 99.0% | **0.10** |
  - **γ1 = 0 での棄却率が 3.5〜6.0%＝名目 5% と整合**。検定が正しく size を保つことを実測で確認した（機構全体の妥当性検査を兼ねる）。
- **ゲート 3（増分版・σ̂ を統制）**: 指摘どおり「単独の γ1 ≠ 0 では不十分」であるため、`log β = γ0 + γ2 log σ̂ + γ1 RSI` として RSI の増分を検定する構成でも測った。σ̂ は CVFE（OHLC 経路）。
  | tf | C | corr(log σ̂, RSI) | γ1=0 | 0.05 | 0.10 | 0.15 | 80% 到達 |
  |---|---|---|---|---|---|---|---|
  | 1h | 4,325 | −0.131 | 8.0% | 82.0% | 100% | 100% | **0.05** |
  | 1D | 910 | −0.329 | 5.5% | 28.0% | 75.0% | 97.5% | **0.15** |
  - σ̂ と RSI の相関は弱い（1h で −0.13）ため、統制しても 1h の検出力はほぼ落ちない。1D は相関が強く（−0.33）標本も小さいため、80% 到達が 0.10 → 0.15 へ悪化する。
- **判定**: ゲート 2・3 ともに通過。**1h が最良の設計点**（C = 10,070／増分検定でも γ1 = 0.05 で 80%）。1D は増分検定では γ1 = 0.15 以上でないと検出できず、事前登録する効果量をそれ未満に置くなら 1D 単独では不足。
- **未実施**: 本検定の実行（観測 γ̂1 の推定と LR 検定）、SPA による無条件経験分位・分位点回帰との比較、Kupiec / Christoffersen 校正。

## ISSUE-229: [事前登録] RSI を共変量とするリターン裾 POT の主検定（2026-08-01・**結果取得前に確定**）
- **重大度**: —（検定の事前登録。以後この内容を変更しない）
- **ステータス**: RESOLVED（実行完了・判定規則 1 により設計を棄却）
- **なぜ事前登録するか**: 勝敗基準を先に固定しないと、結果を見てから解釈を作れてしまう。本エントリは**主検定の実行前**にコミットする（順序は git 履歴で検証可能）。

### 主検定（1 本のみ）
| 項目 | 確定値 |
|---|---|
| 対象 | jp225_tick **1h**・直近 **20,000 本**（CVFE σ̂ の計算コストによる。ゲート 3 増分版と同一窓） |
| 閾値 | **q = 0.75**（下側・ゲート 2 の ForwardStop 採択値 u = −0.00108） |
| 独立化 | intervals declustering（θ̂ で C を決定）→ クラスタ極値のみ使用 |
| モデル | `log β = γ₀ + γ₂ log σ̂ + γ₁ · RSI`（ξ 一定・σ̂ は CVFE OHLC 経路・RSI-14 は 1SD 標準化） |
| 帰無仮説 | **H₀: γ₁ = 0**（σ̂ を統制したうえで RSI に裾情報なし） |
| 検定 | 尤度比検定・自由度 1・**α = 0.05** |
| 最小検出効果量 | **γ₁ = 0.05**（RSI が 1SD 上がると β が +5.1%）。ゲート 3 で検出力 **82%** を実測済み |
| 標本 | C ≈ 4,325 クラスタ（ゲート 3 増分版の実測値） |

### 感度分析（主検定に付随・2 本のみ）
- **5m・4h** の同一構成。**Bonferroni 補正**（各 α = 0.05 / 2 = 0.025）。
- **1D は主検定・感度分析から除外**する。増分検定で 80% 検出力に γ₁ ≥ 0.15 を要し、事前登録した効果量 0.05 に対して検出力不足であるため（ゲート 3 実測）。検出力不足の検定を並べると、非有意を「効果なし」と誤読する余地を残す。

### 判定規則（結果を見る前に固定）
1. **主検定が非有意（p ≥ 0.05）→ 設計を棄却して終了**。RSI は σ̂ を超える増分情報を持たない、が結論。SPA も校正も実施しない。
2. **主検定が有意 → 対抗馬比較へ進む**。GPD-共変量 / 無条件経験分位 / 分位点回帰 を SPA（Hansen 2005）で比較し、GPD が経験分位を上回らなければ **POT は複雑性の純増として棄却**する。
3. 2 を通過した場合にのみ Kupiec / Christoffersen 校正を実施する。

### 実行結果（2026-08-01・事前登録どおり）

**主検定（1h・q=0.75・C=4,325・θ̂=0.888・ξ̂=+0.137）**
| 係数 | 推定値 |
|---|---|
| γ̂₀（切片） | −6.3194 |
| γ̂₂（log σ̂） | **+0.3130** |
| γ̂₁（RSI） | **−0.0299** |

- LR 統計量 = 2.9941、**p = 0.0836** → **α = 0.05 で棄却できず（非有意）**。
- **判定規則 1 を適用し、設計を棄却して終了する。SPA も Kupiec / Christoffersen 校正も実施しない。**
- 観測効果量 |γ̂₁| = 0.030 は事前登録した最小検出効果量 0.05 を**下回る**。よって本結果は「データが足りない」ではなく「**登録した水準の効果は無い**」と読むのが正しい。

**感度分析（Bonferroni・各 α = 0.025）**
| tf | C | γ̂₁ | β への効き | p | 判定 |
|---|---|---|---|---|---|
| 5m | 3,549 | −0.0772 | −7.43% | 0.000015 | 有意 |
| 4h | 4,827 | −0.1210 | −11.39% | < 1e−6 | 有意 |

- 符号は 3 本とも**負で一貫**（RSI が高いほど下落裾のスケールが小さい）。経済的には「直近の強さが続くほど下方テールが薄い」と読める向き。
- **ただし判定は変えない**。事前登録は 1h を主検定と定めており、感度分析が有意だからと結論を差し替えるのは「結果を見てから解釈を作る」ことそのものである。感度分析は主検定の頑健性を見る補助であって、主検定の代替ではない。
- なお γ̂₂（log σ̂）は 3 本とも **+0.27〜+0.40 で有意に正**＝ σ̂ は下落裾スケールの説明変数として機能している。RSI が σ̂ を**超える**増分情報を持つか、が本検定の問いであり、1h ではそれが示されなかった。

### この結果を受けて次に取りうる選択肢
1. **棄却を受け入れて終了**（事前登録どおり）。RSI-共変量 POT は採用しない。
2. **新規に事前登録し直して再検定**する。感度分析の結果を主検定へ格上げするなら、**同じデータで再検定してはならない**（p ハッキングになる）。4h を主検定とし、**本検定に使っていない期間（OOS）**を新たに確保して登録し直す必要がある。1h の 20,000 本窓は 2017-12 以降であり、それ以前の 4h/1D 期間は未使用のため OOS として確保可能。

## ISSUE-230: [運用事故] `unified_ui/serve.sh` が「既に起動済み」で起動しない（2026-08-01）
- **重大度**: Medium（ユーザーが起動できない。コードの欠陥ではなく運用手順の破壊）
- **ステータス**: RESOLVED
- **事象（ユーザー報告）**: `./unified_ui/serve.sh` で起動しない。
- **真因（コード回帰ではない・私の作業手順の誤り）**: ISSUE-198 の調査でルータの挙動を A/B するため、**serve.sh 管理下のルータを停止して `python3 router.py` を直接起動**し、そのまま復元しなかった。`serve.sh` は二重起動防止として 8000 の応答を見て「既に起動済みです」と表示し `exit 0` する。すなわち**スクリプトは設計どおりに動いていた**が、8000 を占有していたのが管理外プロセスだったため、ユーザーからは「起動しない」に見えた。
  - `unified_ui/serve.sh` は新規作成（`3a11ec8`）以降**一度も変更されていない**（`git log` 実測）。「再発」ではない。
- **併発していた構成の乱れ**: リプレイ core も私が `setsid bash serve.sh 8281` で直接起動しており、`unified_ui/serve.sh` の管理ツリー（親 serve.sh → 各 core serve.sh → watch → router）から外れていた。
- **復旧（2026-08-01）**: 管理外プロセスを明示 PID で全停止し、`./unified_ui/serve.sh` から正規起動し直した。
  - 復旧後の構成: `unified_ui/serve.sh` → `indicator_ui/serve.sh 8001` → watch 2 本（`export_jp225_m1 --watch` / `live_tick_watch --stream`）→ `router.py 8000`。8000/8001/8281 すべて 200。
  - **データの鮮度は劣化していない**: 復元前後とも最新足は `2026-07-31 20:14 UTC`。2026-08-01 は**土曜で市場休場**のため停止は正常（watch ログも `2026-08-01: 0 ticks` で整合）。
  - 実 UI 確認: チャート描画・指標 3 件・ライブ⇄リプレイ往復・SW 制御下・コンソールエラー 0。
- **⚠ 作業手順上の反省（再発防止）**: 調査のためにサーバを差し替えるときは、**調査終了時に必ず正規手順（serve.sh）へ戻す**。`python3 router.py` の直接起動は「生起動禁止」の趣旨（データ watch 併走が失われる）にも抵触する。今回は watch が別プロセスで生き残っていたため実害が出なかったが、偶然に依存していた。
- **`pgrep -f` の落とし穴（今回踏んだ）**: `pgrep -f "serve.sh 8001"` は**自分のシェルのコマンドライン**にも一致し、停止コマンド自身が kill されて失敗した（exit 144）。プロセス停止は明示 PID で行うこと。

## ISSUE-231: [不具合・実測再現] リプレイモードの時間足切替でローソクだけ先に描かれ、指標が遅れて追いつく（2026-08-01）
- **重大度**: High（リプレイの不変条件「その時点 T のローソクと指標が同時に現れる」の破れ。実測 359ms の中間状態が露出する）
- **ステータス**: RESOLVED
- **事象（ユーザー報告）**: リプレイモードで時間足を切り替えると、時間足（ローソク）が再現された後にインジケーターが再現される。同時に再現したい。
- **再現（実 UI・8000・リプレイモード・指標 3 件＝moving_averages ×2 / cvfe・5m→15m）**:

  | 経過 | 事象 |
  |---|---|
  | 0ms | `controller.setTimeframe('15m')`（ライブ経路が先着） |
  | 24–25ms | 指標系列を空化（`clearInstanceData` ×3） |
  | 30ms | `renderer.setCandles(1500)` ← **ローソクだけ 15m へ切替・指標は空** |
  | 389–391ms | 指標描画 ← **359ms 遅れて出現** |
  | 779–784ms | 同じ切替をリプレイ経路が**もう一度**全実行（二重実行） |

- **切り分け（同一計測）**: バー送り（`rp-next`）は `setCandles`→指標描画が 1–4ms の同一同期ブロックで完了＝**元から同時**。非同時なのは時間足切替のみ。
- **真因**: 時間足ボタンには共有ベース `IndicatorController.bind()` が張るライブ経路（`controller.setTimeframe`）が既に結線されているのに、`replay.js` も同じ `[data-timeframe]` へ独自リスナ（`setTimeout(..., 60)` → `loadTimeframe`）を追加していた。1 クリックで 2 経路が走り、
  1. 先着したライブ経路が ISSUE-196 の裁定どおり**ローソク先行**で差し替える（指標は空化され compute 完了後に描画）
  2. 約 750ms 後にリプレイ経路が同じ切替をやり直す（全再計算の二重実行）
  となっていた。ライブでは 1. は意図した仕様（切替がローソク描画の遅い指標に律速されない）だが、リプレイでは「リビール範囲のローソクと指標が同時に現れる」ことが不変条件であり、中間状態そのものが仕様違反である。
- **対策（恒久・応急処置なし）**: 時間足切替の**反映役**（candles 取得 → メイン系列差替え → 全指標再計算）だけを差し替え可能にする seam を共有ベースへ追加し、リプレイ層がそこへ自身の `loadTimeframe` を登録して**単一経路**へ一本化した。
  - `timeframe_controller.js`: `setApplier(fn)` を追加。`setTimeframe` は反映役があればライブ反映（`_applyLive`）を行わず委譲する。時間足の確定（`_timeframe` 更新・ボタン active 同期・スケールリセット・`uiState` 永続化・購読者通知）と競合ガードは**差し替えても共通のまま**。
  - `indicator_controller.js`: 薄い委譲 `setTimeframeApplier(applier)` を追加（既存の `setTimeframeObserver` / `setAppliedObserver` と同型の購読スロット。monkey-patch は用いない＝ISSUE-037 の規律を維持）。
  - `replay.js`: `[data-timeframe]` への独自リスナを撤去し、反映役として `loadTimeframe` を登録。`disable()` で解除・`enable()` で再登録（ライブは未登録＝従来経路のまま **byte 挙動不変**）。
  - リプレイ経路は `render()` の `recomputeAllApplied({ preRender })` により、ローソク差替えと全指標描画が **await を挟まない同一同期ブロック**で行われる（ISSUE-023 / ISSUE-048 の不変条件をそのまま利用）。
- **検証（実 UI・同一手順・修正後）**:

  | 指標 | 修正前 | 修正後 |
  |---|---|---|
  | ローソク → 指標の間隔 | **359ms** | **13ms**（同一同期ブロック） |
  | 切替 1 回あたりの `setCandles` | 2 回（二重実行） | **1 回** |
  | 最初の paint（rAF）までに描画済みの指標数 | 0 / 3 | **3 / 3**（＝同一フレーム＝視覚上も同時） |

  再生（`rp-play`）でのバー送り 6 本も回帰なし（各バーで指標同時描画・最終フレームも 3/3）。
- **単体テスト**: `timeframe_controller.test.js` に反映役の 4 件（委譲時にライブ反映を行わない／確定・永続化・通知は共通／`null` で既定復帰／例外時もゲート解放）、`replay_timeframe_applier.test.js` を新規追加（独自リスナ不在・反映役登録・同一バッチ描画・disable/enable の解除/再登録）。既存テストは全通過（indicator_ui 957 / replay_ui 271 / unified_ui 42）。

## ISSUE-232: [不具合・再現済み] リプレイ再生中、ローソクはティック毎に動くのに指標が約 100ms 遅れて追いつく（2026-08-01）
- **重大度**: High（「足の再現に指標が同期しない」＝リプレイの目的である過程の可視化が成立しない）
- **ステータス**: RESOLVED
- **事象（ユーザー報告）**: 時間足の再現後にインジケーターが再現される。同時に再現したい。
  - 当初 ISSUE-231（時間足切替）と解釈して着手したが、**ユーザーが見ていたのは再生中の足内更新**だった。切替の二重経路も実在の不具合だったため ISSUE-231 として別途修正済み。
- **再現（実 UI・8000・リプレイ再生中・5m・ohlc_1min・指標 moving_averages ×2）**:

  | 対象 | 実測 |
  |---|---|
  | ローソクの更新間隔 | 7–9ms（ティック毎・同期） |
  | 指標の追従遅れ | **95–142ms**（平均 117ms） |
  | 指標の更新回数 | 15 バーで **44 回**（≒3 回/バー・throttle と in-flight スキップのため） |

- **真因**: 足内の指標値を**毎ティック `/compute` へ往復**して求めていた（`pushFormingMA` → `recomputeFormingLatest`）。往復は 1 回あたり実測 52ms（HTTP）で、ローソクのティック描画と同一同期ブロックに入れられない。さらに `FORMING_MIN_INTERVAL_MS` の throttle と in-flight スキップで更新が間引かれ、「足だけ先に動く」状態になっていた。
  - サーバ内訳の実測: `load_source` **242ms**（リクエスト毎）／`latest` 計算 **6.6ms**（moving_averages）。すなわち**往復の大半が窓の読み直しで、指標計算そのものではなかった**。
- **対策（恒久）**: 足内推移の各時点の指標値を**バー開始前に 1 リクエストで一括計算**し、描画時は同期反映するだけにする（ISSUE-158 の一括リビールと同型の構造をバー内へ適用）。
  - バックエンド: `/compute` に `mode='latest_seq'` を追加（`formingSeq` を受け `steps` を返す）。窓のロード・truncate・tail を 1 回に畳み、以降は forming の差し替えのみ。既存 `full`/`latest` の分岐は不変。
  - フロント（リプレイ専用）: 純ロジック `forming_plan.js`（サンプリング・形成中 OHLC・陳腐化署名）、専用クライアント `forming_seq_client.js`、`ReplayIndicatorController.formingSeqTargets/applyFormingStep`、`replay.js` の先読み駆動。
  - **速度の不変条件**: 計画は**決して await しない**。使用時点で未完なら即座に従来経路へ落ちる（計算失敗・未対応 controller も同様＝fail-open）。よって計画待ちで再生が遅くなることは構造的に起こらない。
  - 先読みは「現在バーの再生中に次バーぶん」を発行する（`render` 時点でも現在バーぶんを発行）。**リプレイ層が起きている時のみ**発火させる（ライブ表示中に発火して `/intraday` が 404 になる副作用を実測で発見し是正）。
  - 計画は末尾ティックを必ず含むため、バー確定の着地往復（従来 ~100ms/バー）を発行しない。
  - 陳腐化（指標の追加削除・params/variant 変更・時間足/モード変更）は署名照合と明示破棄で遮断し、古い値では描かない。
- **同値性ゲート（実データ・値が変わらないことの実証）**: 一括計算の各ステップと単発 `latest` の結果が **`moving_averages` / `ma_marod` / `btlm_trail` の 3 指標で完全一致**（JSON byte 一致）。
- **検証（実 UI・修正後）**:

  | 指標 | 修正前 | 修正後 |
  |---|---|---|
  | ローソク→指標の時間差 | 117ms（平均） | **0.41ms（平均）/ 0.8ms（最大）** |
  | 指標更新回数 | 44 回 / 15 バー | **260 回 / 13 バー**（全ティック） |
  | 遅延経路（その場計算）の発火 | 常時 | **0 回** |
  | 指標がローソクと対で反映された割合 | — | **100%**（全件が直前のローソク更新と同一同期ブロック） |

- **速度（厳密 A/B・同一バー区間 1470 起点・10 本・各 2 回）**:

  | | 10 本の実時間 | 1 バーあたり |
  |---|---|---|
  | 一括計算あり | 5112ms / 5047ms | **511ms / 505ms** |
  | 従来経路 | 6471ms / 6804ms | 647ms / 680ms |

  **約 25% 高速化**（毎ティックの往復とバー確定の往復が消えたため）。「遅くしない」要件は満たしている。
- **影響範囲**: 変更はリプレイ専用 6 ファイルのみ（`causal_compute.py` / `serve_replay.py` / `replay_indicator_controller.js` / `replay.js` / 新規 2 モジュール）。**共有モジュール・ライブ側のファイルは 1 つも変更していない**（`compute_http_client.js` は symlink 共有のため使わず専用クライアントを新設）。
- **既知の残件（本 Issue の対象外）**: `cvfe` は足内追従の登録リスト `INTRABAR_FORMING_IDS` に無いため、従来どおりバー確定時のみ更新される（ISSUE-145 の登録規約による既存仕様）。足内でも動かすかは別途判断が必要。
- **テスト**: Python 新規 5 件（窓ロード 1 回・単発同値・窓一致・空入力）、JS 新規 15 件（サンプリング／形成中 OHLC／署名 8 件、駆動配線 7 件＝同期反映・待たない・fail-open・後方互換・先読み・settle 省略・ライブ非発火）。既存は全通過（JS 286 + 957 / Python 192）。

---

## ISSUE-233: [不具合・実測再現] リプレイ再生が耐え難く遅い — 足内一括先読み（ISSUE-232）が 1 バーあたり 83 秒を要し確定足計算を待たせる（2026-08-01）

- **重大度**: Critical（実用不能。再生が主機能であり、その主機能が高速化目的の変更によって劣化している）
- **ステータス**: RESOLVED（2026-08-01・feature/latest-incremental-compute・S1〜S5 完了＋実 UI 通過条件を達成）
- **報告**: ユーザー（2026-08-01）「とにかく再生が遅すぎる。耐え難いほど遅すぎる」「更新粒度が低く結果のみが表示される」
- **再現条件**（`simulator/replay_ui/tools/replay_diag.js` による実 UI 吸い出し）:
  - 時間足 `1h` / 再生モード `ohlc_1min` / 速度 `x1.00`（最速）/ 表示期間 1週 / 計算窓 `limit=1386`・`untilTime=1785103200`
  - 適用指標 7 件: `ma_marod` / `moving_averages`×3 / `btlm_trail`(`band_method=empirical`,`maxbars=115`,`empirical_n=495`,`n_cov=495`) / `btlm_trail_marod`(`maxbars=266`,`window_n=500`) / `market_profile`
  - 足内のティック点数 196・**指標更新回数 0**（`window.__rpForm` の `planned=0`）

### 実測（同一設定・実 HTTP 経路・`127.0.0.1:8281`）

| 指標 | 足内一括計算 `latest_seq`（32 ステップ） | 確定足 `full`（毎バー 1 回） |
|---|---|---|
| `ma_marod` | 28.54s | 0.21s |
| `moving_averages`（hlc3 / high / low） | 1.23s / 0.27s / 3.37s | 0.05s ×3 |
| `btlm_trail` | **41.71s** | 0.39s |
| `btlm_trail_marod` | 7.99s | 0.25s |
| **合計** | **83.10s** | **1.00s** |

- 1 バーの再生所要は `196 点 × PER_POINT_MS(6ms) ≒ 1.2s`。**先読みに必要な 83s との比は約 70 倍**であり、計画は原理的に間に合わない。
- 完了予想の実測値も一致: 残り 114 足で 2 分 26 秒 ＝ 1.28s/足（＝アニメ点数律速）。別時点では残り 105 足で 19 分 45 秒 ＝ 11.3s/足（先読みキューが確定足計算を待たせている状態）。

### 真因（3 点）

1. **`latest` 1 ステップが `full` 1 回より高コスト**。`btlm_trail` は 1 ステップ 1.30s に対し full 0.39s。ISSUE-232 の設計前提「`load_source` 242ms に対し latest 計算は 6.6ms」は、軽量指標・小窓での実測であり、本件の設定（経験分位バンド・`n_cov=495`・`window_n=495/500`・窓 1386 本）では成立しない。窓ロードを 1 回へ畳んでも、32 回の latest 計算そのものが支配的になる。
2. **`btlm_trail` は原理的に計画へ載らない**。41.71s は `forming_seq_client.js` の `SEQ_TIMEOUT_MS = 30000` を超えるため必ず abort → `.catch(() => null)` → `steps` が空 → `planned=0`。ゆえに「足内で指標が一切動かず、確定時に結果だけが現れる」。
3. **先読みが確定足計算を待たせている**。`serve_replay.py` の `self._lock` と単一 `compute-worker` により `/compute` は直列化される。fire-and-forget の先読み 83s ぶんがキューを占有し、本来 1.00s の毎バー full 計算がその後ろに並ぶ。「計画は決して await しない」という速度の不変条件は**フロントの制御フローについてのみ成立**しており、**バックエンドのキューについては成立していない**（ISSUE-232 の設計の穴）。

### 影響

- ISSUE-232 の一括先読みは、本設定において高速化ではなく**純粋な負荷追加**になっている。
- 症状「更新粒度が低く結果のみが表示される」は真因 2 の直接の帰結。
- 別途報告された「結果表示のバグ（線が垂直に落ちる）」は、実 UI の描画データ吸い出し（末尾 3 点・最大跳躍）では再現しなかった。`btlm_trail_mean` は 65505.68 → 65444.96 → 65420.25（最大跳躍 82.39）と連続。**本 Issue とは分離して別途調査する**（未確定）。

### 対策案（未承認・要判断）

- **案 A（即時・回帰の切り戻し相当）**: 足内一括先読みを既定で無効化する。毎バーの計算は full 1.00s のみとなり、足内の指標追従は ISSUE-232 以前の経路（120ms スロットル）へ戻る。ISSUE-232 が解こうとした「指標が約 100ms 遅れる」は再発するが、83s の負荷は消える。
- **案 B（恒久）**: 先読みを**実測コストに基づく予算制**にする。(1) 指標ごとに 1 ステップの所要を実測して保持し、(2) 「1 バーの再生所要 × 安全率」を超える計画は発行しない、(3) `MAX_FORMING_STEPS` を固定 32 ではなく予算から逆算する（重い指標は 2〜4 ステップ、軽い指標は 32）。
- **案 C（バックエンド）**: 先読みを確定足計算より**低優先度**のキューへ回し、確定足計算が先読みを追い越せるようにする（現在は単一 FIFO で追い越し不可）。
- 案 A は即時の実用性回復、案 B・C は恒久解。A → B/C の順を推奨するが、**いずれも挙動変更のため未実施**。

### 実測の訂正（2026-08-01）— 「足内更新は動いていない」は誤り

当初「指標の足内更新は 0 回＝機能していない」と報告したが、**実測で誤りと判明した**。実 UI（1h / ohlc_1min / x1.00）で本番メソッド（`applyFormingStep` と `recomputeFormingLatest`）の呼び出し回数を直接数えた結果:

| 構成 | 指標更新/足 | ローソク更新/足 | 秒/足 |
|---|---|---|---|
| `moving_averages` のみ | **27.2 回** | 202 | 1.40 |
| ＋ `btlm_trail`（経験分位・`n_cov=495`） | **4.0 回** | 201 | 1.45 |
| ＋ `ma_marod`（3 指標） | **0 回** | 201 | 1.49 |

足内更新は**壊れていない**。指標を重くすると 27 → 4 → 0 と静かに落ちる。したがって ISSUE-145（2026-07-20 RESOLVED）の対策は**当時の構成では実際に機能していた**。「解決していなかった」という本 Issue 当初の記述は撤回する。正しくは「**解決していたが、指標の重さに応じて黙って劣化する**」。

### 応急処置の撤回（2026-08-01・ユーザー厳命）

一度実装した**予算制（実測コストからステップ数を逆算し、超過時は発行しない）を全面撤回した**（`replay.js` / `forming_plan.js` / `forming_seq_client.js` / 追加テストを revert）。

撤回理由（ユーザー厳命「今後は抜本的解決方法のみ提示しろ。応急処置は絶対に提示するな」）:

1. 予算制は根本原因（`latest_compute` が末尾 1 点のために窓全体を再計算する設計）に一切触れていない。速くなったのは**仕事を減らしたから**である。
2. さらに悪く、予算制は**劣化を自動化・不可視化する**。指標を足すたびに更新粒度が黙って落ち、原因が見えなくなる。ISSUE-145 以来この症状が繰り返し再発している構造そのものを強化していた。

撤回により重い構成の再生は 2.27 秒/足へ戻る。

### 真因（唯一・確定）

`indigators/indicator_ui/api/adapter/compute/latest_dispatch.py:57`

```python
sub = df if meta.min_window is None else df.tail(meta.min_window)
series = adapter.compute(compute_id, variant, sub, params)
```

`latest` は増分計算ではなく、**末尾 1 点（`trailing_k=1`）のために窓全体を計算し直して最後の 1 点だけ切り出す**実装である。`moving_averages` は `ma_type` によらず `min_window=None`（＝tail せず全件・`call_binding.py:138-144`）。実測の 1 ステップ所要は `btlm_trail` 334ms / `ma_marod` 159ms / `moving_averages` 8〜105ms で、固定費（窓ロード）は 0.05s に過ぎない。

この設計である限り、更新粒度は「1 足の長さ ÷ 1 往復の所要」で上限が決まり、指標を重くすれば必ず落ちる。ISSUE-145・ISSUE-232 のいずれもこの点に触れていない。

### 抜本的解決（設計待ち）

**`latest` を真の増分計算にする**（前回の状態を保持し 1 点だけ進める）。1 回 159〜334ms が数ミリ秒になり、指標の重さに関係なくローソクと同じ粒度（1分OHLC で 201 回/足）が成立する。

| 対象 | 保持する状態 | 難度 |
|---|---|---|
| `ema` / `smma` | 前回値 1 個 | 低 |
| `sma` / `lwma` | 環状バッファ（`length` 本） | 低 |
| 経験分位バンド（`n_cov` / `window_n` / `empirical_n`） | 順序統計構造（挿入・削除・分位取得） | 中 |
| イベント分位（`k_events`・エピソード declustering） | エピソード状態機械 | 高 |

検証方式は ISSUE-158 で確立済みのものを流用する（現行 `full` を参照実装とし、全系列で `max_dev = 0` を固定）。各段階の通過条件は「`full` との全系列 `max_dev = 0`」かつ「1 ステップ所要 < 5ms」。

### 副産物（撤回せず残置）

- 診断ツール `simulator/replay_ui/tools/replay_diag.js`。実 UI の設定・描画値・足内粒度を 1 回で吸い出す（読み取りのみ・副作用なし）。本 Issue の実測はすべてこれで取得した。

### 抜本的解決の実装記録（feature/latest-incremental-compute）

内部設計 `.doc/indicator-management-ui/内部設計_latest増分計算.md` §8 を全承認（2026-08-01）のうえ実装。

#### S1: moving_averages（4 種）＝完了

- **真因の除去**: `latest` を「窓全体を再計算して末尾 1 点を切り出す」から「確定バーまでの MA バッファを状態として保持し、形成中バー 1 本ぶんだけ漸化を進める」へ置換した。所要は窓長に依らず一定になる。
- **src への追加（ユーザー承認 2026-08-01）**: `linear_weighted_ma_on_buffer` は `prev_calculated>0` 分岐で走行和 `total`/`lsum` を窓から再構築するため、full の漸化を継続できず末尾値が bit 一致しない（実測 max_dev 2.1e-09 @ n=1400）。`buffer[i]=total_i/weight` は丸め済みで `total_i` を復元できないため、src 外からの継続は原理的に不可能。走行和を授受する `linear_weighted_ma_on_buffer_stateful` / `LwmaState` を `moving_averages/src/core.py` へ追加し、**既存 `linear_weighted_ma_on_buffer` はその共有部品（`_lwma_seed` / `_lwma_advance`）へ委譲**させた（漸化式の定義は 1 箇所のみ・二重定義を作らない）。既存関数の出力は凍結オラクルとの bit 一致テストで恒久固定。
- **実測（窓 1386 本 / length=24 / 1 ステップ）**:

| ma_type | full | latest（増分・定常） | 倍率 |
|---|---|---|---|
| sma | 3.59ms | **0.140ms** | 25.6x |
| ema | 3.38ms | **0.137ms** | 24.7x |
| smma | 3.38ms | **0.144ms** | 23.5x |
| lwma | 3.58ms | **0.132ms** | 27.1x |

  窓 5000 本では full 11.7〜12.8ms に対し latest 0.157〜0.173ms（70〜81x）。**latest は窓長に依らずほぼ一定**であり、通過条件「1 ステップ < 5ms」を満たす。
- **一致検証**: 4 種 × length{2,9,24,50} × offset{-3,-1,0,1,3} × source 8 択 × wait_for_close × min_tail{2,5,30} で `full` と **完全一致（max_dev = 0）**。足内更新の非破壊性（同一確定状態から形成中バー 10 通り）・バー確定の前進（窓を 1 本ずつ伸長）・窓の縮小/再伸長・左端シフトでも一致を確認。
- **回帰**: indicator_ui Python 554 / replay_ui Python 192 / moving_averages 167 / replay_ui JS 286 / indicator_ui JS 957 いずれも全通過。
- **未対応（従来経路のまま・挙動不変）**: `smoothing_type != none`（平滑化は MA 系列に対する pandas rolling/ewm であり、末尾だけを bit 一致で求める手段が src の公開面に無い）。本数が `length+3` 未満の warm-up 直後。

#### S2/S3/S4: btlm_trail（窓末尾 OLS・経験分位バンド・被覆率）＝完了

- **真因の除去**: 末尾 1 点しか要らない latest 経路が、各バーで同じ計算を繰り返すローリング全体を走っていた。src に「1 バーぶんの計算」の公開入口を置き、**ローリング版がその入口を各バーで呼ぶ構成**へ変えた（定義は 1 箇所のまま）。増分器は確定バーまでの各系列を状態として保持し、形成中バー 1 本ぶんだけを 1 バー入口で計算する。
- **src への追加（B-2 承認の範囲・計算式は不変）**:
  - `core.window_end_scalar`（非公開 `_window_end_scalar` を公開化。旧名は同一オブジェクトの別名として残置）
  - `trail.empirical_quantile_latest` / `trail.coverage_latest` / `trail.deviation_ratio` / `trail.ols_band` / `trail.empirical_band`（いずれも 1 バー／1 窓ぶんの唯一の定義。`_empirical_quantile_causal` と `rolling_coverage` と `build_btlm_trail` がこれらを呼ぶ）
  - `TrailResult.deviations`（乖離率。増分器が次バーの経験分位を求めるために要る・既定 None で後方互換）
- **実測（窓 1386 本・1 ステップ）**:

| 構成 | full | latest（増分・定常） | 倍率 |
|---|---|---|---|
| 実測構成（empirical・maxbars=115・empirical_n=495・n_cov=495） | 143.1ms | **0.356ms** | 402x |
| ols 既定（maxbars=100・q_out=0.99・n_cov=250） | 44.0ms | **0.368ms** | 120x |

  内訳の改善: 窓末尾 OLS 28.9ms→0.021ms／経験分位 1 本 51.4ms→0.037ms／被覆率 5.0ms→O(n_cov) の numpy 和。
- **一致検証**: band_method{ols,empirical} × maxbars{50,100,115} × q_out{無効/有効} × source 8 択 × show_metrics × min_tail{2,5,30} で `full` と **完全一致（max_dev = 0）**。対象系列は mean / q5 / q95 / off_hi / off_lo / beta / sigma / band_hit_rate の全 8 系列。足内更新の非破壊性・バー確定の前進・窓の縮小/再伸長・左端シフトも一致。
- **設計との差分**: S3 は内部設計が「順序統計構造（挿入・削除・分位取得）で O(log n)」としていたが、**当該バーを除く因果境界**（分位は確定済みの乖離率のみを使う）により、足内更新では分位窓が動かない。確定バーの前進時に末尾 emp_n 本を `np.quantile` へ 1 回渡すだけで足り（実測 0.037ms）、順序統計構造は不要だった。構造を持たないぶん、参照実装と同じ配列を同じ関数へ渡す＝bit 一致が構造的に保証される。
- **回帰**: indicator_ui Python 592 / replay_ui Python 192 / btlm_trail 31 / moving_averages 167 / ma_marod 43 / btlm_trail_marod 30 / common 81 全通過。

#### S5: ma_marod / btlm_trail_marod（因果分位バンド・イベント分位）＝完了

B-6（S1〜S4 後に再測定して着手可否を判断）に従い再測定した結果、`ma_marod` 117.8ms / `btlm_trail_marod` 152.4ms が残り、この 2 つを残すと 7 指標構成の更新粒度は達成不能と確定したため着手した。

- **真因の除去**: 分位バンドもイベント分位も **当該バーを除く** 因果統計（ISSUE-141 の規約）であり、形成中バーの水準は確定済みの観測だけで決まる。足内更新のたびに窓全体を走り直す必要はない。確定バーまでの値系列とイベント観測列を状態に保持し、形成中バーは 1 バー入口で 1 点だけ求める。
- **両指標は構造が同一**（値系列＝乖離率 → 因果ローリング分位バンド → 外れ値イベント分位水準）で、差は基準線だけ（移動平均 / OLS 窓末尾トレンド）。増分器は 1 実装を共有し、基準線のみ差し替える。
- **共有プリミティブへの追加（計算式は不変・既存経路が委譲）**:
  - `common/marod_bands.py`: `causal_stat_latest`（1 バーぶんの因果統計・`rolling_causal` が委譲）／`stat_reducer`／`marod_percent`（乖離率の唯一の定義。ma_marod・btlm_trail_marod の両 core が委譲＝既存の二重定義も解消）
  - `common/event_quantiles.py`: `step_events`（1 バーぶんのイベント検出・エピソード確定）／`levels_at`（観測 m 件時点の水準）／`event_levels_latest`（次バーの水準 4 値）。本体ループがこれらを呼ぶ構成へ変更
- **実測（窓 1386 本・1 ステップ）**:

| 指標 | full | latest（増分・定常） | 倍率 |
|---|---|---|---|
| `ma_marod`（ema・length=50・window_n=495） | 117.8ms | **0.356ms** | 331x |
| `btlm_trail_marod`（maxbars=266・window_n=500） | 152.4ms | **0.427ms** | 357x |

- **一致検証**: 基準線種別（ma_marod は sma/ema/smma/lwma 全種）× window_n × q_out{無効/有効} × k_events × event_agg{episode,bar} × source × min_tail で `full` と **完全一致（max_dev = 0）**。対象は本体・0% 基準線（horizontal_line）・分位バンド 2 本・イベント分位水準線 4 本の全系列。エピソード declustering の状態がバー確定で参照実装と同一に進むことも、窓を 1 本ずつ伸ばす検証で固定した。

### 実測構成（7 指標）の 1 ステップ合計

| 指標 | 対応前 | 対応後 |
|---|---|---|
| `moving_averages` × 3 | 約 10〜24ms | 0.42ms |
| `btlm_trail`（empirical） | 143.1ms | 0.356ms |
| `ma_marod` | 117.8ms | 0.356ms |
| `btlm_trail_marod` | 152.4ms | 0.427ms |
| **合計** | **約 425ms** | **約 1.56ms** |

1 足（1h・1分OHLC・201 点）は 1.21 秒であり、更新粒度の上限は 1.21s ÷ 1.56ms ≈ 775 回/足。**要求（ローソクと同じ 201 回/足）に対し 3.8 倍の余裕**がある。指標を足しても粒度が落ちない構造になった。

- **回帰**: indicator_ui Python 639 / replay_ui Python 192 / btlm_trail 31 / moving_averages 167 / ma_marod 43 / btlm_trail_marod 30 / common 81 / replay_ui JS 286 / indicator_ui JS 957 全通過。

### 実 UI 検証（§6.2）と、そこで判明した 2 つの真因

バックエンドの増分化（S1〜S5）だけでは実 UI の粒度は 0 のままだった。実 UI 実測（serve.sh・ポート 8280・jp225_tick・1h・`ohlc_1min`・x1.00・指標 5 件＝`btlm_trail`×3 / `btlm_trail_marod` / `ma_marod`）で、残る 2 つの真因を特定して除去した。

#### 真因 A: 足内 1 ステップごとに窓を DataFrame へ組み直していた

`causal_compute_seq` は時点ごとに `apply_forming(bars, forming)` で窓全体（1492 本）を plain dict へコピーし、ゲートウェイがそのたびに DataFrame を組み直していた。実測の内訳（実 HTTP・formingSeq 点数を変えて回帰）:

| formingSeq 点数 | 対応前 | 対応後 |
|---|---|---|
| 1 点（固定費） | 38ms | 47ms |
| 50 点 | 143ms | 69ms |
| 201 点 | 458ms | 158ms |
| 400 点 | 902ms | 269ms |
| **1 ステップの限界費用** | **2.1ms** | **0.56ms** |

指標計算そのもの（0.36ms）より変換費用（1.7ms）の方が大きい状態だった。`apply` は末尾しか触らないため（同値性は `test_forming_bar.py` の分割テストで固定）、確定プレフィクスと時点ごとの末尾差分に分けて渡し、**窓の変換を 1 回に畳んだ**（`CausalComputePort.compute_latest_seq`）。値は単発 `latest` と bit 同値（ゲートウェイの一致テストで固定）。

#### 真因 B: 足内一括計算が 1 度も成立していなかった（フロントの呼出バグ）

`FormingSeqClient` が注入された fetch を `this._fetch(...)` とレシーバ付きで呼んでいた。`replay.js` の既定値は束縛していない素の `fetch` のため、ブラウザでは **必ず** `Failed to execute 'fetch' on 'Window': Illegal invocation` になる。呼び出し側は失敗を握り潰して従来経路へ落とす（`.catch(() => null)`）ため、**ISSUE-232 の足内一括計算は実 UI で 1 度も成立していなかった**。本 Issue の「指標更新回数 0」はこれが直接原因である（計算速度の問題ではなかった）。関数参照として呼ぶよう是正し、束縛の有無に依らず動くことをテストで固定した（`tests/forming_seq_client.test.js`）。

#### サンプリング上限（`MAX_FORMING_STEPS = 32`）の廃止（ユーザー承認 2026-08-01）

「1 ステップ 6.6ms〜170ms なので全ティック計算は現実的でない」という前提で置かれていた上限を撤去し、**指標の更新回数は常にローソクの更新回数と一致する**ようにした（点間でローソクだけが動く区間を作らない）。上限を残す／値を上げるのは §7 が却下する応急処置である。`real_ticks`（月足で数十万ティック）も間引かない方針をユーザーが選択（全モードで廃止）。

#### 通過条件の達成（実 UI・連続 6 足）

| バー | ローソク更新回数 | 指標更新回数 |
|---|---|---|
| 1491〜1495 | 201 | **201** |
| 1496 | 202 | **202** |

指標 5 件適用のまま **指標更新回数 == ローソク更新回数** を満たした（対応前は 0）。描画も正常（`btlm_trail` の帯 3 本・MAROD 系 2 pane・読取欄の値すべて表示）。

### 最終回帰

indicator_ui Python 639 / replay_ui Python 202 / btlm_trail 31 / moving_averages 167 / ma_marod 43 / btlm_trail_marod 30 / common 81 / indicator_ui JS 957 / replay_ui JS 290 — 全通過。


## ISSUE-234: [不具合・実測再現] リプレイ core で `/tickvol_profile` が 500（DatasetPort 未結線）（2026-08-01）

- **重大度**: High（新機能の帯定義がリプレイ側でまったく取得できない）
- **ステータス**: RESOLVED（2026-08-01・feature/latest-incremental-compute）
- **事象**: 8280 で `GET /tickvol_profile?datasetRef=jp225_tick` が 500 `internal`。本文は
  「DatasetPort が未結線です。エントリポイントで adapter.gateway.composition.install_default_ports() を…」。
- **真因**: `usecase/dataset_port.py` の既定 factory 登録は「各エントリポイントの責務」で、本番は
  `framework/server.py`、テストは `api/tests/conftest.py` が 1 回呼ぶ規約。リプレイプロセスは
  ライブ側 controller を bridge で再利用する**第 3 のエントリポイント**であり、どちらの登録経路も通らない。
- **対策（根本）**: `simulator/replay_ui/adapter/_indicator_ui_bridge.py::load_tickvol_handler` が
  handler の import 前に `install_default_ports()` を呼ぶ（冪等）。規約どおり「エントリポイントが登録する」に揃えた。
- **検証**: 8280・8281・8001 の 3 者で同一入力の応答が md5 一致（`5d5db42d…`）。未知 ref は 400 `validation`。

## ISSUE-235: [不具合・実測再現] 統合 UI で `/tickvol_profile` が 404（Service Worker のリライト表に未登録）（2026-08-01）

- **重大度**: High（統合 UI では帯が一切出ない。単体起動（8280）では出るため気付きにくい）
- **ステータス**: RESOLVED（2026-08-01・feature/latest-incremental-compute）
- **事象**: 8000（統合）でライブモード時 `GET /tickvol_profile?…` → 404。console に
  `Failed to load resource: 404 @ http://127.0.0.1:8000/tickvol_profile?…`。
- **真因**: root 相対 API fetch は SW（`unified_ui/web/js/sw_rewrite.js`）が `/live|/replay` を前置する設計で、
  対象は `API_SEGMENTS` 表の列挙のみ。新エンドポイントを表へ登録していなかった（静的資産扱い＝素通し→404）。
- **対策（根本）**: `API_SEGMENTS` に `tickvol_profile` を追加（表駆動の拡張点をそのまま使う）。両 core が同一実装を
  持ち応答が byte 一致するため `LIVE_ONLY_SEGMENTS` には**入れず**アクティブモードの core へ回す。
- **検証**: 8000 のライブモードで 200・帯描画あり（帯色ピクセル 119）。リプレイモードでは `until=` 付きで
  `/replay` 側へ振り分く。`unified_ui/web/tests/sw_rewrite.test.js` に両モードの回帰を追加（43 件緑）。

## ISSUE-236: [設計欠陥・実測再現] 指標ペインへ背景プリミティブを装着すると一部ペインだけ塗られない（2026-08-01）

- **重大度**: Medium（見た目の欠落。再現条件が「指標の再計算後」で分かりにくい）
- **ステータス**: RESOLVED（2026-08-01・feature/latest-incremental-compute）
- **事象**: 取引密度帯を全ペインへ装着する実装で、実 UI 実測の内部状態が `ranges=[68, 0, 0]`
  （メインペインのみ塗られ、指標ペイン 2 枚が空）。
- **真因**: lwc のプリミティブは**系列**にしか装着できず、指標ペイン内の系列は指標の再計算で作り直される。
  作り直しのたびにプリミティブが外れるため「検知して張り直す」同期が必要になるが、検知契機
  （`setAppliedObserver` / candle observer）は再計算完了と順序が保証されず、取りこぼしが残る。
- **対策（根本）**: 同期そのものを不要にする。**作り直されないメイン系列（価格パネル）へ 1 度だけ装着**する
  設計へ変更し（`ChartRenderer.attachBackgroundPrimitive`）、ペイン同期 API を廃した。依頼者の要求
  「チャートパネルの背景色を変えたい」とも一致する。
- **検証**: 実 UI で 時間足往復・期間プリセット往復・再読込（復元）のいずれでも塗りが追随
  （`onLoad=68` / `4h=0` / `5m=21` / `1日プリセット=17`）。

## ISSUE-237: [不具合・実測再現] 足の差し替えで取引密度帯が古い足のまま残る（2026-08-01）

- **重大度**: Medium
- **ステータス**: RESOLVED（2026-08-01・feature/latest-incremental-compute）
- **事象**: 期間プリセット変更（足の全置換）後、帯が新しい足へ追随せず塗りが消えた。
- **真因**: アクターの再描画契機を時間足変更とリプレイ時計にしか繋いでいなかった。足の差し替え
  （期間プリセット・カレンダー・リビール）は `ChartRenderer` の candle observer で通知されるが未購読だった。
- **対策**: 両 composition root の `setCandleObserver` 既存コールバックへ `onCandlesChanged()` を合成
  （購読スロットは単数のため既存購読者と同一コールバック内で呼ぶ）。指標の増減は `setAppliedObserver` を
  合成して拾う（統合 UI で後から `replay.js` が上書きしても本フックが消えない形）。
- **検証**: 実 UI で期間プリセット往復後も帯が追随（17 本）。300 バー送りで再取得は 3 回のみ
  （＝またいだセッション日数と一致・バー毎の再取得なし）。

## ISSUE-238: [不具合・実測再現] リプレイの足内で tick 数が更新されず、足の先頭から確定値を表示する（2026-08-01）

- **重大度**: High（未来先取り＝非リペイント原則違反。volume を読む全指標に及ぶ）
- **ステータス**: RESOLVED（2026-08-01・feature/latest-incremental-compute）
- **⚠ 訂正（2026-08-01）**: 起票時に真因を「形成中バーの volume が NaN になり点が立たない」と
  記載したが、**実測で誤りと判明した**。実際は NaN にならず、**確定足の完成値がそのまま残る**。
  症状は「点が立たない」ではなく「足の先頭から完成後の値が出て、足内で 1 度も動かない」。
- **事象（実測 2026-08-01・8000 リプレイ・NI225 5 分足・40 サンプル）**:
  | モード | 同一足内のローソク close 更新回数 | tickvol 更新回数 |
  |---|---|---|
  | 1分OHLC（既定） | 9 | **0** |
  | 実ティック | 25 | **0** |
  さらに足の**最初のフレームから確定値**が出ている（例: 足 1785527400 の先頭フレームで既に 627＝
  その足の完成 tick 数。1785527700 も先頭から 722）。まだ到来していないティックを数えた値である。
- **真因（コード上で確定）**: リプレイの形成中バーはフロントが作る
  （`web/js/replay/forming_plan.js` の `formingStatesAt`）が、その payload は
  `{time, open, high, low, close}` **のみで volume を持たない**。適用側
  `simulator/replay_ui/domain/forming_bar.apply` は仕様どおり「forming に**存在するキーのみ**更新」
  するため、**volume は確定足の完成値が保持される**。よって足内のどの時点でも tick 数は完成値。
  サーバ応答でも確認済み（`mode=latest_seq` の 2 時点とも `tickvol=297.0`＝当該足の完成値）。
- **影響範囲**: tickvol に限らない。`volume` を読む指標（`profit_mfi` 等）はリプレイの足内で
  同じ未来先取りを起こす。tickvol は volume を直接描くため初めて可視化されただけである。
- **対策（根本・実施済・依頼者承認 2026-08-01）**: 形成中バーに「足始端から**リプレイ現在時刻**までの実 tick 数」を
  持たせる。定義はライブの参照実装と同一（`adapter/compute/forming_bar` の
  `volume = len(mids)`＝窓 `[足始端, now)` の実 tick 数）。リプレイの `now` は `to`
  （ISSUE-129 で確定した単一時計）で、各足内時点の `to` は既に `secs`（tick_secs）として存在する
  （real_ticks＝実 tick 時刻／合成モード＝窓等分。MP tick-live が既に採用しユーザー裁定済み）。
  算出はサーバ側の実 tick データから行う（フロントで点数を数える方式は real_ticks 以外で
  実 tick 数と一致しないため採らない）。
  | 層 | ファイル | 変更 |
  |---|---|---|
  | usecase | `usecase/forming_tickvol.py`（新設） | `[win_start, to]` の実 tick 数。数える集合は `/intraday` と同じ domain E-4 `mid_series`（定義を 2 つ持たない）。ティック読込は窓ごとに 1 回 |
  | usecase | `usecase/causal_compute.py` | `window_port` 任意注入。`forming` へ volume を載せてから `apply_forming` |
  | framework | `framework/serve_replay.py` | `winStart`/`winEnd` を受け、`_window_port` を compute へ結線 |
  | front | `replay/forming_plan.js` | 各時点へ `to`（`secs[i]`）を添える。volume はフロントで作らない |
  | front | `replay.js` | real_ticks で `tick_secs` を常時要求（時計が常に要るため）。窓を送出 |
  | front | `adapter/front/forming_seq_client.js` / `compute_http_client.js` / `replay_indicator_controller.js` | `winStart`/`winEnd` の素通し seam（未指定は不送信＝ライブ・旧クライアントは挙動不変） |
  - `forming_bar.apply` の規約（存在するキーのみ更新）は**不変**。載せるキーが 1 つ増えるだけ。
- **収束の裏付け（実測 2026-08-01）**: `/intraday` の mid 列の件数は、同区間の確定足 tickvol と
  **完全一致**する（[1785528000,1785528300) が 770/770、[1785528300,1785528600) が 297/297）。
  よって足終端で形成中の値は確定値へ段差なく収束する。サーバ単体でも確認
  （to=+0s→2 / +60s→82 / +120s→130 / +180s→192 / +240s→254 / +299s→**297**）。
- **検証（実 UI・8000 リプレイ・NI225 5 分足）**:
  | モード | ローソク close 更新 | tickvol 更新（対応前） | 足内の推移 → 確定値 |
  |---|---|---|---|
  | 1分OHLC（既定） | 9 回 | **9 回**（0 回） | 232→525→**627** / 83→223→**297** / 105→213→**262** |
  | 実ティック | 32 回 | **32 回**（0 回） | 4→175→356…（単調・逆行 0 回） |
  足の先頭から確定値が出る未来先取りは解消した。コンソールエラー 0 件。
  回帰: replay Py 236 / JS 301、indicator_ui Py 673 / JS 1006 — 全通過。
- **残る制限**: `始値のみ` / `数学計算(終値)` は足内推移を持たない設計のため `to` を載せない
  （＝従来どおり確定値のまま）。両モードは「1 回だけ更新する」ことが仕様である。

## ISSUE-239: [仕様追加] ティックボリュームに正常帯と外れ値水準（経験的分位 / GPD-POT）を追加する（2026-08-01）

- **重大度**: —（機能追加）
- **ステータス**: RESOLVED（2026-08-01・feature/latest-incremental-compute）
- **依頼**: 「経験的分位、GPD と POT の仕様を追加しろ。確かライブラリーが存在しているので、参照実装しろ」
  「経験的分位＋GPD を並列表示」→ 追補「下位と上位の分位を追加」（依頼者確定 2026-08-01）。
- **参照実装（無改変で組み合わせる。計算式を写していない）**:
  | 役割 | 参照実装 |
  |---|---|
  | 因果ローリング分位バンド（下側/上側・上側は POT の閾値） | `common.marod_bands.quantile_bands` / `causal_stat_latest` |
  | エピソード宣言クラスタリング・経験的分位 | `common.event_quantiles.step_events` / `levels_at` |
  | GPD の最尤当てはめ | `common.gpd.gpd_fit`（2026-07-31 追加・単体 81 件で妥当性検証済み） |
  | 閾値の自動選択（既定値の根拠） | `common.gpd.select_threshold`（ForwardStop） |
  | クラスタ化の測定（設計前提の検証） | `common.extremal_index`（Ferro–Segers intervals） |
  `common/gpd.py` は本件まで指標からの参照が 0 件だった（研究用ゲート検証で追加された道具）。
- **仕様**: 正常帯上端 `u_t`（当該バー除外の因果ローリング分位 `q_high`）を超えたエピソードの極値を
  1 観測とし、その**超過分の同じ分位 `q_out`** を経験的分位と GPD の 2 通りで推定して並べる。
  水準は `u_t + 超過分の水準`。2 本の差が「標本内で数えた値」と「裾の分布形から外挿した値」の差になる。
  系列 6 本: `tickvol`（ヒストグラム）/ `tickvol_q{pct}`（正常帯・下側 `q_low`／上側 `q_high`・
  シアン点線・命名は `btlm_trail_q{pct}` と対称）/ `tickvol_evq_med_hi`（典型深度・実線）/
  `tickvol_evq_ext_hi`（経験的極端分位・破線）/ `tickvol_gpd_hi`（GPD 外挿・橙破線）。
  上側帯は **POT の閾値そのもの**（2 つの定義を持たせない）。下側帯は「普段より極端に静かな足」を
  示す**表示専用**で POT/GPD には使わない。
- **設計判断の実測根拠（2026-08-01・jp225_tick 50,000 本）**:
  - **宣言クラスタリングは必須**: 生の閾値超過は θ = 0.16〜0.27（5m/15m/1h・q=0.90）と強くクラスタ化し、
    GPD の独立前提を満たさない。エピソード極値へ畳むと θ = 0.49〜0.89 でゲート（θ >= 0.2）を通過する。
  - **ローリングは必須**: 水準は非定常で履歴 4 分割の中央値が 5m 170→489 / 1h 666→2049 と 3 倍動く。
    全履歴の当てはめは AD 適合度検定で棄却（p = 0.005〜0.255）、直近 50 件では棄却されない
    （p = 0.455〜0.720）＝ローリングでこそ GPD 近似が成立する。
  - **GPD の最小観測数は 30**: 窓をずらした 10 標本での GPD 水準の変動係数は m=5 で 0.95、
    m=10〜20 で 0.71〜0.73、m=30 で 0.245、m>=50 で 0.14〜0.21。30 未満は水準を出さない（NaN）。
  - **q_high 既定 0.90**: ForwardStop の自動採択は 5m 0.95 / 15m 0.90 / 1h 0.85 と時間足で動くため、
    採択域の内側で観測件数が最も確保できる点を採る。`q_low` は対称に 0.10。
  - **外れ値イベントは上側のみ**: tickvol は最小 1 の計数量（0 の足は 0 本）で下側は裾でない。
  - **`event_agg` を公開しない**: 「バー値」集計を選べるようにすると上記の独立前提が壊れる。
- **却下した構造（実測で不成立）**: 「窓 N の生の超過へ毎バー GPD を当てはめる」案。宣言クラスタリング後の
  有効クラスタ数が N=500 で 12 件・N=1000 で 11 件しか残らず当てはめが成立しない（N>=2000 が必要）。
  さらに毎バー当てはめは 1500 バーで **1.5〜1.9 秒**（既存最遅 cvfe 148ms の 10 倍超）だった。
- **性能（ISSUE-233 と同じ真因・同じ解）**: 水準は確定イベント全体に依存し有限 tail を取れない
  （直近 50 件に 5m で 1,800 バー必要）ため、`latest_meta` は増分計算を宣言する
  （`adapter/compute/incremental/tickvol.py`）。帯・水準は状態遷移時に 1 度だけ求めて状態へ持たせ、
  `emit` は読むだけにした。実測 **full 151ms / 足内 1 ステップ 0.49ms**
  （足内の GPD 再当てはめ 0 回・帯も足内で動かないことをテストで固定）。
- **検証**: latest == full を 50 本の逐次バー送り × 6 系列・巻き戻し・パラメータ 4 通りで完全一致を確認。
  実 UI（8000・ライブ／リプレイ両モード・NI225 5 分足）で 6 系列すべて描画し、水準の順序が
  `本体 262 < q10 328.00 < q90 1206.20 < 典型 1342.30 < 経験的極端 1956.26 < GPD 2139.59` となり
  GPD が経験的の外側に出ることを確認。コンソールエラー 0 件。回帰: indicator_ui Py 673 / JS 1006、
  replay Py 213 / JS 296、unified 43、tickvol 37、common 81 — 全通過。

## ISSUE-240: [仕様追加] ティックボリュームに btlm_trail の仕様（回帰トレンド・帯・β/σ/実績率）を追加する（2026-08-01）

- **重大度**: —（機能追加）
- **ステータス**: RESOLVED（2026-08-01・feature/latest-incremental-compute）
- **依頼**: 「ティックボリュームに btlm_trail の仕様を追加しろ」。適用方式は「既存に追加（分位は共有）」
  を依頼者が選択（2026-08-01）。
- **参照実装（無改変・計算式を 1 行も写していない）**: `btlm_trail`（F-01 窓末尾 OLS ローリング／
  F-05・F-06 バンド方式と分位ペア／F-08 外れ値分位ライン／F-09 バンド内実績率）。
  `indigators/tickvol/src/trend.py` が `build_btlm_trail` / `rolling_coverage` へ委譲し、増分器は
  1 バー入口（`window_end_scalar` / `empirical_quantile_latest` / `ols_band` / `empirical_band` /
  `deviation_ratio` / `coverage_latest`）だけを呼ぶ。btlm_trail 本体は無改変（OCP・
  btlm_trail_marod / ma_marod と同じ規律）。
  - `build_btlm_trail` はソース合成（8 択）を経る契約なので、tick 数を 4 値すべてに置いた合成
    DataFrame を渡し `source="close"` を指定する。tick 数は高値/安値/始値の区別を持たない 1 本の
    系列であり、この写像で情報は落ちない。
- **追加系列（8 本・計 14 本）**: `tickvol_trend_mean`（トレンド現在位置・ドット）/
  `tickvol_trend_q{pct}`（トレンド帯・動的名）/ `tickvol_trend_off_hi`・`_off_lo`（外れ値分位線）/
  `tickvol_trend_beta`・`_sigma`・`_band_hit_rate`（読取欄専用）。
  - **命名の衝突回避**: 分位値（q_low/q_high/q_out）は既存の水準帯と**共有**する仕様のため、
    帯の系列名が `tickvol_q{pct}` と完全一致してしまう。トレンド側は `tickvol_trend_q{pct}` として
    接頭辞で分ける（同じ分位で「水準の帯」と「トレンドの帯」を並べて読む）。
- **追加パラメータ（5 個）**: `maxbars`(100) / `band_method`(**empirical**) / `empirical_n`(500) /
  `show_metrics`(true) / `n_cov`(250)。maxbars・empirical_n・n_cov は btlm_trail 本体と同じ既定。
- **既定のバンド方式を btlm_trail 本体（ols）から変えた実測根拠（jp225_tick 6,000 本）**:
  トレンドからの乖離率は右に強く歪む（歪度 5m +35.5 / 15m +4.35 / 1h +2.15）。tick 数は最小 1 の
  計数量で、正規仮定の名目 ols は成立しない。
  | 時間足 | 名目 | ols 実績率 | 経験分位 実績率 | ols の帯下端<1 | 経験分位の帯下端<1 |
  |---|---|---|---|---|---|
  | 5m | 80% | 75.6%(-4.4pp) | 79.2%(-0.8pp) | 20.6% | 1.1% |
  | 15m | 80% | 80.8%(+0.8pp) | 79.6%(-0.4pp) | 14.8% | 0.0% |
  | 1h | 80% | 83.2%(+3.2pp) | 80.0%(+0.0pp) | 22.6% | 0.0% |
  | 5m | 90% | 86.0%(-4.0pp) | 89.2%(-0.8pp) | 33.8% | 4.8% |
  | 1h | 90% | 91.6%(+1.6pp) | 89.2%(-0.8pp) | 57.5% | 0.0% |
  経験分位は全条件で名目に近く（乖離 0.8pp 以内）、かつ帯下端が「tick 数として成立しない値」に
  なる割合がほぼ 0。ols は最大 57.5% のバーで下端が 1 を割る。**選択肢自体は両方残す**（btlm_trail
  と同じ語彙）。
- **同梱の不具合是正（`readout_only` が pane 指標でどこにも出ない）**: 描画ヒント `readout_only` は
  「描画せず読取欄だけに出す」意味だが、読取欄への登録条件が `!pane && overlayReadout` で pane 指標を
  除外していた。そのため tickvol の β/σ/実績率は計算・配信されても**線も出ず読取欄にも出ない死荷重**
  だった。条件へ `|| p.readout_only === true` を足す（対象は明示的にヒントを付けた系列だけなので、
  既存指標の読取欄行は 1 行も増えない）。
- **性能**: full 405ms（トレンド分 ≒260ms。内訳は btlm_trail の仕様どおり `_empirical_quantile_causal`
  ×4＋回帰ローリング＋被覆率ローリング）／**足内 1 ステップ 0.93ms**。トレンドは当該バーを含む窓の
  OLS なので形成中バーの値で動く＝`emit` で 1 点だけ求める（btlm_trail F-01 の定義どおり）。
- **検証**: latest == full を全 14 系列で確認。実 UI（8000 ライブ）で 14 系列すべて描画し、読取欄に
  `tickvol_trend_beta: 7.224 / _sigma: 259.864 / _band_hit_rate: 0.772` が出ることを確認。
  コンソールエラー 0 件。回帰: indicator_ui Py 674 / JS 1009、replay Py 236 / JS 301、unified 43、
  tickvol 51、btlm_trail 31 / marod 30 / ma_marod 43 / moving_averages 167、common 81 — 全通過。
- **注意点（実 UI 所見）**: 1 ペインに 14 系列となり、外れ値分位線の上端（既定 q_out=0.99 で実測 3,861）
  がペインの自動スケールを引き上げるため、本体ヒストグラムの見かけの高さが下がる。線を減らす場合は
  `show_metrics` を切る／`q_out` を空にするのが既存の operating point（いずれもパラメータで可能）。

## ISSUE-241: [調査・実測] 符号付きティック（ゼロ起点・上昇 +1／下落 −1）の有意性 → 方向情報は無い（2026-08-01）

- **重大度**: —（仕様検討のための調査。実装は行っていない）
- **ステータス**: RESOLVED（2026-08-01・結論: 方向としての採用は**却下**）
- **依頼**: 「ゼロを起点に、ティックが上昇 +1、下落 −1 を検討したいが有意性を調査しろ。現在は積み上げ式だが、
  上昇下落を明確に分離したい」。
- **定義（測る前に固定）**: `mid=(bid+ask)/2`（既存の唯一規則 `marketdata.tick_m1._ts_and_mid`）／
  `s_i = sign(mid_i − mid_{i−1})`／足の値は `n`(=現行 tickvol) / `up` / `dn` / `delta = up − dn` /
  `imb = delta/n`。**等値 tick は実測 0.0%** のため符号判定に曖昧さは無い（tick 間隔 ~100ms・
  mid 変化の中央値 3.0 ポイント・スプレッド中央値 5.11）。
- **標本**: jp225_tick の生ティック parquet。学習期間 2026-02〜07（155 日・5m 34,214 バー）と
  **別期間 2024-01〜06（155 日・5m 33,856 バー）** の 2 期間で同一手順を反復した。
- **検定**: Newey–West（HAC）標準誤差つき回帰の t 値と、ブロック順列検定（予測子のみブロック単位で
  並べ替え・2,000 反復）の 2 通り。系列依存を無視した t 検定は使わない。

| 検定 | 5m | 15m | 1h |
|---|---|---|---|
| 方向 corr(delta_t, r_(t+1)) 2026 | +0.0043 (p=.58/.43) | −0.0138 (p=.26/.15) | +0.0108 (p=.64/.57) |
| 方向 corr(delta_t, r_(t+1)) 2024 | +0.0010 (p=.90/.85) | +0.0069 (p=.65/.47) | +0.0489 (p=.02/.007) |
| 符号一致率（2026 / 2024） | 50.32% / 49.31% | 49.91% / 50.34% | 50.36% / 50.87% |
| **corr(up, dn)** | +0.9924 / +0.9864 | +0.9972 / +0.9950 | +0.9992 / +0.9984 |
| 変動 corr(n, \|r_(t+1)\|) | +0.359 / +0.356 | +0.325 / +0.318 | +0.181 / +0.198 |
| 変動 corr(\|delta\|, \|r_(t+1)\|) | +0.209 / +0.223 | +0.198 / +0.200 | +0.145 / +0.123 |

- **結論 1（方向は有意でない）**: 全時間足・両期間で方向の相関はゼロ近傍・符号一致率は 50% と区別できない。
  名目有意は 2 件だけ（2024 1h corr p=.007／2024 5m 一致率 p=.016）だが、**いずれも他期間で再現せず**
  （2026 1h は p=.57、2026 5m 一致率は 50.32% で p=.25）、しかも 5m の一致率は 49.31%＝**50% 未満**
  （コインより悪い）。約 24 検定に対し α=.05 の偶然期待は約 1.2 件で、この 2 件はその範囲内。
  期間 4 分割でも符号が反転する（5m: −0.015 / −0.001 / +0.023 / +0.009）。
- **結論 2（「上昇下落の分離」が分離になっていない）**: `corr(up, dn) = 0.986〜0.999`、`up/n` の平均は
  0.5001〜0.5019。up と dn はほぼ同一の系列（どちらも ≒ n/2）で、`|r_(t+1)|` への相関も
  0.3586 vs 0.3585（5m）と同値。**分けても 2 本の同じ線が並ぶだけ**で、差分 `delta` は
  `Var(delta)/E[n] = 0.83〜0.97`（公平コイン=1.0）＝コイン投げ以下のばらつきしか持たない
  （1 未満＝bid-ask バウンスによる tick 単位の平均回帰。トレンド構造は無い）。
- **結論 3（現行の積み上げの方が強い）**: 変動幅の予測は `n`（現行 tickvol）が `|delta|` を全条件で上回る。
- **結論 4（ローソクの劣化コピー）**: `corr(delta_t, r_t) = 0.72/0.65/0.53`、`sign(delta)=sign(r)` の
  一致率は 81.9%/78.6%/72.7%。**18〜27% の足でローソクの色と符号が食い違う**（陽線なのに delta<0）ため、
  「上昇/下落」として読ませると誤読を生む。
- **原理的な限界（重要）**: 本配信は**気配（bid/ask）のみで約定データを持たない**。したがって真の売買
  不均衡（order flow imbalance）は原理的に構成できない。mid の tick rule が拾うのは主に bid-ask バウンス
  である（結論 2 の分散比がその証拠）。代替として気配数量の偏り `(bidVol−askVol)/(bidVol+askVol)` も
  測ったが、`corr(qi_t, r_t)=+0.0006` / `corr(qi_t, r_(t+1))=+0.0041`（5m・20 日）で情報を持たない。
- **唯一の生存項目（方向ではない）**: `n` を統制した `|delta|` は `|r_(t+1)|` に対し増分有意
  （5m t=+7.28/+9.22、15m t=+4.79/+4.70。ただし 1h は 2024 で t=+1.73 p=.084 と非有意）。
  これは「片寄りの**強さ**」であって上昇下落の**方向**ではない。採用するなら別提案として扱う。
- **判定**: 依頼の形（ゼロ起点・+1/−1 で方向を分離）での採用は却下。実装は行っていない。

- **追補（RS 検証・依頼 2026-08-01）**: 「RS」を 2 通りに読めるため両方測った。**結論は一致**。
  - **(a) RS = up/dn → RSI 形式インデックス** `RSI_tick = 100 − 100/(1+RS) = 100·up/n`
    （等値 tick が 0.0% なので `up+dn=n`＝変換がそのまま成立）。
    | 時間足 | 分布 5%〜95% | ≧70 の出現率 | ≦30 の出現率 | 次足への corr（p） | 方向的中率 |
    |---|---|---|---|---|---|
    | 5m | 45.3〜54.9 | 0.275% | 0.240% | +0.0020 (.61/.70) | 50.26% |
    | 15m | 47.3〜52.8 | 0.035% | 0.018% | −0.0086 (.27/.35) | 49.89% |
    | 1h | 48.5〜51.4 | **0.000%** | **0.000%** | +0.0232 (.24/.21) | 50.25% |
    **RSI として成立しない**: 1 時間足は実測レンジが 42.6〜61.0 で買われすぎ/売られすぎ水準に
    **一度も到達しない**。閾値を狭めれば「反応」するが、それは 50 近傍の微小な揺れを拡大しているだけ。
    予測力も全時間足で非有意（HAC・ブロック順列とも）。価格 RSI(14) との相関は 0.13〜0.15。
  - **(b) R/S 分析（ハースト指数 H）**。推定量の有限標本バイアスを避けるため、**同じ値を並べ替えた
    帰無分布**（200 回）と比較した。
    | 対象 | 5m | 15m | 1h | 判定 |
    |---|---|---|---|---|
    | `n`（現行 tickvol） | 0.801 (z=+24.0) | 0.768 (z=+14.7) | 0.738 (z=+6.9) | **有意に持続** |
    | `delta`（偏りの絶対量） | 0.601 (z=+5.0) | 0.615 (z=+4.5) | 0.638 (z=+3.4) | 有意 |
    | `imb = delta/n`（偏りそのもの） | 0.553 (p=.265) | 0.561 (p=.410) | 0.562 (p=.955) | **非有意** |
    tick 符号列 s_i（±1・n=1,502,485）の H=0.5322 に対し、同長の公平コインは H=0.5189±0.0045。
    **`delta` に見えた持続性は偏りの持続ではなく `n`（tick 数）の持続を写したもの**で、スケールを
    外した `imb` では帰無と区別できない＝**偏りそのものはランダムウォーク**。
  - (a)(b) は独立の手続きだが同じ結論に到達する: 偏りを RS 化しても指標として機能しない。
- **追補 2（コップ方式・容量・ピーク判定の実測 2026-08-02）**: 「ゼロ起点の単純累積」が却下されたのを受け、
  依頼者提案の **コップ方式**（容量 c を超えた分だけを上下 2 つのコップへ n 区間累積）を検定した。
  - **分離は成立する（唯一の前進）**: 閾値で切ると up/dn の相関が **0.99 → 0.53〜0.70（5m）/
    −0.35〜+0.24（1h）** へ落ちる。当初の目的「上昇下落の分離」は、線形の差分ではなく閾値超過なら達成できる。
  - **回数と量は別物**: 溢れた「量」は溢れた「回数」で 41〜74% が説明できない（相関 0.51〜0.76）。
    容量を上げるほど両者は近づく（95% 点で 0.76）＝薄い裾では回数が支配的。
  - **容量は確定できない**:
    | 基準 | 結果 |
    |---|---|
    | GPD 適合（ForwardStop） | 2026 5m=95%点／**2024 5m=選択不能**（止まらず）／1h=85%点前後（両期間）＝**5m で再現しない** |
    | 指標の安定性 | 分離度が c に対し単調（5m +0.689→+0.607）で**高原が無い**＝最適点が定義できない |
    | 水準の期間安定性（用途「どこまで行ったら極端か」） | どの c でも 2026/2024 で 90% 点が **43〜77% ずれる**。絶対水準は定義不能 |
    → 使えるのは相対水準（直近 N 本での分位）のみで、それは tickvol の水準線が既に採っている方式。
  - **「今がピーク」は判定できない**: U_t が直近 k 本の最大なら宣言し、m 本以内に更新されなければ的中とする
    ルールを 18 条件（k=20/50/100 × m=10/20/50 × 上側/差 × 2 期間）で実測。宣言時の的中率 **41.9%** に対し
    「全時点で言い続ける」ベース率は **49.8%**＝**選ぶこと自体が逆効果**。ブロック順列帰無（41.7%）とも一致し
    情報が無い。名目有意の 5 件はすべて帰無より**低い**方向。理由は累積が重なり窓で緩やかに動くため、
    直近最大＝上昇中でその後も更新されやすいこと。
  - 帰無のブロック長は窓長の 10 倍（1,000 本）に取り、窓の重なりが作る自己相関を壊さない作りにした。
- **⚠ 本調査中の誤報告と訂正（2026-08-02）**: 「溢れの深さは強く持続（H=0.80〜0.99・両期間で再現）」と
  報告したが、**検定が成立していなかった**。実測側は 100 本の重なり移動窓で自己相関が構成上必ず入るのに、
  帰無側は値を並べ替えてそれを壊していたため、有意になるのが当然だった。重なりを除くと 5m で H=0.60
  （帰無 0.55）、1h は非有意。**「深さが量のばらつきの 48〜63%」も同じ重なり窓での分解のため未確定**とする。
  再発防止をメモリへ記録（移動窓・累積など構成上の自己相関を持つ量に並べ替え帰無を使わない）。
- **調査全体の判定**: 符号付きティックは、単純累積・RS 化・コップ方式（容量確定・ピーク判定）のいずれの
  形でも指標として成立しない。**実装は行っていない**。唯一の確定的な前進は「閾値超過なら上下を分離できる」
  という構成上の事実のみで、それを使う用途は見つかっていない。


## ISSUE-242: [仕様追加] 上昇／下落ティックボリュームを新規指標として追加する（2026-08-02）

- **重大度**: —（機能追加。データ基盤の追加列を伴う）
- **ステータス**: RESOLVED（2026-08-02・feature/latest-incremental-compute）
- **依頼**: 「n 区間累積値のティックボリュームを計測する指標なので、新規で作成しろ」「n は動的。
  上昇と下落を別々の 2 本でバーで描け」（依頼者指定 2026-08-02）。
- **前提の欠落（着手前に判明）**: 配信データに方向の内訳が無く `volume`（合計ティック数）しか無い。
  ISSUE-241 の調査で使った up/dn は生ティック parquet からその場で数えたもので、配信経路には存在
  しなかった。よって指標追加ではなく**データ基盤の拡張**が先に要ることを報告し、承認を得て実施した。
- **データ基盤（既存を壊さない追加のみ）**:
  | 層 | 変更 |
  |---|---|
  | `marketdata/csv_schema.py` | `UPDOWN_COLUMNS`（up/dn）・`SUM_COLUMNS`・`header_for` を追加。`HEADER` は不変 |
  | `marketdata/tick_m1.py` | `ticks_to_m1` が up/dn を集計。CSV は**持つときだけ末尾へ 2 列**追加 |
  | `marketdata/resample.py` | up/dn を volume と同じく合算（既定の "last" では誤り） |
  | `marketdata/rollup.py` | `_bar_to_dict` / `merge_same_period` / CSV 行・ヘッダが up/dn を運ぶ |
  - **方向の定義**: `sign(mid_i − mid_{i−1})` を **その分バーの中で** 取る。分・日をまたいで比べない。
    理由はチャンク独立性の契約（per-day concat == whole）で、またぐと処理単位で値が変わり壊れる。
    代償として各分の先頭ティックは方向を持たず `up + dn = volume − 分数` になる。等値ティックは
    どちらにも数えない（実測 0.0%）。
  - 既存 CSV（jp225_m1 / jp225_daily / sample）は up/dn を持たないため**列も挙動も不変**。
- **再生成**: `jp225_tick_m1.csv`（4,026,267 行・82 秒）と `rollups/jp225_tick/*`（39.9 秒）を再構築。
  実施前に両者をバックアップ（`*.bak-updown-*`）。行数は再構築前後で一致。
- **指標（新規パッケージ `indigators/tickvol_updown`）**: 各足の up/dn を直近 `window_n` 本ぶん合計し、
  ゼロを起点に 2 本のバーで描く（上昇＝正・緑／下落＝負・赤）。`window_n` は動的（既定 20）。
  `latest_meta` は窓長確定の非再帰集約として有限 tail（window_n + 8）を宣言＝latest は full と厳密一致。
  up/dn を持たない ref は `missing_column`（値を捏造しない）。
  **足内更新には登録しない**（形成中バーは方向内訳を持たず、窓に NaN が入ると累積が消えるため）。
- **⚠ 実測上の性質（採用は依頼者裁定・ISSUE-241）**: 上昇と下落はほぼ同じ大きさで動くため 2 本は
  ほぼ対称になる（移動累積の相関 0.9993〜0.9999・全期間累積 1.000000・最終差 0.188%）。実 UI でも
  上下がほぼ鏡像に見える。差し引いた 1 本にすると残差はコイン投げ以下（分散比 0.83〜0.97）。
- **表示形式の追補（依頼者指示 2026-08-02「1 本で表示」）**: 2 本のバーは実 UI で予測どおり
  ほぼ鏡像になり読めなかったため、**1 本のバー（上昇 − 下落）** へ変更した。値の符号が優勢な側を
  表し、バーごとの色を符号で切り替える（正＝緑・負＝赤。back が per-point ``color`` で与える）。
  系列は `tickvol_updown_up` / `_dn` の 2 本から `tickvol_updown` の 1 本へ。
- **検証**: 実 UI（8000 ライブ・NI225 5 分足・n=20）で 1 本描画・コンソールエラー 0 件
  （最新 −62。1,481 本の内訳は負 869 / 正 612 で符号どおり色が分かれる）。
  回帰: marketdata 217 / indicator_ui Py 683 / JS 1009 / replay Py 236 / JS 301 /
  tickvol_updown 12 — 全通過。


## ISSUE-243: [調査・実測] 疑似VWAP（ティック回数加重平均価格）の成立判定 → 別物だが方向情報は無く、既存列近似で 8〜9 割再現（2026-08-02）

- **重大度**: —（仕様検討）
- **ステータス**: IN_PROGRESS（Phase 1 実測完了・採否は依頼者裁定待ち）
- **依頼**: 「疑似VWAP = (価格帯別ティック回数 × 該当価格) / ティックボリューム を検討したい」（2026-08-02）。
  確定事項（同日 y/n）: 算出経路＝M1 に価格合計列を追加（価格帯を経由しない）／集計窓＝直近 N 本
  ローリング／進め方＝性質の実測を先に。
- **定義の同値性**: 依頼式は価格帯幅 → 0 の極限で「窓内全ティックの mid の単純平均」に一致する。
  `疑似VWAP_t(N) = Σ_{i=t-N+1..t} PV_i / Σ_{i=t-N+1..t} V_i`（`PV_i` = バー内 Σmid、`V_i` = tick 数）。
  価格帯経由は同じ量の**量子化近似**にすぎない。
- **実測**: `tools/verify_pseudo_vwap.py`（新規・読み取り専用）。jp225_tick の生ティック parquet。
  2024 全年（M1 333,488 本）／2026-01-01〜07-31（197,601 本）× 5m/15m/1h × N=20/50/100。
  集計は本番の `ticks_to_m1` / `repair_day_outliers` / `resample_ohlc_tf` をそのまま使用（式を写していない）。

  | 測定 | 結果 | 判定 |
  |---|---|---|
  | 1. 非退化（事前登録ゲート） | `median(\|疑似VWAP − SMA(close,N)\|) / median(TR)` = **0.148〜0.476**（閾値 0.10） | **通過**。SMA の再発明ではない |
  | 1b. 時間加重との重複 | 滞在秒加重平均（既存 dwell 相当）との差は SMA との差の **0.84〜1.03 倍** | 既存 dwell とも別物 |
  | 2. 依頼原式（価格帯経由）の量子化誤差 | 10pt グリッド＝信号の **0.20〜6.3%**／最小価格単位 0.0255＝**~1e-6%** | 帯を経由しない設計が正当 |
  | 2b. **既存列だけの近似で足りるか** | `Σ(hlc3×volume)/Σvolume` と厳密値の差は信号の **4.1〜22.4%**（絶対 0.93〜9.06pt） | **新規列で得る増分は 1 割前後** |
  | 3. 方向情報 | `sign(close − 疑似VWAP)` の将来 h=1/5/20 本リターン。非重複標本・ブロック順列（10N 本）・Holm 補正で **54 条件すべて非有意**（生 p 最小 0.024＝5m/N=50/h=1 → Holm 後 0.648）。2024/2026 で符号一致もしない | **情報なし** |
  | 4. 形成中バーの厳密性 | 部分窓で `(Σ確定 pv + Σ形成中 mid)/(Σ確定 vol + 形成中 tick 数)` が厳密値と一致（最大誤差 **3.6e-11**＝float 丸めのみ） | 足内更新は厳密に可能 |

- **測定 3 の作り**: 移動窓の構成上の自己相関を帰無で壊さないため、標本は h 本ごとの非重複、
  帰無は状態系列のブロック順列（ブロック長 = 10N 本相当）。ISSUE-241 の誤報告（並べ替え帰無）の再発防止。
- **測定 3 の限定**: 検定したのは「終値が疑似VWAP の上か下か」という**方向シグナル 1 用法のみ**。
  乖離率（押し目買い）用法は未検定（本リポジトリでは SMA 乖離率にエッジが実測されている）。
- **判断待ちの論点**: 「厳密な `pv` 列を足す（データ基盤 4 ファイル + M1 CSV・ロールアップ全再生成）」
  対価が、既存列近似に対する **信号の 1 割前後の精度** と足内更新の厳密性のみである点。
- **追加実測（測定 5・依頼者指示「乖離率の検定を先に」2026-08-02）**: 下方乖離ロング（押し目買い）で
  疑似VWAP乖離率 と SMA乖離率 を比較。閾値は乖離率の因果ローリング経験分位（当該バー除外・
  `common.marod_bands.quantile_bands`・q=0.10）、エントリーは h 本重ならないよう間引き、帰無は
  将来リターン系列のブロック順列（ブロック長 10N 本）。集合は「疑似VWAP のみ成立／SMA のみ成立／
  両方成立／各全体」の 5 つで、**「疑似VWAP のみ成立」にエッジがあるか**が pv 列を足す価値の直接判定。

  | 足 | 期間 | 結果 |
  |---|---|---|
  | 5m/15m/1h | 2024 / 2026(1-7月) | `pvwap_only` **0/30 件有意**（Holm 後）。生 p 最小 0.012（2026 15m N=50 h=5 +12.8bp）だが 2024 同条件は −1.3bp で符号不一致。`sma_only` 0/32・`both` 0/36・各全体 0/36 |
  | 1D | 2012-06〜2018 / 2019〜2026-07 | `pvwap_only` は**標本不足で 1 件も成立せず**＝日足では両者がほぼ同一のエントリーを出す。`both`/各全体も 0/12 |

  - **日足は検出力が足りず結論不能**（エントリー 20〜76 件・帰無ブロック数 2〜9。N=100 は
    ブロック長 1,000 本が系列長 1,700 本に迫り帰無が退化する）。かつティック履歴が **2012-06 以降**
    しか無いため、日足の疑似VWAP はそもそも標本を増やせない**構造的な上限**がある。
  - よって判定材料になるのは日中足であり、そこでは `pvwap_only`（エントリー 200〜500 件）に
    エッジが無い。**疑似VWAP は SMA 乖離率を上回らない。**
- **Phase 1 の総括**: 疑似VWAP は SMA とも滞在秒加重とも**別の量**（測定 1 通過）だが、
  (a) 方向シグナル・(b) 乖離率押し目買い のいずれでも**情報を持たない**（測定 3・5）。
  さらに (c) 既存列のみの `Σ(hlc3×volume)/Σvolume` が厳密値の 78〜96% を再現する（測定 2b）。
  実装するとすれば根拠は「表示用の厳密な平均価格」と「足内更新の厳密性」のみ。
- **追加実測（測定 6・依頼者指示「別の用法をさらに検定」2026-08-02）**: 3 用法を検定した。
  A: pv 固有残差 `resid = 疑似VWAP − Σ(hlc3×volume)/Σvolume`／B: セッションアンカーVWAP
  （下方乖離ロング・上方乖離反転・上抜け順張り）／C: 乖離幅 `spread = |close − 疑似VWAP|/close`
  のボラティリティ予測。状態変数は因果分位で上位群/下位群に分け、帰無はブロック順列（10N 本）、
  標本は h 本ごとの非重複、Holm は族内補正。5m/15m/1h × N=20/50/100 × h=5/20 × 2 期間。

  | 用法 | 結果 |
  |---|---|
  | A: resid → 将来リターン | 0/36 有意 |
  | A: resid → 将来 RV | 2/36（期間再現なし） |
  | B: セッションVWAP 3 用法 → 将来リターン | いずれも 0/12 |
  | C: spread → 将来リターン | 1/36 |
  | C: spread → 将来 RV | 21/36。ただし**対照 `spread_sma`（SMA 版）が 25/36 でより強い**＝ボラの自己持続であり疑似VWAP 固有ではない |
  | **C固有: Δspread = spread − spread_sma → 将来 RV** | **6/36。符号は 33/36 が負で一貫** |

- **✅ 確定した唯一の有効用法（ISSUE-243 の結論）**: `Δspread`（＝疑似VWAP と SMA の乖離幅の差。
  **pv 列を足して初めて作れる量**で、OHLCV からは作れない）は **短期実現ボラティリティの負の予測子**。
  事前登録条件 **5m・N=20・h=5** を、一度も観測していない第三期間で確認し、交絡も潰した。

  | 標本 | 上位群 RV | 下位群 RV | 群間差 | p |
  |---|---|---|---|---|
  | 2019–2023（OOS・事前登録）| 15.05 bp | 18.63 bp | **−3.58 bp** | 0.0001 |
  | 2024 | 17.02 | 21.36 | −4.34 | 0.0001 |
  | 2026(1–7月) | 22.20 | 30.31 | −8.11 | 0.0001 |
  | 上記に `spread_sma` 中位帯を条件付加 | — | — | −3.11 / −3.81 / −8.22 | 0.0001 |
  | さらに `tickvol` 中位帯も条件付加 | 13.39 / 14.30 / 20.34 | 16.79 / 18.51 / 26.23 | **−3.40 / −4.21 / −5.90** | 0.0001 |

  - **対照が逆符号**: `spread_sma` 単体は RV を**正**に予測する（+6.94 bp）。Δspread はその裏返しではない。
  - **既存 tickvol の言い換えでもない**: 窓合計 tickvol を中位帯へ固定しても効果はほぼ減衰しない
    （−18〜22% の RV 低下が残る）。
  - **限定**: 確立したのは 5m・N=20・h=5 のみ（他足・他窓は探索止まりで未確立）。方向性の情報は
    どの用法にも無い（リターン系は全滅）。**機序は未検証**（推論を事実として述べない）。
- **追加実測（測定 7・機序の記述統計）**: Δspread 上位群 / 下位群の共変量中央値（5m・N=20・3 期間とも同方向）。

  | 共変量 | 上位群（静かになる） | 下位群（荒れる） |
  |---|---|---|
  | 終値 − 疑似VWAP | **+25.9 / +50.8 / +102.8 pt** | −10.1 / −17.8 / −19.8 pt |
  | 疑似VWAP − SMA | −8.1 / −14.3 / −28.4 pt | −7.4 / −11.8 / −23.1 pt |
  | トレンド効率（net/path）| 0.29 / 0.32 / 0.34 | 0.34 / 0.34 / 0.36 |
  | 過去 RV（直近 N 本）| 30.1 / 33.5 / 49.0 bp | 32.5 / 36.4 / 55.6 bp |
  | 窓 tickvol | 1841 / 2928 / 6827 | 1705 / 2906 / 6880 |

  - Δspread の上位 / 下位は実質的に「終値が疑似VWAP の**上か下か**」の分離である。
  - 窓 tickvol は両群でほぼ同一（比 0.99〜1.08）＝ 既存 tickvol 指標の言い換えではない（条件付 2 と整合）。
  - 上位群は直近 RV が 7〜12% 低い → **過去 RV 自体が残る交絡**であることが判明したため次を実施。

- **追加実測（過去 RV の交絡除去・条件付 3）**: SMA 乖離幅・tickvol に加え**過去 RV も中位帯へ固定**しても効果は
  減衰しない（むしろ拡大）。5m・N=20・h=5。

  | 期間 | 上位群 | 下位群 | 群間差 | p |
  |---|---|---|---|---|
  | 2019–2023 | 11.64 bp | 15.58 bp | **−3.93 bp（−25%）** | 0.0001 |
  | 2024 | 12.80 | 16.75 | **−3.95 bp（−24%）** | 0.0011 |
  | 2026(1–7月) | 17.36 | 23.69 | **−6.33 bp（−27%）** | 0.0006 |

- **⚠ 「静かになる」の意味の確定（分解実測）**: 乖離幅は確かに縮む（上位群 −3.3/−4.0/−5.9 bp・
  下位群は逆に +4.7/+5.6/+7.2 bp 広がる・いずれも p=0.0001）。しかしその要因は**価格の回帰ではない**。

  | 25 本後の動き | 上位群 | 下位群 | 群間差の有意性 |
  |---|---|---|---|
  | **終値** | +0.55 / +0.82 / +1.24 bp | −0.30 / +0.45 / +3.88 bp | **無し**（p = 0.34〜0.58） |
  | **疑似VWAP** | +3.17 / +2.83 / +6.69 bp | −0.74 / −0.52 / +0.21 bp | 有り（p = 0.0002） |

  → **価格が止まっている間に平均が追いつく**のであって、疑似VWAP へ回帰するのではない。
  方向性が全用法で非有意だったこととも整合する。

- **追加実測（当日版・セッションアンカー VWAP のボラ予測）**: 当日累積の疑似VWAP で同じ検定を行った。
  対照はセッション累積の単純終値平均（OHLCV だけで作れる版）。場中序盤 24 本（5m で 2 時間）は除外。

  | 標本 | ローリング版（直近 N 本） | 当日版（セッション累積） |
  |---|---|---|
  | 素の状態 | −3.58 / −4.34 / −8.11 bp（全 p=0.0001）| −3.93 / −5.51 / −8.52 bp（全 p=0.0001）|
  | ＋過去 RV・tickvol を中位帯へ固定 | **−3.93 / −3.95 / −6.33 bp（p ≤ 0.0011）残る** | **−1.00 / −0.71 / −2.31 bp（p = 0.1175 / 0.6229 / 0.3194）消える** |

  → **当日版に固有の情報は無い**。素で有意に見えるのはボラの自己持続と出来密度で説明できる。
  採用しうるのは**直近 N 本のローリング版のみ**。

- **追加実測（成立域の広がり）**: 5m/15m/30m/1h/4h × N=10/20/50/100 × h=5/20 × 3 期間を
  条件付 2 で走査。3 期間すべてで負かつ有意なのは **5m・N=10・h=5**（−2.05/−2.38/−3.99）と
  **5m・N=20・h=5**（−3.40/−4.21/−5.90）の 2 条件のみ。15m 以上は 2024 で符号が反転し成立しない。
  4h は標本不足で判定不能。

- **⚠ 乖離率の既知エッジとの関係（誤解の防止）**: 「疑似VWAP だと乖離率の有意性が無い」のではない。
  本検定では **SMA 乖離率も同じく全滅**している（日中足 0/32・日足 0/12）＝ 原子（四本値 vs ティック平均）の
  差ではなく、本検定が既知の日足エッジを再現できていない。さらに**日足では「疑似VWAP だけが成立する日」が
  1 件も無い**（両者がほぼ同一の判定を出す）ため、既知の SMA 日足エッジは疑似VWAP でも同様に出るはずで、
  失われてはいない。両者に差が出るのは日中足のみで、そこでは双方ともエッジが無い。


## ISSUE-244: [整理] ティックボリューム系の指標を整理する（上昇下落を UI から撤去・tickvol から回帰トレンドを撤去）（2026-08-02）

- **重大度**: —（機能整理。データ基盤・計算コードは残す）
- **ステータス**: RESOLVED（2026-08-02・feature/latest-incremental-compute）
- **依頼**: 「ティックボリューム系の指標を整理する。上昇下落ティックボリュームを UI 上から削除しろ。
  ソースコードはアーカイブとして保持しておく」「ティックボリュームから、btlm_trail を削除しろ」
  （依頼者指示 2026-08-02）。
- **方針**: UI から外す／計算コードはアーカイブとして残す／**データ基盤には触らない**。

### 作業 1: 上昇下落ティックボリューム（`tickvol_updown`）を UI から撤去

- **外した理由（実測）**: 上昇と下落がほぼ鏡像（移動累積の相関 0.9993〜0.9999・全期間累積
  1.000000・最終差 0.188%）。1 本化しても残差はコイン投げ以下（分散比 0.83〜0.97）。
  符号付きティックに方向情報が無いことは ISSUE-241 の 18 条件で確定済み。
- **外した結線**: `call_binding.py`（`_TABLE` エントリ・`_tickvol_updown_latest_meta`・
  `_TICKVOL_UPDOWN_MARGIN`）／`golden/catalog_defaults.json`／`test_solid_binding_spec_guards.py`／
  `catalog.js`（`IndicatorDef` と `REGISTRY`）／JS テスト 6 本の件数。
- **削除**: `api/tests/test_tickvol_updown_binding.py`（結線そのもののテスト・依頼者承認済み）。
- **残したもの**: パッケージ `indigators/tickvol_updown/`（単体テスト 12 件は通る）、
  `marketdata` の `up`/`dn` 列と再生成済み CSV。
- **front だけ外せない理由**: `catalog_schema_sync.test.js` が golden の全 compute_id を front
  レジストリに要求するため、back `_TABLE` と golden も同時に外す必要がある。

### 作業 2: ティックボリューム（`tickvol`）から回帰トレンド（btlm_trail 仕様・ISSUE-240）を撤去

- **外した系列 7 本**: `tickvol_trend_mean` / `_q{pct}`（動的）/ `_off_hi` / `_off_lo` /
  `_beta` / `_sigma` / `_band_hit_rate`。
- **外したパラメータ 5 個**: `maxbars` / `band_method` / `empirical_n` / `show_metrics` / `n_cov`。
- **残したもの**: 本体ヒストグラム・正常帯 `tickvol_q{pct}`・外れ値水準 3 本（ISSUE-239）と、
  パラメータ `window_n` / `q_low` / `q_high` / `q_out` / `k_events`。
- **触った層**: 指標（`tickvol/src/{__init__,lwc_chart}.py`）／結線（`call_binding.py` の
  `params_defaults`）／増分（`incremental/tickvol.py` の `_State.trend`・`deviations`・`_trend_at`・
  emit のトレンド節。帯の割当が 4 本 → 2 本）／golden／front `catalog.js`／テスト 4 本。
- **`indigators/tickvol/src/trend.py` は残す**（アーカイブ）。単体テスト `test_trend.py` も通る。

### アーカイブの所在

`indigators/tickvol_updown/ARCHIVE.md` に両方の「外した理由」「残したもの」「復活手順（触る
ファイルの完全なリスト）」を記録した。`tickvol_updown/src/__init__.py` と
`tickvol/src/trend.py` の docstring 冒頭にも「UI 未結線のアーカイブ」を明記した。

### 検証

- 回帰: indicator_ui Py 674 / JS 1009、replay Py 236 / JS 301、marketdata 217、common 81、
  tickvol 51、tickvol_updown 12、btlm_trail 31 — **全通過**。
  （指標パッケージは top-level 名 `src` が衝突するため 1 パッケージずつ実行する。）
- 同期契約: `test_catalog_schema.py` ↔ `catalog_schema_sync.test.js` が両方緑
  （結線の外し漏れはここで落ちる）。
- 実 UI（8000・NI225 5 分足・ライブ／リプレイ両モード）: 指標一覧に「上昇下落ティックボリューム」が
  出ないこと、`tickvol` ペインが本体＋正常帯 2 本＋水準線 3 本のみ（紫のトレンド線・帯が出ない）で
  あること、パラメータ欄が 5 個であること、コンソールエラー 0 件を確認。
  なお本検証中に ISSUE-245（撤去した指標の凡例行が保存状態から残る）を検出し、同時に是正した。


## ISSUE-245: [不具合・実測再現] 指標を UI から外すと、保存済み設定に残った instance が「死んだ凡例行」として残る（2026-08-02）

- **重大度**: Medium（撤去した指標を適用済みだったユーザーだけに出る。描画・計算は行われない）
- **ステータス**: RESOLVED（2026-08-02・feature/latest-incremental-compute）
- **事象**: ISSUE-244 で `tickvol_updown` を UI から外した直後、実 UI（8000 ライブ）の凡例に
  `tickvol_updown` の行が残った。ラベルは翻訳されず raw な指標 ID のまま、系列もデータも無い。
  `localStorage["live:indicatorUi.applied.v1"]` に当該 instance が残っていることが実測で確認できた。
- **真因**: **在席の権威はカタログ 1 つなのに、保存状態がそれと独立にカタログ外の指標を保持していた。**
  `indicator_state_store.rebuildApplied` は `_catalog.get` が無い instance を compute/描画から
  除外する（`if (!def) continue`）が、**`_state.applied` からは落とさない**。一方
  `indicator_controller._renderLegend`（`:960-977`）は `_state.applied` をそのまま行に写し、
  `def` が無い場合は `inst.indicatorId` をラベルにフォールバックする。結果、状態とカタログの
  不整合がそのまま凡例へ出た。
- **対策（根本）**: 復元の入口で不整合を作らない。`indicator_state_store._pruneUnknown` を新設し、
  `_restoreRun` が `loadApplied()` した直後に**カタログに存在しない indicatorId を除去**してから
  `_commitState` する。除去が発生したときだけ `saveApplied` で永続化へ書き戻す
  （次回起動でゴミが再登場しない・除去が無ければ書き込まない）。
  凡例側のフォールバック表示は防御として残す（症状を隠す修正はしない）。
- **検証**:
  - 回帰テスト新設 `web/tests/restore_prunes_unknown_indicator.test.js`（3 件）:
    状態に残らない／永続化へ書き戻す／全件既知なら落とさず書き込まない。
  - 実 UI（8000）: 再読込後の凡例は `moving_averages` / `btlm_trail` / `tickvol_bands` /
    `ティックボリューム` の 4 行のみ。`localStorage` の保存 ID も 4 件へ縮小。
    ページ全体に `tickvol_updown` の文字列は 0 件。コンソールエラー 0 件。
  - リプレイモードでも同一（カタログ 26 件・`tickvol` は 5 パラメータ）。
  - 回帰: indicator_ui JS 1012（+3）・replay JS 301 — 全通過。

## ISSUE-246: [整理] RSI から EMA 平滑線（`ma_period` / `rsi_ma`）を削除する（2026-08-02）

- **重大度**: —（機能整理。RSI 本線・σ 水準の値は不変）
- **ステータス**: RESOLVED（2026-08-02・refactor/rsi-drop-ma-period）
- **依頼**: 「RSI の指標から `ma_period` の設定項目を削除。使用方法に意味を見出だせないが、何かあるか」
  → 調査結果（`ma_period` の唯一の作用は EMA 平滑線 `rsi_ma` の描画。σ 水準は元から**生 RSI**由来で
  無関係。戦略・シミュレータ側の参照はゼロ）を提示し、**パラメータと系列を同時に削除**する案で承認取得
  （y・2026-08-02）。パラメータのみ削ると平滑線が period=5 固定で残るため不整合になる。
- **元 MQL との差分**: 元 `PRO!fitRSI.mq4` の `ExtMABuffer`（`iMAOnArray(MODE_EMA, InpMAPeriod=5)`）を
  移植対象外とした。参照実装からの意図的な逸脱であり、承認に基づく（SPEC §2「対象外」に明記）。
- **触った層**: 指標（`profit_rsi/src/{core,rsi,plot,lwc_chart,__init__}.py`・`demo.py`）／
  結線（`call_binding.py` の `params_defaults`）／golden（`catalog_defaults.json`）／
  front（`catalog.js` の `params` と `series`。ライブ／リプレイは symlink 共有で 1 実体）／
  ドキュメント（`profit_rsi/SPEC.md`・`README.md`）／テスト 7 本。
- **削除した公開シンボル**: `DEFAULT_MA_PERIOD`・`MA_COLUMN`（`rsi_ma`）・`RsiResult.ma`、
  および `compute_rsi_full` / `build_rsi` / `rsi_levels` / `plot_rsi` / `add_rsi` の `ma_period` 引数。

### 検証

- 回帰: profit_rsi 43／indicator_ui Py 674・JS 1012／replay JS 301 — **全通過**。
  σ 水準が生 RSI 由来であることは TC-11（EMA 平滑系列を foil に用いる）で削除後も固定した。
- 実 UI（8000 ライブ・NI225 1 時間足・スタック再起動後）: RSI のパラメータ欄が `rsi_period` と
  「ソース」の 2 個のみ（`ma_period` が無い）、スタイル欄が `rsi` 1 行のみ（`rsi_ma` が無い）、
  RSI ペインは線 1 本＋σ 水準線 7 本で描画。指標関連のコンソールエラー 0 件。

## ISSUE-247: [仕様追加] RSI の水準を「分位＋POT/GPD」へ全面置換する（2026-08-02）

- **重大度**: —（水準の定義変更。RSI 本線の値は不変）
- **ステータス**: RESOLVED（2026-08-02・feature/rsi-quantile-pot-gpd）
- **依頼**: 「RSI に分位、GPD と POT の概念を取り入れたい」（2026-08-02）。構成は y/n 確認のうえ
  **σ 7 水準の全面置換**・**上下両側**で確定。共有化（`gpd_excess_quantile` の `common` 昇格）も承認済み。
- **置換した理由（実測）**: 元の σ 7 水準は `iStdDevOnArray(ExtRSIBuffer, 0, rates_total, ...)`＝
  **全系列**の統計で、バー t の水準が t より後のバーに依存する（非因果・リペイント）。ライブ／
  リプレイの水準として成立しない。因果ローリング分位を閾値とする POT へ置換した。

### 構成（tickvol/src/levels.py と同型・共有プリミティブは無改変参照）

1. 正常帯＝POT 閾値 `u_t`: 当該バー除外の因果ローリング分位（`common.marod_bands`）。
2. 超過は**余地割合** `(RSI−u)/(100−u)`（下側 `(u−RSI)/u`）で測る ← **本件固有**。
3. 超過エピソードへ畳み込み（`common.event_quantiles.step_events`・`episode`）。
4. 直近 `k_events` 件から経験的分位（`ext`）と GPD 外挿（`gpd`）の 2 水準。

### 着手前ゲートの実測（jp225_tick・5m/15m/1h/4h/1D × 上下側）

| 測定 | 結果 |
|---|---|
| θ̂（生の閾値超過） | 0.206〜0.295（ISSUE-227 の RSI 系列 θ̂ = 0.107〜0.269 と整合） |
| θ̂（エピソード畳み込み後） | **0.859〜0.947**＝ゲート（θ >= 0.2）通過 |
| エピソード観測数 | 95〜1,625 件（GPD 最小 30 を全条件で充足） |
| ξ̂ | 全 10 条件で負（−0.20〜−1.08）＝有限終端 |
| GPD 終端 vs 理論境界 | 上側 94.9〜107.0（境界 100）／下側 −3.2〜+7.4（境界 0）＝**有界性を data-driven に復元** |
| AD 適合度（直近 50 件） | p = 0.475〜0.960（全条件で非棄却） |
| AD 適合度（全履歴） | 1h 下側 p=0.005・4h 下側 p=0.020 で棄却 → **ローリング必須** |
| ForwardStop（全履歴） | 採択が時間足で 0.80〜0.95 に散る（非定常のため選択基準にしない） |
| ローリング棄却率（窓 10 本） | q=0.80〜0.95 のいずれでも 0〜20%（名目 5% と整合）→ 既定 q=0.90 |

- **⚠ 途中で見つけた設計上の欠陥（是正済み）**: tickvol と同じ生スケール（`RSI − u`）で水準を
  出すと、「現在の閾値＋過去の超過量」が RSI の境界を越え、**全バーの 26〜35%（5,566〜17,625 本）が
  [0,100] の外**へ出た。有界量に無界量向けの構成をそのまま移したのが原因。余地割合スケールへ
  変更して範囲外は 0〜0.7% へ縮小し、GPD 外挿を台 1.0 で抑えて **0 本**にした。
- **⚠ 実装中に検出した誤り（是正済み）**: 下側の分位方向。本実装の観測は「超過の大きさ」なので
  下側も `q_out`（0.99）が最も深い。`common.event_quantiles` の `ext_lo` が `1−q_out` を使うのは
  あちらの観測が符号付きの値そのものだからで、規約が異なる。初版は `1−q_out` を渡しており、
  下側水準が正常帯のすぐ内側（実測 29.49 対 帯 30.01）に出ていた。

### 共有化（承認済み）

`gpd_excess_quantile` と `MIN_GPD_EVENTS` を `common/gpd.py` へ昇格（追加のみ）。
`tickvol/src/levels.py` は委譲へ置換し公開名を温存。common 側に単体テスト 5 件を新設。

### 検証

- 回帰: common 86（+5）／profit_rsi 54（+11）／tickvol 51／indicator_ui Py 674・JS 1012／
  replay JS 301 — **全通過**。混在 kind（line＋horizontal_line）の JS 回帰テストは代表を
  profit_rsi → profit_mfi へ差し替えた（profit_rsi は全 line になったため）。
- 性能: 1h・1500 本の `/compute` が **0.21 秒**（GPD 当てはめはイベント数ぶんに限定）。
- 実 UI（8000 ライブ・NI225 1 時間足・スタック再起動後）: パラメーター欄に
  移動期間（閾値）500 / 下側分位 0.1 / 上側分位 0.9 / 外れ値の極端分位 0.99 / 外れ値イベント数 K 50、
  スタイル欄に 7 系列（rsi・rsi_q10・rsi_q90・rsi_evq_ext_hi/lo・rsi_gpd_hi/lo）。
  RSI ペインに緑の本線＋シアン点線の正常帯（77.24 / 17.84）＋赤破線の経験的水準（97.25 / 1.63）
  ＋橙破線の GPD 水準（97.45 / 1.52）が描画。指標関連のコンソールエラー 0 件。
