// market_profile_rig.js — テストが MP をアクター駆動指標として結線するための唯一の口。
//
// なぜ在るか（ISSUE-479 Wave2b J-1 OCP-5 S3）:
//   MP のアクター・解決役は IndicatorController の ctor 引数（marketProfile / mpModeResolver /
//   mpGrowthResolver）ではなくなった。本番の供給経路は合成根（chart_app_wiring の
//   registerActorController）1 本だけである。テストが ctor へ渡し続けると、キーは黙って無視され
//   「注入したつもりで何も届いていない」テストになる（本番に存在しない構築形を検証する状態）。
//
//   本 rig は**本番と同じ形**——契約射影 createHostView + MarketProfileController + 登録——を
//   1 か所に持つ。各テストへ手書きで複製すると、供給の形が変わった日にテストだけが取り残される。
//
// 本番との一致は `chart_app_wiring_market_profile_registration.test.js` が固定する
//   （共有配線が同じ形で登録していること・生 host を渡していないこと・両 root が実体を転送すること）。
//   本 rig はその形の**再現**であって、本番の代用ではない。

import { createHostView } from '../../js/adapter/front/host_view.js';
import {
  MARKET_PROFILE_HOST_CONTRACT,
  MarketProfileController,
} from '../../js/adapter/front/market_profile_controller.js';

/**
 * controller へ MP のアクターコントローラを登録する（合成根と同一の形）。
 *
 * @param {object} controller IndicatorController / ReplayIndicatorController。
 * @param {{actor?: ?object, modeResolver?: ?function, growthResolver?: ?function}} [deps]
 * @returns {MarketProfileController} 登録した協働子（テストが直接叩ける）。
 */
export function registerMarketProfile(controller, deps = {}) {
  const mp = new MarketProfileController(
    createHostView(controller, MARKET_PROFILE_HOST_CONTRACT),
    {
      actor: deps.actor ?? null,
      modeResolver: deps.modeResolver ?? null,
      growthResolver: deps.growthResolver ?? null,
    },
  );
  controller.registerActorController('market_profile', mp);
  return mp;
}
