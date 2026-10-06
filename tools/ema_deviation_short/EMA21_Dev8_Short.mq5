//+------------------------------------------------------------------+
//| EMA21_Dev8_Short.mq5                                             |
//| 21EMA 上方乖離 8% 水準で売り、HoldBars 本後の始値で買い戻す。      |
//| 規則は tools/ema_deviation_short/rules.py と同一。               |
//|   水準 = 確定足 EMA × (1 + Deviation)                             |
//|   新しい足: 期限切れ建玉を決済 → 未約定指値を取消 → 保有なしなら  |
//|            bid >= 水準 で成行売り、それ以外は水準に指値売り。     |
//|   損切り・利確なし。                                              |
//+------------------------------------------------------------------+
#property version   "1.00"
#include <Trade\Trade.mqh>

input ENUM_TIMEFRAMES Timeframe = PERIOD_D1; // 判定する時間足
input int    EmaPeriod = 21;                  // EMA 期間
input double Deviation = 0.08;                // 上方乖離（0.08 = 8%）
input int    HoldBars  = 5;                   // 保有本数（建てた足から N 本後の始値で決済）
input double Lot       = 1.0;                 // 発注数量
input long   Magic     = 21080;               // マジックナンバー

CTrade   trade;
int      emaHandle = INVALID_HANDLE;
datetime lastBarTime = 0;

int OnInit()
{
   if(HoldBars < 1) return(INIT_PARAMETERS_INCORRECT);
   emaHandle = iMA(_Symbol, Timeframe, EmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(emaHandle == INVALID_HANDLE) return(INIT_FAILED);
   trade.SetExpertMagicNumber(Magic);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   if(emaHandle != INVALID_HANDLE) IndicatorRelease(emaHandle);
}

bool HasPosition(ulong &ticket)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == Magic)
      { ticket = t; return(true); }
   }
   return(false);
}

void DeletePendings()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong t = OrderGetTicket(i);
      if(OrderGetString(ORDER_SYMBOL) == _Symbol && OrderGetInteger(ORDER_MAGIC) == Magic)
         trade.OrderDelete(t);
   }
}

void OnTick()
{
   datetime barTime = iTime(_Symbol, Timeframe, 0);
   if(barTime == 0 || barTime == lastBarTime) return;
   lastBarTime = barTime;

   // 1) 期限切れ建玉の決済（建てた足から HoldBars 本以上経過）
   ulong ticket;
   if(HasPosition(ticket))
   {
      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
      int age = iBarShift(_Symbol, Timeframe, opened, false);
      if(age >= HoldBars) trade.PositionClose(ticket);
   }

   // 2) 前の足の未約定指値を取消（1 足寿命）
   DeletePendings();
   if(HasPosition(ticket)) return;

   // 3) 水準 = 確定足 EMA × (1 + Deviation)
   double ema[];
   if(CopyBuffer(emaHandle, 0, 1, 1, ema) != 1) return;
   double level = NormalizeDouble(ema[0] * (1.0 + Deviation), _Digits);
   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(bid >= level)
      trade.Sell(Lot, _Symbol, 0.0, 0.0, 0.0, "ema_dev_short");
   else
      trade.SellLimit(Lot, level, _Symbol, 0.0, 0.0, ORDER_TIME_GTC, 0, "ema_dev_short");
}
