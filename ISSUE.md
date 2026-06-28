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
- **ステータス**: OPEN
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
