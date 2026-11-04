"""Localized diagnostics and Telegram command-menu descriptions."""
from __future__ import annotations
from localization import DEFAULT_LANGUAGE, LANGUAGES, normalize_language

KEYS = ["ping_prefix","id_title","chat_id","user_id","id_command","command_start","command_lang","command_id","command_cancel"]
_ROWS = {
    "en": ("pong — ", "ℹ️ *Group Help*", "*CHAT ID:*", "*YOUR ID:*", "Command: /id", "🏠 Open the main menu", "🌐 Change language", "ℹ️ Show chat and user IDs", "❌ Cancel the current action"),
    "my": ("pong — ", "ℹ️ *Group အကူအညီ*", "*CHAT ID:*", "*သင့် USER ID:*", "Command: /id", "🏠 ပင်မမီနူးဖွင့်ရန်", "🌐 ဘာသာစကားပြောင်းရန်", "ℹ️ Chat နှင့် User ID များကြည့်ရန်", "❌ လက်ရှိလုပ်ဆောင်ချက်ကို ပယ်ဖျက်ရန်"),
    "zh": ("pong — ", "ℹ️ *群组帮助*", "*聊天 ID：*", "*您的 ID：*", "命令：/id", "🏠 打开主菜单", "🌐 更改语言", "ℹ️ 查看聊天和用户 ID", "❌ 取消当前操作"),
    "es": ("pong — ", "ℹ️ *Ayuda del grupo*", "*ID DEL CHAT:*", "*TU ID:*", "Comando: /id", "🏠 Abrir menú principal", "🌐 Cambiar idioma", "ℹ️ Mostrar ID del chat y del usuario", "❌ Cancelar la acción actual"),
    "hi": ("pong — ", "ℹ️ *ग्रुप सहायता*", "*CHAT ID:*", "*आपकी ID:*", "कमांड: /id", "🏠 मुख्य मेनू खोलें", "🌐 भाषा बदलें", "ℹ️ चैट और उपयोगकर्ता ID दिखाएँ", "❌ वर्तमान कार्रवाई रद्द करें"),
    "ar": ("pong — ", "ℹ️ *مساعدة المجموعة*", "*معرّف المحادثة:*", "*معرّفك:*", "الأمر: /id", "🏠 فتح القائمة الرئيسية", "🌐 تغيير اللغة", "ℹ️ عرض معرّف المحادثة والمستخدم", "❌ إلغاء الإجراء الحالي"),
    "pt": ("pong — ", "ℹ️ *Ajuda do grupo*", "*ID DO CHAT:*", "*SEU ID:*", "Comando: /id", "🏠 Abrir o menu principal", "🌐 Alterar idioma", "ℹ️ Mostrar IDs do chat e do usuário", "❌ Cancelar a ação atual"),
    "fr": ("pong — ", "ℹ️ *Aide du groupe*", "*ID DE LA DISCUSSION :*", "*VOTRE ID :*", "Commande : /id", "🏠 Ouvrir le menu principal", "🌐 Changer de langue", "ℹ️ Afficher les ID de la discussion et de l’utilisateur", "❌ Annuler l’action en cours"),
    "ru": ("pong — ", "ℹ️ *Помощь по группе*", "*ID ЧАТА:*", "*ВАШ ID:*", "Команда: /id", "🏠 Открыть главное меню", "🌐 Изменить язык", "ℹ️ Показать ID чата и пользователя", "❌ Отменить текущее действие"),
    "bn": ("pong — ", "ℹ️ *গ্রুপ সহায়তা*", "*CHAT ID:*", "*আপনার ID:*", "কমান্ড: /id", "🏠 প্রধান মেনু খুলুন", "🌐 ভাষা পরিবর্তন", "ℹ️ চ্যাট ও ব্যবহারকারীর ID দেখুন", "❌ বর্তমান কাজ বাতিল করুন"),
    "id": ("pong — ", "ℹ️ *Bantuan Grup*", "*ID CHAT:*", "*ID ANDA:*", "Perintah: /id", "🏠 Buka Menu Utama", "🌐 Ubah Bahasa", "ℹ️ Tampilkan ID chat dan pengguna", "❌ Batalkan tindakan saat ini"),
    "ja": ("pong — ", "ℹ️ *グループヘルプ*", "*チャット ID：*", "*あなたの ID：*", "コマンド：/id", "🏠 メインメニューを開く", "🌐 言語を変更", "ℹ️ チャットとユーザーの ID を表示", "❌ 現在の操作をキャンセル"),
    "ko": ("pong — ", "ℹ️ *그룹 도움말*", "*채팅 ID:*", "*내 ID:*", "명령어: /id", "🏠 메인 메뉴 열기", "🌐 언어 변경", "ℹ️ 채팅 및 사용자 ID 표시", "❌ 현재 작업 취소"),
    "th": ("pong — ", "ℹ️ *ความช่วยเหลือกลุ่ม*", "*CHAT ID:*", "*ID ของคุณ:*", "คำสั่ง: /id", "🏠 เปิดเมนูหลัก", "🌐 เปลี่ยนภาษา", "ℹ️ แสดง ID แชตและผู้ใช้", "❌ ยกเลิกการทำงานปัจจุบัน"),
    "vi": ("pong — ", "ℹ️ *Trợ giúp nhóm*", "*ID CUỘC TRÒ CHUYỆN:*", "*ID CỦA BẠN:*", "Lệnh: /id", "🏠 Mở Menu chính", "🌐 Đổi ngôn ngữ", "ℹ️ Hiển thị ID cuộc trò chuyện và người dùng", "❌ Hủy thao tác hiện tại"),
    "tr": ("pong — ", "ℹ️ *Grup Yardımı*", "*SOHBET ID:*", "*KULLANICI ID'NİZ:*", "Komut: /id", "🏠 Ana Menüyü Aç", "🌐 Dili Değiştir", "ℹ️ Sohbet ve kullanıcı kimliklerini göster", "❌ Geçerli işlemi iptal et"),
    "de": ("pong — ", "ℹ️ *Gruppenhilfe*", "*CHAT-ID:*", "*DEINE ID:*", "Befehl: /id", "🏠 Hauptmenü öffnen", "🌐 Sprache ändern", "ℹ️ Chat- und Benutzer-ID anzeigen", "❌ Aktuelle Aktion abbrechen"),
    "it": ("pong — ", "ℹ️ *Aiuto del gruppo*", "*ID CHAT:*", "*IL TUO ID:*", "Comando: /id", "🏠 Apri il menu principale", "🌐 Cambia lingua", "ℹ️ Mostra ID chat e utente", "❌ Annulla l’azione corrente"),
    "ur": ("pong — ", "ℹ️ *گروپ کی مدد*", "*چیٹ ID:*", "*آپ کا ID:*", "کمانڈ: /id", "🏠 مرکزی مینو کھولیں", "🌐 زبان تبدیل کریں", "ℹ️ چیٹ اور صارف کی ID دکھائیں", "❌ موجودہ کارروائی منسوخ کریں"),
    "sw": ("pong — ", "ℹ️ *Msaada wa Kikundi*", "*CHAT ID:*", "*ID YAKO:*", "Amri: /id", "🏠 Fungua Menyu Kuu", "🌐 Badilisha Lugha", "ℹ️ Onyesha ID ya gumzo na mtumiaji", "❌ Ghairi kitendo cha sasa"),
}
if set(_ROWS) != set(LANGUAGES):
    raise RuntimeError("System UI translations must cover every supported locale.")
CATALOG = {locale: dict(zip(KEYS, row, strict=True)) for locale, row in _ROWS.items()}

def translate_system(language: str | None, key: str) -> str:
    locale = normalize_language(language)
    return CATALOG[locale].get(key, key)

def command_descriptions(language: str | None):
    locale = normalize_language(language)
    values = CATALOG[locale]
    return [
        ("start", values["command_start"]),
        ("lang", values["command_lang"]),
        ("id", values["command_id"]),
        ("cancel", values["command_cancel"]),
    ]
