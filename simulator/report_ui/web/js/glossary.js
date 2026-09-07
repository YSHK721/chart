// レポート項目の語彙表（REPORT_VOCAB）と、そこから導出する章立て（REPORT_GROUPS）・
// 日本語呼称（LABELS_JA）・用語解説（GLOSSARY）。
// 依存0のリーフモジュール（DOM 非依存・他 js 非 import）。compare.js（劣化比較表の章立て・
// ラベル）と用語表示が消費する。詳細設計 §11・試作 index.html:851-889 準拠。
//
// 章立て・呼称は MT5 ReportTester のラベル体系に合わせる。本番 report は BacktestStats 保持
// 指標のみ（§4.5）のため、章立てに載るが report に無いキーは表示側で k in r フィルタにより
// スキップされる（欠落耐性）。

// レポート項目の語彙表（**唯一の定義**・ISSUE-502 D-4）。
//   1 項目 = 1 エントリ = { key: 英ラベル, ja: 日本語呼称, role: 役割, read: 見方 }。
//   章立て（REPORT_GROUPS）・呼称表（LABELS_JA）・用語解説（GLOSSARY）は**すべてこの表から
//   導出**する。かつては同じ 54 キーを 3 つの構造が独立に列挙しており、1 キー追加＝3 箇所の
//   編集、片方だけ落とせば「章立てには出るが呼称も解説も出ない」が無言で成立した。
//   表を 1 つにすれば、キーの取りこぼしは**構造的に起こり得ない**（検定ではなく形で担保する）。
//
//   back（build_report_payload._report）が出す report キーが本表に無いと、画面は英語ラベルの
//   まま章立てからも解説からも漏れる。その包含関係は
//   simulator/report_ui/tests/unit/test_report_vocab_single_source.py が機械的に落とす。
export const REPORT_VOCAB = [
  ["戦略・テスト設定", [
    { key: "Expert", ja: "戦略（エキスパート）",
      role: "検証対象の自動売買ロジック（EA）の識別子。",
      read: "どの戦略の成績かを示す。結果はこの戦略に固有。" },
    { key: "Symbol", ja: "銘柄",
      role: "取引した金融商品（銘柄）。",
      read: "対象市場を確認（JP225＝日経225）。" },
    { key: "Period", ja: "期間",
      role: "バックテストの時間足と対象期間。",
      read: "M1＝1分足。検証範囲が十分かを確認。" },
    { key: "Inputs", ja: "入力パラメータ",
      role: "EA に与えた設定値（パラメータ）。",
      read: "成績はこの設定に依存。再現の前提条件。" },
    { key: "Company", ja: "会社",
      role: "口座を提供するブローカー。",
      read: "約定条件・スプレッドの前提。" },
    { key: "Currency", ja: "通貨",
      role: "損益計算に使う通貨。",
      read: "全金額の単位（JPY）。" },
    { key: "Initial Deposit", ja: "初期証拠金",
      role: "検証開始時の資金。",
      read: "損益率や％ドローダウンの基準値。" },
    { key: "Leverage", ja: "レバレッジ",
      role: "証拠金に対する取引可能額の倍率。",
      read: "高いほど必要証拠金は小さくリスクは大。" },
  ]],
  ["1. 基本設定とテスト環境", [
    { key: "History Quality", ja: "履歴品質",
      role: "使用ヒストリカルデータの整合度。",
      read: "100％が理想。低いと結果の信頼性が下がる。" },
    { key: "Bars", ja: "バー数",
      role: "検証に使ったローソク足の本数。",
      read: "サンプル量の目安。" },
    { key: "Ticks", ja: "ティック数",
      role: "価格更新（ティック）の回数。",
      read: "約定精度の基盤。多いほど現実に近い。" },
    { key: "Symbols", ja: "銘柄数",
      role: "検証した銘柄数。",
      read: "単一銘柄なら1。" },
  ]],
  ["2. 損益と資金効率", [
    { key: "Total Net Profit", ja: "総純損益",
      role: "総利益−総損失＝最終的な純損益。",
      read: "最重要の最終成績。プラスで収益。" },
    { key: "Gross Profit", ja: "総利益",
      role: "勝ち取引の利益合計。",
      read: "収益の源泉の規模。" },
    { key: "Gross Loss", ja: "総損失",
      role: "負け取引の損失合計。",
      read: "コストの規模。絶対値で評価。" },
    { key: "Profit Factor", ja: "プロフィットファクター",
      role: "総利益÷総損失（絶対値）。",
      read: "1超で利益。1.3以上で良好、1未満は赤字。" },
    { key: "Recovery Factor", ja: "リカバリーファクター",
      role: "純益÷最大ドローダウン。",
      read: "高いほどリスクに対し効率よく回復。" },
    { key: "Sharpe Ratio", ja: "シャープレシオ",
      role: "変動リスクあたりの超過収益。",
      read: "高いほど安定。1以上で良好。" },
    { key: "Expected Payoff", ja: "期待利得",
      role: "1取引あたりの平均損益。",
      read: "プラスで優位。取引コストと比較。" },
    { key: "AHPR", ja: "AHPR（算術平均収益率）",
      role: "1取引あたり平均収益率（算術平均）。",
      read: "1超で平均的にプラス。" },
    { key: "GHPR", ja: "GHPR（幾何平均収益率）",
      role: "複利を考慮した平均収益率（幾何平均）。",
      read: "AHPRより実態に近い。1超で資産増。" },
  ]],
  ["3. 取引頻度と保有時間", [
    { key: "Total Trades", ja: "総取引数",
      role: "決済まで完了したポジション数。",
      read: "サンプル数。多いほど統計的に信頼。" },
    { key: "Total Deals", ja: "総ディール数",
      role: "約定（建て＋決済）の回数。",
      read: "概ね取引数の約2倍。" },
    { key: "Minimal position holding time", ja: "最小ポジション保有時間",
      role: "最も短い保有時間。",
      read: "瞬間的な決済の有無を確認。" },
    { key: "Average position holding time", ja: "平均ポジション保有時間",
      role: "平均の保有時間。",
      read: "戦略の時間軸（スキャル/スイング）を判別。" },
    { key: "Maximal position holding time", ja: "最大ポジション保有時間",
      role: "最も長い保有時間。",
      read: "塩漬けや長期保有の有無を確認。" },
  ]],
  ["4. 勝率とポジション別の傾向", [
    { key: "Profit Trades (% of total)", ja: "勝率（勝ち取引）",
      role: "勝ち取引の割合（勝率）。",
      read: "高勝率でも損益比次第。PFと併読。" },
    { key: "Loss Trades (% of total)", ja: "敗率（負け取引）",
      role: "負け取引の割合（敗率）。",
      read: "勝率の裏側。100−勝率。" },
    { key: "Short Trades (won %)", ja: "ショートポジション勝率",
      role: "売り取引の件数と勝率。",
      read: "下落方向での優位性を確認。" },
    { key: "Long Trades (won %)", ja: "ロングポジション勝率",
      role: "買い取引の件数と勝率。",
      read: "上昇方向での優位性を確認。" },
  ]],
  ["5. 勝ち負けの取引詳細", [
    { key: "Largest profit trade", ja: "最大勝ち取引",
      role: "単発で最大の勝ち額。",
      read: "外れ値の影響度を把握。" },
    { key: "Average profit trade", ja: "平均勝ち取引",
      role: "勝ち取引の平均額。",
      read: "平均負けとの比（損益比）で評価。" },
    { key: "Largest loss trade", ja: "最大負け取引",
      role: "単発で最大の負け額。",
      read: "1回あたりリスクの上限。" },
    { key: "Average loss trade", ja: "平均負け取引",
      role: "負け取引の平均額。",
      read: "平均勝ちと比較し損益比を確認。" },
    { key: "Maximum consecutive wins ($)", ja: "最大連続勝ち数（利益）",
      role: "最大の連勝回数（括弧内は利益）。",
      read: "好調局面の継続性。" },
    { key: "Maximum consecutive losses ($)", ja: "最大連続負け数（損失）",
      role: "最大の連敗回数（括弧内は損失）。",
      read: "ドローダウン耐性・資金管理の要。" },
    { key: "Maximal consecutive profit (count)", ja: "最大連続利益（取引数）",
      role: "連続利益の最大額（括弧内は取引数）。",
      read: "最も稼いだ連続局面。" },
    { key: "Maximal consecutive loss (count)", ja: "最大連続損失（取引数）",
      role: "連続損失の最大額（括弧内は取引数）。",
      read: "最も負けた連続局面。資金計画の基準。" },
    { key: "Average consecutive wins", ja: "平均連続勝ち数",
      role: "平均の連勝数。",
      read: "勝ちの続きやすさ。" },
    { key: "Average consecutive losses", ja: "平均連続負け数",
      role: "平均の連敗数。",
      read: "負けの続きやすさ。" },
  ]],
  ["6. リスクとドローダウン", [
    { key: "Balance Drawdown Absolute", ja: "残高ベース絶対ドローダウン",
      role: "初期資金からの最大の落ち込み額。",
      read: "開始直後のリスクの目安。" },
    { key: "Balance Drawdown Maximal", ja: "残高ベース最大ドローダウン",
      role: "確定残高のピークからの最大下落（額/％）。",
      read: "最重要のリスク指標。％が小さいほど安全。" },
    { key: "Balance Drawdown Relative", ja: "残高ベース相対ドローダウン",
      role: "％基準で見た最大下落。",
      read: "資金規模に依らない下落の深さ。" },
    { key: "Equity Drawdown Absolute", ja: "含み損ベース絶対ドローダウン",
      role: "含み損を含む有効証拠金ベースの絶対DD。",
      read: "未決済も含む実リスク。" },
    { key: "Equity Drawdown Maximal", ja: "含み損ベース最大ドローダウン",
      role: "含み損ベースの最大ドローダウン。",
      read: "残高ベースより厳しい実際の証拠金リスク。" },
    { key: "Equity Drawdown Relative", ja: "含み損ベース相対ドローダウン",
      role: "含み損ベースの相対（％）DD。",
      read: "保有中の最大リスク水準。" },
    { key: "Margin Level", ja: "証拠金維持率",
      role: "有効証拠金÷必要証拠金。",
      read: "高いほど余裕。100％割れでロスカット危険。" },
  ]],
  ["7. 統計的指標と相関", [
    { key: "LR Correlation", ja: "線形回帰相関",
      role: "資産曲線の直線への当てはまり（線形回帰相関）。",
      read: "1に近いほど滑らかな右肩上がり。" },
    { key: "LR Standard Error", ja: "線形回帰標準誤差",
      role: "回帰直線からのばらつき。",
      read: "小さいほど安定した成長。" },
    { key: "Z-Score", ja: "Zスコア",
      role: "勝敗の連続性の偏り（統計量）。",
      read: "連勝連敗が偶然か傾向かを判定。" },
    { key: "OnTester result", ja: "OnTester結果",
      role: "EAのカスタム評価関数の戻り値。",
      read: "最適化用の独自スコア（0は未使用）。" },
    { key: "Correlation (Profits,MFE)", ja: "相関係数（利益, MFE）",
      role: "利益と最大含み益（MFE）の相関。",
      read: "高いほど利を伸ばせている。" },
    { key: "Correlation (Profits,MAE)", ja: "相関係数（利益, MAE）",
      role: "利益と最大含み損（MAE）の相関。",
      read: "高いと含み損が結果に影響しやすい。" },
    { key: "Correlation (MFE,MAE)", ja: "相関係数（MFE, MAE）",
      role: "最大含み益と最大含み損の相関。",
      read: "値動きの振れ方の傾向。" },
  ]],
];

// 語彙表から 3 つの表示ビュー（章立て / 呼称 / 用語解説）を導出する純関数。
//   1 エントリの各項目を**ちょうど 1 回ずつ**読み、3 ビューへ同時に配る（作って捨てる計算を
//   持たない＝計算量テストが固定する不変条件）。入力を増やしても 1 エントリあたりの読み取り
//   回数は変わらない（O(n)）。
export function deriveVocabViews(vocab) {
  const groups = [], labelsJa = {}, glossary = {};
  for (const [title, items] of vocab) {
    const keys = [];
    for (const item of items) {
      const { key, ja, role, read } = item;
      keys.push(key);
      labelsJa[key] = ja;
      glossary[key] = { role, read };
    }
    groups.push([title, keys]);
  }
  return { groups, labelsJa, glossary };
}

const _VIEWS = deriveVocabViews(REPORT_VOCAB);

// [章タイトル, [英ラベル, ...]] の配列。劣化比較表・サマリー分類の表示順を規定する。
export const REPORT_GROUPS = _VIEWS.groups;

// 英ラベル → 日本語呼称（仕様書「レポートの定義」準拠）。未登録キーは英語のまま表示する。
export const LABELS_JA = _VIEWS.labelsJa;

// 用語解説（役割/見方）。レポート項目キー → {role, read}。
export const GLOSSARY = _VIEWS.glossary;

// 戦略名 → 戦略の説明（比較・判定タブ「戦略」セクション・約200字）。
export const STRATEGY_INFO = {
  "StopEntryProbe_EA":
    "MAシグナルに依存せず、フラットになる度に現値の上下へ逆指値（BuyStop/SellStop）を両建てで一度だけ設置し、" +
    "片側が約定したら反対側を取消す（OCO）プローブEA。約定玉はSL200/TP500ptsで決済し、フラット復帰で再装填する。" +
    "逆指値の約定挙動・SL/TP・OCO・再アームの実MT5突合検証を目的とした動作確認用戦略。（offset100/Lot0.1/両建て）",
};

// グラフ/ヒートマップ/チャートの見方（取引判断の観点）。試作 index.html:980-1007 準拠。
//   {s:セクション, n:表示名, e:用語キー(data-gg), role, read}。
export const GRAPH_GLOSSARY = [
  { s: "比較・判定（IS vs OOS）", n: "エクイティ曲線（重畳）", e: "Equity IS/OOS", role: "IS(学習)とOOS(検証)の残高推移を同一始点(10,000)で重畳。", read: "判断: OOS線がISと同様に右肩上がりなら戦略は頑健で採用候補。分割後にOOSが失速・右肩下がりなら過剰最適化を疑い不採用。最初の足切り判断に使う。" },
  { s: "比較・判定（IS vs OOS）", n: "純損益の内訳", e: "P/L breakdown", role: "総利益・総損失・純損益を区間別に並置。", read: "判断: OOSで総利益が痩せ総損失が相対的に膨らんでいないか。純益がIS黒字→OOS赤字なら未知区間で機能せず、ライブ投入は見送り。" },
  { s: "比較・判定（IS vs OOS）", n: "最大ドローダウン", e: "Max Drawdown", role: "残高ピークからの下落(JPY/％)をIS/OOS重畳。", read: "判断: OOSのDDがISより大幅に深い/長いなら、実運用で許容できる損失か再評価。ロット・資金量の設計根拠。DD%が口座耐性を超えるなら縮小か不採用。" },
  { s: "比較・判定（IS vs OOS）", n: "指標レーダー", e: "Metrics radar", role: "主要6指標(PF/勝率/ペイオフ/期待値/リターン/低DD)をIS=1.0基準で正規化。", read: "判断: OOS多角形がISの内側に大きく縮む＝総合的に劣化＝過剰最適化。凹む軸(例: 期待値・リターンが中心まで)が戦略の弱点。全体が維持なら頑健。" },
  { s: "比較・判定（IS vs OOS）", n: "劣化比バー", e: "Degradation ratio", role: "各指標のOOS/IS維持率(1.0=維持)。", read: "判断: 1.0未満の指標ほど劣化。期待値・リターンが負(棒なし)＝未知区間で優位性消失。色(赤)の指標が多いほど採用リスク大。どの指標が崩れたかで原因を特定。" },
  { s: "チャート（価格×資産）", n: "価格チャート（ローソク足）", e: "Candlestick", role: "価格(OHLC)と売買マーカー(建玉/決済)。", read: "判断: 損益が出た局面の相場環境(トレンド/レンジ/ボラ)を確認。OOSで相場つきが変わり機能しなくなっていないか、特定の値動きに依存していないかを見極める。" },
  { s: "チャート（価格×資産）", n: "Balance パネル", e: "Balance pane", role: "価格と同一時間軸の資産曲線(下部パネル)。", read: "判断: どの価格局面で資産が伸びた/減ったかを価格と直接照合。特定の急騰・急落だけで稼いでいないか(再現性)を確認。" },
  { s: "チャート（価格×資産）", n: "Drawdown パネル", e: "Drawdown pane", role: "価格と同一時間軸のドローダウン(下部パネル)。", read: "判断: DD拡大局面がどの相場(逆行/レンジ)と一致するか。弱い局面を価格と紐付け、回避条件やフィルタ追加の手掛かりにする。" },
  { s: "グラフ（IS vs OOS）", n: "時間帯別エントリー", e: "Entries by hours", role: "エントリー時刻の分布(IS青/OOS橙)。", read: "判断: 取引集中時間帯がIS/OOSで一致するか。少数の時間帯に依存していると過剰最適化リスク。広く分散するほど汎化的。" },
  { s: "グラフ（IS vs OOS）", n: "曜日別エントリー", e: "Entries by weekdays", role: "曜日ごとのエントリー件数(IS/OOS)。", read: "判断: 特定曜日偏重でないか。曜日構成がIS/OOSで安定していれば挙動が再現的。" },
  { s: "グラフ（IS vs OOS）", n: "月別エントリー", e: "Entries by months", role: "月ごとのエントリー件数(IS/OOS)。", read: "判断: 活動量の安定性。極端な月偏りは特定相場依存の疑い。" },
  { s: "グラフ（IS vs OOS）", n: "時間帯別損益", e: "P&L by hours", role: "決済時刻の時間帯別損益(IS/OOS)。", read: "判断: 稼ぐ時間帯がOOSでも稼げているか。IS黒字→OOS赤字の時間帯＝崩れた局所。運用時間のフィルタ設計に使う。" },
  { s: "グラフ（IS vs OOS）", n: "曜日別損益", e: "P&L by weekdays", role: "曜日別の損益合計(IS/OOS)。", read: "判断: 優位曜日がOOSで維持されるか。負け曜日を除外する判断材料。" },
  { s: "グラフ（IS vs OOS）", n: "月別損益", e: "P&L by months", role: "月別の損益合計(IS/OOS)。", read: "判断: 好不調月の把握。単月の大勝ちに依存していないか。" },
  { s: "グラフ（IS vs OOS）", n: "相関（利益, MFE）", e: "Correlation (Profits,MFE)", role: "各取引の利益と最大含み益の散布(IS/OOS)。", read: "判断: 右肩上がりなら含み益を利益に変換できている(利食いが適切)。OOSで相関が崩れたら利益構造が変化＝決済ロジック再考。" },
  { s: "グラフ（IS vs OOS）", n: "相関（利益, MAE）", e: "Correlation (Profits,MAE)", role: "各取引の利益と最大含み損の散布(IS/OOS)。", read: "判断: 含み損が深い取引が損失に直結していないか。深いMAEでも利益化できているならSL設定が妥当。OOSで悪化なら損切り見直し。" },
  { s: "グラフ（IS vs OOS）", n: "保有時間別損益", e: "Position holding time", role: "保有時間バケット別の損益(IS/OOS)。", read: "判断: 機能する保有時間帯を特定(例: 短期<1mが大赤字なら早すぎる決済が損)。優位な時間帯がOOSでも機能するかで時間軸の妥当性を確認。" },
  { s: "ヒートマップ", n: "損益（曜日×時間）", e: "P&L heatmap", role: "曜日×時間バケットの損益を面表示。", read: "判断: 稼ぐ/負けるバケットを面で把握。優位が一部に偏りすぎていないか(汎化性)。負けバケットの除外条件を検討。" },
  { s: "ヒートマップ", n: "IS vs OOS 損益差", e: "IS-OOS diff", role: "同一バケットの OOS−IS 損益差。", read: "判断: 赤=OOSで悪化したバケット＝過剰最適化の局所。多数が赤なら全面的劣化(不採用)、特定だけ赤なら局所崩壊(その条件を除外して再検討)。" },
  { s: "ヒートマップ", n: "取引回数（曜日×時間）", e: "Trade count", role: "バケット別の取引件数。", read: "判断: 損益の集中が「多数取引」か「少数の偏り」か。少数バケットの大損益は偶然/過剰最適化を疑う。件数の厚いバケットほど統計的に信頼。" },
  { s: "ヒートマップ", n: "勝率（曜日×時間）", e: "Win rate", role: "バケット別の勝率%(50%基準)。", read: "判断: 高勝率バケットの所在。OOSで勝率が崩れる時間帯を特定。低勝率でもペイオフ次第なので平均損益と併読。" },
  { s: "ヒートマップ", n: "平均損益/取引", e: "Avg P&L/trade", role: "バケット別の1取引あたり平均損益。", read: "判断: 取引数に依らない純粋な優位性。1取引あたりプラスのバケットが本当のエッジ。回数ヒートマップと併せ「厚く・プラス」のバケットを重視。" },
];

// --- 用語タブ描画 / hover tip（試作 buildGlossary/wireTips・点9） -------------------

function _gitem(name, enKey, role, read) {
  return `<div class="gitem"><div class="nm">${name}<small>${enKey || ""}</small></div>` +
    `<div class="d"><b>役割</b>${role}</div><div class="d"><b>見方</b>${read}</div></div>`;
}

// 用語説明タブを host(#glossHost) へ描画する（静的・init で 1 回呼ぶ）。
export function buildGlossary(host) {
  if (!host) return;
  let html = "";
  for (const [title, keys] of REPORT_GROUPS) {
    const items = keys.filter((k) => GLOSSARY[k]).map((k) => _gitem(LABELS_JA[k] || k, k, GLOSSARY[k].role, GLOSSARY[k].read));
    if (items.length) html += `<div class="gcard"><h4>${title}</h4>${items.join("")}</div>`;
  }
  const secs = [...new Set(GRAPH_GLOSSARY.map((x) => x.s))];
  for (const sec of secs) {
    const items = GRAPH_GLOSSARY.filter((x) => x.s === sec).map((x) => _gitem(x.n, x.e, x.role, x.read));
    html += `<div class="gcard"><h4>グラフの見方｜${sec}</h4>${items.join("")}</div>`;
  }
  host.innerHTML = html;
}

// data-gk（項目キー）の tip HTML（役割/見方）。未登録は null。
export function gkTip(k) {
  const g = GLOSSARY[k];
  if (!g) return null;
  return `<div class="tt">${LABELS_JA[k] || k}<small>${k}</small></div>` +
    `<div class="d"><b>役割</b>${g.role}</div><div class="d"><b>見方</b>${g.read}</div>`;
}

// data-gg（グラフ/チャートキー）の tip HTML（役割/見方）。未登録は null。
export function ggTip(e) {
  const g = GRAPH_GLOSSARY.find((x) => x.e === e);
  if (!g) return null;
  return `<div class="tt">${g.n}<small>${g.e}</small></div>` +
    `<div class="d"><b>役割</b>${g.role}</div><div class="d"><b>見方</b>${g.read}</div>`;
}

// 項目/グラフタイトルの hover tip（試作 wireTips）を body に結線する（init で 1 回）。
export function wireTips() {
  if (typeof document === "undefined") return;
  const tip = document.createElement("div");
  tip.id = "tip";
  document.body.appendChild(tip);
  document.body.addEventListener("mousemove", (e) => {
    const el = e.target.closest("[data-gk],[data-gg]");
    const html = el && (el.dataset.gk ? gkTip(el.dataset.gk) : ggTip(el.dataset.gg));
    if (!html) { tip.style.display = "none"; return; }
    tip.innerHTML = html;
    tip.style.display = "block";
    let x = e.clientX + 14, y = e.clientY + 16;
    if (x + tip.offsetWidth > innerWidth) x = e.clientX - tip.offsetWidth - 14;
    if (y + tip.offsetHeight > innerHeight) y = e.clientY - tip.offsetHeight - 16;
    tip.style.left = Math.max(4, x) + "px";
    tip.style.top = Math.max(4, y) + "px";
  });
}
