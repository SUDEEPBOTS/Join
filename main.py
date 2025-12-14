import os
import asyncio
import logging
import random
import re  # NEW: Link parsing ke liye
from flask import Flask
from threading import Thread
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, 
    FloodWaitError, 
    UserAlreadyParticipantError,
    InviteHashExpiredError,
    UsernameInvalidError,
    InviteHashInvalidError # NEW: Invalid Hash pakdne ke liye
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
active_clients = [] 
TARGET_CACHE = set()

# --- HELPER FUNCTIONS ---
def is_admin(user_id):
    if user_id == OWNER_ID: return True
    if admins_collection.find_one({"user_id": user_id}): return True
    return False

def refresh_targets():
    global TARGET_CACHE
    temp_set = set()
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
            
            if not stickers_loaded:
                await load_sticker_pack(client)
                stickers_loaded = True

            @client.on(events.NewMessage())
            async def troll_handler(event):
                try:
                    sender = await event.get_sender()
                    if not sender: return
                    sender_id = int(sender.id)

                    if sender_id in TARGET_CACHE:
                        print(f"⚡ FAST REACT: {sender_id}")
                        emoji = random.choice(['😂', '🌚', '🤣', '🤡', '💩', '🔥'])
                        try: await event.react(emoji)
                        except: 
                            try:
                                await client(SendReactionRequest(
                                    peer=event.peer_id, 
                                    msg_id=event.id, 
                                    reaction=[ReactionEmoji(emoticon=emoji)]
                                ))
                            except: pass

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
    await event.reply(f"👋 **Bot Active!**\n\n🎯 Targets: `{len(TARGET_CACHE)}`\n🤖 Bots: `{len(active_clients)}`\n\n**Parser:** Regex v2.0", buttons=buttons)

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if not is_admin(event.sender_id): return
    data = event.data.decode('utf-8')
    
    if data == "add_account_btn":
        user_states[event.sender_id] = {'step': 'ask_number'}
        await event.respond("📞 **Number Bhejein:**")
    elif data == "add_admin_btn":
        user_states[event.sender_id] = {'step': 'ask_admin_id'}
        await event.respond("👤 **New Admin ID:**")
    elif data == "set_target":
        user_states[event.sender_id] = {'step': 'ask_target_id'}
        await event.respond("🎯 **Target User ID:**")
    elif data == "stop_target":
        user_states[event.sender_id] = {'step': 'ask_remove_target_id'}
        await event.respond(f"🛑 **Remove ID:**\nCurrent: {TARGET_CACHE}")

@bot.on(events.NewMessage(pattern='/add'))
async def add_command(event):
    if not is_admin(event.sender_id): return
    user_states[event.sender_id] = {'step': 'ask_number'}
    await event.reply("📞 Phone:")

# --- LINK HANDLER ---
@bot.on(events.NewMessage())
async def message_handler(event):
    if event.text.startswith('/') and event.text != '/cancel': pass
    elif is_admin(event.sender_id):
        # State Handling
        if event.sender_id in user_states:
            state = user_states[event.sender_id]
            text = event.text.strip()
            if state['step'] == 'ask_admin_id':
                try: admins_collection.insert_one({"user_id": int(text)}); await event.reply("✅ Added."); del user_states[event.sender_id]
                except: await event.reply("Invalid.")
            elif state['step'] == 'ask_target_id':
                try: targets_collection.insert_one({"user_id": int(text)}); refresh_targets(); await event.reply(f"🎯 Set: {text}"); del user_states[event.sender_id]
                except: await event.reply("Invalid.")
            elif state['step'] == 'ask_remove_target_id':
                try: targets_collection.delete_one({"user_id": int(text)}); refresh_targets(); await event.reply("🛑 Stopped."); del user_states[event.sender_id]
                except: await event.reply("Invalid.")
            elif state['step'] in ['ask_number', 'ask_otp', 'ask_password']:
                await handle_login_steps(event, state)
        
        # LINK DETECTION (ANYWHERE IN TEXT)
        elif "t.me/" in event.text:
            await handle_join_task(event)

# --- ADVANCED LINK PARSER ---
async def handle_join_task(event):
    text = event.text.strip()
    sessions = list(sessions_collection.find({}))
    if not sessions: await event.reply("Empty DB."); return
    
    status_msg = await event.reply(f"🚀 **Analysing...**")
    
    # --- REGEX MAGIC START ---
    # Finds the hash/username even if hidden in text
    identifier = None
    join_type = None

    # 1. Private Link (+ prefix) -> t.me/+HASH
    match_plus = re.search(r"t\.me/\+([a-zA-Z0-9_\-]+)", text)
    
    # 2. Private Link (joinchat) -> t.me/joinchat/HASH
    match_join = re.search(r"t\.me/joinchat/([a-zA-Z0-9_\-]+)", text)
    
    # 3. Public Link (username) -> t.me/USERNAME
    match_pub = re.search(r"t\.me/([a-zA-Z0-9_]{3,})", text)

    if match_plus:
        identifier = match_plus.group(1)
        join_type = "private"
    elif match_join:
        identifier = match_join.group(1)
        join_type = "private"
    elif match_pub:
        # Ensure it's not 'joinchat' or '+' caught as username
        candidate = match_pub.group(1)
        if candidate not in ["joinchat", "+"]:
            identifier = candidate
            join_type = "public"
    
    if not identifier:
        await status_msg.edit("❌ **Invalid Link Format!**\nSahi link bhejein (e.g., `t.me/+Hash` or `t.me/user`)"); return
    # --- REGEX MAGIC END ---

    await status_msg.edit(f"🚀 **Joining...**\nType: {join_type}\nID: `{identifier}`")
    
    success = 0; failed = 0; error_log = []

    for user_data in sessions:
        try:
            client = TelegramClient(StringSession(user_data['session']), API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized(): failed += 1; await client.disconnect(); continue
            
            joined = False
            try:
                if join_type == "private":
                    await client(ImportChatInviteRequest(identifier))
                else:
                    await client(JoinChannelRequest(identifier))
                joined = True
            except UserAlreadyParticipantError:
                joined = True 
            except InviteHashExpiredError:
                error_log.append("Link Expired")
                break
            except InviteHashInvalidError:
                error_log.append("Invalid Hash/Link")
                break
            except Exception as e:
                failed += 1
                error_log.append(f"{user_data.get('phone')}: {e}")

            if joined:
                success += 1
                try:
                    # Pin & Sticker
                    if join_type == "public":
                         entity = await client.get_entity(identifier)
                    else:
                         # Private entity needs fetch after join
                         # Thoda tricky hai, usually updates se nikalna padta hai
                         # For now, we skip pinning for private if get_entity fails
                         try:
                             # Invite links se entity directly nahi milti bina join kiye
                             # Try getting dialogs
                             pass
                         except: pass
                    
                    # Reaction/Sticker logic...
                except: pass
            
            await client.disconnect()
            await asyncio.sleep(2)
        except: failed += 1

    report = f"✅ **Task Done!**\nSuccess: {success}\nFailed: {failed}"
    if error_log: report += f"\n\n🛑 **Errors:**\n" + "\n".join(set(error_log[:5]))
    await status_msg.edit(report)

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
                await event.reply("✅ **Success!**"); del user_states[chat_id]
            except SessionPasswordNeededError:
                user_states[chat_id]['step'] = 'ask_password'; await event.reply("🔐 **Password:**")
        elif state['step'] == 'ask_password':
            await state['client'].sign_in(password=text)
            save_session(state['phone'], state['client'])
            active_clients.append(state['client'])
            await event.reply("✅ **Success!**"); del user_states[chat_id]
    except Exception as e: await event.reply(f"❌ Error: {e}")

def save_session(phone, client):
    session = StringSession.save(client.session)
    if not sessions_collection.find_one({"phone": phone}):
        sessions_collection.insert_one({"phone": phone, "session": session})

if __name__ == '__main__':
    keep_alive()
    bot.loop.run_until_complete(start_all_clients())
    bot.run_until_disconnected()
            
