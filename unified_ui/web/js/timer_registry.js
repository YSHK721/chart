// teardown 用タイマ登録簿（スケルトン — Red フェーズ）。
//
// 契約（基本設計書 §3 R5）:
//   モード切替時に旧モードの setInterval を確実に停止するための登録簿。
//   既存 bootstrap が受ける setInterval/clearInterval 注入口を統合層がラップし、
//   切替時に未 clear の全 interval を一括停止する。
//   - wrap() が返す setInterval は下層 setInterval へ委譲しつつ id を登録簿へ追跡する
//   - wrap() が返す clearInterval は下層 clearInterval へ委譲しつつ id を登録簿から除去する
//   - clearAll() は未 clear の全 interval を下層 clearInterval で停止する
//   - 個別 clearInterval 済みの id は clearAll で二重 clear しない
//
// Red フェーズ: シグネチャのみ。本体は未実装で throw する。

/**
 * 下層タイマ関数をラップし、id 追跡＋一括 clear 機能を付与する。
 * @param {{setInterval:Function, clearInterval:Function}} base 下層タイマ関数群
 * @returns {{setInterval:Function, clearInterval:Function, clearAll:()=>void}} ラップ済みタイマ関数群
 */
export function wrap(base) {
  // 未 clear の interval id を追跡する登録簿。clearAll で一括停止する。
  const active = new Set();
  return {
    setInterval: (fn, ms, ...rest) => {
      const id = base.setInterval(fn, ms, ...rest);
      active.add(id);
      return id;
    },
    clearInterval: (id) => {
      base.clearInterval(id);
      active.delete(id);
    },
    clearAll: () => {
      // 個別 clear 済みは active から除去済み＝二重 clear しない。
      for (const id of active) {
        base.clearInterval(id);
      }
      active.clear();
    },
  };
}
