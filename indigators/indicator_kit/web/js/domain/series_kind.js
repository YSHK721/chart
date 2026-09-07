// series_kind.js — 系列出力種別（line / histogram / horizontal_line）の能力台帳（ISSUE-134 OCP）。
//
// 従来 kind === 'line' / kind === 'histogram' / kind !== 'histogram' の直接比較が
//   indicator_controller.js / chart_renderer.js / properties_dialog.js に散在し、新 series 種別追加時に
//   3 ファイル同時修正が必要だった。本台帳へ per-kind の能力（capability）を集約し、各呼出面は
//   能力名で参照する（新種別は本台帳 1 箇所への追記で完結する＝OCP）。
//
// 能力の定義（各能力は従来の boolean 比較と 1:1 に一致させる＝挙動変更ゼロ）:
//   - tailUpdatable   : 末尾K差分反映（updateSeriesTail）の対象か（旧 kind==='line'||'histogram'）。
//   - seriesType      : lightweight-charts の系列種別 'line'|'histogram'|null（priceLine は null）。
//                       renderer が LineSeries/HistogramSeries 定義を選ぶ（旧 kind==='histogram'?H:L）。
//   - appliesLineStyle: チャート系列へ lineWidth/lineStyle を適用するか（旧 renderer kind==='line'）。
//   - supportsHeat    : バー別ヒート配色（heat）を持ち得るか（旧 kind==='histogram'）。
//   - overlayReadout  : overlay（pane0 重畳）時に読取欄へ載せる対象か（旧 renderer kind==='line'）。
//   - editableLineStyle: プロパティダイアログで線幅/線種を編集可能か（旧 kind!=='histogram'）。
//   - renderRoute     : _draw が振り分ける描画経路 'line'|'histogram'|'horizontal'（旧 kind 別 filter）。
//                       未知 kind は null＝どの経路にも乗らない（従来: 3 種のいずれにも一致せず非描画）。

export const SERIES_KINDS = Object.freeze({
  line: Object.freeze({
    tailUpdatable: true,
    seriesType: 'line',
    appliesLineStyle: true,
    supportsHeat: false,
    overlayReadout: true,
    editableLineStyle: true,
    renderRoute: 'line',
  }),
  histogram: Object.freeze({
    tailUpdatable: true,
    seriesType: 'histogram',
    appliesLineStyle: false,
    supportsHeat: true,
    overlayReadout: false,
    editableLineStyle: false,
    renderRoute: 'histogram',
  }),
  // level_dash: ローソク足幅の水平ダッシュ（同値 4 値の Candlestick で描く）。
  //   payload 契約は line と同一（{time, value}）で、OHLC への展開は表示層が担う
  //   （back の payload 形状を増やさない＝既存 3 種別の契約に非波及）。
  //   tailUpdatable=false: 末尾差分更新は {time,value} を series.update へ渡す経路であり
  //   Candlestick 系列とは形が合わない。full 再描画のみを対象とする。
  level_dash: Object.freeze({
    tailUpdatable: false,
    seriesType: 'level_dash',
    appliesLineStyle: false,
    supportsHeat: false,
    overlayReadout: false,
    editableLineStyle: false,
    renderRoute: 'level_dash',
  }),
  horizontal_line: Object.freeze({
    tailUpdatable: false,
    seriesType: null,
    appliesLineStyle: false,
    supportsHeat: false,
    overlayReadout: false,
    editableLineStyle: true,
    renderRoute: 'horizontal',
  }),
});

// 未知 kind のフォールバック能力（従来の直接比較の結果と一致させる: === 'line'/'histogram' は false、
//   !== 'histogram' は true、どの描画経路にも乗らない）。到達しない設計だが全入力で従来挙動を保つ。
// 描画経路の台帳（ISSUE-270）。**dispatch の順序・呼び出すメソッド・呼び方**をここで宣言する。
//
// なぜ台帳へ出すか: かつて router 側が `routed` の初期化と 4 分岐の dispatch を直書きしており、
//   `SERIES_KINDS` へ種別を 1 行足しただけでは `routed[route]` が undefined になって
//   **例外も出さず黙って捨てられた**（描画されない）。経路の知識を 2 箇所に分けていたのが原因。
//   経路をここへ集約し、router は本表を上から順に回すだけにする。
//
//   route   : SERIES_KINDS[*].renderRoute が指す経路名
//   method  : ChartRenderer 側のメソッド名
//   perItem : true=要素ごとに 1 回呼ぶ / false=まとめて 1 回呼ぶ
//   payload : perItem のとき、要素から実引数を作る関数（未指定は要素そのもの）
//   opts    : true=第 3 引数に描画オプション（pane/name）を渡す
//
// **順序は z 順**。配列の順に描くため、並べ替えは表示の重なりを変える（従来順を保存すること）。
export const RENDER_ROUTES = Object.freeze([
  Object.freeze({ route: 'histogram', method: 'renderHistogram', perItem: false, opts: true }),
  Object.freeze({ route: 'line', method: 'renderLine', perItem: false, opts: true }),
  Object.freeze({ route: 'level_dash', method: 'renderLevelDash', perItem: false, opts: true }),
  Object.freeze({
    route: 'horizontal',
    method: 'renderHorizontal',
    perItem: true,
    opts: false,
    payload: (h) => (h.lines ?? []),
  }),
]);

const _UNKNOWN_KIND = Object.freeze({
  tailUpdatable: false,
  seriesType: 'line',
  appliesLineStyle: false,
  supportsHeat: false,
  overlayReadout: false,
  editableLineStyle: true,
  renderRoute: null,
});

// kind → 能力台帳エントリ（未知 kind は従来比較を保つフォールバックを返す）。
export function seriesKind(kind) {
  return SERIES_KINDS[kind] ?? _UNKNOWN_KIND;
}
