// chart_template_controller.js — チャートテンプレート（保存・適用・紐付け・切替時自動適用）の協働子。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §5.1（UC-T01 保存）／§5.2（UC-T02 適用）／§5.3（UC-T03 紐付け）／§5.4（UC-T04 切替時自動適用）／
//   §5.5（UC-T05 改名・削除）／§5.6（F-T1〜F-T6）／§7.1（ホスト契約にのみ依存する協働子）／
//   §7.2（適用手順の実体は共有ベースの公開入口 `IndicatorStateStore.rebuildApplied` に単一ソース化。
//        時間足切替への介入は購読スロット（単数・使用済み＝E-7）を使わず composition root での
//        `setTimeframe` デコレートで行い、順序と再入防止は**本協働子が所有する**）。
//
// 責務（SRP）: テンプレート集合・紐付けの保持と、UC-T01〜T05 のオーケストレーション。
// 非責務: DOM 構築（menu / dialogs）・localStorage 具象（gateway）・再構築ループ（共有ベース）。
//
// 多態への非依存（§5.2 手順 1）: `removeInstance` は base（`indicator_controller.js:555-561`・MP 分岐
//   なし）と replay subclass（`replay_indicator_controller.js:257-266`・MP 分岐あり）で挙動が異なる。
//   本協働子は多態に依存せず `_isMarketProfile(def)` で自ら分岐し、MP は `_removeMarketProfile(inst)` を
//   呼ぶ（どちらの host 実体でも同一手順になる）。

import { deserialize } from '../../usecase/facade.js';
// 表示名の導出は既存の凡例（indicator_controller.js:809 _label）と同一規則の純関数を使う
//   （host 契約を広げずに凡例と同じ表記へ揃える）。
import { humanizeKey } from './property_control_builders.js';
import {
  deleteTemplate as deleteTemplateUc,
  findTemplateByName as findTemplateByNameUc,
  renameTemplate as renameTemplateUc,
  resolveBinding,
  saveTemplate,
  setBinding,
  recoverLastSeq,
  toAppliedJsonList,
} from '../../usecase/chart_templates.js';

/**
 * ChartTemplateController が host に要求する最小契約（ISP）。
 *
 * @typedef {object} TemplateHost
 * @property {function} _isMarketProfile      def がアクター駆動（MP）指標かの述語。
 * @property {function} _removeMarketProfile  MP インスタンスの除去（アクター無効化＋detach＋state 除去）。
 * @property {function} removeInstance        非 MP インスタンスの除去（renderer 除去＋state 除去＋永続化）。
 * @property {function} _commitState          協働子が算出した次 state を確定する。
 * @property {function} _persistAll           applied/favorites/uiState を永続化する。
 * @property {function} _renderLegend         凡例を再描画する。
 * @property {{applied: Array, uiState: object, favorites: Array, seqCounters: object}} _state 純状態。
 * @property {{get: function}} _catalog       指標定義カタログ（read: get）。
 * @property {Map} _meta                      instanceId -> { def } 描画済みメタ。
 * @property {object} _store                  永続化・復元ロール（read: rebuildApplied）。
 * @property {string} _datasetRef             計算対象データセット参照（read）。
 * @property {string} _timeframe              現在の表示時間足（read）。
 */

// TemplateHost 契約の実体列挙（構造充足テストの固定点）。既存 host（IndicatorController /
//   ReplayIndicatorController）はいずれの面も在席済みで、追加・改変は不要（加法配線のみ）。
//   ISSUE-099 の共有ベース契約（TIMEFRAME_HOST_CONTRACT / MARKET_PROFILE_HOST_CONTRACT）とは
//   別の関心事のため、共有ベースへは置かず本ファイルで宣言する（U9）。
export const TEMPLATE_HOST_CONTRACT = Object.freeze({
  role: 'TemplateHost',
  methods: Object.freeze([
    '_isMarketProfile', '_removeMarketProfile', 'removeInstance',
    '_commitState', '_persistAll', '_renderLegend',
  ]),
  fields: Object.freeze(['_state', '_catalog', '_meta', '_store', '_datasetRef', '_timeframe']),
});

export class ChartTemplateController {
  /**
   * @param {TemplateHost} host テンプレート契約を満たすホスト（IndicatorController 実体）。
   * @param {object} deps
   * @param {object} deps.gateway TemplateStorePort 実装（LocalStorageTemplateGateway）。
   * @param {?object} [deps.menu] ChartTemplateMenu（render(vm) のみ使用・null 可）。
   * @param {?object} [deps.dialogs] ChartTemplateDialogs（openSave / openManage・null 可）。
   * @param {string[]} [deps.validTimeframes] 有効時間足集合（U1: composition root から注入）。
   * @param {function} [deps.now] UNIX 秒を返す時刻源（テスト注入可）。
   */
  constructor(host, {
    gateway, menu = null, dialogs = null, validTimeframes = [], timeframeLabels = null, now = null,
  } = {}) {
    this._host = host;
    this._gateway = gateway;
    this._menu = menu;
    this._dialogs = dialogs;
    this._validTimeframes = Array.isArray(validTimeframes) ? validTimeframes : [];
    // 時間足キー → 表示ラベル（'1D'→'日'）。単一情報源は timeframe_menu.js の groups で、
    //   composition root が注入する（§6.2 の文言「この時間足（例：日）に紐付ける」）。
    //   未注入時はキーをそのまま表示する（DOM 不在テスト・後方互換）。
    this._timeframeLabels = (timeframeLabels && typeof timeframeLabels === 'object') ? timeframeLabels : null;
    this._now = typeof now === 'function' ? now : () => Math.floor(Date.now() / 1000);
    // 永続層から現在値を読む（破損時は gateway が空既定へ倒す＝F-T2）。
    this._templates = gateway.loadTemplates();
    this._bindings = gateway.loadBindings();
    // §4.3: templateSeq 破損時は既存 tpl#N の最大 N 以上へ復旧してから運用に入る（id 衝突回避）。
    this._lastSeq = recoverLastSeq(gateway.loadTemplateSeq(), this._templates);
    // §5.4 再入防止: 自動適用の実行中に発生した時間足切替要求は無視する。
    this._switching = false;
  }

  // UI 部品（メニュー・ダイアログ）を後から結ぶ（composition root の生成順が協働子より後の場合に使う）。
  //   結線のみで挙動は変えない（未注入時は render / ダイアログ入口が no-op）。
  attachUi({ menu = null, dialogs = null } = {}) {
    if (menu) {
      this._menu = menu;
    }
    if (dialogs) {
      this._dialogs = dialogs;
    }
    this.render();
  }

  // ---- 参照面 ---------------------------------------------------------------
  templates() { return this._templates; }

  bindings() { return this._bindings; }

  // activeTemplateId は uiState（既存キー `indicatorUi.uiState.v1` への加法・§4.2）に載る。
  activeTemplateId() {
    return this._host._state.uiState?.activeTemplateId ?? null;
  }

  // 正規化名が一致する既存テンプレートを返す（無ければ null）。保存時の上書き確認の判定源。
  findTemplateByName(name) {
    return findTemplateByNameUc({ templates: this._templates, name });
  }

  // 時間足の表示ラベル（'1D'→'日'）。未注入・未知キーはキーをそのまま返す。
  timeframeLabel(timeframe) {
    return (this._timeframeLabels && this._timeframeLabels[timeframe]) || timeframe;
  }

  // メニュー再描画用のビューモデル（U3 の push／U6 の開くたび pull の双方で使う）。
  viewModel() {
    return {
      templates: this._templates,
      bindings: this._bindings,
      activeTemplateId: this.activeTemplateId(),
      // §6.2 の「● = 現在足に紐付け」印の判定に使う現在の時間足。
      timeframe: this._host._timeframe,
    };
  }

  render() {
    if (this._menu && typeof this._menu.render === 'function') {
      this._menu.render(this.viewModel());
    }
  }

  // ---- UC-T01 保存（§5.1）----------------------------------------------------
  saveCurrent({ name, bindCurrentTimeframe = true } = {}) {
    const host = this._host;
    const res = saveTemplate({
      templates: this._templates,
      lastSeq: this._lastSeq,
      name,
      applied: host._state.applied,
      now: this._now(),
    });
    if (!res.ok) {
      return res; // F-T1: 保存中止・既存データは不変（表示は dialogs 側のインライン）。
    }
    this._templates = res.templates;
    this._gateway.saveTemplates(res.templates);
    if (res.lastSeq !== this._lastSeq) {
      this._lastSeq = res.lastSeq;
      this._gateway.saveTemplateSeq(res.lastSeq); // §4.3: 発行と同時に永続化する。
    }
    if (bindCurrentTimeframe) {
      this._bindings = setBinding({
        bindings: this._bindings, timeframe: host._timeframe, templateId: res.templateId,
      });
      this._gateway.saveBindings(this._bindings);
    }
    this._setActiveTemplateId(res.templateId);
    this.render();
    return res;
  }

  // ---- UC-T02 適用（§5.2）----------------------------------------------------
  //   現構成の「置換」（マージではない）。空テンプレートの適用は全指標除去を意味する。
  async applyTemplate(templateId) {
    const template = this._templates.find((t) => t && t.templateId === templateId);
    if (!template) {
      // §5.2 例外・F-T3: 何もしない＋当該 id を参照する紐付けの遅延クリーンアップ。
      this._warn(`[template] 適用対象が不在: ${templateId}`);
      this._cleanupBindingsFor(templateId);
      this.render();
      return false;
    }
    await this._removeAllApplied();   // 手順 1
    await this._applyInstances(template); // 手順 2〜6
    this.render();
    return true;
  }

  // ---- UC-T03 紐付け（§5.3）--------------------------------------------------
  //   紐付け操作そのものは構成を変更しない（適用は UC-T02／UC-T04 のみ）。
  setBindingFor(timeframe, templateId) {
    this._bindings = setBinding({ bindings: this._bindings, timeframe, templateId });
    this._gateway.saveBindings(this._bindings);
    this.render();
    return this._bindings;
  }

  // 現在の時間足への紐付け（メニューの「この時間足に紐付け」）。null で解除。
  bindCurrentTimeframe(templateId) {
    return this.setBindingFor(this._host._timeframe, templateId);
  }

  // ---- UC-T04 切替時自動適用（§5.4）------------------------------------------
  /**
   * 時間足切替に介入する（composition root が `controller.setTimeframe` をデコレートして呼ぶ）。
   * 順序・再入防止は本協働子が所有し、root は差し替え 1 行のみを持つ。
   *
   * @param {string} next 切替先の時間足。
   * @param {function} proceed 既存の時間足切替処理（(timeframe) => Promise）。
   */
  async onTimeframeChange(next, proceed) {
    if (this._switching) {
      return undefined; // 自動適用の実行中に発生した切替要求は無視する（§5.4 再入防止）。
    }
    // §5.4 発火条件 1「ユーザーの明示的な時間足**切替**である」: 現在足と同じ項目のクリックは
    //   切替が発生しないため発火条件を満たさない。既存挙動（`timeframe_controller.js:63-65` の
    //   同一性ガードによる no-op）へそのまま委譲する。このガードが無いと、既存実装では完全な
    //   no-op だったクリック 1 回で現構成の除去・置換・永続化まで到達する（既存挙動の破壊）。
    //   時間足メニューは現在足の項目も clickable（`indicator_controller.js:787` が全
    //   [data-timeframe] へ click を配線）であるため実 UI から到達可能。
    if (!next || next === this._host._timeframe) {
      return proceed(next);
    }
    const resolved = resolveBinding({
      bindings: this._bindings,
      templates: this._templates,
      timeframe: next,
      validTimeframes: this._validTimeframes,
      activeTemplateId: this.activeTemplateId(),
    });
    if (resolved.changed) {
      // F-T3: dangling 紐付けの遅延クリーンアップ（自動適用はしない）。
      this._bindings = resolved.bindings;
      this._gateway.saveBindings(this._bindings);
      this.render();
    }
    if (resolved.templateId === null) {
      // 紐付けなし・dangling・同一テンプレート由来 → 現行挙動を完全に維持する。
      return proceed(next);
    }
    const template = this._templates.find((t) => t.templateId === resolved.templateId);
    this._switching = true;
    try {
      await this._removeAllApplied(); // ステップ 1: 切替前に現構成を除去（計算はしない）。
      // ステップ 2: 既存の切替処理（適用済み 0 件＝指標計算なし）。
      //   ★ 失敗時も**ステップ 3 を必ず実行する**（§5.4 の順序 1→2→3 は不変。実行の有無だけを保証する）:
      //   ステップ 1 の除去は既に永続化されている（applied.v1=[]）ため、ここで中断すると
      //   「除去済み・未適用」＝全指標消失がそのまま永続値になり、リロードでも戻らない
      //   （実 UI 検証 D-2 の症状）。切替の失敗は指標構成を破棄してよい理由にならない。
      let switchError = null;
      try {
        await proceed(next);
      } catch (e) {
        switchError = e;
        this._warn(`[template] 時間足切替が失敗（構成は適用して整合を保つ）: ${e && e.message ? e.message : e}`);
      }
      await this._applyInstances(template); // ステップ 3: 新しい足で 1 回だけ適用（UC-T02 手順 2 以降）。
      this.render();
      if (switchError) {
        throw switchError; // 呼び出し元への例外伝播は既存の setTimeframe と同一に保つ。
      }
    } finally {
      this._switching = false;
    }
    return undefined;
  }

  // ---- UC-T05 改名・削除（§5.5）----------------------------------------------
  renameTemplate(templateId, name) {
    const res = renameTemplateUc({
      templates: this._templates, templateId, name, now: this._now(),
    });
    if (!res.ok) {
      return res; // F-T1: インライン表示は dialogs 側。既存データは不変。
    }
    this._templates = res.templates;
    this._gateway.saveTemplates(res.templates);
    this.render();
    return res;
  }

  // 削除（確認 1 段は dialogs 側）。現在チャート上の構成は変更しない。
  deleteTemplate(templateId) {
    const before = this._bindings;
    const res = deleteTemplateUc({
      templates: this._templates,
      bindings: this._bindings,
      templateId,
      activeTemplateId: this.activeTemplateId(),
    });
    this._templates = res.templates;
    this._gateway.saveTemplates(res.templates);
    if (Object.keys(res.bindings).length !== Object.keys(before).length) {
      this._bindings = res.bindings;
      this._gateway.saveBindings(this._bindings);
    }
    if (res.activeTemplateId !== this.activeTemplateId()) {
      this._setActiveTemplateId(res.activeTemplateId);
    }
    this.render();
    return res;
  }

  // ---- ダイアログ入口（メニューのコールバック先・§6.2）------------------------
  openSaveDialog() {
    if (!this._dialogs || typeof this._dialogs.openSave !== 'function') {
      return;
    }
    const host = this._host;
    this._dialogs.openSave({
      timeframeLabel: this.timeframeLabel(host._timeframe),
      indicatorNames: host._state.applied.map((i) => this._labelOf(i)),
      // 上書き確認（ユーザー指示 2026-07-28）の判定器。判定は usecase の純関数のみが行い、
      //   ダイアログは結果（既存テンプレート or null）を受け取るだけ（DIP・判定源の一本化）。
      findExisting: (name) => this.findTemplateByName(name),
      onSubmit: ({ name, bindCurrentTimeframe }) => this.saveCurrent({ name, bindCurrentTimeframe }),
    });
  }

  openManageDialog() {
    if (!this._dialogs || typeof this._dialogs.openManage !== 'function') {
      return;
    }
    this._dialogs.openManage({
      templates: this._templates,
      onRename: (templateId, name) => this.renameTemplate(templateId, name),
      onDelete: (templateId) => this.deleteTemplate(templateId),
    });
  }

  // ---- 内部（手順の実体）-----------------------------------------------------

  // 手順 1: 現在の適用済みインスタンスを全件除去する（計算はしない）。
  //   U5: バッチ除去入口は新設せず、既存 `removeInstance` を N 回呼ぶ。
  async _removeAllApplied() {
    const host = this._host;
    for (const inst of [...host._state.applied]) {
      const def = host._catalog.get(inst.indicatorId);
      if (host._isMarketProfile(def)) {
        await host._removeMarketProfile(inst);
        continue;
      }
      host.removeInstance(inst.instanceId);
    }
  }

  // 手順 2〜6: 宣言順に適用（instanceId 再採番）→ 現在の足で再構築 → activeTemplateId 更新＋永続化 → 凡例。
  async _applyInstances(template) {
    const host = this._host;
    const instances = this._singleMarketProfile(template.instances ?? []);
    const mapped = toAppliedJsonList(instances, host._state.seqCounters);
    // rebuildApplied の事前条件（styles 再適用が `_state.applied.find` を読む）を満たすため、
    //   再構築の前に state へ在席させる。deserialize で AppliedInstance（不変・凍結）へ復元する。
    host._commitState(deserialize(JSON.stringify({
      applied: mapped.applied,
      favorites: host._state.favorites,
      seqCounters: mapped.seqCounters,
      uiState: host._state.uiState,
    })));
    // 手順 2〜4: 計算・描画・visible 復元・styles 再適用・個別失敗の局所化（共有ベースの単一ソース）。
    //   ★ 失敗の局所化（§5.6 F-T4「当該 1 件のみスキップし残りの適用と描画は継続する。全体を中止しない」）:
    //   共有ベースの再構築ループは非 MP の compute 例外のみを try/catch で握る。MP 復元経路
    //   （`_mp.restoreInstance` → actor.setEnabled）は catch の外にあるため、MP の失敗は
    //   rebuildApplied 全体を reject させる。ここで中断すると**手順 5（applied.v1 の永続化）が
    //   実行されず**、手順 1 の除去が永続化した空構成 `[]` が最終値として残り、リロードで
    //   ユーザーの構成が消える（実 UI 検証 D-1 の症状）。よって適用の完遂（永続化・凡例）は
    //   再構築の成否に依存させない。
    //   共有ベース側は「1 件ずつ」呼べば失敗が当該 1 件に閉じる（再構築ループの本体はインスタンス
    //   間に共有状態を持たない: `_meta` は Map への追加、`_commitLastSeries` は毎回上書きで
    //   反復間の読み出しが無い、gateway は呼び出しごとに生成される）。よって公開入口を増やさず
    //   （§7.2 S2 の「公開入口 1 個」を維持）、協働子側で 1 件ずつ呼んで局所化する。
    for (const inst of [...host._state.applied]) {
      try {
        await host._store.rebuildApplied([inst]);
      } catch (e) {
        this._warn(`[template] 適用スキップ: ${inst.instanceId} / ${e && e.message ? e.message : e}`);
      }
    }
    // 手順 5: activeTemplateId 更新＋永続化（applied.v1 / uiState.v1）。
    this._setActiveTemplateId(template.templateId);
    // 手順 6: 凡例を再描画する。
    host._renderLegend();
  }

  // MP は宣言順の先頭 1 件のみ適用し、後続は無視して警告する（§5.2・E-8 単一インスタンス制約）。
  _singleMarketProfile(instances) {
    const host = this._host;
    const out = [];
    let seen = false;
    for (const t of instances) {
      const def = host._catalog.get(t.indicatorId);
      if (def && host._isMarketProfile(def)) {
        if (seen) {
          this._warn(`[template] MP は単一インスタンス制約のため後続を無視: ${t.indicatorId}`);
          continue;
        }
        seen = true;
      }
      out.push(t);
    }
    return out;
  }

  // uiState へ activeTemplateId を確定して永続化する（§4.2 既存キーへの加法）。
  _setActiveTemplateId(templateId) {
    const host = this._host;
    host._commitState({
      ...host._state,
      uiState: { ...host._state.uiState, activeTemplateId: templateId ?? null },
    });
    host._persistAll();
  }

  // 参照先不在テンプレートへの紐付けを削除して永続化する（F-T3 遅延クリーンアップ）。
  _cleanupBindingsFor(templateId) {
    const entries = Object.entries(this._bindings).filter(([, id]) => id === templateId);
    if (entries.length === 0) {
      return;
    }
    const next = { ...this._bindings };
    for (const [tf] of entries) {
      delete next[tf];
    }
    this._bindings = next;
    this._gateway.saveBindings(next);
  }

  // 保存プレビュー用の指標ラベル（凡例と同一表記。カタログ非在席は indicatorId のまま）。
  _labelOf(inst) {
    const def = this._host._catalog.get(inst.indicatorId);
    if (!def) {
      return inst.indicatorId;
    }
    const label = humanizeKey(def.displayNameKey ?? def.id);
    const variant = inst.variant && inst.variant !== 'default' ? ` (${inst.variant})` : '';
    return `${label}${variant}`;
  }

  _warn(msg) {
    if (typeof console !== 'undefined' && console.warn) {
      console.warn(msg);
    }
  }
}
