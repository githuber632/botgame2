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
HIGHLIGHTS_GROUP_ID = config['HIGHLIGHTS_GROUP_ID']
TOURNAMENT_GROUP_ID = config['TOURNAMENT_GROUP_ID']
ADMIN_IDS = config['ADMIN_IDS']

# --- Localization ---
TEXTS = {
    "ru": {
        "welcome": "Добро пожаловать в медиапространство <b>CYBERQELN</b>\n\nВыбери нужное действие в меню ниже 👇",
        "choose_lang": "Выберите язык / Tilni tanlang:",
        "btn_highlight": "🎥 Отправить хайлайт",
        "btn_tournament": "🏆 Регистрация на турнир",
        "btn_support": "Связь с админом",
        "btn_more": "🔄 Отправить еще",
        "btn_lang": "🌐 Сменить язык",
        "hl_choose_game": "В какую игру ты играешь? 🎮",
        "hl_enter_nick": "Выбрана игра: <b>{}</b>\n\nВведите ваш игровой никнейм:",
        "hl_enter_id": "Теперь введи свой игровой ID:",
        "hl_enter_media": "Круто! Теперь отправь фото или видео своего хайлайта 🎬",
        "hl_success": "🔥 Хайлайт успешно отправлен! Спасибо за участие.",
        "tour_choose": "Выбери дисциплину турнира:",
        "tour_discipline": "Турнир по дисциплине: {}",
        "tour_team": "Введите название вашей команды:",
        "tour_count": "Укажите количество участников в команде (от 5 до 7):",
        "tour_ids": "Количество участников: {}\n\nВведите ID всех игроков команды (каждый с новой строки):",
        "tour_nicks": "Введите никнеймы всех игроков (каждый с новой строки):",
        "tour_phone": "Введите номер телефона капитана команды:",
        "tour_success": "✅ Ваша заявка успешно отправлена!",
        "err_count": "Ошибка! Вы указали {}, а выбрали {} игроков. Введите список заново:",
        "support_msg": "Нужна помощь? Жми кнопку ниже!",
        "support_btn": "✅ Написать",
        "cancel": "Действие отменено",
        "admin_new_hl": "✨ <b>НОВЫЙ ХАЙЛАЙТ!</b> ✨",
        "admin_new_tour": "🔥 <b>НОВАЯ ЗАЯВКА НА ТУРНИР!</b> 🔥"
    },
    "uz": {
        "welcome": "<b>CYBERQELN</b> media maydoniga xush kelibsiz\n\nPastdagi menyudan kerakli bo'limni tanlang 👇",
        "choose_lang": "Tilni tanlang / Выберите язык:",
        "btn_highlight": "🎥 Xaylayt yuborish",
        "btn_tournament": "🏆 Turnirga ro'yxatdan o'tish",
        "btn_support": "Admin bilan aloqa",
        "btn_more": "🔄 Yana yuborish",
        "btn_lang": "🌐 Tilni o'zgartirish",
        "hl_choose_game": "Qaysi o'yinni o'ynaysiz? 🎮",
        "hl_enter_nick": "Tanlangan o'yin: <b>{}</b>\n\nO'yindagi nikneymingizni kiriting:",
        "hl_enter_id": "Endi o'yindagi ID raqamingizni kiriting:",
        "hl_enter_media": "Ajoyib! Endi xaylaytingizning foto yoki videosini yuboring 🎬",
        "hl_success": "🔥 Xaylayt muvaffaqiyatli yuborildi! Ishtirok uchun rahmat.",
        "tour_choose": "Turnir yo'nalishini tanlang:",
        "tour_discipline": "Turnir yo'nalishi: {}",
        "tour_team": "Jamoangiz nomini kiriting:",
        "tour_count": "Jamoa a'zolari sonini tanlang (5 dan 7 gacha):",
        "tour_ids": "Ishtirokchilar soni: {}\n\nBarcha o'yinchilar ID raqamlarini kiriting (har birini yangi qatordan):",
        "tour_nicks": "Barcha o'yinchilar nikneymlarini kiriting (har birini yangi qatordan):",
        "tour_phone": "Jamoa sardori telefon raqamini kiriting:",
        "tour_success": "✅ Arizangiz muvaffaqiyatli yuborildi!",
        "err_count": "Xato! Siz {} ta kiritdingiz, lekin {} ta o'yinchi tanlangan. Ro'yxatni qaytadan kiriting:",
        "support_msg": "Yordam kerakmi? Pastdagi tugmani bosing!",
        "support_btn": "✅ Yozish",
        "cancel": "Amal bekor qilindi",
        "admin_new_hl": "✨ <b>YANGI XAYLAYT!</b> ✨",
        "admin_new_tour": "🔥 <b>TURNIRGA YANGI ARIZA!</b> 🔥"
    }
}

# --- Database ---
class JSONDatabase:
    def __init__(self, db_path='database.json'):
        self.db_path = db_path
        self.data = self._load_data()

    def _load_data(self):
        try:
            with open(self.db_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"users": {}}

    def _save_data(self):
        with open(self.db_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=4, ensure_ascii=False)

    def add_user(self, telegram_id: int, username: str, full_name: str):
        u_id = str(telegram_id)
        if u_id not in self.data['users']:
            self.data['users'][u_id] = {
                "username": username or "N/A",
                "full_name": full_name or "N/A",
                "language": None,
                "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "is_banned": False,
                "is_admin": False,
                "submission_count": 0,
                "last_nickname": None, "last_game_id": None, "last_game": None
            }
            self._save_data()

    def set_lang(self, telegram_id: int, lang: str):
        u_id = str(telegram_id)
        if u_id in self.data['users']:
            self.data['users'][u_id]['language'] = lang
            self._save_data()

    def get_user(self, telegram_id: int):
        return self.data['users'].get(str(telegram_id))

    def update_user_submission(self, telegram_id: int, nick: str, g_id: str, game: str):
        u_id = str(telegram_id)
        if u_id in self.data['users']:
            u = self.data['users'][u_id]
            u["submission_count"] = u.get("submission_count", 0) + 1
            u["last_nickname"], u["last_game_id"], u["last_game"] = nick, g_id, game
            self._save_data()

    def get_all_users(self): return self.data['users']

db = JSONDatabase()

# --- Bot & Dispatcher ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Utils & Keyboards ---
def get_lang(user_id):
    u = db.get_user(user_id)
    return u.get('language') if u else 'ru'

def get_main_menu(user_id):
    lang = get_lang(user_id)
    u = db.get_user(user_id)
    has_history = u and u.get('last_nickname')
    
    t = TEXTS[lang]
    buttons = []
    if has_history: buttons.append([KeyboardButton(text=t["btn_more"])])
    else: buttons.append([KeyboardButton(text=t["btn_highlight"])])
    
    buttons.append([KeyboardButton(text=t["btn_tournament"])])
    buttons.append([KeyboardButton(text=t["btn_support"]), KeyboardButton(text=t["btn_lang"])])
    
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

lang_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Русский 🇷🇺", callback_data="setlang_ru"),
     InlineKeyboardButton(text="O'zbekcha 🇺🇿", callback_data="setlang_uz")]
])

def get_games_kb(prefix):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ MLBB", callback_data=f"{prefix}_MLBB")],
        [InlineKeyboardButton(text="🔫 PUBG", callback_data=f"{prefix}_PUBG")],
        [InlineKeyboardButton(text="🏯 HOK", callback_data=f"{prefix}_HOK")]
    ])

def get_count_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5", callback_data="count_5"),
         InlineKeyboardButton(text="6", callback_data="count_6"),
         InlineKeyboardButton(text="7", callback_data="count_7")]
    ])

# --- States ---
class Form(StatesGroup): game = State(); nick = State(); g_id = State(); media = State()
class TourForm(StatesGroup): game = State(); team = State(); count = State(); ids = State(); nicks = State(); phone = State()

# --- Handlers ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    db.add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    u = db.get_user(message.from_user.id)
    if not u.get('language'):
        await message.answer(TEXTS["ru"]["choose_lang"], reply_markup=lang_kb)
    else:
        lang = u['language']
        await message.answer(TEXTS[lang]["welcome"], reply_markup=get_main_menu(message.from_user.id), parse_mode="HTML")

@dp.callback_query(F.data.startswith("setlang_"))
async def set_language(call: CallbackQuery):
    lang = call.data.split("_")[1]
    db.set_lang(call.from_user.id, lang)
    await call.message.delete()
    await call.message.answer(TEXTS[lang]["welcome"], reply_markup=get_main_menu(call.from_user.id), parse_mode="HTML")
    await call.answer()

@dp.message(F.text.in_([TEXTS["ru"]["btn_lang"], TEXTS["uz"]["btn_lang"]]))
async def change_lang(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(TEXTS["ru"]["choose_lang"], reply_markup=lang_kb)

# --- Highlight Logic ---
@dp.message(F.text.in_([TEXTS["ru"]["btn_highlight"], TEXTS["uz"]["btn_highlight"]]))
async def hl_start(message: Message, state: FSMContext):
    await state.clear()
    lang = get_lang(message.from_user.id)
    await message.answer(TEXTS[lang]["hl_choose_game"], reply_markup=get_games_kb("hl"))

@dp.callback_query(F.data.startswith("hl_"))
async def hl_game(call: CallbackQuery, state: FSMContext):
    game = call.data.split("_")[1]
    lang = get_lang(call.from_user.id)
    await state.update_data(game=game)
    await state.set_state(Form.nick)
    await call.message.edit_text(TEXTS[lang]["hl_enter_nick"].format(game), parse_mode="HTML")

@dp.message(Form.nick)
async def hl_nick(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
    await state.update_data(nick=message.text); await state.set_state(Form.g_id); await message.answer(TEXTS[lang]["hl_enter_id"])

@dp.message(Form.g_id)
async def hl_id(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
    await state.update_data(g_id=message.text); await state.set_state(Form.media); await message.answer(TEXTS[lang]["hl_enter_media"])

@dp.message(Form.media, F.photo | F.video)
async def hl_media(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id); data = await state.get_data(); user = message.from_user
    cap = f"{TEXTS[lang]['admin_new_hl']}\n\n🎮 O'yin: <b>{data['game']}</b>\n👤 Nikneym: <code>{data['nick']}</code>\n🆔 ID: <code>{data['g_id']}</code>\n\n📨 Yuboruvchi: {user.full_name} (@{user.username})"
    try:
        if message.photo: await bot.send_photo(HIGHLIGHTS_GROUP_ID, message.photo[-1].file_id, caption=cap, parse_mode="HTML")
        else: await bot.send_video(HIGHLIGHTS_GROUP_ID, message.video.file_id, caption=cap, parse_mode="HTML")
        db.update_user_submission(user.id, data['nick'], data['g_id'], data['game'])
        await message.answer(TEXTS[lang]["hl_success"], reply_markup=get_main_menu(user.id))
    except: await message.answer("Error")
    await state.clear()

@dp.message(F.text.in_([TEXTS["ru"]["btn_more"], TEXTS["uz"]["btn_more"]]))
async def hl_more(message: Message, state: FSMContext):
    await state.clear(); user = db.get_user(message.from_user.id); lang = get_lang(message.from_user.id)
    if user and user.get('last_nickname'):
        await state.update_data(nick=user['last_nickname'], g_id=user['last_game_id'], game=user.get('last_game', '???'))
        await state.set_state(Form.media)
        await message.answer(TEXTS[lang]["hl_enter_media"])
    else: await hl_start(message, state)

# --- Tournament Logic ---
@dp.message(F.text.in_([TEXTS["ru"]["btn_tournament"], TEXTS["uz"]["btn_tournament"]]))
async def tour_start(message: Message, state: FSMContext):
    await state.clear(); lang = get_lang(message.from_user.id)
    await message.answer(TEXTS[lang]["tour_choose"], reply_markup=get_games_kb("tour"))

@dp.callback_query(F.data.startswith("tour_"))
async def tour_game(call: CallbackQuery, state: FSMContext):
    game = call.data.split("_")[1]; lang = get_lang(call.from_user.id)
    await state.update_data(game=game); await state.set_state(TourForm.team)
    await call.message.edit_text(f"{TEXTS[lang]['tour_discipline'].format(game)}\n\n{TEXTS[lang]['tour_team']}")

@dp.message(TourForm.team)
async def tour_team(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id); await state.update_data(team=message.text)
    await state.set_state(TourForm.count); await message.answer(TEXTS[lang]["tour_count"], reply_markup=get_count_kb())

@dp.callback_query(F.data.startswith("count_"))
async def tour_count(call: CallbackQuery, state: FSMContext):
    count = call.data.split("_")[1]; lang = get_lang(call.from_user.id)
    await state.update_data(count=count); await state.set_state(TourForm.ids)
    await call.message.edit_text(TEXTS[lang]["tour_ids"].format(count))

@dp.message(TourForm.ids)
async def tour_ids(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id); data = await state.get_data(); expected = int(data['count'])
    ids_list = [i.strip() for i in message.text.split('\n') if i.strip()]
    if len(ids_list) != expected: return await message.answer(TEXTS[lang]["err_count"].format(len(ids_list), expected))
    await state.update_data(ids=message.text); await state.set_state(TourForm.nicks); await message.answer(TEXTS[lang]["tour_nicks"])

@dp.message(TourForm.nicks)
async def tour_nicks(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id); data = await state.get_data(); expected = int(data['count'])
    nicks_list = [n.strip() for n in message.text.split('\n') if n.strip()]
    if len(nicks_list) != expected: return await message.answer(TEXTS[lang]["err_count"].format(len(nicks_list), expected))
    await state.update_data(nicks=message.text); await state.set_state(TourForm.phone); await message.answer(TEXTS[lang]["tour_phone"])

@dp.message(TourForm.phone)
async def tour_phone(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id); data = await state.get_data(); user = message.from_user
    admin_msg = (
        f"{TEXTS[lang]['admin_new_tour']}\n\n"
        f"🎮 <b>O'yin:</b> {data['game']}\n"
        f"👥 <b>Jamoa:</b> {data['team']}\n"
        f"🔢 <b>Soni:</b> {data['count']}\n\n"
        f"🆔 <b>IDs:</b>\n<code>{data['ids']}</code>\n\n"
        f"👤 <b>Nicks:</b>\n<code>{data['nicks']}</code>\n\n"
        f"📞 <b>Tel:</b> <code>{message.text}</code>\n"
        f"🔗 <b>Profil:</b> <a href='tg://user?id={user.id}'>{user.full_name}</a>"
    )
    try:
        await bot.send_message(TOURNAMENT_GROUP_ID, admin_msg, parse_mode="HTML")
        await message.answer(TEXTS[lang]["tour_success"], reply_markup=get_main_menu(user.id))
    except: await message.answer("Error")
    await state.clear()

# --- Common Handlers ---
@dp.message(F.text.in_([TEXTS["ru"]["btn_support"], TEXTS["uz"]["btn_support"]]))
async def support(message: Message, state: FSMContext):
    await state.clear(); lang = get_lang(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=TEXTS[lang]["support_btn"], url="https://t.me/Sky_1302")]])
    await message.answer(TEXTS[lang]["support_msg"], reply_markup=kb)

@dp.message(Command("base"))
async def admin_base(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    users = db.get_all_users()
    res = [f"📊 Всего: {len(users)}"]
    for i, (tid, data) in enumerate(users.items(), 1):
        res.append(f"{i}. {data['full_name']} (@{data['username']}) | Lang: {data['language']}")
    txt = "\n".join(res)
    for x in range(0, len(txt), 4096): await message.answer(txt[x:x+4096])

async def main():
    while True:
        try: await dp.start_polling(bot)
        except: await asyncio.sleep(5)

if __name__ == "__main__":
    if sys.platform == 'win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
