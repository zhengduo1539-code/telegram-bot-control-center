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

_EXTRA_ROWS = {
    "en": {"deep_link_invalid": "⚠️ This group could not be verified. Check that the bot is a member and that your admin permissions are current.", "deep_link_verify": "⚠️ I couldn't verify the selected group. You can choose a group manually from the main menu."},
    "my": {"deep_link_invalid": "⚠️ ဤ Group ကို အတည်မပြုနိုင်ပါ။ Bot သည် Group အဖွဲ့ဝင်ဖြစ်ကြောင်းနှင့် သင့် Admin ခွင့်ပြုချက် ရှိကြောင်း စစ်ဆေးပါ။", "deep_link_verify": "⚠️ ရွေးချယ်ထားသော Group ကို အတည်မပြုနိုင်ပါ။ Main Menu မှ Group ကို ကိုယ်တိုင်ရွေးချယ်နိုင်ပါသည်။"},
    "zh": {"deep_link_invalid": "⚠️ 无法验证此群组。请确认机器人仍是成员，且您的管理员权限有效。", "deep_link_verify": "⚠️ 无法验证所选群组。您可以从主菜单手动选择群组。"},
    "es": {"deep_link_invalid": "⚠️ No se pudo verificar este grupo. Comprueba que el bot sea miembro y que tus permisos de administrador sigan vigentes.", "deep_link_verify": "⚠️ No se pudo verificar el grupo seleccionado. Puedes elegir un grupo manualmente desde el menú principal."},
    "hi": {"deep_link_invalid": "⚠️ इस ग्रुप की पुष्टि नहीं हो सकी। जाँचें कि बॉट सदस्य है और आपके एडमिन अधिकार अभी भी मौजूद हैं।", "deep_link_verify": "⚠️ चुने गए ग्रुप की पुष्टि नहीं हो सकी। आप मुख्य मेनू से ग्रुप चुन सकते हैं।"},
    "ar": {"deep_link_invalid": "⚠️ تعذّر التحقق من هذه المجموعة. تأكد من أن البوت عضو وأن صلاحياتك الإدارية ما زالت سارية.", "deep_link_verify": "⚠️ تعذّر التحقق من المجموعة المحددة. يمكنك اختيار مجموعة يدوياً من القائمة الرئيسية."},
    "pt": {"deep_link_invalid": "⚠️ Não foi possível verificar este grupo. Confira se o bot é membro e se suas permissões de administrador ainda estão válidas.", "deep_link_verify": "⚠️ Não foi possível verificar o grupo selecionado. Você pode escolher um grupo manualmente no menu principal."},
    "fr": {"deep_link_invalid": "⚠️ Impossible de vérifier ce groupe. Vérifiez que le bot en est membre et que vos droits d’administrateur sont toujours valides.", "deep_link_verify": "⚠️ Impossible de vérifier le groupe sélectionné. Vous pouvez choisir un groupe manuellement depuis le menu principal."},
    "ru": {"deep_link_invalid": "⚠️ Не удалось проверить эту группу. Убедитесь, что бот состоит в ней, а ваши права администратора действуют.", "deep_link_verify": "⚠️ Не удалось проверить выбранную группу. Вы можете выбрать группу вручную в главном меню."},
    "bn": {"deep_link_invalid": "⚠️ এই গ্রুপ যাচাই করা যায়নি। বট সদস্য কি না এবং আপনার অ্যাডমিন অনুমতি আছে কি না পরীক্ষা করুন।", "deep_link_verify": "⚠️ নির্বাচিত গ্রুপ যাচাই করা যায়নি। প্রধান মেনু থেকে নিজে গ্রুপ বেছে নিতে পারেন।"},
    "id": {"deep_link_invalid": "⚠️ Grup ini tidak dapat diverifikasi. Pastikan bot adalah anggota dan izin admin Anda masih berlaku.", "deep_link_verify": "⚠️ Grup yang dipilih tidak dapat diverifikasi. Anda dapat memilih grup secara manual dari menu utama."},
    "ja": {"deep_link_invalid": "⚠️ このグループを確認できません。ボットが参加しており、管理者権限が有効か確認してください。", "deep_link_verify": "⚠️ 選択したグループを確認できません。メインメニューから手動でグループを選択できます。"},
    "ko": {"deep_link_invalid": "⚠️ 이 그룹을 확인할 수 없습니다. 봇이 멤버인지, 관리자 권한이 유효한지 확인하세요.", "deep_link_verify": "⚠️ 선택한 그룹을 확인할 수 없습니다. 메인 메뉴에서 그룹을 직접 선택할 수 있습니다."},
    "th": {"deep_link_invalid": "⚠️ ไม่สามารถตรวจสอบกลุ่มนี้ได้ โปรดตรวจสอบว่าบอทเป็นสมาชิกและสิทธิ์ผู้ดูแลของคุณยังใช้งานได้", "deep_link_verify": "⚠️ ไม่สามารถตรวจสอบกลุ่มที่เลือกได้ คุณสามารถเลือกกลุ่มด้วยตนเองจากเมนูหลัก"},
    "vi": {"deep_link_invalid": "⚠️ Không thể xác minh nhóm này. Hãy kiểm tra bot còn là thành viên và quyền quản trị của bạn vẫn còn hiệu lực.", "deep_link_verify": "⚠️ Không thể xác minh nhóm đã chọn. Bạn có thể chọn nhóm thủ công từ Menu chính."},
    "tr": {"deep_link_invalid": "⚠️ Bu grup doğrulanamadı. Botun üye olduğunu ve yönetici izinlerinizin geçerli olduğunu kontrol edin.", "deep_link_verify": "⚠️ Seçilen grup doğrulanamadı. Ana menüden manuel olarak grup seçebilirsiniz."},
    "de": {"deep_link_invalid": "⚠️ Diese Gruppe konnte nicht überprüft werden. Prüfe, ob der Bot Mitglied ist und deine Adminrechte noch gültig sind.", "deep_link_verify": "⚠️ Die ausgewählte Gruppe konnte nicht überprüft werden. Du kannst im Hauptmenü manuell eine Gruppe auswählen."},
    "it": {"deep_link_invalid": "⚠️ Impossibile verificare questo gruppo. Controlla che il bot sia membro e che i tuoi permessi di amministratore siano validi.", "deep_link_verify": "⚠️ Impossibile verificare il gruppo selezionato. Puoi scegliere manualmente un gruppo dal menu principale."},
    "ur": {"deep_link_invalid": "⚠️ اس گروپ کی تصدیق نہیں ہو سکی۔ چیک کریں کہ بوٹ رکن ہے اور آپ کے ایڈمن اختیارات موجود ہیں۔", "deep_link_verify": "⚠️ منتخب گروپ کی تصدیق نہیں ہو سکی۔ آپ مرکزی مینو سے گروپ خود منتخب کر سکتے ہیں۔"},
    "sw": {"deep_link_invalid": "⚠️ Kikundi hiki hakikuweza kuthibitishwa. Hakikisha bot ni mwanachama na ruhusa zako za msimamizi bado zipo.", "deep_link_verify": "⚠️ Kikundi kilichochaguliwa hakikuweza kuthibitishwa. Unaweza kuchagua kikundi mwenyewe kutoka kwenye menyu kuu."},
}
for _locale, _extra in _EXTRA_ROWS.items():
    CATALOG[_locale].update(_extra)

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
