import os
import asyncio
import logging
from flask import Flask
from threading import Thread
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, 
    FloodWaitError, 
    UserAlreadyParticipantError, 
    UserBannedInChannelError,
    AuthKeyDuplicatedError
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

# --- MONGODB ---
cluster = MongoClient(MONGO_URI)
db = cluster["TelegramBot"]
sessions_collection = db["sessions"]
admins_collection = db["admins"]

# --- LOGGING ---
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

# --- FLASK ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is Running!"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
def keep_alive(): t = Thread(target=run_web); t.start()

# --- BOT CLIENT ---
bot = TelegramClient('admin_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
user_states = {} 

def is_admin(user_id):
    if user_id == OWNER_ID: return True
    if admins_collection.find_one({"user_id": user_id}): return True
    return False

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if not is_admin(event.sender_id): return
    total_acc = sessions_collection.count_documents({})
    buttons = [
        [Button.inline(f"🤖 Total Accounts: {total_acc}", data="stats")],
        [Button.inline("➕ Add Admin", data="add_admin_btn")]
    ]
    await event.reply(f"👋 **Namaste Boss!**\n\n🆔 Owner: `{OWNER_ID}`\n\nAb agar join fail hoga to main **Error Reason** bataunga.", buttons=buttons)

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if not is_admin(event.sender_id): return
    data = event.data.decode('utf-8')
    if data == "stats":
        total = sessions_collection.count_documents({})
        await event.answer(f"Accounts: {total}", alert=True)
    elif data == "add_admin_btn":
        user_states[event.sender_id] = {'step': 'ask_admin_id'}
        await event.respond("User ID bhejein jise Admin banana hai.")

@bot.on(events.NewMessage(pattern='/add'))
async def add_command(event):
    if not is_admin(event.sender_id): return
    user_states[event.sender_id] = {'step': 'ask_number'}
    await event.reply("📞 **Phone Number Bhejein** (+91...)")

@bot.on(events.NewMessage())
async def message_handler(event):
    if event.text.startswith('/') and event.text != '/cancel': pass
    elif is_admin(event.sender_id):
        if event.sender_id in user_states:
            state = user_states[event.sender_id]
            if state['step'] == 'ask_admin_id':
                try:
                    nid = int(event.text.strip())
                    admins_collection.insert_one({"user_id": nid})
                    await event.reply("✅ Admin Added.")
                    del user_states[event.sender_id]
                except: await event.reply("Invalid ID.")
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
                await event.reply("📨 **OTP Bhejein** (Space ke sath: 1 2 3 4 5)")
            else: await event.reply("Already Added."); del user_states[chat_id]
        elif state['step'] == 'ask_otp':
            otp = text.replace(" ", "")
            try:
                await state['client'].sign_in(state['phone'], otp, phone_code_hash=state['hash'])
                save_session(state['phone'], state['client'])
                await event.reply("✅ **Saved!**"); await state['client'].disconnect(); del user_states[chat_id]
            except SessionPasswordNeededError:
                user_states[chat_id]['step'] = 'ask_password'; await event.reply("🔐 **Password Bhejein**")
        elif state['step'] == 'ask_password':
            await state['client'].sign_in(password=text)
            save_session(state['phone'], state['client'])
            await event.reply("✅ **Saved!**"); await state['client'].disconnect(); del user_states[chat_id]
    except Exception as e: await event.reply(f"❌ Login Error: {e}"); 

def save_session(phone, client):
    session = StringSession.save(client.session)
    if not sessions_collection.find_one({"phone": phone}):
        sessions_collection.insert_one({"phone": phone, "session": session})

# --- IMPROVED JOIN LOGIC WITH ERROR REPORTING ---
async def handle_join_task(event):
    link = event.text.strip()
    sessions = list(sessions_collection.find({}))
    if not sessions: await event.reply("❌ Database Empty."); return

    status_msg = await event.reply(f"🚀 **Processing...**\nTarget: {link}")
    
    success = 0
    failed = 0
    error_log = [] # Yahan hum errors save karenge

    for user_data in sessions:
        phone = user_data.get('phone', 'Unknown')
        client = TelegramClient(StringSession(user_data['session']), API_ID, API_HASH)
        
        try:
            await client.connect()
            
            if not await client.is_user_authorized():
                failed += 1
                error_log.append(f"❌ {phone}: Session Expired/Auth Fail")
                # Optional: Delete invalid session
                # sessions_collection.delete_one({"phone": phone})
                await client.disconnect()
                continue

            joined = False
            try:
                if "+" in link or "joinchat" in link:
                    hash_key = link.split("+")[1]
                    await client(ImportChatInviteRequest(hash_key))
                else:
                    username = link.split("/")[-1]
                    await client.join_chat(username)
                joined = True
            except UserAlreadyParticipantError:
                joined = True
            except Exception as e:
                failed += 1
                error_log.append(f"❌ {phone} Join Error: {str(e)}")

            if joined:
                try:
                    entity = await client.get_entity(link)
                    await client(ToggleDialogPinRequest(peer=entity, pinned=True))
                    if STICKER_ID: await client.send_file(entity, STICKER_ID)
                    success += 1
                except Exception as e:
                    # Join ho gaya par pin fail hua, ise success hi maano
                    success += 1
                    error_log.append(f"⚠️ {phone} Pin/Sticker Error: {str(e)}")

            await client.disconnect()
            await asyncio.sleep(2)
            
        except Exception as e:
            failed += 1
            error_log.append(f"❌ {phone} Client Error: {str(e)}")

    # Final Report
    report = f"✅ **Task Done!**\n\nSuccessful: {success}\nFailed: {failed}\n\n**🛑 Error Details:**\n"
    if error_log:
        report += "\n".join(error_log[:10]) # Sirf top 10 errors dikhayega taaki message lamba na ho
    else:
        report += "None"

    await status_msg.edit(report)

if __name__ == '__main__':
    keep_alive()
    bot.run_until_disconnected()
        
