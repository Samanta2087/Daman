import time
import requests
import urllib3
import os
import sqlite3
import json
import random
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sklearn.ensemble import GradientBoostingClassifier
from telethon import TelegramClient, events, Button
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ================= CONFIGURATION =================

# 1. TELETHON SETUP
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')

# 2. BOT SETUP
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))

# 3. CHANNEL SETUP
CHANNELS = {
    "MAIN CHANNEL": os.getenv('MAIN_CHANNEL', '@your_main_channel'),
    "VIP CHANNEL": os.getenv('VIP_CHANNEL', '@your_vip_channel'),
    "TEST CHANNEL": os.getenv('TEST_CHANNEL', '@your_test_channel')
}
SESSION_NAME = 'wingo_aggressive_bot'

# 4. STICKER SETUP
WIN_STICKER_FILE = "win.webp" 

# ================= GAME CONFIG =================
API_PATH = "/WinGo/WinGo_1M/GetHistoryIssuePage.json"
DOMAINS = [
    "https://draw.ar-lottery01.com",
    "https://draw.ar-lottery02.com",
    "https://draw.ar-lottery03.com"
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://damangames.run/",
    "Origin": "https://damangames.run"
}
PARAMS = {"no": 1, "size": 10, "language": "en"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DB_FILE = "wingo_history.db"
ACCURACY_FILE = "real_accuracy.json"

# ================= GLOBAL STATE =================
system_state = {
    "mode": "manual_off", 
    "start_time": None,
    "end_time": None,
    "waiting_for_input": False,
    "waiting_for_name": False,
    "game_name": "BDG",
    "active_channel_name": list(CHANNELS.keys())[0],
    "active_channel_link": list(CHANNELS.values())[0],
    "last_channel_bet": None
}

# ================= HELPER FUNCTIONS =================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def get_ist_time():
    utc_now = datetime.utcnow()
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    return ist_now

def check_posting_status():
    mode = system_state["mode"]
    if mode == "manual_on": return True, "🟢 FORCE ON"
    elif mode == "manual_off": return False, "🔴 FORCE OFF"
    elif mode == "auto_time":
        if not system_state["start_time"] or not system_state["end_time"]:
            return False, "⚠️ TIME NOT SET"
        now = get_ist_time()
        current_hm = now.strftime("%H:%M")
        if system_state["start_time"] <= current_hm <= system_state["end_time"]:
            return True, f"⏰ AUTO ON ({system_state['start_time']}-{system_state['end_time']})"
        else:
            return False, f"⏳ AUTO OFF (Wait: {system_state['start_time']})"
    return False, "UNKNOWN"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS wingo_history (
            period TEXT PRIMARY KEY,
            number INTEGER,
            size TEXT,
            color TEXT,
            time TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_to_db(data_list):
    if not data_list: return
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        for data in data_list:
            cursor.execute('''
                INSERT OR REPLACE INTO wingo_history (period, number, size, color, time)
                VALUES (?, ?, ?, ?, ?)
            ''', (data['period'], data['number'], data['size'], data['color'], data['time']))
        
        # Keep only last 2000 records
        cursor.execute('SELECT COUNT(*) FROM wingo_history')
        count = cursor.fetchone()[0]
        if count > 2000:
            cursor.execute('''
                DELETE FROM wingo_history WHERE period IN (
                    SELECT period FROM wingo_history ORDER BY period ASC LIMIT ?
                )
            ''', (count - 2000,))
        
        conn.commit()
        conn.close()
    except: pass

def read_from_db():
    """Read all data from database as DataFrame"""
    try:
        conn = sqlite3.connect(DB_FILE)
        df = pd.read_sql_query('SELECT * FROM wingo_history ORDER BY period', conn)
        conn.close()
        return df
    except:
        return pd.DataFrame(columns=['period', 'number', 'size', 'color', 'time'])

def get_color(n):
    if n in (0, 5): return "🟣 Violet"
    if n in (1, 3, 7, 9): return "🟢 Green"
    return "🔴 Red"

def load_accuracy():
    if os.path.exists(ACCURACY_FILE):
        try:
            with open(ACCURACY_FILE, "r") as f: return json.load(f)
        except: pass
    return {"total_bets": 0, "wins": 0, "last_10_results": []}

def save_accuracy(data):
    try:
        with open(ACCURACY_FILE, "w") as f: json.dump(data, f, indent=4)
    except: pass

def update_accuracy(real_result, predicted_result, acc_data):
    if not predicted_result or predicted_result == "Waiting...": return acc_data
    is_win = (real_result == predicted_result)
    acc_data["total_bets"] += 1
    acc_data["wins"] += 1 if is_win else 0
    acc_data["last_10_results"].append("✅" if is_win else "❌")
    if len(acc_data["last_10_results"]) > 10: acc_data["last_10_results"].pop(0)
    save_accuracy(acc_data)
    return acc_data

# ================= WARM UP =================
def warm_up_system():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔥 Warming up... Downloading 1000+ past results...")
    collected_data = []
    for page in range(1, 101): 
        success = False
        for domain in DOMAINS:
            try:
                p = PARAMS.copy()
                p['no'] = page
                p['ts'] = str(int(time.time() * 1000))
                r = requests.get(domain + API_PATH, params=p, headers=HEADERS, timeout=3, verify=False)
                if r.status_code == 200:
                    data = r.json()
                    if "data" in data and "list" in data["data"]:
                        for item in data["data"]["list"]:
                            period = str(item["issueNumber"])
                            number = int(item["number"])
                            size = 'Big' if number >= 5 else 'Small'
                            color = get_color(number)
                            collected_data.append({
                                "period": period, "number": number, "size": size, 
                                "color": color, "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            })
                        success = True
                        break
            except: continue
        if not success: break
        if page % 20 == 0: print(f"[{datetime.now().strftime('%H:%M:%S')}] 📥 Downloaded {page * 10} records...")

    if collected_data:
        collected_data.reverse()
        save_to_db(collected_data)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Brain Loaded! Total Database: {len(collected_data)}")

# ================= 🧠 AGGRESSIVE INTELLIGENCE =================

def logic_historical_probability(df):
    """
    Relaxed Constraints: Returns prediction even if confidence is > 50%
    """
    history_sizes = df['size'].tolist()
    if len(history_sizes) < 10: return None, 0
    
    current_pattern = history_sizes[-3:] # Reduced pattern size to 3 for more matches
    
    big_next_count = 0
    small_next_count = 0
    
    search_start_index = max(0, len(history_sizes) - 500)
    
    for i in range(search_start_index, len(history_sizes) - 4):
        window = history_sizes[i : i+3]
        if window == current_pattern:
            next_val = history_sizes[i+3]
            if next_val == 'Big': big_next_count += 1
            elif next_val == 'Small': small_next_count += 1
            
    total = big_next_count + small_next_count
    if total > 0:
        big_prob = (big_next_count / total) * 100
        small_prob = (small_next_count / total) * 100
        
        # Aggressive: 50% এর বেশি হলেই প্রেডিকশন দাও
        if big_prob > small_prob: return "Big", big_prob
        elif small_prob > big_prob: return "Small", small_prob
        
    return None, 0

def logic_streak_master(df):
    history_sizes = df['size'].tolist()
    if len(history_sizes) < 5: return None, 0
    
    # Common Patterns
    last_3 = history_sizes[-3:]
    last_2 = history_sizes[-2:]
    
    # 3 Streak
    if last_3 == ['Big', 'Big', 'Big']: return "Big", 85.0
    if last_3 == ['Small', 'Small', 'Small']: return "Small", 85.0
    
    # 2-2 Pattern
    last_4 = history_sizes[-4:]
    if last_4 == ['Big', 'Big', 'Small', 'Small']: return "Big", 75.0
    if last_4 == ['Small', 'Small', 'Big', 'Big']: return "Small", 75.0
    
    # ZigZag (B S B -> S)
    if last_3 == ['Big', 'Small', 'Big']: return "Small", 70.0
    if last_3 == ['Small', 'Big', 'Small']: return "Big", 70.0

    return None, 0

def train_and_predict_gbm():
    try:
        df = read_from_db()
        if len(df) == 0: return None, 0
        if len(df) < 20: return None, 0 # Reduced requirement
        
        df['target'] = df['size'].apply(lambda x: 1 if x == 'Big' else 0)
        # Reduced Lags for faster adaptation
        for i in range(1, 4): df[f'lag_{i}'] = df['target'].shift(i)
        
        df = df.dropna()
        X = df[[f'lag_{i}' for i in range(1,4)]]
        y = df['target']
        
        # Simplified Model for Aggression
        model = GradientBoostingClassifier(n_estimators=50, learning_rate=0.2, max_depth=2, random_state=42)
        model.fit(X, y)
        
        last_row = df.iloc[-1]
        input_data = {f'lag_{i}': last_row[f'lag_{i-1}'] if i>1 else last_row['target'] for i in range(1,4)}
        current_input = pd.DataFrame([input_data])
        
        predicted_class = model.predict(current_input)[0]
        confidence = max(model.predict_proba(current_input)[0]) * 100
        
        return ("Big" if predicted_class == 1 else "Small"), round(confidence, 2)
    except: return None, 0

# ================= TELETHON CLIENTS =================

bot = TelegramClient('bot_control_agg', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
userbot = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ================= CONTROL PANEL =================
# ... (Same Panel Logic) ...
async def get_panel_message():
    _, status_msg = check_posting_status()
    ist_time = get_ist_time().strftime('%H:%M:%S')
    msg = (
        f"🎛 <b>AGGRESSIVE AI PANEL</b>\n\n"
        f"📢 <b>Target:</b> {system_state['active_channel_name']}\n"
        f"🎮 <b>Game:</b> {system_state['game_name']}\n"
        f"📡 <b>Status:</b> {status_msg}\n"
        f"🕒 <b>Time:</b> <code>{ist_time}</code>"
    )
    return msg

@bot.on(events.NewMessage(pattern='/control'))
async def send_control_panel(event):
    if event.sender_id != ADMIN_ID: return
    msg = await get_panel_message()
    keyboards = [
        [Button.inline("🟢 FORCE START", b'force_start'), Button.inline("🔴 FORCE STOP", b'force_stop')],
        [Button.inline("⏰ AUTO SCHEDULE", b'auto_mode'), Button.inline("📢 SELECT CHANNEL", b'select_channel')],
        [Button.inline("🎮 CHANGE GAME NAME", b'change_game'), Button.inline("✏️ SET TIME", b'set_time')]
    ]
    await event.respond(msg, buttons=keyboards, parse_mode='html')

# ... (Callback Handlers and Input Handlers Same as before) ...
@bot.on(events.CallbackQuery)
async def handler(event):
    if event.sender_id != ADMIN_ID: return
    data = event.data
    
    if data == b'force_start':
        system_state["mode"] = "manual_on"
        await event.answer("🟢 Force Started!", alert=True)

    elif data == b'force_stop':
        system_state["mode"] = "manual_off"
        await event.answer("🔴 Force Stopped!", alert=True)

    elif data == b'auto_mode':
        if not system_state["start_time"]: await event.answer("⚠️ Set Time First!", alert=True)
        else:
            system_state["mode"] = "auto_time"
            await event.answer("⏰ Auto Mode ON", alert=True)

    elif data == b'select_channel':
        buttons = []
        for name in CHANNELS.keys():
            buttons.append([Button.inline(f"📡 {name}", data=f"ch_{name}".encode())])
        buttons.append([Button.inline("🔙 BACK", b'back_main')])
        await event.edit("📢 <b>Select Target Channel:</b>", buttons=buttons, parse_mode='html')
        return

    elif data.startswith(b'ch_'):
        selected_name = data.decode().replace("ch_", "")
        if selected_name in CHANNELS:
            system_state["active_channel_name"] = selected_name
            system_state["active_channel_link"] = CHANNELS[selected_name]
            await event.answer(f"✅ Selected: {selected_name}", alert=True)
            msg = await get_panel_message()
            keyboards = [
                [Button.inline("🟢 FORCE START", b'force_start'), Button.inline("🔴 FORCE STOP", b'force_stop')],
                [Button.inline("⏰ AUTO SCHEDULE", b'auto_mode'), Button.inline("📢 SELECT CHANNEL", b'select_channel')],
                [Button.inline("🎮 CHANGE GAME NAME", b'change_game'), Button.inline("✏️ SET TIME", b'set_time')]
            ]
            await event.edit(msg, buttons=keyboards, parse_mode='html')
            return

    elif data == b'change_game':
        system_state["waiting_for_name"] = True
        await event.respond("🎮 Enter Game Name:", parse_mode='html')
        return

    elif data == b'set_time':
        system_state["waiting_for_input"] = True
        await event.respond("✏️ Enter Time (e.g. 19:00-19:20)", parse_mode='html')
        return

    elif data == b'back_main': pass

    msg = await get_panel_message()
    keyboards = [
        [Button.inline("🟢 FORCE START", b'force_start'), Button.inline("🔴 FORCE STOP", b'force_stop')],
        [Button.inline("⏰ AUTO SCHEDULE", b'auto_mode'), Button.inline("📢 SELECT CHANNEL", b'select_channel')],
        [Button.inline("🎮 CHANGE GAME NAME", b'change_game'), Button.inline("✏️ SET TIME", b'set_time')]
    ]
    await event.edit(msg, buttons=keyboards, parse_mode='html')

@bot.on(events.NewMessage)
async def input_handler(event):
    if event.sender_id != ADMIN_ID: return
    text = event.text.strip()
    
    if system_state["waiting_for_name"]:
        system_state["game_name"] = text.upper()
        system_state["waiting_for_name"] = False
        await event.reply(f"✅ Game Name: <b>{text.upper()}</b>", parse_mode='html')
        return

    if system_state["waiting_for_input"]:
        try:
            start, end = text.split('-')
            datetime.strptime(start.strip(), "%H:%M")
            datetime.strptime(end.strip(), "%H:%M")
            system_state["start_time"] = start.strip()
            system_state["end_time"] = end.strip()
            system_state["mode"] = "auto_time"
            system_state["waiting_for_input"] = False
            await event.reply(f"✅ Time Set: {start}-{end}")
        except:
            await event.reply("⚠️ Invalid Format! Use HH:MM-HH:MM")

# ================= GAME LOOP =================

async def game_loop():
    log("🚀 Aggressive Bot Started (No Waiting)...")
    init_db()
    warm_up_system()
    
    last_period = None
    last_prediction = None
    acc_data = load_accuracy()

    while True:
        try:
            data = None
            for domain in DOMAINS:
                try:
                    p = PARAMS.copy()
                    p['ts'] = str(int(time.time() * 1000))
                    r = requests.get(domain + API_PATH, params=p, headers=HEADERS, timeout=5, verify=False)
                    if r.status_code == 200:
                        temp = r.json()
                        if "data" in temp and "list" in temp["data"]:
                            data = temp
                            break
                except: continue
            
            if not data:
                await asyncio.sleep(2)
                continue

            latest = data["data"]["list"][0]
            period = str(latest["issueNumber"])
            number = int(latest["number"])

            if period != last_period:
                size = 'Big' if number >= 5 else 'Small'
                color = get_color(number)

                if last_prediction and last_period:
                    acc_data = update_accuracy(size, last_prediction, acc_data)
                
                save_to_db([{
                    "period": period, "number": number, "size": size, 
                    "color": color, "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }])

                df = read_from_db()
                
                # --- INTELLIGENCE ---
                hist_pred, hist_conf = logic_historical_probability(df)
                streak_pred, streak_conf = logic_streak_master(df)
                ml_pred, ml_conf = train_and_predict_gbm()

                final_pred = "Waiting..."
                final_logic = "Insufficient Data"
                final_conf = 0

                # ⚠️ AGGRESSIVE DECISION MAKING
                
                # 1. High Confidence Check
                if streak_pred: 
                    final_pred, final_conf, final_logic = streak_pred, streak_conf, "🐉 Dragon Streak"
                elif hist_pred and hist_conf > 55: # Lowered threshold
                    final_pred, final_conf, final_logic = hist_pred, hist_conf, "📜 History Pattern"
                elif ml_pred:
                    final_pred, final_conf, final_logic = ml_pred, ml_conf, "🤖 AI Prediction"
                
                # 2. LAST RESORT (If all above fail or are waiting)
                if final_pred == "Waiting...":
                    # Simple Trend Follow: Last was Big -> Predict Big
                    final_pred = size 
                    final_conf = 51.0
                    final_logic = "⚠️ Blind Trend Follow"

                if final_conf > 99: final_conf = 99
                
                last_prediction = final_pred
                real_win_rate = 0
                if acc_data["total_bets"] > 0:
                    real_win_rate = round((acc_data["wins"] / acc_data["total_bets"]) * 100, 1)

                should_post, status_msg = check_posting_status()
                target_channel = system_state["active_channel_link"]

                # Admin Log
                try:
                    await bot.send_message(ADMIN_ID, f"🎰 {system_state['game_name']} | {status_msg}\n🔢 {period[-3:]} | {number} ({size})\n🤖 Pred: <b>{final_pred}</b> ({round(final_conf)}%)\n🧠 {final_logic}", parse_mode='html')
                except: pass

                if should_post:
                    # Win Logic
                    last_bet = system_state["last_channel_bet"]
                    if last_bet and last_bet["period"] == period:
                        if last_bet["pick"] == size:
                            if os.path.exists(WIN_STICKER_FILE):
                                try: await userbot.send_file(target_channel, WIN_STICKER_FILE)
                                except: pass
                            else:
                                win_msg = (
                                    f"✅ <b>{system_state['game_name']} WIN</b>\n\n"
                                    f"💰 <b>PERIOD NO. - {period[-3:]}</b>\n"
                                    f"💰 <b>RESULT - {size.upper()}</b>\n"
                                    f"🔥 <b>WINNER WINNER!</b> 🏆"
                                )
                                try: await userbot.send_message(target_channel, win_msg, parse_mode='html')
                                except: pass
                    
                    # Next Prediction
                    next_p = str(int(period)+1)
                    msg_channel = (
                        f"✅ <b>{system_state['game_name']}</b> - ( WINGO 1MIN )\n\n"
                        f"💰 <b>PERIOD NO. - {next_p[-3:]}</b>\n\n"
                        f"💰 <b>BET - {final_pred.upper()}</b>"
                    )
                    try:
                        await userbot.send_message(target_channel, msg_channel, parse_mode='html')
                        log(f"🚀 Sent to Channel: {final_pred}")
                        system_state["last_channel_bet"] = {"period": next_p, "pick": final_pred}
                    except Exception as e: log(f"⚠️ Channel Error: {e}")
                else:
                    system_state["last_channel_bet"] = None

                last_period = period

            await asyncio.sleep(5)

        except Exception as e:
            await asyncio.sleep(5)

if __name__ == '__main__':
    with userbot:
        userbot.loop.run_until_complete(game_loop())