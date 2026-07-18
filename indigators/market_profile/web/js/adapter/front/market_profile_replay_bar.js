// market_profile_replay_bar.js — Market Profile リプレイ用の下部スライダバー（DOM アダプター）。
//
// 設計入力: 依頼（増分1）「replay=ON でチャート下部にスライダバーを表示。構成＝◀リプレイ ラベル＋
//   <input type=range>（min=0/max=足数-1/既定=右端=最新）＋選択中の日時ラベル。OFF で非表示・全期間へ復帰」。
//   移植元 prototype_260630-01/web/index.html（#asof/#asoft スライダ）・app.js（asofIdx・applyAsofView）。
//
// 責務（SRP）: スライダ DOM の生成・表示制御・index→time 変換・日時ラベル更新に限定する。当時プロファイルの
//   再取得（to 付き fetch）や T 縦線描画は本コンポーネントの責務外（MarketProfileActor / primitive が担う）。
//   本バーは input で onScrub(T) を呼ぶだけ（DIP: 上位の editor/actor を知らない）。lightweight-charts に
//   直接依存しない（座標が要る箇所は primitive 側の attach 済み chart を使う＝本バーは純 DOM）。
//
// 非破壊方針: 既定は非表示（display:none 相当の hidden フラグ）。setVisible(true) で初めて可視化する。
//   candles 未設定のうちは onScrub を発火しない（空 candles ガード）。

// UNIX 秒 → 'YYYY-MM-DD HH:MM'（UTC）表示ラベル。移植元 prototype の ISO 日付ラベルに準拠。
function formatCursorLabel(unixSec) {
  const d = new Date(Number(unixSec) * 1000);
  const iso = d.toISOString(); // e.g. '1970-01-01T00:16:40.000Z'
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
}

// モードトグルの選択肢（アンカー = from なし累積 / ローリング = T 直前 ROLL_BARS 本の窓）。
const MODES = [
  { key: 'anchor', label: 'アンカー' },
  { key: 'rolling', label: 'ローリング' },
];

export class MarketProfileReplayBar {
  // document/container: DOM 生成・追加先（container 下部にバーを appendChild）。
  // onScrub: (timeSec) => void。スライダ input で「対応足の time」を通知する（actor.setReplayCursor へ配線）。
  // onChange: () => void。モード（アンカー/ローリング）・スナップショットの状態変更を通知する（actor が
  //   from/today を再計算するトリガ）。省略時 no-op。増分2 A/C。
  constructor({ document, container, onScrub, onChange } = {}) {
    this._doc = document ?? null;
    this._container = container ?? null;
    this._onScrub = typeof onScrub === 'function' ? onScrub : () => {};
    this._onChange = typeof onChange === 'function' ? onChange : () => {};
    this._candles = [];
    this._visible = false;
    this._label = '';
    this._root = null;
    this._range = null;
    this._dateLabel = null;
    // 増分2 状態: モード（既定アンカー）・スナップショット（既定 OFF）。
    this._mode = 'anchor';
    this._snapshot = false;
    this._modeBtns = new Map(); // key -> ボタン要素（is-active トグル用）。
    this._build();
  }

  // バー DOM を一度だけ構築する（◀リプレイ ラベル + モードトグル + range + スナップショット + 日時ラベル）。既定は非表示。
  _build() {
    if (!this._doc || typeof this._doc.createElement !== 'function'
        || !this._container || typeof this._container.appendChild !== 'function') {
      return; // DOM 不在・container が appendChild 非提供（SSR/テスト最小 fake）は no-op（防御）。
    }
    const root = this._doc.createElement('div');
    root.className = 'mp-replay-bar';
    root.setAttribute('hidden', 'true');

    const tag = this._doc.createElement('span');
    tag.className = 'mp-replay-label';
    tag.textContent = '◀リプレイ';

    // モードトグル（segmented・既存 .prop-seg-btn 流用）。押した側でアンカー/ローリングを切替える。
    const seg = this._doc.createElement('span');
    seg.className = 'prop-segmented mp-replay-mode';
    for (const m of MODES) {
      const btn = this._doc.createElement('button');
      btn.className = 'prop-seg-btn' + (m.key === this._mode ? ' is-active' : '');
      btn.textContent = m.label;
      btn.addEventListener('click', () => this._setMode(m.key));
      seg.appendChild(btn);
      this._modeBtns.set(m.key, btn);
    }

    const range = this._doc.createElement('input');
    range.type = 'range';
    range.className = 'mp-replay-range';
    range.min = '0';
    range.max = '0';
    range.value = '0';
    range.disabled = true; // candles 未設定のうちは操作不可（空 candles ガード）。
    range.addEventListener('input', () => this._onInput());

    // スナップショットチェック（既定 OFF）。ON で「当時の見え方」（ローソクトリム＋当日強調）。
    const snapWrap = this._doc.createElement('label');
    snapWrap.className = 'mp-replay-snap';
    const snapCb = this._doc.createElement('input');
    snapCb.type = 'checkbox';
    snapCb.className = 'mp-replay-snap-cb';
    snapCb.addEventListener('change', () => this._setSnapshot(!!snapCb.checked));
    const snapText = this._doc.createElement('span');
    snapText.textContent = 'スナップショット';
    snapWrap.appendChild(snapCb);
    snapWrap.appendChild(snapText);

    const dateLabel = this._doc.createElement('span');
    dateLabel.className = 'mp-replay-date';
    dateLabel.textContent = '';

    root.appendChild(tag);
    root.appendChild(seg);
    root.appendChild(range);
    root.appendChild(snapWrap);
    root.appendChild(dateLabel);
    this._container.appendChild(root);

    this._root = root;
    this._range = range;
    this._dateLabel = dateLabel;
    this._snapCb = snapCb;
  }

  // モード（アンカー/ローリング）を切替え、ボタンの is-active を更新して onChange を通知する。
  _setMode(key) {
    if (key !== 'anchor' && key !== 'rolling') {
      return;
    }
    this._mode = key;
    for (const [k, btn] of this._modeBtns) {
      const active = k === key;
      btn.className = 'prop-seg-btn' + (active ? ' is-active' : '');
    }
    this._onChange();
  }

  // スナップショット ON/OFF を切替え、onChange を通知する。
  _setSnapshot(on) {
    this._snapshot = !!on;
    this._onChange();
  }

  // 現在のモード（'anchor' | 'rolling'）。actor が from を計算する分岐に使う。
  mode() {
    return this._mode;
  }

  // スナップショット状態（true/false）。actor が today/トリムを切替える。
  isSnapshot() {
    return this._snapshot;
  }

  // 足配列を受けてスライダの min=0/max=足数-1/既定 value=右端（最新）を設定する（index→time の元）。
  setCandles(candles) {
    this._candles = Array.isArray(candles) ? candles : [];
    if (!this._range) {
      return;
    }
    const last = Math.max(0, this._candles.length - 1);
    this._range.min = '0';
    this._range.max = String(last);
    this._range.value = String(last); // 既定=右端=最新。
    this._range.disabled = this._candles.length === 0;
    // 既定ラベル（最新足の日時）。空なら空文字。
    this._label = this._candles.length ? formatCursorLabel(this._candles[last].time) : '';
    if (this._dateLabel) {
      this._dateLabel.textContent = this._label;
    }
  }

  // 表示/非表示。true でチャート下部に可視化、false で非表示（全期間へ復帰は actor 側が担う）。
  setVisible(visible) {
    this._visible = !!visible;
    if (this._root) {
      if (this._visible) {
        this._root.setAttribute('hidden', 'false');
      } else {
        this._root.setAttribute('hidden', 'true');
      }
    }
  }

  isVisible() {
    return this._visible;
  }

  // 現在の選択日時ラベル（テスト・表示確認用）。
  currentLabel() {
    return this._label;
  }

  // 現在スライダ位置に対応する足の time（UNIX 秒）。既定は右端＝最新。空 candles は null。
  //   スクラブ前でも「現在の T」を返せるようにし、actor が初期カーソル（T 縦線）に使う。
  currentTime() {
    if (this._candles.length === 0) {
      return null;
    }
    return this._candles[this.currentIndex()].time;
  }

  // 現在スライダ位置の足 index（0..足数-1）。空 candles は 0。スワイプの相対デルタ基準（startIdx）。
  currentIndex() {
    if (this._candles.length === 0) {
      return 0;
    }
    let idx = this._range ? parseInt(this._range.value, 10) : this._candles.length - 1;
    if (Number.isNaN(idx)) {
      idx = this._candles.length - 1;
    }
    return Math.max(0, Math.min(idx, this._candles.length - 1));
  }

  // スライダ input: value（index）→ 対応足 time を決めて onScrub へ通知し、日時ラベルを更新する。
  _onInput() {
    if (!this._range || this._candles.length === 0) {
      return; // 空 candles ガード（onScrub を発火しない）。
    }
    let idx = parseInt(this._range.value, 10);
    if (Number.isNaN(idx)) {
      return;
    }
    idx = Math.max(0, Math.min(idx, this._candles.length - 1));
    const time = this._candles[idx].time;
    this._label = formatCursorLabel(time);
    if (this._dateLabel) {
      this._dateLabel.textContent = this._label;
    }
    this._onScrub(time);
  }
}
