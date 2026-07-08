// IndicatorLegendView（adapter/front/indicator_legend_view.js）。
//
// 設計入力: ISSUE-038（IndicatorController の SRP 違反是正・View 描画の分離）。
//   凡例行 / お気に入り / ダイアログリストの「純 DOM 構築」を IndicatorController から切り出した
//   adapter 層ビュー。controller は行の view-model（label / visible / favorite / category）＋
//   コールバック（onEye/onGear/onClose/onToggleFavorite/onPick）を注入し、ビューは DOM を構築して
//   イベント時にコールバックを発火するだけ。オーケストレーション・状態・永続化・ハンドラ本体
//   （_onGear 等）は controller に残す（本ビューは持たない）。
//
// 設計方針（crosshair_readout_view.js と同方針・YAGNI）:
//   - usecase/domain を参照しない（adapter 層・Presenter 抽象は作らない）。
//   - lightweight-charts（upstream）に触れない。
//   - DOM は注入（document）。要素は id で解決する。テストは fake document を渡す。
//   - 対象要素が不在でもクラッシュしない（防御的・空表示）。

export class IndicatorLegendView {
  // document: DOM 実装（注入）。null 可（node 単体テスト・DOM 不在時は各メソッドが no-op）。
  constructor({ document: doc = null } = {}) {
    this._document = doc;
  }

  _byId(id) {
    const doc = this._document;
    if (!doc || typeof doc.getElementById !== 'function') {
      return null;
    }
    return doc.getElementById(id);
  }

  // 凡例（#legend）を描画する。rows: [{ label, visible, onEye, onGear, onClose }]。
  //   旧 IndicatorController._renderLegend の DOM 構築部を byte 等価で移設したもの。
  renderLegend(rows) {
    const doc = this._document;
    const legend = this._byId('legend');
    if (!doc || !legend) {
      return;
    }
    legend.innerHTML = '';
    for (const r of rows ?? []) {
      const row = doc.createElement('div');
      row.className = 'legend-row';

      const label = doc.createElement('span');
      label.className = 'legend-label';
      label.textContent = r.label;

      const eye = doc.createElement('button');
      eye.className = 'legend-eye';
      eye.title = r.visible ? '非表示にする' : '表示する';
      eye.textContent = r.visible ? '👁' : '🙈';
      eye.addEventListener('click', () => r.onEye());

      const gear = doc.createElement('button');
      gear.className = 'legend-gear';
      gear.title = '設定';
      gear.textContent = '⚙';
      gear.addEventListener('click', () => r.onGear());

      const close = doc.createElement('button');
      close.className = 'legend-remove';
      close.title = '削除';
      close.textContent = '✕';
      close.addEventListener('click', () => r.onClose());

      row.append(label, eye, gear, close);
      legend.append(row);
    }
  }

  // ダイアログの指標リスト（#indicator-list）を描画する。
  //   rows: [{ label, category, favorite, onToggleFavorite, onPick }]。
  //   旧 IndicatorController._renderDialogList の DOM 構築部を byte 等価で移設したもの。
  renderDialogList(rows) {
    const doc = this._document;
    const list = this._byId('indicator-list');
    if (!doc || !list) {
      return;
    }
    list.innerHTML = '';
    for (const r of rows ?? []) {
      const row = doc.createElement('div');
      row.className = 'ind-row';

      const star = doc.createElement('button');
      star.className = 'ind-fav' + (r.favorite ? ' is-on' : '');
      star.textContent = r.favorite ? '★' : '☆';
      star.addEventListener('click', (ev) => { ev.stopPropagation(); r.onToggleFavorite(); });

      const name = doc.createElement('span');
      name.className = 'ind-name';
      name.textContent = r.label;

      const cat = doc.createElement('span');
      cat.className = 'ind-cat';
      cat.textContent = r.category;

      row.append(star, name, cat);
      row.addEventListener('click', () => r.onPick());
      list.append(row);
    }
  }

  // ダイアログ（#indicator-dialog）の is-open クラスを純トグルする（開閉の DOM 表現のみ）。
  //   uiState 更新・_renderDialogList 呼び出しは controller に残す（本メソッドは DOM だけ）。
  setDialogOpen(open) {
    const dialog = this._byId('indicator-dialog');
    if (!dialog) {
      return;
    }
    if (open) {
      dialog.classList.add('is-open');
    } else {
      dialog.classList.remove('is-open');
    }
  }

  // グループ内で active 要素のみ is-active を付与する純 DOM ヘルパ（旧 _setActive）。
  setActive(group, active) {
    for (const el of group ?? []) {
      el.classList.toggle('is-active', el === active);
    }
  }
}
