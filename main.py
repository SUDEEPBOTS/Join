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
    UserBannedInChannelError
)
from telethon.tl.functions.messages import ToggleDialogPinRequest, ImportChatInviteRequest
from pymongo import MongoClient

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "12345")) 
API_HASH = os.environ.get("API_HASH", "your_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URI = os.environ.get("MONGO_URI", "your_mongo_url")
OWNER_ID = 8389974605  # Ye ID hamesha Admin rahegi
STICKER_ID = os.environ.get("STICKER_ID", None)

# --- MONGODB CONNECTION ---
cluster = MongoClient(MONGO_URI)
db = cluster["TelegramBot"]
sessions_collection = db["sessions"]
admins_collection = db["admins"]

# --- LOGGING ---
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

# --- FLASK SERVER ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is Running!"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
def keep_alive(): t = Thread(target=run_web); t.start()

# --- MAIN BOT CLIENT ---
bot = TelegramClient('admin_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# --- STATE MANAGEMENT ---
user_states = {} # Stores current step for users (login or add admin)

# --- HELPER: CHECK ADMIN ---
def is_admin(user_id):
    if user_id == OWNER_ID: return True
    if admins_collection.find_one({"user_id": user_id}): return True
    return False

# --- START COMMAND ---
@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if not is_admin(event.sender_id): return
    
    total_acc = sessions_collection.count_documents({})
    
    buttons = [
        [Button.inline(f"🤖 Total Accounts: {total_acc}", data="stats")],
        [Button.inline("➕ Add Admin", data="add_admin_btn")]
    ]
    
    await event.reply(
        f"👋 **Namaste Boss!**\n\n"
        f"Ye Bot MongoDB se connected hai.\n"
        f"👑 Owner ID: `{OWNER_ID}`\n\n"
        f"**Commands:**\n"
        f"1. `/add` - Naya account add karne ke liye\n"
        f"2. Link bhejo - Join + Pin + Sticker process start hoga.\n",
        buttons=buttons
    )

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if not is_admin(event.sender_id): return
    data = event.data.decode('utf-8')
    
    if data == "stats":
        total_acc = sessions_collection.count_documents({})
        total_admins = admins_collection.count_documents({}) + 1
        await event.answer(f"Accounts: {total_acc}\nAdmins: {total_admins}", alert=True)
        
    elif data == "add_admin_btn":
        user_states[event.sender_id] = {'step': 'ask_admin_id'}
        await event.respond("👤 **Naya Admin Banana Hai?**\n\nJis user ko admin banana hai, uski **Telegram User ID** bhejein (Numbers only).")

# --- ACCOUNT ADDING & ADMIN ADDING FLOW ---
@bot.on(events.NewMessage(pattern='/add'))
async def add_command(event):
    if not is_admin(event.sender_id): return
    user_states[event.sender_id] = {'step': 'ask_number'}
    await event.reply("📞 **Phone Number Bhejein**\n(Country code ke sath, e.g., +919876543210).")

@bot.on(events.NewMessage())
async def message_handler(event):
    # Ignore commands unless in a flow
    if event.text.startswith('/') and event.text != '/cancel': pass
    
    # Check if user is admin
    elif is_admin(event.sender_id):
        
        # Check if user is in a state (Adding Account or Adding Admin)
        if event.sender_id in user_states:
            state = user_states[event.sender_id]
            
            # --- ADD ADMIN FLOW ---
            if state['step'] == 'ask_admin_id':
                try:
                    new_admin_id = int(event.text.strip())
                    if not admins_collection.find_one({"user_id": new_admin_id}):
                        admins_collection.insert_one({"user_id": new_admin_id})
                        await event.reply(f"✅ **Success!**\nUser ID `{new_admin_id}` ab Admin hai.")
                    else:
                        await event.reply("⚠️ Ye user pehle se admin hai.")
                    del user_states[event.sender_id]
                except ValueError:
                    await event.reply("❌ Invalid ID! Sirf number bhejein.")
            
            # --- LOGIN FLOW (Add Account) ---
            elif state['step'] == 'ask_number':
                await handle_login_steps(event, state)
            elif state['step'] == 'ask_otp':
                await handle_login_steps(event, state)
            elif state['step'] == 'ask_password':
                await handle_login_steps(event, state)
        
        # --- JOIN TASK FLOW (If sending a link) ---
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
                user_states[chat_id] = {
                    'step': 'ask_otp', 'phone': phone, 'client': temp_client, 'hash': send_code.phone_code_hash
                }
                await event.reply("📨 **OTP Code Bhejein** (Space ke sath, e.g., `1 2 3 4 5`)")
            else:
                await event.reply("⚠️ Ye number pehle se added hai.")
                del user_states[chat_id]

        elif state['step'] == 'ask_otp':
            otp = text.replace(" ", "")
            try:
                await state['client'].sign_in(state['phone'], otp, phone_code_hash=state['hash'])
                save_session(state['phone'], state['client'])
                await event.reply(f"✅ **Account `{state['phone']}` Saved!**")
                await state['client'].disconnect()
                del user_states[chat_id]
            except SessionPasswordNeededError:
                user_states[chat_id]['step'] = 'ask_password'
                await event.reply("🔐 **2FA Password Bhejein**")

        elif state['step'] == 'ask_password':
            try:
                await state['client'].sign_in(password=text)
                save_session(state['phone'], state['client'])
                await event.reply(f"✅ **Account `{state['phone']}` Saved!**")
                await state['client'].disconnect()
                del user_states[chat_id]
            except Exception as e:
                await event.reply(f"❌ Error: {e}")

    except Exception as e:
        await event.reply(f"❌ Error: {e}")
        if chat_id in user_states: del user_states[chat_id]

def save_session(phone, client):
    session = StringSession.save(client.session)
    if not sessions_collection.find_one({"phone": phone}):
        sessions_collection.insert_one({"phone": phone, "session": session})

# --- JOIN & PIN LOGIC (Fixed) ---
async def handle_join_task(event):
    link = event.text.strip()
    sessions = list(sessions_collection.find({}))
    
    if not sessions:
        await event.reply("❌ Database khali hai.")
        return

    status_msg = await event.reply(f"🚀 **Starting...**\nTarget: `{link}`\nAccounts: {len(sessions)}")
    
    success = 0
    failed = 0
    
    for user_data in sessions:
        client = TelegramClient(StringSession(user_data['session']), API_ID, API_HASH)
        try:
            await client.connect()
            
            # Check if session is alive
            if not await client.is_user_authorized():
                print(f"Dead Session: {user_data['phone']}")
                failed += 1
                await client.disconnect()
                continue

            # 1. Try to Join
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
                joined = True # Already in group is considered success
            except Exception as e:
                print(f"Join Failed for {user_data['phone']}: {e}")
                failed += 1
            
            # 2. If Joined, Pin & Sticker
            if joined:
                try:
                    entity = await client.get_entity(link)
                    await client(ToggleDialogPinRequest(peer=entity, pinned=True))
                    if STICKER_ID:
                        await client.send_file(entity, STICKER_ID)
                    success += 1
                except Exception as e:
                    print(f"Pin/Sticker Failed: {e}")
                    # Count as success because join worked
                    success += 1 

            await client.disconnect()
            await asyncio.sleep(2) # Delay to prevent flood
            
        except Exception as e:
            print(f"Client Error: {e}")
            failed += 1

    await status_msg.edit(f"✅ **Task Done!**\n\nSuccessful Joins: {success}\nFailed: {failed}")

if __name__ == '__main__':
    keep_alive()
    bot.run_until_disconnected()
            
