import asyncio
import json
import logging
import threading
import firebase_admin
from firebase_admin import credentials, firestore
from telegram import Update, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
import os

# 1. Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = '8518828255:AAECuDFQKBQZNtD5B1o4ltQUGpX3jCxP40U'
ADMIN_ID = 387881523
WEBAPP_URL = 'https://tmaminiapp.github.io/mariamstyle/'
FIREBASE_KEY_PATH = 'config.json'  # файл с ключами Firebase

db_fs = None


# --- ИНИЦИАЛИЗАЦИЯ FIREBASE ---
def init_firebase():
    global db_fs
    try:
        # Проверяем, существует ли файл
        if not os.path.exists(FIREBASE_KEY_PATH):
            logging.error(f"❌ Файл {FIREBASE_KEY_PATH} не найден!")
            return False

        cred = credentials.Certificate(FIREBASE_KEY_PATH)
        firebase_admin.initialize_app(cred)
        db_fs = firestore.client()
        logging.info("✅ Firebase успешно подключен")
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка Firebase: {e}")
        return False


# --- ФОНОВОЕ СЛУШАНИЕ ИЗМЕНЕНИЙ (УВЕДОМЛЕНИЕ КЛИЕНТУ) ---
def setup_firebase_listener(loop, application):
    global db_fs
    if db_fs is None:
        logging.error("❌ Firebase не инициализирован, слушатель не запущен")
        return

    def on_snapshot(col_snapshot, changes, read_time):
        for change in changes:
            try:
                if change.type.name == 'MODIFIED':
                    order_data = change.document.to_dict()
                    status = order_data.get('status')
                    order_id = order_data.get('order_id')
                    client_id = order_data.get('user', {}).get('id')

                    if client_id:
                        if status == 'Отправлен':
                            msg = f"📦 <b>Ваш заказ #{order_id} отправлен!</b>\nСкоро он будет у вас. Спасибо за покупку! ✨"
                        elif status == 'Доставлен':
                            msg = f"✅ <b>Ваш заказ #{order_id} доставлен!</b>\nНадеемся, вам всё понравилось. Будем рады вашему отзыву! ✨"
                        else:
                            return

                        # Отправка сообщения
                        asyncio.run_coroutine_threadsafe(
                            application.bot.send_message(chat_id=client_id, text=msg, parse_mode='HTML'),
                            loop
                        )
                        logging.info(f"📩 Уведомление ({status}) отправлено клиенту {client_id}")
            except Exception as e:
                logging.error(f"Ошибка в on_snapshot: {e}")

    try:
        db_fs.collection('orders').on_snapshot(on_snapshot)
        logging.info("👂 Слушатель Firebase запущен")
    except Exception as e:
        logging.error(f"❌ Ошибка при запуске слушателя Firebase: {e}")


# --- ОБРАБОТКА НОВОГО ЗАКАЗА ---
async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw_json = update.effective_message.web_app_data.data
        data = json.loads(raw_json)
        user_id = update.effective_user.id
        username = update.effective_user.username or "нет username"

        logging.info(f"📦 Получен новый заказ от пользователя {user_id}")

        # Извлекаем все данные
        order_id = data.get('order_id', '???')
        name = data.get('customer_name') or data.get('name') or 'Не указано'
        phone = data.get('customer_phone') or data.get('phone') or 'Не указано'
        address = data.get('address') or data.get('customer_address') or 'Не указан'
        delivery = data.get('delivery') or data.get('delivery_type') or 'Не выбрана'
        total = data.get('order_total') or data.get('total') or 0

        # Получаем состав заказа
        items_list = data.get('items_text')
        if not items_list and 'items' in data:
            items = data.get('items', [])
            items_list = "\n".join(
                [
                    f"▫️ {i.get('title')} ({i.get('size') or i.get('selSize') or '-'}) — {i.get('price')} ₽ x{i.get('count') or i.get('qty') or 1}"
                    for i in items]
            )

        if not items_list:
            items_list = "Состав не указан"

        # Сохраняем в Firebase
        if db_fs:
            try:
                order_entry = {
                    **data,
                    'status': 'Новый',
                    'user': {'id': user_id, 'username': username},
                    'createdAt': firestore.SERVER_TIMESTAMP
                }
                db_fs.collection("orders").add(order_entry)
                logging.info(f"✅ Заказ #{order_id} сохранен в Firebase")
            except Exception as e:
                logging.error(f"❌ Ошибка сохранения в Firebase: {e}")

        # Отправляем уведомление админу
        admin_message = (
            f"🛍 <b>НОВЫЙ ЗАКАЗ #{order_id}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Клиент:</b> {name}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"📞 <b>Телефон:</b> <code>{phone}</code>\n"
            f"🚚 <b>Доставка:</b> {delivery}\n"
            f"📍 <b>Адрес:</b> {address}\n\n"
            f"📋 <b>СОСТАВ ЗАКАЗА:</b>\n{items_list}\n\n"
            f"💰 <b>ИТОГО: {total} ₽</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 <a href='tg://user?id={user_id}'>Связаться с клиентом</a>"
        )

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_message,
            parse_mode='HTML',
            disable_web_page_preview=True
        )

        await update.message.reply_text(f"✅ Заказ #{order_id} принят!")

    except Exception as e:
        logging.error(f"❌ Ошибка в web_app_data: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("🛍 Открыть Магазин", web_app=WebAppInfo(url=WEBAPP_URL))]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Добро пожаловать в MARIAM style! 👋\n"
        "Нажмите кнопку ниже, чтобы открыть каталог.",
        reply_markup=reply_markup
    )


def main():
    # Инициализируем Firebase
    if not init_firebase():
        logging.warning("⚠️ Продолжаем без Firebase (уведомления о статусе не будут работать)")

    # Создаем приложение бота
    application = ApplicationBuilder().token(TOKEN).build()

    # Добавляем обработчики
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))

    # Запускаем слушатель Firebase в отдельном потоке (только если Firebase инициализирован)
    if db_fs:
        try:
            loop = asyncio.new_event_loop()
            listener_thread = threading.Thread(
                target=setup_firebase_listener,
                args=(loop, application),
                daemon=True
            )
            listener_thread.start()
            logging.info("👂 Слушатель Firebase запущен в отдельном потоке")
        except Exception as e:
            logging.error(f"❌ Ошибка при запуске слушателя Firebase: {e}")

    logging.info("🚀 Бот запущен и готов к работе!")
    application.run_polling()


if __name__ == '__main__':
    main()