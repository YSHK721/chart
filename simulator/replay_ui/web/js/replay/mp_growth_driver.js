// mp_growth_driver.js — MP tick-live 成長駆動の独立ドライバ（ISSUE-133 SRP）。
//
// ISSUE-133（SRP）: setupReplay（replay.js）に混在していた「MP tick-live 成長駆動」アクターを、再生制御
//   （render/playLoop/animateForming/ETA）から分離した。本ドライバは MP アクター（marketProfile）への
//   グリッド拡張 push（growTo の in-flight coalesce）と、バー入場 base 取り直し（enterBar）・足内 tick 供給
//   （feedTick）・確定 fold（settle）の駆動シーケンスのみを所有する。再生制御は本ドライバへ委譲する。
//
// 挙動不変（抽出前 replay.js と同一の await 順序・coalesce 意味論・分岐）:
//   - growInFlight: growTo の多重発火抑止（fire-and-forget 中は 1 回に畳む）。
//   - enterBar(t): バー単位ジャンプの base 因果取得（now=T・from=mpBaseFrom）。呼び出し側が mpOn() で gate。
//   - onFormingTick(mid, sec): revealed tick がグリッド外なら growTo を発火（in-flight coalesce）→ feedTick。
//   - settleMath(winEnd) / settleBar(winEnd): 確定時に winEnd で全窓 fold を await→settleTick。
//
// 依存注入: marketProfile（MP アクター）・mpBaseFrom（再生開始点 replayStart のバー時刻を返す関数・live 参照）・
//   sleepMs（待機）・animMinMs（in-flight ポーリング間隔）。呼び出し側の mpOn() gate は抽出前と同一位置で温存。
export function createMpGrowthDriver({ marketProfile, mpBaseFrom, sleepMs, animMinMs }) {
  // MP tick-live グリッド拡張の in-flight フラグ（pushFormingMA / formingInFlight と同型の coalesce）。
  let growInFlight = false;

  // 当日プロファイル欠陥修正: enterBar(now=当日00:00) は base 窓空＋forming tick0 で縮退グリッド ([0,1]) に
  //   なり、以後の当日実 tick(価格~71000) が範囲外で全て捨てられ当日プロファイルが育たない。revealed tick が
  //   グリッド外に出たら growTo(直近 revealed 秒) を発火し、now までの因果窓で forming を再取得→グリッドを
  //   拡張して forming.ticks を畳み込む（未来リーク禁止＝now は必ず secs[i]）。await でループを止めない
  //   fire-and-forget（完了で再描画）。in-flight 中は coalesce（多重発火抑止）。
  function pushGrowTo(sec) {
    if (growInFlight) return;
    growInFlight = true;
    marketProfile.growTo(sec, mpBaseFrom())
      .catch(() => { /* グリッド拡張失敗はアニメ継続（次 tick が再発火・settle が最終確定） */ })
      .finally(() => { growInFlight = false; });
  }

  // 確定時のグリッド拡張強制（mp_core 一致点）: in-flight を待ってから最終 secs で growTo を await し、
  //   当日窓全 tick を畳み込んだ確定グリッドにしてから settleTick する（backend base=1 dwell と一致）。
  async function settleGrowTo(sec) {
    while (growInFlight) { await sleepMs(animMinMs); }
    growInFlight = true;
    try { await marketProfile.growTo(sec, mpBaseFrom()); }
    catch (_e) { /* 確定着地の拡張失敗は次フレームの enterBar が回復 */ }
    finally { growInFlight = false; }
  }

  return {
    // 成長 push の in-flight 状態（抽出前 replay.js の growInFlight 参照点に対応）。
    isGrowing() { return growInFlight; },

    // バー単位ジャンプで base を now=T（因果）で取り直す（rollover 兼・await ready で直後の
    //   animateForming feedTick 取りこぼしを防ぐ）。呼び出し側が mpOn() で gate する。
    async enterBar(t) {
      await marketProfile.enterBar(t, mpBaseFrom());
    },

    // 足内 tick 供給: revealed tick がグリッド外（縮退 [0,1] 等）なら growTo を発火し now=sec までの
    //   因果窓でグリッドを拡張する（in-flight coalesce・feedTick は継続）。拡張完了後の tick は範囲内で
    //   feedTick が育て、確定時 settleGrowTo が全 tick を再畳み込む。呼び出し側が mpOn()＋sec 有無で gate。
    onFormingTick(mid, sec) {
      if (typeof marketProfile.isTickInGrid === 'function'
          && !marketProfile.isTickInGrid(mid) && !growInFlight) {
        pushGrowTo(sec);
      }
      marketProfile.feedTick(sec, mid);
    },

    // math（終値・足内推移なし）の確定: winEnd で完成プロファイルを一度描く（成長なし）。growTo 非対応
    //   （関数でない）actor は fold をスキップし settleTick のみ。呼び出し側が mpOn() で gate。
    async settleMath(winEnd) {
      if (winEnd != null && typeof marketProfile.growTo === 'function') { await settleGrowTo(winEnd); }
      marketProfile.settleTick();
    },

    // 足確定: 当日窓全 tick を winEnd で再畳み込みしてグリッド確定（mp_core 一致点＝backend base=1 dwell と
    //   一致）してから最終 snapshot を強制描画する。呼び出し側が mpOn() で gate。
    async settleBar(winEnd) {
      if (typeof marketProfile.growTo === 'function') {
        if (winEnd != null) await settleGrowTo(winEnd);
      }
      marketProfile.settleTick();
    },
  };
}
