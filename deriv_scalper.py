import os
import json
import time
import asyncio
import websockets
import requests
import pandas as pd
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator
from dotenv import load_dotenv
import socket

# Load .env variables
load_dotenv()

DERIV_TOKEN = os.getenv("DERIV_API_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STAKE = 0.35  # Stake per trade
LOSS_LIMIT = 3

loss_count = 0


async def send_telegram(msg):
    """Send a message to your Telegram bot"""
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            response = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
            if not response.ok:
                print(f"[Telegram Error] {response.text}")
        except Exception as e:
            print(f"[Telegram Exception] {e}")
    else:
        print("[Error] Telegram token or chat ID not found in environment variables.")


async def get_candles():
    """Fetch 50 candles (1-min) from Deriv for R_50 index"""
    try:
        async with websockets.connect("wss://ws.deriv.com/websockets/v3?app_id=1089") as ws:
            request = {
                "ticks_history": "R_50",
                "adjust_start_time": 1,
                "count": 50,
                "end": "latest",
                "granularity": 60,
                "style": "candles"
            }
            await ws.send(json.dumps(request))
            response = await ws.recv()
            data = json.loads(response)
            candles = data.get("candles", [])
            df = pd.DataFrame(candles)
            df['close'] = df['close'].astype(float)
            return df
    except Exception as e:
        await send_telegram(f"⚠️ Failed to get candles: {str(e)}")
        return None


def check_signal(df):
    """Check EMA cross + RSI for entry signals"""
    df['ema5'] = EMAIndicator(df['close'], 5).ema_indicator()
    df['ema14'] = EMAIndicator(df['close'], 14).ema_indicator()
    df['rsi'] = RSIIndicator(df['close'], 14).rsi()

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    if prev['ema5'] < prev['ema14'] and latest['ema5'] > latest['ema14'] and latest['rsi'] < 30:
        return "CALL"
    elif prev['ema5'] > prev['ema14'] and latest['ema5'] < latest['ema14'] and latest['rsi'] > 70:
        return "PUT"
    return None


async def place_trade(signal):
    """Send trade request to Deriv API"""
    contract_type = "CALL" if signal == "CALL" else "PUT"
    payload = {
        "buy": 1,
        "price": STAKE,
        "parameters": {
            "amount": STAKE,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": 1,
            "duration_unit": "m",
            "symbol": "R_50"
        },
        "passthrough": {"signal": signal}
    }
    try:
        async with websockets.connect("wss://ws.deriv.com/websockets/v3?app_id=1089") as ws:
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            await ws.recv()
            await ws.send(json.dumps(payload))
            response = await ws.recv()
            return json.loads(response)
    except Exception as e:
        await send_telegram(f"⚠️ Trade execution failed: {str(e)}")
        return None


async def check_contract_result(contract_id):
    """Check the result of the trade"""
    try:
        async with websockets.connect("wss://ws.deriv.com/websockets/v3?app_id=1089") as ws:
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            await ws.recv()
            await ws.send(json.dumps({"contract": contract_id}))
            data = await ws.recv()
            return json.loads(data)
    except Exception as e:
        await send_telegram(f"⚠️ Error checking result: {str(e)}")
        return None


async def run_bot():
    global loss_count
    await send_telegram("🤖 Deriv Scalper Bot Started.")

    while True:
        if loss_count >= LOSS_LIMIT:
            await send_telegram("🚫 Stopped after reaching 3 losses.")
            break

        try:
            df = await get_candles()
            if df is None or df.empty:
                await asyncio.sleep(10)
                continue

            signal = check_signal(df)
            if signal:
                await send_telegram(f"📈 Signal Detected: {signal}")
                result = await place_trade(signal)

                if not result or "error" in result:
                    error_msg = result.get("error", {}).get("message", "Unknown error")
                    await send_telegram(f"❌ Trade Error: {error_msg}")
                    break

                contract_id = result['buy']['contract_id']
                await send_telegram(f"✅ Trade placed with Contract ID: {contract_id}")

                await asyncio.sleep(65)  # Wait for result

                await send_telegram("⏳ Checking trade result...")
                outcome = await check_contract_result(contract_id)

                if outcome:
                    profit = outcome.get('contract', {}).get('profit')
                    if profit and float(profit) > 0:
                        await send_telegram(f"🎯 Win! +${profit}")
                        loss_count = 0
                    else:
                        await send_telegram("🔻 Loss.")
                        loss_count += 1
            else:
                await send_telegram("📉 No valid signal.")

        except Exception as e:
            await send_telegram(f"⚠️ Unexpected Error: {str(e)}")

        await asyncio.sleep(10)


if __name__ == "__main__":
    # Debug DNS on startup
    try:
        print("🔍 Resolving hostnames...")
        print("api.telegram.org →", socket.gethostbyname("api.telegram.org"))
        print("ws.deriv.com →", socket.gethostbyname("ws.deriv.com"))
    except Exception as e:
        print("DNS resolution failed:", e)

    asyncio.run(run_bot())
