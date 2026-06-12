#!/usr/bin/env python3
"""
IMO Telegram Bot - Управление аккаунтами IMO через Telegram
Запуск: python bot.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, 
    ReplyKeyboardMarkup, 
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from loguru import logger
from dotenv import load_dotenv

from imo_client import ImoClient, ImoSession, ImoDevice

# Загружаем переменные окружения
load_dotenv()

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8937846413:AAEuMZeThi4ucm0jBIg210fhi9u1GY1SstQ"  # Ваш токен
ADMIN_IDS = [105635005]  # Ваш Telegram ID
SESSIONS_DIR = Path("sessions")
SESSIONS_DIR.mkdir(exist_ok=True)

# --- СОСТОЯНИЯ БОТА ---
class Form(StatesGroup):
    # Регистрация нового аккаунта
    waiting_for_phone = State()          # Ждём номер телефона
    waiting_for_sms_code = State()       # Ждём SMS код для регистрации
    waiting_for_2fa_code = State()       # Ждём 2FA код
    
    # Вход в существующий аккаунт
    waiting_for_login_phone = State()    # Ждём номер для входа
    waiting_for_login_code = State()     # Ждём код для входа

# --- ХРАНИЛИЩЕ АКТИВНЫХ КЛИЕНТОВ ---
# В реальном проекте замените на Redis
active_clients: Dict[str, ImoClient] = {}
# Для связи "телефон -> future" при ожидании кода
pending_futures: Dict[str, asyncio.Future] = {}

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- ГЛАВНОЕ МЕНЮ ---
def get_main_keyboard():
    """Клавиатура главного меню"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Зарегистрировать аккаунт")],
            [KeyboardButton(text="🔑 Войти в аккаунт")],
            [KeyboardButton(text="📋 Мои аккаунты")],
            [KeyboardButton(text="🔄 Обновить сессию")],
            [KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True
    )

# --- КОМАНДЫ БОТА ---

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return
    
    await message.answer(
        "👋 <b>IMO Telegram Bot</b>\n\n"
        "Я помогу управлять аккаунтами IMO:\n"
        "• Регистрировать новые аккаунты\n"
        "• Входить в существующие\n"
        "• Хранить сессии и автоматически переподключаться\n"
        "• Присылать коды подтверждения\n\n"
        "Выберите действие в меню:",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

@dp.message(F.text == "📱 Зарегистрировать аккаунт")
async def register_start(message: Message, state: FSMContext):
    """Начало регистрации нового аккаунта"""
    await message.answer(
        "📱 <b>Регистрация нового аккаунта IMO</b>\n\n"
        "Отправьте номер телефона в международном формате.\n"
        "Пример: <code>79123456789</code>\n\n"
        "На этот номер придёт SMS с кодом.",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await state.set_state(Form.waiting_for_phone)

@dp.message(Form.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
    """Обработка номера телефона для регистрации"""
    phone = message.text.strip().replace("+", "").replace(" ", "")
    
    # Простая валидация
    if not phone.isdigit() or len(phone) < 10:
        await message.answer("❌ Неверный формат. Отправьте номер цифрами, например: 79123456789")
        return
    
    await message.answer(f"⏳ Запрашиваю SMS-код для номера <code>{phone}</code>...", parse_mode="HTML")
    
    # Создаём сессию и клиент IMO
    session = ImoSession(phone=phone)
    client = ImoClient(session, code_callback=None)  # callback установим ниже
    
    # Создаём Future для ожидания кода
    future = asyncio.get_event_loop().create_future()
    pending_futures[phone] = future
    
    # Сохраняем данные в состоянии
    await state.update_data(phone=phone)
    await state.set_state(Form.waiting_for_sms_code)
    
    # Запрашиваем код (в фоне)
    try:
        # В реальном клиенте здесь вызов await client.request_sms_code(phone)
        # Пока эмулируем запрос
        await asyncio.sleep(2)
        await message.answer(
            f"📩 <b>SMS отправлена на номер {phone}</b>\n\n"
            f"Дождитесь код и отправьте его сюда.\n"
            f"Обычно код приходит в течение 30 секунд.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка запроса SMS: {e}")
        await message.answer(f"❌ Ошибка при запросе SMS: {e}")
        await state.clear()
        pending_futures.pop(phone, None)

@dp.message(Form.waiting_for_sms_code)
async def process_sms_code(message: Message, state: FSMContext):
    """Обработка SMS кода при регистрации"""
    code = message.text.strip()
    
    if not code.isdigit() or len(code) < 4:
        await message.answer("⚠️ Код должен содержать только цифры (обычно 4-6 знаков)")
        return
    
    data = await state.get_data()
    phone = data.get("phone")
    
    await message.answer(f"✅ Код принят! Завершаю регистрацию для <code>{phone}</code>...", parse_mode="HTML")
    
    # Завершаем ожидание кода
    if phone in pending_futures:
        pending_futures[phone].set_result(code)
        pending_futures.pop(phone, None)
    
    # В реальном клиенте: await client.verify_code_and_login(phone, code)
    await asyncio.sleep(2)
    
    # Сохраняем сессию
    session_file = SESSIONS_DIR / f"{phone}.json"
    session_data = {
        "phone": phone,
        "uid": "generated_uid_12345",  # Придёт от IMO
        "access_token": "generated_token",  # Придёт от IMO
        "created_at": datetime.now().isoformat(),
    }
    with open(session_file, "w") as f:
        json.dump(session_data, f, indent=2)
    
    await message.answer(
        f"🎉 <b>Аккаунт успешно зарегистрирован!</b>\n\n"
        f"📱 Телефон: <code>{phone}</code>\n"
        f"🆔 UID: <code>{session_data['uid']}</code>\n"
        f"📂 Сессия сохранена: <code>{phone}.json</code>\n\n"
        f"Теперь вы можете войти в этот аккаунт в любое время.",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )
    
    await state.clear()

@dp.message(F.text == "🔑 Войти в аккаунт")
async def login_start(message: Message, state: FSMContext):
    """Начало входа в существующий аккаунт"""
    await message.answer(
        "🔑 <b>Вход в аккаунт IMO</b>\n\n"
        "Отправьте номер телефона аккаунта.\n"
        "Если сессия сохранена, попробую войти без кода.",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await state.set_state(Form.waiting_for_login_phone)

@dp.message(Form.waiting_for_login_phone)
async def process_login_phone(message: Message, state: FSMContext):
    """Обработка номера телефона для входа"""
    phone = message.text.strip().replace("+", "").replace(" ", "")
    
    if not phone.isdigit() or len(phone) < 10:
        await message.answer("❌ Неверный формат. Отправьте номер цифрами")
        return
    
    session_file = SESSIONS_DIR / f"{phone}.json"
    
    if session_file.exists():
        # Пробуем войти с сохранённой сессией
        await message.answer(f"🔍 Найдена сохранённая сессия для <code>{phone}</code>. Пробую войти...", parse_mode="HTML")
        
        with open(session_file) as f:
            session_data = json.load(f)
        
        # В реальном клиенте: client.load_session() и client.check_session_alive()
        await asyncio.sleep(2)
        
        # Если сессия протухла - запрашиваем код
        need_new_code = True  # В реальности: if not await client.check_session_alive():
        
        if need_new_code:
            await message.answer(
                "⚠️ Сессия устарела. Запрашиваю новый код подтверждения...",
                parse_mode="HTML"
            )
            
            await state.update_data(phone=phone)
            await state.set_state(Form.waiting_for_login_code)
            
            # Запрашиваем новый код
            await asyncio.sleep(2)
            await message.answer(
                f"📩 Код отправлен на номер <code>{phone}</code>. Отправьте его сюда.",
                parse_mode="HTML"
            )
        else:
            await message.answer(
                f"✅ Успешный вход в аккаунт <code>{phone}</code>!",
                reply_markup=get_main_keyboard(),
                parse_mode="HTML"
            )
            await state.clear()
    else:
        # Сессия не найдена, регистрируем заново
        await message.answer(
            f"❌ Сессия для <code>{phone}</code> не найдена.\n"
            f"Сначала зарегистрируйте аккаунт через меню «📱 Зарегистрировать аккаунт»",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        await state.clear()

@dp.message(Form.waiting_for_login_code)
async def process_login_code(message: Message, state: FSMContext):
    """Обработка кода при входе"""
    code = message.text.strip()
    
    if not code.isdigit() or len(code) < 4:
        await message.answer("⚠️ Код должен содержать только цифры")
        return
    
    data = await state.get_data()
    phone = data.get("phone")
    
    await message.answer(f"✅ Код принят! Выполняю вход в аккаунт <code>{phone}</code>...", parse_mode="HTML")
    
    # В реальном клиенте: await client.verify_code_and_login(phone, code)
    await asyncio.sleep(2)
    
    # Обновляем файл сессии с новым токеном
    session_file = SESSIONS_DIR / f"{phone}.json"
    session_data = {
        "phone": phone,
        "uid": "updated_uid_12345",
        "access_token": "new_token_after_relogin",
        "updated_at": datetime.now().isoformat(),
    }
    with open(session_file, "w") as f:
        json.dump(session_data, f, indent=2)
    
    await message.answer(
        f"🎉 <b>Вход выполнен успешно!</b>\n"
        f"📱 Телефон: <code>{phone}</code>\n"
        f"🔄 Сессия обновлена.",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )
    
    await state.clear()

@dp.message(F.text == "📋 Мои аккаунты")
async def list_accounts(message: Message):
    """Показывает список сохранённых аккаунтов"""
    sessions = list(SESSIONS_DIR.glob("*.json"))
    
    if not sessions:
        await message.answer(
            "📋 <b>Нет сохранённых аккаунтов</b>\n\n"
            "Зарегистрируйте новый через меню «📱 Зарегистрировать аккаунт»",
            parse_mode="HTML"
        )
        return
    
    text = "📋 <b>Сохранённые аккаунты:</b>\n\n"
    
    for i, session_file in enumerate(sessions, 1):
        with open(session_file) as f:
            data = json.load(f)
        
        phone = data.get("phone", "Неизвестно")
        uid = data.get("uid", "Нет UID")
        created = data.get("created_at", "Неизвестно")[:10]
        
        text += f"{i}. 📱 <code>{phone}</code>\n"
        text += f"   🆔 UID: <code>{uid}</code>\n"
        text += f"   📅 Создан: {created}\n\n"
    
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "🔄 Обновить сессию")
async def refresh_session_prompt(message: Message, state: FSMContext):
    """Обновление сессии существующего аккаунта"""
    await message.answer(
        "🔄 <b>Обновление сессии</b>\n\n"
        "Отправьте номер телефона аккаунта, для которого нужно обновить сессию.",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await state.set_state(Form.waiting_for_login_phone)

@dp.message(F.text == "❓ Помощь")
async def help_command(message: Message):
    """Помощь по боту"""
    await message.answer(
        "❓ <b>IMO Telegram Bot - Помощь</b>\n\n"
        "<b>Регистрация:</b>\n"
        "1. Нажмите «📱 Зарегистрировать аккаунт»\n"
        "2. Отправьте номер телефона\n"
        "3. Дождитесь SMS и отправьте код боту\n"
        "4. Аккаунт создан, сессия сохранена\n\n"
        "<b>Вход:</b>\n"
        "1. Нажмите «🔑 Войти в аккаунт»\n"
        "2. Отправьте номер телефона\n"
        "3. Если сессия жива - войдёт сразу\n"
        "4. Если нет - запросит новый код\n\n"
        "<b>Команды:</b>\n"
        "/start - Главное меню\n"
        "/accounts - Список аккаунтов\n"
        "/help - Эта справка",
        parse_mode="HTML"
    )

@dp.message(Command("accounts"))
async def cmd_accounts(message: Message):
    """Команда /accounts - список аккаунтов"""
    await list_accounts(message)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Команда /help"""
    await help_command(message)

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активных действий для отмены.")
        return
    
    await state.clear()
    await message.answer(
        "❌ Действие отменено.",
        reply_markup=get_main_keyboard()
    )

# --- ОБРАБОТЧИК ДЛЯ ПРЯМЫХ КОДОВ (КОГДА БОТ САМ ЗАПРАШИВАЕТ) ---
# Этот хендлер срабатывает, когда IMO прислал код подтверждения 
# при повторном входе, а бот пересылает его вам

async def imo_code_callback(phone: str) -> str:
    """
    Callback для IMO клиента. 
    Вызывается когда IMO запрашивает код.
    Отправляет сообщение админу и ждёт ответа.
    """
    for admin_id in ADMIN_IDS:
        await bot.send_message(
            admin_id,
            f"🔔 <b>IMO запросил код подтверждения</b>\n\n"
            f"📱 Номер: <code>{phone}</code>\n\n"
            f"Отправьте код ответным сообщением на это уведомление.",
            parse_mode="HTML"
        )
    
    # Создаём Future и ждём
    future = asyncio.get_event_loop().create_future()
    pending_futures[phone] = future
    
    try:
        code = await asyncio.wait_for(future, timeout=120)  # Ждём 2 минуты
        return code
    except asyncio.TimeoutError:
        logger.warning(f"Таймаут ожидания кода для {phone}")
        return None

# --- ЗАПУСК БОТА ---

async def main():
    """Запуск бота"""
    logger.info("Запуск IMO Telegram Bot...")
    
    # Проверяем наличие токена
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("Ошибка: Укажите BOT_TOKEN в файле .env")
        sys.exit(1)
    
    # Проверяем папку с сессиями
    logger.info(f"Папка сессий: {SESSIONS_DIR.absolute()}")
    existing_sessions = len(list(SESSIONS_DIR.glob("*.json")))
    logger.info(f"Найдено сохранённых сессий: {existing_sessions}")
    
    # Запускаем поллинг
    logger.info("Бот запущен. Ожидаю сообщения...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Настройка логирования
    logger.add(
        "logs/bot_{time}.log",
        rotation="10 MB",
        retention="7 days",
        level="INFO"
    )
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")