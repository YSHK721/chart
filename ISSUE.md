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
- ステータス：OPEN
- 検出日：2026-06-17
- 検出経路：backtest usecase 層 TDD（compute_stats を METRICS §12 の10トレード期待値で固定する過程）。tdd-executor が3独立手法で実測
- 内容：
  - (1) §12.2/§12.6 は Sharpe=0.17・σ=0.020019 と記載するが、§1.2/§11 の式（ddof=0 母分散）からは σ=0.018362・Sharpe=0.1862 となり再現不能。§12 のその他 STAT_*（PF=1.5593/EP=33/RF=0.9429/DD=350・3.38%/Z=1.3416/連勝連敗/件数）は式と完全一致
  - (2) §11 の Z-Score ヘルパーは sqrt が欠落しており §3.2 数式と不一致。§3.2 数式採用で §12 期待値 1.3416 に一致
- 対策（暫定・実施済み）：「式を一次情報とする」方針に従い Sharpe=0.1862・Z=1.3416（§3.2 式）で実装・固定。回帰テスト添付済み
- 未解決点（要ユーザー確認）：実 MT5 STAT_* の σ 定義（母分散 ddof=0 か標本分散 ddof=1 か）と Sharpe 基準。Section 5 integration で実 MT5 突合時に §12 記載値 0.17 の出所を確定し、必要なら式 or 仕様書を改訂
- 追記（2026-06-17・usecaseレビューで深掘り）：Sharpe の「収益率基準」自体も一次情報間で矛盾。METRICS §1.2 は balance-HPR・ddof=0・非年率を規定する一方、PROCESS §6.1/§7-#9 は equity・単純収益率・足ベース・ddof=1・年率係数√A を規定。現実装は「式優先」方針により METRICS §1.2 を採用。doc 側でどちらを正とするか（MT5 STAT_SHARPE_RATIO の実定義）を Section 5 実 MT5 突合時に確定し統一する。

## ISSUE-014

- 概要：tick_model の許容値表記が一次情報間で不一致。PROCESS §7 #1 は「全ティック/OHLC4展開/始値のみ」（正準＝every_tick/ohlc_expand/open_only）だが、DESIGN §7.2:290 のドメインモデル・スケッチは `Literal["ohlc_simulate"]` と別名
- 重大度：低（config_loader 実装は PROCESS §7 正準名に準拠＝正しい。将来 Engine 側が DESIGN §7.2 名を期待すると不整合になる潜在リスクのみ）
- ステータス：OPEN
- 検出日：2026-06-17
- 検出経路：backtest framework 層 config_loader のコードレビュー（重点観点2 の許容値照合）
- 対策（要文書修正）：DESIGN §7.2 のスケッチ値 `ohlc_simulate` を PROCESS §7 の正準名（every_tick/ohlc_expand/open_only）へ追従更新する。コードは現状維持で可
