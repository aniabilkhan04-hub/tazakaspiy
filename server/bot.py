# Telegram-бот для волонтёров: сообщить о свалке, отметить, что мусор убран,
# добавить пункт переработки / эко-магазин / место для посадки дерева.
# Весь сценарий построен на кнопках (ReplyKeyboardMarkup), без ввода команд руками.

import logging
import os
import time

from telegram import ReplyKeyboardMarkup, KeyboardButton, Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from db import get_or_create_user, insert_report, count_recent_reports, delete_upload

# Допустимая область (Атырау и окрестности) — точки за её пределами не принимаются
LAT_MIN, LAT_MAX = 46.7, 47.5
LON_MIN, LON_MAX = 51.5, 52.4

MAX_DESCRIPTION_LEN = 500
MAX_REPORTS_PER_DAY = 10

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')

BUTTON_TO_TYPE = {
    '📸 Сообщить о свалке': 'dump',
    '✅ Я убрал мусор': 'cleaned',
    '♻️ Пункт переработки': 'recycling',
    '🌳 Место для посадки дерева': 'tree',
    '🛍 Эко-магазин': 'shop',
}

TYPE_LABELS = {
    'dump': 'несанкционированная свалка',
    'cleaned': 'место, где убран мусор',
    'recycling': 'пункт переработки',
    'tree': 'место для посадки дерева',
    'shop': 'эко-магазин',
}

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ['📸 Сообщить о свалке', '✅ Я убрал мусор'],
        ['♻️ Пункт переработки', '🌳 Место для посадки дерева'],
        ['🛍 Эко-магазин', '🏆 Мой рейтинг'],
    ],
    resize_keyboard=True,
)

CANCEL_ONLY = ReplyKeyboardMarkup([['❌ Отмена']], resize_keyboard=True)

LOCATION_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton('📍 Отправить геолокацию', request_location=True)], ['❌ Отмена']],
    resize_keyboard=True,
    one_time_keyboard=True,
)

SKIP_DESCRIPTION_KEYBOARD = ReplyKeyboardMarkup([['-'], ['❌ Отмена']], resize_keyboard=True)

# chat_id -> { "type": str, "step": str, "photo_path": str, "lat": float, "lon": float }
sessions = {}


def discard_session(chat_id):
    """Сбрасывает незавершённый сценарий и удаляет уже скачанное фото."""
    session = sessions.pop(chat_id, None)
    if session:
        delete_upload(session.get('photo_path'))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    discard_session(chat_id)
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    await update.message.reply_text(
        f"Привет, {user.first_name or 'друг'}! 🌱\n\n"
        "Это эко-бот города Атырау. Здесь вы можете сообщить о несанкционированной свалке, "
        "отметить, что убрали мусор, а также добавить на карту пункт переработки, эко-магазин "
        "или место для посадки дерева.\n\nВыберите действие на клавиатуре ниже 👇",
        reply_markup=MAIN_MENU,
    )


async def download_photo(context: ContextTypes.DEFAULT_TYPE, file_id: str, chat_id: int) -> str:
    tg_file = await context.bot.get_file(file_id)
    filename = f"photo_{chat_id}_{int(time.time() * 1000)}.jpg"
    dest = os.path.join(UPLOADS_DIR, filename)
    await tg_file.download_to_drive(dest)
    return f"/uploads/{filename}"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None:
        return
    chat_id = update.effective_chat.id
    text = msg.text

    if update.effective_chat.type != 'private':
        return

    if text == '❌ Отмена':
        discard_session(chat_id)
        await msg.reply_text('Действие отменено.', reply_markup=MAIN_MENU)
        return

    if text == '🏆 Мой рейтинг':
        user = update.effective_user
        db_user = get_or_create_user(user.id, user.username, user.first_name)
        await msg.reply_text(
            f"Ваш рейтинг: ⭐ {db_user['rating']}\n(количество одобренных заявок «Я убрал мусор»)",
            reply_markup=MAIN_MENU,
        )
        return

    # Начало нового сценария по кнопке из главного меню
    if text in BUTTON_TO_TYPE:
        report_type = BUTTON_TO_TYPE[text]
        user = update.effective_user
        db_user = get_or_create_user(user.id, user.username, user.first_name)
        if count_recent_reports(db_user['id']) >= MAX_REPORTS_PER_DAY:
            await msg.reply_text(
                f'Вы отправили максимум заявок за сутки ({MAX_REPORTS_PER_DAY}). Попробуйте завтра 🌙',
                reply_markup=MAIN_MENU,
            )
            return
        discard_session(chat_id)
        sessions[chat_id] = {'type': report_type, 'step': 'photo'}
        await msg.reply_text(
            f"Вы отмечаете: «{TYPE_LABELS[report_type]}».\n\nПришлите, пожалуйста, фото 📸",
            reply_markup=CANCEL_ONLY,
        )
        return

    session = sessions.get(chat_id)
    if not session:
        await msg.reply_text('Выберите действие на клавиатуре ниже 👇', reply_markup=MAIN_MENU)
        return

    if session['step'] == 'photo':
        if not msg.photo:
            await msg.reply_text('Пожалуйста, отправьте именно фото 📸', reply_markup=CANCEL_ONLY)
            return
        file_id = msg.photo[-1].file_id
        try:
            session['photo_path'] = await download_photo(context, file_id, chat_id)
        except Exception:
            await msg.reply_text('Не удалось загрузить фото, попробуйте ещё раз.', reply_markup=CANCEL_ONLY)
            return
        session['step'] = 'location'
        await msg.reply_text('Отлично! Теперь отправьте геолокацию места 📍', reply_markup=LOCATION_KEYBOARD)
        return

    if session['step'] == 'location':
        if not msg.location:
            await msg.reply_text(
                'Пожалуйста, отправьте геолокацию, нажав на кнопку ниже 📍',
                reply_markup=LOCATION_KEYBOARD,
            )
            return
        lat, lon = msg.location.latitude, msg.location.longitude
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            await msg.reply_text(
                'Эта точка находится за пределами Атырау и окрестностей. '
                'Отправьте геолокацию места в городе 📍',
                reply_markup=LOCATION_KEYBOARD,
            )
            return
        session['lat'] = lat
        session['lon'] = lon
        session['step'] = 'description'
        await msg.reply_text(
            'Добавьте короткое описание (или отправьте «-», чтобы пропустить)',
            reply_markup=SKIP_DESCRIPTION_KEYBOARD,
        )
        return

    if session['step'] == 'description':
        if not text:
            await msg.reply_text(
                'Пожалуйста, отправьте текст описания или «-»', reply_markup=SKIP_DESCRIPTION_KEYBOARD
            )
            return
        if len(text) > MAX_DESCRIPTION_LEN:
            await msg.reply_text(
                f'Слишком длинное описание (максимум {MAX_DESCRIPTION_LEN} символов). Сократите, пожалуйста.',
                reply_markup=SKIP_DESCRIPTION_KEYBOARD,
            )
            return
        description = None if text == '-' else text
        user = update.effective_user
        db_user = get_or_create_user(user.id, user.username, user.first_name)

        insert_report(
            session['type'],
            session['lat'],
            session['lon'],
            description,
            session.get('photo_path'),
            db_user['id'],
        )
        sessions.pop(chat_id, None)

        extra = (
            ' После одобрения ваш рейтинг вырастет, а ваше имя из Telegram появится на сайте! 🌟'
            if session['type'] == 'cleaned'
            else ''
        )
        await msg.reply_text(f"✅ Спасибо! Ваша заявка отправлена на модерацию.{extra}", reply_markup=MAIN_MENU)
        return


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.getLogger(__name__).error('Ошибка в боте', exc_info=context.error)


def build_bot() -> Application:
    token = os.environ['TELEGRAM_BOT_TOKEN']
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    application.add_error_handler(on_error)
    return application
