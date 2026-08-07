// PropertiesDialog（adapter/front/properties_dialog.js）— インジケーター・プロパティ ダイアログ DOM。
//
// 設計入力: 内部設計_パラメータ設定ダイアログ.md v0.2.0
//   §2 ダイアログ構造（タイトル/タブ/フッター・ドラッグ・×閉じる）
//   §3 PARAM_DEF→コントロール写像 / §3.4 グループ / §3.5 条件付き有効化 / §3.6 ツールチップ
//   §5 リアルタイム検証（F-11・OK 制御）/ §6 スタイル・可視性タブ / §7 振る舞い・状態遷移
//   §8.2 PropertiesDialog 署名 / §8.4 upstream JS API 非依存。
//
// 純ロジックは usecase/form_model.js（buildFormModel/computeEnabled/validateForm/resetToDefaults）
//   へ委譲する（DOM 非依存ロジックの分離・§10.4）。本ファイルは DOM 構築・イベント配線のみ。
//
// ★ upstream JS API（addLineSeries / createPriceLine / setData / applyOptions 等）は
//   一切参照しない（§8.4・母体 §9 grep 0 件規律）。描画反映は onApply（→ ChartRenderer/facade 経由）。

import {
  buildFormModel,
  buildSeriesStyleRows,
  computeEnabled,
  computeVisible,
  validateForm,
  resetToDefaults,
} from '../../usecase/form_model.js';
import { seriesKind } from '../../domain/series_kind.js';
// control_type → コントロール生成器のテーブル（ISSUE-181・OCP）。生成手続き本体と
//   ラベル化/色変換の純関数は adapter/front/property_control_builders.js が所有する。
import {
  buildControl,
  buildSegmented,
  humanizeKey,
  toHex,
} from './property_control_builders.js';
// 期間プリセット（基本設計_期間プリセット.md §6.5）: 実効計算時間足の解決は usecase の純関数へ委譲する。
import { effectiveTimeframe } from '../../usecase/period_presets.js';
// 時間足 → 表示ラベル（'1h'→'1時間'）の単一情報源（timeframe_menu.js・ISSUE-123）。
//   期間プリセット一覧の見出し「◯◯足 基準」に使う（ラベルを二重定義しない）。
import { timeframeLabels } from './timeframe_menu.js';

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

    // 現在のフォーム値（name -> value）。初期は instance.params 優先→default。
    const currentParams = (instance && instance.params) || {};
    const model = buildFormModel(def, currentParams);
    this._values = {};
    for (const f of model.fields) {
      this._values[f.name] = f.value;
    }

    // バリアント（profit_band global↔robust 等）。OK 時に variant 変更を実反映する。
    this._variants = (def.compute && def.compute.variants) || ['default'];
    this._variant = (instance && instance.variant) || this._variants[0];

    this._activeTab = 'inputs';
    this._root = null; // ダイアログ最上位要素
    this._fieldEls = new Map(); // name -> { row, control, error, info }
    // 値へ反映できていない入力エラー（name -> message）。在席中は OK を押せない。
    this._pendingErrors = new Map();
    this._okBtn = null;
    this._drag = null;
    this._offset = { x: 0, y: 0 };

    // コントロール生成器との結合面（ControlContext）。値の所有者は本クラス（_values）のまま。
    //   getValue/setValue は呼び出し時解決の遅延アクセサ＝デフォルト復元で _values を
    //   差し替えても従来どおり最新の入れ物を参照する（挙動不変）。
    this._controlCtx = {
      doc: this._doc,
      getValue: (name) => this._values[name],
      setValue: (name, value) => { this._values[name] = value; },
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
        if (message) {
          this._pendingErrors.set(name, message);
        } else {
          this._pendingErrors.delete(name);
        }
        this._revalidate();
      },
    };
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

  // 述語（conditionalEnable / conditionalVisible / optionEnable）へ渡す評価コンテキスト。
  //   外部状態（timeframe / datasetRef 等）へ **選択中の variant** を重ねる。variant ごとに
  //   受理 param が異なる（ISSUE-278 #8）ため、可視判定は variant を知る必要がある。
  _evalContext() {
    return { ...this._context, variant: this._variant };
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
        color: toHex(r.color ?? '#2962ff'),
        width: r.width ?? 1,
        style: r.style ?? 'solid',
      }));
    } else {
      rows = (this._def.series ?? []).map((s, idx) => ({
        label: s.seriesName ?? (s.dynamic ? '(動的系列)' : `系列${idx + 1}`),
        names: s.seriesName ? [s.seriesName] : [],
        kind: s.kind ?? 'line',
        heat: false,
        color: toHex(s.colorRule ?? '#2962ff'),
        width: s.width ?? 1,
        style: s.style ?? 'solid',
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
      // 統合 select（案A）の初期値: display=='bar' なら 'bar'、'dots' なら 'dot'、それ以外は線種。
      //   'bar'（btlm_trail_marod・棒グラフ）と 'dot'（btlm_trail・ドット）は排他（各系列のゲート次第）。
      const unifiedInit = (r.display === 'bar')
        ? 'bar'
        : (r.display === 'dots') ? 'dot' : (r.style ?? 'solid');
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
      const lineEditable = seriesKind(r.kind).editableLineStyle;
      if (r.pointStyleEditable || r.barStyleEditable) {
        // 対象系列（案A）: 線種と系列表示を統合した 1 つの select を kind に依らず出す。base=[solid,dotted,
        //   dashed]。pointStyleEditable なら先頭に 'dot'（サークル描画）＝btlm_trail の
        //   [dot,solid,dotted,dashed] を厳密再現（挙動不変）。barStyleEditable なら末尾に 'bar'（棒グラフ・
        //   0% 中心）＝MAROD の [solid,dotted,dashed,bar]（dot は出さない）。両ゲートは直交。棒表示中
        //   （kind='histogram'）でも select を出して line/dot へ戻せるようにする（editableLineStyle に依存
        //   しない＝往復可能性を担保）。線幅入力は line 描画時のみ（histogram では lineWidth 非適用）。
        if (lineEditable) {
          width = buildWidthInput();
        }
        unified = doc.createElement('select');
        unified.className = 'prop-input prop-input-select';
        const opts = ['solid', 'dotted', 'dashed'];
        if (r.pointStyleEditable) opts.unshift('dot');
        if (r.barStyleEditable) opts.push('bar');
        for (const st of opts) {
          const o = doc.createElement('option');
          o.value = st;
          o.textContent = st;
          if (unifiedInit === st) o.selected = true;
          unified.append(o);
        }
        unified.value = unifiedInit;
        if (width) {
          row.append(width, unified);
        } else {
          row.append(unified);
        }
      } else if (lineEditable) {
        // 未付与系列（補助線・読取・全他指標）: 従来どおり 線幅 ＋ 3 択（solid/dotted/dashed）＝byte 不変。
        width = buildWidthInput();
        style = doc.createElement('select');
        style.className = 'prop-input prop-input-select';
        for (const st of ['solid', 'dotted', 'dashed']) {
          const o = doc.createElement('option');
          o.value = st;
          o.textContent = st;
          if (r.style === st) o.selected = true;
          style.append(o);
        }
        // option 追加後に value を明示設定（実 DOM で選択を確定・DOM スタブでも value を保証）。
        style.value = r.style;
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

  // OK 時のスタイル/可視性差分を { seriesName: { color?, width?, style?, visible? } } に集約する。
  //   変更が無ければ空オブジェクト。行が bucket 粒度のときは全構成系列へ展開する。
  _collectStyleChanges() {
    const patch = {};
    const put = (names, fields) => {
      for (const n of names) {
        patch[n] = { ...(patch[n] ?? {}), ...fields };
      }
    };
    for (const s of this._styleState ?? []) {
      const fields = {};
      // heat 行は色入力を生成しない（null・ISSUE-112）＝色は差分対象外（ヒート絶対優先）。
      if (s.color && s.color.value !== s.initial.color) {
        fields.color = s.color.value;
      }
      // histogram 行は width/style 入力を生成しない（null・ISSUE-111）＝色のみ差分対象。
      if (s.width && s.width.value !== s.initial.width && s.width.value !== '') {
        fields.width = Number(s.width.value);
      }
      if (s.style && s.style.value !== s.initial.style) {
        fields.style = s.style.value;
      }
      // 統合 select（案A）: dot は display=dots、bar は display=bar（棒・線種概念なし＝style を載せない）、
      //   線種（solid/dotted/dashed）はライン描画＋当該線種へ分解する。永続化スキーマは既存の
      //   per-series {display?, style?} のまま（display 値域に 'bar' を加算・往復整合・移行不要）。
      if (s.unified && s.unified.value !== s.initial.unified) {
        if (s.unified.value === 'dot') {
          fields.display = 'dots';
        } else if (s.unified.value === 'bar') {
          fields.display = 'bar';
        } else {
          fields.display = 'line';
          fields.style = s.unified.value;
        }
      }
      if (Object.keys(fields).length > 0) {
        put(s.names, fields);
      }
    }
    for (const v of this._visibilityState ?? []) {
      if (v.checkbox.checked !== v.initial) {
        put(v.names, { visible: v.checkbox.checked });
      }
    }
    return patch;
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
    const visible = computeVisible(this._def, this._values, this._evalContext());
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
    // 未解決の入力エラー（期間表記の換算失敗など）がある間は確定させない。
    //   当該欄のエラー文言はコントロール側が自前で表示済みのため、ここでは OK 制御のみ行う。
    const ok = effective.length === 0 && this._pendingErrors.size === 0;
    if (this._okBtn) {
      this._okBtn.disabled = !ok;
    }
    return ok;
  }

  // 条件付き有効化（§3.5）。disabled のフィールド行をグレーアウト。
  _refreshEnabled() {
    const enabled = computeEnabled(this._def, this._values, this._evalContext());
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
        const ok = !!pdef.optionEnable(raw, this._values, this._evalContext());
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
    const visible = computeVisible(this._def, this._values, this._evalContext());
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
