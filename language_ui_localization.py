"""Language-picker side messages for all supported locales."""
from localization import DEFAULT_LANGUAGE, LANGUAGES, normalize_language

_ROWS = {
"en": ("🌐 Language preferences can only be changed in the bot's private chat.", "📩 Open the bot in private chat, then send /lang.", "⚠️ Your language preference could not be saved. Please try again."),
"my": ("🌐 ဘာသာစကားဆက်တင်ကို Bot Private Chat တွင်သာ ပြောင်းလဲနိုင်ပါသည်။", "📩 Bot Private Chat ကိုဖွင့်ပြီး /lang ပေးပို့ပါ။", "⚠️ ဘာသာစကားဆက်တင်ကို သိမ်းဆည်းမရပါ။ ထပ်မံကြိုးစားပါ။"),
"zh": ("🌐 只能在机器人的私聊中更改语言设置。", "📩 打开机器人的私聊，然后发送 /lang。", "⚠️ 无法保存语言设置，请重试。"),
"es": ("🌐 El idioma solo se puede cambiar en el chat privado del bot.", "📩 Abre el chat privado del bot y envía /lang.", "⚠️ No se pudo guardar el idioma. Inténtalo de nuevo."),
"hi": ("🌐 भाषा सेटिंग केवल बॉट की निजी चैट में बदली जा सकती है।", "📩 बॉट की निजी चैट खोलें और /lang भेजें।", "⚠️ भाषा सेटिंग सेव नहीं हो सकी। फिर कोशिश करें।"),
"ar": ("🌐 يمكن تغيير إعدادات اللغة في المحادثة الخاصة مع البوت فقط.", "📩 افتح المحادثة الخاصة مع البوت ثم أرسل /lang.", "⚠️ تعذّر حفظ إعداد اللغة. حاول مجدداً."),
"pt": ("🌐 O idioma só pode ser alterado no chat privado do bot.", "📩 Abra o chat privado do bot e envie /lang.", "⚠️ Não foi possível salvar o idioma. Tente novamente."),
"fr": ("🌐 La langue peut uniquement être modifiée dans la discussion privée du bot.", "📩 Ouvrez la discussion privée du bot, puis envoyez /lang.", "⚠️ Impossible d’enregistrer la langue. Réessayez."),
"ru": ("🌐 Язык можно изменить только в личном чате с ботом.", "📩 Откройте личный чат с ботом и отправьте /lang.", "⚠️ Не удалось сохранить язык. Попробуйте снова."),
"bn": ("🌐 ভাষা সেটিং শুধু বটের ব্যক্তিগত চ্যাটে পরিবর্তন করা যায়।", "📩 বটের ব্যক্তিগত চ্যাট খুলে /lang পাঠান।", "⚠️ ভাষা সেটিং সংরক্ষণ করা যায়নি। আবার চেষ্টা করুন।"),
"id": ("🌐 Bahasa hanya dapat diubah melalui chat pribadi bot.", "📩 Buka chat pribadi bot, lalu kirim /lang.", "⚠️ Pengaturan bahasa tidak dapat disimpan. Silakan coba lagi."),
"ja": ("🌐 言語設定はボットとのプライベートチャットでのみ変更できます。", "📩 ボットとのプライベートチャットを開き、/lang を送信してください。", "⚠️ 言語設定を保存できませんでした。もう一度お試しください。"),
"ko": ("🌐 언어 설정은 봇과의 비공개 채팅에서만 변경할 수 있습니다.", "📩 봇과의 비공개 채팅을 열고 /lang을 보내세요.", "⚠️ 언어 설정을 저장하지 못했습니다. 다시 시도하세요."),
"th": ("🌐 เปลี่ยนการตั้งค่าภาษาได้เฉพาะในแชตส่วนตัวกับบอทเท่านั้น", "📩 เปิดแชตส่วนตัวกับบอทแล้วส่ง /lang", "⚠️ บันทึกการตั้งค่าภาษาไม่ได้ โปรดลองอีกครั้ง"),
"vi": ("🌐 Chỉ có thể thay đổi ngôn ngữ trong cuộc trò chuyện riêng với bot.", "📩 Mở trò chuyện riêng với bot rồi gửi /lang.", "⚠️ Không thể lưu cài đặt ngôn ngữ. Vui lòng thử lại."),
"tr": ("🌐 Dil ayarları yalnızca botun özel sohbetinde değiştirilebilir.", "📩 Botla özel sohbeti açın ve /lang gönderin.", "⚠️ Dil tercihi kaydedilemedi. Lütfen tekrar deneyin."),
"de": ("🌐 Die Sprache kann nur im privaten Chat mit dem Bot geändert werden.", "📩 Öffne den privaten Chat mit dem Bot und sende /lang.", "⚠️ Die Spracheinstellung konnte nicht gespeichert werden. Bitte erneut versuchen."),
"it": ("🌐 La lingua può essere modificata solo nella chat privata del bot.", "📩 Apri la chat privata del bot e invia /lang.", "⚠️ Impossibile salvare la lingua. Riprova."),
"ur": ("🌐 زبان کی ترتیب صرف بوٹ کی نجی چیٹ میں تبدیل کی جا سکتی ہے۔", "📩 بوٹ کی نجی چیٹ کھولیں اور /lang بھیجیں۔", "⚠️ زبان کی ترتیب محفوظ نہیں ہو سکی۔ دوبارہ کوشش کریں۔"),
"sw": ("🌐 Lugha inaweza kubadilishwa tu kwenye gumzo la faragha la bot.", "📩 Fungua gumzo la faragha la bot kisha tuma /lang.", "⚠️ Mpangilio wa lugha haukuweza kuhifadhiwa. Tafadhali jaribu tena."),
}
if set(_ROWS) != set(LANGUAGES) or any(len(v) != 3 for v in _ROWS.values()):
    raise RuntimeError("Language picker translations must cover all supported locales.")

def translate_language_ui(language: str | None, key: str) -> str:
    locale = normalize_language(language)
    index = {"private_only": 0, "open_private": 1, "save_failed": 2}.get(key)
    return _ROWS.get(locale, _ROWS[DEFAULT_LANGUAGE])[index] if index is not None else key
