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
