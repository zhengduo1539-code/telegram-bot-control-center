"""Telegram destination authorization with localized user-facing errors."""
from __future__ import annotations

from telegram import Bot
from telegram.error import Forbidden, TelegramError

from localization import DEFAULT_LANGUAGE, LANGUAGES, normalize_language


class ChatAccessError(ValueError):
    """Raised when the requester or bot cannot publish to the selected chat."""


_KEYS = ("unavailable", "admin", "channel_admin", "post_messages", "not_member", "restricted")
_ROWS = {
"en": ("I couldn't verify this chat. Check its Chat ID and make sure the bot is a member.", "Only a group administrator or channel owner can create schedules here.", "The bot must be a channel administrator before it can publish posts.", "The bot is missing the 'Post Messages' permission in this channel.", "The bot is not a member of this group. Add it and try again.", "The bot is restricted and cannot send messages in this group."),
"my": ("ဤ Chat ကို အတည်မပြုနိုင်ပါ။ Chat ID ကို စစ်ဆေးပြီး Bot ကို Group ထဲသို့ ထည့်ထားကြောင်း သေချာပါစေ။", "ဤနေရာတွင် Schedule ပြုလုပ်ရန် Group Admin သို့မဟုတ် Channel Owner ဖြစ်ရပါမည်။", "Post တင်နိုင်ရန် Bot ကို Channel Admin အဖြစ် သတ်မှတ်ပေးရပါမည်။", "ဤ Channel တွင် Bot ၌ Post Messages ခွင့်ပြုချက် မရှိပါ။", "Bot သည် ဤ Group ၏ အဖွဲ့ဝင်မဟုတ်ပါ။ Group ထဲသို့ ထည့်ပြီး ထပ်ကြိုးစားပါ။", "Bot ကို ဤ Group တွင် ကန့်သတ်ထားသောကြောင့် Message ပို့၍မရပါ။"),
"zh": ("无法验证此聊天。请检查 Chat ID，并确认机器人已加入。", "只有群组管理员或频道所有者可以在此创建日程。", "机器人必须先成为频道管理员才能发布帖子。", "机器人在此频道缺少“发布消息”权限。", "机器人尚未加入此群组。请先添加机器人再试。", "机器人在此群组受到限制，无法发送消息。"),
"es": ("No se pudo verificar este chat. Comprueba el Chat ID y que el bot sea miembro.", "Solo un administrador del grupo o propietario del canal puede crear programaciones aquí.", "El bot debe ser administrador del canal para poder publicar.", "Al bot le falta el permiso «Publicar mensajes» en este canal.", "El bot no es miembro de este grupo. Añádelo e inténtalo de nuevo.", "El bot tiene restricciones y no puede enviar mensajes en este grupo."),
"hi": ("इस चैट की पुष्टि नहीं हो सकी। Chat ID जाँचें और सुनिश्चित करें कि बॉट सदस्य है।", "यहाँ केवल ग्रुप एडमिन या चैनल मालिक शेड्यूल बना सकता है।", "पोस्ट करने के लिए बॉट को चैनल एडमिन बनाना आवश्यक है।", "इस चैनल में बॉट के पास संदेश पोस्ट करने की अनुमति नहीं है।", "बॉट इस ग्रुप का सदस्य नहीं है। उसे जोड़कर फिर कोशिश करें।", "इस ग्रुप में बॉट प्रतिबंधित है और संदेश नहीं भेज सकता।"),
"ar": ("تعذّر التحقق من هذه المحادثة. تحقق من Chat ID وتأكد من أن البوت عضو فيها.", "يمكن لمسؤول المجموعة أو مالك القناة فقط إنشاء جداول هنا.", "يجب أن يكون البوت مشرفاً في القناة حتى يتمكن من النشر.", "يفتقد البوت إذن نشر الرسائل في هذه القناة.", "البوت ليس عضواً في هذه المجموعة. أضفه ثم حاول مجدداً.", "البوت مقيّد ولا يمكنه إرسال الرسائل في هذه المجموعة."),
"pt": ("Não foi possível verificar este chat. Confira o Chat ID e se o bot é membro.", "Somente um administrador do grupo ou proprietário do canal pode criar agendamentos aqui.", "O bot precisa ser administrador do canal para publicar.", "O bot não tem a permissão de publicar mensagens neste canal.", "O bot não é membro deste grupo. Adicione-o e tente novamente.", "O bot está restrito e não pode enviar mensagens neste grupo."),
"fr": ("Impossible de vérifier cette discussion. Vérifiez le Chat ID et l’adhésion du bot.", "Seul un administrateur du groupe ou le propriétaire du canal peut créer des programmations ici.", "Le bot doit être administrateur du canal pour publier.", "Le bot ne dispose pas du droit « Publier des messages » dans ce canal.", "Le bot n’est pas membre de ce groupe. Ajoutez-le puis réessayez.", "Le bot est restreint et ne peut pas envoyer de messages dans ce groupe."),
"ru": ("Не удалось проверить чат. Проверьте Chat ID и убедитесь, что бот состоит в чате.", "Создавать расписания здесь может только администратор группы или владелец канала.", "Для публикации бот должен быть администратором канала.", "У бота нет разрешения на публикацию сообщений в этом канале.", "Бот не состоит в этой группе. Добавьте его и попробуйте снова.", "Бот ограничен и не может отправлять сообщения в этой группе."),
"bn": ("এই চ্যাট যাচাই করা যায়নি। Chat ID পরীক্ষা করুন এবং বট সদস্য কি না নিশ্চিত করুন।", "এখানে শুধু গ্রুপ অ্যাডমিন বা চ্যানেলের মালিক সময়সূচি তৈরি করতে পারবেন।", "পোস্ট করতে বটকে চ্যানেলের অ্যাডমিন করতে হবে।", "এই চ্যানেলে বটের পোস্ট করার অনুমতি নেই।", "বট এই গ্রুপের সদস্য নয়। বটকে যোগ করে আবার চেষ্টা করুন।", "এই গ্রুপে বট সীমাবদ্ধ, তাই বার্তা পাঠাতে পারবে না।"),
"id": ("Chat ini tidak dapat diverifikasi. Periksa Chat ID dan pastikan bot menjadi anggota.", "Hanya admin grup atau pemilik kanal yang dapat membuat jadwal di sini.", "Bot harus menjadi admin kanal agar dapat memposting.", "Bot tidak memiliki izin untuk memposting pesan di kanal ini.", "Bot bukan anggota grup ini. Tambahkan bot lalu coba lagi.", "Bot dibatasi dan tidak dapat mengirim pesan di grup ini."),
"ja": ("このチャットを確認できません。Chat ID とボットが参加済みかを確認してください。", "ここで予定を作成できるのはグループ管理者またはチャンネル所有者のみです。", "投稿するにはボットをチャンネル管理者に設定してください。", "このチャンネルでボットに「メッセージを投稿」権限がありません。", "ボットはこのグループに参加していません。追加して再試行してください。", "このグループではボットが制限されており、メッセージを送信できません。"),
"ko": ("이 채팅을 확인할 수 없습니다. Chat ID와 봇 가입 여부를 확인하세요.", "이곳에서 일정을 만들 수 있는 사람은 그룹 관리자 또는 채널 소유자뿐입니다.", "게시하려면 봇을 채널 관리자로 지정해야 합니다.", "이 채널에서 봇에 게시 권한이 없습니다.", "봇이 이 그룹의 구성원이 아닙니다. 봇을 추가한 뒤 다시 시도하세요.", "이 그룹에서 봇이 제한되어 메시지를 보낼 수 없습니다."),
"th": ("ไม่สามารถตรวจสอบแชตนี้ได้ โปรดตรวจสอบ Chat ID และตรวจสอบว่าบอทเป็นสมาชิกแล้ว", "เฉพาะผู้ดูแลกลุ่มหรือเจ้าของช่องเท่านั้นที่สร้างกำหนดการได้", "ต้องตั้งบอทเป็นผู้ดูแลช่องก่อนจึงจะโพสต์ได้", "บอทไม่มีสิทธิ์โพสต์ข้อความในช่องนี้", "บอทยังไม่ได้เป็นสมาชิกของกลุ่มนี้ โปรดเพิ่มบอทแล้วลองอีกครั้ง", "บอทถูกจำกัดสิทธิ์และไม่สามารถส่งข้อความในกลุ่มนี้ได้"),
"vi": ("Không thể xác minh cuộc trò chuyện này. Hãy kiểm tra Chat ID và đảm bảo bot là thành viên.", "Chỉ quản trị viên nhóm hoặc chủ kênh mới có thể tạo lịch tại đây.", "Bot phải là quản trị viên kênh mới có thể đăng bài.", "Bot thiếu quyền đăng tin nhắn trong kênh này.", "Bot chưa là thành viên của nhóm này. Hãy thêm bot rồi thử lại.", "Bot bị hạn chế và không thể gửi tin nhắn trong nhóm này."),
"tr": ("Bu sohbet doğrulanamadı. Chat ID'yi ve botun üye olduğunu kontrol edin.", "Burada yalnızca grup yöneticisi veya kanal sahibi zamanlama oluşturabilir.", "Gönderi yayınlamak için bot kanal yöneticisi olmalıdır.", "Botta bu kanalda mesaj gönderme izni yok.", "Bot bu grubun üyesi değil. Botu ekleyip tekrar deneyin.", "Bot bu grupta kısıtlı ve mesaj gönderemiyor."),
"de": ("Dieser Chat konnte nicht überprüft werden. Prüfe die Chat-ID und ob der Bot Mitglied ist.", "Nur ein Gruppenadministrator oder Kanalinhaber kann hier Zeitpläne erstellen.", "Der Bot muss Kanaladministrator sein, um Beiträge zu veröffentlichen.", "Dem Bot fehlt in diesem Kanal die Berechtigung zum Senden von Beiträgen.", "Der Bot ist kein Mitglied dieser Gruppe. Füge ihn hinzu und versuche es erneut.", "Der Bot ist in dieser Gruppe eingeschränkt und kann keine Nachrichten senden."),
"it": ("Impossibile verificare questa chat. Controlla il Chat ID e che il bot sia membro.", "Solo un amministratore del gruppo o il proprietario del canale può creare programmazioni qui.", "Il bot deve essere amministratore del canale per poter pubblicare.", "Al bot manca il permesso di pubblicare messaggi in questo canale.", "Il bot non è membro di questo gruppo. Aggiungilo e riprova.", "Il bot è limitato e non può inviare messaggi in questo gruppo."),
"ur": ("اس چیٹ کی تصدیق نہیں ہو سکی۔ Chat ID چیک کریں اور یقینی بنائیں کہ بوٹ اس کا رکن ہے۔", "یہاں صرف گروپ ایڈمن یا چینل کا مالک شیڈول بنا سکتا ہے۔", "پوسٹ کرنے کے لیے بوٹ کو چینل ایڈمن بنانا ضروری ہے۔", "اس چینل میں بوٹ کو پیغامات پوسٹ کرنے کی اجازت نہیں ہے۔", "بوٹ اس گروپ کا رکن نہیں۔ اسے شامل کرکے دوبارہ کوشش کریں۔", "اس گروپ میں بوٹ محدود ہے اور پیغامات نہیں بھیج سکتا۔"),
"sw": ("Haikuwezekana kuthibitisha gumzo hili. Kagua Chat ID na uhakikishe bot ni mwanachama.", "Msimamizi wa kikundi au mmiliki wa chaneli pekee ndiye anayeweza kuunda ratiba hapa.", "Bot lazima iwe msimamizi wa chaneli ili kuchapisha.", "Bot haina ruhusa ya kuchapisha ujumbe kwenye chaneli hii.", "Bot si mwanachama wa kikundi hiki. Iongeze kisha ujaribu tena.", "Bot imewekewa vikwazo na haiwezi kutuma ujumbe kwenye kikundi hiki."),
}
if set(_ROWS) != set(LANGUAGES):
    raise RuntimeError("Permission translations must cover every supported language.")


def _message(language: str | None, key: str) -> str:
    locale = normalize_language(language)
    return dict(zip(_KEYS, _ROWS[locale], strict=True))[key]


async def ensure_chat_access(bot: Bot, chat_id: int, user_id: int, language: str = DEFAULT_LANGUAGE):
    """Verify the chat exists, requester is an admin, and the bot can publish."""
    try:
        chat = await bot.get_chat(chat_id)
        requester = await bot.get_chat_member(chat_id, user_id)
        bot_member = await bot.get_chat_member(chat_id, bot.id)
    except (Forbidden, TelegramError) as exc:
        raise ChatAccessError(_message(language, "unavailable")) from exc
    if requester.status not in ("administrator", "creator"):
        raise ChatAccessError(_message(language, "admin"))
    if chat.type == "channel":
        if bot_member.status not in ("administrator", "creator"):
            raise ChatAccessError(_message(language, "channel_admin"))
        if getattr(bot_member, "can_post_messages", True) is False:
            raise ChatAccessError(_message(language, "post_messages"))
    else:
        if bot_member.status in ("left", "kicked"):
            raise ChatAccessError(_message(language, "not_member"))
        if bot_member.status == "restricted" and not getattr(bot_member, "can_send_messages", False):
            raise ChatAccessError(_message(language, "restricted"))
    return chat
