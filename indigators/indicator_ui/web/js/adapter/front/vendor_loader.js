// VendorLoader（adapter/front/vendor_loader.js）— lightweight-charts の読込ガード（ISSUE-166）。
//
// 解決する問題: 配信の途中切断（ERR_CONTENT_LENGTH_MISMATCH・serve.sh 再起動との競合等）で
//   `window.LightweightCharts` が未定義のまま bootstrap すると createChart で即死し、UI は
//   表示されるがチャート無し・全操作無反応になり F5 まで回復しない。cache-bust 付きで
//   最大 3 回再読込し、成功してから bootstrap する（ユーザー承認 2026-07-23）。
//
// なぜモジュールなのか（ISSUE-278 #11）:
//   この手順は配信 3 ページ（indicator_ui / replay_ui / unified_ui）すべてで必要だが、
//   両 core の index.html に inline で二重に書かれ、**実際にユーザーが使う統合ページ
//   （:8000）にだけ入っていなかった**（`onerror → resolve(false)` で起動を諦めていた）。
//   承認済みの防御が実配信から抜ける事故は、手順が複数箇所に手書きされている限り繰り返す。
//   よって手順は 1 モジュールが所有し、3 ページはこれを import して使う。
//
// 環境非依存: document / window / setTimeout は注入可能（SSR・単体テストで差し替えられる）。

/**
 * lightweight-charts を確実に読み込む（既に在れば即返す・失敗時は cache-bust 再試行）。
 *
 * @param {object} [opts]
 * @param {string} [opts.src]          スクリプト URL（ページごとに異なる。既定は同階層の vendor）。
 * @param {number} [opts.retries]      再試行回数（既定 3）。
 * @param {number} [opts.baseDelayMs]  再試行間隔の基準（attempt 倍で伸ばす・既定 500）。
 * @param {object} [opts.doc]          document 実装（注入）。
 * @param {object} [opts.win]          window 実装（注入・`LightweightCharts` の在席判定に使う）。
 * @param {Function} [opts.setTimeout] タイマー実装（注入）。
 * @returns {Promise<object|undefined>} 読み込めた LightweightCharts。全試行が失敗したら undefined。
 */
export async function ensureLightweightCharts({
  src = './vendor/lightweight-charts.js',
  retries = 3,
  baseDelayMs = 500,
  doc = (typeof document !== 'undefined' ? document : null),
  win = (typeof window !== 'undefined' ? window : null),
  setTimeout: setTimeoutImpl = (typeof globalThis !== 'undefined' ? globalThis.setTimeout : null),
} = {}) {
  if (!doc || !win) {
    return undefined;   // DOM 非対応（SSR・純ロジックテスト）＝読み込む対象が無い。
  }
  if (win.LightweightCharts) {
    return win.LightweightCharts;
  }
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    await new Promise((resolve) => {
      const s = doc.createElement('script');
      // cache-bust: 途中切断で壊れた応答がキャッシュされていると、再試行しても同じ壊れた
      //   バイト列を掴む。attempt と時刻でクエリを変え、必ず取り直させる。
      s.src = `${src}${src.includes('?') ? '&' : '?'}retry=${attempt}-${Date.now()}`;
      s.onload = resolve;
      s.onerror = resolve;   // 失敗も resolve し、下の存在検査と待機で判定する。
      doc.head.appendChild(s);
    });
    if (win.LightweightCharts) {
      return win.LightweightCharts;
    }
    if (attempt < retries && setTimeoutImpl) {
      await new Promise((r) => setTimeoutImpl(r, baseDelayMs * attempt));
    }
  }
  return undefined;
}
