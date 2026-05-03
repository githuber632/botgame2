import asyncio
import logging
import sys
import json
import time
from datetime import datetime

import aiohttp

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, CallbackQuery, WebAppInfo
)

# --- Config ---
with open('config.json', 'r') as _f:
    _cfg = json.load(_f)

BOT_TOKEN           = _cfg['BOT_TOKEN']
HIGHLIGHTS_GROUP_ID = _cfg['HIGHLIGHTS_GROUP_ID']
TOURNAMENT_GROUP_ID = _cfg['TOURNAMENT_GROUP_ID']
ADMIN_IDS           = _cfg['ADMIN_IDS']
WEBAPP_URL          = "https://cyberqeln.com"

# --- Firebase ---
FB_API_KEY  = "AIzaSyDYAQuDqrCkXts95znZANUqDD-tL9JTkLI"
FB_PROJECT  = "cyberqeln"
BOT_FB_EMAIL    = "bot@cyberqeln.app"
BOT_FB_PASSWORD = "CyberQELN_Bot_2024!"
_fb = {"idToken": None, "refreshToken": None, "expiresAt": 0}

def _fs_val(v):
    if isinstance(v, bool):   return {"booleanValue": v}
    if isinstance(v, int):    return {"integerValue": str(v)}
    if v is None:             return {"nullValue": None}
    return {"stringValue": str(v)}

async def _fb_token(session: aiohttp.ClientSession) -> str:
    now = time.time()
    if _fb["idToken"] and now < _fb["expiresAt"] - 60:
        return _fb["idToken"]
    if _fb["refreshToken"]:
        async with session.post(
            f"https://securetoken.googleapis.com/v1/token?key={FB_API_KEY}",
            json={"grant_type": "refresh_token", "refresh_token": _fb["refreshToken"]}
        ) as r:
            if r.status == 200:
                d = await r.json()
                _fb["idToken"]      = d["id_token"]
                _fb["refreshToken"] = d["refresh_token"]
                _fb["expiresAt"]    = now + int(d["expires_in"])
                return _fb["idToken"]
    # Sign in with bot service account
    async with session.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FB_API_KEY}",
        json={"email": BOT_FB_EMAIL, "password": BOT_FB_PASSWORD, "returnSecureToken": True}
    ) as r:
        d = await r.json()
        _fb["idToken"]      = d["idToken"]
        _fb["refreshToken"] = d["refreshToken"]
        _fb["expiresAt"]    = now + int(d["expiresIn"])
        return _fb["idToken"]

async def fs_set(session: aiohttp.ClientSession, col: str, doc_id: str, data: dict) -> bool:
    token = await _fb_token(session)
    url   = f"https://firestore.googleapis.com/v1/projects/{FB_PROJECT}/databases/(default)/documents/{col}/{doc_id}"
    fields = {k: _fs_val(v) for k, v in data.items()}
    async with session.patch(url, headers={"Authorization": f"Bearer {token}"}, json={"fields": fields}) as r:
        return r.status == 200

async def fs_get(session: aiohttp.ClientSession, col: str, doc_id: str) -> dict | None:
    token = await _fb_token(session)
    url   = f"https://firestore.googleapis.com/v1/projects/{FB_PROJECT}/databases/(default)/documents/{col}/{doc_id}"
    async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as r:
        if r.status != 200:
            return None
        result = {}
        for k, v in (await r.json()).get("fields", {}).items():
            if "stringValue"  in v: result[k] = v["stringValue"]
            elif "integerValue" in v: result[k] = int(v["integerValue"])
            elif "booleanValue" in v: result[k] = v["booleanValue"]
        return result

async def fs_create(session: aiohttp.ClientSession, col: str, data: dict) -> bool:
    token = await _fb_token(session)
    url   = f"https://firestore.googleapis.com/v1/projects/{FB_PROJECT}/databases/(default)/documents/{col}"
    fields = {k: _fs_val(v) for k, v in data.items()}
    async with session.post(url, headers={"Authorization": f"Bearer {token}"}, json={"fields": fields}) as r:
        return r.status in (200, 201)

async def get_tg_photo(user_id: int) -> str:
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count > 0:
            file = await bot.get_file(photos.photos[0][-1].file_id)
            return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    except Exception:
        pass
    return ""

async def fb_ensure_user(session: aiohttp.ClientSession, tg_user: types.User) -> str | None:
    """Sign in or create Firebase user for a Telegram user. Returns UID."""
    email    = f"tg{tg_user.id}@cyberqeln.app"
    password = f"TG_{tg_user.id}_CyberQELN"
    name     = tg_user.full_name or tg_user.first_name or "Player"
    photo    = await get_tg_photo(tg_user.id)

    # Try sign in
    async with session.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FB_API_KEY}",
        json={"email": email, "password": password, "returnSecureToken": True}
    ) as r:
        if r.status == 200:
            d   = await r.json()
            uid = d["localId"]
            await fs_set(session, "tg_profiles", str(tg_user.id), {
                "telegramId": tg_user.id, "firstName": tg_user.first_name or "",
                "username": tg_user.username or "", "photoUrl": photo, "firebaseUid": uid,
            })
            return uid

    # Create new user
    async with session.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FB_API_KEY}",
        json={"email": email, "password": password, "returnSecureToken": True}
    ) as r:
        if r.status != 200:
            return None
        d        = await r.json()
        uid      = d["localId"]
        id_token = d["idToken"]

    # Save profile to Firestore using the user's own token
    profile_url = f"https://firestore.googleapis.com/v1/projects/{FB_PROJECT}/databases/(default)/documents/users/{uid}"
    fields = {
        "uid":             {"stringValue": uid},
        "name":            {"stringValue": name},
        "email":           {"stringValue": email},
        "avatar":          {"stringValue": photo},
        "role":            {"stringValue": "user"},
        "rating":          {"integerValue": "1000"},
        "telegramId":      {"integerValue": str(tg_user.id)},
        "telegramUsername":{"stringValue": tg_user.username or ""},
        "createdAt":       {"stringValue": datetime.now().isoformat()},
    }
    async with session.patch(
        profile_url,
        headers={"Authorization": f"Bearer {id_token}"},
        json={"fields": fields}
    ) as _:
        pass

    await fs_set(session, "tg_profiles", str(tg_user.id), {
        "telegramId": tg_user.id, "firstName": tg_user.first_name or "",
        "username": tg_user.username or "", "photoUrl": photo, "firebaseUid": uid,
    })
    return uid

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
        "btn_webapp": "🌐 Открыть CyberQELN",
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
        "admin_new_hl": "✨ <b>НОВЫЙ ХАЙЛАЙТ!</b> ✨",
        "admin_new_tour": "🔥 <b>НОВАЯ ЗАЯВКА НА ТУРНИР!</b> 🔥",
    },
    "uz": {
        "welcome": "<b>CYBERQELN</b> media maydoniga xush kelibsiz\n\nPastdagi menyudan kerakli bo'limni tanlang 👇",
        "choose_lang": "Tilni tanlang / Выберите язык:",
        "btn_highlight": "🎥 Xaylayt yuborish",
        "btn_tournament": "🏆 Turnirga ro'yxatdan o'tish",
        "btn_support": "Admin bilan aloqa",
        "btn_more": "🔄 Yana yuborish",
        "btn_lang": "🌐 Tilni o'zgartirish",
        "btn_webapp": "🌐 CyberQELN ochish",
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
        "admin_new_hl": "✨ <b>YANGI XAYLAYT!</b> ✨",
        "admin_new_tour": "🔥 <b>TURNIRGA YANGI ARIZA!</b> 🔥",
    }
}

# --- JSON Database ---
class JSONDatabase:
    def __init__(self, path='database.json'):
        self.path = path
        self.data = self._load()

    def _load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"users": {}}

    def _save(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=4, ensure_ascii=False)

    def add_user(self, tg_id, username, full_name):
        uid = str(tg_id)
        if uid not in self.data['users']:
            self.data['users'][uid] = {
                "username": username or "N/A", "full_name": full_name or "N/A",
                "language": None, "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "is_banned": False, "submission_count": 0,
                "last_nickname": None, "last_game_id": None, "last_game": None,
            }
            self._save()

    def set_lang(self, tg_id, lang):
        uid = str(tg_id)
        if uid in self.data['users']:
            self.data['users'][uid]['language'] = lang
            self._save()

    def get_user(self, tg_id):
        return self.data['users'].get(str(tg_id))

    def update_submission(self, tg_id, nick, g_id, game):
        uid = str(tg_id)
        if uid in self.data['users']:
            u = self.data['users'][uid]
            u['submission_count'] = u.get('submission_count', 0) + 1
            u['last_nickname'], u['last_game_id'], u['last_game'] = nick, g_id, game
            self._save()

    def get_all(self): return self.data['users']

db = JSONDatabase()

# --- Bot & Dispatcher ---
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

def get_lang(uid): u = db.get_user(uid); return u.get('language') if u else 'ru'

def get_main_menu(uid):
    lang = get_lang(uid); u = db.get_user(uid); t = TEXTS[lang]
    btns = []
    btns.append([KeyboardButton(text=t["btn_more"] if u and u.get('last_nickname') else t["btn_highlight"])])
    btns.append([KeyboardButton(text=t["btn_tournament"])])
    btns.append([KeyboardButton(text=t["btn_support"]), KeyboardButton(text=t["btn_lang"])])
    btns.append([KeyboardButton(text=t["btn_webapp"], web_app=WebAppInfo(url=WEBAPP_URL))])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

lang_kb = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="Русский 🇷🇺", callback_data="setlang_ru"),
    InlineKeyboardButton(text="O'zbekcha 🇺🇿", callback_data="setlang_uz"),
]])

def games_kb(prefix): return InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⚔️ MLBB", callback_data=f"{prefix}_MLBB")],
    [InlineKeyboardButton(text="🔫 PUBG", callback_data=f"{prefix}_PUBG")],
    [InlineKeyboardButton(text="🏯 HOK",  callback_data=f"{prefix}_HOK")],
])

count_kb = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="5", callback_data="count_5"),
    InlineKeyboardButton(text="6", callback_data="count_6"),
    InlineKeyboardButton(text="7", callback_data="count_7"),
]])

class Form(StatesGroup):     game=State(); nick=State(); g_id=State(); media=State()
class TourForm(StatesGroup): game=State(); team=State(); count=State(); ids=State(); nicks=State(); phone=State()

# --- Handlers ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    u = message.from_user
    db.add_user(u.id, u.username, u.full_name)

    # Site auth code: /start auth_XXXXXXXX
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("auth_"):
        code = parts[1][5:]
        name = u.full_name or u.first_name or "Player"
        try:
            photo = await get_tg_photo(u.id)
        except Exception:
            photo = ""
        try:
            async with aiohttp.ClientSession() as session:
                uid = await fb_ensure_user(session, u)
                ok  = await fs_set(session, "tg_auth", code, {
                    "status": "done", "tgId": u.id,
                    "name": name, "username": u.username or "",
                    "photo": photo, "uid": uid or "",
                })
            if ok:
                await message.answer(
                    f"✅ <b>Авторизация прошла успешно!</b>\n\n"
                    f"Добро пожаловать, <b>{name}</b>!\n"
                    f"Вернись на сайт — вход выполнится автоматически.",
                    parse_mode="HTML", reply_markup=get_main_menu(u.id)
                )
            else:
                await message.answer("❌ Не удалось завершить авторизацию. Попробуй снова.")
        except Exception as e:
            print(f"[auth error] {e}")
            await message.answer("❌ Ошибка соединения. Попробуй снова.")
        return

    # Regular start
    asyncio.create_task(_bg_save_tg_profile(u))
    user_data = db.get_user(u.id)
    if not user_data.get('language'):
        await message.answer(TEXTS["ru"]["choose_lang"], reply_markup=lang_kb)
    else:
        await message.answer(TEXTS[user_data['language']]["welcome"],
                             reply_markup=get_main_menu(u.id), parse_mode="HTML")

async def _bg_save_tg_profile(u: types.User):
    try:
        async with aiohttp.ClientSession() as session:
            await fs_set(session, "tg_profiles", str(u.id), {
                "telegramId": u.id, "firstName": u.first_name or "",
                "username": u.username or "",
            })
    except Exception: pass

@dp.callback_query(F.data.startswith("setlang_"))
async def set_language(call: CallbackQuery):
    lang = call.data.split("_")[1]
    db.set_lang(call.from_user.id, lang)
    await call.message.delete()
    await call.message.answer(TEXTS[lang]["welcome"],
                              reply_markup=get_main_menu(call.from_user.id), parse_mode="HTML")
    await call.answer()

@dp.message(F.text.in_([TEXTS["ru"]["btn_lang"], TEXTS["uz"]["btn_lang"]]))
async def change_lang(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(TEXTS["ru"]["choose_lang"], reply_markup=lang_kb)

# Highlight
@dp.message(F.text.in_([TEXTS["ru"]["btn_highlight"], TEXTS["uz"]["btn_highlight"]]))
async def hl_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(TEXTS[get_lang(message.from_user.id)]["hl_choose_game"], reply_markup=games_kb("hl"))

@dp.callback_query(F.data.startswith("hl_"))
async def hl_game(call: CallbackQuery, state: FSMContext):
    game = call.data.split("_")[1]; lang = get_lang(call.from_user.id)
    await state.update_data(game=game); await state.set_state(Form.nick)
    await call.message.edit_text(TEXTS[lang]["hl_enter_nick"].format(game), parse_mode="HTML")

@dp.message(Form.nick)
async def hl_nick(message: Message, state: FSMContext):
    await state.update_data(nick=message.text); await state.set_state(Form.g_id)
    await message.answer(TEXTS[get_lang(message.from_user.id)]["hl_enter_id"])

@dp.message(Form.g_id)
async def hl_id(message: Message, state: FSMContext):
    await state.update_data(g_id=message.text); await state.set_state(Form.media)
    await message.answer(TEXTS[get_lang(message.from_user.id)]["hl_enter_media"])

@dp.message(Form.media, F.photo | F.video)
async def hl_media(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id); data = await state.get_data(); u = message.from_user
    cap = (f"{TEXTS[lang]['admin_new_hl']}\n\n"
           f"🎮 Игра: <b>{data['game']}</b>\n"
           f"👤 Ник: <code>{data['nick']}</code>\n"
           f"🆔 ID: <code>{data['g_id']}</code>\n\n"
           f"📨 Отправил: {u.full_name} (@{u.username})")
    try:
        if message.photo:
            file_id, ftype = message.photo[-1].file_id, "photo"
            await bot.send_photo(HIGHLIGHTS_GROUP_ID, file_id, caption=cap, parse_mode="HTML")
        else:
            file_id, ftype = message.video.file_id, "video"
            await bot.send_video(HIGHLIGHTS_GROUP_ID, file_id, caption=cap, parse_mode="HTML")
        db.update_submission(u.id, data['nick'], data['g_id'], data['game'])
        asyncio.create_task(_bg_save_highlight(u, data['game'], data['nick'], data['g_id'], file_id, ftype))
        await message.answer(TEXTS[lang]["hl_success"], reply_markup=get_main_menu(u.id))
    except Exception as e:
        print(f"[hl error] {e}"); await message.answer("Error")
    await state.clear()

async def _bg_save_highlight(u, game, nick, g_id, file_id, ftype):
    try:
        async with aiohttp.ClientSession() as session:
            tg_p = await fs_get(session, "tg_profiles", str(u.id))
            fb_uid = (tg_p or {}).get("firebaseUid", "")
            data = {
                "game": game, "nickname": nick, "gameId": g_id,
                "telegramId": u.id, "telegramUsername": u.username or "",
                "authorName": u.full_name or u.first_name or "",
                "fileId": file_id, "fileType": ftype,
                "title": f"{nick} — {game}",
                "status": "pending", "views": 0, "likes": 0,
                "createdAt": datetime.now().isoformat(),
            }
            if fb_uid: data["uid"] = fb_uid
            await fs_create(session, "highlights", data)
    except Exception as e:
        print(f"[hl firestore error] {e}")

@dp.message(F.text.in_([TEXTS["ru"]["btn_more"], TEXTS["uz"]["btn_more"]]))
async def hl_more(message: Message, state: FSMContext):
    await state.clear(); u = db.get_user(message.from_user.id)
    if u and u.get('last_nickname'):
        await state.update_data(nick=u['last_nickname'], g_id=u['last_game_id'], game=u.get('last_game','?'))
        await state.set_state(Form.media)
        await message.answer(TEXTS[get_lang(message.from_user.id)]["hl_enter_media"])
    else:
        await hl_start(message, state)

# Tournament
@dp.message(F.text.in_([TEXTS["ru"]["btn_tournament"], TEXTS["uz"]["btn_tournament"]]))
async def tour_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(TEXTS[get_lang(message.from_user.id)]["tour_choose"], reply_markup=games_kb("tour"))

@dp.callback_query(F.data.startswith("tour_"))
async def tour_game(call: CallbackQuery, state: FSMContext):
    game = call.data.split("_")[1]; lang = get_lang(call.from_user.id)
    await state.update_data(game=game); await state.set_state(TourForm.team)
    await call.message.edit_text(f"{TEXTS[lang]['tour_discipline'].format(game)}\n\n{TEXTS[lang]['tour_team']}")

@dp.message(TourForm.team)
async def tour_team(message: Message, state: FSMContext):
    await state.update_data(team=message.text); await state.set_state(TourForm.count)
    await message.answer(TEXTS[get_lang(message.from_user.id)]["tour_count"], reply_markup=count_kb)

@dp.callback_query(F.data.startswith("count_"))
async def tour_count(call: CallbackQuery, state: FSMContext):
    count = call.data.split("_")[1]; lang = get_lang(call.from_user.id)
    await state.update_data(count=count); await state.set_state(TourForm.ids)
    await call.message.edit_text(TEXTS[lang]["tour_ids"].format(count))

@dp.message(TourForm.ids)
async def tour_ids(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id); data = await state.get_data(); exp = int(data['count'])
    lst = [x.strip() for x in message.text.split('\n') if x.strip()]
    if len(lst) != exp: return await message.answer(TEXTS[lang]["err_count"].format(len(lst), exp))
    await state.update_data(ids=message.text); await state.set_state(TourForm.nicks)
    await message.answer(TEXTS[lang]["tour_nicks"])

@dp.message(TourForm.nicks)
async def tour_nicks(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id); data = await state.get_data(); exp = int(data['count'])
    lst = [x.strip() for x in message.text.split('\n') if x.strip()]
    if len(lst) != exp: return await message.answer(TEXTS[lang]["err_count"].format(len(lst), exp))
    await state.update_data(nicks=message.text); await state.set_state(TourForm.phone)
    await message.answer(TEXTS[lang]["tour_phone"])

@dp.message(TourForm.phone)
async def tour_phone(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id); data = await state.get_data(); u = message.from_user
    msg = (f"{TEXTS[lang]['admin_new_tour']}\n\n"
           f"🎮 <b>Игра:</b> {data['game']}\n"
           f"👥 <b>Команда:</b> {data['team']}\n"
           f"🔢 <b>Кол-во:</b> {data['count']}\n\n"
           f"🆔 <b>IDs:</b>\n<code>{data['ids']}</code>\n\n"
           f"👤 <b>Ники:</b>\n<code>{data['nicks']}</code>\n\n"
           f"📞 <b>Тел:</b> <code>{message.text}</code>\n"
           f"🔗 <a href='tg://user?id={u.id}'>{u.full_name}</a>")
    try:
        await bot.send_message(TOURNAMENT_GROUP_ID, msg, parse_mode="HTML")
        await message.answer(TEXTS[lang]["tour_success"], reply_markup=get_main_menu(u.id))
    except: await message.answer("Error")
    await state.clear()

# Support
@dp.message(F.text.in_([TEXTS["ru"]["btn_support"], TEXTS["uz"]["btn_support"]]))
async def support(message: Message, state: FSMContext):
    await state.clear(); lang = get_lang(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=TEXTS[lang]["support_btn"], url="https://t.me/Sky_1302")
    ]])
    await message.answer(TEXTS[lang]["support_msg"], reply_markup=kb)

@dp.message(Command("base"))
async def admin_base(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    users = db.get_all(); lines = [f"📊 Всего: {len(users)}"]
    for i, (tid, d) in enumerate(users.items(), 1):
        lines.append(f"{i}. {d['full_name']} (@{d['username']}) | {d['language']}")
    txt = "\n".join(lines)
    for x in range(0, len(txt), 4096): await message.answer(txt[x:x+4096])

async def main():
    while True:
        try: await dp.start_polling(bot)
        except: await asyncio.sleep(5)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
