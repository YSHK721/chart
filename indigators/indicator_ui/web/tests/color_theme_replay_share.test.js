// リプレイ側との共有（基本設計_指標カラーテーマ §7.8「共有の実体」）の固定。
//
// 実測（2026-08-09）で確定した共有の実態 — **dual-root と symlink は 1 つの機構の両輪**であり、
// 「全ファイルに symlink を張る」は規約ではない:
//
//   (1) 配信（ブラウザ・実 HTTP）は **dual-root** で解決する。
//       `simulator/replay_ui/framework/static_file_server.py` の `resolve()` は replay の `web_dir`
//       で miss したら同一 rel パスで `shared_js_root`（＝`indigators/indicator_ui/web`）へ
//       フォールバックする（許可根は js / css / vendor サブツリー）。実測: replay ツリーに
//       symlink が **無い** 7 本（`app_chrome_view.js` / `host_view.js` / `catalog_entry.js` /
//       `pair_render_constants.js` / `market_profile_params.js` / `market_profile_controller.js` /
//       `session_ohlc.js`）も `resolve()` は HIT する。よって symlink の欠落は 404 を意味しない。
//   (2) Node（テスト）は symlink を realpath で辿るため、いったんライブ実体へ入れば以降の相対
//       import はライブ側で解決する。したがって symlink が**本当に要る**のは、replay ツリーの
//       **実ファイル**（と replay のテスト）が論理パスで直接 import するモジュールだけである。
//
// よって本ファイルが固定するのは次の 2 つだけにする（在ってほしい形ではなく、要求そのもの）:
//   - TC-CS02: replay ツリーの実ファイルからの論理 import に欠落が無いこと（＝実際の要求）。
//   - TC-CS01 / TC-CS03 / TC-CS04: 共有モジュールの実体はライブ側 1 つで、replay 側に**実ファイル
//     の複製**を作らないこと（複製は必ず取り残しを生む・ISSUE-304）。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  existsSync, lstatSync, readFileSync, readdirSync,
} from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const LIVE_WEB = resolve(HERE, '../');
const REPLAY_WEB = resolve(HERE, '../../../../simulator/replay_ui/web');

// 本段階で追加・使用する共有モジュール（配信パスは live / replay で同一の相対形）。
const SHARED = [
  'js/adapter/front/color_theme_menu.js',
  'js/adapter/front/color_theme_dialogs.js',
  'js/adapter/front/color_theme_controller.js',
  'js/adapter/front/local_storage_theme_gateway.js',
  'js/usecase/color_themes.js',
];

// あるファイルの相対 import を、そのファイルの**論理パス**基準で列挙する。
function importsOf(absPath) {
  const src = readFileSync(absPath, 'utf8');
  return [...src.matchAll(/^\s*import\s[^;]*?from\s+'(\.[^']+)'/gm)].map((m) => m[1]);
}

// 実ファイル（symlink を辿らない）だけを集める。symlink はライブ実体への参照であり、
//   その先の import はライブ側の論理パスで解決される（走査対象はライブ側の責務）。
function realJsFilesUnder(dir, out = []) {
  if (!existsSync(dir)) {
    return out;
  }
  for (const name of readdirSync(dir)) {
    const p = resolve(dir, name);
    const st = lstatSync(p);
    if (st.isSymbolicLink()) {
      continue;
    }
    if (st.isDirectory()) {
      realJsFilesUnder(p, out);
    } else if (name.endsWith('.js')) {
      out.push(p);
    }
  }
  return out;
}

test('TC-CS01 共有モジュールを replay 側に置くなら symlink（実ファイルの複製を作らない）', () => {
  // Arrange / Act / Assert
  //   在席そのものは要求ではない（上記 (1)(2) のとおり dual-root が配信を解決し、Node は
  //   replay の実ファイルが論理 import する分だけを必要とする＝その欠落は TC-CS02 が検出する）。
  //   実測（2026-08-09）: 本 5 本は replay の実ファイルから 1 本も import されていない。
  //   よって固定するのは「置くなら symlink」だけにする（表明を要求へ一致させる）。
  for (const rel of SHARED) {
    const p = resolve(REPLAY_WEB, rel);
    assert.ok(
      !existsSync(p) || lstatSync(p).isSymbolicLink(),
      `${rel} が実ファイルの複製になっている（複製は取り残しを生む・ISSUE-304）`,
    );
  }
});

test('TC-CS02 replay ツリーの実ファイルからの論理 import に欠落が無い（Node も配信も解決できる）', () => {
  // Arrange: replay 固有の実ファイル（js 配下＝配信・テストの双方が通る経路）と replay のテスト。
  //   symlink 越しの推移閉包は走査しない（realpath でライブ側へ入るため、ライブ側の問題になる）。
  const files = [
    ...realJsFilesUnder(resolve(REPLAY_WEB, 'js')),
    ...realJsFilesUnder(resolve(REPLAY_WEB, 'tests')),
  ];
  const missing = [];
  // Act
  for (const abs of files) {
    for (const spec of importsOf(abs)) {
      const target = resolve(dirname(abs), spec);
      if (!existsSync(target)) {
        missing.push(`${relative(REPLAY_WEB, abs)} -> ${spec}`);
      }
    }
  }
  // Assert
  assert.ok(files.length > 0, '走査対象が 0 本（パスの取り違え）');
  assert.deepEqual(missing, [], `replay 実ファイルの論理 import が解決できない: ${missing.join(' / ')}`);
});

test('TC-CS03 共有モジュールの実体はライブ側 1 つ（単一ソース）', () => {
  // Arrange / Act / Assert
  for (const rel of SHARED) {
    assert.ok(lstatSync(resolve(LIVE_WEB, rel)).isFile(), `${rel} の実体はライブ側に置く`);
  }
});

test('TC-CS04 テーマの adapter/front モジュールはライブ側に実体 3 本ちょうど（増減を検出する）', () => {
  // Arrange
  const dir = resolve(LIVE_WEB, 'js/adapter/front');
  // Act: 接頭辞で**列挙**する。名前を先に絞ると 3 本目が増えても構造的に検出できず、
  //   テスト名（「2 本だけ」）が検定内容と食い違う（実際は「この 2 本が在る」しか見ていない）。
  const themeUiFiles = readdirSync(dir).filter((f) => f.startsWith('color_theme_'));
  // Assert: 集合一致＝増えても減っても落ちる。
  assert.deepEqual(themeUiFiles.sort(), [
    'color_theme_controller.js', 'color_theme_dialogs.js', 'color_theme_menu.js',
  ]);
});
