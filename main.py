import os
import asyncio
import logging
import random
from flask import Flask
from threading import Thread
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetStickerSetRequest, SendReactionRequest
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
    # SAB KUCH INT MEIN CONVERT KARO
    temp_set = set()
    for t in targets_collection.find({}):
        try:
            temp_set.add(int(t['user_id']))
        except:
            pass
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

            # --- TROLL HANDLER (NO FILTERS) ---
            # Hum saare messages sunenge, filter baad mein karenge
            @client.on(events.NewMessage())
            async def troll_handler(event):
                try:
                    # Sender ID nikalo safe tareeke se
                    sender = await event.get_sender()
                    if not sender: return
                    sender_id = int(sender.id)

                    # LOGGING: Har message ka ID print karega (Debugging ke liye)
                    # print(f"👀 Saw msg from: {sender_id} in Chat: {event.chat_id}")

                    # MATCH CHECK
                    if sender_id in TARGET_CACHE:
                        print(f"🎯 TARGET DETECTED: {sender_id}")
                        
                        # 1. Reaction Logic
                        await asyncio.sleep(random.randint(1, 4))
                        emoji = random.choice(['😂', '🌚', '🤣', '🤡', '💩', '🔥'])
                        
                        try:
                            # Method 1: Easy Way
                            await event.react(emoji)
                            print(f"✅ Reacted {emoji}")
                        except:
                            try:
                                # Method 2: API Way (Stronger)
                                await client(SendReactionRequest(
                                    peer=event.peer_id,
                                    msg_id=event.id,
                                    reaction=[ReactionEmoji(emoticon=emoji)]
                                ))
                                print(f"✅ Reacted API {emoji}")
                            except Exception as e:
                                print(f"❌ Reaction Failed: {e}")

                        # 2. Sticker Reply
                        if TROLL_STICKERS:
                            sticker = random.choice(TROLL_STICKERS)
                            try:
                                await event.reply(file=sticker)
                                print("✅ Sticker Sent")
                            except Exception as e:
                                print(f"❌ Sticker Failed: {e}")

                except Exception as e:
                    print(f"Handler Error: {e}")

            active_clients.append(client)
        except Exception as e:
            print(f"Client Fail: {e}")

# --- ADMIN COMMANDS ---
@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if not is_admin(event.sender_id): return
    refresh_targets()
    buttons = [
        [Button.inline("➕ Add Admin", data="add_admin_btn"), Button.inline("🎯 Set Target", data="set_target")],
        [Button.inline("🛑 Stop Target", data="stop_target")]
    ]
    await event.reply(f"👋 **Working!**\nTargets: `{len(TARGET_CACHE)}`\nStickers: `{len(TROLL_STICKERS)}`", buttons=buttons)

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if not is_admin(event.sender_id): return
    data = event.data.decode('utf-8')
    
    if data == "add_admin_btn":
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
                try: 
                    targets_collection.insert_one({"user_id": int(text)})
                    refresh_targets()
                    await event.reply(f"🎯 Set: {text}")
                    del user_states[event.sender_id]
                except: await event.reply("Invalid.")
            elif state['step'] == 'ask_remove_target_id':
                try: targets_collection.delete_one({"user_id": int(text)}); refresh_targets(); await event.reply("🛑 Stopped."); del user_states[event.sender_id]
                except: await event.reply("Invalid.")
            elif state['step'] in ['ask_number', 'ask_otp', 'ask_password']:
                await handle_login_steps(event, state)

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

if __name__ == '__main__':
    keep_alive()
    bot.loop.run_until_complete(start_all_clients())
    bot.run_until_disconnected()
    
