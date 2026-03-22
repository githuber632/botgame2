import asyncio
import logging
import sys
import json
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.filters import Command, CommandStart, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.exceptions import TelegramNetworkError

# --- Configuration ---
def load_config():
    with open('config.json', 'r') as f:
        return json.load(f)

config = load_config()
BOT_TOKEN = config['BOT_TOKEN']
GROUP_ID = config['GROUP_ID']
ADMIN_IDS = config['ADMIN_IDS']

# --- Database ---
class JSONDatabase:
    def __init__(self, db_path='database.json'):
        self.db_path = db_path
        self.data = self._load_data()

    def _load_data(self):
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"users": {}}

    def _save_data(self):
        with open(self.db_path, 'w') as f:
            json.dump(self.data, f, indent=4)

    def add_user(self, telegram_id: int, username: str, full_name: str):
        user_id_str = str(telegram_id)
        if user_id_str not in self.data['users']:
            self.data['users'][user_id_str] = {
                "username": username,
                "full_name": full_name,
                "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "is_banned": False,
                "is_admin": False,
                "submission_count": 0,
                "last_nickname": None,
                "last_game_id": None
            }
            self._save_data()
            return True
        # Если пользователь есть, но у него нет новых полей (миграция на лету)
        else:
            user = self.data['users'][user_id_str]
            updated = False
            if "submission_count" not in user:
                user["submission_count"] = 0
                updated = True
            if "last_nickname" not in user:
                user["last_nickname"] = None
                updated = True
            if "last_game_id" not in user:
                user["last_game_id"] = None
                updated = True
            if updated:
                self._save_data()
        return False

    def update_user_submission(self, telegram_id: int, nickname: str, game_id: str):
        user_id_str = str(telegram_id)
        if user_id_str in self.data['users']:
            user = self.data['users'][user_id_str]
            user["submission_count"] += 1
            user["last_nickname"] = nickname
            user["last_game_id"] = game_id
            self._save_data()

    def get_user(self, telegram_id: int):
        return self.data['users'].get(str(telegram_id))

    def get_all_users(self):
        return self.data['users']

    def set_ban_status(self, telegram_id: int, is_banned: bool):
        user_id_str = str(telegram_id)
        if user_id_str in self.data['users']:
            self.data['users'][user_id_str]['is_banned'] = is_banned
            self._save_data()

    def set_admin_status(self, telegram_id: int, is_admin: bool):
        user_id_str = str(telegram_id)
        if user_id_str in self.data['users']:
            self.data['users'][user_id_str]['is_admin'] = is_admin
            self._save_data()

    def is_user_banned(self, telegram_id: int):
        user_id_str = str(telegram_id)
        user = self.data['users'].get(user_id_str)
        return user['is_banned'] if user else False

    def is_user_admin_in_db(self, telegram_id: int):
        user_id_str = str(telegram_id)
        user = self.data['users'].get(user_id_str)
        return user['is_admin'] if user else False

db = JSONDatabase()

# --- Bot Initialization ---
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Middleware ---
class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        if event.from_user and db.is_user_banned(event.from_user.id):
            return
        return await handler(event, data)

dp.message.middleware(BanMiddleware())

# --- Filters ---
class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if not message.from_user:
            return False
        is_config_admin = message.from_user.id in ADMIN_IDS
        is_db_admin = db.is_user_admin_in_db(message.from_user.id)
        return is_config_admin or is_db_admin

# --- Keyboard Menus ---
def get_main_menu(show_submit_more=False):
    keyboard = [[KeyboardButton(text="Связь с админом")]]
    if show_submit_more:
        keyboard.insert(0, [KeyboardButton(text="Отправить еще")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# --- States ---
class Form(StatesGroup):
    nickname = State()
    game_id = State()
    media = State()

# --- Admin Commands ---
@dp.message(IsAdminFilter(), Command("ban", "unban", "addadmin", "deladmin"))
async def admin_commands(message: Message):
    command = message.text.split()[0].replace('/', '')
    args = message.text.split()

    if len(args) != 2:
        await message.answer(f"Использование: /{command} ID")
        return

    try:
        user_id = int(args[1])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return

    if command == "ban":
        db.set_ban_status(user_id, True)
        await message.answer(f"Пользователь {user_id} забанен.")
        try:
            await bot.send_message(user_id, "Вы были забанены администратором.")
        except: pass
    
    elif command == "unban":
        db.set_ban_status(user_id, False)
        await message.answer(f"Пользователь {user_id} разбанен.")
        try:
            await bot.send_message(user_id, "Вы были разбанены администратором.")
        except: pass

    elif command in ["addadmin", "deladmin"]:
        if message.from_user.id not in ADMIN_IDS:
            await message.answer("Только главные администраторы могут управлять админами.")
            return
        
        is_admin = command == "addadmin"
        db.set_admin_status(user_id, is_admin)
        status = "назначен администратором" if is_admin else "больше не администратор"
        await message.answer(f"Пользователь {user_id} {status}.")

# --- Command Handlers ---
@dp.message(Command("cancel"))
@dp.message(F.text.casefold() == "отмена")
async def cancel_handler(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активных действий для отмены.", reply_markup=get_main_menu())
        return
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_menu())

@dp.message(CommandStart())
async def command_start(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    db.add_user(user_id, message.from_user.username or "N/A", message.from_user.full_name or "N/A")
    
    await state.set_state(Form.nickname)
    await message.answer("Добро пожаловать в медиапространство CYBERQELN", reply_markup=get_main_menu())
    await message.answer("Введите свой nickname")

@dp.message(IsAdminFilter(), Command("base"))
async def get_users_base(message: Message) -> None:
    users = db.get_all_users()
    if not users:
        await message.answer("В базе данных пока нет пользователей.")
        return

    total_users = len(users)
    response_parts = [f"📊 <b>Статистика бота</b>\n👥 Всего пользователей: <b>{total_users}</b>\n"]
    
    for i, (tg_id, user_data) in enumerate(users.items(), 1):
        tg_id = int(tg_id)
        status = ""
        if user_data.get('is_banned'): status += " 🚫 BANNED"
        if user_data.get('is_admin'): status += " 👮 ADMIN"
        if tg_id in ADMIN_IDS: status += " 👑 SUPER ADMIN"

        user_link = f"@{user_data.get('username')}" if user_data.get('username') != "N/A" else "Нет юзернейма"
        subs_count = user_data.get('submission_count', 0)
        
        response_parts.append(
            f"🔹 <b>{i}. {user_data.get('full_name')}</b>{status}\n"
            f"   🆔 ID: <code>{tg_id}</code>\n"
            f"   👤 Link: {user_link}\n"
            f"   📩 Отправок: {subs_count}\n"
            f"   📅 Дата: {user_data.get('join_date', '').split(' ')[0]}"
        )
    
    response_text = "\n\n".join(response_parts)
    if len(response_text) > 4096:
        for x in range(0, len(response_text), 4096):
            await message.answer(response_text[x:x+4096], parse_mode="HTML")
    else:
        await message.answer(response_text, parse_mode="HTML")

# --- Text Handlers ---
@dp.message(F.text == "Связь с админом")
async def contact_admin(message: Message):
    await message.answer(
        "Нажмите на кнопку ниже, чтобы связаться с администратором.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Написать", url="https://t.me/Sky_1302")]])
    )

@dp.message(F.text == "Отправить еще")
async def submit_more(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = db.get_user(user_id)
    
    if not user_data:
        # Если базы данных нет или пользователя в ней нет - регистрируем его
        db.add_user(user_id, message.from_user.username or "N/A", message.from_user.full_name or "N/A")
        await state.set_state(Form.nickname)
        await message.answer("Ваши данные не найдены в базе. Пожалуйста, введите никнейм:")
        return

    last_nick = user_data.get('last_nickname')
    last_gid = user_data.get('last_game_id')

    if last_nick and last_gid:
        # Данные есть, пропускаем ввод ника и ID
        await state.update_data(nickname=last_nick, game_id=last_gid)
        await state.set_state(Form.media)
        await message.answer(f"Используем прошлые данные:\nНик: {last_nick}\nID: {last_gid}\n\nОтправьте фото или видео:")
    else:
        # Данных нет, просим ввести
        await state.set_state(Form.nickname)
        await message.answer("Введите свой nickname")

# --- FSM Handlers ---
@dp.message(Form.nickname)
async def process_nickname(message: Message, state: FSMContext) -> None:
    await state.update_data(nickname=message.text)
    await state.set_state(Form.game_id)
    await message.answer("Отлично! Теперь введи свой ID в игре.")

@dp.message(Form.game_id)
async def process_game_id(message: Message, state: FSMContext) -> None:
    await state.update_data(game_id=message.text)
    await state.set_state(Form.media)
    await message.answer("Теперь отправь фото или видео.")

@dp.message(Form.media, F.photo | F.video)
async def process_media(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    nickname = data['nickname']
    game_id = data['game_id']
    tg_user = message.from_user
    
    caption = (
        f"Новая заявка!\n\n"
        f"👤 Никнейм в игре: {nickname}\n"
        f"🆔 ID в игре: {game_id}\n"
        f"📨 Отправитель: {tg_user.full_name} (@{tg_user.username}) [ID: {tg_user.id}]"
    )
    
    try:
        if message.photo:
            await bot.send_photo(chat_id=GROUP_ID, photo=message.photo[-1].file_id, caption=caption)
        elif message.video:
            await bot.send_video(chat_id=GROUP_ID, video=message.video.file_id, caption=caption)
        
        # Обновляем статистику в БД
        db.update_user_submission(tg_user.id, nickname, game_id)
        
        await message.answer("Спасибо! Твои данные отправлены.", reply_markup=get_main_menu(show_submit_more=True))
    except Exception as e:
        logging.error(f"Error sending message to group: {e}")
        await message.answer("Произошла ошибка при отправке данных.")

    await state.clear()

@dp.message(Form.media)
async def process_media_invalid(message: Message) -> None:
    await message.answer("Пожалуйста, отправь именно фото или видео.")

# --- Main Function ---
async def main() -> None:
    logging.info("Бот запущен...")
    while True:
        try:
            await dp.start_polling(bot)
        except TelegramNetworkError as e:
            logging.error(f"Ошибка сети: {e}. Переподключение через 5 секунд...")
            await asyncio.sleep(5)
        except Exception as e:
            logging.error(f"Неожиданная ошибка: {e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
