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
    "menu_title", "schedule_new", "my_posts", "language", "cancel", "add_group", "share_bot",
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
        "🌐 Language", "❌ Cancel", "➕ Add to My Group", "📤 Share Bot",
        "🛠 Owner Panel", "🌐 Choose your language",
        "Select one of the 20 languages below. Your choice will be saved to your account.",
        "✅ Language saved successfully.", "👋 *Schedule Posts for This Group*",
        "To schedule posts for *{group}*, continue in the bot's private chat.",
        "📩 Open Bot in Private Chat",
        "ℹ️ Only this group's administrators can create schedules.",
        "👋 Private Setup", "📍 Selected Group: *{group}*",
        "Select *📅 Schedule New Post* to get started. This group will be selected automatically.",
        "📝 Step 2/8 — Post Content",
        "Send the text or media you want the bot to schedule (photo, video, document, audio or animation).",
        "🗂 Choose a group or channel", "✏️ Enter Chat ID manually",
        "⬅️ Back", "✅ Confirm",
    ),
    "my": (
        "🏠 ပင်မမီနူး — လုပ်ဆောင်ချက်ရွေးချယ်ပါ", "📅 Post အသစ် Schedule", "📋 My Posts",
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

_EXTRA_KEYS = ("posts_empty", "posts_title",)
_EXTRA_ROWS: dict[str, tuple[str, ...]] = {
    "en": (
        
        "📋 No posts yet. Choose Schedule New Post to create one.", "📋 My Posts",
    ),
    "my": (
        
        "📋 Post မရှိသေးပါ။ Schedule New Post ကိုရွေးပြီး စတင်ပါ။", "📋 ကျွန်ုပ်၏ Posts",
    ),
    "zh": (
        
        "📋 还没有帖子。选择“创建定时帖子”开始使用。", "📋 我的帖子",
    ),
    "es": (
        
        "📋 Aún no hay publicaciones. Elige Programar publicación para crear una.", "📋 Mis publicaciones",
    ),
    "hi": (
        
        "📋 अभी कोई पोस्ट नहीं है। नई पोस्ट शेड्यूल करें चुनें।", "📋 मेरी पोस्ट",
    ),
    "ar": (
        
        "📋 لا توجد منشورات بعد. اختر جدولة منشور جديد للبدء.", "📋 منشوراتي",
    ),
    "pt": (
        
        "📋 Ainda não há publicações. Escolha Agendar nova publicação para criar uma.", "📋 Minhas publicações",
    ),
    "fr": (
        
        "📋 Aucune publication. Choisissez Programmer une publication pour commencer.", "📋 Mes publications",
    ),
    "ru": (
        
        "📋 Публикаций пока нет. Выберите «Запланировать публикацию», чтобы создать первую.", "📋 Мои публикации",
    ),
    "bn": (
        
        "📋 এখনো কোনো পোস্ট নেই। শুরু করতে নতুন পোস্টের সময় নির্ধারণ করুন বেছে নিন।", "📋 আমার পোস্ট",
    ),
    "id": (
        
        "📋 Belum ada postingan. Pilih Jadwalkan Postingan Baru untuk membuatnya.", "📋 Postingan Saya",
    ),
    "ja": (
        
        "📋 投稿はまだありません。「新しい投稿を予約」から作成してください。", "📋 自分の投稿",
    ),
    "ko": (
        
        "📋 게시물이 없습니다. 새 게시물 예약을 선택해 시작하세요.", "📋 내 게시물",
    ),
    "th": (
        
        "📋 ยังไม่มีโพสต์ เลือกตั้งเวลาโพสต์ใหม่เพื่อสร้างโพสต์", "📋 โพสต์ของฉัน",
    ),
    "vi": (
        
        "📋 Chưa có bài đăng. Chọn Lên lịch bài đăng mới để tạo.", "📋 Bài đăng của tôi",
    ),
    "tr": (
        
        "📋 Henüz gönderi yok. Oluşturmak için Yeni Gönderi Planla seçeneğini seçin.", "📋 Gönderilerim",
    ),
    "de": (
        
        "📋 Noch keine Beiträge. Wähle Neuen Beitrag planen, um einen zu erstellen.", "📋 Meine Beiträge",
    ),
    "it": (
        
        "📋 Nessun post ancora. Scegli Programma nuovo post per crearne uno.", "📋 I miei post",
    ),
    "ur": (
        
        "📋 ابھی کوئی پوسٹ نہیں۔ شروع کرنے کے لیے نئی پوسٹ شیڈول کریں منتخب کریں۔", "📋 میری پوسٹس",
    ),
    "sw": (
        
        "📋 Bado hakuna machapisho. Chagua Panga Chapisho Jipya ili kuunda.", "📋 Machapisho Yangu",
    ),
}
for _locale, _values in _EXTRA_ROWS.items():
    TRANSLATIONS[_locale].update(dict(zip(_EXTRA_KEYS, _values, strict=True)))

_ACTION_KEYS = (
    "pause", "resume", "delete", "clone",
    "previous", "next",
)
_ACTION_ROWS: dict[str, tuple[str, ...]] = {
    "en": ("⏸ Pause", "▶️ Resume", "🗑 Delete", "🔁 Clone", "◀️ Prev", "Next ▶️",),
    "my": ("⏸ ခေတ္တရပ်", "▶️ ပြန်စ", "🗑 ဖျက်ရန်", "🔁 မိတ္တူပွား", "◀️ နောက်သို့", "ရှေ့သို့ ▶️",),
    "zh": ("⏸ 暂停", "▶️ 恢复", "🗑 删除", "🔁 克隆", "◀️ 上一页", "下一页 ▶️",),
    "es": ("⏸ Pausar", "▶️ Reanudar", "🗑 Eliminar", "🔁 Clonar", "◀️ Anterior", "Siguiente ▶️",),
    "hi": ("⏸ रोकें", "▶️ फिर शुरू करें", "🗑 हटाएँ", "🔁 क्लोन करें", "◀️ पिछला", "अगला ▶️",),
    "ar": ("⏸ إيقاف مؤقت", "▶️ استئناف", "🗑 حذف", "🔁 نسخ", "◀️ السابق", "التالي ▶️",),
    "pt": ("⏸ Pausar", "▶️ Retomar", "🗑 Excluir", "🔁 Clonar", "◀️ Anterior", "Próximo ▶️",),
    "fr": ("⏸ Pause", "▶️ Reprendre", "🗑 Supprimer", "🔁 Cloner", "◀️ Précédent", "Suivant ▶️",),
    "ru": ("⏸ Приостановить", "▶️ Возобновить", "🗑 Удалить", "🔁 Копировать", "◀️ Назад", "Далее ▶️",),
    "bn": ("⏸ বিরতি দিন", "▶️ আবার চালু করুন", "🗑 মুছুন", "🔁 কপি করুন", "◀️ আগের", "পরের ▶️",),
    "id": ("⏸ Jeda", "▶️ Lanjutkan", "🗑 Hapus", "🔁 Klon", "◀️ Sebelumnya", "Berikutnya ▶️",),
    "ja": ("⏸ 一時停止", "▶️ 再開", "🗑 削除", "🔁 複製", "◀️ 前へ", "次へ ▶️",),
    "ko": ("⏸ 일시 중지", "▶️ 재개", "🗑 삭제", "🔁 복제", "◀️ 이전", "다음 ▶️",),
    "th": ("⏸ หยุดชั่วคราว", "▶️ ทำต่อ", "🗑 ลบ", "🔁 ทำสำเนา", "◀️ ก่อนหน้า", "ถัดไป ▶️",),
    "vi": ("⏸ Tạm dừng", "▶️ Tiếp tục", "🗑 Xóa", "🔁 Sao chép", "◀️ Trước", "Tiếp ▶️",),
    "tr": ("⏸ Duraklat", "▶️ Sürdür", "🗑 Sil", "🔁 Kopyala", "◀️ Önceki", "Sonraki ▶️",),
    "de": ("⏸ Pausieren", "▶️ Fortsetzen", "🗑 Löschen", "🔁 Klonen", "◀️ Zurück", "Weiter ▶️",),
    "it": ("⏸ Pausa", "▶️ Riprendi", "🗑 Elimina", "🔁 Clona", "◀️ Precedente", "Successivo ▶️",),
    "ur": ("⏸ روکیں", "▶️ دوبارہ شروع", "🗑 حذف کریں", "🔁 نقل بنائیں", "◀️ پچھلا", "اگلا ▶️",),
    "sw": ("⏸ Sitisha", "▶️ Endelea", "🗑 Futa", "🔁 Nakili", "◀️ Iliyotangulia", "Inayofuata ▶️",),
}
for _locale, _values in _ACTION_ROWS.items():
    TRANSLATIONS[_locale].update(dict(zip(_ACTION_KEYS, _values, strict=True)))

_WIZARD_KEYS = (
    "recurrence_prompt", "recurrence_interval", "recurrence_days",
    "recurrence_once", "timezone_prompt", "first_run_prompt", "max_runs_prompt",
)
_WIZARD_ROWS: dict[str, tuple[str, ...]] = {
    "en": (
        "🔁 Step 3/8 — Recurrence Type\n\nHow often should this post be sent?",
        "🔂 Interval (every X min/hr/day)", "📆 Specific Days of Week",
        "1️⃣ One-Time Post", "🌍 Step 4/8 — Timezone\n\nSelect your timezone so the post time is interpreted correctly.",
        "🕐 Step 5/8 — First Run Time\n\n• HH:MM — today at this time\n• DD/MM/YYYY HH:MM — specific date\n\nOr tap Suggest Best Time.",
        "🔢 Step 6/8 — Max Runs\n\nHow many times should this post be sent? Choose a preset or enter a custom number.",
    ),
    "my": (
        "🔁 အဆင့် ၃/၈ — ထပ်ခါတလဲလဲ ပို့မည့်ပုံစံ\n\nဤ Post ကို ဘယ်နှစ်ကြိမ်၊ ဘယ်လိုအချိန်ခြားပြီး ပို့မလဲ?",
        "🔂 အချိန်ခြားပြီး ပို့ရန်", "📆 သတ်မှတ်ထားသော ရက်များတွင် ပို့ရန်",
        "1️⃣ တစ်ကြိမ်တည်း ပို့ရန်", "🌍 အဆင့် ၄/၈ — အချိန်ဇုန်\n\nPost ပို့မည့်အချိန်ကို မှန်ကန်စွာတွက်ချက်ရန် သင့်အချိန်ဇုန်ကို ရွေးပါ။",
        "🕐 အဆင့် ၅/၈ — ပထမဆုံးပို့မည့်အချိန်\n\n• HH:MM — ယနေ့အချိန်\n• DD/MM/YYYY HH:MM — သတ်မှတ်ရက်စွဲ\n\nသင့်တော်သောအချိန် အကြံပြုချက်ကိုလည်း ရယူနိုင်ပါသည်။",
        "🔢 အဆင့် ၆/၈ — ပို့မည့်အကြိမ်ရေ\n\nPost ကို စုစုပေါင်း ဘယ်နှစ်ကြိမ် ပို့စေချင်ပါသလဲ။ အမြန်ရွေးချယ်မှု သို့မဟုတ် အရေအတွက်ကို ကိုယ်တိုင်ထည့်ပါ။",
    ),
    "zh": (
        "🔁 第 3/8 步 — 重复方式\n\n此帖子需要多久发送一次？",
        "🔂 按间隔发送", "📆 在指定星期发送", "1️⃣ 仅发送一次",
        "🌍 第 4/8 步 — 时区\n\n选择时区，以正确计算帖子发送时间。",
        "🕐 第 5/8 步 — 首次发送时间\n\n• HH:MM — 今天的时间\n• DD/MM/YYYY HH:MM — 指定日期\n\n也可以点击推荐最佳时间。",
        "🔢 第 6/8 步 — 发送次数\n\n此帖子需要发送多少次？请选择预设值或输入自定义次数。",
    ),
    "es": (
        "🔁 Paso 3/8 — Tipo de repetición\n\n¿Con qué frecuencia debe enviarse esta publicación?",
        "🔂 Por intervalo", "📆 Días específicos de la semana", "1️⃣ Una sola vez",
        "🌍 Paso 4/8 — Zona horaria\n\nSelecciona tu zona horaria para calcular correctamente la hora de publicación.",
        "🕐 Paso 5/8 — Primera publicación\n\n• HH:MM — hoy a esta hora\n• DD/MM/YYYY HH:MM — fecha específica\n\nTambién puedes sugerir la mejor hora.",
        "🔢 Paso 6/8 — Número de envíos\n\n¿Cuántas veces debe enviarse? Elige una opción o introduce un número.",
    ),
    "hi": (
        "🔁 चरण 3/8 — दोहराव का प्रकार\n\nयह पोस्ट कितनी बार भेजी जानी चाहिए?",
        "🔂 अंतराल के अनुसार", "📆 सप्ताह के चुने हुए दिन", "1️⃣ केवल एक बार",
        "🌍 चरण 4/8 — समय क्षेत्र\n\nपोस्ट का समय सही रखने के लिए अपना समय क्षेत्र चुनें।",
        "🕐 चरण 5/8 — पहली पोस्ट का समय\n\n• HH:MM — आज का समय\n• DD/MM/YYYY HH:MM — निश्चित तारीख\n\nआप सबसे अच्छा समय सुझाने का विकल्प भी चुन सकते हैं।",
        "🔢 चरण 6/8 — कुल बार\n\nयह पोस्ट कितनी बार भेजी जाए? विकल्प चुनें या संख्या दर्ज करें।",
    ),
    "ar": (
        "🔁 الخطوة 3/8 — نوع التكرار\n\nكم مرة يجب إرسال هذا المنشور؟",
        "🔂 حسب الفاصل الزمني", "📆 أيام محددة من الأسبوع", "1️⃣ مرة واحدة فقط",
        "🌍 الخطوة 4/8 — المنطقة الزمنية\n\nاختر منطقتك الزمنية لحساب وقت النشر بشكل صحيح.",
        "🕐 الخطوة 5/8 — وقت الإرسال الأول\n\n• HH:MM — اليوم في هذا الوقت\n• DD/MM/YYYY HH:MM — تاريخ محدد\n\nيمكنك أيضاً اقتراح أفضل وقت.",
        "🔢 الخطوة 6/8 — عدد مرات الإرسال\n\nكم مرة يجب إرسال المنشور؟ اختر قيمة أو أدخل رقماً.",
    ),
    "pt": (
        "🔁 Etapa 3/8 — Tipo de repetição\n\nCom que frequência esta publicação deve ser enviada?",
        "🔂 Por intervalo", "📆 Dias específicos da semana", "1️⃣ Apenas uma vez",
        "🌍 Etapa 4/8 — Fuso horário\n\nEscolha seu fuso horário para calcular corretamente o horário da publicação.",
        "🕐 Etapa 5/8 — Primeiro envio\n\n• HH:MM — hoje neste horário\n• DD/MM/YYYY HH:MM — data específica\n\nVocê também pode sugerir o melhor horário.",
        "🔢 Etapa 6/8 — Número de envios\n\nQuantas vezes a publicação deve ser enviada? Escolha uma opção ou digite um número.",
    ),
    "fr": (
        "🔁 Étape 3/8 — Type de répétition\n\nÀ quelle fréquence cette publication doit-elle être envoyée ?",
        "🔂 Par intervalle", "📆 Jours spécifiques de la semaine", "1️⃣ Une seule fois",
        "🌍 Étape 4/8 — Fuseau horaire\n\nChoisissez votre fuseau horaire pour calculer correctement l'heure de publication.",
        "🕐 Étape 5/8 — Premier envoi\n\n• HH:MM — aujourd'hui à cette heure\n• DD/MM/YYYY HH:MM — date précise\n\nVous pouvez aussi suggérer le meilleur horaire.",
        "🔢 Étape 6/8 — Nombre d'envois\n\nCombien de fois publier ? Choisissez une option ou saisissez un nombre.",
    ),
    "ru": (
        "🔁 Шаг 3/8 — Тип повторения\n\nКак часто отправлять эту публикацию?",
        "🔂 Через интервал", "📆 В выбранные дни недели", "1️⃣ Только один раз",
        "🌍 Шаг 4/8 — Часовой пояс\n\nВыберите часовой пояс для правильного расчёта времени публикации.",
        "🕐 Шаг 5/8 — Время первой отправки\n\n• HH:MM — сегодня в это время\n• DD/MM/YYYY HH:MM — конкретная дата\n\nТакже можно предложить лучшее время.",
        "🔢 Шаг 6/8 — Количество отправок\n\nСколько раз отправлять публикацию? Выберите вариант или введите число.",
    ),
    "bn": (
        "🔁 ধাপ ৩/৮ — পুনরাবৃত্তির ধরন\n\nএই পোস্ট কত ঘন ঘন পাঠানো হবে?",
        "🔂 নির্দিষ্ট বিরতিতে", "📆 সপ্তাহের নির্দিষ্ট দিনে", "1️⃣ একবারই পাঠান",
        "🌍 ধাপ ৪/৮ — সময় অঞ্চল\n\nপোস্টের সময় ঠিকভাবে নির্ধারণ করতে আপনার সময় অঞ্চল বেছে নিন।",
        "🕐 ধাপ ৫/৮ — প্রথম পাঠানোর সময়\n\n• HH:MM — আজকের সময়\n• DD/MM/YYYY HH:MM — নির্দিষ্ট তারিখ\n\nসেরা সময়ের পরামর্শও নিতে পারেন।",
        "🔢 ধাপ ৬/৮ — মোট পাঠানোর সংখ্যা\n\nপোস্টটি কতবার পাঠানো হবে? একটি বিকল্প বেছে নিন বা সংখ্যা লিখুন।",
    ),
    "id": (
        "🔁 Langkah 3/8 — Jenis Pengulangan\n\nSeberapa sering postingan ini harus dikirim?",
        "🔂 Berdasarkan interval", "📆 Hari tertentu dalam seminggu", "1️⃣ Sekali saja",
        "🌍 Langkah 4/8 — Zona Waktu\n\nPilih zona waktu agar waktu posting dihitung dengan benar.",
        "🕐 Langkah 5/8 — Waktu Kirim Pertama\n\n• HH:MM — hari ini pada waktu tersebut\n• DD/MM/YYYY HH:MM — tanggal tertentu\n\nAnda juga dapat meminta saran waktu terbaik.",
        "🔢 Langkah 6/8 — Jumlah Pengiriman\n\nBerapa kali postingan ini dikirim? Pilih opsi atau masukkan angka.",
    ),
    "ja": (
        "🔁 ステップ 3/8 — 繰り返し設定\n\nこの投稿をどのくらいの頻度で送信しますか？",
        "🔂 間隔を指定", "📆 曜日を指定", "1️⃣ 1回のみ",
        "🌍 ステップ 4/8 — タイムゾーン\n\n投稿時刻を正しく計算するため、タイムゾーンを選択してください。",
        "🕐 ステップ 5/8 — 初回送信時刻\n\n• HH:MM — 今日の時刻\n• DD/MM/YYYY HH:MM — 指定日\n\nおすすめの時刻を提案することもできます。",
        "🔢 ステップ 6/8 — 送信回数\n\nこの投稿を何回送信しますか？プリセットを選ぶか数字を入力してください。",
    ),
    "ko": (
        "🔁 3/8단계 — 반복 유형\n\n이 게시물을 얼마나 자주 보내야 하나요?",
        "🔂 간격으로 반복", "📆 특정 요일에 반복", "1️⃣ 한 번만 게시",
        "🌍 4/8단계 — 시간대\n\n게시 시간을 정확하게 계산할 시간대를 선택하세요.",
        "🕐 5/8단계 — 첫 게시 시간\n\n• HH:MM — 오늘 해당 시간\n• DD/MM/YYYY HH:MM — 지정 날짜\n\n추천 시간을 확인할 수도 있습니다.",
        "🔢 6/8단계 — 게시 횟수\n\n게시물을 몇 번 보낼까요? 미리 설정된 옵션을 선택하거나 숫자를 입력하세요.",
    ),
    "th": (
        "🔁 ขั้นตอน 3/8 — รูปแบบการทำซ้ำ\n\nต้องการส่งโพสต์นี้บ่อยแค่ไหน?",
        "🔂 ทำซ้ำตามช่วงเวลา", "📆 วันที่กำหนดในสัปดาห์", "1️⃣ ส่งครั้งเดียว",
        "🌍 ขั้นตอน 4/8 — เขตเวลา\n\nเลือกเขตเวลาเพื่อคำนวณเวลาโพสต์ให้ถูกต้อง",
        "🕐 ขั้นตอน 5/8 — เวลาโพสต์ครั้งแรก\n\n• HH:MM — วันนี้ตามเวลานี้\n• DD/MM/YYYY HH:MM — วันที่กำหนด\n\nหรือขอคำแนะนำเวลาที่เหมาะสมได้",
        "🔢 ขั้นตอน 6/8 — จำนวนครั้งที่ส่ง\n\nต้องการส่งโพสต์นี้กี่ครั้ง? เลือกค่าที่กำหนดหรือพิมพ์ตัวเลข",
    ),
    "vi": (
        "🔁 Bước 3/8 — Kiểu lặp lại\n\nBạn muốn gửi bài đăng này thường xuyên như thế nào?",
        "🔂 Theo khoảng thời gian", "📆 Các ngày cụ thể trong tuần", "1️⃣ Chỉ gửi một lần",
        "🌍 Bước 4/8 — Múi giờ\n\nChọn múi giờ để tính chính xác thời gian đăng bài.",
        "🕐 Bước 5/8 — Thời gian gửi đầu tiên\n\n• HH:MM — hôm nay vào giờ này\n• DD/MM/YYYY HH:MM — ngày cụ thể\n\nBạn cũng có thể nhận đề xuất thời gian tốt nhất.",
        "🔢 Bước 6/8 — Số lần gửi\n\nBài đăng này sẽ được gửi bao nhiêu lần? Chọn tùy chọn hoặc nhập số.",
    ),
    "tr": (
        "🔁 3/8. Adım — Tekrarlama Türü\n\nBu gönderi ne sıklıkla gönderilmeli?",
        "🔂 Aralıklarla", "📆 Haftanın belirli günlerinde", "1️⃣ Yalnızca bir kez",
        "🌍 4/8. Adım — Saat Dilimi\n\nGönderi saatini doğru hesaplamak için saat diliminizi seçin.",
        "🕐 5/8. Adım — İlk Gönderim Zamanı\n\n• HH:MM — bugün bu saatte\n• DD/MM/YYYY HH:MM — belirli tarih\n\nEn iyi zaman önerisini de alabilirsiniz.",
        "🔢 6/8. Adım — Gönderim Sayısı\n\nBu gönderi kaç kez gönderilsin? Bir seçenek seçin veya sayı girin.",
    ),
    "de": (
        "🔁 Schritt 3/8 — Wiederholungstyp\n\nWie oft soll dieser Beitrag gesendet werden?",
        "🔂 In Intervallen", "📆 An bestimmten Wochentagen", "1️⃣ Nur einmal",
        "🌍 Schritt 4/8 — Zeitzone\n\nWähle deine Zeitzone, damit die Veröffentlichungszeit korrekt berechnet wird.",
        "🕐 Schritt 5/8 — Erste Veröffentlichungszeit\n\n• HH:MM — heute zu dieser Uhrzeit\n• DD/MM/YYYY HH:MM — bestimmtes Datum\n\nDu kannst auch eine passende Zeit vorschlagen lassen.",
        "🔢 Schritt 6/8 — Anzahl der Veröffentlichungen\n\nWie oft soll dieser Beitrag gesendet werden? Wähle eine Option oder gib eine Zahl ein.",
    ),
    "it": (
        "🔁 Passaggio 3/8 — Tipo di ripetizione\n\nCon quale frequenza deve essere inviato questo post?",
        "🔂 A intervalli", "📆 Giorni specifici della settimana", "1️⃣ Una sola volta",
        "🌍 Passaggio 4/8 — Fuso orario\n\nScegli il fuso orario per calcolare correttamente l'ora di pubblicazione.",
        "🕐 Passaggio 5/8 — Primo invio\n\n• HH:MM — oggi a quest'ora\n• DD/MM/YYYY HH:MM — data specifica\n\nPuoi anche chiedere di suggerire l'orario migliore.",
        "🔢 Passaggio 6/8 — Numero di invii\n\nQuante volte deve essere inviato il post? Scegli un'opzione o inserisci un numero.",
    ),
    "ur": (
        "🔁 مرحلہ 3/8 — تکرار کی قسم\n\nیہ پوسٹ کتنی بار بھیجی جائے؟",
        "🔂 وقفے کے مطابق", "📆 ہفتے کے مخصوص دن", "1️⃣ صرف ایک بار",
        "🌍 مرحلہ 4/8 — ٹائم زون\n\nپوسٹ کا وقت درست رکھنے کے لیے اپنا ٹائم زون منتخب کریں۔",
        "🕐 مرحلہ 5/8 — پہلی پوسٹ کا وقت\n\n• HH:MM — آج اس وقت\n• DD/MM/YYYY HH:MM — مخصوص تاریخ\n\nبہترین وقت کی تجویز بھی حاصل کر سکتے ہیں۔",
        "🔢 مرحلہ 6/8 — بھیجنے کی تعداد\n\nیہ پوسٹ کتنی بار بھیجی جائے؟ کوئی آپشن منتخب کریں یا نمبر درج کریں۔",
    ),
    "sw": (
        "🔁 Hatua ya 3/8 — Aina ya Marudio\n\nChapisho hili litumwe mara ngapi?",
        "🔂 Kwa vipindi", "📆 Siku maalum za wiki", "1️⃣ Mara moja tu",
        "🌍 Hatua ya 4/8 — Eneo la Saa\n\nChagua eneo lako la saa ili muda wa chapisho uhesabiwe kwa usahihi.",
        "🕐 Hatua ya 5/8 — Muda wa Kwanza wa Kutuma\n\n• HH:MM — leo kwa muda huu\n• DD/MM/YYYY HH:MM — tarehe maalum\n\nUnaweza pia kuomba muda bora upendekezwe.",
        "🔢 Hatua ya 6/8 — Idadi ya Machapisho\n\nChapisho hili litumwe mara ngapi? Chagua chaguo au weka nambari.",
    ),
}
for _locale, _values in _WIZARD_ROWS.items():
    TRANSLATIONS[_locale].update(dict(zip(_WIZARD_KEYS, _values, strict=True)))

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


def language_keyboard(selected_language: str | None = None) -> ReplyKeyboardMarkup:
    """Build a compact language keyboard and highlight the saved locale."""
    selected = normalize_language(selected_language)
    codes = list(LANGUAGE_LABELS)
    rows = [
        [
            KeyboardButton(
                LANGUAGE_LABELS[code],
                style="success" if code == selected else "primary",
            )
            for code in codes[index:index + 2]
        ]
        for index in range(0, len(codes), 2)
    ]
    rows.append([KeyboardButton(language_cancel_button_label(selected), style="primary")])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=True,
        is_persistent=False,
        input_field_placeholder="Choose a language",
    )


def language_cancel_button_label(language: str | None = None) -> str:
    """Return the localized Main Menu action shown below language choices.

    The translated menu title already includes its own leading emoji, so do
    not prepend another one here.
    """
    return translate(language, "menu_title")


def language_button_pattern() -> str:
    """Match official language choices and the localized language-picker exit."""
    labels = set(LANGUAGE_BY_LABEL) | {
        language_cancel_button_label(code) for code in LANGUAGES
    }
    return "^(?:" + "|".join(re.escape(label) for label in labels) + ")$"


def language_from_button(label: str | None) -> str | None:
    """Resolve the exact language-picker label to its locale code."""
    return LANGUAGE_BY_LABEL.get(label or "")
