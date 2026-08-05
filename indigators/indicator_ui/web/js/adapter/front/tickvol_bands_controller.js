// tickvol_bands_controller.js — 取引密度帯（時刻帯の背景色）のアクター駆動オーケストレーション協働子。
//
// IndicatorController が computeId → アクターコントローラのレジストリで本協働子へ委譲する
// （registerActorController）。面は MarketProfileController と同一（applyMarketProfile / toggleVisible /
// removeInstance / onGear / restoreInstance / onLiveRecompute）＝host 側に種別分岐を増やさない。
//
// MP と同じく /compute はバイパスし、no-op gateway で state へ instance を登録する（凡例・永続化・
// 復元の対象に含める）。描画はアクター（/tickvol_profile → 背景プリミティブ）へ委譲する。

import {
  apply,
  toggleVisible as facadeToggleVisible,
  remove as facadeRemove,
} from '../../usecase/facade.js';
import { PropertiesDialog } from './properties_dialog.js';

export class TickvolBandsController {
  /**
   * @param {object} host IndicatorController（MarketProfileHost と同じ面を使う）。
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
}
