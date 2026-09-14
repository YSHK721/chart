// モード別 localStorage 名前空間分離ラッパ（storage ポート版）。
//
// 契約（基本設計書 §3 R4 / code-review 🟡-5）:
//   単一オリジン化で live/replay が同一 localStorage を共有しキー衝突する。
//   既存 bootstrap は `storage`（getItem/setItem/removeItem を持つ localStorage 互換）を受け、
//   内部で `new LocalStorageGateway(storage)` を組む。よって注入口は「get/set gateway」ではなく
//   「storage ポート」であり、本モジュールは storage ポートをモード別 prefix でラップする
//   （実配線＝unified_root.js が注入する実体）。
//   - getItem/setItem/removeItem は下層 storage へ `${mode}:${key}` の物理キーで委譲する
//   - live と replay で同一論理キーが物理的に衝突しない（相互不可視）
//   - 下層 storage の既存キー体系（prefix 無しキー）には触れない

/**
 * 下層 storage（localStorage 互換）をモード名前空間でラップする。
 * @param {{getItem:(key:string)=>*, setItem:(key:string,value:*)=>void, removeItem:(key:string)=>void}} base 下層 storage
 * @param {'live'|'replay'} mode 名前空間モード
 * @returns {{getItem:Function, setItem:Function, removeItem:Function, key:Function, length:number, clear:Function}} prefix 付与ラッパ
 */
export function scopedStorage(base, mode) {
  const prefix = `${mode}:`;
  return {
    getItem: (key) => base.getItem(prefix + key),
    setItem: (key, value) => base.setItem(prefix + key, value),
    removeItem: (key) => base.removeItem(prefix + key),
    // LocalStorageGateway は参照しないが localStorage 互換のため素通しで用意（防御）。
    key: (index) => base.key(index),
    get length() {
      return base.length;
    },
    clear: () => {
      /* モード横断の一括 clear は行わない（他モード資産を消さない）。 */
    },
  };
}
