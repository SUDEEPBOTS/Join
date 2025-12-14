import os
import asyncio
import logging
import random
import re
from flask import Flask
from threading import Thread
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, 
    FloodWaitError, 
    UserAlreadyParticipantError,
    InviteHashExpiredError,
    InviteHashInvalidError
)
from telethon.tl.functions.messages import (
    GetStickerSetRequest, 
    SendReactionRequest, 
    ImportChatInviteRequest,
    ToggleDialogPinRequest
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import InputStickerSetShortName, ReactionEmoji
from pymongo import MongoClient

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "12345")) 
API_HASH = os.environ.get("API_HASH", "your_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URI = os.environ.get("MONGO_URI", "your_mongo_url")
OWNER_ID = 8389974605  
TROLL_PACK_NAME = "SHIVANSHOP4" 
TROLL_STICKERS = [] 

# --- MONGODB ---
cluster = MongoClient(MONGO_URI)
db = cluster["TelegramBot"]
sessions_collection = db["sessions"]
admins_collection = db["admins"]
targets_collection = db["targets"]

# --- LOGGING ---
logging.basicConfig(format='%(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- FLASK ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is Running!"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
def keep_alive(): t = Thread(target=run_web); t.daemon = True; t.start()

# --- GLOBALS ---
bot = TelegramClient('admin_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
user_states = {} 
active_clients = [] # Isme saare connected userbots rahenge
TARGET_CACHE = set()

# --- HELPER FUNCTIONS ---
def is_admin(user_id):
    if user_id == OWNER_ID: return True
    if admins_collection.find_one({"user_id": user_id}): return True
    return False

def refresh_targets():
    global TARGET_CACHE
    temp_set = set()
    # Cache ko fresh DB data se bharein
    for t in targets_collection.find({}):
        try: temp_set.add(int(t['user_id']))
        except: pass
    TARGET_CACHE = temp_set
    print(f"🔄 Targets Refreshed: {TARGET_CACHE}")

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
        print(f"❌ Sticker Error: {e}")

# --- GANG ATTACK LOGIC ---
async def gang_reaction(chat_id, message_id):
    """Sab bots ko ek sath order deta hai react karne ka"""
    emoji = random.choice(['😂', '🌚', '🤣', '🤡', '💩', '🔥'])
    
    # Har active client (userbot) se react karwao
    for client in active_clients:
        try:
            await client(SendReactionRequest(
                peer=chat_id,
                msg_id=message_id,
                reaction=[ReactionEmoji(emoticon=emoji)]
            ))
        except: pass # Agar ek fail ho to dusra na ruke

# --- MONITORING BOT ---
async def start_all_clients():
    refresh_targets()
    sessions = list(sessions_collection.find({}))
    if not sessions: return
    print(f"🔄 Starting {len(sessions)} Bots...")
    
    stickers_loaded = False

    for user_data in sessions:
        try:
            client = TelegramClient(StringSession(user_data['session']), API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized(): continue
            
            # Load Stickers (Bas ek baar)
            if not stickers_loaded:
                await load_sticker_pack(client)
                stickers_loaded = True

            # LISTENER
            @client.on(events.NewMessage())
            async def troll_handler(event):
                try:
                    sender = await event.get_sender()
                    if not sender: return
                    sender_id = int(sender.id)

                    if sender_id in TARGET_CACHE:
                        print(f"⚡ TARGET SPOTTED: {sender_id}")
                        
                        # 1. GANG REACTION (Sab Ek Sath)
                        # Hum current client se react nahi karwayenge, balki 
                        # 'gang_reaction' function call karenge jo SABSE karwayega.
                        # Taaki sync rahe.
                        asyncio.create_task(gang_reaction(event.chat_id, event.id))

                        # 2. Sticker Reply (20% Chance - Only Current Bot)
                        if TROLL_STICKERS and random.random() < 0.20:
                            sticker = random.choice(TROLL_STICKERS)
                            try: await event.reply(file=sticker)
                            except: pass

                except: pass

            active_clients.append(client)
        except: pass

# --- ADMIN COMMANDS ---
@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if not is_admin(event.sender_id): return
    refresh_targets()
    buttons = [
        [Button.inline("➕ Add Account", data="add_account_btn"), Button.inline("➕ Add Admin", data="add_admin_btn")],
        [Button.inline("🎯 Set Target", data="set_target"), Button.inline("🛑 Stop Target", data="stop_target")]
    ]
    await event.reply(f"👋 **Gang Mode On!**\n\n🎯 Targets: `{len(TARGET_CACHE)}`\n🤖 Bots: `{len(active_clients)}`\n⚡ **All Bots Will React**", buttons=buttons)

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if not is_admin(event.sender_id): return
    data = event.data.decode('utf-8')
    
    if data == "add_account_btn":
        user_states[event.sender_id] = {'step': 'ask_number'}
        await event.respond("📞 **Naya Number Bhejein:**")
    elif data == "add_admin_btn":
        user_states[event.sender_id] = {'step': 'ask_admin_id'}
        await event.respond("👤 **New Admin ID:**")
    elif data == "set_target":
        user_states[event.sender_id] = {'step': 'ask_target_id'}
        await event.respond("🎯 **Target User ID:**")
    elif data == "stop_target":
        user_states[event.sender_id] = {'step': 'ask_remove_target_id'}
        await event.respond(f"🛑 **Kis ID ko Stop karna hai?**\nCurrent: {TARGET_CACHE}")

@bot.on(events.NewMessage(pattern='/add'))
async def add_command(event):
    if not is_admin(event.sender_id): return
    user_states[event.sender_id] = {'step': 'ask_number'}
    await event.reply("📞 Phone:")

# --- MESSAGE & LINK HANDLER ---
@bot.on(events.NewMessage())
async def message_handler(event):
    if event.text.startswith('/') and event.text != '/cancel': pass
    elif is_admin(event.sender_id):
        # 1. State Handling
        if event.sender_id in user_states:
            state = user_states[event.sender_id]
            text = event.text.strip()
            
            if state['step'] == 'ask_admin_id':
                try: admins_collection.insert_one({"user_id": int(text)}); await event.reply("✅ Added."); del user_states[event.sender_id]
                except: await event.reply("Invalid.")
            
            elif state['step'] == 'ask_target_id':
                try: 
                    targets_collection.insert_one({"user_id": int(text)})
                    refresh_targets() # Update Cache Immediately
                    await event.reply(f"🎯 Set: {text}"); del user_states[event.sender_id]
                except: await event.reply("Invalid.")
            
            # --- STOP TARGET FIX ---
            elif state['step'] == 'ask_remove_target_id':
                try:
                    t_id = int(text)
                    # delete_many use kiya taaki duplicates bhi ud jayein
                    result = targets_collection.delete_many({"user_id": t_id})
                    
                    # Memory/Cache ko turant clear karo
                    refresh_targets()
                    
                    if result.deleted_count > 0:
                        await event.reply(f"🛑 **Stopped!** ID {t_id} removed."); 
                    else:
                        await event.reply("⚠️ ID database mein nahi mili, but cache refresh kar diya.");
                    
                    del user_states[event.sender_id]
                except: await event.reply("Invalid ID.")

            elif state['step'] in ['ask_number', 'ask_otp', 'ask_password']:
                await handle_login_steps(event, state)
        
        # 2. Join Link
        elif "t.me/" in event.text:
            await handle_join_task(event)

# --- JOIN LOGIC (REGEX) ---
async def handle_join_task(event):
    text = event.text.strip()
    sessions = list(sessions_collection.find({}))
    if not sessions: await event.reply("Empty DB."); return
    
    status_msg = await event.reply(f"🚀 **Checking Link...**")
    
    # Regex Parser
    identifier = None
    join_type = None

    match_plus = re.search(r"t\.me/\+([a-zA-Z0-9_\-]+)", text)
    match_join = re.search(r"t\.me/joinchat/([a-zA-Z0-9_\-]+)", text)
    match_pub = re.search(r"t\.me/([a-zA-Z0-9_]{3,})", text)

    if match_plus:
        identifier = match_plus.group(1); join_type = "private"
    elif match_join:
        identifier = match_join.group(1); join_type = "private"
    elif match_pub:
        candidate = match_pub.group(1)
        if candidate not in ["joinchat", "+"]: identifier = candidate; join_type = "public"
    
    if not identifier: await status_msg.edit("❌ **Invalid Link!**"); return
    await status_msg.edit(f"🚀 **Joining {join_type}...**\nID: `{identifier}`")
    
    success = 0; failed = 0; error_log = []

    for user_data in sessions:
        try:
            client = TelegramClient(StringSession(user_data['session']), API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized(): failed += 1; await client.disconnect(); continue
            
            joined = False
            try:
                if join_type == "private": await client(ImportChatInviteRequest(identifier))
                else: await client(JoinChannelRequest(identifier))
                joined = True
            except UserAlreadyParticipantError: joined = True 
            except Exception as e: failed += 1; error_log.append(f"{user_data.get('phone')}: {e}")

            if joined:
                success += 1
                try:
                    if join_type == "public": entity = await client.get_entity(identifier)
                    else: entity = await client.get_entity(identifier) # Try for private
                    await client(ToggleDialogPinRequest(peer=entity, pinned=True))
                except: pass
            
            await client.disconnect()
            await asyncio.sleep(2)
        except: failed += 1

    report = f"✅ **Done!**\nSuccess: {success}\nFailed: {failed}"
    if error_log: report += f"\nErrors:\n" + "\n".join(set(error_log[:5]))
    await status_msg.edit(report)

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
                await event.reply("📨 **OTP:**")
            else: await event.reply("Already Added."); del user_states[chat_id]
        elif state['step'] == 'ask_otp':
            otp = text.replace(" ", "")
            try:
                await state['client'].sign_in(state['phone'], otp, phone_code_hash=state['hash'])
                save_session(state['phone'], state['client'])
                active_clients.append(state['client'])
                await event.reply("✅ **Added!**"); del user_states[chat_id]
            except SessionPasswordNeededError:
                user_states[chat_id]['step'] = 'ask_password'; await event.reply("🔐 **Password:**")
        elif state['step'] == 'ask_password':
            await state['client'].sign_in(password=text)
            save_session(state['phone'], state['client'])
            active_clients.append(state['client'])
            await event.reply("✅ **Added!**"); del user_states[chat_id]
    except Exception as e: await event.reply(f"❌ Error: {e}")

def save_session(phone, client):
    session = StringSession.save(client.session)
    if not sessions_collection.find_one({"phone": phone}):
        sessions_collection.insert_one({"phone": phone, "session": session})

if __name__ == '__main__':
    keep_alive()
    bot.loop.run_until_complete(start_all_clients())
    bot.run_until_disconnected()
    
