// color_theme_menu.js — 指標カラーテーマのドロップダウン（DOM アダプター・両アプリ共有）。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.1
//   §6.1（空マウント `<div class="color-theme-menu" id="color-theme-menu"></div>` は
//        `app_chrome_view.installChartToolbar` が生成し、項目 DOM は本モジュールが生成する。
//        index.html は 1 枚も触らない＝ISSUE-278 #16 の規約）、
//   §6.2（行構成＝「テーマなし（既定色）」固定行 → 区切り → 保存済み各行 →「新しいテーマを作成…」
//        →「管理（名前を変更・削除）…」／行クリックで即適用／`activeThemeId` 一致行は `is-active`／
//        固定行は削除も改名もできない／開閉挙動は時間足・テンプレートメニューと同一）、
//   §7.1（controller を知らない＝DIP）。
//
// 責務（SRP）: メニュー DOM の生成と開閉。テーマの適用・作成・管理そのものは注入コールバック
//   （onSelect / onCreate / onManage）へ委譲する（本モジュールは協働子を import しない＝DIP）。
//   DOM 不在（SSR・テスト最小 fake）は no-op。
//
// 「テーマなし（既定色）」が固定行である理由（§6.2）: 選択中テーマ無しは**テーマ集合の要素ではない**
//   （`activeThemeId = null`）。保存済み一覧の中に混ぜると、改名・削除の対象になり得る席ができる。
//   固定行は `themeId = ''`（空文字）で識別し、クリック時にのみ `null` へ写像する。空文字は
//   `dataset` が値を持たない状態（undefined）と区別できるため、`!== undefined` で行判定できる
//   （`chart_template_menu.js` の `tplBind === ''`＝紐付けなしと同一のイディオム）。
//
// 再描画: 一覧はビューモデル注入 `render(vm)` で更新し、かつ**開くたびに再描画**する（provide 注入時）。
//   適用・保存の後に協働子が `render()` を呼ぶ経路と、開くたびの再描画の 2 経路で選択状態が追随する。

import { installDocumentCloseHandler, removeDocumentCloseHandler } from './menu_document_close.js';

// 固定行（テーマなし＝既定色）の識別子。空文字は「保存済みテーマではない」ことを表す。
const NONE_ROW_ID = '';

export class ColorThemeMenu {
  /**
   * @param {object} opts
   * @param {object} opts.document        DOM 実装（注入。null 可＝no-op）。
   * @param {Array}  [opts.themes]        COLOR_THEME[]（宣言順に一覧表示）。
   * @param {?string}[opts.activeThemeId] 選択状態（is-active）にする themeId。null＝固定行。
   * @param {?function}[opts.provide]     開くたびに最新ビューモデルを取得するコールバック。
   * @param {?function}[opts.onSelect]    行クリック（themeId ／ 固定行は null）＝適用要求（UC-C02）。
   * @param {?function}[opts.onCreate]    「新しいテーマを作成…」クリック。
   * @param {?function}[opts.onManage]    「管理（名前を変更・削除）…」クリック。
   */
  constructor({
    document: doc = null, themes = [], activeThemeId = null,
    provide = null, onSelect = null, onCreate = null, onManage = null,
  } = {}) {
    this._doc = doc;
    this._themes = Array.isArray(themes) ? themes : [];
    this._activeThemeId = activeThemeId ?? null;
    this._provide = typeof provide === 'function' ? provide : null;
    this._onSelect = typeof onSelect === 'function' ? onSelect : null;
    this._onCreate = typeof onCreate === 'function' ? onCreate : null;
    this._onManage = typeof onManage === 'function' ? onManage : null;
    this._pop = null;
    this._list = null;
    this._docCloseHandler = null;
  }

  install() {
    const doc = this._doc;
    if (!doc || typeof doc.getElementById !== 'function' || typeof doc.createElement !== 'function') {
      return; // DOM 不在（SSR・テスト最小 fake）は no-op（chart_template_menu と同型）。
    }
    const mount = doc.getElementById('color-theme-menu');
    if (!mount || typeof mount.appendChild !== 'function') {
      return;
    }

    // トリガー（時間足・テンプレートメニューのトリガーと同型のボタン＋キャレット）。
    const trigger = doc.createElement('button');
    trigger.type = 'button';
    trigger.id = 'color-theme-menu-trigger';
    trigger.className = 'tb-interval color-theme-menu-trigger';
    trigger.title = '指標カラーテーマ';
    const label = doc.createElement('span');
    label.id = 'color-theme-menu-label';
    label.textContent = 'テーマ';
    const caret = doc.createElement('span');
    caret.className = 'color-theme-menu-caret';
    caret.textContent = '▾';
    trigger.append(label, caret);

    // ポップ（固定行と保存済み一覧のコンテナ ＋ 作成 ＋ 管理）。
    const pop = doc.createElement('div');
    pop.id = 'color-theme-menu-pop';
    pop.className = 'color-theme-menu-pop is-hidden';

    // 一覧コンテナ（固定行・区切り・保存済み行を _renderRows が作り直す）。
    const list = doc.createElement('div');
    list.className = 'color-theme-menu-list';
    pop.append(list);

    const create = doc.createElement('button');
    create.type = 'button';
    create.className = 'color-theme-menu-item color-theme-menu-action';
    create.dataset.themeAction = 'create';
    create.textContent = '新しいテーマを作成…';
    pop.append(create);

    const manage = doc.createElement('button');
    manage.type = 'button';
    manage.className = 'color-theme-menu-item color-theme-menu-action';
    manage.dataset.themeAction = 'manage';
    manage.textContent = '管理（名前を変更・削除）…';
    pop.append(manage);

    mount.appendChild(trigger);
    mount.appendChild(pop);
    this._pop = pop;
    this._list = list;
    this._renderRows();

    trigger.addEventListener('click', (e) => {
      if (e && typeof e.stopPropagation === 'function') {
        e.stopPropagation(); // document の外側クリッククローズに拾わせない。
      }
      this._toggle();
    });
    // 項目クリック（委譲）: 行＝適用要求、アクション＝作成／管理。いずれも閉じる。
    pop.addEventListener('click', (e) => {
      const t = this._resolveItem(e && e.target);
      const ds = t && t.dataset;
      if (!ds) {
        return;
      }
      if (ds.themeId !== undefined) {
        this._setOpen(false);
        this._onSelect?.(ds.themeId === NONE_ROW_ID ? null : ds.themeId);
        return;
      }
      if (ds.themeAction === 'create') {
        this._setOpen(false);
        this._onCreate?.();
        return;
      }
      if (ds.themeAction === 'manage') {
        this._setOpen(false);
        this._onManage?.();
      }
    });
    // 外側クリックで閉じる（ISSUE-169: 前 mount ぶんの document リスナを外してから張る）。
    this._docCloseHandler = () => this._setOpen(false);
    installDocumentCloseHandler(doc, 'color-theme', this._docCloseHandler);
    pop.addEventListener('pointerdown', (e) => {
      if (e && typeof e.stopPropagation === 'function') {
        e.stopPropagation();
      }
    });
  }

  // クリック対象から「項目要素」を解決する。行は印・名前の子 span を持つため、実 DOM では
  //   e.target が子要素になりうる。子要素からは closest で項目まで遡る
  //   （CSS の pointer-events に依存せず成立させる＝chart_template_menu と同型）。
  _resolveItem(target) {
    if (!target) {
      return null;
    }
    const ds = target.dataset;
    if (ds && (ds.themeId !== undefined || ds.themeAction)) {
      return target;
    }
    if (typeof target.closest === 'function') {
      return target.closest('[data-theme-id],[data-theme-action]');
    }
    return null;
  }

  // ビューモデル注入（協働子が適用・保存・改名・削除の後に呼ぶ）。部分注入は既存値を保持する。
  render({ themes, activeThemeId } = {}) {
    if (Array.isArray(themes)) {
      this._themes = themes;
    }
    if (activeThemeId !== undefined) {
      this._activeThemeId = activeThemeId ?? null;
    }
    this._renderRows();
  }

  // 一覧の中身を作り直す（コンテナ自体は install 時の 1 個を保持する）。
  _renderRows() {
    const doc = this._doc;
    if (!doc || !this._list) {
      return;
    }
    this._list.innerHTML = ''; // IndicatorLegendView と同作法（子を落とす）。
    // 固定行（テーマなし＝既定色）は常に先頭・常に在席する（§6.2）。
    this._list.append(this._buildRow(NONE_ROW_ID, 'テーマなし（既定色）'));
    // 区切りは**保存済みが 1 件以上あるときだけ**置く。区切りは後続の行を束ねる見出しなので、
    //   0 件で出すと「── 保存済み ──」だけが宙に浮く（テンプレートメニューは
    //   `.tpl-menu-list:empty::after` で空文言を出せるが、こちらは固定行が常に在席するため
    //   `:empty` が成立せず、CSS では同じ縮退にできない）。
    if (this._themes.length > 0) {
      const cat = doc.createElement('div');
      cat.className = 'color-theme-menu-cat';
      cat.textContent = '── 保存済み ──';
      this._list.append(cat);
      for (const t of this._themes) {
        this._list.append(this._buildRow(t.themeId, t.name));
      }
    }
  }

  // 1 行: [印] [名前]。行は data-theme-id で識別できる（クリック委譲の元）。
  //   固定行・保存済み行を同じ構造で作るのは、選択状態（is-active）の判定と適用要求の写像を
  //   1 箇所に保つため（分けると「テーマなし」だけ選択状態が付かない取り残しが起きる）。
  _buildRow(themeId, name) {
    const doc = this._doc;
    const row = doc.createElement('button');
    row.type = 'button';
    row.className = themeId === NONE_ROW_ID
      ? 'color-theme-menu-item color-theme-menu-row is-fixed'
      : 'color-theme-menu-item color-theme-menu-row';
    row.dataset.themeId = themeId;
    const isActive = themeId === NONE_ROW_ID
      ? this._activeThemeId === null
      : themeId === this._activeThemeId;
    if (isActive) {
      row.classList.add('is-active');
    }
    const mark = doc.createElement('span');
    mark.className = 'color-theme-menu-mark';
    mark.textContent = isActive ? '●' : '';
    const label = doc.createElement('span');
    label.className = 'color-theme-menu-name';
    label.textContent = name;
    row.append(mark, label);
    return row;
  }

  // ISSUE-169: 明示的な後片付け。document スコープのリスナを外す（DOM は呼び出し側が破棄する）。
  dispose() {
    removeDocumentCloseHandler(this._doc, 'color-theme', this._docCloseHandler);
    this._docCloseHandler = null;
  }

  _toggle() {
    const pop = this._pop;
    const isOpen = !!(pop && pop.classList && pop.classList.contains && !pop.classList.contains('is-hidden'));
    this._setOpen(!isOpen);
  }

  _setOpen(on) {
    const pop = this._pop;
    if (on && this._provide) {
      // 開くたびに再描画する（協働子の render 呼び出しとの順序依存を構造的に作らない）。
      this.render(this._provide() ?? {});
    }
    if (pop && pop.classList && typeof pop.classList.toggle === 'function') {
      pop.classList.toggle('is-hidden', !on);
    }
  }
}
