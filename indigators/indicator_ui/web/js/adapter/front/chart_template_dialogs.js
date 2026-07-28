// chart_template_dialogs.js — テンプレートの保存／管理モーダル DOM（両アプリ共有）。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §6.2（「現在の構成を保存…」ダイアログ＝名前入力（初期値＝空）・保存対象の指標一覧
//        （読み取り専用プレビュー・件数と指標名）・「この時間足（例：日）に紐付ける」チェック
//        （既定 ON）／「管理」ダイアログ＝一覧＋改名・削除（削除は確認 1 段））、
//   §5.1 例外・F-T1（名前が空／40 文字超／件数上限は保存せずダイアログ内にインライン表示）、
//   §5.5（改名は検証して更新・削除は確認を 1 段挟む）、
//   §7.1（controller を知らない＝DIP）。
//
// 参照実装（同型元）: properties_dialog.js（open で DOM 構築 → document.body へ追加、close で
//   parentNode から除去。背景クリックでは閉じない＝誤操作防止・§2.3）。
//
// 責務（SRP）: DOM 構築とイベント配線のみ。名前検証・永続化・テンプレート操作は
//   注入コールバック（onSubmit / onRename / onDelete）へ委譲し、結果 { ok, code } を
//   インライン表示へ写像する（判定ロジックは本ファイルに持たない）。

// code（usecase/chart_templates.js の CODE 語彙）→ 表示文言。未知 code は既定文言へ倒す。
const MESSAGE = Object.freeze({
  empty: '名前を入力してください。',
  too_long: '名前は 40 文字以内で入力してください。',
  duplicate: '同じ名前のテンプレートが既にあります。',
  limit: 'テンプレートは 50 件までです。不要なテンプレートを削除してください。',
  not_found: '対象のテンプレートが見つかりません。',
});

function messageFor(code) {
  return MESSAGE[code] ?? '保存できませんでした。';
}

export class ChartTemplateDialogs {
  // document: DOM 実装（注入）。null 可（DOM 不在時は各メソッドが no-op＝防御）。
  constructor({ document: doc = null } = {}) {
    this._doc = doc;
    this._root = null;
  }

  _usable() {
    const doc = this._doc;
    return !!(doc && typeof doc.createElement === 'function' && doc.body && typeof doc.body.append === 'function');
  }

  close() {
    if (this._root && this._root.parentNode) {
      this._root.parentNode.removeChild(this._root);
    }
    this._root = null;
  }

  // ---- 共通の殻（背景＋パネル＋ヘッダ＋本文＋フッタ）--------------------------
  _openShell(kind, title) {
    this.close(); // 同時に 2 枚開かない（後勝ち）。
    const doc = this._doc;
    const root = doc.createElement('div');
    root.className = 'tpl-dialog-backdrop is-open';
    root.dataset.tplDialog = kind;

    const panel = doc.createElement('div');
    panel.className = 'tpl-dialog';
    if (typeof panel.setAttribute === 'function') {
      panel.setAttribute('role', 'dialog');
    }

    const head = doc.createElement('div');
    head.className = 'tpl-dialog-head';
    const titleEl = doc.createElement('span');
    titleEl.className = 'tpl-dialog-title';
    titleEl.textContent = title;
    const closeBtn = doc.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'tpl-dialog-close';
    closeBtn.dataset.tplAction = 'cancel';
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', () => this.close());
    head.append(titleEl, closeBtn);

    const body = doc.createElement('div');
    body.className = 'tpl-dialog-body';

    const error = doc.createElement('div');
    error.className = 'tpl-dialog-error';
    error.dataset.tplError = kind;
    error.textContent = '';

    const foot = doc.createElement('div');
    foot.className = 'tpl-dialog-foot';

    panel.append(head, body, error, foot);
    root.append(panel);
    // 背景クリックでは閉じない（誤操作防止・properties_dialog と同方針）。
    root.addEventListener('mousedown', (ev) => {
      if (ev && ev.target === root && typeof ev.stopPropagation === 'function') {
        ev.stopPropagation();
      }
    });
    doc.body.append(root);
    this._root = root;
    return { root, body, error, foot };
  }

  _button(label, action, className) {
    const btn = this._doc.createElement('button');
    btn.type = 'button';
    btn.className = className;
    btn.dataset.tplAction = action;
    btn.textContent = label;
    return btn;
  }

  // ---- UC-T01「現在の構成を保存…」-------------------------------------------
  /**
   * @param {object} opts
   * @param {string}   [opts.timeframeLabel] 現在の時間足の表示名（例「日」）。
   * @param {string[]} [opts.indicatorNames] 保存対象の指標名（読み取り専用プレビュー）。
   * @param {function} [opts.onSubmit] ({ name, bindCurrentTimeframe }) => { ok, code }。
   * @param {?function} [opts.findExisting] (name) => CHART_TEMPLATE|null。正規化名が一致する既存
   *   テンプレートを返す判定器（usecase の純関数を注入する。本ダイアログは文字列比較を持たない）。
   *   未注入時は確認を挟まない（従来挙動）。
   */
  openSave({
    timeframeLabel = '', indicatorNames = [], onSubmit = () => ({ ok: true }), findExisting = null,
  } = {}) {
    if (!this._usable()) {
      return null;
    }
    const doc = this._doc;
    const { root, body, error, foot } = this._openShell('save', '現在の構成を保存');

    // 名前入力（初期値＝空・§6.2）。
    const nameRow = doc.createElement('div');
    nameRow.className = 'tpl-dialog-row';
    const nameLabel = doc.createElement('span');
    nameLabel.className = 'tpl-dialog-label';
    nameLabel.textContent = 'テンプレート名';
    const nameInput = doc.createElement('input');
    nameInput.type = 'text';
    nameInput.className = 'tpl-dialog-input';
    nameInput.dataset.tplField = 'name';
    nameInput.value = '';
    nameRow.append(nameLabel, nameInput);

    // 保存対象の指標一覧（読み取り専用プレビュー・件数と指標名・§6.2）。
    const preview = doc.createElement('div');
    preview.className = 'tpl-dialog-preview';
    const previewHead = doc.createElement('div');
    previewHead.className = 'tpl-dialog-preview-head';
    previewHead.textContent = `保存対象の指標: ${indicatorNames.length} 件`;
    preview.append(previewHead);
    for (const label of indicatorNames) {
      const item = doc.createElement('div');
      item.className = 'tpl-dialog-preview-item';
      item.textContent = label;
      preview.append(item);
    }

    // 「この時間足（例：日）に紐付ける」チェック（既定 ON・§6.2）。
    const bindRow = doc.createElement('label');
    bindRow.className = 'tpl-dialog-check';
    const bindInput = doc.createElement('input');
    bindInput.type = 'checkbox';
    bindInput.dataset.tplField = 'bind';
    bindInput.checked = true;
    const bindText = doc.createElement('span');
    bindText.textContent = timeframeLabel
      ? `この時間足（${timeframeLabel}）に紐付ける`
      : 'この時間足に紐付ける';
    bindRow.append(bindInput, bindText);

    body.append(nameRow, preview, bindRow);

    const cancel = this._button('キャンセル', 'cancel', 'tpl-dialog-btn');
    cancel.addEventListener('click', () => this.close());
    const submit = this._button('保存', 'submit', 'tpl-dialog-btn is-primary');
    // 上書き確認（ユーザー指示 2026-07-28）: 正規化名が既存と一致する場合は保存前に確認 1 段を挟む。
    //   確認状態＝「上書きする」ラベル＋対象名のインライン表示。名前を編集したら解除する
    //   （§5.5 削除の確認 1 段と同型のイディオム）。判定は注入された usecase 純関数のみが行う。
    let pendingOverwrite = null; // 確認中の対象テンプレート（null=通常の保存状態）。
    const clearOverwriteConfirm = () => {
      if (!pendingOverwrite) {
        return;
      }
      pendingOverwrite = null;
      submit.textContent = '保存';
      error.textContent = '';
      error.classList.remove('is-confirm');
    };
    // 名前の編集で確認を解除する（編集後の名前が別テンプレートと一致すれば再度確認に入る）。
    nameInput.addEventListener('input', clearOverwriteConfirm);
    nameInput.addEventListener('change', clearOverwriteConfirm);
    submit.addEventListener('click', () => {
      if (!pendingOverwrite) {
        const existing = typeof findExisting === 'function' ? findExisting(nameInput.value) : null;
        if (existing) {
          // 保存せず確認へ入る。エラーではないため配色はエラー色にしない（is-confirm）。
          pendingOverwrite = existing;
          submit.textContent = '上書きする';
          error.textContent = `「${existing.name}」を上書きします。`;
          error.classList.add('is-confirm');
          return;
        }
      }
      const result = onSubmit({ name: nameInput.value, bindCurrentTimeframe: !!bindInput.checked }) ?? { ok: true };
      if (result.ok) {
        this.close();
        return;
      }
      clearOverwriteConfirm();
      error.textContent = messageFor(result.code); // F-T1: ダイアログ内インライン表示。
    });
    foot.append(cancel, submit);

    if (typeof nameInput.focus === 'function') {
      nameInput.focus();
    }
    return root;
  }

  // ---- UC-T05「管理（改名・削除）…」-----------------------------------------
  /**
   * @param {object} opts
   * @param {Array}    [opts.templates] CHART_TEMPLATE[]（宣言順に一覧）。
   * @param {function} [opts.onRename] (templateId, name) => { ok, code }。
   * @param {function} [opts.onDelete] (templateId) => void（確認 1 段の後に呼ばれる）。
   */
  openManage({ templates = [], onRename = () => ({ ok: true }), onDelete = () => {} } = {}) {
    if (!this._usable()) {
      return null;
    }
    const doc = this._doc;
    const { root, body, error, foot } = this._openShell('manage', 'テンプレートの管理');

    const list = doc.createElement('div');
    list.className = 'tpl-dialog-list';
    for (const t of templates) {
      list.append(this._buildManageRow(t, { error, onRename, onDelete }));
    }
    body.append(list);

    const close = this._button('閉じる', 'cancel', 'tpl-dialog-btn');
    close.addEventListener('click', () => this.close());
    foot.append(close);
    return root;
  }

  // 管理ダイアログの 1 行: [名前] [改名] [削除]。改名・削除の操作面は行内で切り替える。
  _buildManageRow(t, { error, onRename, onDelete }) {
    const doc = this._doc;
    const row = doc.createElement('div');
    row.className = 'tpl-dialog-list-row';
    row.dataset.tplRow = t.templateId;

    const name = doc.createElement('span');
    name.className = 'tpl-dialog-list-name';
    name.textContent = t.name;

    // 改名: 入力＋確定＋取消（既定は非表示クラス）。
    const renameInput = doc.createElement('input');
    renameInput.type = 'text';
    renameInput.className = 'tpl-dialog-input tpl-dialog-rename is-hidden';
    renameInput.dataset.tplRenameInput = t.templateId;
    renameInput.value = t.name;
    const renameCommit = doc.createElement('button');
    renameCommit.type = 'button';
    renameCommit.className = 'tpl-dialog-btn is-hidden';
    renameCommit.dataset.tplRenameCommit = t.templateId;
    renameCommit.textContent = '確定';

    const rename = doc.createElement('button');
    rename.type = 'button';
    rename.className = 'tpl-dialog-btn';
    rename.dataset.tplRename = t.templateId;
    rename.textContent = '名前を変更';
    rename.addEventListener('click', () => {
      renameInput.classList.remove('is-hidden');
      renameCommit.classList.remove('is-hidden');
      rename.classList.add('is-hidden');
    });
    renameCommit.addEventListener('click', () => {
      error.textContent = '';
      const result = onRename(t.templateId, renameInput.value) ?? { ok: true };
      if (result.ok) {
        name.textContent = renameInput.value;
        renameInput.classList.add('is-hidden');
        renameCommit.classList.add('is-hidden');
        rename.classList.remove('is-hidden');
        return;
      }
      error.textContent = messageFor(result.code); // F-T1: インライン表示・閉じない。
    });

    // 削除: 確認を 1 段挟む（§5.5・取り消し不能な状態変更のため）。
    const confirm = doc.createElement('button');
    confirm.type = 'button';
    confirm.className = 'tpl-dialog-btn is-danger is-hidden';
    confirm.dataset.tplDeleteConfirm = t.templateId;
    confirm.textContent = '削除する';
    const remove = doc.createElement('button');
    remove.type = 'button';
    remove.className = 'tpl-dialog-btn';
    remove.dataset.tplDelete = t.templateId;
    remove.textContent = '削除';
    remove.addEventListener('click', () => {
      confirm.classList.remove('is-hidden');
      remove.classList.add('is-hidden');
    });
    confirm.addEventListener('click', () => {
      onDelete(t.templateId);
      if (row.parentNode && typeof row.parentNode.removeChild === 'function') {
        row.parentNode.removeChild(row);
      }
    });

    row.append(name, renameInput, renameCommit, rename, confirm, remove);
    return row;
  }
}
