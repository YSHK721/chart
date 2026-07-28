// build.mjs — 単一の自己完結 HTML を生成するビルドスクリプト（node 標準のみ・新規依存なし）。
//
// 目的: file:// で ES Modules が読めない制約を回避し、bundled lightweight-charts JS ＋
//   全 ES Modules（domain/usecase/adapter/front/data）＋ index.html/css をインライン結合して
//   indigators/indicator_ui/out/prototype.html を生成する。
//
// 方式: ES Modules を依存順に連結し、各ファイルの `import ... from '...';` 行を除去、
//   `export ` 修飾子を除去して 1 つの古典 <script>（IIFE）スコープに収める。
//   （事前確認: モジュール間で export シンボル名の衝突が無いこと）。
//
// read-only: lightweight-charts JS は読むだけ。design/ は触らない。

import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const WEB = __dirname;
const REPO = resolve(WEB, '../../..'); // /workspaces/app
const LWC_JS = resolve(REPO, 'lightweight-charts-python-main/lightweight_charts/js/lightweight-charts.js');
const OUT = resolve(WEB, '../out/prototype.html');

// ES Modules を依存順に列挙（内→外）。
const MODULE_ORDER = [
  'js/domain/constraint_eval.js',
  'js/domain/compute_error.js',
  'js/domain/domain_models.js',
  'js/domain/series_kind.js',
  'js/domain/tf_meta.js',
  'js/domain/market_profile_dwell_accumulator.js',
  'js/domain/session_day.js',
  'js/domain/session_ohlc.js',
  'js/domain/mp_source_capability.js',
  'js/domain/mp_display_mode.js',
  'js/usecase/catalog_entry.js',
  'js/usecase/catalog.js',
  'js/usecase/facade.js',
  'js/usecase/intrabar_forming_ids.js',
  'js/usecase/actor_driven_ids.js',
  'js/usecase/chart_templates.js',
  'data/sample_data.js',
  'js/adapter/front/format.js',
  'js/adapter/front/scale_controller.js',
  'js/adapter/front/candle_feed.js',
  'js/adapter/front/series_drawer.js',
  'js/adapter/front/chart_renderer.js',
  'js/adapter/front/compute_http_client.js',
  'js/adapter/front/embedded_compute_gateway.js',
  'js/adapter/front/local_storage_gateway.js',
  'js/adapter/front/catalog_client.js',
  'js/usecase/form_model.js',
  // 期間プリセット: 換算表 v1 と純関数（property_control_builders / properties_dialog が参照）。
  'js/usecase/period_presets.js',
  // 時間足ラベルの単一情報源（timeframeLabels）。properties_dialog が期間プリセットの
  //   見出し表示に使うため、従来位置（indicator_controller 群の後）から前へ移す。
  //   本モジュールは相対 import を持たない葉であり、前方移動で依存順は壊れない。
  'js/adapter/front/timeframe_menu.js',
  'js/adapter/front/property_control_builders.js',
  'js/adapter/front/properties_dialog.js',
  'js/adapter/front/indicator_legend_view.js',
  'js/adapter/front/market_profile_params.js',
  'js/adapter/front/market_profile_controller.js',
  'js/adapter/front/timeframe_controller.js',
  'js/adapter/front/update_scheduler.js',
  'js/adapter/front/recompute_gate.js',
  'js/adapter/front/series_render_router.js',
  'js/adapter/front/indicator_state_store.js',
  'js/adapter/front/series_name_matcher.js',
  'js/adapter/front/indicator_dialog_controller.js',
  'js/adapter/front/indicator_controller.js',
  'js/adapter/front/live_updater.js',
  'js/adapter/front/forming_bar_updater.js',
  'js/adapter/front/live_tick_player.js',
  'js/adapter/front/crosshair_readout_view.js',
  'js/adapter/front/current_price_view.js',
  'js/adapter/front/pair_render_constants.js',
  'js/adapter/front/pair_primitive_base.js',
  'js/adapter/front/pair_lines_primitive.js',
  'js/adapter/front/trade_markers_renderer.js',
  'js/adapter/front/market_profile_client.js',
  'js/adapter/front/market_profile_forming_client.js',
  'js/adapter/front/mp_primitive_roles.js',
  'js/adapter/front/mp_replay_scrub.js',
  'js/adapter/front/mp_chart_layout.js',
  'js/adapter/front/mp_fetch_params.js',
  'js/adapter/front/mp_tick_growth.js',
  'js/adapter/front/mp_mode_transition.js',
  'js/adapter/front/mp_session_tiles.js',
  'js/adapter/front/market_profile_primitive.js',
  'js/adapter/front/market_profile_actor.js',
  'js/adapter/front/chart_bootstrap.js',
  'js/adapter/front/chart_interaction_controller.js',
  'js/adapter/front/scroll_to_latest_button.js',
  // timeframe_menu.js は前方（form_model の直後）へ移動済み（期間プリセットの見出し表示で
  //   properties_dialog が timeframeLabels を参照するため）。
  'js/adapter/front/local_storage_template_gateway.js',
  'js/adapter/front/chart_template_menu.js',
  'js/adapter/front/chart_template_dialogs.js',
  'js/adapter/front/chart_template_controller.js',
  'js/adapter/front/live_follow_controller.js',
  'js/adapter/front/mp_live_mode_coordinator.js',
  'js/adapter/front/tf_period_profile_client.js',
  'js/adapter/front/tf_period_jitter_buffer.js',
  'js/adapter/front/tf_period_profile_actor.js',
  'js/adapter/front/tf_period_tooltip.js',
  'js/adapter/front/composition_root_front.js',
];

// import 行を除去し、export 修飾子を剥がして 1 スコープに収める。
function stripModuleSyntax(src) {
  const lines = src.split('\n');
  const out = [];
  const aliasLines = []; // `import { X as Y }` -> `const Y = X;`（フラットスコープで別名を再現）
  let inImport = false;
  let importBuf = '';

  // 1 つの import 文（単一/複数行）から `X as Y` の別名束縛を抽出。
  const collectAliases = (stmt) => {
    const braced = stmt.match(/\{([\s\S]*?)\}/);
    if (!braced) {
      return;
    }
    for (const spec of braced[1].split(',')) {
      const m = spec.trim().match(/^([A-Za-z0-9_$]+)\s+as\s+([A-Za-z0-9_$]+)$/);
      if (m && m[1] !== m[2]) {
        aliasLines.push(`const ${m[2]} = ${m[1]};`);
      }
    }
  };

  for (const line of lines) {
    const t = line.trim();
    if (inImport) {
      importBuf += ' ' + t;
      if (t.includes('from ') || t.endsWith(';') || t.includes("'")) {
        inImport = false;
        collectAliases(importBuf);
        importBuf = '';
      }
      continue;
    }
    if (/^import\s/.test(t)) {
      if (t.includes('from ') && t.endsWith(';')) {
        // 単一行 import: その場で別名抽出。
        collectAliases(t);
      } else {
        inImport = true;
        importBuf = t;
      }
      continue;
    }
    // export 修飾子を剥がす（export class/function/const/async function/let/var）。
    const l = line.replace(/^(\s*)export\s+(default\s+)?/, '$1');
    out.push(l);
  }
  // 別名束縛を末尾に追加（連結後のフラットスコープで参照可能にする）。
  return out.join('\n') + (aliasLines.length ? '\n' + aliasLines.join('\n') + '\n' : '');
}

async function main() {
  // 1) bundled lightweight-charts（read-only）
  const lwcSrc = await readFile(LWC_JS, 'utf8');

  // 2) ES Modules を連結
  let bundle = '';
  for (const rel of MODULE_ORDER) {
    const src = await readFile(resolve(WEB, rel), 'utf8');
    bundle += `\n// ===== ${rel} =====\n` + stripModuleSyntax(src) + '\n';
  }

  // 3) CSS
  const css = await readFile(resolve(WEB, 'css/app.css'), 'utf8');

  // 4) index.html から body 内のマークアップ（#app + ダイアログ）を抽出
  const indexHtml = await readFile(resolve(WEB, 'index.html'), 'utf8');
  const bodyMatch = indexHtml.match(/<div id="app">[\s\S]*?<\/div>\s*<!-- bundled/);
  const appMarkup = indexHtml.slice(
    indexHtml.indexOf('<div id="app">'),
    indexHtml.indexOf('<!-- bundled lightweight-charts'),
  );

  // 5) 自己完結 HTML を組み立て
  const html = `<!DOCTYPE html>
<html lang="ja" data-theme="dark">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>インジケーター管理 UI（プロトタイプ A方式・単一HTML）</title>
<style>
${css}
</style>
</head>
<body>
${appMarkup.trim()}

<!-- bundled lightweight-charts v4.1.3（read-only をインライン） -->
<script>
${lwcSrc}
</script>

<!-- ES Modules（domain/usecase/adapter/front/data）をインライン IIFE 化 -->
<script>
(function () {
${bundle}
// ブートストラップ
var boot = bootstrap({
  lwc: window.LightweightCharts,
  container: document.getElementById('chart'),
  doc: document,
});
boot.controller.bind();
boot.controller.restore();
})();
</script>
</body>
</html>
`;

  await mkdir(dirname(OUT), { recursive: true });
  await writeFile(OUT, html, 'utf8');
  process.stdout.write(`built: ${OUT} (${html.length} bytes)\n`);
}

main().catch((e) => { process.stderr.write(String(e && e.stack ? e.stack : e) + '\n'); process.exit(1); });
