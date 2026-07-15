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

// tf-period が日別列を描く対応 tf（列描画時は解像度が GRID_W 固定＝resmode/bins/range 無効）。
//   ISSUE-070。count 列は 1m..1D、zp 列は 15m..1D 対応（backend の周期退化ガードと一致）。
const _MP_PLAYER_TF = new Set(['1m', '5m', '15m', '30m', '1h', '4h', '1D']);
const _MP_ZP_TF = new Set(['15m', '30m', '1h', '4h', '1D']);

// ISSUE-080（依頼者裁定 2026-07-15）: 日別（周期）プロファイルで zp を選べない時間足。
//   原則「列の周期＝チャートの時間足。作れないソースは出さない」＝z は 1m/5m 周期で統計が
//   成立せず（原子=1分1点）、代替粒度（日タイル・15m列）を黙って出すのは粒度契約違反。
//   actor の実行時ガード（非対応組合せは fetch も描画もしない）と単一情報源で共有する。
export const MP_ZP_SESSIONS_BLOCKED_TFS = new Set(['1m', '5m']);

// tf-period が日別プロファイル列を描く状態か（＝解像度パラメータが無効な状態）。
//   条件: served(B方式) かつ mode=sessions かつ対応 tf（src=zp は 15m..1D 限定）。ctx は
//   { timeframe, servedMode } を受ける（gear ダイアログが現 timeframe/mode を注入）。
function _mpTfPeriodDrawsColumns(values, ctx) {
  if (!ctx || ctx.servedMode !== 'b') { return false; }
  if (values.mode !== 'sessions') { return false; }
  const tf = ctx.timeframe;
  if (!_MP_PLAYER_TF.has(tf)) { return false; }
  if (values.src === 'zp' && !_MP_ZP_TF.has(tf)) { return false; }
  return true;
}

// 解像度パラメータ（resmode/bins/range）の enabled 述語: tf-period 列描画時のみ無効（グレーアウト）。
//   通常モード・非対応tf の日別（タイル描画）・A方式では解像度が有効なので enabled=true。
const _mpResolutionEnabled = (values, ctx) => !_mpTfPeriodDrawsColumns(values, ctx);

// 期間パラメータ（period）の enabled 述語: 通常モードかつ固定周期 tf（1m..1D）でのみ有効。
//   リプレイ（as-seen-at-t の窓は T が決める）・日別（各営業日で分割済み）では計測窓の意味が
//   重複/矛盾するためグレーアウト。1W/1M は最新バー期間が「当日」を包含し窓が退化するため無効。
//   ctx 不在（A方式・単体テスト）は timeframe 判定をスキップ（mode 条件のみ）。
const _mpPeriodEnabled = (values, ctx) => values.mode === 'normal'
  && (!ctx || ctx.timeframe == null || _MP_PLAYER_TF.has(ctx.timeframe));

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
      // dispbp: 表示幅（bp・価格比 1bp=0.01%・FLOAT 自由入力・ISSUE-079 依頼者承認 2026-07-15）。
      //   旧 解像度トグル（resmode）＋ビン（bins）＋レンジpt（range）を**一本化**して置換する。
      //   絶対 pt/本数指定は価格水準で意味が変わる（時代ドリフト）ため、比率（bp）で表示粗さを
      //   指定する（「計算は 1bp 固定・見せ方は自由」の二層構造。zp の内部格子 1bp が情報の下限）。
      //   client への写像は actor が「最新終値 × bp/1e4 → barw(pt)」で行う（backend 変更なし・
      //   既存 &barw= 経路を再利用＝時代整合は要求時の現在価格で自動確保）。
      //   dwell の内部格子は絶対 10pt のまま（現在価格で約1.5bp・過去ほど粗い下限＝既知の残課題）。
      param('dispbp', ParamType.FLOAT, 3.0,
        [{ kind: ConstraintKind.MIN_VALUE, operands: ['dispbp', 1], messageKey: 'err.dispbp.min' }], null, {
          group: 'group.calc', order: 0, label: '表示幅(bp)', step: 0.5, min: 1,
          conditionalEnable: _mpResolutionEnabled, // ISSUE-070: tf-period 列描画時グレーアウト。
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
      param('src', ParamType.ENUM, 'zp', [], ['dwell', 'zp'], {
        group: 'group.calc', order: 4, label: 'ソース',
        enumLabels: {
          dwell: '滞在時間(実ティック)', zp: '超過占有z(p)',
        },
        tooltip: '滞在時間＝実ティックの滞在秒（日別では全時間足で周期ごとの列を表示）／超過占有z(p)＝偶然比の異常度（zは15分以上の周期でのみ統計が成立するため、日別×1分/5分足では選択不可。日単位のzは日足チャートの日別で確認）',
        // ISSUE-080: 日別×1m/5m では zp の option を無効化（灰色・選択不可）。代替粒度は出さない。
        //   ctx 不在（A方式・単体テスト）は制限しない（timeframe を知り得ないため安全側＝有効）。
        optionEnable: (value, values, ctx) => value !== 'zp'
          || values.mode !== 'sessions'
          || !ctx || ctx.timeframe == null
          || !MP_ZP_SESSIONS_BLOCKED_TFS.has(ctx.timeframe),
      }),

      // period: 計測窓（ENUM・既定 'all'＝全期間・ISSUE-071 (b)案）。'day'＝当日始端からの窓で計測する
      //   （client が from=当日始端 を &from= へ付与し backend が candles を time>=from に限定する既存機構）。
      //   zp 専用に表示する（conditionalVisible src=zp）: 全期間Σz合成では当日の成長が z_max 正規化に
      //   埋没して視認不能（実測 0.05%/分）だが、当日窓なら当日単独の z（実測 バー長4.6%/分・約90倍）を
      //   ライブで視認できる。dwell は成長時に forming 経路が当日絞り済み（ISSUE-065）のため本 param 対象外。
      //   帰無（偶然の期待値/ばらつき）は窓と独立に各日の直前 NULL_HIST_DAYS 完了日から構築されるため、
      //   当日窓でも z の統計的品質は不変。通常モード×固定周期 tf（1m..1D）でのみ有効（_mpPeriodEnabled）。
      param('period', ParamType.ENUM, 'all', [], ['all', 'day'], {
        group: 'group.calc', order: 5, label: '期間',
        conditionalVisible: { when: { param: 'src', equals: 'zp' } },
        conditionalEnable: _mpPeriodEnabled,
        enumLabels: { all: '全期間', day: '当日' },
      }),
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
      param('mode', ParamType.ENUM, 'normal', [], ['normal', 'replay', 'sessions'], {
        group: 'group.display', order: 1, label: '表示モード', controlType: 'segmented',
        enumLabels: {
          normal: '通常', replay: 'リプレイ', sessions: '日別プロファイル',
        },
        tooltip: '通常＝全期間累積プロファイル（成長時は現在足forming で足内成長）／リプレイ＝時間カーソルで当時を再生／日別プロファイル＝各営業日を列で分割表示（成長時は当日タイルが因果成長）',
      }),
    ],
    series: [
      new SeriesDef({ kind: SeriesKind.HORIZONTAL_LINE, sourceColumn: null, seriesName: 'market_profile', dynamic: false }),
    ],
    compute: { computeId: 'market_profile', requiredColumns: OHLC, timeRequired: false, backendParam: null, variants: ['default'] },
  });
}
