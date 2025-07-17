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
STAKE = 0.35
LOSS_LIMIT = 3
APP_ID = 85545  # ✅ Your App ID
loss_count = 0

SYMBOLS = ["R_50", "R_100", "R_25", "R_75", "R_10"]  # ✅ Add more as needed

async def send_telegram(msg):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})

async def get_candles(symbol):
    try:
        async with websockets.connect(f"wss://ws.deriv.com/websockets/v3?app_id={APP_ID}") as ws:
            await ws.send(json.dumps({
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": 50,
                "end": "latest",
                "granularity": 60,
                "style": "candles"
            }))
            response = await ws.recv()
            data = json.loads(response)
            candles = data.get("candles", [])
            if not candles:
                raise ValueError("No candles returned")
            df = pd.DataFrame(candles)
            df['close'] = df['close'].astype(float)
            return df
    except Exception as e:
        await send_telegram(f"⚠️ Failed to get candles for {symbol}: {str(e)}")
        return None

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

async def place_trade(symbol, signal):
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
            "symbol": symbol
        },
        "passthrough": {"signal": signal}
    }

    async with websockets.connect(f"wss://ws.deriv.com/websockets/v3?app_id={APP_ID}") as ws:
        await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        await ws.recv()
        await ws.send(json.dumps(payload))
        result = json.loads(await ws.recv())
        return result

async def check_contract(contract_id):
    async with websockets.connect(f"wss://ws.deriv.com/websockets/v3?app_id={APP_ID}") as ws:
        await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        await ws.recv()
        await ws.send(json.dumps({"contract": contract_id}))
        data = await ws.recv()
        return json.loads(data)

async def run_bot():
    global loss_count
    while True:
        try:
            if loss_count >= LOSS_LIMIT:
                await send_telegram("🚫 Bot paused after reaching loss limit.")
                break

            for symbol in SYMBOLS:
                df = await get_candles(symbol)
                if df is None:
                    continue

                signal = check_signal(df)
                if signal:
                    await send_telegram(f"📊 {symbol} Signal: {signal}. Placing trade...")
                    result = await place_trade(symbol, signal)

                    if "error" in result:
                        await send_telegram(f"❌ Error on {symbol}: {result['error']['message']}")
                        continue

                    contract_id = result['buy']['contract_id']
                    await send_telegram(f"✅ Trade Placed on {symbol}: {signal}, Contract ID: {contract_id}")

                    await asyncio.sleep(65)  # wait for result
                    outcome = await check_contract(contract_id)
                    profit = outcome.get('contract', {}).get('profit')

                    if profit and float(profit) > 0:
                        await send_telegram(f"🎯 Win on {symbol}: +${profit}")
                        loss_count = 0
                    else:
                        await send_telegram(f"🔻 Loss on {symbol}")
                        loss_count += 1
                else:
                    await send_telegram(f"📉 No signal for {symbol}")
                await asyncio.sleep(5)

        except Exception as e:
            await send_telegram(f"⚠️ Bot Error: {str(e)}")
            await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(run_bot())
