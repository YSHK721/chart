// live_ticks_client（adapter/front/live_ticks_client.js）— なめらか tick 再生の供給口。
//
// 設計入力: 依頼者指示 2026-08-31「ライブチャート仕様に合わせて滑らかに再生するようにしろ」。
//   再生機構そのもの（12 秒固定遅延・100ms 粒度・カーソル増分・clockOffset）は **live フロントの
//   LiveTickPlayer が参照実装かつ唯一の実装**であり、dashboard はそれを実行時 import して使う
//   （composition_root_front.js。period_presets / candles と同じ「live から借りる」規約）。
//   本モジュールが持つのは player へ注入する 2 つの取得関数だけ:
//
//     fetchLiveTicks(since, req) → GET <prefix>/live_ticks?since=&timeframe=&datasetRef=
//     loadFormingBar(ref, tf)    → GET <prefix>/forming_bar?datasetRef=&timeframe=
//
//   クエリと応答の契約は live フロントの参照実装
//   （indicator_ui composition_root_front.js の fetchLiveTicks / fetchFormingBar）と同一。
//   指標末尾値の申告（specs / limit / tailsWithinMs）も参照実装と同じクエリで付ける
//   （第 2 表のなめらか再生・依頼者指示 2026-08-31。specs 無しの要求は従来 byte のまま）。
//
// 失敗は null（player は次 poll で回復する・参照実装と同じ規約）。timeoutMs は返らない要求で
//   poll を恒久停止させないための打ち切り（ISSUE-263 と同じ理由。player が req に載せてくる）。

/**
 * live core の tick 供給クライアントを作る。
 *
 * @param {object}   deps
 * @param {Function} deps.fetch      fetch 実装（注入必須・candles_client と同じ理由）
 * @param {string}   deps.apiPrefix  live core の prefix（統合ページなら '/live'）
 * @returns {{fetchLiveTicks: Function, loadFormingBar: Function}}
 */
export function createLiveTicksFeed({ fetch: fetchFn, apiPrefix } = {}) {
  if (typeof fetchFn !== 'function') {
    throw new TypeError('createLiveTicksFeed: fetch の注入は必須');
  }
  if (typeof apiPrefix !== 'string') {
    throw new TypeError('createLiveTicksFeed: apiPrefix の注入は必須');
  }

  /** 増分 tick（{ok, ticks:[[ms,mid],...], barTimes, nowBarTime, serverNowMs} | null）。 */
  async function fetchLiveTicks(since = 0, req = null) {
    const timeoutMs = (req && Number.isFinite(req.timeoutMs)) ? req.timeoutMs : null;
    const controller = (timeoutMs !== null && typeof AbortController === 'function')
      ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
    try {
      let url = `${apiPrefix}/live_ticks?since=${encodeURIComponent(since)}`;
      if (req && req.timeframe) {
        url += `&timeframe=${encodeURIComponent(req.timeframe)}`;
      }
      if (req && req.datasetRef) {
        url += `&datasetRef=${encodeURIComponent(req.datasetRef)}`;
      }
      if (req && req.specs && req.specs.length) {
        url += `&specs=${encodeURIComponent(JSON.stringify(req.specs))}`;
        if (req.limit !== undefined && req.limit !== null) {
          url += `&limit=${encodeURIComponent(req.limit)}`;
        }
        if (req.tailsWithinMs !== undefined && req.tailsWithinMs !== null) {
          url += `&tailsWithinMs=${encodeURIComponent(req.tailsWithinMs)}`;
        }
      }
      const resp = controller ? await fetchFn(url, { signal: controller.signal })
        : await fetchFn(url);
      if (!resp.ok) {
        return null;
      }
      const payload = await resp.json();
      return payload && payload.ok ? payload : null;
    } catch {
      return null;   // 中断・ネットワーク失敗とも null（player は次 poll で回復する）。
    } finally {
      if (timer) {
        clearTimeout(timer);
      }
    }
  }

  /** 形成中バー（{time,open,high,low,close,volume} | null）。 */
  async function loadFormingBar(datasetRef, timeframe) {
    try {
      let url = `${apiPrefix}/forming_bar?datasetRef=${encodeURIComponent(datasetRef)}`;
      if (timeframe) {
        url += `&timeframe=${encodeURIComponent(timeframe)}`;
      }
      const resp = await fetchFn(url);
      if (!resp.ok) {
        return null;
      }
      const payload = await resp.json();
      return payload && payload.ok ? payload.bar : null;
    } catch {
      return null;
    }
  }

  return { fetchLiveTicks, loadFormingBar };
}
