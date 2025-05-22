import os
import asyncio
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web
from aiogram.webhook.aiohttp_server import setup_application
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL", "https://incognito-bot.onrender.com")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret123456")
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

DB_NAME = "users.db"

async def create_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                gender TEXT,
                interested_in TEXT,
                photo_id TEXT,
                bio TEXT,
                location TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS likes (
                liker_id INTEGER,
                liked_id INTEGER,
                PRIMARY KEY (liker_id, liked_id)
            )
        """)
        await db.commit()

class ProfileForm(StatesGroup):
    gender = State()
    interested_in = State()
    photo = State()
    bio = State()
    location = State()

class Onboarding(StatesGroup):
    nickname = State()
    gender = State()
    interested_in = State()
    photo = State()
    bio = State()
    location = State()

@dp.message_handler(commands=['start'], state="*")
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Hey, gorgeous 😘 What should I call you here? (Type your nickname)")
    await Onboarding.nickname.set()

@dp.message_handler(state=Onboarding.nickname)
async def process_nickname(message: types.Message, state: FSMContext):
    nickname = message.text.strip()
    if len(nickname) > 20:
        await message.answer("That’s quite a long name! Keep it short & sweet, please.")
        return
    await state.update_data(nickname=nickname)

    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("👨 Man", "👩 Woman", "🏳️ Other")
    await message.answer("Lovely name! Now tell me, are you a 👨 Man, 👩 Woman, or something more intriguing? (Choose one)", reply_markup=kb)
    await Onboarding.gender.set()

@dp.message_handler(lambda m: m.text in ["👨 Man", "👩 Woman", "🏳️ Other"], state=Onboarding.gender)
async def process_gender(message: types.Message, state: FSMContext):
    gender = message.text
    await state.update_data(gender=gender)

    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("👩 Women", "👨 Men", "🔀 Both")
    await message.answer("Oooh, a lady of mystery! Who are you hoping to find here? 👩 Women, 👨 Men, or 🔀 Both?", reply_markup=kb)
    await Onboarding.interested_in.set()

@dp.message_handler(lambda m: m.text in ["👩 Women", "👨 Men", "🔀 Both"], state=Onboarding.interested_in)
async def process_interested_in(message: types.Message, state: FSMContext):
    interested_in = message.text
    await state.update_data(interested_in=interested_in)

    await message.answer("Perfect! Now, share a photo that'll make hearts skip a beat 💓 (Please send me a photo)", reply_markup=ReplyKeyboardRemove())
    await Onboarding.photo.set()

@dp.message_handler(content_types=types.ContentType.PHOTO, state=Onboarding.photo)
async def process_photo(message: types.Message, state: FSMContext):
    photo = message.photo[-1].file_id
    await state.update_data(photo_id=photo)

    await message.answer("Got it! Now, tell me something naughty in your bio 😏 (Keep it short and spicy!)")
    await Onboarding.bio.set()

@dp.message_handler(state=Onboarding.bio)
async def process_bio(message: types.Message, state: FSMContext):
    bio = message.text.strip()
    if len(bio) > 200:
        await message.answer("Whoa, too long! Keep it spicy but short, please.")
        return
    await state.update_data(bio=bio)

    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton("Share my location 📍", request_location=True))
    await message.answer("Almost done! Share your location discreetly so we can find local matches.", reply_markup=kb)
    await Onboarding.location.set()

@dp.message_handler(content_types=types.ContentType.LOCATION, state=Onboarding.location)
async def process_location(message: types.Message, state: FSMContext):
    location_str = f"{message.location.latitude},{message.location.longitude}"
    data = await state.get_data()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (user_id, gender, interested_in, photo_id, bio, location)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            message.from_user.id,
            data['gender'].replace("👨 Man", "Male").replace("👩 Woman", "Female").replace("🏳️ Other", "Other"),
            data['interested_in'].replace("👩 Women", "Women").replace("👨 Men", "Men").replace("🔀 Both", "Both"),
            data['photo_id'],
            data['bio'],
            location_str
        ))
        await db.commit()

    await state.finish()
    await message.answer("You’re all set, darling 🔥 Get ready to find your secret connections — use /find to start!", reply_markup=ReplyKeyboardRemove())

@dp.message_handler(commands=["find"])
async def find_matches(message: types.Message):
    user_id = message.from_user.id

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT gender, interested_in, location FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                await message.answer("Please create your profile using /start.")
                return

            gender, interested_in, location = user
            lat, lon = map(float, location.split(","))

        async with db.execute("SELECT liked_id FROM likes WHERE liker_id = ?", (user_id,)) as cursor:
            seen = [row[0] for row in await cursor.fetchall()]

        query = """
        SELECT user_id, gender, photo_id, bio FROM users
        WHERE user_id != ?
        AND gender IN (?, 'Both')
        AND interested_in IN (?, 'Both')
        AND user_id NOT IN ({})
        LIMIT 1
        """.format(",".join("?" * len(seen)) if seen else "0")

        args = [user_id, interested_in, gender] + seen
        async with db.execute(query, args) as cursor:
            match = await cursor.fetchone()

        if match:
            match_id, match_gender, photo_id, bio = match
            kb = InlineKeyboardMarkup()
            kb.add(
                InlineKeyboardButton("❤️ Like", callback_data=f"like:{match_id}"),
                InlineKeyboardButton("❌ Skip", callback_data=f"skip:{match_id}")
            )
            await bot.send_photo(
                message.chat.id,
                photo=photo_id,
                caption=f"{match_gender}\n\n{bio}",
                reply_markup=kb
            )
        else:
            await message.answer("No matches found right now. Try again later.")

@dp.callback_query_handler(lambda c: c.data.startswith(("like:", "skip:")))
async def handle_swipe(call: types.CallbackQuery):
    action, target_id = call.data.split(":")
    user_id = call.from_user.id
    target_id = int(target_id)

    async with aiosqlite.connect(DB_NAME) as db:
        if action == "like":
            await db.execute("INSERT OR IGNORE INTO likes (liker_id, liked_id) VALUES (?, ?)", (user_id, target_id))
            await db.commit()

            async with db.execute("SELECT 1 FROM likes WHERE liker_id = ? AND liked_id = ?", (target_id, user_id)) as cursor:
                if await cursor.fetchone():
                    await bot.send_message(user_id, "🔥 It's a match!")
                    await bot.send_message(target_id, "🔥 It's a match!")

        await call.message.delete()
        await find_matches(call.message)

async def on_startup(app):
    await create_db()
    await bot.set_webhook(f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}", secret_token=WEBHOOK_SECRET)

async def on_shutdown(app):
    await bot.delete_webhook()

async def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    setup_application(app, dp, bot=bot, path=WEBHOOK_PATH)
    return app

if __name__ == "__main__":
    web.run_app(main(), port=int(os.getenv("PORT", 5000)))
