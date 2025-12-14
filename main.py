import os
import asyncio
import logging
import random
from flask import Flask
from threading import Thread
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, 
    FloodWaitError, 
    UserAlreadyParticipantError
)
from telethon.tl.functions.messages import ToggleDialogPinRequest, ImportChatInviteRequest
from pymongo import MongoClient

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "12345")) 
API_HASH = os.environ.get("API_HASH", "your_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URI = os.environ.get("MONGO_URI", "your_mongo_url")
OWNER_ID = 8389974605  
STICKER_ID = os.environ.get("STICKER_ID", None) 

# Troll Stickers List
TROLL_STICKERS = [
    # Yahan apne funny stickers ke ID daalein
    "CAACAgUAAxkBAA...", 
]

# --- MONGODB ---
cluster = MongoClient(MONGO_URI)
db = cluster["TelegramBot"]
sessions_collection = db["sessions"]
admins_collection = db["admins"]
targets_collection = db["targets"]

# --- LOGGING ---
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

# --- FLASK ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is Running!"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
def keep_alive(): t = Thread(target=run_web); t.start()

# --- GLOBALS ---
bot = TelegramClient('admin_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
user_states = {} 
active_clients = [] 
TARGET_CACHE = set() # Fast access ke liye targets yahan save honge

# --- HELPER FUNCTIONS ---
def is_admin(user_id):
    if user_id == OWNER_ID: return True
    if admins_collection.find_one({"user_id": user_id}): return True
    return False

def refresh_targets():
    """DB se targets ko memory mein load karega (Speed ke liye)"""
    global TARGET_CACHE
    TARGET_CACHE = set(t['user_id'] for t in targets_collection.find({}))

# --- BACKGROUND TROLL CLIENTS MANAGER ---
async def start_all_clients():
    refresh_targets() # Startup par cache update
    sessions = list(sessions_collection.find({}))
    if not sessions: return

    print(f"🔄 Starting {len(sessions)} Monitoring Bots...")
    
    for user_data in sessions:
        try:
            client = TelegramClient(StringSession(user_data['session']), API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized(): continue
            
            # --- TROLL EVENT HANDLER ---
            @client.on(events.NewMessage(incoming=True))
            async def troll_handler(event):
                # Check Global Cache (DB query se fast hai)
                if event.sender_id in TARGET_CACHE:
                    try:
                        # 1. Random Delay 
                        await asyncio.sleep(random.randint(3, 10))
                        
                        # 2. Emoji Reaction
                        emoji = random.choice(['😂', '🌚', '🤣', '🤡', '💩'])
                        try: await event.react(emoji)
                        except: pass 

                        # 3. Sticker Reply (Chance 70%)
                        if TROLL_STICKERS and random.random() > 0.3:
                            sticker = random.choice(TROLL_STICKERS)
                            if "CAAC" in sticker: 
                                await event.reply(file=sticker)
                                
                    except Exception as e:
                        print(f"Troll Error: {e}")

            active_clients.append(client)
            
        except Exception as e:
            print(f"Client Fail: {user_data.get('phone')}")

# --- ADMIN COMMANDS ---
@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if not is_admin(event.sender_id): return
    refresh_targets() # Count fresh rakho
    
    buttons = [
        [Button.inline("➕ Add Admin", data="add_admin_btn"), Button.inline("🤖 Stats", data="stats")],
        [Button.inline("🎯 Set Target", data="set_target"), Button.inline("🛑 Stop Target", data="stop_target")]
    ]
    
    msg = (f"👋 **Boss!**\n\n"
           f"🔫 Active Targets: `{len(TARGET_CACHE)}`\n"
           f"🤖 Active Bots: `{len(active_clients)}`\n\n"
           f"**Controls:**\n"
           f"🎯 **Set:** Kisi user ko target list mein daalo.\n"
           f"🛑 **Stop:** Target list se hatao (Troll band).")
    
    await event.reply(msg, buttons=buttons)

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if not is_admin(event.sender_id): return
    data = event.data.decode('utf-8')
    
    if data == "stats":
        total = sessions_collection.count_documents({})
        await event.answer(f"Total Accounts: {total}", alert=True)
        
    elif data == "add_admin_btn":
        user_states[event.sender_id] = {'step': 'ask_admin_id'}
        await event.respond("👤 **New Admin ID Bhejein:**")
        
    elif data == "set_target":
        user_states[event.sender_id] = {'step': 'ask_target_id'}
        await event.respond("🎯 **Target User ID Bhejein:**\n(Is par bot hasenge)")

    elif data == "stop_target":
        user_states[event.sender_id] = {'step': 'ask_remove_target_id'}
        # List dikhao kaun target hai
        targets_list = "\n".join([f"`{t}`" for t in TARGET_CACHE])
        if not targets_list: targets_list = "None"
        await event.respond(f"🛑 **Kisko Stop Karna Hai?**\nUser ID Bhejein.\n\n**Current Targets:**\n{targets_list}")

@bot.on(events.NewMessage(pattern='/add'))
async def add_command(event):
    if not is_admin(event.sender_id): return
    user_states[event.sender_id] = {'step': 'ask_number'}
    await event.reply("📞 **Phone Number Bhejein**")

# --- MESSAGE HANDLER ---
@bot.on(events.NewMessage())
async def message_handler(event):
    if event.text.startswith('/') and event.text != '/cancel': pass
    elif is_admin(event.sender_id):
        if event.sender_id in user_states:
            state = user_states[event.sender_id]
            text = event.text.strip()
            
            # Add Admin
            if state['step'] == 'ask_admin_id':
                try:
                    nid = int(text)
                    admins_collection.insert_one({"user_id": nid})
                    await event.reply("✅ Admin Added.")
                    del user_states[event.sender_id]
                except: await event.reply("❌ Invalid ID.")

            # Set Target
            elif state['step'] == 'ask_target_id':
                try:
                    tid = int(text)
                    targets_collection.insert_one({"user_id": tid})
                    refresh_targets() # Cache Update
                    await event.reply(f"🎯 **Target {tid} Locked!**\nAb bots is par react karenge.")
                    del user_states[event.sender_id]
                except: await event.reply("❌ Invalid ID.")

            # Stop Target
            elif state['step'] == 'ask_remove_target_id':
                try:
                    tid = int(text)
                    targets_collection.delete_one({"user_id": tid})
                    refresh_targets() # Cache Update
                    await event.reply(f"🛑 **Target {tid} Removed!**\nAb bot shant rahenge.")
                    del user_states[event.sender_id]
                except: await event.reply("❌ Invalid ID.")

            # Login Steps
            elif state['step'] in ['ask_number', 'ask_otp', 'ask_password']:
                await handle_login_steps(event, state)
                
        elif "t.me/" in event.text:
            await handle_join_task(event)

# --- LOGIN LOGIC ---
async def handle_login_steps(event, state):
    text = event.text.strip()
    chat_id = event.chat_id
    try:
        if state['step'] == 'ask_number':
            phone = text
            temp_client = TelegramClient(StringSession(), API_ID, API_HASH)
            await temp_client.connect()
            if not await temp_client.is_user_authorized():
                send_code = await temp_client.send_code_request(phone)
                user_states[chat_id] = {'step': 'ask_otp', 'phone': phone, 'client': temp_client, 'hash': send_code.phone_code_hash}
                await event.reply("📨 **OTP Bhejein**")
            else: await event.reply("Already Added."); del user_states[chat_id]
        elif state['step'] == 'ask_otp':
            otp = text.replace(" ", "")
            try:
                await state['client'].sign_in(state['phone'], otp, phone_code_hash=state['hash'])
                save_session(state['phone'], state['client'])
                active_clients.append(state['client']) 
                await event.reply("✅ **Saved!**"); del user_states[chat_id]
            except SessionPasswordNeededError:
                user_states[chat_id]['step'] = 'ask_password'; await event.reply("🔐 **Password Bhejein**")
        elif state['step'] == 'ask_password':
            await state['client'].sign_in(password=text)
            save_session(state['phone'], state['client'])
            active_clients.append(state['client'])
            await event.reply("✅ **Saved!**"); del user_states[chat_id]
    except Exception as e: await event.reply(f"❌ Error: {e}")

def save_session(phone, client):
    session = StringSession.save(client.session)
    if not sessions_collection.find_one({"phone": phone}):
        sessions_collection.insert_one({"phone": phone, "session": session})

# --- JOIN TASK ---
async def handle_join_task(event):
    link = event.text.strip()
    sessions = list(sessions_collection.find({}))
    if not sessions: await event.reply("❌ Database Empty."); return
    status_msg = await event.reply(f"🚀 **Joining...**\nTarget: {link}")
    success = 0; failed = 0
    
    for user_data in sessions:
        try:
            client = TelegramClient(StringSession(user_data['session']), API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized(): failed += 1; await client.disconnect(); continue
            
            try:
                if "+" in link or "joinchat" in link:
                    await client(ImportChatInviteRequest(link.split("+")[1]))
                else:
                    await client.join_chat(link.split("/")[-1])
                
                entity = await client.get_entity(link)
                await client(ToggleDialogPinRequest(peer=entity, pinned=True))
                if STICKER_ID: await client.send_file(entity, STICKER_ID)
                success += 1
            except: failed += 1
            
            await client.disconnect()
            await asyncio.sleep(2)
        except: failed += 1
    await status_msg.edit(f"✅ **Done!**\nSuccess: {success}\nFailed: {failed}")

if __name__ == '__main__':
    keep_alive()
    bot.loop.run_until_complete(start_all_clients())
    print("Bot Started...")
    bot.run_until_disconnected()
                
