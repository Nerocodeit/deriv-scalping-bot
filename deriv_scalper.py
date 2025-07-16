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

# Load env variables
load_dotenv()

DERIV_TOKEN = os.getenv("DERIV_API_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STAKE = 0.35  # $ per trade
LOSS_LIMIT = 3  # Stop after 3 losses

loss_count = 0

async def send_telegram(msg):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

async def get_candles():
    async with websockets.connect("wss://ws.deriv.com/websockets/v3?app_id=1089") as ws:
        # Request candles
        req = {
            "ticks_history": "R_50",
            "adjust_start_time": 1,
            "count": 50,
            "end": "latest",
            "granularity": 60,
            "style": "candles"
        }
        await ws.send(json.dumps(req))
        res = await ws.recv()
        data = json.loads(res)
        candles = data.get("candles", [])
        df = pd.DataFrame(candles)
        df['close'] = df['close'].astype(float)
        return df

def check_signal(df):
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
    async with websockets.connect("wss://ws.deriv.com/websockets/v3?app_id=1089") as ws:
        await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        await ws.recv()
        await ws.send(json.dumps(payload))
        res = await ws.recv()
        return json.loads(res)

async def run_bot():
    global loss_count
    while True:
        try:
            if loss_count >= LOSS_LIMIT:
                await send_telegram("🚫 Bot paused after 3 losses.")
                break

            df = await get_candles()
            signal = check_signal(df)
            if signal:
                await send_telegram(f"📊 Signal found: {signal}. Placing trade...")
                result = await place_trade(signal)

                if "error" in result:
                    await send_telegram(f"❌ Trade Error: {result['error']['message']}")
                    break
                else:
                    contract_id = result['buy']['contract_id']
                    await send_telegram(f"✅ Trade Placed: {signal}\nContract ID: {contract_id}")
                    await asyncio.sleep(65)  # Wait for result

                    # Check result
                    await send_telegram("⏳ Checking result...")
                    async with websockets.connect("wss://ws.deriv.com/websockets/v3?app_id=1089") as ws:
                        await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
                        await ws.recv()
                        await ws.send(json.dumps({"contract": contract_id}))
                        outcome_data = json.loads(await ws.recv())

                        profit = outcome_data.get('contract', {}).get('profit')
                        if profit and float(profit) > 0:
                            await send_telegram(f"🎯 Win: +${profit}")
                            loss_count = 0
                        else:
                            await send_telegram("🔻 Loss.")
                            loss_count += 1
            else:
                await send_telegram("📉 No valid signal at the moment.")
            await asyncio.sleep(10)
        except Exception as e:
            await send_telegram(f"⚠️ Error: {str(e)}")
            await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(run_bot())
