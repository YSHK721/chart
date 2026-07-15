// PropertiesDialog（adapter/front/properties_dialog.js）— インジケーター・プロパティ ダイアログ DOM。
//
// 設計入力: 内部設計_パラメータ設定ダイアログ.md v0.2.0
//   §2 ダイアログ構造（タイトル/タブ/フッター・ドラッグ・×閉じる）
//   §3 PARAM_DEF→コントロール写像 / §3.4 グループ / §3.5 条件付き有効化 / §3.6 ツールチップ
//   §5 リアルタイム検証（F-11・OK 制御）/ §6 スタイル・可視性タブ / §7 振る舞い・状態遷移
//   §8.2 PropertiesDialog 署名 / §8.4 upstream JS API 非依存 / §9 A 方式可動差（H-1）。
//
// 純ロジックは usecase/form_model.js（buildFormModel/computeEnabled/validateForm/resetToDefaults）
//   へ委譲する（DOM 非依存ロジックの分離・§10.4）。本ファイルは DOM 構築・イベント配線のみ。
//
// ★ upstream JS API（addLineSeries / createPriceLine / setData / applyOptions 等）は
//   一切参照しない（§8.4・母体 §9 grep 0 件規律）。描画反映は onApply（→ ChartRenderer/facade 経由）。

import {
  buildFormModel,
  computeEnabled,
  computeVisible,
  validateForm,
  resetToDefaults,
} from '../../usecase/form_model.js';

// A 方式（埋め込み事前計算）で「variant 以外のパラメータが描画へ反映されない」ことを
// UI に明示する注記（§9.3・H-1・サイレント不一致を作らない）。
const A_METHOD_NOTE =
  'この値の反映は B 方式（ライブ API）で有効です。現プロトタイプ（A 方式）では variant 以外の値の変更は描画へ未反映です。';

// i18n 解決器を持たないプロトタイプ向けの簡易ラベル化（キー末尾を表示）。
function humanizeKey(key) {
  if (key === null || key === undefined) {
    return '';
  }
  const s = String(key);
  return s.includes('.') ? s.split('.').pop() : s;
}

export class PropertiesDialog {
  // { document, def, instance, onApply, onCancel }
  //   def      : IndicatorDef（catalog.get の戻り）
  //   instance : AppliedInstance（params/variant を持つ。null 可＝既定値で開く）
  //   onApply  : (values) => void   OK 押下時に収集値を渡す（→ recomputeInstance）
  //   onCancel : () => void         キャンセル/×/背景時（任意）
  // mode: 'b'=served（ライブ API・params 実反映）/ 'a'=file://（埋め込み事前計算・params 未反映）。
  //   既定 'a'（従来挙動・単体テスト互換）。'b' では A 方式注記を出さない（実反映されるため）。
  constructor({ document: doc, def, instance = null, mode = 'a', context = {}, onApply = () => {}, onCancel = () => {} }) {
    this._doc = doc;
    this._def = def;
    this._instance = instance;
    this._mode = mode;
    // context: computeEnabled の関数述語へ渡す外部状態（例 { timeframe, servedMode }）。
    //   ISSUE-070: mode=sessions×対応tf の解像度グレーアウト判定に timeframe が要る（param 値外）。
    this._context = context || {};
    this._onApply = onApply;
    this._onCancel = onCancel;

    // 現在のフォーム値（name -> value）。初期は instance.params 優先→default。
    const currentParams = (instance && instance.params) || {};
    const model = buildFormModel(def, currentParams);
    this._values = {};
    for (const f of model.fields) {
      this._values[f.name] = f.value;
    }

    // バリアント（profit_band global↔robust 等）。OK 時に variant 変更を実反映する
    //   経路を残す（A 方式でも variant 切替は事前計算 series があり実描画される・§9.2）。
    this._variants = (def.compute && def.compute.variants) || ['default'];
    this._variant = (instance && instance.variant) || this._variants[0];

    this._activeTab = 'inputs';
    this._root = null; // ダイアログ最上位要素
    this._fieldEls = new Map(); // name -> { row, control, error, info }
    this._okBtn = null;
    this._drag = null;
    this._offset = { x: 0, y: 0 };
  }

  // ダイアログ DOM を生成し document.body へ追加・配線する（§2・§7）。
  open() {
    const doc = this._doc;
    const root = doc.createElement('div');
    root.className = 'prop-dialog-backdrop is-open';
    root.setAttribute('data-prop-dialog', this._def.id);

    const panel = doc.createElement('div');
    panel.className = 'prop-dialog';
    panel.setAttribute('role', 'dialog');

    panel.append(
      this._buildHeader(),
      this._buildTabBar(),
      this._buildBody(),
      this._buildFooter(),
    );
    root.append(panel);

    // 背景クリックは閉じない（誤操作防止・§2.3）。パネル外側だけ無反応。
    root.addEventListener('mousedown', (ev) => {
      if (ev.target === root) {
        ev.stopPropagation();
      }
    });

    this._root = root;
    this._panel = panel;
    doc.body.append(root);

    // 初期検証・有効化反映・表示トグル反映。
    this._revalidate();
    this._refreshEnabled();
    this._refreshVisible();
    return root;
  }

  close() {
    if (this._root && this._root.parentNode) {
      this._root.parentNode.removeChild(this._root);
    }
    this._root = null;
  }

  // ---- ヘッダ（タイトル＋×・ドラッグ移動ハンドル）-----------------------------
  _buildHeader() {
    const doc = this._doc;
    const head = doc.createElement('div');
    head.className = 'prop-dialog-head';

    const title = doc.createElement('span');
    title.className = 'prop-dialog-title';
    const variantLabel =
      this._instance && this._instance.variant && this._instance.variant !== 'default'
        ? ` (${this._instance.variant})`
        : '';
    title.textContent = humanizeKey(this._def.displayNameKey ?? this._def.id) + variantLabel;

    const close = doc.createElement('button');
    close.className = 'prop-dialog-close';
    close.type = 'button';
    close.setAttribute('aria-label', '閉じる');
    close.textContent = '×';
    close.addEventListener('click', () => this._onCancelClick());

    head.append(title, close);

    // ドラッグ移動（Pointer Events・ブラウザ標準のみ・§2.3）。
    head.addEventListener('pointerdown', (ev) => this._onDragStart(ev));
    return head;
  }

  // ---- タブバー（パラメーター/スタイル/可視性）-------------------------------
  _buildTabBar() {
    const doc = this._doc;
    const bar = doc.createElement('div');
    bar.className = 'prop-tabs';
    const tabs = [
      { key: 'inputs', label: 'パラメーター' },
      { key: 'style', label: 'スタイル' },
      { key: 'visibility', label: '可視性' },
    ];
    this._tabBtns = new Map();
    for (const t of tabs) {
      const btn = doc.createElement('button');
      btn.type = 'button';
      btn.className = 'prop-tab' + (t.key === this._activeTab ? ' is-active' : '');
      btn.dataset.propTab = t.key;
      btn.textContent = t.label;
      btn.addEventListener('click', () => this._switchTab(t.key));
      this._tabBtns.set(t.key, btn);
      bar.append(btn);
    }
    return bar;
  }

  _switchTab(key) {
    this._activeTab = key;
    for (const [k, btn] of this._tabBtns) {
      btn.classList.toggle('is-active', k === key);
    }
    for (const [k, pane] of this._panes) {
      pane.classList.toggle('is-active', k === key);
    }
  }

  // ---- ボディ（3 タブのペイン）-----------------------------------------------
  _buildBody() {
    const doc = this._doc;
    const body = doc.createElement('div');
    body.className = 'prop-body';

    this._panes = new Map();
    const inputs = this._buildInputsPane();
    const style = this._buildStylePane();
    const visibility = this._buildVisibilityPane();
    this._panes.set('inputs', inputs);
    this._panes.set('style', style);
    this._panes.set('visibility', visibility);

    body.append(inputs, style, visibility);
    return body;
  }

  // パラメーター タブ（本書の主眼・§3/§5）。
  _buildInputsPane() {
    const doc = this._doc;
    const pane = doc.createElement('div');
    pane.className = 'prop-pane is-active';
    pane.dataset.propPane = 'inputs';

    // バリアントセレクタ（複数 variant を持つ指標のみ・global↔robust 等）。
    //   variant 変更は A/B 双方で実描画反映される（事前計算 series が存在・§9.2）。
    if (this._variants.length > 1) {
      pane.append(this._buildVariantRow());
    }

    const model = buildFormModel(this._def, this._values);
    for (const group of model.groups) {
      if (group.key !== null) {
        const heading = doc.createElement('div');
        heading.className = 'prop-group-heading';
        heading.textContent = humanizeKey(group.key);
        pane.append(heading);
      }
      for (const field of group.fields) {
        pane.append(this._buildFieldRow(field));
      }
    }
    return pane;
  }

  // バリアント選択行（variant 変更は実描画反映・§9.2・H-1 対象外）。
  _buildVariantRow() {
    const doc = this._doc;
    const row = doc.createElement('div');
    row.className = 'prop-field-row';
    row.dataset.propField = '__variant';

    const label = doc.createElement('label');
    label.className = 'prop-field-label';
    label.textContent = 'バリアント';

    const controlWrap = doc.createElement('div');
    controlWrap.className = 'prop-field-control';
    const sel = doc.createElement('select');
    sel.className = 'prop-input prop-input-select';
    sel.dataset.propName = '__variant';
    for (const v of this._variants) {
      const opt = doc.createElement('option');
      opt.value = v;
      opt.textContent = humanizeKey(v);
      if (v === this._variant) opt.selected = true;
      sel.append(opt);
    }
    sel.addEventListener('change', () => { this._variant = sel.value; });
    controlWrap.append(sel);
    row.append(label, controlWrap);
    return row;
  }

  // 1 フィールド行（ラベル / コントロール / 単位 / info / インライン違反）。
  _buildFieldRow(field) {
    const doc = this._doc;
    const row = doc.createElement('div');
    row.className = 'prop-field-row';
    row.dataset.propField = field.name;

    const label = doc.createElement('label');
    label.className = 'prop-field-label';
    label.textContent = humanizeKey(field.label);

    // info（ツールチップ）アイコン。
    let info = null;
    if (field.tooltip) {
      info = doc.createElement('span');
      info.className = 'prop-field-info';
      info.textContent = 'ⓘ';
      info.title = humanizeKey(field.tooltip);
      label.append(' ', info);
    }

    const controlWrap = doc.createElement('div');
    controlWrap.className = 'prop-field-control';
    const control = this._buildControl(field);
    controlWrap.append(control);
    if (field.unit) {
      const unit = doc.createElement('span');
      unit.className = 'prop-field-unit';
      unit.textContent = humanizeKey(field.unit);
      controlWrap.append(unit);
    }

    const error = doc.createElement('div');
    error.className = 'prop-field-error';
    error.dataset.propError = field.name;

    row.append(label, controlWrap, error);
    this._fieldEls.set(field.name, { row, control, error, info });
    return row;
  }

  // control_type 別レンダリング（§3.1）。
  _buildControl(field) {
    switch (field.controlType) {
      case 'number':
        return this._buildNumber(field);
      case 'select':
        return this._buildSelect(field);
      case 'segmented':
        return this._buildSegmented(field);
      case 'checkbox':
        return this._buildCheckbox(field);
      case 'list':
        return this._buildFloatList(field);
      case 'multiselect':
        return this._buildMultiselect(field);
      case 'color':
        return this._buildColor(field);
      case 'window_compound':
        return this._buildWindowCompound(field);
      default:
        return this._buildText(field);
    }
  }

  _buildNumber(field) {
    const doc = this._doc;
    const input = doc.createElement('input');
    input.type = 'number';
    input.className = 'prop-input prop-input-number';
    input.dataset.propName = field.name;
    if (field.step !== null) input.step = String(field.step);
    if (field.min !== null) input.min = String(field.min);
    if (field.max !== null) input.max = String(field.max);
    input.value = field.value === null || field.value === undefined ? '' : String(field.value);
    input.addEventListener('input', () => {
      const raw = input.value;
      this._values[field.name] = raw === '' ? null : Number(raw);
      this._onChange();
    });
    return input;
  }

  _buildSelect(field) {
    const doc = this._doc;
    const sel = doc.createElement('select');
    sel.className = 'prop-input prop-input-select';
    sel.dataset.propName = field.name;
    for (const v of field.enumValues ?? []) {
      const opt = doc.createElement('option');
      opt.value = String(v);
      // enumLabels（日本語表示マップ）優先。未指定はキー末尾を表示（従来挙動）。
      opt.textContent = (field.enumLabels && field.enumLabels[v] != null)
        ? field.enumLabels[v]
        : humanizeKey(String(v));
      if (v === field.value) opt.selected = true;
      sel.append(opt);
    }
    sel.addEventListener('change', () => {
      // enum 値は raw（文字列/数値）。数値 enum は元型へ復元。
      const picked = (field.enumValues ?? []).find((v) => String(v) === sel.value);
      this._values[field.name] = picked !== undefined ? picked : sel.value;
      this._onChange();
    });
    return sel;
  }

  // segmented: ENUM を「横並びセグメントボタン群」で描く（ドロップダウンでなくトグル・§3.1 拡張）。
  //   試作 prototype_260630-01 の解像度トグル（ビン ⇄ レンジ）移植。各 option をボタン化し、
  //   選択中に is-active を付与。クリックで this._values[name] を更新後、既存 _onChange() を呼ぶ
  //   （→ _refreshVisible/_revalidate が走り bins/range 行が即出没する）。
  _buildSegmented(field) {
    const doc = this._doc;
    const wrap = doc.createElement('div');
    wrap.className = 'prop-input prop-segmented';
    wrap.dataset.propName = field.name;
    const options = field.enumValues ?? [];
    const buttons = [];
    const setActive = (val) => {
      for (const b of buttons) {
        b.classList.toggle('is-active', String(b.dataset.segValue) === String(val));
      }
    };
    for (const v of options) {
      const btn = doc.createElement('button');
      btn.type = 'button';
      btn.className = 'prop-seg-btn';
      btn.dataset.segValue = String(v);
      // enumLabels（日本語表示マップ）優先。未指定はキー末尾を表示（select と同挙動）。
      btn.textContent = (field.enumLabels && field.enumLabels[v] != null)
        ? field.enumLabels[v]
        : humanizeKey(String(v));
      btn.addEventListener('click', () => {
        // v は走査中の option 値そのもの（enum の raw な元型＝文字列/数値を保持）。
        //   options.find で自分自身を引き直す必要はないため直接代入する。
        this._values[field.name] = v;
        setActive(this._values[field.name]);
        this._onChange();
      });
      buttons.push(btn);
      wrap.append(btn);
    }
    setActive(field.value);
    return wrap;
  }

  _buildCheckbox(field) {
    const doc = this._doc;
    const input = doc.createElement('input');
    input.type = 'checkbox';
    input.className = 'prop-input prop-input-checkbox';
    input.dataset.propName = field.name;
    input.checked = Boolean(field.value);
    input.addEventListener('change', () => {
      this._values[field.name] = input.checked;
      this._onChange();
    });
    return input;
  }

  _buildText(field) {
    const doc = this._doc;
    const input = doc.createElement('input');
    input.type = 'text';
    input.className = 'prop-input prop-input-text';
    input.dataset.propName = field.name;
    input.value = field.value === null || field.value === undefined ? '' : String(field.value);
    input.addEventListener('input', () => {
      this._values[field.name] = input.value === '' ? null : input.value;
      this._onChange();
    });
    return input;
  }

  _buildColor(field) {
    const doc = this._doc;
    const input = doc.createElement('input');
    input.type = 'color';
    input.className = 'prop-input prop-input-color';
    input.dataset.propName = field.name;
    // <input type=color> は #rrggbb のみ。rgba 既定はそのまま値として保持し、
    // ピッカーには近似 hex を表示する（プロトタイプ・スタイルタブは最小可）。
    input.value = toHex(field.value);
    input.addEventListener('input', () => {
      this._values[field.name] = input.value;
      this._onChange();
    });
    return input;
  }

  // FLOAT_LIST（probabilities）リスト編集（§3.2）。各要素=数値入力＋削除、末尾に追加。
  _buildFloatList(field) {
    const doc = this._doc;
    const wrap = doc.createElement('div');
    wrap.className = 'prop-input prop-list';
    wrap.dataset.propName = field.name;

    const list = Array.isArray(field.value) ? field.value.slice() : [];
    this._values[field.name] = list;

    const rows = doc.createElement('div');
    rows.className = 'prop-list-rows';

    const renderRows = () => {
      rows.innerHTML = '';
      const cur = this._values[field.name];
      cur.forEach((val, idx) => {
        const r = doc.createElement('div');
        r.className = 'prop-list-row';
        const num = doc.createElement('input');
        num.type = 'number';
        num.step = 'any';
        num.className = 'prop-input prop-input-number';
        num.value = String(val);
        num.addEventListener('input', () => {
          const arr = this._values[field.name].slice();
          arr[idx] = num.value === '' ? null : Number(num.value);
          this._values[field.name] = arr;
          this._onChange();
        });
        const del = doc.createElement('button');
        del.type = 'button';
        del.className = 'prop-list-del';
        del.textContent = '−';
        del.addEventListener('click', () => {
          const arr = this._values[field.name].slice();
          // 空リスト禁止（最低 1 要素・§3.2）。
          if (arr.length <= 1) return;
          arr.splice(idx, 1);
          this._values[field.name] = arr;
          renderRows();
          this._onChange();
        });
        r.append(num, del);
        rows.append(r);
      });
    };
    renderRows();

    const add = doc.createElement('button');
    add.type = 'button';
    add.className = 'prop-list-add';
    add.textContent = '＋追加';
    add.addEventListener('click', () => {
      const arr = this._values[field.name].slice();
      arr.push(0.95); // 既定追加値（§3.2）。
      this._values[field.name] = arr;
      renderRows();
      this._onChange();
    });

    wrap.append(rows, add);
    return wrap;
  }

  // ENUM_LIST（buckets）マルチセレクト（候補からチェックで複数選択・§3.1）。
  _buildMultiselect(field) {
    const doc = this._doc;
    const wrap = doc.createElement('div');
    wrap.className = 'prop-input prop-multiselect';
    wrap.dataset.propName = field.name;
    const selected = new Set(Array.isArray(field.value) ? field.value : []);
    this._values[field.name] = [...selected];

    for (const opt of field.enumValues ?? []) {
      const chip = doc.createElement('label');
      chip.className = 'prop-chip';
      const cb = doc.createElement('input');
      cb.type = 'checkbox';
      cb.checked = selected.has(opt);
      cb.addEventListener('change', () => {
        if (cb.checked) selected.add(opt);
        else selected.delete(opt);
        // 元の候補順を保持して配列化。
        this._values[field.name] = (field.enumValues ?? []).filter((v) => selected.has(v));
        this._onChange();
      });
      chip.append(cb, doc.createTextNode(' ' + humanizeKey(String(opt))));
      wrap.append(chip);
    }
    return wrap;
  }

  // window（Union[str,int]）複合: ラジオ expanding/固定窓 ＋ 数値（§4.3.1）。
  _buildWindowCompound(field) {
    const doc = this._doc;
    const wrap = doc.createElement('div');
    wrap.className = 'prop-input prop-window-compound';
    wrap.dataset.propName = field.name;

    const isExpanding = field.value === 'expanding' || field.value === null || field.value === undefined;

    const radioExp = doc.createElement('label');
    const rExp = doc.createElement('input');
    rExp.type = 'radio';
    rExp.name = `prop-window-${field.name}`;
    rExp.checked = isExpanding;
    radioExp.append(rExp, doc.createTextNode(' 展開(expanding)'));

    const radioFixed = doc.createElement('label');
    const rFixed = doc.createElement('input');
    rFixed.type = 'radio';
    rFixed.name = `prop-window-${field.name}`;
    rFixed.checked = !isExpanding;
    radioFixed.append(rFixed, doc.createTextNode(' 固定窓'));

    const num = doc.createElement('input');
    num.type = 'number';
    num.min = '1';
    num.step = '1';
    num.className = 'prop-input prop-input-number';
    num.disabled = isExpanding;
    num.value = isExpanding ? '' : String(field.value);

    const sync = () => {
      if (rExp.checked) {
        num.disabled = true;
        this._values[field.name] = 'expanding';
      } else {
        num.disabled = false;
        this._values[field.name] = num.value === '' ? null : Number(num.value);
      }
      this._onChange();
    };
    rExp.addEventListener('change', sync);
    rFixed.addEventListener('change', sync);
    num.addEventListener('input', sync);

    wrap.append(radioExp, radioFixed, num);
    return wrap;
  }

  // ---- スタイル タブ（SERIES_DEF 単位の色/線幅/線種・最小可・§6.1）------------
  _buildStylePane() {
    const doc = this._doc;
    const pane = doc.createElement('div');
    pane.className = 'prop-pane';
    pane.dataset.propPane = 'style';

    const note = this._buildAMethodNote();
    if (note) {
      pane.append(note);
    }

    this._styleState = [];
    const series = this._def.series ?? [];
    series.forEach((s, idx) => {
      const row = doc.createElement('div');
      row.className = 'prop-style-row';

      const name = doc.createElement('span');
      name.className = 'prop-style-name';
      name.textContent = s.seriesName ?? (s.dynamic ? '(動的系列)' : `系列${idx + 1}`);

      const color = doc.createElement('input');
      color.type = 'color';
      color.className = 'prop-input prop-input-color';
      color.value = toHex(s.colorRule ?? '#2962ff');

      const width = doc.createElement('input');
      width.type = 'number';
      width.min = '1';
      width.step = '1';
      width.className = 'prop-input prop-input-number';
      width.value = String(s.width ?? 1);

      const style = doc.createElement('select');
      style.className = 'prop-input prop-input-select';
      for (const st of ['solid', 'dotted', 'dashed']) {
        const o = doc.createElement('option');
        o.value = st;
        o.textContent = st;
        if ((s.style ?? 'solid') === st) o.selected = true;
        style.append(o);
      }

      const state = { seriesName: s.seriesName, color, width, style };
      this._styleState.push(state);
      row.append(name, color, width, style);
      pane.append(row);
    });
    return pane;
  }

  // ---- 可視性 タブ（系列単位の表示/非表示・最小可・§6.2）---------------------
  _buildVisibilityPane() {
    const doc = this._doc;
    const pane = doc.createElement('div');
    pane.className = 'prop-pane';
    pane.dataset.propPane = 'visibility';

    const visNote = this._buildAMethodNote();
    if (visNote) {
      pane.append(visNote);
    }

    this._visibilityState = [];
    const series = this._def.series ?? [];
    series.forEach((s, idx) => {
      const row = doc.createElement('label');
      row.className = 'prop-visibility-row';
      const cb = doc.createElement('input');
      cb.type = 'checkbox';
      cb.checked = true;
      const name = doc.createElement('span');
      name.textContent = s.seriesName ?? (s.dynamic ? '(動的系列)' : `系列${idx + 1}`);
      row.append(cb, name);
      this._visibilityState.push({ seriesName: s.seriesName, checkbox: cb });
      pane.append(row);
    });
    return pane;
  }

  // A 方式の可動差を明示する注記要素（§9.3・H-1・サイレント不一致を作らない）。
  //   B 方式（served）では params が実反映されるため注記を出さない（null を返す）。
  //   A 方式（file://）でのみ注記を生成して返す（モード明示・サイレント化しない）。
  _buildAMethodNote() {
    if (this._mode === 'b') {
      return null;
    }
    const doc = this._doc;
    const note = doc.createElement('div');
    note.className = 'prop-a-method-note';
    note.dataset.aMethodNote = '1';
    note.textContent = A_METHOD_NOTE;
    return note;
  }

  // ---- フッター（デフォルト/キャンセル/OK）-----------------------------------
  _buildFooter() {
    const doc = this._doc;
    const footer = doc.createElement('div');
    footer.className = 'prop-footer';

    const def = doc.createElement('button');
    def.type = 'button';
    def.className = 'prop-btn prop-btn-default';
    def.textContent = 'デフォルト';
    def.addEventListener('click', () => this._onDefaultClick());

    const spacer = doc.createElement('span');
    spacer.className = 'prop-footer-spacer';

    const cancel = doc.createElement('button');
    cancel.type = 'button';
    cancel.className = 'prop-btn prop-btn-cancel';
    cancel.textContent = 'キャンセル';
    cancel.addEventListener('click', () => this._onCancelClick());

    const ok = doc.createElement('button');
    ok.type = 'button';
    ok.className = 'prop-btn prop-btn-ok';
    ok.textContent = 'OK';
    ok.addEventListener('click', () => this._onOkClick());
    this._okBtn = ok;

    footer.append(def, spacer, cancel, ok);
    return footer;
  }

  // ---- イベント---------------------------------------------------------------

  _onChange() {
    this._revalidate();
    this._refreshEnabled();
    this._refreshVisible();
  }

  // F-11 リアルタイム検証（§5）。違反をインライン表示し OK を制御する。
  // 条件付き非表示（conditionalVisible=false）のフィールドは検証対象外とする
  //   （隠れた bins が既定 60 のままなら妥当だが、空値等でも OK を阻害させないため・トグル安全化）。
  _revalidate() {
    const { violations } = validateForm(this._def, this._values);
    const visible = computeVisible(this._def, this._values);
    // 非表示フィールドの違反は表示せず OK も阻害しない。
    const effective = violations.filter((v) => visible[v.param] !== false);
    // 全フィールドのエラー表示をクリア。
    for (const [, els] of this._fieldEls) {
      els.error.textContent = '';
      els.row.classList.remove('is-invalid');
    }
    for (const v of effective) {
      const els = this._fieldEls.get(v.param);
      if (els) {
        els.error.textContent = humanizeKey(v.constraint);
        els.row.classList.add('is-invalid');
      }
    }
    const ok = effective.length === 0;
    if (this._okBtn) {
      this._okBtn.disabled = !ok;
    }
    return ok;
  }

  // 条件付き有効化（§3.5）。disabled のフィールド行をグレーアウト。
  _refreshEnabled() {
    const enabled = computeEnabled(this._def, this._values, this._context);
    for (const [name, els] of this._fieldEls) {
      const on = enabled[name] !== false;
      els.row.classList.toggle('is-disabled', !on);
      if (els.control) {
        // コントロール（および内部の入力要素）を無効化。
        if ('disabled' in els.control) {
          els.control.disabled = !on;
        }
        for (const inner of els.control.querySelectorAll ? els.control.querySelectorAll('input,select,button') : []) {
          inner.disabled = !on;
        }
      }
    }
    // ISSUE-080: ENUM の option 単位無効化（optionEnable 述語）。select の各 option へ反映する
    //   （mode/timeframe 変化に動的追従＝行の conditionalEnable と同じ再評価タイミング）。
    //   選択中の値が無効化されたときは**最初の有効 option へ自動切替**する（灰色のまま選択が残ると
    //   OK で無効組合せが保存され実行時ガードで空表示になるため。切替はダイアログ上で可視＝
    //   黙った代替ではない。例: 日別×1分で src=zp → 滞在時間 へ跳ぶ）。
    for (const pdef of this._def.params ?? []) {
      if (typeof pdef.optionEnable !== 'function') {
        continue;
      }
      const els = this._fieldEls.get(pdef.name);
      const sel = els && els.control && els.control.tagName === 'SELECT'
        ? els.control
        : els && els.control && els.control.querySelector ? els.control.querySelector('select') : null;
      if (!sel) {
        continue;
      }
      let firstEnabled = null;
      let currentDisabled = false;
      for (const opt of sel.options ?? []) {
        const raw = (pdef.enumValues ?? []).find((v) => String(v) === opt.value) ?? opt.value;
        const ok = !!pdef.optionEnable(raw, this._values, this._context);
        opt.disabled = !ok;
        if (ok && firstEnabled === null) {
          firstEnabled = raw;
        }
        if (!ok && String(this._values[pdef.name]) === opt.value) {
          currentDisabled = true;
        }
      }
      if (currentDisabled && firstEnabled !== null) {
        this._values[pdef.name] = firstEnabled;
        sel.value = String(firstEnabled);
        this._refreshVisible(); // src 連動の表示（period 行など）も追従させる。
      }
    }
  }

  // 条件付き表示（§3.5 拡張・トグル）。conditionalVisible=false のフィールド行を非表示にする。
  //   _refreshEnabled（グレーアウト）と対称の動的経路。range を変えた瞬間に「ビン」行が出没する。
  //   静的除外（uiVisible===false）は buildFormModel が担い、本メソッドは動的トグルのみ担う。
  _refreshVisible() {
    const visible = computeVisible(this._def, this._values);
    for (const [name, els] of this._fieldEls) {
      const on = visible[name] !== false;
      els.row.style.display = on ? '' : 'none';
    }
  }

  _onDefaultClick() {
    const defaults = resetToDefaults(this._def);
    this._values = { ...defaults };
    // 再構築（フォーム内のみ・OK 押下まで適用しない・§7.1）。
    this._rebuildBody();
  }

  _rebuildBody() {
    const body = this._panel.querySelector('.prop-body');
    if (!body) return;
    const newBody = this._buildBody();
    body.replaceWith(newBody);
    this._switchTab(this._activeTab);
    this._revalidate();
    this._refreshEnabled();
    this._refreshVisible();
  }

  _onOkClick() {
    if (!this._revalidate()) {
      return; // 違反時は適用しない。
    }
    const values = { ...this._values };
    const variant = this._variant;
    this.close();
    // variant は params とは別経路（recompute の variant 引数）で渡す。
    this._onApply(values, variant);
  }

  _onCancelClick() {
    this.close();
    this._onCancel();
  }

  // ---- ドラッグ移動（Pointer Events・§2.3）-----------------------------------
  _onDragStart(ev) {
    if (!this._panel) return;
    this._drag = { startX: ev.clientX, startY: ev.clientY, baseX: this._offset.x, baseY: this._offset.y };
    const move = (e) => this._onDragMove(e);
    const up = () => {
      this._doc.removeEventListener('pointermove', move);
      this._doc.removeEventListener('pointerup', up);
      this._drag = null;
    };
    this._doc.addEventListener('pointermove', move);
    this._doc.addEventListener('pointerup', up);
  }

  _onDragMove(ev) {
    if (!this._drag) return;
    this._offset = {
      x: this._drag.baseX + (ev.clientX - this._drag.startX),
      y: this._drag.baseY + (ev.clientY - this._drag.startY),
    };
    this._panel.style.transform = `translate(${this._offset.x}px, ${this._offset.y}px)`;
  }
}

// rgba(...)/#rgb/#rrggbb を <input type=color> 用の #rrggbb へ近似変換する。
// 解析不能な値は安全な既定（#2962ff）を返す（プロトタイプ・スタイルタブ最小可）。
export function toHex(value) {
  if (typeof value !== 'string') return '#2962ff';
  const v = value.trim();
  if (/^#[0-9a-fA-F]{6}$/.test(v)) return v.toLowerCase();
  if (/^#[0-9a-fA-F]{3}$/.test(v)) {
    return ('#' + v.slice(1).split('').map((c) => c + c).join('')).toLowerCase();
  }
  const m = v.match(/^rgba?\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)/i);
  if (m) {
    const toByte = (n) => Math.max(0, Math.min(255, Math.round(Number(n))));
    const hex = (n) => toByte(n).toString(16).padStart(2, '0');
    return ('#' + hex(m[1]) + hex(m[2]) + hex(m[3])).toLowerCase();
  }
  return '#2962ff';
}
