"""
Backtest Engine — Multi-Instrument
-------------------------------------
รัน backtest ย้อนหลัง 2 ปีสำหรับทุก instrument
"""

import json
import random
from datetime import datetime, timedelta
from technical_analyst import calculate_rsi, calculate_macd, find_support_resistance
from risk_manager import RISK_RULES, check_rr_ratio, calculate_lot_size
from instruments import INSTRUMENTS, get_instrument

# =============================
# 1. Generate Historical Data
# =============================

# ราคาเริ่มต้นของแต่ละ instrument เมื่อ 2 ปีที่แล้ว
START_PRICES = {
    "XAUUSD": 1900.0,
    "XAGUSD": 23.5,
    "USOIL":  75.0,
    "EURUSD": 1.0820,
    "GBPUSD": 1.2450,
    "USDJPY": 134.0,
    "AUDUSD": 0.6680,
    "USDCAD": 1.3550,
    "USDCHF": 0.9150,
    "EURJPY": 145.0,
}

VOLATILITY = {
    "XAUUSD": 8.0,   "XAGUSD": 0.25,  "USOIL":  1.2,
    "EURUSD": 0.003, "GBPUSD": 0.004, "USDJPY": 0.5,
    "AUDUSD": 0.003, "USDCAD": 0.003, "USDCHF": 0.003,
    "EURJPY": 0.6,
}

TRENDS = {
    "XAUUSD": (0.10, 0.25), "XAGUSD": (0.002, 0.004), "USOIL": (0.05, -0.03),
    "EURUSD": (0.0001, 0.0002), "GBPUSD": (0.0001, 0.0003), "USDJPY": (0.08, -0.05),
    "AUDUSD": (0.0001, 0.0002), "USDCAD": (-0.0001, 0.0001), "USDCHF": (-0.0001, 0.0001),
    "EURJPY": (0.05, 0.08),
}


def generate_history(symbol: str, days: int = 730) -> list:
    random.seed(hash(symbol) % 10000)
    candles = []
    price = START_PRICES.get(symbol, 100.0)
    vol = VOLATILITY.get(symbol, 1.0)
    trend_y1, trend_y2 = TRENDS.get(symbol, (0.01, 0.01))
    date = datetime.now() - timedelta(days=days)

    for day in range(days):
        if date.weekday() >= 5:
            date += timedelta(days=1)
            continue
        trend = trend_y1 if day < 365 else trend_y2
        for hour in [0, 4, 8, 12, 16, 20]:
            change = random.gauss(trend, vol)
            min_price = START_PRICES.get(symbol, 1.0) * 0.5
            max_price = START_PRICES.get(symbol, 1.0) * 2.5
            price = max(min_price, min(max_price, price + change))
            high = price + random.uniform(vol * 0.2, vol * 1.5)
            low = price - random.uniform(vol * 0.2, vol * 1.5)
            candles.append({
                "datetime": date.replace(hour=hour).isoformat(),
                "open": round(price + random.gauss(0, vol * 0.3), 5),
                "high": round(high, 5),
                "low": round(low, 5),
                "close": round(price, 5),
            })
        date += timedelta(days=1)

    return candles


# =============================
# 2. Signal Generator
# =============================

def generate_signal(prices: list, symbol: str) -> dict:
    if len(prices) < 30:
        return {"signal": "NEUTRAL", "confidence": 0}

    cfg = get_instrument(symbol)
    rsi = calculate_rsi(prices)
    macd = calculate_macd(prices)
    levels = find_support_resistance(prices)
    current = prices[-1]
    sl_buf = cfg["sl_buffer_pct"]

    score = 0
    if rsi < 35: score += 2
    elif rsi < 45: score += 1
    elif rsi > 65: score -= 2
    elif rsi > 55: score -= 1

    if macd["crossover"] == "bullish_crossover": score += 2
    elif macd["crossover"] == "bearish_crossover": score -= 2

    support = levels.get("nearest_support")
    resistance = levels.get("nearest_resistance")
    if support and abs(current - support) / current < 0.005: score += 1
    if resistance and abs(current - resistance) / current < 0.005: score -= 1

    if score >= 3:
        signal = "BUY"
        confidence = min(90, 55 + score * 8)
        sl = round(current * (1 - sl_buf), 5)
        tp = round(current * (1 + sl_buf * 2), 5)
    elif score <= -3:
        signal = "SELL"
        confidence = min(90, 55 + abs(score) * 8)
        sl = round(current * (1 + sl_buf), 5)
        tp = round(current * (1 - sl_buf * 2), 5)
    else:
        return {"signal": "NEUTRAL", "confidence": 40, "stop_loss": 0, "take_profit": 0, "current_price": current, "rsi": round(rsi, 1)}

    return {
        "signal": signal, "confidence": confidence,
        "stop_loss": sl, "take_profit": tp,
        "current_price": current, "rsi": round(rsi, 1),
        "macd_crossover": macd["crossover"],
    }


# =============================
# 3. Simulate Outcome
# =============================

def simulate_outcome(signal, entry, sl, tp, future_prices) -> dict:
    if not future_prices:
        return {"result": "TIMEOUT", "pnl_pips": 0}
    for price in future_prices[:100]:
        if signal == "BUY":
            if price <= sl: return {"result": "LOSS", "pnl_pips": round(sl - entry, 5)}
            if price >= tp: return {"result": "WIN", "pnl_pips": round(tp - entry, 5)}
        elif signal == "SELL":
            if price >= sl: return {"result": "LOSS", "pnl_pips": round(entry - sl, 5) * -1}
            if price <= tp: return {"result": "WIN", "pnl_pips": round(entry - tp, 5)}
    last = future_prices[-1]
    pnl = last - entry if signal == "BUY" else entry - last
    return {"result": "TIMEOUT", "pnl_pips": round(pnl, 5)}


# =============================
# 4. Run Backtest
# =============================

def run_backtest(symbol: str, days: int = 730, account_balance: float = 1000.0) -> dict:
    candles = generate_history(symbol, days)
    prices_all = [c["close"] for c in candles]
    lookback = 60
    step = 6

    trades = []
    balance = account_balance
    equity_curve = [{"date": candles[0]["datetime"][:10], "balance": balance}]

    for i in range(lookback, len(candles) - 100, step):
        prices = prices_all[i - lookback:i]
        sig = generate_signal(prices, symbol)

        if sig["signal"] == "NEUTRAL": continue
        if sig["confidence"] < RISK_RULES["min_confidence"]: continue

        rr = check_rr_ratio(sig["current_price"], sig["stop_loss"], sig["take_profit"])
        if rr < RISK_RULES["min_rr_ratio"]: continue

        pos = calculate_lot_size(balance, RISK_RULES["max_risk_per_trade_pct"], sig["current_price"], sig["stop_loss"], symbol)
        risk_amt = pos["risk_amount_usd"]

        outcome = simulate_outcome(sig["signal"], sig["current_price"], sig["stop_loss"], sig["take_profit"], prices_all[i:i+100])

        if outcome["result"] == "WIN": pnl = risk_amt * rr
        elif outcome["result"] == "LOSS": pnl = -risk_amt
        else:
            sl_dist = abs(sig["current_price"] - sig["stop_loss"])
            pnl = risk_amt * outcome["pnl_pips"] / sl_dist if sl_dist > 0 else 0

        pnl = round(pnl, 2)
        balance = round(balance + pnl, 2)

        trades.append({
            "date": candles[i]["datetime"][:10],
            "signal": sig["signal"],
            "confidence": sig["confidence"],
            "entry": sig["current_price"],
            "sl": sig["stop_loss"],
            "tp": sig["take_profit"],
            "rr": rr,
            "result": outcome["result"],
            "pnl": pnl,
            "balance": balance,
            "rsi": sig.get("rsi", 0),
        })
        equity_curve.append({"date": candles[i]["datetime"][:10], "balance": balance})

        if balance <= 0: break

    if not trades:
        return {"symbol": symbol, "error": "No trades generated"}

    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    total = len(trades)
    win_rate = round(len(wins) / total * 100, 1)
    total_pnl = round(balance - account_balance, 2)
    max_bal = max(t["balance"] for t in trades)
    min_bal = min(t["balance"] for t in trades if t["balance"] <= max_bal)
    max_dd = round(max_bal - min_bal, 2)
    avg_win = round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0
    pf_denom = abs(sum(t["pnl"] for t in losses))
    profit_factor = round(sum(t["pnl"] for t in wins) / pf_denom, 2) if pf_denom > 0 else 0

    returns = [t["pnl"] / account_balance for t in trades]
    avg_r = sum(returns) / len(returns) if returns else 0
    std_r = (sum((r - avg_r) ** 2 for r in returns) / len(returns)) ** 0.5 if returns else 1
    sharpe = round((avg_r / std_r) * (252 ** 0.5), 2) if std_r > 0 else 0

    monthly = {}
    for t in trades:
        m = t["date"][:7]
        if m not in monthly:
            monthly[m] = {"wins": 0, "losses": 0, "pnl": 0}
        if t["result"] == "WIN": monthly[m]["wins"] += 1
        elif t["result"] == "LOSS": monthly[m]["losses"] += 1
        monthly[m]["pnl"] = round(monthly[m]["pnl"] + t["pnl"], 2)

    return {
        "symbol": symbol,
        "summary": {
            "total_trades": total,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "final_balance": balance,
            "max_drawdown": max_dd,
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
        "monthly": monthly,
    }


def run_all_backtests(account_balance: float = 1000.0) -> dict:
    symbols = list(INSTRUMENTS.keys())
    all_results = {}
    print(f"Running backtest for {len(symbols)} instruments...\n")
    for symbol in symbols:
        print(f"  {symbol}...", end=" ", flush=True)
        result = run_backtest(symbol, account_balance=account_balance)
        all_results[symbol] = result
        if "error" not in result:
            s = result["summary"]
            print(f"win={s['win_rate']}% pnl=${s['total_pnl']} sharpe={s['sharpe_ratio']}")
        else:
            print(f"ERROR: {result['error']}")

    with open("backtest_all.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print("\nSaved to backtest_all.json")
    return all_results


if __name__ == "__main__":
    results = run_all_backtests(account_balance=1000.0)

    print(f"\n{'='*60}")
    print(f"{'INSTRUMENT':<12} {'WIN%':>6} {'P&L':>8} {'RETURN':>8} {'DRAWDOWN':>10} {'SHARPE':>8}")
    print(f"{'='*60}")
    for sym, r in results.items():
        if "error" in r:
            print(f"{sym:<12} ERROR")
            continue
        s = r["summary"]
        profit_marker = "✓" if s["total_pnl"] > 0 else "✗"
        print(f"{sym:<12} {s['win_rate']:>5}% ${s['total_pnl']:>7} {s['return_pct']:>7}% ${s['max_drawdown']:>8} {s['sharpe_ratio']:>8}")
    print(f"{'='*60}")
