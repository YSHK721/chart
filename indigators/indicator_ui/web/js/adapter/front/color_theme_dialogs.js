// color_theme_dialogs.js — 指標カラーテーマの編集／管理モーダル DOM（両アプリ共有）。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.1
//   §6.3（編集ダイアログ＝テーマ名／トークン 14 行／各行は色スウォッチ＋現在値表示＋
//        「未指定に戻す」／初期表示はすべて未指定＝恒等テーマ／時間足の明度差は「使う」チェック
//        （OFF＝`tfModifier: null`）＋各足の数値入力（-1.00〜1.00・0.01 刻み）／保存押下時に
//        正規化名が既存と一致したら確認 1 段）、
//   §6.2（管理ダイアログ＝改名・削除。「テーマなし（既定色）」は固定行＝管理対象ではない）、
//   §5.7 F-C1（検証失敗は保存せずダイアログ内へインライン表示。既存データは不変）、
//   §7.1（controller を知らない＝DIP。日本語ラベルの写像は本モジュールが持つ）。
//
// 参照実装（同型元）: chart_template_dialogs.js（2 枚のダイアログを 1 モジュールが所有・複数形。
//   open で DOM 構築 → document.body へ追加、close で parentNode から除去。背景クリックでは
//   閉じない＝誤操作防止）。上書き確認 1 段のイディオムも同ファイルから写す。
//
// 責務（SRP）: DOM 構築とイベント配線のみ。名前検証・正規化・永続化・テーマ操作は注入コールバック
//   （onSubmit / onRename / onDelete / findExisting）へ委譲し、結果 { ok, code } を文言へ写像する。
//
// OCP（行の生成元）: トークン行は `COLOR_ROLES`、時間足行は `TF_CODES` から生成する。手書きの配列を
//   持たないため、台帳へ 1 語（1 足）足せばダイアログが自動で追随する。手書きにするとダイアログ
//   だけが取り残される（時間足メニューが ISSUE-123 で台帳導出へ移った理由と同じ）。
//
// 依存（§7.8 内向きのみ）: domain の台帳 2 本と値の正規化、usecase の導出 1 本。adapter・協働子は
//   参照しない。導出（usecase）を adapter が参照するのは層として正しい（内向き）。ダイアログが
//   導出規則を持つのではなく、**唯一の導出実装に問い合わせて表示する**だけである。

import { COLOR_ROLES } from '../../domain/color_roles.js';
import { normalizeHexColor } from '../../domain/color_value.js';
import { TF_CODES } from '../../domain/tf_meta.js';
import { expandRoleColors } from '../../usecase/color_derivation.js';
import {
  DIAGNOSTIC, DIRECTION_CONTRAST_MIN, SURFACE_CONTRAST_MIN, diagnoseTheme,
} from '../../usecase/color_diagnostics.js';

// トークン → 日本語ラベル（§6.3 の表記）。domain に置かない（domain は表示を知らない・§7.1）。
// ラベルは**具体物を先頭**に置く（実使用フィードバック 2026-08-09「ローソク足の色が変更できない」）。
//   機能は動いていたが、行ラベルが「強気・上方向」で「ローソク」の語がヒントにしか無く、探しても
//   見つからなかった。ラベル列は左端固定幅・本文色で 16 行を走査するときに読まれる唯一の列であり、
//   ヒント（右端・11px・最も弱い色）は走査対象にならない。語彙も行数も増やさずに語順だけで直す。
const ROLE_LABEL = Object.freeze({
  bullish: 'ローソク陽線・強気',
  bearish: 'ローソク陰線・弱気',
  neutral: '基準・中立',
  alert: '警戒・外れ値',
  primary: '主出力',
  secondary: '副出力',
  range: '通常域',
  level: '参照水準',
  muted: '非強調',
  surface: '面',
  grid: '目盛線',
  border: '境界',
  text: '文字',
  highlight: '現在地',
  accent: '操作の主役',
  danger: '破壊・エラー',
  profit: '売買ペア線 利益・勝ち',
  loss: '売買ペア線 損失・負け',
});

// トークン → 「この色が使われる場所」の短い併記（§6.3）。指標とクロムの双方に効くことを示す。
const ROLE_HINT = Object.freeze({
  bullish: 'ローソク陽線 / 現在値（上げ）/ 上伸バンド',
  bearish: 'ローソク陰線 / 現在値（下げ）/ 下落バンド',
  neutral: '回帰平均線 / 0% 基準線 / 中心線',
  alert: '外れ値水準 / GPD 線 / オフセット',
  primary: '各指標の本体線・本体ヒストグラム',
  secondary: '平滑線 / シグナル線 / 副オシレータ',
  range: '分位バンド / BB 上下 / 確率帯',
  level: '水平水準線群',
  muted: '読取欄専用の系列',
  surface: 'チャート背景（減光・分析 tint も追従）',
  grid: 'グリッド縦横',
  border: '価格軸線 / 時間軸線 / pane 区切り',
  text: '軸ラベル',
  highlight: '現在値ライン',
  accent: '選択中の項目 / フォーカス枠 / 主ボタン',
  danger: 'エラー表示 / 削除ボタン / ライブ追従の点灯',
  profit: '売買ペア線（勝ち）/ 取引明細の利益',
  loss: '売買ペア線（負け）/ 取引明細の損失',
});

// 台帳に無いキーはトークン名をそのまま表示する（§7.1・§6.3）。全域的＝例外を投げない。
export function labelForRole(token) {
  const key = String(token ?? '');
  return ROLE_LABEL[key] ?? key;
}

export function hintForRole(token) {
  return ROLE_HINT[String(token ?? '')] ?? '';
}

// code（usecase/color_themes.js の CODE 語彙）→ 表示文言。未知 code は既定文言へ倒す（F-C1）。
const MESSAGE = Object.freeze({
  empty: '名前を入力してください。',
  too_long: '名前は 40 文字以内で入力してください。',
  duplicate: '同じ名前のテーマが既にあります。',
  limit: 'テーマは 50 件までです。不要なテーマを削除してください。',
  not_found: '対象のテーマが見つかりません。',
});

function messageFor(code) {
  return MESSAGE[code] ?? '保存できませんでした。';
}

// 未指定（＝そのトークンは現行の既定色のまま）を表す表示文字列（§6.3）。
const UNSET_TEXT = '未指定';
// `<input type="color">` は値を空にできないため、未指定状態でも見た目上の値が要る。
//   「未指定かどうか」は value から**推論しない**（下の USE_LABEL のトグルが唯一の持ち主）。
const SWATCH_INITIAL = '#000000';
// 宣言の有無を表す明示トグルの文言（§6.3 の「未指定に戻す」と対になる）。
//   推論（input / change の発火の有無）にすると、値が変わらない選択＝スウォッチ初期値と同じ色
//   （黒 #000000）はイベントが出ないため永久に宣言できない。状態は行が明示的に持つ。
const USE_LABEL = 'この色を使う';
// 未指定だが**導出できる**トークンの現在値表示（段階 5-C）。
//   これが無いと「この色を未指定にしたら何色になるか」が見えず、導出は「決める項目を減らす」
//   機能として成立しない（未指定にするのが怖いので結局 14 行を埋めることになる）。
//   接頭辞で宣言値（`#rrggbb`）と区別する — 宣言の有無を持つのはトグルであって表示ではないが、
//   表示が両者を**見分けられる**必要はある。
const DERIVED_PREFIX = '自動 ';

export class ColorThemeDialogs {
  // document: DOM 実装（注入）。null 可（DOM 不在時は各メソッドが no-op＝防御）。
  constructor({ document: doc = null } = {}) {
    this._doc = doc;
    this._root = null;
    // 閉じるときに 1 度だけ走る後始末（プレビューの解除・段階 5-C-3）。null＝後始末なし。
    this._onDismiss = null;
  }

  _usable() {
    const doc = this._doc;
    return !!(doc && typeof doc.createElement === 'function' && doc.body && typeof doc.body.append === 'function');
  }

  // 閉じる経路はすべてここを通る（キャンセル・×・保存成功・別ダイアログによる置き換え）。
  //   よって後始末（プレビュー解除）をここ 1 箇所に置けば、取り残しが構成上できない。
  //   先に null 化してから呼ぶ＝閉じた後に close を重ねても 2 度発火しない（冪等）。
  close() {
    if (this._root && this._root.parentNode) {
      this._root.parentNode.removeChild(this._root);
    }
    this._root = null;
    const dismiss = this._onDismiss;
    this._onDismiss = null;
    if (typeof dismiss === 'function') {
      dismiss();
    }
  }

  // ---- 共通の殻（背景＋パネル＋ヘッダ＋本文＋エラー＋フッタ）------------------
  _openShell(kind, title) {
    this.close(); // 同時に 2 枚開かない（後勝ち）。
    const doc = this._doc;
    const root = doc.createElement('div');
    root.className = 'theme-dialog-backdrop is-open';
    root.dataset.themeDialog = kind;

    const panel = doc.createElement('div');
    panel.className = 'theme-dialog';
    if (typeof panel.setAttribute === 'function') {
      panel.setAttribute('role', 'dialog');
    }

    const head = doc.createElement('div');
    head.className = 'theme-dialog-head';
    const titleEl = doc.createElement('span');
    titleEl.className = 'theme-dialog-title';
    titleEl.textContent = title;
    const closeBtn = doc.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'theme-dialog-close';
    closeBtn.dataset.themeAction = 'cancel';
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', () => this.close());
    head.append(titleEl, closeBtn);

    const body = doc.createElement('div');
    body.className = 'theme-dialog-body';

    const error = doc.createElement('div');
    error.className = 'theme-dialog-error';
    error.dataset.themeError = kind;
    error.textContent = '';

    const foot = doc.createElement('div');
    foot.className = 'theme-dialog-foot';

    panel.append(head, body, error, foot);
    root.append(panel);
    // 背景クリックでは閉じない（誤操作防止・chart_template_dialogs と同方針）。
    root.addEventListener('mousedown', (ev) => {
      if (ev && ev.target === root && typeof ev.stopPropagation === 'function') {
        ev.stopPropagation();
      }
    });
    doc.body.append(root);
    this._root = root;
    return {
      root, body, error, foot,
    };
  }

  _button(label, action, className) {
    const btn = this._doc.createElement('button');
    btn.type = 'button';
    btn.className = className;
    btn.dataset.themeAction = action;
    btn.textContent = label;
    return btn;
  }

  _section(title) {
    const el = this._doc.createElement('div');
    el.className = 'theme-dialog-section';
    el.textContent = title;
    return el;
  }

  // ---- UC-C01「新しいテーマを作成…」（§6.3）---------------------------------
  /**
   * @param {object} opts
   * @param {string}   [opts.title]  ヘッダ文言。
   * @param {function} [opts.onSubmit] ({ name, roleColors, tfModifier }) => { ok, code }。
   * @param {?function}[opts.findExisting] (name) => COLOR_THEME|null。正規化名が一致する既存テーマを
   *   返す判定器（usecase の純関数を注入する。本ダイアログは文字列比較を持たない）。未注入時は
   *   確認を挟まない。
   */
  /**
   * テーマの編集ダイアログを開く。
   *
   * `theme` を渡すと**その内容を初期値**として開く（保存済みテーマの編集）。渡さなければ
   * 従来どおり「名前は空・14 行すべて未指定」で開く（新規作成）。
   *
   * なぜ初期値が要るか: §5.1 処理 2 は同名保存で `roleColors` を**丸ごと置換**する。初期値が
   * 無いと、1 色だけ直したいときにも 14 行を再入力するしかなく、入力し忘れたトークンは
   * 消える。「一度確定したテーマは変更できない」状態がここから生まれていた。置換の規則自体は
   * 変えない（変えるべきは置換前の値を編集の出発点にできること）。
   */
  openEdit({
    title = 'テーマを作成', onSubmit = () => ({ ok: true }), findExisting = null, theme = null,
    onPreview = null,
  } = {}) {
    if (!this._usable()) {
      return null;
    }
    const doc = this._doc;
    const {
      root, body, error, foot,
    } = this._openShell('edit', title);

    // テーマ名（初期値＝空・§6.3）。
    const nameRow = doc.createElement('div');
    nameRow.className = 'theme-dialog-row';
    const nameLabel = doc.createElement('span');
    nameLabel.className = 'theme-dialog-label';
    nameLabel.textContent = 'テーマ名';
    const nameInput = doc.createElement('input');
    nameInput.type = 'text';
    nameInput.className = 'theme-dialog-input';
    nameInput.dataset.themeField = 'name';
    // 編集なら既存名を初期表示する（毎回打ち直させない）。新規は空。
    nameInput.value = theme && typeof theme.name === 'string' ? theme.name : '';
    nameRow.append(nameLabel, nameInput);
    body.append(nameRow);

    // 意味の色（トークン 14 行）。行は台帳から生成する（手書き配列を持たない・§6.3 OCP）。
    body.append(this._section('意味の色'));
    //: token -> { swatch, value, specified }。保存時にここだけを読む（DOM を再走査しない）。
    const slots = new Map();
    const initialColors = (theme && theme.roleColors && typeof theme.roleColors === 'object')
      ? theme.roleColors : {};
    // 状態が変わるたびに走る 1 本（呼び出し点を増やさない）。表示の作り直し・プレビュー・診断が
    //   ここに集約される。行はこれを呼ぶだけで、何が起きるかを知らない。
    //   `emitDraft` は時間足の要素を参照するため、実体は下（時間足セクション構築後）で束ねる。
    const onChanged = () => {
      renderDiagnostics(doc, diagnostics, renderRoleRows(slots));
      emitDraft();
    };
    for (const token of COLOR_ROLES) {
      body.append(this._buildRoleRow(token, slots, initialColors[token], onChanged));
    }
    // 診断の置き場（段階 5-C-4）。トークン行の直後＝指摘の対象が見えている位置に置く。
    //   **保存を妨げない**ため、フッタ（保存ボタン）とは独立した領域にする。
    const diagnostics = doc.createElement('div');
    diagnostics.className = 'theme-dialog-diagnostics';
    diagnostics.dataset.themeDiagnostics = 'edit';
    body.append(diagnostics);

    // 初期表示（新規は全行未指定・編集は theme の宣言）からも導出と診断を見せる。
    //   **開いただけではプレビューしない**（開くのは状態の変更ではない）。
    renderDiagnostics(doc, diagnostics, renderRoleRows(slots));

    // 時間足による明度差（指標系列のみ・§6.3・§4.7 で地には効かない）。
    body.append(this._section('時間足による明度差（指標系列のみ・チャートの地には効きません）'));
    const tfCheck = doc.createElement('label');
    tfCheck.className = 'theme-dialog-check';
    const tfToggle = doc.createElement('input');
    tfToggle.type = 'checkbox';
    tfToggle.dataset.themeField = 'tf-enabled';
    tfToggle.checked = false; // OFF＝tfModifier: null（§6.3）。
    const tfText = doc.createElement('span');
    tfText.textContent = '使う';
    tfCheck.append(tfToggle, tfText);
    body.append(tfCheck);

    tfToggle.addEventListener('change', () => onChanged());

    const tfGrid = doc.createElement('div');
    tfGrid.className = 'theme-dialog-tf-grid';
    //: tf -> input。時間足の行は台帳（TF_CODES）から生成する（§6.3）。
    const tfInputs = new Map();
    for (const code of TF_CODES) {
      tfGrid.append(this._buildTfCell(code, tfInputs, onChanged));
    }
    body.append(tfGrid);

    // 下書きの組み立て（保存時と**同じ 2 本の collector** を使う＝プレビューと保存の食い違いを
    //   構成上作らない）。未注入なら no-op で、ダイアログの振る舞いは変わらない。
    function emitDraft() {
      if (typeof onPreview !== 'function') {
        return;
      }
      onPreview({
        roleColors: collectRoleColors(slots),
        tfModifier: tfToggle.checked ? collectTfModifier(tfInputs) : null,
      });
    }

    // 閉じたらプレビューを解除する（キャンセル・×・保存成功・置き換えのすべてが close を通る）。
    this._onDismiss = typeof onPreview === 'function' ? () => onPreview(null) : null;

    const cancel = this._button('キャンセル', 'cancel', 'theme-dialog-btn');
    cancel.addEventListener('click', () => this.close());
    const submit = this._button('保存', 'submit', 'theme-dialog-btn is-primary');

    // 上書き確認 1 段（§6.3・chart_template_dialogs.js の同型イディオム）。判定は注入された
    //   usecase 純関数のみが行う（本ダイアログは正規化・文字列比較を持たない）。
    let pendingOverwrite = null; // 確認中の対象テーマ（null＝通常の保存状態）。
    const clearOverwriteConfirm = () => {
      if (!pendingOverwrite) {
        return;
      }
      pendingOverwrite = null;
      submit.textContent = '保存';
      error.textContent = '';
      error.classList.remove('is-confirm');
    };
    // 名前の編集で確認を解除する（編集後の名前が別テーマと一致すれば再度確認に入る）。
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
      const result = onSubmit({
        // 編集で開いたときは対象の themeId を載せる。協働子はこれを見て「新規採番」ではなく
        //   「その席の更新」を行える（名前を変えても同じテーマを直したことになる）。
        //   新規作成では undefined＝従来どおり採番される。
        themeId: theme && typeof theme.themeId === 'string' ? theme.themeId : undefined,
        name: nameInput.value,
        roleColors: collectRoleColors(slots),
        tfModifier: tfToggle.checked ? collectTfModifier(tfInputs) : null,
      }) ?? { ok: true };
      if (result.ok) {
        this.close();
        return;
      }
      clearOverwriteConfirm();
      error.textContent = messageFor(result.code); // F-C1: ダイアログ内インライン表示。
    });
    foot.append(cancel, submit);

    if (typeof nameInput.focus === 'function') {
      nameInput.focus();
    }
    return root;
  }

  // 1 トークン行: [ラベル] [使うトグル] [スウォッチ] [現在値] [未指定に戻す] [用途の併記]。
  //   宣言の有無はトグルの checked が唯一の持ち主で、スウォッチの値とは独立に決まる。
  //   initialColor（編集時のみ）: この行が「宣言済み」で開き、スウォッチの初期値になる。
  //   onChanged: 状態が変わったことを開いている側へ知らせる（表示の作り直し・プレビュー・診断）。
  //   行の内部では「何が起きるか」を知らない（行は状態を変えるだけ）。
  _buildRoleRow(token, slots, initialColor = null, onChanged = () => {}) {
    const doc = this._doc;
    const declared = typeof initialColor === 'string' && /^#[0-9a-fA-F]{6}$/.test(initialColor);
    const row = doc.createElement('div');
    row.className = 'theme-dialog-role-row';
    row.dataset.themeRole = token;

    const label = doc.createElement('span');
    label.className = 'theme-dialog-role-label';
    label.textContent = labelForRole(token);

    // 明示状態（§6.3・「未指定に戻す」と対）。ここを推論に戻してはならない。
    const use = doc.createElement('label');
    use.className = 'theme-dialog-use';
    const useBox = doc.createElement('input');
    useBox.type = 'checkbox';
    useBox.className = 'theme-dialog-use-box';
    useBox.dataset.themeUse = token;
    // 新規は未指定（恒等テーマ・§6.3）。編集は既存の宣言をそのまま初期状態にする。
    useBox.checked = declared;
    if (typeof useBox.setAttribute === 'function') {
      useBox.setAttribute('title', USE_LABEL);
      useBox.setAttribute('aria-label', `${labelForRole(token)}: ${USE_LABEL}`);
    }
    use.append(useBox);

    const swatch = doc.createElement('input');
    swatch.type = 'color';
    swatch.className = 'theme-dialog-swatch';
    swatch.dataset.themeSwatch = token;
    // 走査用の別名（テスト・E2E がトークンで引ける）。値の持ち主は本要素のまま。
    swatch.dataset.themeToken = token;
    // 編集時は既存の色を初期値にする（1 色だけ直せる）。新規は未指定時の表示値。
    swatch.value = declared ? initialColor.toLowerCase() : SWATCH_INITIAL;

    const value = doc.createElement('span');
    value.className = 'theme-dialog-role-value';
    value.dataset.themeValue = token;
    value.textContent = declared ? String(swatch.value) : UNSET_TEXT;

    const clear = doc.createElement('button');
    clear.type = 'button';
    clear.className = 'theme-dialog-btn theme-dialog-clear';
    clear.dataset.themeClear = token;
    clear.textContent = '未指定に戻す';

    const hint = doc.createElement('span');
    hint.className = 'theme-dialog-role-hint';
    hint.textContent = hintForRole(token);

    // 保存時に読むのはこの slot だけ（DOM を再走査しない）。specified の実体はトグルの checked。
    //   `value` も持つ: 表示は 1 行だけでは決まらない（導出の従属先が同時に動く）ため、
    //   14 行ぶんの表示を作る renderRoleRows がこの参照から書き直す。
    const slot = { swatch, useBox, value };
    slots.set(token, slot);
    // 表示（現在値欄）は状態の従属物。状態を変える入口はここ 1 箇所に集約する。
    //   状態を変えたら 14 行**すべて**の表示を作り直す（この行の従属先が取り残されないように）。
    const setSpecified = (on) => {
      useBox.checked = on;
      onChanged();
    };
    useBox.addEventListener('change', () => setSpecified(!!useBox.checked));
    // 色を選ぶ操作は「この色を使う」の意思表示でもあるためトグルを ON へ揃える（表示と状態を
    //   ずらさない）。宣言の有無そのものはトグルが持つので、イベントが出ない選択でも黒を
    //   宣言できる（トグルだけで完結する）。
    const onPick = () => setSpecified(true);
    swatch.addEventListener('input', onPick);
    swatch.addEventListener('change', onPick);
    clear.addEventListener('click', () => setSpecified(false));

    row.append(label, use, swatch, value, clear, hint);
    return row;
  }

  // 1 時間足セル: [足コード] [数値入力]。値域・刻みは §6.3（-1.00〜1.00・0.01）。
  _buildTfCell(code, tfInputs, onChanged = () => {}) {
    const doc = this._doc;
    const cell = doc.createElement('label');
    cell.className = 'theme-dialog-tf-cell';
    const label = doc.createElement('span');
    label.className = 'theme-dialog-tf-label';
    label.textContent = code;
    const input = doc.createElement('input');
    input.type = 'number';
    input.className = 'theme-dialog-tf-input';
    input.dataset.themeTf = code;
    input.min = '-1';
    input.max = '1';
    input.step = '0.01';
    input.value = '';
    // 時間足の数値入力は、トークン行と並ぶ**もう 1 つの状態の入口**。同じ onChanged を通す。
    input.addEventListener('input', () => onChanged());
    input.addEventListener('change', () => onChanged());
    tfInputs.set(code, input);
    cell.append(label, input);
    return cell;
  }

  // ---- UC-C03「管理（名前を変更・削除）…」（§6.2・§5.3）--------------------
  /**
   * @param {object} opts
   * @param {Array}    [opts.themes]   COLOR_THEME[]（宣言順に一覧）。固定行は含めない。
   * @param {function} [opts.onRename] (themeId, name) => { ok, code }。
   * @param {function} [opts.onDelete] (themeId) => void（確認 1 段の後に呼ばれる）。
   */
  openManage({
    themes = [], onRename = () => ({ ok: true }), onDelete = () => {}, onEdit = null,
  } = {}) {
    if (!this._usable()) {
      return null;
    }
    const doc = this._doc;
    const {
      root, body, error, foot,
    } = this._openShell('manage', 'テーマの管理');

    const list = doc.createElement('div');
    list.className = 'theme-dialog-list';
    // 「テーマなし（既定色）」は選択中テーマ無しの表現であってテーマ集合の要素ではないため
    //   一覧に出さない（出すと削除・改名の対象になり得る席ができる・§6.2）。
    for (const t of themes) {
      list.append(this._buildManageRow(t, {
        error, onRename, onDelete, onEdit,
      }));
    }
    body.append(list);

    const close = this._button('閉じる', 'cancel', 'theme-dialog-btn');
    close.addEventListener('click', () => this.close());
    foot.append(close);
    return root;
  }

  // 管理ダイアログの 1 行: [名前] [色を編集] [名前を変更] [削除]。操作面は行内で切り替える。
  //   「色を編集」は保存済みテーマの色を直す唯一の導線（依頼者指示 2026-08-09）。これが無いと、
  //   一度確定したテーマは 14 行を再入力しない限り変更できない。
  _buildManageRow(t, {
    error, onRename, onDelete, onEdit = null,
  }) {
    const doc = this._doc;
    const row = doc.createElement('div');
    row.className = 'theme-dialog-list-row';
    row.dataset.themeRow = t.themeId;

    const name = doc.createElement('span');
    name.className = 'theme-dialog-list-name';
    name.textContent = t.name;

    const renameInput = doc.createElement('input');
    renameInput.type = 'text';
    renameInput.className = 'theme-dialog-input theme-dialog-rename is-hidden';
    renameInput.dataset.themeRenameInput = t.themeId;
    renameInput.value = t.name;
    const renameCommit = doc.createElement('button');
    renameCommit.type = 'button';
    renameCommit.className = 'theme-dialog-btn is-hidden';
    renameCommit.dataset.themeRenameCommit = t.themeId;
    renameCommit.textContent = '確定';

    const rename = doc.createElement('button');
    rename.type = 'button';
    rename.className = 'theme-dialog-btn';
    rename.dataset.themeRename = t.themeId;
    rename.textContent = '名前を変更';
    rename.addEventListener('click', () => {
      renameInput.classList.remove('is-hidden');
      renameCommit.classList.remove('is-hidden');
      rename.classList.add('is-hidden');
    });
    renameCommit.addEventListener('click', () => {
      error.textContent = '';
      const result = onRename(t.themeId, renameInput.value) ?? { ok: true };
      if (result.ok) {
        name.textContent = renameInput.value;
        renameInput.classList.add('is-hidden');
        renameCommit.classList.add('is-hidden');
        rename.classList.remove('is-hidden');
        return;
      }
      error.textContent = messageFor(result.code); // F-C1: インライン表示・閉じない。
    });

    // 削除は確認を 1 段挟む（§5.3・取り消し不能な状態変更のため）。
    const confirm = doc.createElement('button');
    confirm.type = 'button';
    confirm.className = 'theme-dialog-btn is-danger is-hidden';
    confirm.dataset.themeDeleteConfirm = t.themeId;
    confirm.textContent = '削除する';
    const remove = doc.createElement('button');
    remove.type = 'button';
    remove.className = 'theme-dialog-btn';
    remove.dataset.themeDelete = t.themeId;
    remove.textContent = '削除';
    remove.addEventListener('click', () => {
      confirm.classList.remove('is-hidden');
      remove.classList.add('is-hidden');
    });
    confirm.addEventListener('click', () => {
      onDelete(t.themeId);
      if (row.parentNode && typeof row.parentNode.removeChild === 'function') {
        row.parentNode.removeChild(row);
      }
    });

    const edit = this._button('色を編集', 'edit', 'theme-dialog-btn');
    edit.dataset.themeEdit = t.themeId;
    edit.addEventListener('click', () => {
      if (typeof onEdit === 'function') {
        onEdit(t.themeId);
      }
    });
    row.append(name, edit, renameInput, renameCommit, rename, confirm, remove);
    return row;
  }
}

// 14 行の表示を、現在の宣言集合から**まとめて**作り直す（段階 5-C-2）。
//
//   1 行の表示が自分の状態だけでは決まらないためまとめて行う: `surface` を変えると未指定の
//   grid / border / text / level / muted / highlight が同時に動き、`primary` を変えると
//   secondary / range / neutral が動く。行ごとに更新すると、変えた行の従属先が取り残される。
//
//   宣言の有無は**トグルが唯一の持ち主**という規律を壊さない（上の USE_LABEL のコメント参照）。
//   本関数は状態を 1 つも書き換えず、状態（useBox.checked と swatch.value）から表示を作るだけで
//   ある。導出値をスウォッチへ映すのは見た目のためで、宣言はしない（collectRoleColors は
//   useBox.checked しか見ない）。
//
//   導出元が揃わないトークン（例: surface 未宣言のときの grid）は expandRoleColors がキーを
//   生成しないため、従来どおり「未指定」＋スウォッチ既定色になる。ここで既定色を導出値のように
//   見せると「未指定にしたらこの色になる」という表示が嘘になる（実際は現行の既定色のまま）。
// 診断（usecase）の**事実**を日本語の一文へ写像する（段階 5-C-4）。
//
//   文言は adapter の責務であり、usecase は `{ code, tokens, measured }` という事実だけを返す
//   （純関数に表示を持たせない・§7.1 と同じ規律）。「何が・どれだけ」が分かる形にするため、
//   トークン名（日本語ラベル）と **measured（実測値）** と目安（閾値）を必ず含める。
//   閾値は usecase の公開定数をそのまま使う（ダイアログが数値を再宣言すると判定源が 2 つになる）。
function diagnosticText(diagnostic) {
  const names = diagnostic.tokens.map((t) => labelForRole(t)).join(' / ');
  if (diagnostic.code === DIAGNOSTIC.collision) {
    return `${names}: 同じ色です（${diagnostic.measured} 語）。画面上で意味を見分けられません。`;
  }
  if (diagnostic.code === DIAGNOSTIC.surfaceContrast) {
    return `${labelForRole(diagnostic.tokens[0])}: 面から浮き上がりません`
      + `（コントラスト比 ${diagnostic.measured.toFixed(2)}・目安 ${SURFACE_CONTRAST_MIN.toFixed(1)} 以上）。`;
  }
  if (diagnostic.code === DIAGNOSTIC.directionContrast) {
    return `${names}: 上下の向きを明るさで見分けられません`
      + `（コントラスト比 ${diagnostic.measured.toFixed(2)}・目安 ${DIRECTION_CONTRAST_MIN.toFixed(2)} 以上）。`;
  }
  return `${names}: ${diagnostic.measured}`;
}

// 診断の行を作り直す。**保存を妨げない**（保存ボタンに触れない・確認を挟まない）。
//   0 件のときは行を 1 本も作らない（「問題ありません」のような無内容な行はノイズにしかならない）。
//   診断対象は**射影後**の roleColors（導出込み）である。ユーザーが実際に見る色を診断しなければ
//   意味がない — 実測では白地 + 基点 5 語のとき、宣言していない導出色（neutral 2.647 /
//   range 1.577）が W-C2 を出す。宣言だけを見ていたらこの 2 語は見落とされる。
function renderDiagnostics(doc, box, roleColors) {
  box.innerHTML = '';
  for (const diagnostic of diagnoseTheme({ roleColors })) {
    const line = doc.createElement('div');
    line.className = 'theme-dialog-diagnostic';
    line.dataset.themeDiagnosticCode = diagnostic.code;
    line.textContent = diagnosticText(diagnostic);
    box.append(line);
  }
}

function renderRoleRows(slots) {
  const declared = {};
  for (const [token, slot] of slots) {
    if (!slot.useBox.checked) {
      continue;
    }
    // 導出の入力は保存形 hex6 でなければならない。書き方の吸収は domain の単一情報源に任せる
    //   （ダイアログが正規表現を持つと、受理集合の持ち主が 2 つになる）。
    const hex = normalizeHexColor(slot.swatch.value);
    if (hex !== null) {
      declared[token] = hex;
    }
  }
  const expanded = expandRoleColors(declared);
  // 射影後の色は診断（renderDiagnostics）も使うため返す。呼び出し側で導出をもう 1 度回すと、
  //   1 操作あたりの導出が 2 回になる（同じ入力・同じ結果の重複計算）。
  for (const [token, slot] of slots) {
    if (slot.useBox.checked) {
      slot.value.textContent = String(slot.swatch.value);
      continue;
    }
    const derived = expanded[token];
    if (typeof derived === 'string') {
      slot.value.textContent = `${DERIVED_PREFIX}${derived}`;
      slot.swatch.value = derived;
    } else {
      slot.value.textContent = UNSET_TEXT;
      slot.swatch.value = SWATCH_INITIAL;
    }
  }
  return expanded;
}

// 宣言されたトークンだけを集める。未指定は 1 件も載せない（＝恒等テーマ・§4.4）。
//   「宣言されたか」は明示トグル（useBox.checked）が答える。値との一致では判定しない
//   （＝スウォッチ初期値と同じ色でも宣言できる）。
//   値の正規化（#rrggbb 小文字化・解釈不能の棄却）は usecase（normalizeRoleColors）が行う。
function collectRoleColors(slots) {
  const out = {};
  for (const [token, slot] of slots) {
    if (slot.useBox.checked) {
      out[token] = String(slot.swatch.value);
    }
  }
  return out;
}

// 全時間足のキーを持つ写像を返す。未入力・数値でない入力は 0（＝変化なし・§6.3）。
//   クランプ・丸めは usecase（normalizeTfModifier）が行う（判定源を 2 つ作らない）。
function collectTfModifier(tfInputs) {
  const out = {};
  for (const [code, input] of tfInputs) {
    const n = Number(input.value);
    out[code] = Number.isFinite(n) && String(input.value).trim() !== '' ? n : 0;
  }
  return out;
}
