// 実 UI 経路の投入ドライバ（Phase 8 スライス 6・NFR-09）。
//
// 何をするか: **本番の front 合成根**（`mountSimExecutionPanel`）を fake DOM の上でそのまま
// 動かし、実 HTTP のサーバ（unified ルータ → sim core）へ投入する。結果を JSON 1 行で
// 標準出力へ出す。呼出側は `sim_ui/tests/integration/test_settings_ui_end_to_end.py`。
//
// なぜ front を動かすのか: 投入本文を python 側で組み直すと、front の組み立て規則
// （key_order 順・既定値・規則 E の出し分け・`Leverage` の文字列化）を第 2 実装として
// 書き写すことになり、**front が実際に作る本文がサーバに通るか**を何も確かめられない。
// 実際、python 側の素朴な写し（`str(10.0)` → "10.0"）は front（`String(10)` → "10"）と
// 食い違い、規則 J で 400 になる。動かすのは front 本体でなければならない。
//
// 注入 fetch は「ブラウザの相対パス解決」の代わりである（front は同一オリジンの相対
// パスで書く。node には基準オリジンが無いので base を前置する）。統合 UI の routedFetch と
// 同じ役割であり、front 側のパスは 1 文字も変えない。
//
// これはテストではない（`*.test.js` ではないので `npm test` は収集しない）。
//
// 使い方: node e2e_submit_driver.mjs <base-url> <scenario> [mode]
//   settings   : schema 込み（Tester Settings パネルが settings の供給元）
//   legacy     : schema 面が無い構成（fail-open → 旧フォーム投入・settings 不在）
//   mismatch   : Period を実行対象データセットと食い違わせて投入
//   bad_symbol : 候補外の Symbol を打って投入（受付が 400 で拒む＝理由文の掲示を観測する）
//   mode="watch": 投入後、状態監視の掲示が終端まで追従するのを待ってから出力する
//                 （既定は投入の応答を得た時点で終了＝既存シナリオの観測は変わらない）

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { mountSimExecutionPanel } from "../js/adapter/front/composition_root_execution.js";

const [base, scenario, mode] = process.argv.slice(2);
const SCHEMA_PATH = "/sim/settings-schema";
const SUBMIT_TIMEOUT_MS = 60_000;
/** 監視の掲示が終端に追い付くのを待つ上限（front の照会周期は 1000ms）。 */
const WATCH_TIMEOUT_MS = 300_000;
/** `bad_symbol` シナリオで打つ、候補に無い銘柄（実ブラウザの select では作れない値）。 */
const OUT_OF_CANDIDATE_SYMBOL = "NOT_A_DATASET_SYMBOL";
/** `legacy` シナリオで入力する初期資金（既定値ではストップアウトに達する・下記参照）。 */
const LEGACY_DEPOSIT = "10000000";
/** `custom_range` シナリオの期間（データセットの実在範囲内・`YYYY.MM.DD`＝R10 の書式）。 */
const CUSTOM_FROM = "2025.01.06";
const CUSTOM_TO = "2025.01.10";

/** 相対パスを base で解決する fetch（＋投入本文の採取）。`legacy` は schema 面を塞ぐ。 */
function makeFetch(calls) {
  return async (path, init) => {
    calls.push({ path, init });
    if (scenario === "legacy" && path === SCHEMA_PATH) {
      return { ok: false, status: 404, json: async () => ({ error: "schema 面が無い構成" }) };
    }
    return globalThis.fetch(base + path, init);
  };
}

/** fake DOM のリスナを直接叩く（実ブラウザのイベント発火の代わり）。 */
const fire = (el, ev) => (el._listeners[ev] || []).forEach((f) => f());

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 掲示面（M6）に出ている文字を集める（枠の区切りは空白 1 個で連結する）。 */
function statusPanelText(doc) {
  const panel = findById(doc.body, "simRunStatusPanel");
  if (!panel) return "";
  return flatten(panel)
    .map((n) => String(n.textContent || ""))
    .filter((t) => t.length > 0)
    .join(" ");
}

/** 掲示枠 1 つの文字（class 名で引く）。 */
function statusSlotText(doc, className) {
  const panel = findById(doc.body, "simRunStatusPanel");
  if (!panel) return "";
  const hit = flatten(panel).find(
    (n) => String(n.className || "").split(/\s+/).includes(className),
  );
  return hit ? String(hit.textContent || "") : "";
}

/**
 * 状態監視の掲示がサーバの終端状態に追い付くまで待つ。
 *
 * 終端かどうかも「どの状態か」も**サーバの応答をそのまま**使う（front の語彙も終端集合も
 * ここへ書かない）。掲示された状態がサーバの `status` と一致したら追い付いたと判定する。
 */
async function waitForStatusPanelToCatchUp(doc, jobId) {
  const deadline = Date.now() + WATCH_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const res = await globalThis.fetch(`${base}/sim/jobs/${jobId}`, { cache: "no-store" });
    const payload = await res.json();
    if (payload.terminal === true && statusSlotText(doc, "run-status-state") === payload.status) {
      return payload;
    }
    await sleep(250);
  }
  return null;
}

async function main() {
  const doc = fakeDoc();
  const calls = [];
  let submitted = null;
  let failure = null;
  let settle;
  const done = new Promise((resolve) => { settle = resolve; });

  const { testerView } = await mountSimExecutionPanel({
    doc,
    host: doc.body,
    fetch: makeFetch(calls),
    onSubmitted: (r) => { submitted = r; settle(); },
    onError: (e) => { failure = { message: String(e && e.message), status: e && e.status }; settle(); },
  });

  if (scenario === "legacy") {
    // 旧フォームの初期資金を積み増す。既定値（10,000）は本データセット・本 EA では
    // ストップアウトに達し、**旧経路のエンジン既定（fail_stop）は完走しない**（実測）。
    // settings 経路が完走するのは写像層が `stop_out_action` を明示するからであり、
    // 旧経路との差は Phase 8 以前からの既存挙動である（本ドライバはそれを迂回しない
    // ——利用者が入力できる値を入力しているだけである）。
    findById(doc.body, "execDeposit").value = LEGACY_DEPOSIT;
  }

  if (scenario === "custom_range") {
    // 期間をカスタム指定する（データセットの実在範囲内＝窓は実際に適用され run は完走する）。
    const toggle = findById(doc.body, "testerDateCustom");
    toggle.checked = true;
    fire(toggle, "change");
    const from = findById(doc.body, "testerFromDate");
    const to = findById(doc.body, "testerToDate");
    from.value = CUSTOM_FROM;
    to.value = CUSTOM_TO;
    fire(to, "change");
  }

  if (scenario === "unsupported") {
    // 非対象トークンを**宣言から**引いて選ぶ（`Model=4` 等の数値を書かない）。
    // schema の告知が `on_tokens` で名指ししたキーとトークンをそのまま使う。
    const schema = await (await globalThis.fetch(base + SCHEMA_PATH)).json();
    const notice = schema.unsupported.find(
      (n) => n.trigger === "on_tokens" && n.keys.includes("Model"),
    );
    const control = findById(doc.body, `tester${notice.keys[0]}`);
    control.value = notice.tokens[0];
    fire(control, "change");
  }

  if (scenario === "mismatch") {
    // 実行対象データセットと食い違う `Period` を選ぶ（schema が配る別トークン）。
    const schema = await (await globalThis.fetch(base + SCHEMA_PATH)).json();
    const period = findById(doc.body, "testerPeriod");
    const other = schema.enum_options.Period.find((o) => o.token !== String(period.value));
    period.value = other.token;
    fire(period, "change");
  }

  if (scenario === "bad_symbol") {
    // 候補に無い銘柄を打つ（実行対象データセットが解決できない＝`.ini` の Symbol と
    // 実行仕様の symbol が食い違い、受付が 400 で拒む）。実ブラウザは候補付き select の
    // ため候補外を作れない（ISSUE-422 実測）＝この観測は fake DOM 経路に限る。
    const symbol = findById(doc.body, "testerSymbol");
    symbol.value = OUT_OF_CANDIDATE_SYMBOL;
    fire(symbol, "change");
  }

  fire(findById(doc.body, "runStart"), "click");
  const timer = setTimeout(() => settle(), SUBMIT_TIMEOUT_MS);
  await done;
  clearTimeout(timer);

  if (mode === "watch" && submitted && submitted.job_id) {
    await waitForStatusPanelToCatchUp(doc, submitted.job_id);
  }

  const post = calls.find((c) => c.path === "/sim/jobs");
  process.stdout.write(`${JSON.stringify({
    scenario,
    requested_paths: calls.map((c) => c.path),
    body: post ? JSON.parse(post.init.body) : null,
    submitted,
    failure,
    warnings: testerView.warnings(),
    active_unsupported: testerView.activeUnsupported().map((n) => n.unsupported_id),
    // 画面に出た理由文（宣言の写しでないことを呼出側が宣言表と突き合わせる）
    shown_unsupported_reasons: testerView.activeUnsupported().map((n) => n.reason),
    tester_panel_present: Boolean(findById(doc.body, "simTesterPanel")),
    legacy_ea_field_present: Boolean(findById(doc.body, "execEaName")),
    legacy_deposit_field_present: Boolean(findById(doc.body, "execDeposit")),
    // 掲示面（M6）に実際に出ている文字（§19.6 段階 3・追加のみ）。既存キーは変えない。
    status_panel_text: statusPanelText(doc),
  })}\n`);
}

main().then(
  () => process.exit(0),
  (e) => { process.stderr.write(`${e && e.stack ? e.stack : e}\n`); process.exit(1); },
);
