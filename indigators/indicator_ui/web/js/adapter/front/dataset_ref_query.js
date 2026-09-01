// 表示対象 datasetRef を URL クエリで上書きする（ISSUE-447 段階 1・A-3 案 U1）。
//
// 設計入力: `.doc/MT5_REALTIME_TICK_SUPPLY_BASIC_DESIGN.md` §9 承認表 A-3
//   「**承認（U1）**: `datasetRef` を URL クエリで上書き（既定挙動不変）。
//     案 U2（セレクタ UI）は不採用」。
//
// なぜ入口の定数ではいけないか: `index.html` が対象 ref を値として自称していると、
//   別 ref（jp225_mt5）を実 UI で確認するたびに入口を書き換える義務が生まれる。戻し忘れれば
//   既定表示が静かに別データへ移る。入口が持つのは**既定**だけにし、選択は URL で外から与える。
//
// 純粋関数である（`location` を関数内で読まない）。入口が `location.search` を渡す。
//   参照実装 `simulator/sim_ui/web/js/adapter/front/report_source_client.js:27-35`
//   （`readJobId(search)`）と同型: search 注入・先頭 '?' 吸収・trim 後に空なら未指定。
//   相違は 1 点のみで、未指定時に null ではなく**呼び出し側の既定値**を返す。
//
// ref の実在は検証しない（意図的）。未知 ref はそのまま通し、サーバの既存エラー経路
//   （`/candles` → validation → HTTP 400「未知の datasetRef です」）に委ねる。front に
//   第 2 の台帳を置くと台帳が 2 つになって静かにずれる（ISSUE-368 原因 α と同型）。

/** 上書きに使うクエリ名。`?dataset=jp225_mt5` の形で与える。 */
export const DATASET_REF_QUERY_PARAM = 'dataset';

/**
 * `search`（`location.search` 相当）から datasetRef を決める。
 *
 * 未指定・空・空白のみ・非文字列はすべて「未指定」とみなし `fallback` を返す
 * （既定経路では解析を 1 回も発行しない）。
 *
 * @param {string} search  クエリ文字列（先頭の '?' は有っても無くてもよい）
 * @param {string} fallback クエリで指定が無いときに使う既定 ref
 * @returns {string} 使用する datasetRef
 */
export function resolveDatasetRef(search, fallback) {
  if (typeof search !== 'string' || search === '') return fallback;
  // 先頭 '?' の除去は**冗長**である（実測 2026-09-01: `new URLSearchParams('?a=1')` と
  //   `new URLSearchParams('a=1')` は同値。WHATWG URL 仕様が先頭 '?' を除くと定めている）。
  //   変異検定でこの分岐を外しても 16 件全緑＝等価変異体であり、検定の弱さではない。
  //   参照実装 `readJobId` と同じ形を保つために残す（読み手が仕様の細部を知らなくても読める）。
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  const raw = params.get(DATASET_REF_QUERY_PARAM);
  if (raw === null) return fallback;
  const ref = raw.trim();
  return ref === '' ? fallback : ref;
}
