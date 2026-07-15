// tf_meta.js（domain）— tf メタの単一情報源（ISSUE-087 🔴-2）。
//
// 旧状態: TF_BAR_SEC/TF_SECONDS が market_profile_actor.js・growth_window.js・live_tick_player.js・
//   composition_root_front.js（TFP_BAR_SEC）に 4 重定義され、バンドラ（build.mjs の IIFE 連結）の
//   top-level const 衝突が共有を阻害していた。本モジュールへ一本化し、全利用側は import する
//   （連結後も定義は 1 箇所＝衝突しない）。Python 側の単一情報源は marketdata/tf_meta.py
//   （同一の値・同期は golden fixture 検定＝ISSUE-087 🔴-3）。
//
// 規約: 1W/1M の秒長は名目値（7日/30日）。カレンダー周期の厳密境界はラベル規約
//   （session_day/resample）が担い、本表は窓幅・表示近似の用途に限る。

export const TF_BAR_SEC = Object.freeze({
  '1m': 60, '5m': 300, '15m': 900, '30m': 1800,
  '1h': 3600, '4h': 14400, '1D': 86400, '1W': 604800, '1M': 2592000,
});

// 固定周期（floor 可能）tf＝LiveTickPlayer/forming の対応集合（1W/1M はカレンダー周期で対象外。
//   Python marketdata.tf_meta.is_supported_timeframe と同一集合）。
export const FLOOR_TFS = Object.freeze(['1m', '5m', '15m', '30m', '1h', '4h', '1D']);

export function isFloorTimeframe(tf) {
  return FLOOR_TFS.includes(tf);
}
