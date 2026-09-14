// js_layer_direction.test.js（sim core）— フロント（JS）の依存方向ゲートを sim へ展開する
//   （ISSUE-479 Wave2b・JS レビュー 🟡-5）。
//
// なぜ在るか: 依存方向ゲート（J-4）は indicator_ui（live core）と dashboard_ui にしか置かれて
//   おらず、replay / sim は同じ Dependency Rule に従うのに**永久に検出されない**状態だった。
//
// 走査の実装は tools/js_layer_guard.mjs が、検定本体（何を assert するか）は
//   tools/js_layer_guard_suite.mjs が唯一持つ。本ファイルは「どの根を見るか」だけを渡す
//   （core ごとに assert を手書きで複製しない＝片方だけ緩む形を作らない）。
//
// 現状 offender 0 の**回帰錨**である。錨が空振りしていないことは、共有スイート内の
//   検出器自己検定（合成ソースで実際に捕捉する／自 core 除外が他 core まで緩めない）が実証する。

import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { registerLayerDirectionSuite } from '../../../../tools/js_layer_guard_suite.mjs';

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

registerLayerDirectionSuite({
  label: 'sim',
  jsRoot: path.join(WEB, 'js'),
  repoRoot: path.resolve(WEB, '..', '..', '..'),
  // 配信 URL の第 1 セグメント（`/sim/...`）＝この core 自身の名前。
  ownCore: 'sim',
  otherCore: 'live',
  // sim の配信根が持つ層（domain / usecase は未設置＝構造から見つかるものだけを前提にする）。
  requiredLayers: ['adapter', 'public'],
  minFiles: 10,
});
