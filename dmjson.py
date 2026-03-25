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

# 4. STICKER SETUP (3 win images for random selection)
WIN_STICKERS = ["win1.webp", "win2.webp", "win3.webp"]
PREDICTION_END_IMAGE = "Predaction End.webp" 

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
SCHEDULE_FILE = "daily_schedule.json"
ANNOUNCEMENT_FILE = "daily_announcements.json"

# ================= GLOBAL STATE =================
system_state = {
    "mode": "manual_off", 
    "start_time": None,
    "end_time": None,
    "waiting_for_input": False,
    "waiting_for_name": False,
    "waiting_for_manual_schedule": False,
    "waiting_for_announcement": False,
    "game_name": "BDG",
    "active_channel_name": list(CHANNELS.keys())[0],
    "active_channel_link": list(CHANNELS.values())[0],
    "last_channel_bet": None,
    "consecutive_losses": 0,
    "stopped_by_losses": False,
    "daily_schedules": [],
    "daily_announcements": []
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

def load_daily_schedules():
    """Load daily schedules from JSON file"""
    if os.path.exists(SCHEDULE_FILE):
        try:
            with open(SCHEDULE_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return []

def save_daily_schedules(schedules):
    """Save daily schedules to JSON file"""
    try:
        with open(SCHEDULE_FILE, "w") as f:
            json.dump(schedules, f, indent=4)
    except:
        pass

def load_daily_announcements():
    """Load daily announcements from JSON file"""
    if os.path.exists(ANNOUNCEMENT_FILE):
        try:
            with open(ANNOUNCEMENT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return []

def save_daily_announcements(announcements):
    """Save daily announcements to JSON file"""
    try:
        with open(ANNOUNCEMENT_FILE, "w", encoding="utf-8") as f:
            json.dump(announcements, f, indent=4, ensure_ascii=False)
    except:
        pass

def check_daily_schedules():
    """Check if current time matches any daily schedule"""
    now = get_ist_time()
    current_time = now.strftime("%H:%M")
    
    # Check for start time activation
    for schedule in system_state["daily_schedules"]:
        if schedule["time"] == current_time:
            return "start", schedule
    
    # Check for end time deactivation
    for schedule in system_state["daily_schedules"]:
        if "end_time" in schedule and schedule["end_time"] == current_time:
            return "end", schedule
    
    return None, None

def check_daily_announcements():
    """Check if current time matches any announcement schedule"""
    now = get_ist_time()
    current_time = now.strftime("%H:%M")
    
    announcements_to_send = []
    for announcement in system_state["daily_announcements"]:
        if announcement["time"] == current_time:
            announcements_to_send.append(announcement)
    
    return announcements_to_send

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

# ================= 🎯 SIMPLE TREND FOLLOWING =================

def simple_trend_follow(last_size):
    """
    Simple Logic: If last was Big -> predict Big, if Small -> predict Small
    """
    return last_size, 75.0

# ================= TELETHON CLIENTS =================

bot = TelegramClient('bot_control_agg', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
userbot = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ================= CONTROL PANEL =================
# ... (Same Panel Logic) ...
async def get_panel_message():
    _, status_msg = check_posting_status()
    ist_time = get_ist_time().strftime('%H:%M:%S')
    schedule_count = len(system_state["daily_schedules"])
    announcement_count = len(system_state["daily_announcements"])
    msg = (
        f"🎛 <b>AGGRESSIVE AI PANEL</b>\n\n"
        f"📢 <b>Target:</b> {system_state['active_channel_name']}\n"
        f"🎮 <b>Game:</b> {system_state['game_name']}\n"
        f"📡 <b>Status:</b> {status_msg}\n"
        f"📅 <b>Daily Schedules:</b> {schedule_count}\n"
        f"📣 <b>Announcements:</b> {announcement_count}\n"
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
        [Button.inline("🎮 CHANGE GAME NAME", b'change_game'), Button.inline("✏️ SET TIME", b'set_time')],
        [Button.inline("🔧 SOLVE PROBLEM", b'solve_problem'), Button.inline("📅 VIEW SCHEDULES", b'view_schedules')],
        [Button.inline("📣 ANNOUNCEMENT", b'announcement'), Button.inline("👁️ VIEW ANNOUNCEMENTS", b'view_announcements')]
    ]
    await event.respond(msg, buttons=keyboards, parse_mode='html')

# ... (Callback Handlers and Input Handlers Same as before) ...
@bot.on(events.CallbackQuery)
async def handler(event):
    if event.sender_id != ADMIN_ID: return
    data = event.data
    
    if data == b'force_start':
        system_state["mode"] = "manual_on"
        system_state["stopped_by_losses"] = False
        system_state["consecutive_losses"] = 0
        await event.answer("🟢 Force Started!", alert=True)

    elif data == b'force_stop':
        system_state["mode"] = "manual_off"
        await event.answer("🔴 Force Stopped!", alert=True)

    elif data == b'auto_mode':
        if not system_state["start_time"]: await event.answer("⚠️ Set Time First!", alert=True)
        else:
            system_state["mode"] = "auto_time"
            system_state["stopped_by_losses"] = False
            system_state["consecutive_losses"] = 0
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

    elif data == b'solve_problem':
        system_state["waiting_for_manual_schedule"] = True
        await event.respond(
            "🔧 <b>SOLVE PROBLEM - Set Daily Schedule</b>\n\n"
            "Enter in format:\n"
            "<code>START|END|GAME</code>\n\n"
            "Examples:\n"
            "<code>19:30|19:50|BDG</code>\n"
            "<code>20:15|20:45|DAMAN</code>\n\n"
            "Or without end time:\n"
            "<code>19:30|BDG</code>\n\n"
            "This will run automatically every day!",
            parse_mode='html'
        )
        return

    elif data == b'announcement':
        system_state["waiting_for_announcement"] = True
        await event.respond(
            "📣 <b>DAILY ANNOUNCEMENT</b>\n\n"
            "Enter in format:\n"
            "<code>TIME|MESSAGE</code>\n\n"
            "Examples:\n"
            "<code>19:00|🎮 Game starting in 30 minutes!</code>\n"
            "<code>20:00|💰 Big win incoming! Join now!</code>\n\n"
            "This will be sent automatically every day!",
            parse_mode='html'
        )
        return

    elif data == b'view_announcements':
        if not system_state["daily_announcements"]:
            await event.answer("📣 No announcements set yet!", alert=True)
            return
        
        ann_msg = "📣 <b>DAILY ANNOUNCEMENTS</b>\n\n"
        for idx, ann in enumerate(system_state["daily_announcements"], 1):
            preview = ann['message'][:50] + "..." if len(ann['message']) > 50 else ann['message']
            ann_msg += f"{idx}. ⏰ <code>{ann['time']}</code>\n   📝 {preview}\n\n"
        
        buttons = []
        for idx, ann in enumerate(system_state["daily_announcements"]):
            buttons.append([Button.inline(f"❌ Delete #{idx+1}", data=f"del_ann_{idx}".encode())])
        buttons.append([Button.inline("🔙 BACK", b'back_main')])
        
        await event.edit(ann_msg, buttons=buttons, parse_mode='html')
        return

    elif data.startswith(b'del_ann_'):
        try:
            idx = int(data.decode().replace("del_ann_", ""))
            if 0 <= idx < len(system_state["daily_announcements"]):
                deleted = system_state["daily_announcements"].pop(idx)
                save_daily_announcements(system_state["daily_announcements"])
                await event.answer(f"✅ Deleted announcement at {deleted['time']}", alert=True)
        except:
            await event.answer("❌ Error deleting announcement", alert=True)
        
        # Refresh announcements view
        if system_state["daily_announcements"]:
            ann_msg = "📣 <b>DAILY ANNOUNCEMENTS</b>\n\n"
            for idx, ann in enumerate(system_state["daily_announcements"], 1):
                preview = ann['message'][:50] + "..." if len(ann['message']) > 50 else ann['message']
                ann_msg += f"{idx}. ⏰ <code>{ann['time']}</code>\n   📝 {preview}\n\n"
            
            buttons = []
            for idx, ann in enumerate(system_state["daily_announcements"]):
                buttons.append([Button.inline(f"❌ Delete #{idx+1}", data=f"del_ann_{idx}".encode())])
            buttons.append([Button.inline("🔙 BACK", b'back_main')])
            
            await event.edit(ann_msg, buttons=buttons, parse_mode='html')
        else:
            msg = await get_panel_message()
            keyboards = [
                [Button.inline("🟢 FORCE START", b'force_start'), Button.inline("🔴 FORCE STOP", b'force_stop')],
                [Button.inline("⏰ AUTO SCHEDULE", b'auto_mode'), Button.inline("📢 SELECT CHANNEL", b'select_channel')],
                [Button.inline("🎮 CHANGE GAME NAME", b'change_game'), Button.inline("✏️ SET TIME", b'set_time')],
                [Button.inline("🔧 SOLVE PROBLEM", b'solve_problem'), Button.inline("📅 VIEW SCHEDULES", b'view_schedules')],
                [Button.inline("📣 ANNOUNCEMENT", b'announcement'), Button.inline("👁️ VIEW ANNOUNCEMENTS", b'view_announcements')]
            ]
            await event.edit(msg, buttons=keyboards, parse_mode='html')
        return

    elif data == b'view_schedules':
        if not system_state["daily_schedules"]:
            await event.answer("📅 No schedules set yet!", alert=True)
            return
        
        schedule_msg = "📅 <b>DAILY SCHEDULES</b>\n\n"
        for idx, sch in enumerate(system_state["daily_schedules"], 1):
            time_info = f"⏰ <code>{sch['time']}</code>"
            if "end_time" in sch:
                time_info += f" → <code>{sch['end_time']}</code>"
            schedule_msg += f"{idx}. {time_info} | 🎮 <b>{sch['game']}</b>\n"
        
        buttons = []
        for idx, sch in enumerate(system_state["daily_schedules"]):
            buttons.append([Button.inline(f"❌ Delete #{idx+1}", data=f"del_sch_{idx}".encode())])
        buttons.append([Button.inline("🔙 BACK", b'back_main')])
        
        await event.edit(schedule_msg, buttons=buttons, parse_mode='html')
        return

    elif data.startswith(b'del_sch_'):
        try:
            idx = int(data.decode().replace("del_sch_", ""))
            if 0 <= idx < len(system_state["daily_schedules"]):
                deleted = system_state["daily_schedules"].pop(idx)
                save_daily_schedules(system_state["daily_schedules"])
                await event.answer(f"✅ Deleted: {deleted['time']} | {deleted['game']}", alert=True)
        except:
            await event.answer("❌ Error deleting schedule", alert=True)
        
        # Refresh schedule view
        if system_state["daily_schedules"]:
            schedule_msg = "📅 <b>DAILY SCHEDULES</b>\n\n"
            for idx, sch in enumerate(system_state["daily_schedules"], 1):
                time_info = f"⏰ <code>{sch['time']}</code>"
                if "end_time" in sch:
                    time_info += f" → <code>{sch['end_time']}</code>"
                schedule_msg += f"{idx}. {time_info} | 🎮 <b>{sch['game']}</b>\n"
            
            buttons = []
            for idx, sch in enumerate(system_state["daily_schedules"]):
                buttons.append([Button.inline(f"❌ Delete #{idx+1}", data=f"del_sch_{idx}".encode())])
            buttons.append([Button.inline("🔙 BACK", b'back_main')])
            
            await event.edit(schedule_msg, buttons=buttons, parse_mode='html')
        else:
            msg = await get_panel_message()
            keyboards = [
                [Button.inline("🟢 FORCE START", b'force_start'), Button.inline("🔴 FORCE STOP", b'force_stop')],
                [Button.inline("⏰ AUTO SCHEDULE", b'auto_mode'), Button.inline("📢 SELECT CHANNEL", b'select_channel')],
                [Button.inline("🎮 CHANGE GAME NAME", b'change_game'), Button.inline("✏️ SET TIME", b'set_time')],
                [Button.inline("🔧 SOLVE PROBLEM", b'solve_problem'), Button.inline("📅 VIEW SCHEDULES", b'view_schedules')],
                [Button.inline("📣 ANNOUNCEMENT", b'announcement'), Button.inline("👁️ VIEW ANNOUNCEMENTS", b'view_announcements')]
            ]
            await event.edit(msg, buttons=keyboards, parse_mode='html')
        return

    elif data == b'back_main': pass

    msg = await get_panel_message()
    keyboards = [
        [Button.inline("🟢 FORCE START", b'force_start'), Button.inline("🔴 FORCE STOP", b'force_stop')],
        [Button.inline("⏰ AUTO SCHEDULE", b'auto_mode'), Button.inline("📢 SELECT CHANNEL", b'select_channel')],
        [Button.inline("🎮 CHANGE GAME NAME", b'change_game'), Button.inline("✏️ SET TIME", b'set_time')],
        [Button.inline("🔧 SOLVE PROBLEM", b'solve_problem'), Button.inline("📅 VIEW SCHEDULES", b'view_schedules')],
        [Button.inline("📣 ANNOUNCEMENT", b'announcement'), Button.inline("👁️ VIEW ANNOUNCEMENTS", b'view_announcements')]
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

    if system_state["waiting_for_announcement"]:
        try:
            if '|' not in text:
                await event.reply("⚠️ Invalid Format! Use TIME|MESSAGE")
                return
            
            parts = text.split('|', 1)  # Split only on first |
            if len(parts) != 2:
                await event.reply("⚠️ Invalid Format! Use TIME|MESSAGE")
                return
            
            time_str, message = parts
            time_str = time_str.strip()
            message = message.strip()
            
            # Validate time format
            datetime.strptime(time_str, "%H:%M")
            
            if not message:
                await event.reply("⚠️ Message cannot be empty!")
                return
            
            new_announcement = {
                "time": time_str,
                "message": message
            }
            
            system_state["daily_announcements"].append(new_announcement)
            save_daily_announcements(system_state["daily_announcements"])
            system_state["waiting_for_announcement"] = False
            
            preview = message[:100] + "..." if len(message) > 100 else message
            await event.reply(
                f"✅ <b>Daily Announcement Added!</b>\n\n"
                f"⏰ Time: <code>{time_str}</code>\n"
                f"📝 Message:\n{preview}\n\n"
                f"This will be sent automatically every day!",
                parse_mode='html'
            )
        except Exception as e:
            await event.reply(f"⚠️ Invalid Format!\n\nExample:\n19:00|🎮 Game starting soon!")
        return

    if system_state["waiting_for_manual_schedule"]:
        try:
            if '|' not in text:
                await event.reply("⚠️ Invalid Format! Use START|END|GAME or START|GAME")
                return
            
            parts = text.split('|')
            
            if len(parts) == 2:
                # Format: TIME|GAME (no end time)
                time_str, game_str = parts
                time_str = time_str.strip()
                game_str = game_str.strip().upper()
                
                # Validate time format
                datetime.strptime(time_str, "%H:%M")
                
                new_schedule = {
                    "time": time_str,
                    "game": game_str
                }
                
                response_msg = (
                    f"✅ <b>Daily Schedule Added!</b>\n\n"
                    f"⏰ Start: <code>{time_str}</code>\n"
                    f"🎮 Game: <b>{game_str}</b>\n\n"
                    f"This will activate automatically every day!"
                )
                
            elif len(parts) == 3:
                # Format: START|END|GAME
                start_time, end_time, game_str = parts
                start_time = start_time.strip()
                end_time = end_time.strip()
                game_str = game_str.strip().upper()
                
                # Validate time formats
                datetime.strptime(start_time, "%H:%M")
                datetime.strptime(end_time, "%H:%M")
                
                new_schedule = {
                    "time": start_time,
                    "end_time": end_time,
                    "game": game_str
                }
                
                response_msg = (
                    f"✅ <b>Daily Schedule Added!</b>\n\n"
                    f"⏰ Start: <code>{start_time}</code>\n"
                    f"🛑 End: <code>{end_time}</code>\n"
                    f"🎮 Game: <b>{game_str}</b>\n\n"
                    f"This will activate and deactivate automatically every day!"
                )
            else:
                await event.reply("⚠️ Invalid Format! Use START|END|GAME or START|GAME")
                return
            
            system_state["daily_schedules"].append(new_schedule)
            save_daily_schedules(system_state["daily_schedules"])
            system_state["waiting_for_manual_schedule"] = False
            
            await event.reply(response_msg, parse_mode='html')
        except Exception as e:
            await event.reply(f"⚠️ Invalid Format!\n\nExamples:\n19:30|19:50|BDG\n20:00|DAMAN")

# ================= GAME LOOP =================

async def game_loop():
    log("🚀 Aggressive Bot Started (No Waiting)...")
    init_db()
    warm_up_system()
    
    # Load daily schedules and announcements
    system_state["daily_schedules"] = load_daily_schedules()
    system_state["daily_announcements"] = load_daily_announcements()
    log(f"📅 Loaded {len(system_state['daily_schedules'])} daily schedules")
    log(f"📣 Loaded {len(system_state['daily_announcements'])} daily announcements")
    
    last_period = None
    last_prediction = None
    last_result = None
    acc_data = load_accuracy()
    last_schedule_check = None

    while True:
        try:
            # Check daily schedules and announcements every minute
            current_minute = get_ist_time().strftime("%H:%M")
            if current_minute != last_schedule_check:
                last_schedule_check = current_minute
                
                # Check for announcements to send
                announcements = check_daily_announcements()
                target_channel = system_state["active_channel_link"]
                for announcement in announcements:
                    try:
                        await userbot.send_message(target_channel, announcement['message'], parse_mode='html')
                        log(f"📣 Sent announcement: {announcement['time']}")
                        await bot.send_message(
                            ADMIN_ID,
                            f"📣 <b>ANNOUNCEMENT SENT</b>\n\n"
                            f"⏰ Time: <code>{announcement['time']}</code>\n"
                            f"📝 Message sent to channel!",
                            parse_mode='html'
                        )
                    except Exception as e:
                        log(f"⚠️ Announcement error: {e}")
                
                # Check for schedule changes
                schedule_action, schedule = check_daily_schedules()
                
                if schedule_action == "start":
                    system_state["game_name"] = schedule["game"]
                    system_state["mode"] = "manual_on"
                    system_state["stopped_by_losses"] = False
                    system_state["consecutive_losses"] = 0
                    log(f"📅 Daily Schedule Activated: {schedule['time']} | {schedule['game']}")
                    try:
                        end_info = f" → <code>{schedule['end_time']}</code>" if "end_time" in schedule else ""
                        await bot.send_message(
                            ADMIN_ID,
                            f"📅 <b>DAILY SCHEDULE ACTIVATED</b>\n\n"
                            f"⏰ Time: <code>{schedule['time']}</code>{end_info}\n"
                            f"🎮 Game: <b>{schedule['game']}</b>\n\n"
                            f"🟢 Bot is now running!",
                            parse_mode='html'
                        )
                    except:
                        pass
                
                elif schedule_action == "end":
                    system_state["mode"] = "manual_off"
                    log(f"🛑 Daily Schedule Ended: {schedule['end_time']} | {schedule['game']}")
                    
                    # Send PREDICTION END image to channel
                    target_channel = system_state["active_channel_link"]
                    if os.path.exists(PREDICTION_END_IMAGE):
                        try:
                            await userbot.send_file(target_channel, PREDICTION_END_IMAGE)
                            log("📤 Sent PREDICTION END image to channel")
                        except Exception as e:
                            log(f"⚠️ Failed to send end image: {e}")
                    else:
                        # Send text message if image not found
                        try:
                            end_msg = (
                                f"🛑 <b>PREDICTION END</b>\n\n"
                                f"⏰ <b>Time: {schedule['end_time']}</b>\n\n"
                                f"Thank you for playing!\n"
                                f"See you next time! 👋"
                            )
                            await userbot.send_message(target_channel, end_msg, parse_mode='html')
                        except:
                            pass
                    
                    # Notify admin
                    try:
                        await bot.send_message(
                            ADMIN_ID,
                            f"🛑 <b>DAILY SCHEDULE ENDED</b>\n\n"
                            f"⏰ End Time: <code>{schedule['end_time']}</code>\n"
                            f"🎮 Game: <b>{schedule['game']}</b>\n\n"
                            f"🔴 Bot stopped automatically!",
                            parse_mode='html'
                        )
                    except:
                        pass
            
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

                # --- SIMPLE TREND FOLLOWING ---
                final_pred, final_conf = simple_trend_follow(size)
                final_logic = "📈 Trend Following"
                
                last_prediction = final_pred
                real_win_rate = 0
                if acc_data["total_bets"] > 0:
                    real_win_rate = round((acc_data["wins"] / acc_data["total_bets"]) * 100, 1)

                should_post, status_msg = check_posting_status()
                target_channel = system_state["active_channel_link"]

                # Check if stopped by losses
                if system_state["stopped_by_losses"]:
                    status_msg = "🛑 STOPPED (4 Losses)"
                    should_post = False

                # Prepare result message for admin
                result_msg = ""
                if last_prediction and last_result:
                    if last_prediction == last_result:
                        result_msg = "\n✅ <b>LAST: WIN</b>"
                    else:
                        result_msg = f"\n❌ <b>LAST: LOSS</b> (Pred: {last_prediction}, Got: {last_result})"

                # Admin Log
                try:
                    await bot.send_message(ADMIN_ID, f"🎰 {system_state['game_name']} | {status_msg}\n🔢 {period[-3:]} | {number} ({size})\n🤖 Pred: <b>{final_pred}</b> ({round(final_conf)}%)\n🧠 {final_logic}{result_msg}", parse_mode='html')
                except: pass

                # Update last result for next comparison
                last_result = size

                if should_post:
                    # Win/Loss Logic
                    last_bet = system_state["last_channel_bet"]
                    if last_bet and last_bet["period"] == period:
                        if last_bet["pick"] == size:
                            # WIN - Reset loss counter
                            system_state["consecutive_losses"] = 0
                            
                            # Send random win sticker
                            win_sticker = random.choice(WIN_STICKERS)
                            if os.path.exists(win_sticker):
                                try: await userbot.send_file(target_channel, win_sticker)
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
                        else:
                            # LOSS - Increment loss counter
                            system_state["consecutive_losses"] += 1
                            log(f"❌ Loss {system_state['consecutive_losses']}/4")
                    
                    # Check if 4 losses in a row
                    if system_state["consecutive_losses"] >= 4:
                        bad_series_msg = (
                            f"⚠️ <b>Very Bad Series</b> ⚠️\n\n"
                            f"🛑 <b>Wait For Next Prediction</b>"
                        )
                        try:
                            await userbot.send_message(target_channel, bad_series_msg, parse_mode='html')
                            log("🛑 4 Losses - Stopping all predictions until manual restart")
                        except: pass
                        system_state["last_channel_bet"] = None
                        system_state["stopped_by_losses"] = True  # Stop until manual restart
                    else:
                        # Send Next Prediction
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
