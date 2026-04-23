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
    ReplyKeyboardMarkup, KeyboardButton, CallbackQuery
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
                "last_game_id": None,
                "last_game": None
            }
            self._save_data()
            return True
        return False

    def update_user_submission(self, telegram_id: int, nickname: str, game_id: str, game: str):
        user_id_str = str(telegram_id)
        if user_id_str in self.data['users']:
            user = self.data['users'][user_id_str]
            user["submission_count"] = user.get("submission_count", 0) + 1
            user["last_nickname"] = nickname
            user["last_game_id"] = game_id
            user["last_game"] = game
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
        if hasattr(event, 'from_user') and event.from_user and db.is_user_banned(event.from_user.id):
            return
        return await handler(event, data)

dp.message.middleware(BanMiddleware())

# --- Filters ---
class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if not message.from_user: return False
        return message.from_user.id in ADMIN_IDS or db.is_user_admin_in_db(message.from_user.id)

# --- Keyboards ---
def get_main_menu(user_id=None):
    user_data = db.get_user(user_id) if user_id else None
    has_history = user_data and user_data.get('last_nickname')

    if has_history:
        buttons = [
            [KeyboardButton(text="🔄 Отправить еще")],
            [KeyboardButton(text="🏆 Регистрация на турнир")],
            [KeyboardButton(text="Связь с админом")]
        ]
    else:
        buttons = [
            [KeyboardButton(text="🎥 Отправить хайлайт")],
            [KeyboardButton(text="🏆 Регистрация на турнир")],
            [KeyboardButton(text="Связь с админом")]
        ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

highlight_games_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⚔️ MLBB", callback_data="hl_MLBB")],
    [InlineKeyboardButton(text="🔫 PUBG", callback_data="hl_PUBG")],
    [InlineKeyboardButton(text="🏯 HOK", callback_data="hl_HOK")]
])

tournament_games_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⚔️ MLBB", callback_data="tour_MLBB")],
    [InlineKeyboardButton(text="🔫 PUBG", callback_data="tour_PUBG")],
    [InlineKeyboardButton(text="🏯 HOK", callback_data="tour_HOK")]
])

def get_players_count_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5 игроков", callback_data="count_5")],
        [InlineKeyboardButton(text="6 игроков", callback_data="count_6")],
        [InlineKeyboardButton(text="7 игроков", callback_data="count_7")]
    ])

# --- States ---
class Form(StatesGroup):
    game = State()
    nickname = State()
    game_id = State()
    media = State()

class TournamentForm(StatesGroup):
    game = State()
    team_name = State()
    players_count = State()
    players_ids = State()
    players_nicknames = State()
    captain_phone = State()

# --- Admin Commands ---
@dp.message(IsAdminFilter(), Command("ban", "unban", "addadmin", "deladmin"))
async def admin_commands(message: Message):
    cmd_parts = message.text.split()
    cmd = cmd_parts[0].replace('/', '')
    if len(cmd_parts) != 2: return await message.answer(f"Использование: /{cmd} ID")
    try:
        u_id = int(cmd_parts[1])
        if cmd == "ban": db.set_ban_status(u_id, True); await message.answer(f"Забанен {u_id}")
        elif cmd == "unban": db.set_ban_status(u_id, False); await message.answer(f"Разбанен {u_id}")
        elif cmd in ["addadmin", "deladmin"]:
            if message.from_user.id not in ADMIN_IDS: return await message.answer("Нет прав")
            db.set_admin_status(u_id, cmd == "addadmin")
            await message.answer(f"Статус админа изменен для {u_id}")
    except: await message.answer("Ошибка в ID")

@dp.message(IsAdminFilter(), Command("base"))
async def get_users_base(message: Message):
    users = db.get_all_users()
    if not users: return await message.answer("Пусто")
    res = [f"📊 <b>Всего: {len(users)}</b>\n"]
    for i, (tid, data) in enumerate(users.items(), 1):
        status = " 🚫" if data.get('is_banned') else ""
        if data.get('is_admin') or int(tid) in ADMIN_IDS: status += " 👮"
        res.append(f"<b>{i}. {data.get('full_name')}</b>{status}\nID: <code>{tid}</code> | 📩: {data.get('submission_count', 0)}")
    
    txt = "\n\n".join(res)
    for x in range(0, len(txt), 4096): await message.answer(txt[x:x+4096], parse_mode="HTML")

# --- Highlight Logic ---
@dp.message(F.text == "🎥 Отправить хайлайт")
async def highlight_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("В какую игру ты играешь? 🎮", reply_markup=highlight_games_kb)

@dp.callback_query(F.data.startswith("hl_"))
async def highlight_game_select(callback: CallbackQuery, state: FSMContext):
    game = callback.data.split("_")[1]
    await state.update_data(game=game)
    await state.set_state(Form.nickname)
    await callback.message.edit_text(f"Выбрана игра: <b>{game}</b>\n\nВведите ваш игровой никнейм:")
    await callback.answer()

@dp.message(F.text == "🔄 Отправить еще")
async def submit_more(message: Message, state: FSMContext):
    await state.clear()
    user_data = db.get_user(message.from_user.id)
    if user_data and user_data.get('last_nickname'):
        await state.update_data(
            nickname=user_data['last_nickname'], 
            game_id=user_data['last_game_id'],
            game=user_data.get('last_game', 'Неизвестно')
        )
        await state.set_state(Form.media)
        await message.answer(
            f"Используем прошлые данные:\n"
            f"🎮 Игра: {user_data.get('last_game', 'Неизвестно')}\n"
            f"👤 Ник: {user_data['last_nickname']}\n"
            f"🆔 ID: {user_data['last_game_id']}\n\n"
            f"Пришлите фото или видео хайлайта! ✨"
        )
    else:
        await highlight_start(message, state)

# --- Tournament Logic ---
@dp.message(F.text == "🏆 Регистрация на турнир")
async def tournament_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выбери дисциплину турнира:", reply_markup=tournament_games_kb)

@dp.callback_query(F.data.startswith("tour_"))
async def tournament_game_select(callback: CallbackQuery, state: FSMContext):
    game = callback.data.split("_")[1]
    await state.update_data(game=game)
    await state.set_state(TournamentForm.team_name)
    await callback.message.edit_text(f"Турнир по дисциплине: {game}\n\nВведите название вашей команды:")
    await callback.answer()

@dp.message(TournamentForm.team_name)
async def tour_team(message: Message, state: FSMContext):
    await state.update_data(team_name=message.text)
    await state.set_state(TournamentForm.players_count)
    await message.answer("Укажите количество участников в команде (от 5 до 7):", reply_markup=get_players_count_kb())

@dp.callback_query(F.data.startswith("count_"))
async def tour_count_select(callback: CallbackQuery, state: FSMContext):
    count = callback.data.split("_")[1]
    await state.update_data(players_count=count)
    await state.set_state(TournamentForm.players_ids)
    await callback.message.edit_text(f"Количество участников: {count}\n\nВведите ID всех игроков команды (каждый с новой строки):")
    await callback.answer()

@dp.message(TournamentForm.players_ids)
async def tour_players_ids(message: Message, state: FSMContext):
    data = await state.get_data()
    expected_count = int(data['players_count'])
    ids = [i.strip() for i in message.text.split('\n') if i.strip()]
    
    if len(ids) != expected_count:
        await message.answer(f"Ошибка! Вы указали {len(ids)} ID, а выбрали {expected_count} игроков. Пожалуйста, введите ровно {expected_count} ID (по одному на строку):")
        return

    await state.update_data(players_ids=message.text)
    await state.set_state(TournamentForm.players_nicknames)
    await message.answer("Введите никнеймы всех игроков (каждый с новой строки):")

@dp.message(TournamentForm.players_nicknames)
async def tour_players_nicknames(message: Message, state: FSMContext):
    data = await state.get_data()
    expected_count = int(data['players_count'])
    nicknames = [n.strip() for n in message.text.split('\n') if n.strip()]

    if len(nicknames) != expected_count:
        await message.answer(f"Ошибка! Вы указали {len(nicknames)} никнеймов, а выбрали {expected_count} игроков. Пожалуйста, введите ровно {expected_count} никнеймов (по одному на строку):")
        return

    await state.update_data(players_nicknames=message.text)
    await state.set_state(TournamentForm.captain_phone)
    await message.answer("Введите номер телефона капитана команды:")

@dp.message(TournamentForm.captain_phone)
async def tour_captain_phone(message: Message, state: FSMContext):
    data = await state.get_data()
    user = message.from_user
    captain_phone = message.text
    
    admin_msg = (
        "🔥 <b>НОВАЯ ЗАЯВКА НА ТУРНИР!</b> 🔥\n\n"
        f"🎮 Игра: <b>{data['game']}</b>\n"
        f"👥 Команда: <code>{data['team_name']}</code>\n"
        f"👥 Участников: <code>{data['players_count']}</code>\n\n"
        f"🆔 <b>ID игроков команды:</b>\n<code>{data['players_ids']}</code>\n\n"
        f"👤 <b>Никнеймы игроков:</b>\n<code>{data['players_nicknames']}</code>\n\n"
        f"📞 <b>Номер капитана:</b>\n<code>{captain_phone}</code>\n\n"
        f"🔗 Профиль: <a href='tg://user?id={user.id}'>{user.full_name}</a>\n"
        f"📨 Юзернейм: @{user.username or 'отсутствует'}"
    )
    
    try:
        await bot.send_message(GROUP_ID, admin_msg, parse_mode="HTML")
        await message.answer("✅ Ваша заявка отправлена!", reply_markup=get_main_menu(user.id))
    except Exception as e:
        logging.error(f"Error in tour_finish: {e}")
        await message.answer("❌ Ошибка отправки.")

    await state.clear()

# --- Main Logic ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    db.add_user(message.from_user.id, message.from_user.username or "N/A", message.from_user.full_name or "N/A")
    await message.answer(
        "Добро пожаловать в медиапространство <b>CYBERQELN</b>\n\n"
        "Выбери нужное действие в меню ниже 👇", 
        reply_markup=get_main_menu(message.from_user.id), parse_mode="HTML"
    )

@dp.message(F.text == "Связь с админом")
async def contact_admin(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Написать", url="https://t.me/Sky_1302")]])
    await message.answer("Нужна помощь? Жми кнопку ниже!", reply_markup=kb)

# --- FSM Handlers ---

@dp.message(Form.nickname)
async def proc_nick(message: Message, state: FSMContext):
    await state.update_data(nickname=message.text)
    await state.set_state(Form.game_id)
    await message.answer("Теперь введи свой игровой ID:")

@dp.message(Form.game_id)
async def proc_gid(message: Message, state: FSMContext):
    await state.update_data(game_id=message.text)
    await state.set_state(Form.media)
    await message.answer("Круто! Теперь отправь фото или видео своего хайлайта 🎬")

@dp.message(Form.media, F.photo | F.video)
async def proc_media(message: Message, state: FSMContext):
    data = await state.get_data()
    user = message.from_user
    nickname = data.get('nickname')
    game_id = data.get('game_id')
    game = data.get('game', 'Неизвестно')
    
    cap = (
        "✨ <b>НОВЫЙ ХАЙЛАЙТ!</b> ✨\n\n"
        f"🎮 Игра: <b>{game}</b>\n"
        f"👤 Ник: <code>{nickname}</code>\n"
        f"🆔 ID: <code>{game_id}</code>\n\n"
        f"📨 От: {user.full_name} (@{user.username})"
    )
    try:
        if message.photo:
            await bot.send_photo(GROUP_ID, message.photo[-1].file_id, caption=cap, parse_mode="HTML")
        elif message.video:
            await bot.send_video(GROUP_ID, message.video.file_id, caption=cap, parse_mode="HTML")
        
        db.update_user_submission(user.id, nickname, game_id, game)
        await message.answer("🔥 Хайлайт успешно отправлен! Спасибо за участие.", reply_markup=get_main_menu(user.id))
    except Exception as e:
        logging.error(f"Error in proc_media: {e}")
        await message.answer("❌ Ошибка при отправке.")
    
    await state.clear()

@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено", reply_markup=get_main_menu(message.from_user.id))

async def main():
    while True:
        try:
            await dp.start_polling(bot)
        except TelegramNetworkError:
            await asyncio.sleep(5)
        except Exception:
            await asyncio.sleep(15)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
