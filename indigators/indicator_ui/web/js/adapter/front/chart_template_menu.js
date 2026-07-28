// chart_template_menu.js — テンプレートドロップダウン（DOM アダプター・両アプリ共有）。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §6.1（`index.html` には空マウント `<div class="tpl-menu" id="tpl-menu"></div>` のみを置き、
//        項目 DOM は共有 JS が生成する＝`timeframe_menu.js` の ISSUE-123 方針）、
//   §6.2（メニュー構造＝保存／保存済み一覧／この時間足に紐付け／管理・各行右側に紐付け先時間足の
//        バッジ・`activeTemplateId` 一致行は `is-active`・開閉挙動は時間足メニューと同一）、
//   §7.1（controller を知らない＝DIP）。
//
// 責務（SRP）: メニュー DOM 生成と開閉。テンプレートの保存・適用・紐付け・管理そのものは
//   注入コールバック（onSelect / onSave / onBind / onManage）の呼び出しに委譲する
//   （本コンポーネントは controller を import しない・DIP）。DOM 不在（SSR/テスト）は no-op。
//
// 再描画（U3/U6）: 一覧はビューモデル注入 render(vm) で更新し、かつ**開くたびに再描画**する
//   （provide 注入時）。これにより restore() 完了と install() の順序依存を構造的に作らない。

export class ChartTemplateMenu {
  /**
   * @param {object} opts
   * @param {object} opts.document           DOM 実装（注入。null 可＝no-op）。
   * @param {Array}  [opts.templates]        CHART_TEMPLATE[]（宣言順に一覧表示）。
   * @param {object} [opts.bindings]         { [timeframe]: templateId }（バッジ表示元）。
   * @param {?string}[opts.activeTemplateId] 選択状態（is-active）にする templateId。
   * @param {?function}[opts.provide]        開くたびに最新ビューモデルを取得するコールバック。
   * @param {?function}[opts.onSelect]       保存済み行クリック（templateId）＝適用要求（U4）。
   * @param {?function}[opts.onSave]         「現在の構成を保存…」クリック。
   * @param {?function}[opts.onBind]         「この時間足に紐付け」選択（templateId|null）。
   * @param {?function}[opts.onManage]       「管理（改名・削除）…」クリック。
   */
  constructor({
    document: doc, templates = [], bindings = {}, activeTemplateId = null, timeframe = null,
    provide = null, onSelect = null, onSave = null, onBind = null, onManage = null,
  } = {}) {
    this._doc = doc;
    this._templates = Array.isArray(templates) ? templates : [];
    this._bindings = bindings && typeof bindings === 'object' ? bindings : {};
    this._activeTemplateId = activeTemplateId ?? null;
    // 現在の時間足（§6.2 の「● = 現在足に紐付け」印の判定に使う。未注入なら印を出さない）。
    this._timeframe = timeframe ?? null;
    this._provide = typeof provide === 'function' ? provide : null;
    this._onSelect = typeof onSelect === 'function' ? onSelect : null;
    this._onSave = typeof onSave === 'function' ? onSave : null;
    this._onBind = typeof onBind === 'function' ? onBind : null;
    this._onManage = typeof onManage === 'function' ? onManage : null;
    this._pop = null;
    this._list = null;
    this._bindList = null;
  }

  install() {
    const doc = this._doc;
    if (!doc || typeof doc.getElementById !== 'function' || typeof doc.createElement !== 'function') {
      return; // DOM 不在（SSR/テスト最小 fake）は no-op（防御・timeframe_menu と同型）。
    }
    const mount = doc.getElementById('tpl-menu');
    if (!mount || typeof mount.appendChild !== 'function') {
      return;
    }

    // トリガー（時間足メニューのトリガーと同型のボタン＋キャレット）。
    const trigger = doc.createElement('button');
    trigger.type = 'button';
    trigger.id = 'tpl-menu-trigger';
    trigger.className = 'tb-interval tpl-menu-trigger';
    trigger.title = 'チャートテンプレート';
    const label = doc.createElement('span');
    label.id = 'tpl-menu-label';
    label.textContent = 'テンプレート';
    const caret = doc.createElement('span');
    caret.className = 'tpl-menu-caret';
    caret.textContent = '▾';
    trigger.append(label, caret);

    // ポップ（保存 / 保存済み一覧 / この時間足に紐付け / 管理）。
    const pop = doc.createElement('div');
    pop.id = 'tpl-menu-pop';
    pop.className = 'tpl-menu-pop is-hidden';

    const save = doc.createElement('button');
    save.type = 'button';
    save.className = 'tpl-menu-item tpl-menu-action';
    save.dataset.tplAction = 'save';
    save.textContent = '現在の構成を保存…';
    pop.append(save);

    const savedCat = doc.createElement('div');
    savedCat.className = 'tpl-menu-cat';
    savedCat.textContent = '保存済み';
    pop.append(savedCat);

    // 一覧コンテナ（render で中身のみ差し替える）。
    const list = doc.createElement('div');
    list.className = 'tpl-menu-list';
    pop.append(list);

    const bindCat = doc.createElement('div');
    bindCat.className = 'tpl-menu-cat';
    bindCat.textContent = 'この時間足に紐付け';
    pop.append(bindCat);

    // 紐付け選択コンテナ（render で中身のみ差し替える）。
    const bindList = doc.createElement('div');
    bindList.className = 'tpl-menu-bind';
    pop.append(bindList);

    const manage = doc.createElement('button');
    manage.type = 'button';
    manage.className = 'tpl-menu-item tpl-menu-action';
    manage.dataset.tplAction = 'manage';
    manage.textContent = '管理（名前を変更・削除）…';
    pop.append(manage);

    mount.appendChild(trigger);
    mount.appendChild(pop);
    this._pop = pop;
    this._list = list;
    this._bindList = bindList;
    this._renderRows();

    trigger.addEventListener('click', (e) => {
      if (e && typeof e.stopPropagation === 'function') {
        e.stopPropagation(); // document の外側クリッククローズに拾わせない。
      }
      this._toggle();
    });
    // 項目クリック（委譲）: 行＝適用要求、紐付け＝設定/解除、アクション＝保存/管理。いずれも閉じる。
    pop.addEventListener('click', (e) => {
      const t = this._resolveItem(e && e.target);
      const ds = t && t.dataset;
      if (!ds) {
        return;
      }
      if (ds.templateId) {
        this._setOpen(false);
        this._onSelect?.(ds.templateId);
        return;
      }
      if (ds.tplBind !== undefined) {
        this._setOpen(false);
        this._onBind?.(ds.tplBind === '' ? null : ds.tplBind);
        return;
      }
      if (ds.tplAction === 'save') {
        this._setOpen(false);
        this._onSave?.();
        return;
      }
      if (ds.tplAction === 'manage') {
        this._setOpen(false);
        this._onManage?.();
      }
    });
    // 外側クリックで閉じる（メニュー内クリックは pop/trigger 側で処理済み）。
    if (typeof doc.addEventListener === 'function') {
      doc.addEventListener('click', () => this._setOpen(false));
    }
    pop.addEventListener('pointerdown', (e) => {
      if (e && typeof e.stopPropagation === 'function') {
        e.stopPropagation();
      }
    });
  }

  // クリック対象から「項目要素」を解決する。行は名前・バッジの子 span を持つため、実 DOM では
  //   e.target が子要素になりうる（時間足メニューの項目はテキストのみで子を持たないため不要だった）。
  //   子要素からは closest で項目まで遡る（CSS の pointer-events に依存せず成立させる）。
  _resolveItem(target) {
    if (!target) {
      return null;
    }
    const ds = target.dataset;
    if (ds && (ds.templateId || ds.tplBind !== undefined || ds.tplAction)) {
      return target;
    }
    if (typeof target.closest === 'function') {
      return target.closest('[data-template-id],[data-tpl-bind],[data-tpl-action]');
    }
    return null;
  }

  // ビューモデル注入（U3: controller が保存・改名・削除・適用・紐付けの後に呼ぶ）。
  render({ templates, bindings, activeTemplateId, timeframe } = {}) {
    if (Array.isArray(templates)) {
      this._templates = templates;
    }
    if (bindings && typeof bindings === 'object') {
      this._bindings = bindings;
    }
    if (activeTemplateId !== undefined) {
      this._activeTemplateId = activeTemplateId;
    }
    if (timeframe !== undefined) {
      this._timeframe = timeframe;
    }
    this._renderRows();
  }

  // 一覧・紐付け選択の中身を作り直す（コンテナ自体は install 時の 1 個を保持する）。
  _renderRows() {
    const doc = this._doc;
    if (!doc || !this._list || !this._bindList) {
      return;
    }
    this._clear(this._list);
    for (const t of this._templates) {
      this._list.append(this._buildRow(t));
    }
    this._clear(this._bindList);
    this._bindList.append(this._buildBindItem('', '紐付けなし'));
    for (const t of this._templates) {
      this._bindList.append(this._buildBindItem(t.templateId, t.name));
    }
  }

  _clear(el) {
    // IndicatorLegendView と同作法（innerHTML='' で子を落とす）。
    el.innerHTML = '';
  }

  // 保存済み 1 行: [名前] [紐付け先バッジ]。行は data-template-id で識別できる（U4 の委譲元）。
  _buildRow(t) {
    const doc = this._doc;
    const row = doc.createElement('button');
    row.type = 'button';
    row.className = 'tpl-menu-item tpl-menu-row';
    row.dataset.templateId = t.templateId;
    if (t.templateId === this._activeTemplateId) {
      row.classList.add('is-active');
    }
    // 「● = 現在足に紐付け」（§6.2 のメニュー構造）。現在足が未注入・未紐付けなら空。
    const mark = doc.createElement('span');
    mark.className = 'tpl-menu-mark';
    mark.textContent = this._timeframe && this._bindings[this._timeframe] === t.templateId ? '●' : '';
    const name = doc.createElement('span');
    name.className = 'tpl-menu-name';
    name.textContent = t.name;
    const badge = doc.createElement('span');
    badge.className = 'tpl-menu-badge';
    const tfs = Object.entries(this._bindings)
      .filter(([, id]) => id === t.templateId)
      .map(([tf]) => tf);
    badge.textContent = tfs.length > 0 ? `(${tfs.join(', ')})` : '';
    row.append(mark, name, badge);
    return row;
  }

  // 「この時間足に紐付け」の 1 項目（templateId='' は紐付けなし＝解除）。
  _buildBindItem(templateId, text) {
    const doc = this._doc;
    const item = doc.createElement('button');
    item.type = 'button';
    item.className = 'tpl-menu-item tpl-menu-bind-item';
    item.dataset.tplBind = templateId;
    item.textContent = text;
    return item;
  }

  _toggle() {
    const pop = this._pop;
    const isOpen = !!(pop && pop.classList && pop.classList.contains && !pop.classList.contains('is-hidden'));
    this._setOpen(!isOpen);
  }

  _setOpen(on) {
    const pop = this._pop;
    if (on && this._provide) {
      // U6: 開くたびに再描画する（restore() 完了との順序依存を構造的に作らない）。
      this.render(this._provide() ?? {});
    }
    if (pop && pop.classList && typeof pop.classList.toggle === 'function') {
      pop.classList.toggle('is-hidden', !on);
    }
  }
}
