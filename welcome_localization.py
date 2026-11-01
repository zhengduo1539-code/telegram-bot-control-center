"""Localized private-chat /start welcome messages for every supported locale."""
from __future__ import annotations

from localization import LANGUAGES, normalize_language, translate


# Keep the welcome copy separate from handler logic. Action names are inserted
# from the same catalog used by the inline keyboard, so the instructions always
# match the buttons displayed underneath the message.
WELCOME_MESSAGES: dict[str, str] = {
    "en": """👋 *Welcome to Schedule Bot*
Your all-in-one Telegram publishing assistant.

━━━━━━━━━━━━━━━━━━
✨ *What you can do*
📅 Schedule one-time or recurring posts
🗂 Queue content for daily time slots
🎲 Publish randomly from a Media Pool
⚙️ Set timezones, posting windows and post actions
━━━━━━━━━━━━━━━━━━

🚀 *Quick start*
1. Add the bot to your group or channel.
2. Allow the bot to send messages. In channels, make it an admin with permission to post.
3. Choose *{schedule_new}* and follow the guided setup.
4. Review the summary and confirm to save.

Use *{help}* for the feature guide or *{language}* to change your language.""",

    "my": """👋 *Schedule Bot မှ ကြိုဆိုပါတယ်*
Telegram Group နှင့် Channel များအတွက် Post များကို အချိန်ဇယားဖြင့် စီမံပေးသည့် Bot ဖြစ်ပါတယ်။

━━━━━━━━━━━━━━━━━━
✨ *အသုံးပြုနိုင်သော Features*
📅 တစ်ကြိမ်တည်း သို့မဟုတ် ထပ်ခါတလဲလဲ Post ပို့ရန်
🗂 နေ့စဉ်အချိန်အလိုက် Queue ဖြင့် ပို့ရန်
🎲 Media Pool မှ ကျပန်းရွေးချယ်ပို့ရန်
⚙️ Timezone၊ Posting Window နှင့် Post Actions သတ်မှတ်ရန်
━━━━━━━━━━━━━━━━━━

🚀 *စတင်အသုံးပြုရန်*
1. Bot ကို သင့် Group သို့မဟုတ် Channel ထဲသို့ ထည့်ပါ။
2. Message ပို့ခွင့်ပေးပါ။ Channel ဖြစ်ပါက Post Messages ခွင့်ပါသော Admin အဖြစ် သတ်မှတ်ပါ။
3. *{schedule_new}* ကိုနှိပ်ပြီး အဆင့်လိုက်လုပ်ဆောင်ပါ။
4. နောက်ဆုံး Summary ကို စစ်ဆေးပြီး Confirm နှိပ်ပါ။

အသုံးပြုနည်းအတွက် *{help}*၊ ဘာသာစကားပြောင်းရန် *{language}* ကိုရွေးပါ။""",

    "zh": """👋 *欢迎使用 Schedule Bot*
您的 Telegram 群组和频道自动发布与排程助手。

━━━━━━━━━━━━━━━━━━
✨ *您可以使用这些功能*
📅 安排单次或重复发布
🗂 将内容加入每日时间队列
🎲 从媒体库中随机发布内容
⚙️ 设置时区、发布时段和发布操作
━━━━━━━━━━━━━━━━━━

🚀 *快速开始*
1. 将机器人添加到您的群组或频道。
2. 允许机器人发送消息。若用于频道，请将机器人设为管理员并授予发布权限。
3. 点击 *{schedule_new}*，按照引导完成设置。
4. 检查摘要并点击确认以保存。

点击 *{help}* 查看功能指南，或点击 *{language}* 更改语言。""",

    "es": """👋 *Te damos la bienvenida a Schedule Bot*
Tu asistente para programar publicaciones en grupos y canales de Telegram.

━━━━━━━━━━━━━━━━━━
✨ *Qué puedes hacer*
📅 Programar publicaciones únicas o recurrentes
🗂 Organizar contenido en horarios diarios
🎲 Publicar contenido aleatorio de una biblioteca multimedia
⚙️ Configurar zonas horarias, franjas de publicación y acciones
━━━━━━━━━━━━━━━━━━

🚀 *Inicio rápido*
1. Añade el bot a tu grupo o canal.
2. Permite que el bot envíe mensajes. En los canales, hazlo administrador con permiso para publicar.
3. Pulsa *{schedule_new}* y sigue los pasos guiados.
4. Revisa el resumen y confirma para guardar.

Usa *{help}* para consultar la guía o *{language}* para cambiar el idioma.""",

    "hi": """👋 *Schedule Bot में आपका स्वागत है*
Telegram ग्रुप और चैनलों में पोस्ट शेड्यूल करने वाला आपका सहायक।

━━━━━━━━━━━━━━━━━━
✨ *आप क्या कर सकते हैं*
📅 एक बार या बार-बार पोस्ट शेड्यूल करें
🗂 रोज़ाना समय के अनुसार पोस्ट की कतार बनाएँ
🎲 मीडिया पूल से सामग्री को बेतरतीब प्रकाशित करें
⚙️ टाइमज़ोन, पोस्टिंग समय और पोस्ट क्रियाएँ तय करें
━━━━━━━━━━━━━━━━━━

🚀 *जल्दी शुरू करें*
1. बॉट को अपने ग्रुप या चैनल में जोड़ें।
2. बॉट को संदेश भेजने की अनुमति दें। चैनल में पोस्ट करने के लिए उसे आवश्यक अधिकारों वाला एडमिन बनाएँ।
3. *{schedule_new}* चुनें और दिए गए चरणों का पालन करें।
4. सारांश जाँचें और सहेजने के लिए पुष्टि करें।

सुविधाओं की जानकारी के लिए *{help}* या भाषा बदलने के लिए *{language}* चुनें।""",

    "ar": """👋 *مرحباً بك في Schedule Bot*
مساعدك لتنظيم وجدولة المنشورات في مجموعات وقنوات Telegram.

━━━━━━━━━━━━━━━━━━
✨ *ما الذي يمكنك فعله؟*
📅 جدولة منشورات لمرة واحدة أو بشكل متكرر
🗂 ترتيب المحتوى في أوقات يومية محددة
🎲 نشر محتوى عشوائي من مكتبة الوسائط
⚙️ ضبط المنطقة الزمنية وفترات النشر وإجراءات المنشورات
━━━━━━━━━━━━━━━━━━

🚀 *البدء السريع*
1. أضف البوت إلى مجموعتك أو قناتك.
2. اسمح للبوت بإرسال الرسائل. وفي القنوات، اجعله مشرفاً مع صلاحية النشر.
3. اختر *{schedule_new}* واتبع خطوات الإعداد.
4. راجع الملخص واضغط على التأكيد للحفظ.

استخدم *{help}* لعرض دليل الميزات أو *{language}* لتغيير اللغة.""",

    "pt": """👋 *Boas-vindas ao Schedule Bot*
Seu assistente para programar publicações em grupos e canais do Telegram.

━━━━━━━━━━━━━━━━━━
✨ *O que você pode fazer*
📅 Agendar publicações únicas ou recorrentes
🗂 Organizar conteúdo em horários diários
🎲 Publicar itens aleatórios de uma biblioteca de mídia
⚙️ Configurar fusos horários, períodos de publicação e ações
━━━━━━━━━━━━━━━━━━

🚀 *Comece agora*
1. Adicione o bot ao seu grupo ou canal.
2. Permita que o bot envie mensagens. Em canais, torne-o administrador com permissão para publicar.
3. Selecione *{schedule_new}* e siga as etapas.
4. Revise o resumo e confirme para salvar.

Use *{help}* para consultar o guia ou *{language}* para alterar o idioma.""",

    "fr": """👋 *Bienvenue sur Schedule Bot*
Votre assistant pour programmer des publications dans les groupes et chaînes Telegram.

━━━━━━━━━━━━━━━━━━
✨ *Ce que vous pouvez faire*
📅 Programmer des publications ponctuelles ou récurrentes
🗂 Organiser le contenu selon des créneaux quotidiens
🎲 Publier des éléments au hasard depuis une bibliothèque multimédia
⚙️ Régler le fuseau horaire, les plages de publication et les actions
━━━━━━━━━━━━━━━━━━

🚀 *Démarrage rapide*
1. Ajoutez le bot à votre groupe ou à votre chaîne.
2. Autorisez le bot à envoyer des messages. Dans une chaîne, définissez-le comme administrateur avec le droit de publier.
3. Choisissez *{schedule_new}* et suivez les étapes.
4. Vérifiez le récapitulatif et confirmez pour enregistrer.

Consultez *{help}* pour le guide ou *{language}* pour changer de langue.""",

    "ru": """👋 *Добро пожаловать в Schedule Bot*
Ваш помощник для планирования публикаций в группах и каналах Telegram.

━━━━━━━━━━━━━━━━━━
✨ *Что можно делать*
📅 Планировать разовые и повторяющиеся публикации
🗂 Добавлять контент в ежедневную очередь по времени
🎲 Случайным образом публиковать материалы из медиатеки
⚙️ Настраивать часовой пояс, интервалы публикаций и действия
━━━━━━━━━━━━━━━━━━

🚀 *Быстрый старт*
1. Добавьте бота в свою группу или канал.
2. Разрешите боту отправлять сообщения. В канале назначьте его администратором с правом публикации.
3. Выберите *{schedule_new}* и следуйте инструкциям.
4. Проверьте сводку и подтвердите сохранение.

Откройте *{help}*, чтобы узнать о функциях, или *{language}*, чтобы сменить язык.""",

    "bn": """👋 *Schedule Bot-এ স্বাগতম*
Telegram গ্রুপ ও চ্যানেলে পোস্টের সময় নির্ধারণের জন্য আপনার সহায়ক বট।

━━━━━━━━━━━━━━━━━━
✨ *যা করতে পারবেন*
📅 একবার বা নিয়মিত পোস্টের সময় নির্ধারণ
🗂 প্রতিদিনের নির্দিষ্ট সময় অনুযায়ী কনটেন্ট কিউতে রাখা
🎲 মিডিয়া পুল থেকে এলোমেলোভাবে কনটেন্ট প্রকাশ
⚙️ টাইমজোন, পোস্ট করার সময়সীমা ও পোস্টের কার্যক্রম নির্ধারণ
━━━━━━━━━━━━━━━━━━

🚀 *দ্রুত শুরু করুন*
1. আপনার গ্রুপ বা চ্যানেলে বটটি যোগ করুন।
2. বটকে মেসেজ পাঠানোর অনুমতি দিন। চ্যানেলে পোস্ট করার জন্য প্রয়োজনীয় অধিকারসহ অ্যাডমিন করুন।
3. *{schedule_new}* চাপুন এবং ধাপগুলো অনুসরণ করুন।
4. সারাংশ দেখে সংরক্ষণের জন্য নিশ্চিত করুন।

ফিচার জানতে *{help}* অথবা ভাষা পরিবর্তন করতে *{language}* বেছে নিন।""",

    "id": """👋 *Selamat datang di Schedule Bot*
Asisten untuk menjadwalkan posting di grup dan channel Telegram Anda.

━━━━━━━━━━━━━━━━━━
✨ *Yang bisa Anda lakukan*
📅 Menjadwalkan posting sekali atau berulang
🗂 Mengatur konten dalam antrean berdasarkan waktu harian
🎲 Menerbitkan konten secara acak dari Media Pool
⚙️ Mengatur zona waktu, rentang publikasi, dan tindakan posting
━━━━━━━━━━━━━━━━━━

🚀 *Mulai dengan cepat*
1. Tambahkan bot ke grup atau channel Anda.
2. Izinkan bot mengirim pesan. Untuk channel, jadikan bot admin dengan izin untuk memposting.
3. Pilih *{schedule_new}* dan ikuti langkah-langkah panduan.
4. Periksa ringkasan lalu konfirmasi untuk menyimpan.

Gunakan *{help}* untuk panduan fitur atau *{language}* untuk mengubah bahasa.""",

    "ja": """👋 *Schedule Botへようこそ*
Telegramのグループやチャンネルへの投稿を予約・管理するアシスタントです。

━━━━━━━━━━━━━━━━━━
✨ *できること*
📅 単発または繰り返しの投稿を予約
🗂 毎日の時間枠に合わせて投稿をキューに登録
🎲 メディアプールからコンテンツをランダムに投稿
⚙️ タイムゾーン、投稿時間帯、投稿アクションを設定
━━━━━━━━━━━━━━━━━━

🚀 *クイックスタート*
1. ボットをグループまたはチャンネルに追加します。
2. ボットにメッセージ送信を許可します。チャンネルでは投稿権限を持つ管理者に設定してください。
3. *{schedule_new}* を選び、案内に従って設定します。
4. 内容を確認し、保存するために確定します。

機能ガイドは *{help}*、言語の変更は *{language}* を選択してください。""",

    "ko": """👋 *Schedule Bot에 오신 것을 환영합니다*
Telegram 그룹과 채널의 게시물을 예약하고 관리하는 도우미입니다.

━━━━━━━━━━━━━━━━━━
✨ *사용할 수 있는 기능*
📅 한 번 또는 반복 게시물 예약
🗂 매일 지정한 시간에 맞춰 콘텐츠 대기열 관리
🎲 미디어 풀에서 콘텐츠를 무작위로 게시
⚙️ 시간대, 게시 시간 범위 및 게시 작업 설정
━━━━━━━━━━━━━━━━━━

🚀 *빠른 시작*
1. 봇을 그룹 또는 채널에 추가하세요.
2. 봇에 메시지 전송 권한을 부여하세요. 채널에서는 게시 권한이 있는 관리자로 지정하세요.
3. *{schedule_new}*을 선택하고 안내에 따라 설정하세요.
4. 요약을 확인한 후 저장을 확정하세요.

기능 안내는 *{help}*, 언어 변경은 *{language}*를 선택하세요.""",

    "th": """👋 *ยินดีต้อนรับสู่ Schedule Bot*
ผู้ช่วยจัดตารางโพสต์สำหรับกลุ่มและช่อง Telegram ของคุณ

━━━━━━━━━━━━━━━━━━
✨ *คุณสามารถทำอะไรได้บ้าง*
📅 ตั้งเวลาโพสต์ครั้งเดียวหรือโพสต์ซ้ำ
🗂 จัดคิวเนื้อหาตามช่วงเวลาประจำวัน
🎲 สุ่มเผยแพร่เนื้อหาจาก Media Pool
⚙️ ตั้งค่าเขตเวลา ช่วงเวลาโพสต์ และการดำเนินการกับโพสต์
━━━━━━━━━━━━━━━━━━

🚀 *เริ่มต้นใช้งาน*
1. เพิ่มบอทลงในกลุ่มหรือช่อง Telegram ของคุณ
2. อนุญาตให้บอทส่งข้อความได้ หากใช้ในช่อง ให้ตั้งบอทเป็นผู้ดูแลและอนุญาตให้โพสต์
3. เลือก *{schedule_new}* แล้วทำตามขั้นตอนที่แนะนำ
4. ตรวจสอบสรุปและกดยืนยันเพื่อบันทึก

เลือก *{help}* เพื่ออ่านคู่มือ หรือ *{language}* เพื่อเปลี่ยนภาษา""",

    "vi": """👋 *Chào mừng bạn đến với Schedule Bot*
Trợ lý lên lịch và quản lý bài đăng cho nhóm và kênh Telegram của bạn.

━━━━━━━━━━━━━━━━━━
✨ *Bạn có thể làm gì*
📅 Lên lịch bài đăng một lần hoặc định kỳ
🗂 Xếp nội dung vào hàng đợi theo khung giờ hằng ngày
🎲 Đăng ngẫu nhiên nội dung từ Kho phương tiện
⚙️ Thiết lập múi giờ, khung giờ đăng và thao tác với bài đăng
━━━━━━━━━━━━━━━━━━

🚀 *Bắt đầu nhanh*
1. Thêm bot vào nhóm hoặc kênh của bạn.
2. Cho phép bot gửi tin nhắn. Trong kênh, hãy cấp cho bot quyền quản trị và quyền đăng bài.
3. Chọn *{schedule_new}* và làm theo hướng dẫn.
4. Kiểm tra phần tóm tắt rồi xác nhận để lưu.

Chọn *{help}* để xem hướng dẫn hoặc *{language}* để đổi ngôn ngữ.""",

    "tr": """👋 *Schedule Bot'a hoş geldiniz*
Telegram gruplarınız ve kanallarınız için gönderi planlama asistanınız.

━━━━━━━━━━━━━━━━━━
✨ *Neler yapabilirsiniz?*
📅 Tek seferlik veya tekrarlanan gönderiler planlayın
🗂 İçerikleri günlük saatlere göre kuyruğa alın
🎲 Medya Havuzu'ndan rastgele içerik yayınlayın
⚙️ Saat dilimini, paylaşım aralıklarını ve gönderi işlemlerini ayarlayın
━━━━━━━━━━━━━━━━━━

🚀 *Hızlı başlangıç*
1. Botu grubunuza veya kanalınıza ekleyin.
2. Bota mesaj gönderme izni verin. Kanallarda paylaşım izni olan bir yönetici yapın.
3. *{schedule_new}* seçeneğine basın ve yönlendirmeleri izleyin.
4. Özeti inceleyin ve kaydetmek için onaylayın.

Özellik rehberi için *{help}*, dili değiştirmek için *{language}* seçeneğini kullanın.""",

    "de": """👋 *Willkommen bei Schedule Bot*
Ihr Assistent zum Planen und Verwalten von Beiträgen in Telegram-Gruppen und -Kanälen.

━━━━━━━━━━━━━━━━━━
✨ *Das können Sie tun*
📅 Einmalige oder wiederkehrende Beiträge planen
🗂 Inhalte in tägliche Zeitfenster einreihen
🎲 Inhalte zufällig aus einem Medienpool veröffentlichen
⚙️ Zeitzone, Veröffentlichungszeiten und Beitragsaktionen festlegen
━━━━━━━━━━━━━━━━━━

🚀 *Schnellstart*
1. Fügen Sie den Bot zu Ihrer Gruppe oder Ihrem Kanal hinzu.
2. Erlauben Sie dem Bot, Nachrichten zu senden. Machen Sie ihn in Kanälen mit Veröffentlichungsrecht zum Administrator.
3. Wählen Sie *{schedule_new}* und folgen Sie der Anleitung.
4. Prüfen Sie die Zusammenfassung und bestätigen Sie das Speichern.

Mit *{help}* öffnen Sie die Funktionsübersicht; mit *{language}* ändern Sie die Sprache.""",

    "it": """👋 *Benvenuto su Schedule Bot*
Il tuo assistente per programmare e gestire i post nei gruppi e canali Telegram.

━━━━━━━━━━━━━━━━━━
✨ *Cosa puoi fare*
📅 Programmare post singoli o ricorrenti
🗂 Inserire contenuti in una coda con orari giornalieri
🎲 Pubblicare contenuti casuali da una raccolta multimediale
⚙️ Impostare fuso orario, fasce di pubblicazione e azioni sui post
━━━━━━━━━━━━━━━━━━

🚀 *Per iniziare*
1. Aggiungi il bot al tuo gruppo o canale.
2. Consenti al bot di inviare messaggi. Nei canali, rendilo amministratore con il permesso di pubblicare.
3. Seleziona *{schedule_new}* e segui la procedura guidata.
4. Controlla il riepilogo e conferma per salvare.

Usa *{help}* per la guida alle funzioni o *{language}* per cambiare lingua.""",

    "ur": """👋 *Schedule Bot میں خوش آمدید*
آپ کے Telegram گروپس اور چینلز کے لیے پوسٹس شیڈول کرنے کا معاون۔

━━━━━━━━━━━━━━━━━━
✨ *آپ کیا کر سکتے ہیں*
📅 ایک بار یا بار بار پوسٹس شیڈول کریں
🗂 روزانہ کے مقررہ اوقات کے مطابق مواد قطار میں رکھیں
🎲 میڈیا پول سے بے ترتیب مواد شائع کریں
⚙️ ٹائم زون، پوسٹنگ کے اوقات اور پوسٹ کے اقدامات ترتیب دیں
━━━━━━━━━━━━━━━━━━

🚀 *فوری آغاز*
1. بوٹ کو اپنے گروپ یا چینل میں شامل کریں۔
2. بوٹ کو پیغامات بھیجنے کی اجازت دیں۔ چینل میں پوسٹ کرنے کے لیے اسے متعلقہ اجازتوں کے ساتھ ایڈمن بنائیں۔
3. *{schedule_new}* منتخب کریں اور دی گئی ہدایات پر عمل کریں۔
4. خلاصہ دیکھیں اور محفوظ کرنے کے لیے تصدیق کریں۔

خصوصیات کی رہنمائی کے لیے *{help}* یا زبان تبدیل کرنے کے لیے *{language}* منتخب کریں۔""",

    "sw": """👋 *Karibu kwenye Schedule Bot*
Msaidizi wako wa kupanga na kusimamia machapisho katika vikundi na chaneli za Telegram.

━━━━━━━━━━━━━━━━━━
✨ *Unachoweza kufanya*
📅 Panga machapisho ya mara moja au yanayojirudia
🗂 Weka maudhui kwenye foleni kulingana na muda wa kila siku
🎲 Chapisha maudhui bila mpangilio kutoka kwenye Maktaba ya Midia
⚙️ Weka saa za eneo, vipindi vya uchapishaji na vitendo vya machapisho
━━━━━━━━━━━━━━━━━━

🚀 *Anza haraka*
1. Ongeza bot kwenye kikundi au chaneli yako.
2. Ruhusu bot kutuma ujumbe. Kwenye chaneli, iweke kuwa msimamizi mwenye ruhusa ya kuchapisha.
3. Chagua *{schedule_new}* na ufuate hatua zinazoelekezwa.
4. Kagua muhtasari na uthibitishe ili kuhifadhi.

Tumia *{help}* kupata mwongozo wa vipengele au *{language}* kubadilisha lugha.""",
}


def welcome_text(language: str | None) -> str:
    """Return welcome copy in the selected locale with matching menu labels."""
    locale = normalize_language(language)
    template = WELCOME_MESSAGES[locale]
    return template.format(
        schedule_new=translate(locale, "schedule_new"),
        help=translate(locale, "help_title"),
        language=translate(locale, "language"),
    )
