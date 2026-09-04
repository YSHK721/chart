// market_profile_controller.js — Market Profile（A7）アクター駆動のオーケストレーション協働子。
//
// ISSUE-094 🔴-4: indicator_controller.js（A6 指標管理 UI）へ凝集していた MP アクター駆動の一式
//   （apply/enable/toggle/remove/gear/reapply/restore/live-recompute）を本協働子へ外出しする。
//   indicator_controller は legend/menu からのコールバックを本協働子へ委譲するだけの薄い司令塔へ縮退する。
//
// 設計上の制約（byte 挙動不変）: 共有ベース IndicatorController は replay_ui の
//   ReplayIndicatorController（symlink 単一ソースを extends・触れない）に継承され、subclass は
//   _toggleMarketProfileVisible / _removeMarketProfile / _applyMpGrowth 等の inherited メソッドや
//   _mpParams override（super._mpParams）に依存する。そのため本協働子は host（IndicatorController
//   インスタンス）を受け取り host.* を直接操作する「クラス抽出＋ホスト参照」方式を採る。base の各
//   メソッドは本協働子へ委譲する薄いラッパへ縮退し、既存の呼出面（this._xxx / super._xxx / ctrl._xxx）を
//   そのまま温存する（挙動は抽出前と byte 等価）。_mpParams は subclass override を尊重するため host 経由で呼ぶ。

import {
  apply,
  toggleVisible as mpFacadeToggleVisible,
  remove as mpFacadeRemove,
} from '../../usecase/facade.js';
import { PropertiesDialog } from './properties_dialog.js';

export class MarketProfileController {
  // 依存契約（ISP・ISSUE-099 🟡-4）: 本協働子は host（IndicatorController）の広い公開面ではなく、
  //   MP ロール専用の狭い契約 MarketProfileHost にのみ依存する。契約の単一ソースは
  //   indicator_controller.js（@typedef MarketProfileHost ＋ MARKET_PROFILE_HOST_CONTRACT）で明文化し、
  //   IndicatorController（present）/ ReplayIndicatorController（replay・symlink 継承）が
  //   メンバー名・挙動不変のまま構造的に本契約を満たす。reveal seam の _untilTime は replay subclass
  //   のみ在席する optional 面（present は != null guard で no-op）。
  /**
   * @param {import('./indicator_controller.js').MarketProfileHost} host MP ロール契約を満たすホスト。
   * @param {{actor?: ?object}} [opts] アクターの直接注入（ISSUE-479 Wave2 J-1 OCP-5 S1）。
   *
   *   なぜ口を開けるか: 本協働子はアクターを host の**フィールド名**（`_marketProfile`）で
   *   引いており、「誰がアクターを持っているか」を協働子が知っている状態だった。合成根が
   *   注入できる口があれば、フィールド名に依存しない登録（registerActorController）へ移せる。
   *   **加法**である: `opts` を省略すると従来どおり host を読む（既定の挙動は byte 不変）。
   *   注入した場合は host 側の後付け差し替え（replay 合成根が構築後に代入する経路）に
   *   引きずられない＝注入した合成根が所有者であることを明示する。
   */
  constructor(host, { actor = null } = {}) {
    this._host = host;
    this._injectedActor = actor;
  }

  // MP アクター（注入があればそれ、無ければ host のフィールドを遅延で読む）。
  //   host 読みを遅延にしているのは、合成根が構築後にアクターを差し込む既存経路
  //   （replay: `controller._marketProfile = marketProfile`）を温存するためである。
  _actor() {
    return this._injectedActor ?? this._host._marketProfile;
  }

  // MP アクターへ params を渡す共通経路（apply/gear/restore/連動 再適用で共用）。
  //   ライブ連動（mpModeResolver 注入時）は mode を選択表示モード（gear 記憶／未選択は既定 normal）へ解決してから
  //   渡す（'ticklive' 置換はしない＝直交化）。解決役は同時に userMode（gear 選択）を記憶する。mode 未指定
  //   （旧インスタンス）は解決しない（actor 既定＝通常）。未注入時は _mpParams の結果をそのまま渡す＝byte 不変。
  //   さらに growth 解決役（mpGrowthResolver 注入時）は setParams 後に growing 信号（applyGrowthState）を適用する。
  //   FOLLOW=growing=true（成長 ON）／ANALYSIS=false（static）。未注入時は applyGrowthState を呼ばない＝byte 不変。
  //   marketProfile 未注入時は no-op（呼び出し側の guard と二重防御）。
  applyMpParams(p) {
    const host = this._host;
    if (!this._actor()) {
      return;
    }
    const params = host._mpParams(p);
    if (params.mode != null && host._mpModeResolver) {
      params.mode = host._mpModeResolver(params.mode);
    }
    this._actor().setParams(params);
    this.applyMpGrowth();
  }

  // 直交化: 現在の成長状態（mpGrowthResolver）を MP アクターへ growing 信号として適用する。
  //   setParams（mode 遷移で _exitTicklive→growing リセット）の後に呼び、mode を維持したまま growing を確定する。
  //   解決役未注入 or actor が applyGrowthState 非所持なら no-op（byte 不変）。返り値 growing を呼び出し側が使う。
  applyMpGrowth() {
    const host = this._host;
    if (!host._mpGrowthResolver || !this._actor()) {
      return false;
    }
    const growing = !!host._mpGrowthResolver();
    if (typeof this._actor().applyGrowthState === 'function') {
      this._actor().applyGrowthState({ growing });
    }
    return growing;
  }

  // ライブ連動: チャート FOLLOW/ANALYSIS 遷移時に、現在表示中 MP の実効モードを再適用する（present 固有）。
  //   GrowthCoordinator.onLiveStateChange → reapply として配線される。連動未配線（mpModeResolver 未注入）
  //   時は呼ばれない設計だが、MP 不在/無効/未表示時も自己 guard で no-op（副作用なし）。
  //   実効モードは resolver(null)（記憶更新なし・実効解決のみ）で強制し、保存 params（bins/va/src/range）は
  //   維持したまま mode だけ差し替えて refresh する（既存 setParams→refresh 経路を再利用・actor 不変）。
  async reapplyMode() {
    const host = this._host;
    if (!this._actor() || !host._mpModeResolver) {
      return;
    }
    if (typeof this._actor().isEnabled === 'function' && !this._actor().isEnabled()) {
      return; // MP 未表示（enabled=false）は再適用不要。
    }
    const inst = host._state.applied.find(
      (i) => host._isMarketProfile(host._catalog.get(i.indicatorId)) && i.visible,
    );
    if (!inst) {
      return; // 表示中 MP インスタンスが無い。
    }
    const params = host._mpParams(host._paramsObject(inst.params));
    params.mode = host._mpModeResolver(null); // 選択表示モード（gear 記憶／未選択は既定）を維持（'ticklive' 置換なし）。
    this._actor().setParams(params);
    // 直交化: mode を維持したまま growing だけをトグルする（applyGrowthState）。FOLLOW=growing=true / ANALYSIS=false。
    const growing = this.applyMpGrowth();
    // growing 時のみ成長エンジンを起動する。present の成長は forming を onLiveTick（→_enterTicklive）で取得する
    //   （live loop(recomputeAllApplied)/初期 add と同一経路）。refresh は /market_profile の base 累積を描くだけで
    //   forming を発火しないため、growing では onLiveTick を呼ぶ。非成長（static＝ANALYSIS）は refresh で選択モードを反映。
    if (growing && typeof this._actor().onLiveTick === 'function') {
      await this._actor().onLiveTick();
    } else if (typeof this._actor().refresh === 'function') {
      await this._actor().refresh();
    }
  }

  // MP 専用適用パス: /compute をバイパスし、state には no-op gateway で instance を登録して
  //   凡例表示・永続化・restore の対象に含める。描画は MarketProfileActor（GET /market_profile →
  //   primitive）へ委譲する。_draw（F3 系列描画）は通さない。
  async applyMarketProfile(def, variant, params) {
    const host = this._host;
    // MP 単一インスタンス制約: 既に MP が適用済みなら新規 legend 行を作らず no-op で
    //   既存インスタンスを返す（二重 legend 行→単一 actor 駆動での状態乖離を防ぐ）。
    //   actor へは触れない: 既存が非表示なら表示状態の乖離、gear 変更済みなら params
    //   の既定値クロバーを招くため、可視・params の現状を保存する。
    const existing = host._state.applied.find(
      (i) => host._isMarketProfile(host._catalog.get(i.indicatorId)),
    );
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
    await this.enableMarketProfile(params);
    host._persistAll();
    host._renderLegend();
    return instance;
  }

  // MP アクターへ params を渡して有効化する（setParams→setEnabled(true)＝取得＋表示）。
  //   setEnabled(true) は内部で refresh も行う。未注入時は no-op。
  async enableMarketProfile(params) {
    const host = this._host;
    if (!this._actor()) {
      return;
    }
    this.applyMpParams(params);
    await this._actor().setEnabled(true);
    // [reveal seam] reveal（replay）では現在バー T（_untilTime）が確定していれば即 enterBar で base を
    //   描画する。present は _untilTime を持たない（undefined）ため常に skip（byte 挙動不変）。
    if (host._untilTime != null && typeof this._actor().enterBar === 'function') {
      await this._actor().enterBar(host._untilTime);
    }
  }

  // MP 凡例 eye: 表示/非表示トグル（state.visible を反転し actor.setEnabled へ同期）。
  async toggleVisible(inst) {
    const host = this._host;
    host._commitState(mpFacadeToggleVisible(host._state, inst.instanceId));
    const updated = host._state.applied.find((i) => i.instanceId === inst.instanceId);
    if (this._actor() && updated) {
      await this._actor().setEnabled(updated.visible);
    }
    host._persistAll();
    host._renderLegend();
  }

  // MP 凡例 close: 非表示＋detach してから applied/meta から除去する（renderer.remove は不要＝
  //   MP は renderer に系列を持たない）。
  async removeInstance(inst) {
    const host = this._host;
    if (this._actor()) {
      await this._actor().setEnabled(false);
      if (typeof this._actor().detach === 'function') {
        this._actor().detach();
      }
    }
    host._commitState(mpFacadeRemove(host._state, inst.instanceId));
    host._meta.delete(inst.instanceId);
    host._persistAll();
    host._renderLegend();
  }

  // MP 凡例 gear: プロパティダイアログで bins/va/src を編集し、onApply で setParams+refresh。
  //   /compute は呼ばない。DOM 不在時は現 params で即時反映（フォールバック）。
  onGear(inst, def) {
    const host = this._host;
    const doc = host._document;
    const stored = host._paramsObject(inst.params);
    const currentParams = (stored && Object.keys(stored).length > 0)
      ? stored
      : host._defaultParams(def);
    const applyParams = async (values) => {
      host._commitState(host._withParams(host._state, inst.instanceId, values));
      if (this._actor()) {
        this.applyMpParams(values);
        // [reveal seam] reveal（replay）かつ **push 成長中**（isGrowingPush＝growing かつ非 sessions）のときだけ
        //   現在バー T で enterBar（forming push で base 取り直し）。sessions+growing / 非成長は refresh(as-of-T)
        //   へ落とす（成長軸 aware）。present は _untilTime 未設定ゆえ常に refresh＝従来どおり（byte 挙動不変）。
        //   Phase5: 旧 isTicklive()（表示モード）ゲートから isGrowingPush()（成長軸）へ移行（ticklive 撤去）。
        if (host._untilTime != null && typeof this._actor().enterBar === 'function'
            && typeof this._actor().isGrowingPush === 'function'
            && this._actor().isGrowingPush()) {
          await this._actor().enterBar(host._untilTime);
        } else if (typeof this._actor().refresh === 'function') {
          await this._actor().refresh();
        }
      }
      host._persistAll();
      host._renderLegend();
    };
    // applyParams は async。未 await の fire-and-forget のため拒否を .catch で捕捉し
    //   unhandledRejection 化を防ぐ（refresh 失敗等）。
    const runApply = (values) => {
      applyParams(values).catch((err) => {
        if (typeof console !== 'undefined' && console.error) {
          console.error('[MP] gear apply failed', err);
        }
      });
    };
    if (!doc || typeof PropertiesDialog !== 'function') {
      runApply(currentParams);
      return;
    }
    const dialog = new PropertiesDialog({
      document: doc,
      def,
      instance: { ...inst, params: currentParams },
      // ISSUE-109: MP は line/histogram 系列を持たない（TPO 描画は primitive・SeriesDef は
      //   ダミー 1 件）ため、機能しないスタイル/可視性タブ自体を出さない。
      seriesTabs: false,
      // ISSUE-070: MP 解像度パラメータのグレーアウト判定に現 timeframe を渡す
      //   （tf-period が日別列を描くとき resmode/bins/range は無効＝GRID_W 固定のため）。
      context: { timeframe: host._timeframe },
      onApply: (values) => { runApply(values); },
      onCancel: () => {},
    });
    dialog.open();
  }

  // restore（UC-07）の MP 分岐: 保存 params を actor へ渡し、可視だった場合のみ有効化して再取得・表示する。
  //   /compute で計算しようとして失敗させない（MP は backend に compute を持たない）。
  async restoreInstance(inst) {
    const host = this._host;
    const rp = host._paramsObject(inst.params);
    if (this._actor()) {
      this.applyMpParams(rp);
      if (inst.visible) {
        await this._actor().setEnabled(true);
      }
    }
  }

  // recomputeAllApplied（ライブ入口）の MP 分岐: /compute へ流さず actor.refresh（現時間足で再取得）へ委譲する。
  //   [reveal seam] present の MP actor は onLiveTick（ticklive ON=forming 増分 / OFF=refresh 委譲）を持つ。
  //   typeof gate で present は従来どおり onLiveTick を呼び（byte 挙動不変）、onLiveTick を持たない replay
  //   slim actor は skip（render seam の enterBar/feedTick が MP を駆動する）。
  async onLiveRecompute(inst) {
    const host = this._host;
    if (this._actor() && inst.visible && typeof this._actor().onLiveTick === 'function') {
      await this._actor().onLiveTick();
    }
  }
}
