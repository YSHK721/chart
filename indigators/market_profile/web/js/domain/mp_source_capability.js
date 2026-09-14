// mp_source_capability.js — Market Profile「ソース能力記述子」の単一情報源（domain）。
//
// 設計入力（ISSUE-097 🟡-8/🟡-9/🔵-20・ISSUE-099 🟡-5 の起点）: MP のソース（zp / dwell / …）
//   ごとの挙動差が、`src === 'zp'` の直書き述語として複数ファイル（catalog_entry.js・
//   market_profile_actor.js・market_profile_primitive.js・composition_root_front.js）へ散在し、
//   新ソース追加が各ファイル約 10 箇所の同期編集を強制していた。本モジュールは各ソースの能力を
//   単一の記述子テーブルへ集約し、各ファイルは記述子参照のみへ寄せる。新ソース追加＝本テーブルへ
//   1 エントリ追加で閉じる（OCP）。
//
// 挙動不変: 記述子から導出した述語結果は集約前の各リテラル（_MP_ZP_TF /
//   MP_ZP_SESSIONS_BLOCKED_TFS / incremental / period / poc / labels）と完全一致する
//   （tests/mp_source_capability.test.js が全 player tf で固定）。
//
// 記述子フィールド:
//   id                : ソース識別子（'zp' | 'dwell' | …）
//   label             : 日本語表示ラベル（catalog の enumLabels）
//   selectable        : src ENUM の選択肢に出す（表示順は配列順）
//   incremental       : per-tick forming 増分が可能か（actor._isIncremental）
//   hasPeriodWindow   : 当日窓 period=day を持つか（period param 表示 / actor._periodExtra）
//   supportedTfs      : 日別/tf-period 列を描ける tf 集合。null = 無制限（全 player tf）
//   blockedSessionTfs : 日別（sessions）モードで選択不可の tf 集合
//   poc               : POC 描画様式 'star'（POC*・黄星）| 'line'（通常線）
//   showLabels        : POC*/VAH/VAL の価格ラベルを描くか
//   tfPeriodSrc       : tf-period 列取得時に付与する src。null = 既定（min-unit カウント）

// zp 対応 tf は Python（tf_period_profile_controller._ZP_TF_ALLOWED）が唯一源で、JS へは
//   **生成物**（mp_capability_generated.js）として配る（ISSUE-264）。かつてここは手書きの
//   写しで、同期手段が無かった。ずれるとサーバは 400 を返すのにフロントは選択可能なまま
//   ＝無言の機能不全になる（ISSUE-253 と同型）。値を変えるときは Python 側だけを変え、
//   生成器（tools/gen_js_parity_golden.py）を再実行する。
import { ZP_SUPPORTED_TFS } from './mp_capability_generated.js';
import { TF_CODES } from './tf_meta.js';

const _ZP_SUPPORTED_TFS = new Set(ZP_SUPPORTED_TFS);
// 日別（sessions）で選択不可の tf＝player tf のうち zp 非対応のもの（**導出**・補集合）。
//   かつては独立の literal だったため、対応 tf を変えても追随しなかった。
const _ZP_BLOCKED_SESSION_TFS = new Set(TF_CODES.filter((tf) => !_ZP_SUPPORTED_TFS.has(tf)));
const _EMPTY_TFS = new Set();

// 既定（未知 src / 未選択）＝非 zp の従来挙動。dwell もこの既定と同一に振る舞う。
const _DEFAULT_CAPABILITY = Object.freeze({
  id: null,
  label: null,
  selectable: false,
  incremental: true,
  hasPeriodWindow: false,
  supportedTfs: null,
  blockedSessionTfs: _EMPTY_TFS,
  poc: 'line',
  showLabels: false,
  tfPeriodSrc: null,
});

// ソース能力記述子テーブル（配列順＝ENUM 表示順）。新ソースは 1 エントリ追加で閉じる。
const _DESCRIPTORS = [
  Object.freeze({
    id: 'dwell',
    label: '滞在時間(実ティック)',
    selectable: true,
    incremental: true,
    hasPeriodWindow: false,
    supportedTfs: null,
    blockedSessionTfs: _EMPTY_TFS,
    poc: 'line',
    showLabels: false,
    tfPeriodSrc: null,
  }),
  Object.freeze({
    id: 'zp',
    label: '超過占有z(p)',
    selectable: true,
    incremental: false,
    hasPeriodWindow: true,
    supportedTfs: _ZP_SUPPORTED_TFS,
    blockedSessionTfs: _ZP_BLOCKED_SESSION_TFS,
    poc: 'star',
    showLabels: true,
    tfPeriodSrc: 'zp',
  }),
];

const _BY_ID = new Map(_DESCRIPTORS.map((d) => [d.id, d]));

// 指定 src の能力記述子を返す。未登録（candle/m1/undefined/null）は既定（非 zp 挙動）へ縮退。
export function mpSourceCapability(srcId) {
  const d = _BY_ID.get(srcId);
  if (d) { return d; }
  // 既定に id だけ差し替えた読み取り専用ビューを返す（呼び出し側が id を参照できるよう）。
  return srcId == null ? _DEFAULT_CAPABILITY : Object.freeze({ ..._DEFAULT_CAPABILITY, id: srcId });
}

// 指定 src が指定 tf で日別/tf-period 列を描けるか（supportedTfs=null は無制限＝常に true）。
//   旧: catalog `src==='zp' && !_MP_ZP_TF.has(tf)` / composition `mpSrc()!=='zp' || ZP_TF_ALLOWED.has(tf)`。
export function mpSupportsTf(srcId, tf) {
  const s = mpSourceCapability(srcId).supportedTfs;
  return !s || s.has(tf);
}

// tf-period 列取得に付与する src（未対応ソースは null＝既定 min-unit カウント）。
//   旧 composition: `mpSrc()==='zp' ? 'zp' : null`。
export function mpTfPeriodSrc(srcId) {
  return mpSourceCapability(srcId).tfPeriodSrc;
}

// src ENUM の選択肢（表示順）。
export const MP_SELECTABLE_SOURCES = _DESCRIPTORS.filter((d) => d.selectable).map((d) => d.id);

// src ENUM の既定値（依頼者指示 2026-07-12 で zp へ昇格）。
export const MP_DEFAULT_SOURCE = 'zp';

// src ENUM の enumLabels（{id: label}）。
export function mpSourceEnumLabels() {
  const out = {};
  for (const d of _DESCRIPTORS) {
    if (d.selectable) { out[d.id] = d.label; }
  }
  return out;
}

// 後方互換エクスポート（日別×1m/5m で zp を選べない tf 集合）。zp 記述子の blockedSessionTfs と同一実体。
export const MP_ZP_SESSIONS_BLOCKED_TFS = _ZP_BLOCKED_SESSION_TFS;
