// PropertiesDialog（adapter/front/properties_dialog.js）— インジケーター・プロパティ ダイアログ DOM。
//
// 設計入力: 内部設計_パラメータ設定ダイアログ.md v0.2.0
//   §2 ダイアログ構造（タイトル/タブ/フッター・ドラッグ・×閉じる）
//   §3 PARAM_DEF→コントロール写像 / §3.4 グループ / §3.5 条件付き有効化 / §3.6 ツールチップ
//   §5 リアルタイム検証（F-11・OK 制御）/ §6 スタイル・可視性タブ / §7 振る舞い・状態遷移
//   §8.2 PropertiesDialog 署名 / §8.4 upstream JS API 非依存。
//
// 本ファイルの責務は **DOM 構築とイベント配線のみ**（ISSUE-502 段階 4D で宣言と実装を一致させた）。
//   値・規則・状態はいずれも協働子が所有し、本クラスはその判定を DOM へ写すだけを行う:
//     - フォーム値の所有と検証判定  … usecase/property_form_state.js（PropertyFormState）
//     - コントロール生成           … adapter/front/property_control_builders.js（凍結テーブル）
//     - 行モデル（系列 → 表示行）  … usecase/form_model.js（buildSeriesStyleRows）
//     - 表示形式の語彙と永続差分   … usecase/series_style_forms.js（台帳＋純関数）
//     - ドラッグ移動               … adapter/front/dialog_drag_controller.js
//
// ★ upstream JS API（addLineSeries / createPriceLine / setData / applyOptions 等）は
//   一切参照しない（§8.4・母体 §9 grep 0 件規律）。描画反映は onApply（→ ChartRenderer/facade 経由）。

import { buildFormModel, buildSeriesStyleRows } from '../../usecase/form_model.js';
// フォーム値の所有と検証判定（DOM 非依存）。本クラスは値を持たず、この状態器へ委譲する。
import { PropertyFormState } from '../../usecase/property_form_state.js';
// スタイルタブの表示形式は台帳が唯一源（選択肢・初期値・永続差分の分解が同じ宣言から出る）。
//   ここに 'dot' / 'bar' / 線種名を直書きすると、選択肢と保存側が別々に古くなる。
import {
  DEFAULT_LINE_STYLE,
  collectSeriesStyleDiff,
  displayFormInitial,
  displayFormOptions,
  usesUnifiedDisplayForm,
} from '../../usecase/series_style_forms.js';
import { seriesKind } from '../../domain/series_kind.js';
// control_type → コントロール生成器のテーブル（ISSUE-181・OCP）。生成手続き本体と
//   ラベル化/色変換の純関数は adapter/front/property_control_builders.js が所有する。
import {
  buildControl,
  buildSegmented,
  humanizeKey,
  toHex,
} from './property_control_builders.js';
// ドラッグ移動（Pointer Events）は独立モジュールが所有する（ISSUE-502 段階 4D）。
import { DialogDragController } from './dialog_drag_controller.js';
// 期間プリセット（基本設計_期間プリセット.md §6.5）: 実効計算時間足の解決は usecase の純関数へ委譲する。
import { effectiveTimeframe } from '../../usecase/period_presets.js';
// 時間足 → 表示ラベル（'1h'→'1時間'）の単一情報源（timeframe_menu.js・ISSUE-123）。
//   期間プリセット一覧の見出し「◯◯足 基準」に使う（ラベルを二重定義しない）。
import { timeframeLabels } from './timeframe_menu.js';
// 段階 5-E: 系列の既定色は解決順ステップ 5 の単一情報源（color_resolver）を読む。
//   ここに '#2962ff' を書くと、既定色を変えたときに片方だけ古い値で残る（複製＝二重定義）。
import { DEFAULT_SERIES_COLOR } from '../../usecase/color_resolver.js';

// toHex は本モジュールの公開面として維持する（既存の import 元を変えない・ISSUE-181）。
export { toHex };

export class PropertiesDialog {
  // { document, def, instance, onApply, onCancel }
  //   def      : IndicatorDef（catalog.get の戻り）
  //   instance : AppliedInstance（params/variant を持つ。null 可＝既定値で開く）
  //   onApply  : (values) => void   OK 押下時に収集値を渡す（→ recomputeInstance）
  //   onCancel : () => void         キャンセル/×/背景時（任意）
  constructor({
    document: doc, def, instance = null, context = {},
    seriesStyles = null, seriesTabs = true, onApply = () => {}, onCancel = () => {},
  }) {
    this._doc = doc;
    this._def = def;
    this._instance = instance;
    // context: computeEnabled の関数述語へ渡す外部状態（例 { timeframe, servedMode }）。
    //   ISSUE-070: mode=sessions×対応tf の解像度グレーアウト判定に timeframe が要る（param 値外）。
    this._context = context || {};
    // seriesStyles（ISSUE-109）: 実描画中の系列スタイル [{ name, kind, color, width, style, visible }]
    //   （renderer.getSeriesStyles の戻り）。スタイル/可視性タブの行と初期値の実体。null=未供給
    //   （後方互換: def.series からの静的フォールバック表示）。
    this._seriesStyles = Array.isArray(seriesStyles) ? seriesStyles : null;
    // seriesTabs=false（ISSUE-109・MP 等）: 系列スタイルを持たない指標はスタイル/可視性タブ自体を
    //   出さない（ダミー行の露出をやめる）。
    this._seriesTabs = seriesTabs !== false;
    this._onApply = onApply;
    this._onCancel = onCancel;

    // フォーム値（params / variant / 未解決入力エラー）と検証判定の所有者。
    //   本クラスは値を持たず、_values / _variant は下のアクセサでこの状態器を指す
    //   （既存の呼び出し面を変えない）。
    this._form = new PropertyFormState({
      def,
      params: (instance && instance.params) || {},
      variant: instance && instance.variant,
      context: this._context,
    });

    this._activeTab = 'inputs';
    this._root = null; // ダイアログ最上位要素
    this._fieldEls = new Map(); // name -> { row, control, error, info }
    this._okBtn = null;
    // ドラッグ移動（Pointer Events・§2.3）。パネルは open で生成されるため遅延解決で渡す。
    this._dragController = new DialogDragController({
      document: this._doc,
      panel: () => this._panel,
    });

    // コントロール生成器との結合面（ControlContext）。値の所有者は PropertyFormState。
    //   getValue/setValue は呼び出し時解決の遅延アクセサ＝デフォルト復元で値の入れ物を
    //   差し替えても従来どおり最新の入れ物を参照する（挙動不変）。
    this._controlCtx = {
      doc: this._doc,
      getValue: (name) => this._form.getValue(name),
      setValue: (name, value) => this._form.setValue(name, value),
      onChange: () => this._onChange(),
      // 期間プリセットの基準（基本設計_期間プリセット.md §6.5・§8.2）。
      //   呼び出し時解決の遅延アクセサ＝ダイアログ内で `timeframe` パラメータを変えると、
      //   次にプリセットを開いたときの提示集合が新しい実効足で引き直される。
      //   datasetRef／timeframe のいずれかが未供給（旧ホスト・SSR/単体テスト）は null を返し、
      //   コントロール側はプリセット非提示へ退化する（F-P2/F-P3 と同じ扱い）。
      periodContext: () => this._periodContext(),
      // 未解決の入力エラー（期間表記の換算失敗など・値へ反映できていない状態）を登録する。
      //   登録されている間は OK を抑止する（§5 F-11 の OK 制御と同じ扱い）。これが無いと、
      //   エラー表示のまま OK を押せてしまい、旧値が黙って確定して『設定しても元に戻る』
      //   という症状になる（2026-07-29 ユーザー報告の実体）。
      setPendingError: (name, message) => {
        this._form.setPendingError(name, message);
        this._revalidate();
      },
    };
  }

  // ---- フォーム値・バリアントへの呼び出し面（所有者は PropertyFormState）------
  //   既存の消費者（テスト・コントロール）が触る名前を変えないための委譲アクセサ。
  get _values() {
    return this._form.values;
  }

  set _values(next) {
    this._form.replaceValues(next);
  }

  get _variant() {
    return this._form.variant;
  }

  set _variant(next) {
    this._form.variant = next;
  }

  get _variants() {
    return this._form.variants;
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

    // ドラッグ移動（Pointer Events・ブラウザ標準のみ・§2.3）は協働子が持つ。
    head.addEventListener('pointerdown', (ev) => this._dragController.start(ev));
    return head;
  }

  // ---- タブバー（パラメーター/スタイル/可視性）-------------------------------
  _buildTabBar() {
    const doc = this._doc;
    const bar = doc.createElement('div');
    bar.className = 'prop-tabs';
    const tabs = this._seriesTabs
      ? [
        { key: 'inputs', label: 'パラメーター' },
        { key: 'style', label: 'スタイル' },
        { key: 'visibility', label: '可視性' },
      ]
      : [{ key: 'inputs', label: 'パラメーター' }];
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
    this._panes.set('inputs', inputs);
    body.append(inputs);
    if (this._seriesTabs) {
      const style = this._buildStylePane();
      const visibility = this._buildVisibilityPane();
      this._panes.set('style', style);
      this._panes.set('visibility', visibility);
      body.append(style, visibility);
    }
    return body;
  }

  // パラメーター タブ（本書の主眼・§3/§5）。
  _buildInputsPane() {
    const doc = this._doc;
    const pane = doc.createElement('div');
    pane.className = 'prop-pane is-active';
    pane.dataset.propPane = 'inputs';

    // A 方式注記はパラメータタブに出す（ISSUE-109 で移設）。スタイル/可視性は applyOptions 直接
    //   反映のため A/B 両方式で実反映される＝注記の対象はパラメータ値のみになった。

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
    // variant を変えると受理 param が変わる（ISSUE-278 #8）ため、可視・有効・検証を再評価する。
    //   受理しない param の行はその場で消える（＝効かないコントロールを出さない）。
    sel.addEventListener('change', () => {
      this._variant = sel.value;
      this._refreshVisible();
      this._refreshEnabled();
      this._revalidate();
    });
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

    // info（ツールチップ）アイコン。クリックで説明ブロックを開閉する（hover の title だけだと
    //   クリックでは何も出ず、タッチ環境では一切読めない・ユーザー報告 2026-09-05）。
    //   本文は **素のまま** 使う。humanizeKey は i18n キー用（'label.length'→'length'）であり、
    //   自由文へ適用すると小数点（「既定 0.05」等）以降に切り詰められる（ISSUE-494）。
    let info = null;
    let desc = null;
    if (field.tooltip) {
      info = doc.createElement('span');
      info.className = 'prop-field-info';
      info.textContent = 'ⓘ';
      info.title = field.tooltip;
      desc = doc.createElement('div');
      desc.className = 'prop-field-desc';
      desc.dataset.propDesc = field.name;
      desc.textContent = field.tooltip;
      desc.hidden = true;
      info.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        desc.hidden = !desc.hidden;
      });
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
    if (desc) {
      row.append(desc);
    }
    this._fieldEls.set(field.name, { row, control, error, info });
    return row;
  }

  // control_type 別レンダリング（§3.1）。生成器テーブルへ委譲する（switch 廃止・OCP・ISSUE-181）。
  _buildControl(field) {
    return buildControl(field, this._controlCtx);
  }

  // 期間プリセットの基準（datasetRef ＋ 実効計算時間足）を解決する。
  //   実効足＝指標の `timeframe` パラメータ（'chart' 以外なら MTF override）→ チャートの現在足。
  //   規則は usecase/period_presets.js の effectiveTimeframe が唯一の判定源（二重定義しない）。
  _periodContext() {
    const datasetRef = this._context.datasetRef;
    const chartTf = this._context.timeframe;
    if (!datasetRef || !chartTf) {
      return null;
    }
    const timeframe = effectiveTimeframe(this._values, chartTf);
    return {
      datasetRef,
      timeframe,
      timeframeLabel: this._timeframeLabel(timeframe),
    };
  }

  // 時間足 → 見出し表示（'1h' → '1時間足'）。未知足はコードをそのまま出す。
  _timeframeLabel(timeframe) {
    const label = timeframeLabels()[timeframe];
    return label ? `${label}足` : String(timeframe);
  }

  // segmented 単体の生成（既存テストが直接叩く接合面のため、委譲メソッドとして残す）。
  _buildSegmented(field) {
    return buildSegmented(field, this._controlCtx);
  }

  // ---- スタイル/可視性の行モデル（ISSUE-109）---------------------------------
  // 行構築の純ロジック（bucket 粒度畳み込み含む）は usecase/buildSeriesStyleRows へ委譲
  //   （ISSUE-110 🟡-1: 命名規約知識を DOM アダプタに置かない）。本メソッドは表示既定
  //   （hex 変換・null フォールバック）だけを担う。
  //   seriesStyles が供給されている場合はそれが実体（空配列＝スタイル編集可能な系列なし）。
  //   null（未供給・後方互換・SSR/単体テスト）のみ def.series の静的フォールバックへ落ちる。
  _seriesRows() {
    if (this._rows) {
      return this._rows;
    }
    let rows;
    if (this._seriesStyles) {
      rows = buildSeriesStyleRows(this._def, this._seriesStyles).map((r) => ({
        ...r,
        color: toHex(r.color ?? DEFAULT_SERIES_COLOR),
        width: r.width ?? 1,
        style: r.style ?? DEFAULT_LINE_STYLE,
      }));
    } else {
      rows = (this._def.series ?? []).map((s, idx) => ({
        label: s.seriesName ?? (s.dynamic ? '(動的系列)' : `系列${idx + 1}`),
        names: s.seriesName ? [s.seriesName] : [],
        kind: s.kind ?? 'line',
        heat: false,
        // 静的フォールバック（seriesStyles 未供給＝SSR/単体テスト）の表示既定色。
        //   旧実装は s.colorRule を読んでいたが、当該席は代入 0 件で常に null＝実運用でも常に
        //   このフォールバックへ落ちていた（E-6）。席の撤去（§7.2・A-2）に伴い直に書く。
        color: toHex(DEFAULT_SERIES_COLOR),
        width: s.width ?? 1,
        style: s.style ?? DEFAULT_LINE_STYLE,
        visible: true,
      }));
    }
    this._rows = rows;
    return rows;
  }

  // ---- スタイル タブ（実描画系列単位の色/線幅/線種・§6.1）--------------------
  _buildStylePane() {
    const doc = this._doc;
    const pane = doc.createElement('div');
    pane.className = 'prop-pane';
    pane.dataset.propPane = 'style';

    this._styleState = [];
    const rows = this._seriesRows();
    if (rows.length === 0) {
      const empty = doc.createElement('div');
      empty.className = 'prop-style-empty';
      empty.textContent = 'この指標にスタイル編集可能な系列はありません。';
      pane.append(empty);
      return pane;
    }
    for (const r of rows) {
      const row = doc.createElement('div');
      row.className = 'prop-style-row';

      const name = doc.createElement('span');
      name.className = 'prop-style-name';
      name.textContent = r.label;
      row.append(name);

      // ISSUE-112（ユーザー裁定: ヒート絶対優先）: バー別ヒート配色の histogram は色も編集対象外。
      //   色ピッカーを出さず「ヒート配色（自動）」と明示する（機能しない設定項目を露出しない）。
      let color = null;
      if (seriesKind(r.kind).supportsHeat && r.heat) {
        const heatNote = doc.createElement('span');
        heatNote.className = 'prop-style-heat';
        heatNote.textContent = 'ヒート配色（自動）';
        row.append(heatNote);
      } else {
        color = doc.createElement('input');
        color.type = 'color';
        color.className = 'prop-input prop-input-color';
        color.value = r.color;
        row.append(color);
      }

      // ISSUE-111: 線幅/線種はライン系列のみ。histogram（棒グラフ）は色のみ編集可
      //   （renderer.applySeriesStyle も histogram には色しか適用しない＝描画種別と設定項目を一致）。
      let width = null;
      let style = null;
      let unified = null;
      // 選択肢の集合・並び・初期値はすべて表示形式の台帳が決める（series_style_forms.js）。
      //   第 3 の表示形式は台帳 1 エントリの追加で選択肢・初期値・永続差分の 3 つに同時に効く。
      const unifiedInit = displayFormInitial(r);
      const options = displayFormOptions(r);
      // 線幅入力を生成するヘルパ（line 描画時のみ・histogram は lineWidth 非適用）。
      const buildWidthInput = () => {
        const w = doc.createElement('input');
        w.type = 'number';
        w.min = '1';
        w.step = '1';
        w.className = 'prop-input prop-input-number';
        w.value = String(r.width);
        return w;
      };
      const buildStyleSelect = (selected) => {
        const sel = doc.createElement('select');
        sel.className = 'prop-input prop-input-select';
        for (const st of options) {
          const o = doc.createElement('option');
          o.value = st;
          o.textContent = st;
          if (selected === st) o.selected = true;
          sel.append(o);
        }
        // option 追加後に value を明示設定（実 DOM で選択を確定・DOM スタブでも value を保証）。
        sel.value = selected;
        return sel;
      };
      const lineEditable = seriesKind(r.kind).editableLineStyle;
      if (usesUnifiedDisplayForm(r)) {
        // 対象系列（案A）: 線種と系列表示を統合した 1 つの select を kind に依らず出す。並びと
        //   ゲートは台帳が宣言する（btlm_trail は先頭に 'dot'、MAROD は末尾に 'bar'。両ゲートは直交）。
        //   棒表示中（kind='histogram'）でも select を出して line/dot へ戻せるようにする
        //   （editableLineStyle に依存しない＝往復可能性を担保）。線幅入力は line 描画時のみ。
        if (lineEditable) {
          width = buildWidthInput();
        }
        unified = buildStyleSelect(unifiedInit);
        if (width) {
          row.append(width, unified);
        } else {
          row.append(unified);
        }
      } else if (lineEditable) {
        // 未付与系列（補助線・読取・全他指標）: 従来どおり 線幅 ＋ 台帳のゲート無しエントリ＝byte 不変。
        width = buildWidthInput();
        style = buildStyleSelect(r.style);
        row.append(width, style);
      }

      // initial: OK 時の差分判定基準（変更された行×フィールドのみ patch へ載せる）。
      this._styleState.push({
        names: r.names, color, width, style, unified,
        initial: { color: r.color, width: String(r.width), style: r.style, unified: unifiedInit },
      });
      pane.append(row);
    }
    return pane;
  }

  // ---- 可視性 タブ（実描画系列単位の表示/非表示・§6.2）-----------------------
  _buildVisibilityPane() {
    const doc = this._doc;
    const pane = doc.createElement('div');
    pane.className = 'prop-pane';
    pane.dataset.propPane = 'visibility';

    this._visibilityState = [];
    for (const r of this._seriesRows()) {
      const row = doc.createElement('label');
      row.className = 'prop-visibility-row';
      const cb = doc.createElement('input');
      cb.type = 'checkbox';
      cb.checked = r.visible;
      const name = doc.createElement('span');
      name.textContent = r.label;
      row.append(cb, name);
      this._visibilityState.push({ names: r.names, checkbox: cb, initial: r.visible });
      pane.append(row);
    }
    return pane;
  }

  // OK 時のスタイル/可視性差分を { seriesName: { color?, width?, style?, display?, visible? } } に集約する。
  //   組立規則（何を差分と見なすか・統合 select の分解）は usecase/series_style_forms.js が所有する。
  //   本メソッドは入力要素から現在値を読み取って純関数へ渡すだけ（DOM 読み取りのみ）。
  _collectStyleChanges() {
    const snap = (el, initial) => (el ? { value: el.value, initial } : null);
    return collectSeriesStyleDiff({
      styleRows: (this._styleState ?? []).map((s) => ({
        names: s.names,
        color: snap(s.color, s.initial.color),
        width: snap(s.width, s.initial.width),
        style: snap(s.style, s.initial.style),
        unified: snap(s.unified, s.initial.unified),
      })),
      visibilityRows: (this._visibilityState ?? []).map((v) => ({
        names: v.names, checked: v.checkbox.checked, initial: v.initial,
      })),
    });
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

  // F-11 リアルタイム検証（§5）。判定は PropertyFormState が返し、本メソッドは
  //   インライン表示と OK ボタンの活殺へ写すだけを行う。
  _revalidate() {
    const { violations, ok } = this._form.validation();
    // 全フィールドのエラー表示をクリア。
    for (const [, els] of this._fieldEls) {
      els.error.textContent = '';
      els.row.classList.remove('is-invalid');
    }
    for (const v of violations) {
      const els = this._fieldEls.get(v.param);
      if (els) {
        els.error.textContent = humanizeKey(v.constraint);
        els.row.classList.add('is-invalid');
      }
    }
    // 未解決の入力エラー（期間表記の換算失敗など）がある間は確定させない。
    //   当該欄のエラー文言はコントロール側が自前で表示済みのため、ここでは OK 制御のみ行う。
    if (this._okBtn) {
      this._okBtn.disabled = !ok;
    }
    return ok;
  }

  // 条件付き有効化（§3.5）。disabled のフィールド行をグレーアウト。
  _refreshEnabled() {
    const enabled = this._form.enablement();
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
    //   黙った代替ではない。例: 日別×1分で src=zp → 滞在時間 へ跳ぶ）。判定は PropertyFormState。
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
      const options = [...(sel.options ?? [])];
      const decision = this._form.optionEnablement(pdef, options.map((o) => o.value));
      options.forEach((opt, i) => {
        opt.disabled = !decision.enabled[i];
      });
      if (decision.currentDisabled && decision.firstEnabled !== null) {
        this._form.setValue(pdef.name, decision.firstEnabled);
        sel.value = String(decision.firstEnabled);
        this._refreshVisible(); // src 連動の表示（period 行など）も追従させる。
      }
    }
  }

  // 条件付き表示（§3.5 拡張・トグル）。conditionalVisible=false のフィールド行を非表示にする。
  //   _refreshEnabled（グレーアウト）と対称の動的経路。range を変えた瞬間に「ビン」行が出没する。
  //   静的除外（uiVisible===false）は buildFormModel が担い、本メソッドは動的トグルのみ担う。
  _refreshVisible() {
    const visible = this._form.visibility();
    for (const [name, els] of this._fieldEls) {
      const on = visible[name] !== false;
      els.row.style.display = on ? '' : 'none';
    }
  }

  _onDefaultClick() {
    this._form.resetToDefaults();
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
    // ISSUE-109: スタイル/可視性タブの変更差分を収集（変更なしは空オブジェクト）。
    const styles = this._collectStyleChanges();
    this.close();
    // variant は params とは別経路（recompute の variant 引数）で渡す。
    //   styles は第3引数（後方互換: 旧 onApply(values, variant) 消費者は無視して従来動作）。
    this._onApply(values, variant, { styles });
  }

  _onCancelClick() {
    this.close();
    this._onCancel();
  }
}
