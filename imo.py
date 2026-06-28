import asyncio
import json
import random
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# ================= НАСТРОЙКИ =================
TOKEN = "8937846413:AAEUeyU2RsJq6tCmZBYByRuvzf0CM8NEuWQ"

GROUP_ID = -1003876522272          # группа регистрации
SECOND_GROUP_ID = -1004375598061   # группа выдачи

SESSIONS_DIR = Path("sessions")
SESSIONS_DIR.mkdir(exist_ok=True)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================= ДОСТУП =================
def load_users():
    try:
        with open("users.json", "r") as f:
            return json.load(f)
    except:
        return {"admins": [], "users": []}

def is_admin(user_id):
    return user_id in load_users()["admins"]

def is_allowed(user_id):
    data = load_users()
    return user_id in data["admins"] or user_id in data["users"]

# ================= ЛОГИ =================
def log(text):
    with open("logs.txt", "a") as f:
        f.write(text + "\n")

# ================= СТАТА =================
def update_stats(user_id):
    try:
        with open("stats.json", "r") as f:
            stats = json.load(f)
    except:
        stats = {"total": 0, "users": {}}

    stats["total"] += 1
    uid = str(user_id)
    stats["users"][uid] = stats["users"].get(uid, 0) + 1

    with open("stats.json", "w") as f:
        json.dump(stats, f, indent=4)

# ================= FSM =================
class Form(StatesGroup):
    phone = State()
    code = State()

# ================= КНОПКИ =================
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Мои номера")],
            [KeyboardButton(text="➕ Регистрация")]
        ],
        resize_keyboard=True
    )

def request_code_kb(phone, user_id):
    """Кнопка в группе после получения номера"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📩 Запрос кода",
            callback_data=f"reqcode:{phone}:{user_id}"
        )]
    ])

def result_kb(phone, user_id):
    """Кнопки результата в группе после получения кода"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Номер успешно зарегистрирован",
                callback_data=f"regok:{phone}:{user_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Номер не зарегистрирован",
                callback_data=f"regfail:{phone}:{user_id}"
            )
        ]
    ])

# ================= СТАРТ =================
@dp.message(F.text == "/start")
async def start(msg: Message):
    if not is_allowed(msg.from_user.id):
        await msg.answer("❌ Нет доступа")
        return
    await msg.answer("✅ Бот готов", reply_markup=main_kb())

# ================= МОИ НОМЕРА =================
@dp.message(F.text == "📱 Мои номера")
async def my_numbers(msg: Message):
    if not is_allowed(msg.from_user.id):
        return

    numbers = []
    for file in SESSIONS_DIR.glob("*.json"):
        data = json.loads(file.read_text())
        if data.get("owner") == msg.from_user.id:
            numbers.append(data["phone"])

    await msg.answer("\n".join(numbers) if numbers else "📭 Пусто")

# ================= РЕГИСТРАЦИЯ — ШАГ 1: номер =================
@dp.message(F.text == "➕ Регистрация")
async def reg_start(msg: Message, state: FSMContext):
    if not is_allowed(msg.from_user.id):
        return
    await msg.answer("📱 Введи номер телефона:")
    await state.set_state(Form.phone)

@dp.message(Form.phone)
async def reg_phone(msg: Message, state: FSMContext):
    phone = msg.text.strip()
    user_id = msg.from_user.id

    await state.update_data(phone=phone)

    # Отправляем номер в группу с кнопкой «Запрос кода»
    await bot.send_message(
        GROUP_ID,
        f"📱 Новый номер на регистрацию\n"
        f"👤 User ID: {user_id}\n"
        f"📞 Номер: {phone}",
        reply_markup=request_code_kb(phone, user_id)
    )

    # Сообщаем пользователю
    await msg.answer("⏳ Номер взят в обработку")
    await state.set_state(Form.code)

# ================= РЕГИСТРАЦИЯ — ШАГ 2: код =================
@dp.message(Form.code)
async def reg_code(msg: Message, state: FSMContext):
    data = await state.get_data()
    phone = data["phone"]
    code = msg.text.strip()
    user_id = msg.from_user.id

    # Отправляем код в группу с кнопками результата
    await bot.send_message(
        GROUP_ID,
        f"🔑 Код подтверждения\n"
        f"👤 User ID: {user_id}\n"
        f"📞 Номер: {phone}\n"
        f"🔢 Код: {code}",
        reply_markup=result_kb(phone, user_id)
    )

    await msg.answer("⏳ Код отправлен на проверку")
    await state.clear()

# ================= CALLBACK: Запрос кода =================
@dp.callback_query(F.data.startswith("reqcode:"))
async def request_code_cb(callback: CallbackQuery):
    parts = callback.data.split(":")
    phone = parts[1]
    user_id = int(parts[2])

    # Просим пользователя прислать код
    await bot.send_message(
        user_id,
        f"📩 Введи код подтверждения, который пришёл на номер {phone}:"
    )

    await callback.answer("Запрос отправлен пользователю")
    await callback.message.edit_reply_markup(reply_markup=None)

# ================= CALLBACK: Успешная регистрация =================
@dp.callback_query(F.data.startswith("regok:"))
async def reg_ok(callback: CallbackQuery):
    parts = callback.data.split(":")
    phone = parts[1]
    user_id = int(parts[2])

    # Сохраняем сессию
    file = SESSIONS_DIR / f"{phone}.json"
    file.write_text(json.dumps({
        "phone": phone,
        "owner": user_id
    }))

    log(f"OK {phone} owner={user_id}")
    update_stats(user_id)

    await bot.send_message(user_id, f"✅ Номер {phone} успешно зарегистрирован!")
    await callback.answer("Отмечено как успешно")
    await callback.message.edit_reply_markup(reply_markup=None)

# ================= CALLBACK: Неудачная регистрация =================
@dp.callback_query(F.data.startswith("regfail:"))
async def reg_fail(callback: CallbackQuery):
    parts = callback.data.split(":")
    phone = parts[1]
    user_id = int(parts[2])

    log(f"FAIL {phone} owner={user_id}")

    await bot.send_message(user_id, f"❌ Номер {phone} не был зарегистрирован. Попробуй снова.")
    await callback.answer("Отмечено как неудача")
    await callback.message.edit_reply_markup(reply_markup=None)

# ================= ВЫДАЧА НОМЕРОВ (вторая группа) =================
@dp.message(F.chat.id == SECOND_GROUP_ID)
async def give_number(msg: Message):
    if not msg.text:
        return
    if not is_allowed(msg.from_user.id):
        return

    text = msg.text.lower()
    if text not in ["ном", "номер", "замена", "зм"]:
        return

    files = list(SESSIONS_DIR.glob("*.json"))
    if not files:
        await msg.answer("❌ Нет номеров")
        return

    file = random.choice(files)
    data = json.loads(file.read_text())
    phone = data["phone"]
    file.unlink()

    log(f"{msg.from_user.id} взял {phone}")
    update_stats(msg.from_user.id)

    await msg.answer(f"📱 {phone}")

# ================= ЛОГИ =================
@dp.message(F.text == "/logs")
async def logs(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    try:
        data = open("logs.txt").read()
        await msg.answer(data[-3000:] if data else "📭 пусто")
    except:
        await msg.answer("❌ нет логов")

# ================= СТАТА =================
@dp.message(F.text == "/stat")
async def stat(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    try:
        stats = json.load(open("stats.json"))
        text = f"📊 Всего: {stats['total']}\n\n"
        for u, c in stats["users"].items():
            text += f"{u}: {c}\n"
        await msg.answer(text)
    except:
        await msg.answer("❌ ошибка")

# ================= ЗАПУСК =================
async def main():
    print("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
