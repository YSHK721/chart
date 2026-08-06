// catalog_entry.js — Market Profile の IndicatorDef を組み立てる factory（MP モジュール所有）。
//
// 設計入力: MP frontend 切り出し（Phase2）。present catalog.js が持っていた MARKET_PROFILE
//   IndicatorDef 定義を MP モジュール側へ移設し、present は本 factory を import して登録する
//   （メニュー導線＝プロファイルタブからの MP 追加はユーザー明示指示で維持必須）。
//
// 依存注入（DIP）: catalog.js のローカル helper（param / OHLC）とドメイン型（IndicatorDef /
//   SeriesDef / SeriesKind / ParamType / ConstraintKind）は present 側に実在し続けるため、
//   循環 import を避けて引数注入で受け取る。生成される IndicatorDef は移設前と byte 等価。
//
// 挙動不変: 本 factory の戻り値は、移設前に catalog.js 内で inline 定義していた
//   MARKET_PROFILE と同一のオブジェクト構造（id / params / series / compute）を返す。

// ソース能力記述子（domain 単一情報源）。src ごとの挙動差（対応 tf・session ブロック・
//   選択肢・ラベル・期間窓）はすべて本記述子から導出する（ISSUE-097 🟡-8/🟡-9・散在解消）。
import {
  mpSupportsTf,
  mpSourceCapability,
  MP_SELECTABLE_SOURCES,
  MP_DEFAULT_SOURCE,
  mpSourceEnumLabels,
  MP_ZP_SESSIONS_BLOCKED_TFS,
} from '../domain/mp_source_capability.js';
// 表示モードの宣言的属性（splitByDay/isNormal）の単一台帳（ISSUE-134 OCP）。
import { mpDisplayMode } from '../domain/mp_display_mode.js';

// tf-period が日別列を描く対応 tf は「台帳が知っている全時間足」＝ TF_CODES（ISSUE-278 #12）。
//   ISSUE-070／ISSUE-086: 1W/1M もセッション日次ロールアップのバケット列として対応する
//   （count 1m..1M / zp 15m..1M＝backend _BUCKET_TFS・_ZP_TF_ALLOWED と一致）。src 別の
//   対応 tf は記述子（supportedTfs）が持つ。
//   かつてはここに 9 コードを手書きした Set を置いていたが、台帳へ時間足を足しても追随せず、
//   その tf だけ「列が描かれないのに解像度パラメータは有効表示」という無言の不整合になる
//   （ISSUE-253 と同型の事故源）。同パッケージの mp_source_capability.js は既に TF_CODES を
//   参照しており、ここだけが取り残されていた。
import { isKnownTimeframe } from '../domain/tf_meta.js';

// ISSUE-080（依頼者裁定 2026-07-15）: 日別×1m/5m の zp 非対応集合は記述子（zp.blockedSessionTfs）
//   が単一情報源。後方互換のため本名で再エクスポートする（actor と同一実体を共有）。
export { MP_ZP_SESSIONS_BLOCKED_TFS };

// tf-period が日別プロファイル列を描く状態か（＝解像度パラメータが無効な状態）。
//   条件: mode=sessions かつ対応 tf（src=zp は 15m 以上）。ctx は { timeframe } を受ける。
//   かつては served(B方式) 判定も含んでいたが、A方式の廃止（ISSUE-266）で配信は served 一択に
//   なったため撤去した。
function _mpTfPeriodDrawsColumns(values, ctx) {
  if (!ctx) { return false; }
  if (!mpDisplayMode(values.mode).splitByDay) { return false; }
  const tf = ctx.timeframe;
  if (!isKnownTimeframe(tf)) { return false; }
  if (!mpSupportsTf(values.src, tf)) { return false; }
  return true;
}

// 解像度パラメータ（resmode/bins/range）の enabled 述語: tf-period 列描画時のみ無効（グレーアウト）。
//   通常モード・非対応tf の日別（タイル描画）・A方式では解像度が有効なので enabled=true。
const _mpResolutionEnabled = (values, ctx) => !_mpTfPeriodDrawsColumns(values, ctx);

// 期間パラメータ（period）の enabled 述語: 通常モードでのみ有効（ISSUE-086: tf 制限を撤廃し
//   全時間足で統一）。「当日」窓＝現在セッション日はチャート tf と独立に定義できる
//   （actor が from=セッション始端を付与・1W/1M ラベルの未来日化は actor 側で now クランプ）。
//   日別（各営業日で分割済み）では計測窓の意味が重複するため通常モード限定は維持。
const _mpPeriodEnabled = (values, ctx) => mpDisplayMode(values.mode).isNormal;

export function makeMarketProfileDef({
  IndicatorDef,
  SeriesDef,
  SeriesKind,
  ParamType,
  ConstraintKind,
  param,
  OHLC,
}) {
  // --- market_profile（プロファイルタブ・アクター委譲型）--------------------
  // 他指標と異なり /compute で系列計算しない。IndicatorController が
  //   compute.computeId==='market_profile' を判定し、_draw（F3 系列描画）をバイパスして
  //   MarketProfileActor（GET /market_profile → primitive 反映）へ委譲する（専用パス）。
  // series は IndicatorDef の非空必須（domain_models.js L104）を満たすための最小ダミー 1 件で、
  //   上記バイパスにより描画には用いられない（renderLine/renderHorizontal を通さない）。
  // 既存トグル（#market-profile-toggle）は温存（二重導線）。表示名は displayNameKey 方式に準拠。
  return new IndicatorDef({
    id: 'market_profile',
    displayNameKey: 'ind.market_profile',
    category: { group: 'builtin', nameKey: 'cat.volume' },
    tab: 'profile',
    placement: 'overlay',
    params: [
      // ISSUE-081（依頼者指示 2026-07-15）: 表示モードを**親**としてダイアログ先頭へ置き、以下の
      //   パラメータ（子）はモード・時間足に応じて表示/非表示で切り替える（グレーアウト廃止＝
      //   「効かないツマミは見せない」。グレーアウトはユーザビリティを下げるとの依頼者指摘）。
      // mode: 表示モード（ENUM・既定 'normal'・表示系 group・segmented トグル）。旧 replay(BOOL)/
      //   sessions(BOOL) の 2 チェックを 1 つの排他トグル [通常｜リプレイ｜日別プロファイル] へ統合する
      //   （解像度トグル resmode と同方式）。排他が構造的に保証され、同時 ON が不可能になる。
      //   - normal: 全期間累積プロファイル（既定）。成長状態（FOLLOW/reveal 前進）では現在足の bar-period
      //     forming で足内成長する（Model A: 表示モード×成長状態の直交化。成長は growing 信号が担う）。
      //   - replay: リプレイバー表示（旧 replay=true と同一挙動・時間カーソル as-seen-at-t）。sessions は必ず OFF。
      //   - sessions: 日別プロファイル分割（旧 sessions=true と同一）。replay は必ず OFF（バー非表示・
      //     T 縦線/トリム/スナップショット解除）。成長状態では当日タイルが [session_start, cursor) で因果成長
      //     （refresh(to, sessions)・機構A）。
      //   Phase5（統一成長）: 旧 'ticklive' セグメント（表示選択肢）は撤去した。足内 1tick 逐次成長は
      //     「表示モード」ではなく成長軸（growing 信号）が担う（直交化）＝normal/sessions のいずれでも成長する。
      //     成長エンジン（_enterTicklive/forming/DwellAccumulator）は grow 軸で存続（表示選択肢のみ削除）。
      //   actor.setParams が mode を受けて _setReplay/_applySessions の復元経路を再利用し状態遷移する。
      //   order は旧 replay の位置（1）＝表示系 group の先頭。
      // ISSUE-082（依頼者指示 2026-07-15）: リプレイモードは present（本指標）から撤去。
      //   リプレイ機構（actor の setReplayCursor/replay bar 等）は replay_ui（別アプリ）が依存する
      //   共有資産のため温存し、本 catalog の選択肢と composition の配線のみを撤去する。
      //   保存済み mode='replay'／legacy replay:true は controller._deriveMode が 'normal' へ正規化。
      param('mode', ParamType.ENUM, 'normal', [], ['normal', 'sessions'], {
        group: 'group.display', order: 1, label: '表示モード', controlType: 'segmented',
        enumLabels: {
          normal: '通常', sessions: '日別プロファイル',
        },
        tooltip: '通常＝全期間累積プロファイル（成長時は現在足forming で足内成長）／日別プロファイル＝各営業日を列で分割表示（成長時は当日タイルが因果成長）',
      }),
      // dispbp: 表示幅（bp・価格比 1bp=0.01%・FLOAT 自由入力・ISSUE-079 依頼者承認 2026-07-15）。
      //   旧 解像度トグル（resmode）＋ビン（bins）＋レンジpt（range）を**一本化**して置換する。
      //   絶対 pt/本数指定は価格水準で意味が変わる（時代ドリフト）ため、比率（bp）で表示粗さを
      //   指定する（「計算は 1bp 固定・見せ方は自由」の二層構造。zp の内部格子 1bp が情報の下限）。
      //   client への写像は actor が「最新終値 × bp/1e4 → barw(pt)」で行う（backend 変更なし・
      //   既存 &barw= 経路を再利用＝時代整合は要求時の現在価格で自動確保）。
      //   dwell の内部格子は絶対 10pt のまま（現在価格で約1.5bp・過去ほど粗い下限＝既知の残課題）。
      param('dispbp', ParamType.FLOAT, 3.0,
        [{ kind: ConstraintKind.MIN_VALUE, operands: ['dispbp', 1], messageKey: 'err.dispbp.min' }], null, {
          group: 'group.calc', order: 4, label: '表示幅(bp)', step: 0.5, min: 1,
          // ISSUE-070→081: tf-period 列描画時（列は固定生解像度＝bp が効かない）は行ごと非表示。
          conditionalVisible: _mpResolutionEnabled,
          tooltip: '価格帯 1 行の幅を価格比（bp=0.01%）で指定。1bp が下限（zp の計算格子）。値を小さくするほど精細・大きくするほど滑らか。旧「ビン/レンジ(pt)」を置換（絶対値指定は価格水準で意味が変わるため比率へ統一）',
        }),
      // va: バリューエリア比率（FLOAT・既定0.70・0<va<1 RANGE_OPEN）。
      param('va', ParamType.FLOAT, 0.70, [{ kind: ConstraintKind.RANGE_OPEN, operands: [0, 'va', 1], messageKey: 'err.va.range' }], null, { group: 'group.calc', order: 2, step: 0.01, min: 0, max: 1, label: 'バリューエリア' }),
      // limit（対象本数）param は削除済＝MP は常に全期間集計（backend は limit 省略時＝全件集計）。
      // src: 集計原子（ENUM・既定 zp=超過占有 z(p)〔依頼者指示 2026-07-12 で candle から昇格〕/
      //   dwell=実ティック滞在 / m1=tick数 /
      //   zp=超過占有 z(p)＝Null B 帰無に対する分単位滞在の超過スコア。検定パイプライン Step1-5 で
      //   実データ検証済み・POC は POC*=argmax z）。
      //   client.buildMarketProfileUrl が受理し URL の &src= に付与する（省略時はサーバ既定 candle）。
      // candle（足レンジ TPO）と m1（tick数＝生ティック個数・セッション非認識）は選択肢から**非表示**
      //   （依頼者指示 2026-07-12）。candle は原子として最も粗く、m1 は時間帯の配信密度のクセを差し引かない
      //   生カウントで zp が帰無で除去する交絡そのもの＝いずれもティック由来 src（dwell/zp）に情報量で劣後。
      //   backend の src 対応（controller の _ALLOWED_SRC）は candle/m1 とも温存し、将来ティックデータを
      //   受信できないデータセットのフォールバックやデバッグ用途での再有効化を検討する。残る選択肢は
      //   dwell（実滞在秒）と zp（超過占有）の 2 つ。
      // ISSUE-076（B案・依頼者選択 2026-07-13）: 日別×1m/5m の zp は周期列でなく日単位タイルへ
      //   フォールバックする（z は短周期で統計が成立しない・ISSUE-060）。ソース切替で表示粒度が
      //   黙って変わり混乱するとの依頼者指摘を受け、tooltip で挙動差を明記する（選択は許容＝
      //   1分足で日別 z タイルを見る使い方は残す）。
      param('src', ParamType.ENUM, MP_DEFAULT_SOURCE, [], MP_SELECTABLE_SOURCES, {
        group: 'group.calc', order: 1, label: 'ソース',
        enumLabels: mpSourceEnumLabels(),
        tooltip: '滞在時間＝実ティックの滞在秒（日別では全時間足で周期ごとの列を表示）／超過占有z(p)＝偶然比の異常度（zは15分以上の周期でのみ統計が成立するため、日別×1分/5分足では選択不可。日単位のzは日足チャートの日別で確認）',
        // ISSUE-080: 日別×1m/5m では該当ソースの option を無効化（灰色・選択不可）。代替粒度は出さない。
        //   ソース別の session ブロック tf は記述子（blockedSessionTfs）が単一情報源。
        //   ctx 不在（A方式・単体テスト）は制限しない（timeframe を知り得ないため安全側＝有効）。
        optionEnable: (value, values, ctx) => !mpDisplayMode(values.mode).splitByDay
          || !ctx || ctx.timeframe == null
          || !mpSourceCapability(value).blockedSessionTfs.has(ctx.timeframe),
      }),

      // period: 計測窓（ENUM・既定 'all'＝全期間・ISSUE-071 (b)案）。'day'＝当日始端からの窓で計測する
      //   （client が from=当日始端 を &from= へ付与し backend が candles を time>=from に限定する既存機構）。
      //   zp 専用に表示する（conditionalVisible src=zp）: 全期間Σz合成では当日の成長が z_max 正規化に
      //   埋没して視認不能（実測 0.05%/分）だが、当日窓なら当日単独の z（実測 バー長4.6%/分・約90倍）を
      //   ライブで視認できる。dwell は成長時に forming 経路が当日絞り済み（ISSUE-065）のため本 param 対象外。
      //   帰無（偶然の期待値/ばらつき）は窓と独立に各日の直前 NULL_HIST_DAYS 完了日から構築されるため、
      //   当日窓でも z の統計的品質は不変。通常モード×固定周期 tf（1m..1D）でのみ有効（_mpPeriodEnabled）。
      param('period', ParamType.ENUM, 'all', [], ['all', 'day'], {
        group: 'group.calc', order: 3, label: '期間',
        // ISSUE-081: zp×通常×対応 tf のときだけ表示（旧: src 条件で表示＋mode/tf 条件でグレーアウト）。
        conditionalVisible: (values, ctx) => mpSourceCapability(values.src).hasPeriodWindow
          && _mpPeriodEnabled(values, ctx),
        enumLabels: { all: '全期間', day: '当日' },
      }),
    ],
    series: [
      new SeriesDef({ kind: SeriesKind.HORIZONTAL_LINE, sourceColumn: null, seriesName: 'market_profile', dynamic: false }),
    ],
    compute: { computeId: 'market_profile', requiredColumns: OHLC, timeRequired: false, backendParam: null, variants: ['default'] },
  });
}
