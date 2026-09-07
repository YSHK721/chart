// dialog_drag_controller.js — ダイアログのドラッグ移動（Pointer Events・ISSUE-502 段階 4D）。
//
// 責務（単一の変更軸）: **掴んだ点からの移動量をパネルの位置へ反映する**こと。
//   ダイアログの中身（タブ・フォーム・検証）を一切知らない。移動規則が変わる理由
//   （掴める範囲・慣性・画面外拘束など）は、フォームの規則が変わる理由とは独立している。
//
// 抽出前は PropertiesDialog が _drag / _offset の 2 状態と pointerdown/move/up の配線を
//   直接持っており、「DOM 構築・イベント配線のみ」という同ファイルの宣言と実装が食い違っていた。
//
// ★ upstream JS API（addLineSeries / applyOptions 等）は一切参照しない
//   （properties_dialog.js §8.4 と同一規律）。使うのはブラウザ標準の Pointer Events のみ。
export class DialogDragController {
  // { document, panel }
  //   document : ドラッグ中の pointermove/pointerup を受ける document
  //   panel    : 動かす要素、または要素を返す関数（open 前は null を返してよい）
  constructor({ document: doc, panel }) {
    this._doc = doc;
    this._panel = panel;
    this._drag = null;
    this._offset = { x: 0, y: 0 };
  }

  // 現在の移動量（左上からの px オフセット）。
  get offset() {
    return this._offset;
  }

  _target() {
    return typeof this._panel === 'function' ? this._panel() : this._panel;
  }

  // pointerdown ハンドラ。掴んだ点を基準に move/up を document へ登録する
  //   （パネル外へ出ても追従し、離した時点で必ず解除する）。
  start(ev) {
    if (!this._target()) {
      return;
    }
    this._drag = {
      startX: ev.clientX,
      startY: ev.clientY,
      baseX: this._offset.x,
      baseY: this._offset.y,
    };
    const move = (e) => this._move(e);
    const up = () => {
      this._doc.removeEventListener('pointermove', move);
      this._doc.removeEventListener('pointerup', up);
      this._drag = null;
    };
    this._doc.addEventListener('pointermove', move);
    this._doc.addEventListener('pointerup', up);
  }

  _move(ev) {
    if (!this._drag) {
      return;
    }
    this._offset = {
      x: this._drag.baseX + (ev.clientX - this._drag.startX),
      y: this._drag.baseY + (ev.clientY - this._drag.startY),
    };
    const panel = this._target();
    if (panel) {
      panel.style.transform = `translate(${this._offset.x}px, ${this._offset.y}px)`;
    }
  }
}
