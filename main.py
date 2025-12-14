import os
import asyncio
import logging
from flask import Flask
from threading import Thread
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl.functions.messages import UpdatePinnedDialogRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from pymongo import MongoClient

# --- CONFIGURATION (Environment Variables) ---
# .env file se values lega ya Render ke Environment variables se
API_ID = int(os.environ.get("API_ID", "12345"))  # Default hata kar apna daalna
API_HASH = os.environ.get("API_HASH", "your_hash_here")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URI = os.environ.get("MONGO_URI", "your_mongodb_connection_string")
ADMIN_ID = 8389974605  # Aapka Fix Admin ID
STICKER_ID = os.environ.get("STICKER_ID", "CAACAgUAAxkBAA....") # Sticker File ID yahan daalein

# --- MONGODB CONNECTION ---
cluster = MongoClient(MONGO_URI)
db = cluster["TelegramBot"]
collection = db["sessions"]

# --- LOGGING ---
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

# --- FLASK SERVER (RENDER KEEP ALIVE) ---
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is Running and Connected to MongoDB!"

def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# --- MAIN BOT CLIENT ---
bot = TelegramClient('admin_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# --- LOGIN STATES (Account add karne ke liye) ---
# Ye temporary storage hai jab tak login process chal raha hai
login_states = {}

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    
    # MongoDB se count nikalo
    total_acc = collection.count_documents({})
    
    buttons = [[Button.inline(f"🤖 Total Accounts: {total_acc}", data="stats")]]
    await event.reply(
        f"👋 **Namaste Admin!**\n\n"
        f"Ye Bot MongoDB se connected hai.\n"
        f"🆔 Admin ID: `{ADMIN_ID}`\n\n"
        f"**Commands:**\n"
        f"1. `/add` - Naya account add karne ke liye (Login Flow)\n"
        f"2. Link bhejo - Saare accounts join honge + Pin + Sticker.\n",
        buttons=buttons
    )

@bot.on(events.CallbackQuery(data="stats"))
async def stats_handler(event):
    total_acc = collection.count_documents({})
    await event.answer(f"Database mein {total_acc} accounts hain.", alert=True)

# --- ACCOUNT ADDING FLOW (LOGIN SYSTEM) ---

@bot.on(events.NewMessage(pattern='/add'))
async def start_login(event):
    if event.sender_id != ADMIN_ID: return
    
    # State set karein
    login_states[event.chat_id] = {'step': 'ask_number'}
    await event.reply("📞 **Naya Account Add Karna Hai?**\n\nKripya phone number bhejein (Country code ke sath, e.g., +919876543210).")

@bot.on(events.NewMessage())
async def message_handler(event):
    # Agar message command hai to ignore karein (unless it's part of flow)
    if event.text.startswith('/') and event.text != '/cancel':
        pass
    elif event.sender_id == ADMIN_ID and event.chat_id in login_states:
        await handle_login_flow(event)
    elif event.sender_id == ADMIN_ID and ("t.me/" in event.text):
        await handle_join_task(event)

async def handle_login_flow(event):
    state = login_states[event.chat_id]
    text = event.text.strip()
    
    try:
        if state['step'] == 'ask_number':
            # Step 1: Number mila, OTP bhejo
            phone = text
            temp_client = TelegramClient(StringSession(), API_ID, API_HASH)
            await temp_client.connect()
            
            if not await temp_client.is_user_authorized():
                try:
                    send_code = await temp_client.send_code_request(phone)
                    login_states[event.chat_id] = {
                        'step': 'ask_otp',
                        'phone': phone,
                        'client': temp_client,
                        'phone_code_hash': send_code.phone_code_hash
                    }
                    await event.reply("📨 **OTP Bheja gaya!**\n\nTelegram OTP code enter karein (Space ke saath, e.g., `1 2 3 4 5` taaki bot command na samjhe).")
                except Exception as e:
                    await event.reply(f"❌ Error: {str(e)}\nTry `/add` again.")
                    del login_states[event.chat_id]
            else:
                await event.reply("Ye number pehle se authorized hai.")
        
        elif state['step'] == 'ask_otp':
            # Step 2: OTP mila, login try karo
            otp_code = text.replace(" ", "")
            temp_client = state['client']
            phone = state['phone']
            hash_code = state['phone_code_hash']
            
            try:
                await temp_client.sign_in(phone, otp_code, phone_code_hash=hash_code)
                # Agar login ho gaya
                session_string = StringSession.save(temp_client.session)
                save_to_mongo(phone, session_string)
                await event.reply(f"✅ **Login Successful!**\nAccount `{phone}` MongoDB mein save ho gaya.")
                await temp_client.disconnect()
                del login_states[event.chat_id]
            
            except SessionPasswordNeededError:
                # 2FA laga hai
                login_states[event.chat_id]['step'] = 'ask_password'
                await event.reply("🔐 **Two-Step Verification On Hai.**\nKripya apna password bhejein.")
            
            except Exception as e:
                await event.reply(f"❌ OTP Error: {str(e)}\nTry `/add` again.")
                del login_states[event.chat_id]

        elif state['step'] == 'ask_password':
            # Step 3: Password mila, finalize login
            password = text
            temp_client = state['client']
            try:
                await temp_client.sign_in(password=password)
                session_string = StringSession.save(temp_client.session)
                save_to_mongo(state['phone'], session_string)
                await event.reply(f"✅ **Password Accepted!**\nAccount `{state['phone']}` save ho gaya.")
                await temp_client.disconnect()
                del login_states[event.chat_id]
            except Exception as e:
                await event.reply(f"❌ Password Error: {str(e)}")
                del login_states[event.chat_id]

    except Exception as e:
        await event.reply(f"Fatal Error: {e}")
        if event.chat_id in login_states:
            del login_states[event.chat_id]

def save_to_mongo(phone, session):
    # Check if exists
    if not collection.find_one({"phone": phone}):
        collection.insert_one({"phone": phone, "session": session})

# --- MASS JOIN & PIN LOGIC ---
async def handle_join_task(event):
    link = event.text.strip()
    sessions_cursor = collection.find({})
    sessions = list(sessions_cursor)
    
    if not sessions:
        await event.reply("Database mein koi account nahi hai. `/add` use karein.")
        return

    status_msg = await event.reply(f"🚀 **Starting Task...**\nLink: {link}\nTotal Accounts: {len(sessions)}")
    
    success = 0
    failed = 0
    
    for user_data in sessions:
        try:
            client = TelegramClient(StringSession(user_data['session']), API_ID, API_HASH)
            await client.connect()
            
            # Join Logic
            try:
                if "+" in link or "joinchat" in link:
                    # Private Link
                    hash_key = link.split("+")[1]
                    await client(ImportChatInviteRequest(hash_key))
                else:
                    # Public Link
                    username = link.split("/")[-1]
                    await client.join_chat(username)
            except Exception as e:
                # Agar already joined hai ya request link hai to error aayega, but process aage badhega
                pass 
            
            # Entity get karo (Group ID)
            try:
                entity = await client.get_entity(link)
                
                # 1. Pin Chat
                await client(UpdatePinnedDialogRequest(peer=entity, pinned=True))
                
                # 2. Send Sticker
                if STICKER_ID:
                    # Sticker ID ya text bhejein
                    await client.send_file(entity, STICKER_ID)
                    # await client.send_message(entity, "Hello") # Alternative
                
                success += 1
            except Exception as e:
                print(f"Action Failed for {user_data['phone']}: {e}")
                
            await client.disconnect()
            await asyncio.sleep(2) # Floodwait se bachne ke liye delay
            
        except Exception as e:
            failed += 1
            print(f"Client Error: {e}")

    await status_msg.edit(f"✅ **Task Complete!**\n\nJoined & Pinned: {success}\nFailed/Already: {failed}")

if __name__ == '__main__':
    keep_alive()
    bot.run_until_disconnected()

