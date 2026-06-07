"""
Backtest Engine — AI Trading System
-------------------------------------
ทดสอบ Technical Analyst + Risk Manager กับข้อมูลราคาย้อนหลัง 2 ปี
ไม่เรียก Claude API ทุก candle (แพงมาก) แต่ใช้ rule-based logic
แทน agent เพื่อความเร็วและประหยัดค่าใช้จ่าย
"""

import json
import random
from datetime import datetime, timedelta
from technical_analyst import calculate_rsi, calculate_macd, find_support_resistance
from risk_manager import RISK_RULES, check_rr_ratio, calculate_lot_size

# =============================
# 1. สร้าง Mock Historical Data
# =============================

def generate_xauusd_history(days: int = 730) -> list[dict]:
    """
    สร้างข้อมูลราคา XAUUSD ย้อนหลัง 2 ปี (H4 candles)
    ใช้ random walk + trend เพื่อจำลองพฤติกรรมตลาดจริง
    """
    random.seed(2024)
    candles = []
    price = 1900.0  # ราคาเริ่มต้นเมื่อ 2 ปีที่แล้ว
    date = datetime.now() - timedelta(days=days)

    for day in range(days):
        # ข้ามวันเสาร์-อาทิตย์
        if date.weekday() >= 5:
            date += timedelta(days=1)
            continue

        # 6 candles ต่อวัน (H4)
        for hour in [0, 4, 8, 12, 16, 20]:
            # Random walk with trend
            trend = 0.15 if day < 365 else 0.25  # bullish trend ปีหลัง
            change = random.gauss(trend, 8.0)
            price = max(1700, min(2800, price + change))

            high = price + random.uniform(2, 15)
            low = price - random.uniform(2, 15)
            open_p = price + random.gauss(0, 3)

            candles.append({
                "datetime": date.replace(hour=hour).isoformat(),
                "open": round(open_p, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(price, 2),
            })

        date += timedelta(days=1)

    return candles


# =============================
# 2. Signal Generator (Rule-based)
# =============================

def generate_signal(prices: list[float]) -> dict:
    """
    จำลอง Technical Analyst ด้วย rule-based logic
    ไม่เรียก Claude API — ประหยัด token มาก
    """
    if len(prices) < 30:
        return {"signal": "NEUTRAL", "confidence": 0}

    rsi = calculate_rsi(prices)
    macd = calculate_macd(prices)
    levels = find_support_resistance(prices)
    current = prices[-1]

    score = 0
    confidence_factors = []

    # RSI signals
    if rsi < 35:
        score += 2
        confidence_factors.append("RSI oversold")
    elif rsi < 45:
        score += 1
        confidence_factors.append("RSI bullish zone")
    elif rsi > 65:
        score -= 2
        confidence_factors.append("RSI overbought")
    elif rsi > 55:
        score -= 1
        confidence_factors.append("RSI bearish zone")

    # MACD signals
    if macd["crossover"] == "bullish_crossover":
        score += 2
        confidence_factors.append("MACD bullish crossover")
    elif macd["crossover"] == "bearish_crossover":
        score -= 2
        confidence_factors.append("MACD bearish crossover")

    # Support/Resistance
    support = levels.get("nearest_support")
    resistance = levels.get("nearest_resistance")

    if support and abs(current - support) / current < 0.005:
        score += 1
        confidence_factors.append("Near support")
    if resistance and abs(current - resistance) / current < 0.005:
        score -= 1
        confidence_factors.append("Near resistance")

    # Convert score to signal
    if score >= 3:
        signal = "BUY"
        confidence = min(90, 55 + score * 8)
        sl = round(current - (current * 0.008), 2)
        tp = round(current + (current * 0.016), 2)
    elif score <= -3:
        signal = "SELL"
        confidence = min(90, 55 + abs(score) * 8)
        sl = round(current + (current * 0.008), 2)
        tp = round(current - (current * 0.016), 2)
    else:
        signal = "NEUTRAL"
        confidence = 40
        sl = 0
        tp = 0

    return {
        "signal": signal,
        "confidence": confidence,
        "rsi": round(rsi, 1),
        "macd_crossover": macd["crossover"],
        "stop_loss": sl,
        "take_profit": tp,
        "current_price": current,
        "factors": confidence_factors,
    }


# =============================
# 3. Simulate Trade Outcome
# =============================

def simulate_outcome(
    signal: str,
    entry: float,
    sl: float,
    tp: float,
    future_prices: list[float],
) -> dict:
    """จำลองผลของ trade โดยดูว่าราคาไปถึง TP หรือ SL ก่อน"""
    if not future_prices:
        return {"result": "TIMEOUT", "pnl_pips": 0}

    for price in future_prices[:100]:  # ดูแค่ 100 candle ถัดไป
        if signal == "BUY":
            if price <= sl:
                return {"result": "LOSS", "pnl_pips": round(sl - entry, 2)}
            if price >= tp:
                return {"result": "WIN", "pnl_pips": round(tp - entry, 2)}
        elif signal == "SELL":
            if price >= sl:
                return {"result": "LOSS", "pnl_pips": round(entry - sl, 2) * -1}
            if price <= tp:
                return {"result": "WIN", "pnl_pips": round(entry - tp, 2)}

    return {"result": "TIMEOUT", "pnl_pips": round(future_prices[-1] - entry if signal == "BUY" else entry - future_prices[-1], 2)}


# =============================
# 4. Run Backtest
# =============================

def run_backtest(
    candles: list[dict],
    lookback: int = 60,
    step: int = 6,
    account_balance: float = 1000.0,
) -> dict:
    """
    รัน backtest ทั้งหมด
    - lookback: จำนวน candle ที่ใช้วิเคราะห์
    - step: ทดสอบทุกกี่ candle (6 = ทุก 1 วัน)
    """
    trades = []
    balance = account_balance
    equity_curve = [{"date": candles[0]["datetime"][:10], "balance": balance}]
    open_trade = None

    prices_all = [c["close"] for c in candles]

    for i in range(lookback, len(candles) - 100, step):
        prices = prices_all[i - lookback:i]
        sig = generate_signal(prices)

        if sig["signal"] == "NEUTRAL":
            continue
        if sig["confidence"] < RISK_RULES["min_confidence"]:
            continue

        # Risk check
        rr = check_rr_ratio(sig["current_price"], sig["stop_loss"], sig["take_profit"])
        if rr < RISK_RULES["min_rr_ratio"]:
            continue

        # Position sizing
        pos = calculate_lot_size(balance, RISK_RULES["max_risk_per_trade_pct"], sig["current_price"], sig["stop_loss"])
        lot = pos["lot_size"]
        risk_amt = pos["risk_amount_usd"]

        # Simulate outcome
        future = prices_all[i:i + 100]
        outcome = simulate_outcome(sig["signal"], sig["current_price"], sig["stop_loss"], sig["take_profit"], future)

        # Calculate P&L
        if outcome["result"] == "WIN":
            pnl = risk_amt * rr
        elif outcome["result"] == "LOSS":
            pnl = -risk_amt
        else:
            pnl = risk_amt * outcome["pnl_pips"] / abs(sig["current_price"] - sig["stop_loss"]) if abs(sig["current_price"] - sig["stop_loss"]) > 0 else 0

        pnl = round(pnl, 2)
        balance = round(balance + pnl, 2)

        trade = {
            "date": candles[i]["datetime"][:10],
            "signal": sig["signal"],
            "confidence": sig["confidence"],
            "entry": sig["current_price"],
            "sl": sig["stop_loss"],
            "tp": sig["take_profit"],
            "rr": rr,
            "lot": lot,
            "result": outcome["result"],
            "pnl": pnl,
            "balance": balance,
            "rsi": sig["rsi"],
        }
        trades.append(trade)
        equity_curve.append({"date": candles[i]["datetime"][:10], "balance": balance})

        if balance <= 0:
            break

    # Stats
    if not trades:
        return {"error": "No trades generated"}

    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    total = len(trades)
    win_rate = round(len(wins) / total * 100, 1) if total > 0 else 0
    total_pnl = round(balance - account_balance, 2)
    max_balance = max(t["balance"] for t in trades)
    min_balance_after_peak = min(
        t["balance"] for t in trades
        if t["balance"] <= max_balance
    )
    max_drawdown = round(max_balance - min_balance_after_peak, 2)
    avg_win = round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0
    profit_factor = round(abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)), 2) if losses and sum(t["pnl"] for t in losses) != 0 else 0

    returns = [t["pnl"] / account_balance for t in trades]
    avg_return = sum(returns) / len(returns) if returns else 0
    std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5 if returns else 1
    sharpe = round((avg_return / std_return) * (252 ** 0.5), 2) if std_return > 0 else 0

    return {
        "summary": {
            "total_trades": total,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "final_balance": balance,
            "max_drawdown": max_drawdown,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "best_trade": max(trades, key=lambda t: t["pnl"])["pnl"],
            "worst_trade": min(trades, key=lambda t: t["pnl"])["pnl"],
            "account_start": account_balance,
            "return_pct": round((balance - account_balance) / account_balance * 100, 1),
        },
        "trades": trades,
        "equity_curve": equity_curve,
    }


# =============================
# 5. รัน และบันทึกผล
# =============================

if __name__ == "__main__":
    print("=" * 55)
    print("Backtest Engine — XAUUSD · 2 Years · H4")
    print("=" * 55)

    print("\nGenerating 2-year price history...")
    candles = generate_xauusd_history(730)
    print(f"Total candles: {len(candles)}")

    print("Running backtest...")
    result = run_backtest(candles, account_balance=1000.0)

    if "error" in result:
        print(f"Error: {result['error']}")
    else:
        s = result["summary"]
        print(f"\nResults:")
        print(f"  Total trades:   {s['total_trades']}")
        print(f"  Win rate:       {s['win_rate']}%")
        print(f"  Total P&L:      ${s['total_pnl']}")
        print(f"  Return:         {s['return_pct']}%")
        print(f"  Max drawdown:   ${s['max_drawdown']}")
        print(f"  Profit factor:  {s['profit_factor']}")
        print(f"  Sharpe ratio:   {s['sharpe_ratio']}")
        print(f"  Avg win:        ${s['avg_win']}")
        print(f"  Avg loss:       ${s['avg_loss']}")
        print(f"  Best trade:     ${s['best_trade']}")
        print(f"  Worst trade:    ${s['worst_trade']}")
        print(f"  Final balance:  ${s['final_balance']}")

    # บันทึกผลลง JSON
    with open("backtest_result.json", "w") as f:
        json.dump(result, f, indent=2)
    print("\nSaved to backtest_result.json")
