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
from telethon.tl.functions.messages import GetStickerSetRequest, ToggleDialogPinRequest, ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import InputStickerSetShortName
from pymongo import MongoClient

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "12345")) 
API_HASH = os.environ.get("API_HASH", "your_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URI = os.environ.get("MONGO_URI", "your_mongo_url")
OWNER_ID = 8389974605  
STICKER_ID = os.environ.get("STICKER_ID", None) 
TROLL_PACK_NAME = "SHIVANSHOP4" 
TROLL_STICKERS = [] 

# --- MONGODB ---
cluster = MongoClient(MONGO_URI)
db = cluster["TelegramBot"]
sessions_collection = db["sessions"]
admins_collection = db["admins"]
targets_collection = db["targets"]

# --- LOGGING ---
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

# --- FLASK (WEB SERVER FOR RENDER) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is Running!"

def run_web():
    # Render se PORT lo, warna 8080 use karo
    port = int(os.environ.get("PORT", 8080))
    # '0.0.0.0' zaroori hai Render ke liye
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True # Background thread banaya
    t.start()

# --- GLOBALS ---
bot = TelegramClient('admin_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
user_states = {} 
active_clients = [] 
TARGET_CACHE = set()

# --- HELPER FUNCTIONS ---
def is_admin(user_id):
    if user_id == OWNER_ID: return True
    if admins_collection.find_one({"user_id": user_id}): return True
    return False

def refresh_targets():
    global TARGET_CACHE
    TARGET_CACHE = set(t['user_id'] for t in targets_collection.find({}))

# --- STICKER LOADER ---
async def load_sticker_pack(client):
    global TROLL_STICKERS
    if not TROLL_PACK_NAME: return
    try:
        sticker_set = await client(GetStickerSetRequest(
            stickerset=InputStickerSetShortName(TROLL_PACK_NAME),
            hash=0
        ))
        TROLL_STICKERS = sticker_set.documents
        print(f"✅ Loaded {len(TROLL_STICKERS)} stickers")
    except Exception as e:
        print(f"❌ Failed to load sticker pack: {e}")

# --- BACKGROUND MONITORING ---
async def start_all_clients():
    refresh_targets()
    sessions = list(sessions_collection.find({}))
    if not sessions: return
    print(f"🔄 Starting {len(sessions)} Monitoring Bots...")
    
    stickers_loaded = False

    for user_data in sessions:
        try:
            client = TelegramClient(StringSession(user_data['session']), API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized(): continue
            
            if not stickers_loaded:
                await load_sticker_pack(client)
                stickers_loaded = True

            @client.on(events.NewMessage(incoming=True))
            async def troll_handler(event):
                if event.sender_id in TARGET_CACHE:
                    try:
                        await asyncio.sleep(random.randint(3, 10))
                        emoji = random.choice(['😂', '🌚', '🤣', '🤡', '💩'])
                        try: await event.react(emoji)
                        except: pass 
                        if TROLL_STICKERS and random.random() > 0.3:
                            sticker = random.choice(TROLL_STICKERS)
                            await event.reply(file=sticker)
                    except: pass
            active_clients.append(client)
        except: pass

# --- COMMANDS ---
@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if not is_admin(event.sender_id): return
    refresh_targets()
    buttons = [
        [Button.inline("➕ Add Admin", data="add_admin_btn"), Button.inline("🤖 Stats", data="stats")],
        [Button.inline("🎯 Set Target", data="set_target"), Button.inline("🛑 Stop Target", data="stop_target")]
    ]
    await event.reply(f"👋 **Boss!**\nTargets: `{len(TARGET_CACHE)}`\nLoaded Stickers: `{len(TROLL_STICKERS)}`", buttons=buttons)

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if not is_admin(event.sender_id): return
    data = event.data.decode('utf-8')
    if data == "stats":
        total = sessions_collection.count_documents({})
        await event.answer(f"Accounts: {total}", alert=True)
    elif data == "add_admin_btn":
        user_states[event.sender_id] = {'step': 'ask_admin_id'}
        await event.respond("User ID:")
    elif data == "set_target":
        user_states[event.sender_id] = {'step': 'ask_target_id'}
        await event.respond("Target ID:")
    elif data == "stop_target":
        user_states[event.sender_id] = {'step': 'ask_remove_target_id'}
        await event.respond(f"Remove ID. Current: {TARGET_CACHE}")

@bot.on(events.NewMessage(pattern='/add'))
async def add_command(event):
    if not is_admin(event.sender_id): return
    user_states[event.sender_id] = {'step': 'ask_number'}
    await event.reply("📞 Phone:")

@bot.on(events.NewMessage())
async def message_handler(event):
    if event.text.startswith('/') and event.text != '/cancel': pass
    elif is_admin(event.sender_id):
        if event.sender_id in user_states:
            state = user_states[event.sender_id]
            text = event.text.strip()
            if state['step'] == 'ask_admin_id':
                try: admins_collection.insert_one({"user_id": int(text)}); await event.reply("✅ Added."); del user_states[event.sender_id]
                except: await event.reply("Invalid.")
            elif state['step'] == 'ask_target_id':
                try: targets_collection.insert_one({"user_id": int(text)}); refresh_targets(); await event.reply("🎯 Set."); del user_states[event.sender_id]
                except: await event.reply("Invalid.")
            elif state['step'] == 'ask_remove_target_id':
                try: targets_collection.delete_one({"user_id": int(text)}); refresh_targets(); await event.reply("🛑 Stopped."); del user_states[event.sender_id]
                except: await event.reply("Invalid.")
            elif state['step'] in ['ask_number', 'ask_otp', 'ask_password']:
                await handle_login_steps(event, state)
        elif "t.me/" in event.text:
            await handle_join_task(event)

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
                await event.reply("📨 OTP:")
            else: await event.reply("Already Added."); del user_states[chat_id]
        elif state['step'] == 'ask_otp':
            otp = text.replace(" ", "")
            try:
                await state['client'].sign_in(state['phone'], otp, phone_code_hash=state['hash'])
                save_session(state['phone'], state['client'])
                active_clients.append(state['client'])
                await event.reply("✅ Saved!"); del user_states[chat_id]
            except SessionPasswordNeededError:
                user_states[chat_id]['step'] = 'ask_password'; await event.reply("🔐 Password:")
        elif state['step'] == 'ask_password':
            await state['client'].sign_in(password=text)
            save_session(state['phone'], state['client'])
            active_clients.append(state['client'])
            await event.reply("✅ Saved!"); del user_states[chat_id]
    except Exception as e: await event.reply(f"❌ Error: {e}")

def save_session(phone, client):
    session = StringSession.save(client.session)
    if not sessions_collection.find_one({"phone": phone}):
        sessions_collection.insert_one({"phone": phone, "session": session})

async def handle_join_task(event):
    link = event.text.strip()
    sessions = list(sessions_collection.find({}))
    if not sessions: await event.reply("Empty DB."); return
    status_msg = await event.reply(f"🚀 Joining...\nTarget: {link}")
    success = 0; failed = 0; error_log = []
    
    for user_data in sessions:
        try:
            client = TelegramClient(StringSession(user_data['session']), API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized(): failed += 1; await client.disconnect(); continue
            joined = False
            try:
                if "+" in link or "joinchat" in link:
                    await client(ImportChatInviteRequest(link.split("+")[1]))
                else:
                    await client(JoinChannelRequest(link.split("/")[-1]))
                joined = True
            except UserAlreadyParticipantError: joined = True
            except Exception as e: failed += 1; error_log.append(f"{user_data['phone']}: {e}")

            if joined:
                try:
                    entity = await client.get_entity(link)
                    await client(ToggleDialogPinRequest(peer=entity, pinned=True))
                    if STICKER_ID: await client.send_file(entity, STICKER_ID)
                    success += 1
                except: success += 1
            await client.disconnect()
            await asyncio.sleep(2)
        except: failed += 1

    report = f"✅ **Done!**\nSuccess: {success}\nFailed: {failed}"
    if error_log: report += f"\nErrors:\n" + "\n".join(error_log[:5])
    await status_msg.edit(report)

if __name__ == '__main__':
    # 1. Flask Web Server ko start karo background thread mein
    keep_alive()
    
    # 2. Background Bots Start karo
    print("⏳ Starting Background Bots...")
    bot.loop.run_until_complete(start_all_clients())
    
    # 3. Main Bot Start karo
    print("✅ Bot Deployed & Running!")
    bot.run_until_disconnected()
        
