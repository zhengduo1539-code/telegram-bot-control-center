"""Centralized, persistent localization for the Telegram bot UI.

The language picker uses Telegram's native ReplyKeyboardMarkup. Telegram sends
the tapped button label as a message; the handler consumes that label, stores
the language preference, and immediately removes the temporary keyboard.
"""
from __future__ import annotations

import re
from typing import Any

from telegram import KeyboardButton, ReplyKeyboardMarkup

LANGUAGES: dict[str, dict[str, str]] = {
    "en": {"name": "English", "flag": "🇬🇧"},
    "my": {"name": "မြန်မာ", "flag": "🇲🇲"},
    "zh": {"name": "简体中文", "flag": "🇨🇳"},
    "es": {"name": "Español", "flag": "🇪🇸"},
    "hi": {"name": "हिन्दी", "flag": "🇮🇳"},
    "ar": {"name": "العربية", "flag": "🇸🇦"},
    "pt": {"name": "Português", "flag": "🇧🇷"},
    "fr": {"name": "Français", "flag": "🇫🇷"},
    "ru": {"name": "Русский", "flag": "🇷🇺"},
    "bn": {"name": "বাংলা", "flag": "🇧🇩"},
    "id": {"name": "Bahasa Indonesia", "flag": "🇮🇩"},
    "ja": {"name": "日本語", "flag": "🇯🇵"},
    "ko": {"name": "한국어", "flag": "🇰🇷"},
    "th": {"name": "ไทย", "flag": "🇹🇭"},
    "vi": {"name": "Tiếng Việt", "flag": "🇻🇳"},
    "tr": {"name": "Türkçe", "flag": "🇹🇷"},
    "de": {"name": "Deutsch", "flag": "🇩🇪"},
    "it": {"name": "Italiano", "flag": "🇮🇹"},
    "ur": {"name": "اردو", "flag": "🇵🇰"},
    "sw": {"name": "Kiswahili", "flag": "🇰🇪"},
}

LANGUAGE_LABELS = {
    code: f"{details['flag']} {details['name']}"
    for code, details in LANGUAGES.items()
}
LANGUAGE_BY_LABEL = {label: code for code, label in LANGUAGE_LABELS.items()}

_KEYS = (
    "menu_title", "schedule_new", "my_posts", "manage_queue", "media_pool",
    "templates", "my_stats", "language", "cancel", "add_group", "share_bot",
    "owner_panel", "lang_title", "lang_prompt", "lang_saved",
    "group_start_title", "group_start_body", "open_private",
    "group_admin_note", "private_setup_title", "private_setup_body",
    "private_setup_action", "schedule_content_title", "schedule_content_prompt",
    "choose_group", "manual_chat_id", "back", "confirm",
)

# Each locale is kept in the same key order above. Keeping catalog data separate
# from handlers avoids embedding UI translations in business-logic code.
_CATALOG_ROWS: dict[str, tuple[str, ...]] = {
    "en": (
        "🏠 Main Menu — choose an action", "📅 Schedule New Post", "📋 My Posts",
        "🗂 Manage Queue", "🎲 Media Pool", "📝 Templates", "📊 My Stats",
        "🌐 Language", "❌ Cancel", "➕ Add to my Group", "📢 Share Bot",
        "🛠 Owner Panel", "🌐 Choose your language",
        "Select one of the 20 languages below. Your choice will be saved to your account.",
        "✅ Language saved successfully.", "👋 Schedule this group with the bot",
        "Continue in the bot's private chat to schedule posts for *{group}*.",
        "📩 Open Bot in Private Chat",
        "ℹ️ Only an administrator of this group can create schedules for it.",
        "👋 Private Setup", "📍 Selected Group: *{group}*",
        "Choose *📅 Schedule New Post* to add content. This group will be selected automatically.",
        "📝 Step 2/8 — Post Content",
        "Send the text or media you want the bot to schedule (photo, video, document, audio or animation).",
        "🗂 Choose a group or channel", "✏️ Enter Chat ID manually",
        "⬅️ Back", "✅ Confirm",
    ),
    "my": (
        "🏠 ပင်မမီနူး — လုပ်ဆောင်ချက်ရွေးချယ်ပါ", "📅 Post အသစ် Schedule", "📋 My Posts",
        "🗂 Queue စီမံရန်", "🎲 Media Pool", "📝 Templates", "📊 စာရင်းအင်း",
        "🌐 ဘာသာစကား", "❌ ပယ်ဖျက်ရန်", "➕ Group ထဲသို့ ထည့်ရန်", "📢 Bot မျှဝေရန်",
        "🛠 Owner Panel", "🌐 အသုံးပြုမည့် ဘာသာစကားကို ရွေးချယ်ပါ",
        "အောက်ပါ ဘာသာစကား ၂၀ မျိုးထဲမှ ကြိုက်နှစ်သက်ရာကို ရွေးပါ။ ရွေးချယ်မှုကို သင့်အကောင့်တွင် သိမ်းဆည်းပါမည်။",
        "✅ ဘာသာစကားကို မြန်မာအဖြစ် သိမ်းဆည်းပြီးပါပြီ။", "👋 ဤ Group အတွက် Schedule ပြုလုပ်ပါ",
        "ဤ Group (*{group}*) အတွက် Post များကို အချိန်သတ်မှတ်ရန် Bot Private Chat တွင် ဆက်လုပ်ပါ။",
        "📩 Bot Private Chat ဖွင့်ရန်",
        "ℹ️ ဤ Group ကို စီမံခွင့်ရှိသော Admin များသာ Schedule ပြုလုပ်နိုင်ပါသည်။",
        "👋 Private Setup", "📍 ရွေးချယ်ထားသော Group: *{group}*",
        "*📅 Schedule New Post* ကိုနှိပ်ပြီး Post အကြောင်းအရာ ထည့်ပါ။ ဤ Group ကို အလိုအလျောက် ရွေးချယ်ပေးပါမည်။",
        "📝 အဆင့် ၂/၈ — Post အကြောင်းအရာ",
        "အချိန်သတ်မှတ်လိုသော စာသား သို့မဟုတ် မီဒီယာ (ဓာတ်ပုံ၊ ဗီဒီယို၊ ဖိုင်၊ အသံ) ကို ပေးပို့ပါ။",
        "🗂 Group သို့မဟုတ် Channel ရွေးချယ်ပါ", "✏️ Chat ID ကို ကိုယ်တိုင်ထည့်ရန်",
        "⬅️ နောက်သို့", "✅ အတည်ပြုရန်",
    ),
    "zh": (
        "🏠 主菜单 — 请选择一个操作", "📅 创建定时帖子", "📋 我的帖子",
        "🗂 队列管理", "🎲 媒体池", "📝 模板", "📊 我的统计",
        "🌐 语言", "❌ 取消", "➕ 添加到我的群组", "📢 分享机器人",
        "🛠 管理员面板", "🌐 选择您的语言",
        "请从下方 20 种语言中选择一种。您的选择将保存到账户。",
        "✅ 语言设置已保存。", "👋 使用机器人安排此群组的帖子",
        "请在机器人的私聊中继续，为 *{group}* 安排帖子。",
        "📩 打开机器人私聊",
        "ℹ️ 只有此群组的管理员才能为该群组创建计划。",
        "👋 私聊设置", "📍 当前群组：*{group}*",
        "选择 *📅 创建定时帖子* 添加内容。系统会自动选择此群组。",
        "📝 第 2/8 步 — 帖子内容",
        "发送要安排的文字或媒体（照片、视频、文档、音频或动画）。",
        "🗂 选择群组或频道", "✏️ 手动输入 Chat ID",
        "⬅️ 返回", "✅ 确认",
    ),
    "es": (
        "🏠 Menú principal — elige una opción", "📅 Programar publicación", "📋 Mis publicaciones",
        "🗂 Gestionar cola", "🎲 Biblioteca multimedia", "📝 Plantillas", "📊 Mis estadísticas",
        "🌐 Idioma", "❌ Cancelar", "➕ Añadir a mi grupo", "📢 Compartir bot",
        "🛠 Panel del propietario", "🌐 Elige tu idioma",
        "Selecciona uno de los 20 idiomas. Tu elección se guardará en tu cuenta.",
        "✅ Idioma guardado correctamente.", "👋 Programa publicaciones para este grupo",
        "Continúa en el chat privado del bot para programar publicaciones para *{group}*.",
        "📩 Abrir chat privado del bot",
        "ℹ️ Solo un administrador de este grupo puede crear programaciones.",
        "👋 Configuración privada", "📍 Grupo seleccionado: *{group}*",
        "Pulsa *📅 Programar publicación* para añadir contenido. El grupo se seleccionará automáticamente.",
        "📝 Paso 2/8 — Contenido",
        "Envía el texto o contenido multimedia que quieras programar (foto, vídeo, documento, audio o animación).",
        "🗂 Elige un grupo o canal", "✏️ Introducir Chat ID manualmente",
        "⬅️ Atrás", "✅ Confirmar",
    ),
    "hi": (
        "🏠 मुख्य मेनू — कोई विकल्प चुनें", "📅 पोस्ट शेड्यूल करें", "📋 मेरी पोस्ट",
        "🗂 कतार प्रबंधित करें", "🎲 मीडिया पूल", "📝 टेम्पलेट", "📊 मेरे आँकड़े",
        "🌐 भाषा", "❌ रद्द करें", "➕ मेरे ग्रुप में जोड़ें", "📢 बॉट साझा करें",
        "🛠 मालिक पैनल", "🌐 अपनी भाषा चुनें",
        "नीचे दी गई 20 भाषाओं में से एक चुनें। आपकी पसंद आपके खाते में सहेजी जाएगी।",
        "✅ भाषा सफलतापूर्वक सहेजी गई।", "👋 इस ग्रुप के लिए पोस्ट शेड्यूल करें",
        "*{group}* के लिए पोस्ट शेड्यूल करने हेतु बॉट की निजी चैट में जारी रखें।",
        "📩 बॉट की निजी चैट खोलें",
        "ℹ️ केवल इस ग्रुप का एडमिन ही इसके लिए शेड्यूल बना सकता है।",
        "👋 निजी सेटअप", "📍 चुना गया ग्रुप: *{group}*",
        "कंटेंट जोड़ने के लिए *📅 पोस्ट शेड्यूल करें* चुनें। यह ग्रुप अपने आप चुना जाएगा।",
        "📝 चरण 2/8 — पोस्ट सामग्री",
        "शेड्यूल करने के लिए टेक्स्ट या मीडिया (फ़ोटो, वीडियो, दस्तावेज़, ऑडियो या एनिमेशन) भेजें।",
        "🗂 ग्रुप या चैनल चुनें", "✏️ Chat ID स्वयं दर्ज करें",
        "⬅️ वापस", "✅ पुष्टि करें",
    ),
    "ar": (
        "🏠 القائمة الرئيسية — اختر إجراءً", "📅 جدولة منشور جديد", "📋 منشوراتي",
        "🗂 إدارة قائمة الانتظار", "🎲 مكتبة الوسائط", "📝 القوالب", "📊 إحصاءاتي",
        "🌐 اللغة", "❌ إلغاء", "➕ أضفني إلى مجموعتي", "📢 مشاركة البوت",
        "🛠 لوحة المالك", "🌐 اختر لغتك",
        "اختر إحدى اللغات العشرين أدناه. سيتم حفظ اختيارك في حسابك.",
        "✅ تم حفظ اللغة بنجاح.", "👋 جدولة المنشورات لهذه المجموعة",
        "تابع في المحادثة الخاصة مع البوت لجدولة المنشورات في *{group}*.",
        "📩 فتح المحادثة الخاصة مع البوت",
        "ℹ️ يمكن لمسؤول المجموعة فقط إنشاء جداول لهذه المجموعة.",
        "👋 الإعداد الخاص", "📍 المجموعة المحددة: *{group}*",
        "اختر *📅 جدولة منشور جديد* لإضافة المحتوى. سيتم اختيار هذه المجموعة تلقائياً.",
        "📝 الخطوة 2/8 — محتوى المنشور",
        "أرسل النص أو الوسائط التي تريد جدولتها (صورة أو فيديو أو مستند أو صوت أو رسوم متحركة).",
        "🗂 اختر مجموعة أو قناة", "✏️ أدخل معرّف المحادثة يدوياً",
        "⬅️ رجوع", "✅ تأكيد",
    ),
    "pt": (
        "🏠 Menu principal — escolha uma ação", "📅 Agendar nova publicação", "📋 Minhas publicações",
        "🗂 Gerenciar fila", "🎲 Biblioteca de mídia", "📝 Modelos", "📊 Minhas estatísticas",
        "🌐 Idioma", "❌ Cancelar", "➕ Adicionar ao meu grupo", "📢 Compartilhar bot",
        "🛠 Painel do proprietário", "🌐 Escolha seu idioma",
        "Selecione um dos 20 idiomas abaixo. Sua escolha será salva na sua conta.",
        "✅ Idioma salvo com sucesso.", "👋 Agende publicações para este grupo",
        "Continue no chat privado do bot para agendar publicações para *{group}*.",
        "📩 Abrir chat privado do bot",
        "ℹ️ Somente um administrador deste grupo pode criar agendamentos.",
        "👋 Configuração privada", "📍 Grupo selecionado: *{group}*",
        "Escolha *📅 Agendar nova publicação* para adicionar conteúdo. Este grupo será selecionado automaticamente.",
        "📝 Etapa 2/8 — Conteúdo",
        "Envie o texto ou a mídia que deseja agendar (foto, vídeo, documento, áudio ou animação).",
        "🗂 Escolha um grupo ou canal", "✏️ Digitar Chat ID manualmente",
        "⬅️ Voltar", "✅ Confirmar",
    ),
    "fr": (
        "🏠 Menu principal — choisissez une action", "📅 Programmer une publication", "📋 Mes publications",
        "🗂 Gérer la file", "🎲 Bibliothèque multimédia", "📝 Modèles", "📊 Mes statistiques",
        "🌐 Langue", "❌ Annuler", "➕ Ajouter à mon groupe", "📢 Partager le bot",
        "🛠 Panneau du propriétaire", "🌐 Choisissez votre langue",
        "Sélectionnez l'une des 20 langues ci-dessous. Votre choix sera enregistré sur votre compte.",
        "✅ Langue enregistrée.", "👋 Programmer des publications pour ce groupe",
        "Continuez dans la discussion privée du bot pour programmer des publications pour *{group}*.",
        "📩 Ouvrir la discussion privée du bot",
        "ℹ️ Seul un administrateur de ce groupe peut créer des programmations.",
        "👋 Configuration privée", "📍 Groupe sélectionné : *{group}*",
        "Choisissez *📅 Programmer une publication* pour ajouter du contenu. Ce groupe sera sélectionné automatiquement.",
        "📝 Étape 2/8 — Contenu",
        "Envoyez le texte ou le média à programmer (photo, vidéo, document, audio ou animation).",
        "🗂 Choisir un groupe ou un canal", "✏️ Saisir l'identifiant du chat",
        "⬅️ Retour", "✅ Confirmer",
    ),
    "ru": (
        "🏠 Главное меню — выберите действие", "📅 Запланировать публикацию", "📋 Мои публикации",
        "🗂 Управление очередью", "🎲 Медиатека", "📝 Шаблоны", "📊 Моя статистика",
        "🌐 Язык", "❌ Отмена", "➕ Добавить в мою группу", "📢 Поделиться ботом",
        "🛠 Панель владельца", "🌐 Выберите язык",
        "Выберите один из 20 языков ниже. Выбор сохранится в вашем аккаунте.",
        "✅ Язык успешно сохранён.", "👋 Планирование публикаций для этой группы",
        "Продолжите в личном чате бота, чтобы запланировать публикации для *{group}*.",
        "📩 Открыть личный чат с ботом",
        "ℹ️ Создавать расписания для этой группы может только её администратор.",
        "👋 Личная настройка", "📍 Выбранная группа: *{group}*",
        "Нажмите *📅 Запланировать публикацию*, чтобы добавить содержимое. Эта группа будет выбрана автоматически.",
        "📝 Шаг 2/8 — Содержимое публикации",
        "Отправьте текст или медиа для публикации (фото, видео, документ, аудио или анимацию).",
        "🗂 Выберите группу или канал", "✏️ Ввести Chat ID вручную",
        "⬅️ Назад", "✅ Подтвердить",
    ),
    "bn": (
        "🏠 প্রধান মেনু — একটি কাজ বেছে নিন", "📅 নতুন পোস্টের সময় নির্ধারণ", "📋 আমার পোস্ট",
        "🗂 কিউ পরিচালনা", "🎲 মিডিয়া পুল", "📝 টেমপ্লেট", "📊 আমার পরিসংখ্যান",
        "🌐 ভাষা", "❌ বাতিল", "➕ আমার গ্রুপে যোগ করুন", "📢 বট শেয়ার করুন",
        "🛠 মালিকের প্যানেল", "🌐 আপনার ভাষা বেছে নিন",
        "নিচের ২০টি ভাষার মধ্যে একটি বেছে নিন। আপনার পছন্দ অ্যাকাউন্টে সংরক্ষিত হবে।",
        "✅ ভাষা সফলভাবে সংরক্ষিত হয়েছে।", "👋 এই গ্রুপের পোস্টের সময় নির্ধারণ করুন",
        "*{group}* গ্রুপের পোস্ট নির্ধারণ করতে বটের ব্যক্তিগত চ্যাটে চালিয়ে যান।",
        "📩 বটের ব্যক্তিগত চ্যাট খুলুন",
        "ℹ️ শুধু এই গ্রুপের অ্যাডমিনই এখানে সময়সূচি তৈরি করতে পারবেন।",
        "👋 ব্যক্তিগত সেটআপ", "📍 নির্বাচিত গ্রুপ: *{group}*",
        "কনটেন্ট যোগ করতে *📅 নতুন পোস্টের সময় নির্ধারণ* চাপুন। এই গ্রুপটি স্বয়ংক্রিয়ভাবে নির্বাচিত হবে।",
        "📝 ধাপ ২/৮ — পোস্টের বিষয়বস্তু",
        "সময় নির্ধারণের জন্য লেখা বা মিডিয়া (ছবি, ভিডিও, ডকুমেন্ট, অডিও বা অ্যানিমেশন) পাঠান।",
        "🗂 একটি গ্রুপ বা চ্যানেল বেছে নিন", "✏️ Chat ID নিজে লিখুন",
        "⬅️ ফিরে যান", "✅ নিশ্চিত করুন",
    ),
    "id": (
        "🏠 Menu Utama — pilih tindakan", "📅 Jadwalkan Postingan Baru", "📋 Postingan Saya",
        "🗂 Kelola Antrean", "🎲 Koleksi Media", "📝 Templat", "📊 Statistik Saya",
        "🌐 Bahasa", "❌ Batal", "➕ Tambahkan ke Grup Saya", "📢 Bagikan Bot",
        "🛠 Panel Pemilik", "🌐 Pilih bahasa Anda",
        "Pilih satu dari 20 bahasa di bawah. Pilihan Anda akan disimpan di akun.",
        "✅ Bahasa berhasil disimpan.", "👋 Jadwalkan postingan untuk grup ini",
        "Lanjutkan di chat pribadi bot untuk menjadwalkan postingan bagi *{group}*.",
        "📩 Buka Chat Pribadi Bot",
        "ℹ️ Hanya admin grup ini yang dapat membuat jadwal untuk grup ini.",
        "👋 Pengaturan Pribadi", "📍 Grup Terpilih: *{group}*",
        "Pilih *📅 Jadwalkan Postingan Baru* untuk menambahkan konten. Grup ini akan dipilih otomatis.",
        "📝 Langkah 2/8 — Konten Postingan",
        "Kirim teks atau media yang ingin dijadwalkan (foto, video, dokumen, audio, atau animasi).",
        "🗂 Pilih grup atau kanal", "✏️ Masukkan Chat ID secara manual",
        "⬅️ Kembali", "✅ Konfirmasi",
    ),
    "ja": (
        "🏠 メインメニュー — 操作を選択", "📅 新しい投稿を予約", "📋 自分の投稿",
        "🗂 キューを管理", "🎲 メディアプール", "📝 テンプレート", "📊 統計",
        "🌐 言語", "❌ キャンセル", "➕ 自分のグループに追加", "📢 ボットを共有",
        "🛠 オーナーパネル", "🌐 言語を選択",
        "下の20言語から選択してください。選択した言語はアカウントに保存されます。",
        "✅ 言語を保存しました。", "👋 このグループの投稿を予約",
        "*{group}* の投稿を予約するには、ボットとのプライベートチャットで続けてください。",
        "📩 ボットとのプライベートチャットを開く",
        "ℹ️ このグループの管理者のみ予約を作成できます。",
        "👋 プライベート設定", "📍 選択中のグループ：*{group}*",
        "*📅 新しい投稿を予約* を選択して内容を追加してください。このグループは自動選択されます。",
        "📝 ステップ 2/8 — 投稿内容",
        "予約するテキストまたはメディア（写真、動画、文書、音声、アニメーション）を送信してください。",
        "🗂 グループまたはチャンネルを選択", "✏️ Chat IDを手動入力",
        "⬅️ 戻る", "✅ 確認",
    ),
    "ko": (
        "🏠 메인 메뉴 — 작업을 선택하세요", "📅 새 게시물 예약", "📋 내 게시물",
        "🗂 대기열 관리", "🎲 미디어 풀", "📝 템플릿", "📊 내 통계",
        "🌐 언어", "❌ 취소", "➕ 내 그룹에 추가", "📢 봇 공유",
        "🛠 소유자 패널", "🌐 언어 선택",
        "아래 20개 언어 중 하나를 선택하세요. 선택한 언어는 계정에 저장됩니다.",
        "✅ 언어가 저장되었습니다.", "👋 이 그룹의 게시물 예약하기",
        "*{group}* 게시물을 예약하려면 봇과의 개인 채팅에서 계속하세요.",
        "📩 봇 개인 채팅 열기",
        "ℹ️ 이 그룹의 관리자만 일정을 만들 수 있습니다.",
        "👋 개인 설정", "📍 선택된 그룹: *{group}*",
        "*📅 새 게시물 예약*을 눌러 콘텐츠를 추가하세요. 이 그룹이 자동으로 선택됩니다.",
        "📝 2/8단계 — 게시물 콘텐츠",
        "예약할 텍스트 또는 미디어(사진, 동영상, 문서, 오디오, 애니메이션)를 보내세요.",
        "🗂 그룹 또는 채널 선택", "✏️ Chat ID 직접 입력",
        "⬅️ 뒤로", "✅ 확인",
    ),
    "th": (
        "🏠 เมนูหลัก — เลือกการทำงาน", "📅 ตั้งเวลาโพสต์ใหม่", "📋 โพสต์ของฉัน",
        "🗂 จัดการคิว", "🎲 คลังสื่อ", "📝 เทมเพลต", "📊 สถิติของฉัน",
        "🌐 ภาษา", "❌ ยกเลิก", "➕ เพิ่มบอทในกลุ่มของฉัน", "📢 แชร์บอท",
        "🛠 แผงควบคุมเจ้าของ", "🌐 เลือกภาษาของคุณ",
        "เลือกหนึ่งใน 20 ภาษาด้านล่าง ระบบจะบันทึกภาษาที่คุณเลือกไว้ในบัญชี",
        "✅ บันทึกภาษาเรียบร้อยแล้ว", "👋 ตั้งเวลาโพสต์สำหรับกลุ่มนี้",
        "ไปที่แชตส่วนตัวของบอทเพื่อกำหนดเวลาโพสต์สำหรับ *{group}*",
        "📩 เปิดแชตส่วนตัวของบอท",
        "ℹ️ เฉพาะผู้ดูแลกลุ่มนี้เท่านั้นที่สร้างกำหนดการได้",
        "👋 ตั้งค่าในแชตส่วนตัว", "📍 กลุ่มที่เลือก: *{group}*",
        "เลือก *📅 ตั้งเวลาโพสต์ใหม่* เพื่อเพิ่มเนื้อหา ระบบจะเลือกกลุ่มนี้ให้อัตโนมัติ",
        "📝 ขั้นตอน 2/8 — เนื้อหาโพสต์",
        "ส่งข้อความหรือสื่อที่ต้องการตั้งเวลา (รูปภาพ วิดีโอ เอกสาร เสียง หรือภาพเคลื่อนไหว)",
        "🗂 เลือกกลุ่มหรือช่อง", "✏️ ป้อน Chat ID ด้วยตนเอง",
        "⬅️ ย้อนกลับ", "✅ ยืนยัน",
    ),
    "vi": (
        "🏠 Menu chính — chọn một thao tác", "📅 Lên lịch bài đăng mới", "📋 Bài đăng của tôi",
        "🗂 Quản lý hàng đợi", "🎲 Kho phương tiện", "📝 Mẫu", "📊 Thống kê của tôi",
        "🌐 Ngôn ngữ", "❌ Hủy", "➕ Thêm vào nhóm của tôi", "📢 Chia sẻ bot",
        "🛠 Bảng điều khiển chủ sở hữu", "🌐 Chọn ngôn ngữ",
        "Chọn một trong 20 ngôn ngữ bên dưới. Lựa chọn sẽ được lưu vào tài khoản của bạn.",
        "✅ Đã lưu ngôn ngữ.", "👋 Lên lịch bài đăng cho nhóm này",
        "Tiếp tục trong chat riêng với bot để lên lịch bài đăng cho *{group}*.",
        "📩 Mở chat riêng với bot",
        "ℹ️ Chỉ quản trị viên của nhóm này mới có thể tạo lịch đăng.",
        "👋 Thiết lập riêng tư", "📍 Nhóm đã chọn: *{group}*",
        "Chọn *📅 Lên lịch bài đăng mới* để thêm nội dung. Nhóm này sẽ được chọn tự động.",
        "📝 Bước 2/8 — Nội dung bài đăng",
        "Gửi văn bản hoặc phương tiện cần lên lịch (ảnh, video, tài liệu, âm thanh hoặc hoạt ảnh).",
        "🗂 Chọn nhóm hoặc kênh", "✏️ Nhập Chat ID thủ công",
        "⬅️ Quay lại", "✅ Xác nhận",
    ),
    "tr": (
        "🏠 Ana Menü — bir işlem seçin", "📅 Yeni Gönderi Planla", "📋 Gönderilerim",
        "🗂 Kuyruğu Yönet", "🎲 Medya Havuzu", "📝 Şablonlar", "📊 İstatistiklerim",
        "🌐 Dil", "❌ İptal", "➕ Grubuma Ekle", "📢 Botu Paylaş",
        "🛠 Sahip Paneli", "🌐 Dilinizi seçin",
        "Aşağıdaki 20 dilden birini seçin. Seçiminiz hesabınıza kaydedilecektir.",
        "✅ Dil başarıyla kaydedildi.", "👋 Bu grup için gönderi planlayın",
        "*{group}* için gönderi planlamak üzere botun özel sohbetinde devam edin.",
        "📩 Botun Özel Sohbetini Aç",
        "ℹ️ Bu grup için yalnızca grup yöneticileri zamanlama oluşturabilir.",
        "👋 Özel Kurulum", "📍 Seçilen Grup: *{group}*",
        "İçerik eklemek için *📅 Yeni Gönderi Planla* seçeneğine dokunun. Bu grup otomatik seçilecektir.",
        "📝 2/8. Adım — Gönderi İçeriği",
        "Planlamak istediğiniz metni veya medyayı (fotoğraf, video, belge, ses veya animasyon) gönderin.",
        "🗂 Bir grup veya kanal seçin", "✏️ Chat ID'yi elle girin",
        "⬅️ Geri", "✅ Onayla",
    ),
    "de": (
        "🏠 Hauptmenü — Aktion auswählen", "📅 Neuen Beitrag planen", "📋 Meine Beiträge",
        "🗂 Warteschlange verwalten", "🎲 Medienpool", "📝 Vorlagen", "📊 Meine Statistiken",
        "🌐 Sprache", "❌ Abbrechen", "➕ Zu meiner Gruppe hinzufügen", "📢 Bot teilen",
        "🛠 Eigentümer-Panel", "🌐 Sprache auswählen",
        "Wähle eine der 20 Sprachen unten aus. Deine Auswahl wird in deinem Konto gespeichert.",
        "✅ Sprache erfolgreich gespeichert.", "👋 Beiträge für diese Gruppe planen",
        "Fahre im privaten Chat mit dem Bot fort, um Beiträge für *{group}* zu planen.",
        "📩 Privaten Bot-Chat öffnen",
        "ℹ️ Nur ein Administrator dieser Gruppe kann dafür Zeitpläne erstellen.",
        "👋 Private Einrichtung", "📍 Ausgewählte Gruppe: *{group}*",
        "Wähle *📅 Neuen Beitrag planen*, um Inhalte hinzuzufügen. Diese Gruppe wird automatisch ausgewählt.",
        "📝 Schritt 2/8 — Beitragsinhalt",
        "Sende den Text oder das Medium, das geplant werden soll (Foto, Video, Dokument, Audio oder Animation).",
        "🗂 Gruppe oder Kanal auswählen", "✏️ Chat-ID manuell eingeben",
        "⬅️ Zurück", "✅ Bestätigen",
    ),
    "it": (
        "🏠 Menu principale — scegli un'azione", "📅 Programma nuovo post", "📋 I miei post",
        "🗂 Gestisci coda", "🎲 Raccolta media", "📝 Modelli", "📊 Le mie statistiche",
        "🌐 Lingua", "❌ Annulla", "➕ Aggiungi al mio gruppo", "📢 Condividi bot",
        "🛠 Pannello proprietario", "🌐 Scegli la lingua",
        "Seleziona una delle 20 lingue qui sotto. La scelta verrà salvata nel tuo account.",
        "✅ Lingua salvata correttamente.", "👋 Programma i post per questo gruppo",
        "Continua nella chat privata del bot per programmare i post per *{group}*.",
        "📩 Apri la chat privata del bot",
        "ℹ️ Solo un amministratore di questo gruppo può creare programmazioni.",
        "👋 Configurazione privata", "📍 Gruppo selezionato: *{group}*",
        "Scegli *📅 Programma nuovo post* per aggiungere contenuti. Questo gruppo verrà selezionato automaticamente.",
        "📝 Passaggio 2/8 — Contenuto del post",
        "Invia il testo o il contenuto multimediale da programmare (foto, video, documento, audio o animazione).",
        "🗂 Scegli un gruppo o canale", "✏️ Inserisci manualmente il Chat ID",
        "⬅️ Indietro", "✅ Conferma",
    ),
    "ur": (
        "🏠 مرکزی مینو — ایک عمل منتخب کریں", "📅 نئی پوسٹ شیڈول کریں", "📋 میری پوسٹس",
        "🗂 قطار کا انتظام", "🎲 میڈیا پول", "📝 ٹیمپلیٹس", "📊 میرے اعدادوشمار",
        "🌐 زبان", "❌ منسوخ کریں", "➕ میرے گروپ میں شامل کریں", "📢 بوٹ شیئر کریں",
        "🛠 مالک پینل", "🌐 اپنی زبان منتخب کریں",
        "نیچے دی گئی 20 زبانوں میں سے ایک منتخب کریں۔ آپ کا انتخاب اکاؤنٹ میں محفوظ ہوگا۔",
        "✅ زبان کامیابی سے محفوظ ہوگئی۔", "👋 اس گروپ کے لیے پوسٹس شیڈول کریں",
        "*{group}* کے لیے پوسٹس شیڈول کرنے کی خاطر بوٹ کی نجی چیٹ میں جاری رکھیں۔",
        "📩 بوٹ کی نجی چیٹ کھولیں",
        "ℹ️ صرف اس گروپ کا منتظم ہی یہاں شیڈول بنا سکتا ہے۔",
        "👋 نجی سیٹ اپ", "📍 منتخب گروپ: *{group}*",
        "مواد شامل کرنے کے لیے *📅 نئی پوسٹ شیڈول کریں* منتخب کریں۔ یہ گروپ خودکار طور پر منتخب ہوگا۔",
        "📝 مرحلہ 2/8 — پوسٹ کا مواد",
        "شیڈول کرنے کے لیے متن یا میڈیا (تصویر، ویڈیو، دستاویز، آڈیو یا اینیمیشن) بھیجیں۔",
        "🗂 گروپ یا چینل منتخب کریں", "✏️ Chat ID خود درج کریں",
        "⬅️ واپس", "✅ تصدیق کریں",
    ),
    "sw": (
        "🏠 Menyu Kuu — chagua kitendo", "📅 Panga Chapisho Jipya", "📋 Machapisho Yangu",
        "🗂 Simamia Foleni", "🎲 Maktaba ya Midia", "📝 Violezo", "📊 Takwimu Zangu",
        "🌐 Lugha", "❌ Ghairi", "➕ Ongeza kwenye Kikundi Changu", "📢 Shiriki Boti",
        "🛠 Paneli ya Mmiliki", "🌐 Chagua lugha yako",
        "Chagua mojawapo ya lugha 20 hapa chini. Chaguo lako litahifadhiwa kwenye akaunti yako.",
        "✅ Lugha imehifadhiwa kikamilifu.", "👋 Panga machapisho ya kikundi hiki",
        "Endelea kwenye mazungumzo ya faragha ya boti ili kupanga machapisho ya *{group}*.",
        "📩 Fungua Mazungumzo ya Faragha ya Boti",
        "ℹ️ Msimamizi wa kikundi hiki pekee ndiye anayeweza kuunda ratiba.",
        "👋 Mipangilio ya Faragha", "📍 Kikundi Kilichochaguliwa: *{group}*",
        "Chagua *📅 Panga Chapisho Jipya* ili kuongeza maudhui. Kikundi hiki kitachaguliwa kiotomatiki.",
        "📝 Hatua ya 2/8 — Maudhui ya Chapisho",
        "Tuma maandishi au midia unayotaka kupanga (picha, video, hati, sauti au uhuishaji).",
        "🗂 Chagua kikundi au chaneli", "✏️ Weka Chat ID mwenyewe",
        "⬅️ Rudi", "✅ Thibitisha",
    ),
}

TRANSLATIONS: dict[str, dict[str, str]] = {
    code: dict(zip(_KEYS, values, strict=True))
    for code, values in _CATALOG_ROWS.items()
}

_EXTRA_KEYS = (
    "queue_title", "queue_intro", "queue_set_slots", "queue_add_content",
    "queue_view", "queue_clear", "templates_empty", "templates_title",
    "posts_empty", "posts_title",
)
_EXTRA_ROWS: dict[str, tuple[str, ...]] = {
    "en": (
        "🗂 Queue Manager", "Set daily posting slots and add content. The bot publishes one item at each configured slot.",
        "⏰ Set Daily Slots", "➕ Add Content to Queue", "👁 View Queue", "🗑 Clear Queue",
        "📝 No templates saved yet. Save a post as a template from My Posts.",
        "📝 My Templates", "📋 No posts yet. Choose Schedule New Post to create one.", "📋 My Posts",
    ),
    "my": (
        "🗂 Queue စီမံခန့်ခွဲမှု", "နေ့စဉ်တင်မည့်အချိန်များ သတ်မှတ်ပြီး အကြောင်းအရာများ ထည့်ပါ။ သတ်မှတ်ထားသော အချိန်တိုင်းတွင် တစ်ခုစီ ပို့ပေးပါမည်။",
        "⏰ နေ့စဉ်အချိန် သတ်မှတ်ရန်", "➕ Queue ထဲသို့ အကြောင်းအရာထည့်ရန်", "👁 Queue ကြည့်ရန်", "🗑 Queue ရှင်းရန်",
        "📝 Template မသိမ်းရသေးပါ။ My Posts မှ Post တစ်ခုကို Template အဖြစ် သိမ်းနိုင်ပါသည်။",
        "📝 ကျွန်ုပ်၏ Templates", "📋 Post မရှိသေးပါ။ Schedule New Post ကိုရွေးပြီး စတင်ပါ။", "📋 ကျွန်ုပ်၏ Posts",
    ),
    "zh": (
        "🗂 队列管理", "设置每日发布时间并添加内容。机器人会在每个设定时段发布一条内容。",
        "⏰ 设置每日时段", "➕ 添加队列内容", "👁 查看队列", "🗑 清空队列",
        "📝 尚无模板。您可以在“我的帖子”中将帖子保存为模板。",
        "📝 我的模板", "📋 还没有帖子。选择“创建定时帖子”开始使用。", "📋 我的帖子",
    ),
    "es": (
        "🗂 Gestor de cola", "Configura horarios diarios y añade contenido. El bot publicará un elemento en cada horario.",
        "⏰ Configurar horarios diarios", "➕ Añadir contenido a la cola", "👁 Ver cola", "🗑 Vaciar cola",
        "📝 Aún no hay plantillas. Guarda una publicación como plantilla desde Mis publicaciones.",
        "📝 Mis plantillas", "📋 Aún no hay publicaciones. Elige Programar publicación para crear una.", "📋 Mis publicaciones",
    ),
    "hi": (
        "🗂 कतार प्रबंधन", "दैनिक पोस्ट समय तय करें और सामग्री जोड़ें। बॉट हर तय समय पर एक सामग्री प्रकाशित करेगा।",
        "⏰ दैनिक समय तय करें", "➕ कतार में सामग्री जोड़ें", "👁 कतार देखें", "🗑 कतार साफ़ करें",
        "📝 अभी कोई टेम्पलेट नहीं है। मेरी पोस्ट से किसी पोस्ट को टेम्पलेट के रूप में सहेजें।",
        "📝 मेरे टेम्पलेट", "📋 अभी कोई पोस्ट नहीं है। नई पोस्ट शेड्यूल करें चुनें।", "📋 मेरी पोस्ट",
    ),
    "ar": (
        "🗂 إدارة قائمة الانتظار", "حدّد أوقات النشر اليومية وأضف المحتوى. سينشر البوت عنصراً واحداً في كل موعد محدد.",
        "⏰ تحديد المواعيد اليومية", "➕ إضافة محتوى إلى القائمة", "👁 عرض القائمة", "🗑 مسح القائمة",
        "📝 لا توجد قوالب محفوظة بعد. احفظ منشوراً كقالب من منشوراتي.",
        "📝 قوالبي", "📋 لا توجد منشورات بعد. اختر جدولة منشور جديد للبدء.", "📋 منشوراتي",
    ),
    "pt": (
        "🗂 Gerenciador da fila", "Defina horários diários e adicione conteúdo. O bot publicará um item em cada horário configurado.",
        "⏰ Definir horários diários", "➕ Adicionar conteúdo à fila", "👁 Ver fila", "🗑 Limpar fila",
        "📝 Nenhum modelo salvo ainda. Salve uma publicação como modelo em Minhas publicações.",
        "📝 Meus modelos", "📋 Ainda não há publicações. Escolha Agendar nova publicação para criar uma.", "📋 Minhas publicações",
    ),
    "fr": (
        "🗂 Gestion de la file", "Définissez les horaires quotidiens et ajoutez du contenu. Le bot publiera un élément à chaque créneau.",
        "⏰ Définir les créneaux quotidiens", "➕ Ajouter du contenu à la file", "👁 Voir la file", "🗑 Vider la file",
        "📝 Aucun modèle enregistré. Enregistrez une publication comme modèle depuis Mes publications.",
        "📝 Mes modèles", "📋 Aucune publication. Choisissez Programmer une publication pour commencer.", "📋 Mes publications",
    ),
    "ru": (
        "🗂 Управление очередью", "Настройте ежедневные интервалы и добавьте контент. Бот будет публиковать по одному элементу в каждый заданный слот.",
        "⏰ Настроить ежедневные слоты", "➕ Добавить в очередь", "👁 Просмотреть очередь", "🗑 Очистить очередь",
        "📝 Шаблонов пока нет. Сохраните публикацию как шаблон в разделе «Мои публикации».",
        "📝 Мои шаблоны", "📋 Публикаций пока нет. Выберите «Запланировать публикацию», чтобы создать первую.", "📋 Мои публикации",
    ),
    "bn": (
        "🗂 কিউ পরিচালনা", "প্রতিদিনের পোস্টের সময় নির্ধারণ করে কনটেন্ট যোগ করুন। বট প্রতিটি নির্ধারিত সময়ে একটি করে পোস্ট করবে।",
        "⏰ দৈনিক সময় নির্ধারণ", "➕ কিউতে কনটেন্ট যোগ করুন", "👁 কিউ দেখুন", "🗑 কিউ খালি করুন",
        "📝 এখনো কোনো টেমপ্লেট নেই। আমার পোস্ট থেকে একটি পোস্ট টেমপ্লেট হিসেবে সংরক্ষণ করুন।",
        "📝 আমার টেমপ্লেট", "📋 এখনো কোনো পোস্ট নেই। শুরু করতে নতুন পোস্টের সময় নির্ধারণ করুন বেছে নিন।", "📋 আমার পোস্ট",
    ),
    "id": (
        "🗂 Pengelola Antrean", "Atur jadwal posting harian dan tambahkan konten. Bot akan menerbitkan satu item pada setiap slot.",
        "⏰ Atur Slot Harian", "➕ Tambahkan Konten ke Antrean", "👁 Lihat Antrean", "🗑 Bersihkan Antrean",
        "📝 Belum ada templat. Simpan postingan sebagai templat dari Postingan Saya.",
        "📝 Templat Saya", "📋 Belum ada postingan. Pilih Jadwalkan Postingan Baru untuk membuatnya.", "📋 Postingan Saya",
    ),
    "ja": (
        "🗂 キュー管理", "毎日の投稿時間を設定して内容を追加します。設定した各時間に1件ずつ投稿します。",
        "⏰ 毎日の時間を設定", "➕ キューに内容を追加", "👁 キューを表示", "🗑 キューを削除",
        "📝 保存されたテンプレートはありません。「自分の投稿」から投稿をテンプレートとして保存できます。",
        "📝 マイテンプレート", "📋 投稿はまだありません。「新しい投稿を予約」から作成してください。", "📋 自分の投稿",
    ),
    "ko": (
        "🗂 대기열 관리", "매일 게시할 시간을 설정하고 콘텐츠를 추가하세요. 설정된 각 시간에 항목 하나씩 게시됩니다.",
        "⏰ 일일 시간 설정", "➕ 대기열에 콘텐츠 추가", "👁 대기열 보기", "🗑 대기열 비우기",
        "📝 저장된 템플릿이 없습니다. 내 게시물에서 게시물을 템플릿으로 저장하세요.",
        "📝 내 템플릿", "📋 게시물이 없습니다. 새 게시물 예약을 선택해 시작하세요.", "📋 내 게시물",
    ),
    "th": (
        "🗂 จัดการคิว", "ตั้งเวลาโพสต์ประจำวันและเพิ่มเนื้อหา บอทจะโพสต์หนึ่งรายการในแต่ละช่วงเวลาที่กำหนด",
        "⏰ ตั้งเวลาโพสต์รายวัน", "➕ เพิ่มเนื้อหาในคิว", "👁 ดูคิว", "🗑 ล้างคิว",
        "📝 ยังไม่มีเทมเพลต บันทึกโพสต์เป็นเทมเพลตได้จากโพสต์ของฉัน",
        "📝 เทมเพลตของฉัน", "📋 ยังไม่มีโพสต์ เลือกตั้งเวลาโพสต์ใหม่เพื่อสร้างโพสต์", "📋 โพสต์ของฉัน",
    ),
    "vi": (
        "🗂 Quản lý hàng đợi", "Thiết lập giờ đăng hằng ngày và thêm nội dung. Bot sẽ đăng một mục vào mỗi khung giờ đã đặt.",
        "⏰ Đặt khung giờ hằng ngày", "➕ Thêm nội dung vào hàng đợi", "👁 Xem hàng đợi", "🗑 Xóa hàng đợi",
        "📝 Chưa có mẫu nào. Hãy lưu một bài đăng thành mẫu từ Bài đăng của tôi.",
        "📝 Mẫu của tôi", "📋 Chưa có bài đăng. Chọn Lên lịch bài đăng mới để tạo.", "📋 Bài đăng của tôi",
    ),
    "tr": (
        "🗂 Kuyruk Yönetimi", "Günlük paylaşım saatlerini ayarlayın ve içerik ekleyin. Bot, belirlenen her zaman aralığında bir öğe yayınlar.",
        "⏰ Günlük Saatleri Ayarla", "➕ Kuyruğa İçerik Ekle", "👁 Kuyruğu Görüntüle", "🗑 Kuyruğu Temizle",
        "📝 Henüz şablon yok. Gönderilerim bölümünden bir gönderiyi şablon olarak kaydedin.",
        "📝 Şablonlarım", "📋 Henüz gönderi yok. Oluşturmak için Yeni Gönderi Planla seçeneğini seçin.", "📋 Gönderilerim",
    ),
    "de": (
        "🗂 Warteschlangenverwaltung", "Lege tägliche Veröffentlichungszeiten fest und füge Inhalte hinzu. Der Bot veröffentlicht zu jedem Zeitfenster einen Eintrag.",
        "⏰ Tägliche Zeitfenster festlegen", "➕ Inhalt zur Warteschlange hinzufügen", "👁 Warteschlange ansehen", "🗑 Warteschlange leeren",
        "📝 Noch keine Vorlagen gespeichert. Speichere einen Beitrag unter Meine Beiträge als Vorlage.",
        "📝 Meine Vorlagen", "📋 Noch keine Beiträge. Wähle Neuen Beitrag planen, um einen zu erstellen.", "📋 Meine Beiträge",
    ),
    "it": (
        "🗂 Gestione coda", "Imposta gli orari giornalieri e aggiungi contenuti. Il bot pubblicherà un elemento in ogni fascia oraria configurata.",
        "⏰ Imposta orari giornalieri", "➕ Aggiungi contenuto alla coda", "👁 Visualizza coda", "🗑 Svuota coda",
        "📝 Nessun modello salvato. Salva un post come modello da I miei post.",
        "📝 I miei modelli", "📋 Nessun post ancora. Scegli Programma nuovo post per crearne uno.", "📋 I miei post",
    ),
    "ur": (
        "🗂 قطار کا انتظام", "روزانہ پوسٹنگ کے اوقات مقرر کریں اور مواد شامل کریں۔ بوٹ ہر مقررہ وقت پر ایک آئٹم شائع کرے گا۔",
        "⏰ روزانہ اوقات مقرر کریں", "➕ قطار میں مواد شامل کریں", "👁 قطار دیکھیں", "🗑 قطار صاف کریں",
        "📝 ابھی کوئی ٹیمپلیٹ محفوظ نہیں۔ میری پوسٹس سے کسی پوسٹ کو ٹیمپلیٹ کے طور پر محفوظ کریں۔",
        "📝 میرے ٹیمپلیٹس", "📋 ابھی کوئی پوسٹ نہیں۔ شروع کرنے کے لیے نئی پوسٹ شیڈول کریں منتخب کریں۔", "📋 میری پوسٹس",
    ),
    "sw": (
        "🗂 Usimamizi wa Foleni", "Weka nyakati za kila siku za kuchapisha na uongeze maudhui. Boti itachapisha kipengee kimoja kwa kila muda uliowekwa.",
        "⏰ Weka Nyakati za Kila Siku", "➕ Ongeza Maudhui kwenye Foleni", "👁 Tazama Foleni", "🗑 Futa Foleni",
        "📝 Hakuna violezo vilivyohifadhiwa. Hifadhi chapisho kama kiolezo kutoka Machapisho Yangu.",
        "📝 Violezo Vyangu", "📋 Bado hakuna machapisho. Chagua Panga Chapisho Jipya ili kuunda.", "📋 Machapisho Yangu",
    ),
}
for _locale, _values in _EXTRA_ROWS.items():
    TRANSLATIONS[_locale].update(dict(zip(_EXTRA_KEYS, _values, strict=True)))

_HELP_KEYS = ("help_title", "help_text")
_HELP_ROWS: dict[str, tuple[str, str]] = {
    "en": ("📖 Feature Guide", "📅 Schedule New Post — create one-time or recurring posts.\n📋 My Posts — view, pause, resume or delete posts.\n🗂 Manage Queue — set daily slots and queue content.\n🎲 Media Pool — publish random items from a media pool.\n📝 Templates — save and reuse post content.\n🌐 Language — change the bot language with /lang."),
    "my": ("📖 အသုံးပြုနည်း လမ်းညွှန်", "📅 Schedule New Post — တစ်ကြိမ်တည်း သို့မဟုတ် ထပ်ခါတလဲလဲ ပို့မည့် Post များ ဖန်တီးရန်။\n📋 My Posts — Post များ ကြည့်ရန်၊ ခေတ္တရပ်ရန်၊ ပြန်စရန် သို့မဟုတ် ဖျက်ရန်။\n🗂 Manage Queue — နေ့စဉ်အချိန်များ သတ်မှတ်ပြီး Queue ထဲသို့ အကြောင်းအရာထည့်ရန်။\n🎲 Media Pool — မီဒီယာများထဲမှ ကျပန်းရွေး၍ ပို့ရန်။\n📝 Templates — Post များကို Template အဖြစ် သိမ်းပြီး ပြန်သုံးရန်။\n🌐 Language — /lang ဖြင့် ဘာသာစကားပြောင်းရန်။"),
    "zh": ("📖 功能指南", "📅 创建定时帖子 — 创建单次或定期发布。\n📋 我的帖子 — 查看、暂停、恢复或删除帖子。\n🗂 队列管理 — 设置每日时段并添加队列内容。\n🎲 媒体池 — 从媒体池随机发布内容。\n📝 模板 — 保存并重复使用帖子内容。\n🌐 语言 — 使用 /lang 更改机器人语言。"),
    "es": ("📖 Guía de funciones", "📅 Programar publicación — crea publicaciones únicas o recurrentes.\n📋 Mis publicaciones — consulta, pausa, reanuda o elimina publicaciones.\n🗂 Gestionar cola — configura horarios diarios y añade contenido.\n🎲 Biblioteca multimedia — publica elementos aleatorios.\n📝 Plantillas — guarda y reutiliza contenido.\n🌐 Idioma — cambia el idioma con /lang."),
    "hi": ("📖 सुविधा गाइड", "📅 पोस्ट शेड्यूल करें — एक बार या बार-बार पोस्ट बनाएँ।\n📋 मेरी पोस्ट — पोस्ट देखें, रोकें, फिर शुरू करें या हटाएँ।\n🗂 कतार प्रबंधित करें — दैनिक समय तय करें और सामग्री जोड़ें।\n🎲 मीडिया पूल — मीडिया से यादृच्छिक सामग्री प्रकाशित करें।\n📝 टेम्पलेट — सामग्री सहेजें और दोबारा उपयोग करें।\n🌐 भाषा — /lang से भाषा बदलें।"),
    "ar": ("📖 دليل الميزات", "📅 جدولة منشور — أنشئ منشورات لمرة واحدة أو متكررة.\n📋 منشوراتي — اعرض المنشورات أو أوقفها أو استأنفها أو احذفها.\n🗂 إدارة القائمة — حدّد المواعيد اليومية وأضف المحتوى.\n🎲 مكتبة الوسائط — انشر عناصر عشوائية من المكتبة.\n📝 القوالب — احفظ المحتوى وأعد استخدامه.\n🌐 اللغة — غيّر لغة البوت باستخدام /lang."),
    "pt": ("📖 Guia de recursos", "📅 Agendar publicação — crie publicações únicas ou recorrentes.\n📋 Minhas publicações — veja, pause, retome ou exclua publicações.\n🗂 Gerenciar fila — defina horários diários e adicione conteúdo.\n🎲 Biblioteca de mídia — publique itens aleatórios.\n📝 Modelos — salve e reutilize conteúdo.\n🌐 Idioma — altere o idioma do bot com /lang."),
    "fr": ("📖 Guide des fonctionnalités", "📅 Programmer une publication — créez des publications uniques ou récurrentes.\n📋 Mes publications — consultez, mettez en pause, reprenez ou supprimez.\n🗂 Gérer la file — définissez les horaires quotidiens et ajoutez du contenu.\n🎲 Bibliothèque multimédia — publiez des éléments aléatoires.\n📝 Modèles — enregistrez et réutilisez du contenu.\n🌐 Langue — changez la langue du bot avec /lang."),
    "ru": ("📖 Руководство по функциям", "📅 Запланировать публикацию — создавайте разовые и повторяющиеся публикации.\n📋 Мои публикации — просматривайте, приостанавливайте, возобновляйте и удаляйте.\n🗂 Управление очередью — задавайте ежедневные интервалы и добавляйте контент.\n🎲 Медиатека — публикуйте случайные материалы.\n📝 Шаблоны — сохраняйте и используйте контент повторно.\n🌐 Язык — измените язык бота командой /lang."),
    "bn": ("📖 ফিচার নির্দেশিকা", "📅 পোস্টের সময় নির্ধারণ — একবার বা পুনরাবৃত্ত পোস্ট তৈরি করুন।\n📋 আমার পোস্ট — পোস্ট দেখুন, বিরতি দিন, আবার চালু করুন বা মুছুন।\n🗂 কিউ পরিচালনা — দৈনিক সময় নির্ধারণ করে কনটেন্ট যোগ করুন।\n🎲 মিডিয়া পুল — মিডিয়া থেকে এলোমেলো আইটেম প্রকাশ করুন।\n📝 টেমপ্লেট — কনটেন্ট সংরক্ষণ করে আবার ব্যবহার করুন।\n🌐 ভাষা — /lang দিয়ে বটের ভাষা পরিবর্তন করুন।"),
    "id": ("📖 Panduan Fitur", "📅 Jadwalkan Postingan — buat postingan sekali atau berulang.\n📋 Postingan Saya — lihat, jeda, lanjutkan, atau hapus postingan.\n🗂 Kelola Antrean — atur slot harian dan tambahkan konten.\n🎲 Koleksi Media — terbitkan item secara acak.\n📝 Templat — simpan dan gunakan kembali konten.\n🌐 Bahasa — ubah bahasa bot dengan /lang."),
    "ja": ("📖 機能ガイド", "📅 新しい投稿を予約 — 単発または繰り返し投稿を作成します。\n📋 自分の投稿 — 投稿の確認、一時停止、再開、削除を行います。\n🗂 キューを管理 — 毎日の投稿時間を設定して内容を追加します。\n🎲 メディアプール — メディアからランダムに投稿します。\n📝 テンプレート — 内容を保存して再利用します。\n🌐 言語 — /lang でボットの言語を変更します。"),
    "ko": ("📖 기능 안내", "📅 새 게시물 예약 — 일회성 또는 반복 게시물을 만듭니다.\n📋 내 게시물 — 게시물을 확인, 일시 중지, 재개 또는 삭제합니다.\n🗂 대기열 관리 — 일일 시간을 설정하고 콘텐츠를 추가합니다.\n🎲 미디어 풀 — 미디어 항목을 무작위로 게시합니다.\n📝 템플릿 — 콘텐츠를 저장하고 다시 사용합니다.\n🌐 언어 — /lang 명령으로 봇 언어를 변경합니다."),
    "th": ("📖 คู่มือฟีเจอร์", "📅 ตั้งเวลาโพสต์ใหม่ — สร้างโพสต์ครั้งเดียวหรือโพสต์ซ้ำ\n📋 โพสต์ของฉัน — ดู หยุดชั่วคราว เริ่มต่อ หรือลบโพสต์\n🗂 จัดการคิว — ตั้งเวลาโพสต์รายวันและเพิ่มเนื้อหา\n🎲 คลังสื่อ — สุ่มโพสต์รายการจากคลังสื่อ\n📝 เทมเพลต — บันทึกและนำเนื้อหากลับมาใช้\n🌐 ภาษา — เปลี่ยนภาษาบอทด้วย /lang"),
    "vi": ("📖 Hướng dẫn tính năng", "📅 Lên lịch bài đăng — tạo bài đăng một lần hoặc định kỳ.\n📋 Bài đăng của tôi — xem, tạm dừng, tiếp tục hoặc xóa bài đăng.\n🗂 Quản lý hàng đợi — đặt giờ hằng ngày và thêm nội dung.\n🎲 Kho phương tiện — đăng ngẫu nhiên nội dung trong kho.\n📝 Mẫu — lưu và sử dụng lại nội dung.\n🌐 Ngôn ngữ — đổi ngôn ngữ bot bằng /lang."),
    "tr": ("📖 Özellik Rehberi", "📅 Yeni Gönderi Planla — tek seferlik veya yinelenen gönderiler oluşturun.\n📋 Gönderilerim — gönderileri görüntüleyin, duraklatın, sürdürün veya silin.\n🗂 Kuyruğu Yönet — günlük saatleri ayarlayın ve içerik ekleyin.\n🎲 Medya Havuzu — havuzdan rastgele içerik yayınlayın.\n📝 Şablonlar — içeriği kaydedin ve yeniden kullanın.\n🌐 Dil — /lang ile botun dilini değiştirin."),
    "de": ("📖 Funktionsübersicht", "📅 Neuen Beitrag planen — einmalige oder wiederkehrende Beiträge erstellen.\n📋 Meine Beiträge — Beiträge ansehen, pausieren, fortsetzen oder löschen.\n🗂 Warteschlange verwalten — tägliche Zeitfenster festlegen und Inhalte hinzufügen.\n🎲 Medienpool — zufällige Medien veröffentlichen.\n📝 Vorlagen — Inhalte speichern und wiederverwenden.\n🌐 Sprache — die Bot-Sprache mit /lang ändern."),
    "it": ("📖 Guida alle funzioni", "📅 Programma nuovo post — crea post singoli o ricorrenti.\n📋 I miei post — visualizza, metti in pausa, riprendi o elimina i post.\n🗂 Gestisci coda — imposta gli orari giornalieri e aggiungi contenuti.\n🎲 Raccolta media — pubblica elementi casuali.\n📝 Modelli — salva e riutilizza i contenuti.\n🌐 Lingua — cambia la lingua del bot con /lang."),
    "ur": ("📖 خصوصیات کی رہنمائی", "📅 نئی پوسٹ شیڈول کریں — ایک بار یا بار بار پوسٹ بنائیں۔\n📋 میری پوسٹس — پوسٹس دیکھیں، روکیں، دوبارہ شروع کریں یا حذف کریں۔\n🗂 قطار کا انتظام — روزانہ اوقات مقرر کریں اور مواد شامل کریں۔\n🎲 میڈیا پول — میڈیا سے بے ترتیب آئٹمز شائع کریں۔\n📝 ٹیمپلیٹس — مواد محفوظ کرکے دوبارہ استعمال کریں۔\n🌐 زبان — /lang سے بوٹ کی زبان تبدیل کریں۔"),
    "sw": ("📖 Mwongozo wa Vipengele", "📅 Panga Chapisho Jipya — unda machapisho ya mara moja au yanayojirudia.\n📋 Machapisho Yangu — tazama, sitisha, endelea au futa machapisho.\n🗂 Simamia Foleni — weka nyakati za kila siku na uongeze maudhui.\n🎲 Maktaba ya Midia — chapisha vipengee vya nasibu.\n📝 Violezo — hifadhi na utumie maudhui tena.\n🌐 Lugha — badilisha lugha ya boti kwa /lang."),
}
for _locale, _values in _HELP_ROWS.items():
    TRANSLATIONS[_locale].update(dict(zip(_HELP_KEYS, _values, strict=True)))

DEFAULT_LANGUAGE = "en"


def normalize_language(code: str | None) -> str:
    """Return a supported locale code or English as a safe fallback."""
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


def translate(language: str | None, key: str, **values: Any) -> str:
    """Translate a UI key, falling back to English when a key is unavailable."""
    locale = normalize_language(language)
    template = TRANSLATIONS.get(locale, {}).get(
        key, TRANSLATIONS[DEFAULT_LANGUAGE].get(key, key)
    )
    try:
        return template.format(**values)
    except (KeyError, ValueError):
        return template


def language_keyboard() -> ReplyKeyboardMarkup:
    """Build a compact 2-column, one-time reply keyboard for language selection."""
    labels = list(LANGUAGE_LABELS.values())
    rows = [
        [KeyboardButton(labels[index]), KeyboardButton(labels[index + 1])]
        for index in range(0, len(labels), 2)
    ]
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=True,
        is_persistent=False,
        input_field_placeholder="Choose a language",
    )


def language_button_pattern() -> str:
    """Regex that matches only one of the official language-picker button labels."""
    return "^(?:" + "|".join(re.escape(label) for label in LANGUAGE_BY_LABEL) + ")$"


def language_from_button(label: str | None) -> str | None:
    """Resolve the exact language-picker label to its locale code."""
    return LANGUAGE_BY_LABEL.get(label or "")
