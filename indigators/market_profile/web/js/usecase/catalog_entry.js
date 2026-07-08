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
      }),
      // bins: ヒストグラム区間数（ENUM プリセット・既定 '60'）。試作 prototype_260630-01/web/index.html:30-34 の
      //   <select>（option 30 / 60(selected) / 100）に忠実。数値自由入力(INT)からプリセットへ変更し、レンジ(range)と
      //   同じく select・同位置(order 1)で描く。全プリセットが妥当なため MIN_VALUE 制約は不要（除去）。
      //   解像度=ビン（resmode=bins）のときのみ表示（レンジ指定時は非表示 = conditionalVisible トグル）。
      //   client.buildMarketProfileUrl が文字列プリセット（'30'/'60'/'100'）を &bins= へ付与する。
      param('bins', ParamType.ENUM, '60', [], ['30', '60', '100'], {
        group: 'group.calc', order: 1, label: 'ビン',
        conditionalVisible: { when: { param: 'resmode', equals: 'bins' } },
        enumLabels: { 30: '30', 60: '60', 100: '100' },
      }),
      // va: バリューエリア比率（FLOAT・既定0.70・0<va<1 RANGE_OPEN）。
      param('va', ParamType.FLOAT, 0.70, [{ kind: ConstraintKind.RANGE_OPEN, operands: [0, 'va', 1], messageKey: 'err.va.range' }], null, { group: 'group.calc', order: 2, step: 0.01, min: 0, max: 1, label: 'バリューエリア' }),
      // limit（対象本数）param は削除済＝MP は常に全期間集計（backend は limit 省略時＝全件集計）。
      // src: 集計原子（ENUM・既定 candle=足レンジ TPO・後方互換 / dwell=実ティック滞在 / m1=tick数）。
      //   client.buildMarketProfileUrl が受理し URL の &src= に付与する（省略時はサーバ既定 candle）。
      param('src', ParamType.ENUM, 'candle', [], ['candle', 'dwell', 'm1'], {
        group: 'group.calc', order: 4, label: 'ソース',
        enumLabels: { candle: '足レンジ', dwell: '滞在時間(実ティック)', m1: 'tick数' },
      }),
      // range: レンジ(pt) の直接指定（ENUM・既定 100）。試作 prototype_260630-01 の range セレクタを移植。
      //   解像度=レンジ（resmode=range）のときのみ表示。値は client が &barw= を付与し backend が
      //   n_bins = round(窓幅/barw) を算出する（bins は送らない）。
      //   order は bins と同一(1)＝同じ位置で入れ替わる（トグル時に下の va/src がズレず認知負荷を抑える）。
      param('range', ParamType.ENUM, '100', [], ['25', '50', '100', '250', '500'], {
        group: 'group.calc', order: 1, label: 'レンジ(pt)',
        conditionalVisible: { when: { param: 'resmode', equals: 'range' } },
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
