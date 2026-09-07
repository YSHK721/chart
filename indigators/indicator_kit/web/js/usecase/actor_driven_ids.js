// actor_driven_ids.js — 「アクター駆動型」指標の能力台帳（SOLID 是正 🔴-4・OCP）。
//
// アクター駆動型＝ /compute（系列 JSON）を持たず、専用アクター（例: MarketProfileActor）が
// 自前の取得・描画戦略で駆動する指標。controller は本台帳の述語だけを参照し、指標の具体名
// （'market_profile' 等）で分岐しない（series_kind.js の能力テーブルと同型の設計）。
//
// 新しいアクター駆動型指標の追加は本 Set への 1 行追記で完結する（controller 改変不要）。
// ライブ・リプレイ共有（replay 側は symlink 参照＝単一実体）。
export const ACTOR_DRIVEN_COMPUTE_IDS = new Set([
  'market_profile',
  // 取引密度帯（時刻帯の背景色）。系列を持たず、背景プリミティブをアクターが駆動する。
  'tickvol_bands',
]);

// def（カタログ定義）がアクター駆動型かの述語。def 形状は catalog.js の compute.computeId。
export function isActorDriven(def) {
  return ACTOR_DRIVEN_COMPUTE_IDS.has(def?.compute?.computeId);
}
