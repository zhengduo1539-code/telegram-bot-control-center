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
