import os
import aiohttp
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

# .env dosyasını yükleyin
load_dotenv()

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
API_KEY = os.getenv('ALPHAVANTAGE_API_KEY')
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS', '').split(',')  # Yetkilendirilmiş kullanıcılar
BASE_URL = 'https://www.alphavantage.co/query'
CACHE = {}
CACHE_EXPIRY = timedelta(minutes=15)

# Logging ayarları
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Erişim kontrolü
async def is_authorized(update: Update) -> bool:
    username = update.effective_user.username
    if username not in AUTHORIZED_USERS:
        await update.message.reply_text(
            "⛔ Bu bota erişim izniniz yok. Yetkili kişiyle iletişime geçin.\n\n @paraloperceo"
        )
        return False
    return True

# API'den veri çekme (cache destekli)
async def fetch_indicator(indicator, symbol, interval, time_period=None, series_type=None):
    cache_key = f"{indicator}-{symbol}-{interval}-{time_period}-{series_type}"
    now = datetime.now(timezone.utc)

    if cache_key in CACHE and CACHE[cache_key]['expiry'] > now:
        return CACHE[cache_key]['data']

    params = {
        'function': indicator,
        'symbol': symbol,
        'interval': interval,
        'apikey': API_KEY
    }
    if time_period:
        params['time_period'] = time_period
    if series_type:
        params['series_type'] = series_type

    async with aiohttp.ClientSession() as session:
        async with session.get(BASE_URL, params=params) as response:
            if response.status != 200:
                raise ValueError(f"API'den geçerli bir yanıt alınamadı: {response.status}")
            data = await response.json()
            if "Note" in data or "Error Message" in data:
                raise ValueError("API limiti dolmuş veya parametreler hatalı.")
            CACHE[cache_key] = {'data': data, 'expiry': now + CACHE_EXPIRY}
            return data

# Yeni Strateji: Çoklu gösterge ilişkisi
def advanced_signal_analysis(rsi, sma, ema, atr, price, bollinger_upper, bollinger_lower):
    signals = {
        'RSI': 2 if rsi < 30 else -2 if rsi > 70 else 0,
        'SMA': 1 if price > sma else -1,
        'EMA': 1 if price > ema else -1,
        'Bollinger': 1 if price < bollinger_lower else -1 if price > bollinger_upper else 0,
        'ATR': 1 if atr / price < 0.02 else -1
    }

    score = sum(signals.values())
    return "LONG sinyali" if score > 0 else "SHORT sinyali", signals

# TP ve SL hesaplama
def calculate_tp_sl(price, atr, signal):
    tp = price + (2 * atr) if signal == "LONG sinyali" else price - (2 * atr)
    sl = price - (2 * atr) if signal == "LONG sinyali" else price + (2 * atr)
    return round(tp, 2), round(sl, 2)

# Vadeler için yapı
vadeler = {
    "kısa": ("60min", 7, 10, 10, 7),
    "orta": ("daily", 14, 20, 20, 14),
    "uzun": ("weekly", 30, 50, 50, 30)
}

# Dil seçimi yapma
async def select_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Türkçe", callback_data='lang_tr')],
        [InlineKeyboardButton("English", callback_data='lang_en')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Lütfen bir dil seçin / Please select a language:",
        reply_markup=reply_markup
    )

# Dil seçiminden sonra işlem yapma
async def handle_language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_language = query.data

    if selected_language == 'lang_tr':
        context.user_data['language'] = 'tr'
        await query.edit_message_text("Dil Türkçe olarak ayarlandı. / Language set to Turkish.")
    elif selected_language == 'lang_en':
        context.user_data['language'] = 'en'
        await query.edit_message_text("Language set to English. / Dil İngilizce olarak ayarlandı.")

# Kullanıcıdan işlem çifti isteme
async def start_forex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return

    language = context.user_data.get('language', 'tr')
    if language == 'en':
        await update.message.reply_text(
            "Please enter the trading pair (e.g., USDTRY):"
        )
    else:
        await update.message.reply_text(
            "Lütfen işlem çiftini yazınız (örnek: USDTRY):"
        )
    context.user_data['awaiting_pair'] = True

# İşlem çiftini aldıktan sonra vade türünü sorma
async def get_vade_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting_pair'):
        pair = update.message.text.upper()
        context.user_data['pair'] = pair
        context.user_data['awaiting_pair'] = False

        language = context.user_data.get('language', 'tr')
        if language == 'en':
            keyboard = [
                [InlineKeyboardButton("Short Term", callback_data='kısa')],
                [InlineKeyboardButton("Medium Term", callback_data='orta')],
                [InlineKeyboardButton("Long Term", callback_data='uzun')],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"Your trading pair: {pair}\nPlease select the term type:",
                reply_markup=reply_markup
            )
        else:
            keyboard = [
                [InlineKeyboardButton("Kısa Vade", callback_data='kısa')],
                [InlineKeyboardButton("Orta Vade", callback_data='orta')],
                [InlineKeyboardButton("Uzun Vade", callback_data='uzun')],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"İşlem çiftiniz: {pair}\nLütfen vade türünü seçiniz:",
                reply_markup=reply_markup
            )

# Vade seçildikten sonra analiz yapma
async def handle_vade_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    language = context.user_data.get('language', 'tr')

    # Butonları kaldır ve yükleniyor mesajı gönder
    if language == 'en':
        await query.edit_message_text("Analyzing...")
    else:
        await query.edit_message_text("Analiz ediliyor...")

    vade = query.data
    pair = context.user_data.get('pair')

    if not pair:
        if language == 'en':
            await query.edit_message_text("Trading pair information not found. Please start again.")
        else:
            await query.edit_message_text("İşlem çifti bilgisi bulunamadı. Lütfen tekrar başlayın.")
        return

    interval, rsi_period, sma_period, ema_period, atr_period = vadeler[vade]

    try:
        rsi_data = await fetch_indicator('RSI', pair, interval, time_period=rsi_period, series_type='close')
        sma_data = await fetch_indicator('SMA', pair, interval, time_period=sma_period, series_type='close')
        ema_data = await fetch_indicator('EMA', pair, interval, time_period=ema_period, series_type='close')
        price_data = await fetch_indicator('TIME_SERIES_DAILY', pair, interval)
        atr_data = await fetch_indicator('ATR', pair, interval, time_period=atr_period)

        latest_rsi = float(list(rsi_data["Technical Analysis: RSI"].values())[0]["RSI"])
        latest_sma = float(list(sma_data["Technical Analysis: SMA"].values())[0]["SMA"])
        latest_ema = float(list(ema_data["Technical Analysis: EMA"].values())[0]["EMA"])
        latest_price = float(list(price_data["Time Series (Daily)"].values())[0]["4. close"])
        latest_atr = float(list(atr_data["Technical Analysis: ATR"].values())[0]["ATR"])

        signal, signals = advanced_signal_analysis(
            rsi=latest_rsi, sma=latest_sma, ema=latest_ema, atr=latest_atr, price=latest_price,
            bollinger_upper=latest_price * 1.05, bollinger_lower=latest_price * 0.95
        )
        tp, sl = calculate_tp_sl(latest_price, latest_atr, signal)

        emoji = "🚀 Long" if signal == "LONG sinyali" else "📉 Short"

        if language == 'en':
            new_message = (
                f"🪬 Trading Pair: {pair}\n"
                f"Term: {vade.capitalize()} Term\n\n"
                f"{emoji}\nTP: {tp}\nSL: {sl}\n\n"
            )
        else:
            new_message = (
                f"🪬 İşlem Çifti: {pair}\n"
                f"Vade: {vade.capitalize()} Vade\n\n"
                f"{emoji}\nTP: {tp}\nSL: {sl}\n\n"
            )

        # Mevcut mesaj ile karşılaştırma
        await query.edit_message_text(new_message)

    except Exception as e:
        logger.error(f"Hata: {e}")
        if language == 'en':
            await query.edit_message_text(f"Error: {e}")
        else:
            await query.edit_message_text(f"Hata: {e}")

# Bot başlatma
def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", select_language))
    application.add_handler(CallbackQueryHandler(handle_language_selection, pattern='^lang_'))
    application.add_handler(CommandHandler("forex", start_forex))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, get_vade_type))
    application.add_handler(CallbackQueryHandler(handle_vade_selection))

    application.run_polling()

if __name__ == '__main__':
    main()
