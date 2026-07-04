// CrosshairReadoutView（adapter/front/crosshair_readout_view.js）。
//
// クロスヘア価格読み取り欄（左上固定オーバーレイ）の DOM 描画を担う adapter 層ビュー。
//   ChartRenderer が構築する読み取り DTO（プレーンなデータ構造）を受け取り、
//   日時 + 始値/高値/安値/終値 + overlay 各行（系列色付き）を要素へ描画する。
//
// 設計方針:
//   - usecase/domain を参照しない（adapter 層・Presenter 抽象は作らない＝既存 _renderLegend と同方針・YAGNI）。
//   - lightweight-charts（upstream）に触れない。series 実体・lwc 型は DTO に含まれない（隔離維持）。
//   - DOM は注入（document / elementId）。テストは fake document を渡す。
//   - dto が null / ohlc null / overlays 空 / 対象要素不在でも安全（クラッシュしない・空表示）。

import { fmtValue } from './format.js';

// epoch 秒（lightweight-charts の UTCTimestamp）を ISO 風の日時文字列へ。
//   business-day オブジェクト等は防御的に String 化する。
function fmtTime(time) {
  if (time === null || time === undefined) {
    return '';
  }
  if (typeof time === 'number' && Number.isFinite(time)) {
    return new Date(time * 1000).toISOString().replace('T', ' ').slice(0, 16);
  }
  return String(time);
}

// OHLC 行のセル定義（描画順 O/H/L/C）。dto.ohlc のキー・CSS クラス・先頭ラベルを保持する。
const OHLC_CELLS = Object.freeze([
  { key: 'open', className: 'readout-o', label: 'O' },
  { key: 'high', className: 'readout-h', label: 'H' },
  { key: 'low', className: 'readout-l', label: 'L' },
  { key: 'close', className: 'readout-c', label: 'C' },
]);

export class CrosshairReadoutView {
  // document: DOM 実装（注入）。elementId: 描画先要素の id。
  constructor({ document, elementId }) {
    this._document = document ?? null;
    this._elementId = elementId;
  }

  _root() {
    const doc = this._document;
    if (!doc || typeof doc.getElementById !== 'function') {
      return null;
    }
    return doc.getElementById(this._elementId);
  }

  // 読み取り DTO を描画する。{ time, ohlc:{open,high,low,close}|null, overlays:[{name,value,color}] }。
  render(dto) {
    const root = this._root();
    if (!root) {
      return;
    }
    root.innerHTML = '';
    if (!dto) {
      return;
    }
    const doc = this._document;

    // 日時 + OHLC 行。
    if (dto.ohlc) {
      const ohlcRow = doc.createElement('div');
      ohlcRow.className = 'readout-ohlc';
      const time = doc.createElement('span');
      time.className = 'readout-time';
      time.textContent = fmtTime(dto.time);
      const cells = OHLC_CELLS.map(({ key, className, label }) => {
        const cell = doc.createElement('span');
        cell.className = className;
        cell.textContent = `${label} ${fmtValue(dto.ohlc[key])}`;
        return cell;
      });
      ohlcRow.append(time, ...cells);
      root.append(ohlcRow);
    }

    // overlay 各行（系列色付き）。
    for (const ov of dto.overlays ?? []) {
      const row = doc.createElement('div');
      row.className = 'readout-overlay';
      if (ov.color) {
        row.style.color = ov.color;
      }
      row.textContent = `${ov.name}: ${fmtValue(ov.value)}`;
      root.append(row);
    }
  }
}
