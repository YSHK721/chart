// レポート語彙表の単一ソース検定（ISSUE-502 D-4）。
// 対象: glossary.js の REPORT_VOCAB（唯一の定義）と deriveVocabViews（3 ビューの導出）。
//
// 固定する不変条件:
//   1. 3 ビュー（REPORT_GROUPS / LABELS_JA / GLOSSARY）のキー集合と順序が完全に一致する
//      ＝どれか 1 つにだけキーが載る／落ちるが構造的に起こり得ない。
//   2. 導出は語彙表の内容を過不足なく写す（章立ての順序・呼称・役割/見方）。
//   3. 【計算量】導出で発行する読み取りが、出力に使う読み取りとちょうど一致する
//      （作ってから捨てる計算が 0）。かつ入力を増やしても 1 エントリあたりの読み取り回数が
//      増えない（O(n) の表明）。回数そのものは期待値に焼き込まない——焼き込むと浪費が仕様に
//      昇格する（CLAUDE.md 計算量テスト規約）。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  REPORT_VOCAB, deriveVocabViews, REPORT_GROUPS, LABELS_JA, GLOSSARY,
} from "../glossary.js";

const vocabKeys = () => REPORT_VOCAB.flatMap(([, items]) => items.map((i) => i.key));

// --- 1. 3 ビューが同一のキー集合を共有する ------------------------------------

test("REPORT_VOCAB is a non-empty [title, items[]] table with unique keys", () => {
  assert.ok(Array.isArray(REPORT_VOCAB) && REPORT_VOCAB.length > 0);
  for (const group of REPORT_VOCAB) {
    assert.equal(group.length, 2);
    assert.equal(typeof group[0], "string");
    assert.ok(Array.isArray(group[1]) && group[1].length > 0);
    for (const item of group[1]) {
      for (const field of ["key", "ja", "role", "read"]) {
        assert.equal(typeof item[field], "string", `${item.key}: ${field} が文字列ではありません`);
        assert.ok(item[field].length > 0, `${item.key}: ${field} が空です`);
      }
    }
  }
  const keys = vocabKeys();
  assert.equal(new Set(keys).size, keys.length, "REPORT_VOCAB にキーの重複があります");
});

test("the three views share one key set in one order (no structure can drift alone)", () => {
  const keys = vocabKeys();
  assert.deepEqual(REPORT_GROUPS.flatMap((g) => g[1]), keys);
  assert.deepEqual(Object.keys(LABELS_JA), keys);
  assert.deepEqual(Object.keys(GLOSSARY), keys);
});

// --- 2. 導出が語彙表を過不足なく写す ------------------------------------------

test("deriveVocabViews carries every field of the vocabulary into its view", () => {
  for (const [title, items] of REPORT_VOCAB) {
    const group = REPORT_GROUPS.find((g) => g[0] === title);
    assert.ok(group, `章立てに ${title} がありません`);
    assert.deepEqual(group[1], items.map((i) => i.key));
    for (const item of items) {
      assert.equal(LABELS_JA[item.key], item.ja);
      assert.deepEqual(GLOSSARY[item.key], { role: item.role, read: item.read });
    }
  }
});

test("deriveVocabViews preserves the chapter order of the vocabulary", () => {
  assert.deepEqual(REPORT_GROUPS.map((g) => g[0]), REPORT_VOCAB.map((g) => g[0]));
});

test("deriveVocabViews is a pure function of its argument (no hidden global table)", () => {
  const views = deriveVocabViews([["章", [{ key: "K", ja: "呼", role: "役", read: "見" }]]]);
  assert.deepEqual(views.groups, [["章", ["K"]]]);
  assert.deepEqual(views.labelsJa, { K: "呼" });
  assert.deepEqual(views.glossary, { K: { role: "役", read: "見" } });
});

// --- 3. 計算量テスト（Test Spy で読み取り回数を数える）-------------------------

// 各項目の読み取りを数える語彙表を作る（Test Spy）。
//   reads[field] = 導出が発行した読み取り回数。
function spiedVocab(entryCount, reads) {
  const items = [];
  for (let i = 0; i < entryCount; i++) {
    const raw = { key: `K${i}`, ja: `J${i}`, role: `R${i}`, read: `D${i}` };
    const spy = {};
    for (const field of ["key", "ja", "role", "read"]) {
      Object.defineProperty(spy, field, {
        enumerable: true,
        get() { reads[field] += 1; return raw[field]; },
      });
    }
    items.push(spy);
  }
  return [["章", items]];
}

test("deriveVocabViews issues exactly the reads its output uses (0 discarded)", () => {
  const reads = { key: 0, ja: 0, role: 0, read: 0 };
  const n = 8;
  const views = deriveVocabViews(spiedVocab(n, reads));

  // 出力に使われた読み取り数（＝出力の要素数から数え上げた必要回数）。
  const used = {
    key: views.groups[0][1].length,          // 章立てのキー列
    ja: Object.keys(views.labelsJa).length,  // 呼称表の 1 エントリ 1 値
    role: Object.keys(views.glossary).length,
    read: Object.keys(views.glossary).length,
  };
  for (const field of ["key", "ja", "role", "read"]) {
    assert.equal(views.groups[0][1].length, n);
    // 発行した読み取り − 出力に使った読み取り = 0（作ってから捨てる計算が無い）。
    assert.equal(reads[field] - used[field], 0,
      `${field}: 発行 ${reads[field]} − 使用 ${used[field]} ≠ 0（捨てている計算があります）`);
  }
});

test("the reads per entry do not grow with the size of the vocabulary (O(n))", () => {
  // 2 点（小・大）で 1 エントリあたりの読み取り回数が一致することを固定する。
  // 回数の値そのものは期待値に焼き込まない（浪費を仕様に昇格させないため）。
  const perEntry = (n) => {
    const reads = { key: 0, ja: 0, role: 0, read: 0 };
    deriveVocabViews(spiedVocab(n, reads));
    const total = reads.key + reads.ja + reads.role + reads.read;
    assert.equal(total % n, 0, "読み取り総数がエントリ数で割り切れません（線形ではありません）");
    return total / n;
  };
  assert.equal(perEntry(4), perEntry(64));
});
