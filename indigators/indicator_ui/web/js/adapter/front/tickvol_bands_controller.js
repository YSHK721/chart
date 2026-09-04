// tickvol_bands_controller.js — 取引密度帯（時刻帯の背景色）のアクター駆動オーケストレーション協働子。
//
// IndicatorController が computeId → アクターコントローラのレジストリで本協働子へ委譲する
// （registerActorController）。面は MarketProfileController と同一——列挙の唯一源は
// indicator_controller.js の ACTOR_CONTROLLER_FACES であり、ここに書き写さない（写せば古びる。
// 実際に applyMpGrowth を取り残していた）。全面を実装することは
// tests/actor_controller_faces.test.js が固定する＝host 側に種別分岐を増やさない。
//
// MP と同じく /compute はバイパスし、no-op gateway で state へ instance を登録する（凡例・永続化・
// 復元の対象に含める）。描画はアクター（/tickvol_profile → 背景プリミティブ）へ委譲する。

import {
  apply,
  toggleVisible as facadeToggleVisible,
  remove as facadeRemove,
} from '../../usecase/facade.js';
import { PropertiesDialog } from './properties_dialog.js';

/**
 * TickvolBandsController（取引密度帯のアクター駆動オーケストレーションロール）が host に要求する
 * 最小契約（ISSUE-479 Wave2b・JS レビュー 🟡「生 host 注入」是正）。
 *
 * なぜ在るか: 本協働子は合成根から **host 全体**（IndicatorController 実体）を受け取っており、
 *   契約の宣言も射影も無いまま約 40 メソッド＋20 超フィールドへ触れられる状態だった。
 *   同じ登録口を使う MarketProfileController が契約射影を通しているのに、こちらだけ素通しでは
 *   「アクター駆動指標の受け口」という共通の形が実体を失う。契約は**実使用面から導出**した
 *   （下の列挙は本ファイル内の `host.X` 参照の全数と一致する。過不足は検定が落とす）。
 *
 * @typedef {object} TickvolBandsHost
 * @property {{applied: Array}} _state       適用済みインスタンスを保持する純状態オブジェクト。
 * @property {Map} _meta                     instanceId -> { def } 描画済みメタ。
 * @property {string} _datasetRef            計算対象データセット参照（read）。
 * @property {?object} _document             プロパティダイアログ構築用 document（null 可）。
 * @property {string} _timeframe             現在の表示時間足（gear ダイアログ context 用・read）。
 * @property {function} _paramsObject        params を平坦オブジェクトへ正規化する。
 * @property {function} _defaultVariant      def の既定 variant を返す。
 * @property {function} _defaultParams       def の既定 params を返す。
 * @property {function} _withParams          state の instance params を差し替える。
 * @property {function} _renderLegend        凡例を再描画する。
 * @property {function} _persistAll          applied/favorites/uiState を永続化する。
 * @property {function} _commitState         協働子が算出した次 state を確定する。
 */
export const TICKVOL_BANDS_HOST_CONTRACT = Object.freeze({
  role: 'TickvolBandsHost',
  methods: Object.freeze([
    '_paramsObject', '_defaultVariant', '_defaultParams', '_withParams',
    '_renderLegend', '_persistAll', '_commitState',
  ]),
  fields: Object.freeze(['_state', '_meta', '_datasetRef', '_document', '_timeframe']),
  optionalFields: Object.freeze([]),
});

export class TickvolBandsController {
  /**
   * @param {TickvolBandsHost} host 契約を満たすホスト（合成根が射影を渡す）。
   * @param {object} actor TickvolBandsActor（setParams / setEnabled / refresh / isEnabled）。
   */
  constructor(host, actor) {
    this._host = host;
    this._actor = actor;
  }

  // 適用: /compute をバイパスし instance を登録してアクターを有効化する。
  //   単一インスタンス制約（同一指標の凡例行を 2 つ作らない）は MP と同じ規律。
  async applyMarketProfile(def, variant, params) {
    const host = this._host;
    const existing = host._state.applied.find((i) => i.indicatorId === def.id);
    if (existing) {
      return existing;
    }
    const { state, instance } = await apply(
      host._state,
      { indicatorId: def.id, variant: variant ?? host._defaultVariant(def), params, datasetRef: host._datasetRef },
      { compute: async () => ({ generation: 0 }) },
    );
    host._commitState(state);
    host._meta.set(instance.instanceId, { def });
    this._actor.setParams(params);
    await this._actor.setEnabled(true);
    host._persistAll();
    host._renderLegend();
    return instance;
  }

  // 凡例 eye: 表示/非表示（state.visible を反転しアクターへ同期）。
  async toggleVisible(inst) {
    const host = this._host;
    host._commitState(facadeToggleVisible(host._state, inst.instanceId));
    const updated = host._state.applied.find((i) => i.instanceId === inst.instanceId);
    if (updated) {
      await this._actor.setEnabled(updated.visible);
    }
    host._persistAll();
    host._renderLegend();
  }

  // 凡例 close: 消灯してから applied/meta から除去する（renderer に系列は持たない）。
  async removeInstance(inst) {
    const host = this._host;
    await this._actor.setEnabled(false);
    host._commitState(facadeRemove(host._state, inst.instanceId));
    host._meta.delete(inst.instanceId);
    host._persistAll();
    host._renderLegend();
  }

  // 凡例 gear: 参照セッション数・強調する上位割合を編集し、OK でアクターへ反映する（/compute は呼ばない）。
  //   DOM 不在（node 単体テスト等）は現 params で即時反映へフォールバックする（MP と同規律）。
  onGear(inst, def) {
    const host = this._host;
    const doc = host._document;
    const stored = host._paramsObject(inst.params);
    const current = (stored && Object.keys(stored).length > 0) ? stored : host._defaultParams(def);
    const applyParams = async (values) => {
      host._commitState(host._withParams(host._state, inst.instanceId, values));
      this._actor.setParams(values);
      await this._actor.refresh();
      host._persistAll();
      host._renderLegend();
    };
    // applyParams は async。fire-and-forget の拒否を握って unhandledRejection 化を防ぐ（MP と同規律）。
    const runApply = (values) => {
      applyParams(values).catch((err) => {
        if (typeof console !== 'undefined' && console.error) {
          console.error('[tickvol_bands] gear apply failed', err);
        }
      });
    };
    if (!doc || !def || typeof PropertiesDialog !== 'function') {
      runApply(current);
      return;
    }
    const dialog = new PropertiesDialog({
      document: doc,
      def,
      instance: { ...inst, params: current },
      // 系列を持たない（背景プリミティブが描く）ためスタイル/可視性タブは出さない（MP と同じ）。
      seriesTabs: false,
      // 時間足ゲート（1h 以下）の述語が ctx.timeframe を読む。
      context: { timeframe: host._timeframe },
      onApply: (values) => { runApply(values); },
      onCancel: () => {},
    });
    dialog.open();
  }

  // 復元: 保存済み params をアクターへ渡し、可視なら有効化する。
  async restoreInstance(inst) {
    const host = this._host;
    this._actor.setParams(host._paramsObject(inst.params));
    if (inst.visible) {
      await this._actor.setEnabled(true);
    }
  }

  // ライブ再計算（足切替・tick）: /compute へ流さずアクターの再描画へ委譲する。
  //   帯は時間足に依存しない（バックエンドは常に 1 分足原子で集計）ため、ここでは再取得しない。
  async onLiveRecompute(inst) {
    if (inst.visible) {
      this._actor.onCandlesChanged();
    }
  }

  // 成長状態の適用: 帯には成長軸が無いため no-op（ISSUE-479 Wave2b・JS レビュー 🟡-1）。
  //
  //   なぜ持つのか: 本面はアクターコントローラ契約（ACTOR_CONTROLLER_FACES）の 1 本であり、
  //   レジストリ（`_actorControllerFor`）の戻り値に対して呼ばれ得る。MP 固有の概念だからと
  //   欠かすと、登録済みの協働子であっても呼出が TypeError になる（レビューが node で実測した形）。
  //   帯は 1 分足原子の集計をそのまま塗る＝成長中/確定の区別を持たないので、何もしない。
  applyMpGrowth() {}
}
