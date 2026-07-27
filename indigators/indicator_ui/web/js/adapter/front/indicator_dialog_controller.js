// indicator_dialog_controller.js — 指標追加ダイアログ（一覧・絞り込み・開閉）ロール（ISSUE-181・SRP）。
//
// 設計入力（ISSUE-181）: IndicatorController は 5 アクター同居の神クラスで、その 1 つが
//   「指標追加ダイアログの絞り込み UI（タブ / カテゴリ / 検索 / お気に入りのみ）と開閉状態」だった
//   （旧 indicator_controller.js:900-995 の DOM 配線・ダイアログ部）。変更要求の出所は
//   「ダイアログの操作系（UI）」のみで、compute オーケストレーション・永続化・描画とは独立している。
//
// 状態も一緒に移す（ISSUE-181 対応方針・参照実装 mp_primitive_roles.js の分割手法に倣う）:
//   絞り込み UI 状態 `_filter` は本協働子が所有する（host のフィールドではなくなった）。host 側に
//   残るのは facade の純状態（_state）と View（_legendView）＝別アクターの持ち物のみ。
//
// host 契約（IndicatorDialogHost）が要求する最小メンバー:
//   field : _state（favorites / uiState の read/write）・_legendView（DOM 構築の委譲先）・
//           _el（bind() 後のみ在席・dialog 要素の在席ガード）
//   method: _label / _defaultVariant / _setActive / toggleFavorite / applyIndicator /
//           _closeDialog / _renderDialogList
//   ※ host 経由で呼ぶのは subclass の override（replay 等）を尊重するため。
//
// ★ upstream JS API（addLineSeries 等）は一切参照しない（indicator_controller.js と同一規律）。

import { listForView } from '../../usecase/facade.js';

export class IndicatorDialogController {
  constructor(host) {
    this._host = host;
    // ダイアログ絞り込み UI 状態（host から移送・本協働子が所有する）。
    this._filter = { tab: 'indicator', category: null, query: '', favoriteOnly: false };
  }

  // bind() が解決した DOM 参照（_el）へダイアログ操作系のイベントを配線する。
  //   要素不在（node 単体テスト・部分 DOM）は個別にスキップする（従来ガードと同一）。
  bindElements(e) {
    const host = this._host;
    if (e.openBtn) {
      e.openBtn.addEventListener('click', () => host._openDialog());
    }
    if (e.closeBtn) {
      e.closeBtn.addEventListener('click', () => host._closeDialog());
    }
    if (e.search) {
      e.search.addEventListener('input', (ev) => { this._filter.query = ev.target.value; host._renderDialogList(); });
    }
    for (const t of e.tabs ?? []) {
      t.addEventListener('click', () => { host._setActive(e.tabs, t); this._filter.tab = t.dataset.tab; host._renderDialogList(); });
    }
    for (const c of e.cats ?? []) {
      c.addEventListener('click', () => {
        host._setActive(e.cats, c);
        this._filter.category = c.dataset.category || null;
        this._filter.favoriteOnly = c.dataset.category === '__favorites__';
        host._renderDialogList();
      });
    }
  }

  // ダイアログ開: DOM の is-open トグルは View へ委譲。uiState 更新・リスト再描画はここで行う
  //   （dialog 要素在席時のみ状態を進める従来ガードを保持＝挙動不変）。
  open() {
    const host = this._host;
    if (host._el?.dialog) {
      host._legendView.setDialogOpen(true);
      host._state.uiState = { ...host._state.uiState, dialogOpen: true };
      host._renderDialogList();
    }
  }

  close() {
    const host = this._host;
    if (host._el?.dialog) {
      host._legendView.setDialogOpen(false);
      host._state.uiState = { ...host._state.uiState, dialogOpen: false };
    }
  }

  // ダイアログの指標リストを再描画する。行の view-model（label/category/favorite）＋コールバックを
  //   組み立て、DOM 構築は IndicatorLegendView へ委譲する（ISSUE-038・SRP 是正）。挙動不変:
  //   お気に入り絞り込み（listForView）・star の stopPropagation・row クリックの apply+close を保持。
  renderList() {
    const host = this._host;
    const favorites = host._state.favorites;
    const defs = listForView({ ...this._filter, favorites });
    const rows = defs.map((def) => ({
      label: host._label(def),
      category: (def.category?.nameKey ?? '').split('.').pop(),
      favorite: favorites.includes(def.id),
      onToggleFavorite: () => host.toggleFavorite(def.id),
      onPick: () => { host.applyIndicator(def.id, host._defaultVariant(def)); host._closeDialog(); },
    }));
    host._legendView.renderDialogList(rows);
  }
}
