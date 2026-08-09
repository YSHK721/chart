// color_value.js — 「色の値そのもの」の判定・正規化・チャネル演算（domain・依存ゼロ）。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md
//   §4.4（保存形は `#rrggbb` 小文字）、§4.5（受理する値域は既存 toHex と同一集合）、
//   §4.6（チャネル別オフセット・0..255 クランプ）、§4.7（変調式の入力は 1 形式に固定）。
//
// 責務（SRP）: 色の**値**に閉じた知識だけを持つ。すなわち
//   「その文字列は色か」「保存形へどう直すか」「チャネルへどう分解し、どう組み立て直すか」。
// 非責務: 色の**意味**（トークン＝ColorRole）・解決順（テーマ → 個別色 → payload → 既定）・
//   時間足変調の係数規則。これらは値ではなく方針であり、usecase（color_themes / color_resolver）
//   が所有する。層をまたいで両者が混ざると「色の値の直し方」が方針ごとに増える。
//
// 単一情報源である理由（実測）: 同一の正規表現・チャネル分解・0..255 クランプ・2 桁 hex 化が
//   `usecase/color_themes.js` と `usecase/color_resolver.js` に private として二重に存在していた。
//   複製は必ず取り残しを生む（受理集合を片方だけ広げると、保存できるのに解決できない色ができる）。
//
// 全域性: すべての公開関数は不正入力でも例外を投げず、判定は false / 正規化は null を返す。

// 既存 toHex（adapter/front/property_control_builders.js:35-49）が受理する集合と一致させる。
//   ここを広げる／狭めるときは、保存（color_themes）と解決（color_resolver）の双方が同時に動く。
const RE_HEX3 = /^#[0-9a-fA-F]{3}$/;
const RE_HEX6 = /^#[0-9a-fA-F]{6}$/;
// `rgb(` / `rgba(` で始まるか（受理判定用・数値の妥当性までは見ない）。
const RE_RGB_PREFIX = /^rgba?\(/i;
// 数値 3 つ（r, g, b）を取り出す（正規化用）。アルファは読まない＝保存形に持ち込まない。
const RE_RGB_CHANNELS = /^rgba?\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)/i;
// 正規化済みの保存形（小文字 6 桁）。テーマの roleColors は必ずこの形（§4.4）。
const RE_HEX6_LOWER = /^#[0-9a-f]{6}$/;

// 色として受理できる値か（3 桁 hex / 6 桁 hex / rgb() / rgba()）。
export function isColorValue(value) {
  return typeof value === 'string'
    && (RE_HEX3.test(value) || RE_HEX6.test(value) || RE_RGB_PREFIX.test(value));
}

// 保存形（`#rrggbb` 小文字）そのものか。チャネル演算の入力として使える形かの判定でもある。
export function isNormalizedHex(value) {
  return typeof value === 'string' && RE_HEX6_LOWER.test(value);
}

// チャネル値を 0..255 へ収める（丸めはしない＝丸め規則は呼び出し側の方針）。
export function clampChannel(n) {
  return Math.min(255, Math.max(0, n));
}

// 保存形 hex を [r, g, b] へ分解する。入力は isNormalizedHex を満たすこと。
export function toChannels(hex) {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

// [r, g, b] を保存形 hex へ組み立てる。各チャネルは 0..255 へクランプする。
//   非有限（NaN / ±Infinity）のチャネルが 1 つでもあれば **null**（解釈不能）を返す。
//   クランプは `Math.min/max` で NaN を素通しするため、そのまま組み立てると `#NaN0405` のような
//   「hex6 に見えない保存形」を作ってしまう（実測: `rgb(1.2.3,4,5)` のような壊れた値から発生）。
//   本関数の戻り値は §4.4 の「値は `#rrggbb` 小文字 6 桁」を満たすか null のどちらかである、という
//   不変条件を型の出口で確定させる（消費者ごとに `isNormalizedHex` を足す取り残しを作らない）。
export function channelsToHex(channels) {
  if (!Array.isArray(channels) || channels.length !== 3
    || !channels.every((c) => Number.isFinite(c))) {
    return null;
  }
  return `#${channels.map((c) => clampChannel(c).toString(16).padStart(2, '0')).join('')}`;
}

// 色を保存形 `#rrggbb`（小文字）へ正規化する。解釈できない値は null（§5.7 F-C9）。
//   アルファは捨てる（§4.7: 変調式の入力を 1 形式に固定して決定論性を保つ）。
//   既存 toHex は解析不能時に既定色 #2962ff を返すが、ここでは既定色への降格を持ち込まない
//   （「色が無い」と「#2962ff である」は別の状態であり、降格は呼び出し側の方針）。
export function normalizeHexColor(value) {
  if (typeof value !== 'string') {
    return null;
  }
  const v = value.trim();
  if (RE_HEX6.test(v)) {
    return v.toLowerCase();
  }
  if (RE_HEX3.test(v)) {
    return `#${v.slice(1).split('').map((c) => c + c).join('')}`.toLowerCase();
  }
  const m = v.match(RE_RGB_CHANNELS);
  if (m) {
    return channelsToHex([m[1], m[2], m[3]].map((n) => Math.round(Number(n))));
  }
  return null;
}

// === 色の数学（加法）=====================================================
// 以下 5 関数は既存の公開面を一切変えずに足す。全域性の規律は上と同一で、
//   「保存形 hex6 か null」／「有限数か null」を出口で確定させる（消費者に型ガードを配らない）。
//
// 受理集合はいずれも **6 桁 hex のみ**（大文字小文字は問わない）。3 桁 hex・rgb() を受けないのは、
//   これらが「色の値」ではなく「色の書き方」の差であり、書き方の吸収は normalizeHexColor の責務
//   だからである（呼び出し側は normalizeHexColor を通してから本群へ渡す＝変換点を 1 つに保つ）。

// 入力を保存形 hex6 へ落とす。hex6 でなければ null（本群の共通入口）。
function toHex6(value) {
  return (typeof value === 'string' && RE_HEX6.test(value)) ? value.toLowerCase() : null;
}

// 2 色をチャネル線形補間で混ぜる。t=0 で a、t=1 で b。t は [0,1] へクランプする。
//   **丸めは必須**（実測）: 0 と 255 の中点 127.5 を丸めずに channelsToHex へ渡すと
//   `(127.5).toString(16)` が `'7f.8'` となり `#7f.87f.87f.8` という hex6 でない保存形ができる。
//   丸め規則は normalizeHexColor の rgb() 経路（Math.round）と同一に揃える。
export function mixChannels(a, b, t) {
  const ha = toHex6(a);
  const hb = toHex6(b);
  if (ha === null || hb === null || !Number.isFinite(t)) {
    return null;
  }
  const k = Math.min(1, Math.max(0, t));
  const ca = toChannels(ha);
  const cb = toChannels(hb);
  return channelsToHex(ca.map((v, i) => Math.round(v + (cb[i] - v) * k)));
}

// sRGB の伝達関数（WCAG 2.x 相対輝度の定義に現れる線形化）とその逆関数。
const SRGB_THRESHOLD = 0.03928;
function toLinear(c) {
  return c <= SRGB_THRESHOLD ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}
function fromLinear(y) {
  return y <= toLinear(SRGB_THRESHOLD) ? y * 12.92 : 1.055 * (y ** (1 / 2.4)) - 0.055;
}

// WCAG 2.x の相対輝度。黒 0・白 1（実測: IEEE754 で厳密に 0 / 1 になる）。
export function relativeLuminance(hex) {
  const h = toHex6(hex);
  if (h === null) {
    return null;
  }
  const [r, g, b] = toChannels(h).map((v) => toLinear(v / 255));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// WCAG 2.x のコントラスト比。同色 1・白黒 21（実測: いずれも厳密値）。引数順に依らない。
export function contrastRatio(a, b) {
  const ya = relativeLuminance(a);
  const yb = relativeLuminance(b);
  if (ya === null || yb === null) {
    return null;
  }
  return (Math.max(ya, yb) + 0.05) / (Math.min(ya, yb) + 0.05);
}

// HSL 色相を deg 回転する（彩度・明度は保つ）。
//   HSL を経由せず {min, max, 色相} から直接組み立てる。S と L は max/min だけの関数なので、
//   max/min をそのまま持ち回れば「彩度・明度を保つ」が構成上保証される（丸め誤差で動かない）。
//   実測: 492,544 色で deg=0 / 360 / 720 / -360 の往復が全数一致、deg=137 でも max/min 不変。
//   無彩色（C=0）は色相が定義されないため不動（回転しても同じ色）。
export function rotateHue(hex, deg) {
  const h = toHex6(hex);
  if (h === null || !Number.isFinite(deg)) {
    return null;
  }
  const [r, g, b] = toChannels(h);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const chroma = max - min;
  if (chroma === 0) {
    return h;
  }
  let sector;
  if (max === r) {
    sector = ((g - b) / chroma) % 6;
  } else if (max === g) {
    sector = (b - r) / chroma + 2;
  } else {
    sector = (r - g) / chroma + 4;
  }
  const hue = (((sector * 60 + deg) % 360) + 360) % 360;
  const hp = hue / 60;
  const x = chroma * (1 - Math.abs((hp % 2) - 1));
  const table = [
    [chroma, x, 0], [x, chroma, 0], [0, chroma, x],
    [0, x, chroma], [x, 0, chroma], [chroma, 0, x],
  ];
  return channelsToHex(table[Math.floor(hp) % 6].map((v) => Math.round(v + min)));
}

// 彩度 0（相対輝度を保つ無彩色化）。出力は 3 チャネルが等しい灰。
//   「相対輝度を保つ」を満たす灰は 8bit 階調では一般に存在しないため、**到達可能な最良点**
//   （目標輝度に最も近い階調）を選ぶ。逆伝達関数の丸めだけでは不足する（実測: 165,464 色中
//   228 色で隣接階調の方が真に近い＝sRGB 空間の最近傍と輝度空間の最近傍が一致しない）ため、
//   候補 3 点を輝度差で比較して確定させる。灰は不動点（実測: 256 階調すべてで自己一致）。
function toGray(n) {
  return channelsToHex([n, n, n]);
}

export function desaturate(hex) {
  const target = relativeLuminance(hex);
  if (target === null) {
    return null;
  }
  const seed = clampChannel(Math.round(fromLinear(target) * 255));
  let best = null;
  let bestErr = Infinity;
  for (const n of [seed - 1, seed, seed + 1]) {
    if (n < 0 || n > 255) {
      continue;
    }
    const err = Math.abs(relativeLuminance(toGray(n)) - target);
    if (err < bestErr) {
      bestErr = err;
      best = n;
    }
  }
  return toGray(best);
}
