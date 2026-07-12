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
      // resmode: 解像度指定モード（ENUM・既定 bins）。試作 prototype_260630-01 の解像度トグル
      //   （ビン ⇄ レンジ）を移植。ui.controlType='segmented' で横並びセグメントボタンとして描画し、
      //   押した側の入力（bins / range）だけを conditionalVisible で表示する。order は bins/range より前。
      param('resmode', ParamType.ENUM, 'bins', [], ['bins', 'range'], {
        group: 'group.calc', order: 0, label: '解像度', controlType: 'segmented',
        enumLabels: { bins: 'ビン', range: 'レンジ' },
        // ISSUE-070: tf-period が日別列を描くとき（日別×対応tf）は解像度が GRID_W 固定で無効＝グレーアウト。
        conditionalEnable: _mpResolutionEnabled,
      }),
      // bins: ヒストグラム区間数（ENUM プリセット・既定 '60'）。試作 prototype_260630-01/web/index.html:30-34 の
      //   <select>（option 30 / 60(selected) / 100）に忠実。数値自由入力(INT)からプリセットへ変更し、レンジ(range)と
      //   同じく select・同位置(order 1)で描く。全プリセットが妥当なため MIN_VALUE 制約は不要（除去）。
      //   解像度=ビン（resmode=bins）のときのみ表示（レンジ指定時は非表示 = conditionalVisible トグル）。
      //   client.buildMarketProfileUrl が文字列プリセット（'30'/'60'/'100'）を &bins= へ付与する。
      param('bins', ParamType.ENUM, '60', [], ['30', '60', '100'], {
        group: 'group.calc', order: 1, label: 'ビン',
        conditionalVisible: { when: { param: 'resmode', equals: 'bins' } },
        conditionalEnable: _mpResolutionEnabled, // ISSUE-070: tf-period 列描画時グレーアウト。
        enumLabels: { 30: '30', 60: '60', 100: '100' },
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
      param('src', ParamType.ENUM, 'zp', [], ['dwell', 'zp'], {
        group: 'group.calc', order: 4, label: 'ソース',
        enumLabels: {
          dwell: '滞在時間(実ティック)', zp: '超過占有z(p)',
        },
      }),
      // range: レンジ(pt) の直接指定（ENUM・既定 100）。試作 prototype_260630-01 の range セレクタを移植。
      //   解像度=レンジ（resmode=range）のときのみ表示。値は client が &barw= を付与し backend が
      //   n_bins = round(窓幅/barw) を算出する（bins は送らない）。
      //   order は bins と同一(1)＝同じ位置で入れ替わる（トグル時に下の va/src がズレず認知負荷を抑える）。
      param('range', ParamType.ENUM, '100', [], ['25', '50', '100', '250', '500'], {
        group: 'group.calc', order: 1, label: 'レンジ(pt)',
        conditionalVisible: { when: { param: 'resmode', equals: 'range' } },
        conditionalEnable: _mpResolutionEnabled, // ISSUE-070: tf-period 列描画時グレーアウト。
        enumLabels: { 25: '25', 50: '50', 100: '100', 250: '250', 500: '500' },
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
