//+------------------------------------------------------------------+
//|                                                  MA_Slope_EA.mq5 |
//|                                          Copyright 2026, YSHK.721 |
//|                                              https://localhost   |
//+------------------------------------------------------------------+
//| 移動平均線の傾きで売買するシンプルなEA。                          |
//|  - 傾きが上向き → 買い / 下向き → 売り（反転時はドテン）          |
//|  - 確定済みバーで判定（リペイント回避）、新規バーのみ処理        |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, YSHK.721"
#property link      "https://localhost"
#property version   "1.00"

#include <Trade/Trade.mqh>

//--- 入力パラメータ
input int               MA_Period   = 20;            // 移動平均の期間
input ENUM_MA_METHOD    MA_Method   = MODE_EMA;      // 移動平均の種別
input ENUM_APPLIED_PRICE MA_Price   = PRICE_CLOSE;   // 適用価格
input int               SlopeShift  = 1;             // 傾きを測る間隔（バー数）
input double            SlopeMinPts = 1.0;           // 売買に必要な最小傾き（ポイント）
input double            Lot         = 0.1;           // ロット数
input int               StopLoss    = 0;             // 損切り（ポイント、0=無し）
input int               TakeProfit  = 0;             // 利確（ポイント、0=無し）
input ulong             EA_Magic    = 20260618;      // マジックナンバー

//--- グローバル
CTrade   trade;
int      maHandle = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| 初期化                                                           |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(MA_Period <= 0 || SlopeShift <= 0)
     {
      Print("MA_Period と SlopeShift は 1 以上を指定してください。");
      return(INIT_PARAMETERS_INCORRECT);
     }

   maHandle = iMA(_Symbol, _Period, MA_Period, 0, MA_Method, MA_Price);
   if(maHandle == INVALID_HANDLE)
     {
      PrintFormat("iMA ハンドルの生成に失敗。error=%d", GetLastError());
      return(INIT_FAILED);
     }

   trade.SetExpertMagicNumber(EA_Magic);
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| 終了処理                                                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(maHandle != INVALID_HANDLE)
      IndicatorRelease(maHandle);
  }

//+------------------------------------------------------------------+
//| 新規バー判定                                                     |
//+------------------------------------------------------------------+
bool IsNewBar()
  {
   static datetime last_bar_time = 0;
   datetime t = (datetime)SeriesInfoInteger(_Symbol, _Period, SERIES_LASTBAR_DATE);
   if(t != last_bar_time)
     {
      last_bar_time = t;
      return(true);
     }
   return(false);
  }

//+------------------------------------------------------------------+
//| ティック処理                                                     |
//+------------------------------------------------------------------+
void OnTick()
  {
//--- 新規バーでのみ判定する
   if(!IsNewBar())
      return;

//--- 確定済みバーのMA値を取得（index 1 が直近確定バー）
   double ma[];
   ArraySetAsSeries(ma, true);
   int need = SlopeShift + 2;
   if(CopyBuffer(maHandle, 0, 0, need, ma) < need)
      return;

//--- 傾き = 直近確定バー - SlopeShift本前のMA
   double slope = ma[1] - ma[1 + SlopeShift];
   double threshold = SlopeMinPts * _Point;

   int signal = 0;             // 1=買い, -1=売り, 0=様子見
   if(slope > threshold)
      signal = 1;
   else if(slope < -threshold)
      signal = -1;

   if(signal == 0)
      return;

//--- 現在のポジション方向を確認
   int current = 0;            // 1=買い保有, -1=売り保有, 0=無し
   if(PositionSelect(_Symbol) && PositionGetInteger(POSITION_MAGIC) == EA_Magic)
      current = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;

//--- 同方向を既に保有していれば何もしない
   if(signal == current)
      return;

//--- 反対ポジションがあれば決済（ドテン）
   if(current != 0)
      trade.PositionClose(_Symbol);

//--- 新規発注
   OpenPosition(signal);
  }

//+------------------------------------------------------------------+
//| 発注（direction: 1=買い, -1=売り）                               |
//+------------------------------------------------------------------+
void OpenPosition(const int direction)
  {
   double volume = NormalizeLot(Lot);
   if(volume <= 0.0)
     {
      Print("有効なロット数を算出できませんでした。Lot 入力値を確認してください。");
      return;
     }

   double price = (direction == 1)
                  ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                  : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   double sl = 0.0, tp = 0.0;
   if(StopLoss > 0)
      sl = (direction == 1) ? price - StopLoss * _Point : price + StopLoss * _Point;
   if(TakeProfit > 0)
      tp = (direction == 1) ? price + TakeProfit * _Point : price - TakeProfit * _Point;

   sl = (sl > 0) ? NormalizeDouble(sl, _Digits) : 0.0;
   tp = (tp > 0) ? NormalizeDouble(tp, _Digits) : 0.0;

   if(direction == 1)
      trade.Buy(volume, _Symbol, 0.0, sl, tp);
   else
      trade.Sell(volume, _Symbol, 0.0, sl, tp);
  }

//+------------------------------------------------------------------+
//| ロット数を銘柄の制約（最小/最大/ステップ）に合わせる             |
//+------------------------------------------------------------------+
double NormalizeLot(const double lot)
  {
   double min  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double v = lot;
   if(step > 0.0)
      v = MathRound(v / step) * step;       // ステップ単位に丸める
   if(v < min)
      v = min;                              // 最小ロットを下回らない
   if(max > 0.0 && v > max)
      v = max;                              // 最大ロットを超えない

//--- 浮動小数の誤差を除去（ステップの桁数で正規化）
   int digits = (step > 0.0) ? (int)MathCeil(-MathLog10(step)) : 2;
   if(digits < 0)
      digits = 0;
   return(NormalizeDouble(v, digits));
  }
//+------------------------------------------------------------------+
