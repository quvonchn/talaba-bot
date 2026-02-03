"""
Talaba Bot - Yotoqxona Navbatchilik Tizimi
Main bot file with all handlers
"""

import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, filters, ContextTypes,
    ConversationHandler
)
from datetime import date

import database as db

# Load environment
load_dotenv()

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============= SCHEDULE HELPERS =============

def is_general_cleaning_room(room_number: int) -> bool:
    """1, 6, 7, 12 xonalar glavni uborka qiladi"""
    room_suffix = room_number % 100
    return room_suffix in [1, 6, 7, 12]


async def generate_duty_schedule():
    """Navbat jadvalini yaratish (12 kunlik davr)"""
    import aiosqlite
    today = date.today()
    
    async with aiosqlite.connect(db.DATABASE_PATH) as conn:
        for floor in range(2, 10):
            cursor = await conn.execute(
                "SELECT number, duty_days FROM rooms WHERE floor = ? ORDER BY number",
                (floor,)
            )
            rooms = await cursor.fetchall()
            
            duty_sequence = []
            for room_num, duty_days in rooms:
                duty_sequence.extend([room_num] * duty_days)
            
            day_of_year = today.timetuple().tm_yday
            duty_index = day_of_year % len(duty_sequence)
            duty_room = duty_sequence[duty_index]
            
            existing = await conn.execute(
                "SELECT id FROM duty_schedule WHERE date = ? AND floor = ?",
                (today.isoformat(), floor)
            )
            if not await existing.fetchone():
                await conn.execute(
                    """INSERT INTO duty_schedule (date, room_number, floor, status)
                       VALUES (?, ?, ?, 'pending')""",
                    (today.isoformat(), duty_room, floor)
                )
        
        await conn.commit()


# ============= COMMAND HANDLERS =============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - bot haqida"""
    user = update.effective_user
    
    keyboard = [
        [InlineKeyboardButton("📅 Bugungi navbat", callback_data="today")],
        [InlineKeyboardButton("📋 Jadval", callback_data="schedule")],
        [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🎓 Assalomu alaykum, {user.first_name}!\n\n"
        "Men **Talaba Bot** - yotoqxona navbatchilik tizimi.\n\n"
        "📌 Asosiy vazifalarim:\n"
        "• Navbatchilik jadvalini boshqarish\n"
        "• Sardorlarga tasdiqlash imkoniyati\n"
        "• Tarbiyachiga kunlik hisobot\n\n"
        "Quyidagi tugmalardan foydalaning 👇",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = """
🆘 **YORDAM**

**Talabalar uchun:**
/navbat - Bugungi navbatchilar
/jadval [qavat] - Qavat jadvali

**Sardorlar uchun:**
/tasdiqlash [xona] - Navbatchilikni tasdiqlash

**Tarbiyachi uchun:**
/hisobot - Kunlik hisobot
/jazo [xona] [kun] - Jazo berish
/xabar - Guruhlarga xabar yuborish

**Sozlash:**
/setgroup [qavat] - Guruhni ulash
/setsardor [qavat] - Sardorni belgilash
/setadmin - Adminni belgilash
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def today_duty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bugungi navbatni ko'rsatish"""
    # Avval jadval yaratish
    await generate_duty_schedule()
    
    message = f"📅 **BUGUNGI NAVBATCHILAR** - {date.today().strftime('%d.%m.%Y')}\n\n"
    
    for floor in range(2, 10):
        duty = await db.get_today_duty(floor)
        if duty:
            status = "✅" if duty['status'] == 'completed' else "⏳"
            room_num = duty['room_number']
            message += f"{status} {floor}-qavat: **{room_num}-xona**"
            if is_general_cleaning_room(room_num):
                message += " 🧹 (Glavni uborka)"
            message += "\n"
        else:
            message += f"❓ {floor}-qavat: Belgilanmagan\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')


async def confirm_duty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sardor navbatchilikni tasdiqlaydi"""
    if not context.args:
        await update.message.reply_text(
            "❌ Xona raqamini kiriting!\n"
            "Misol: `/tasdiqlash 201`",
            parse_mode='Markdown'
        )
        return
    
    try:
        room_number = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Noto'g'ri xona raqami!")
        return
    
    user = update.effective_user
    await db.confirm_duty(room_number, f"{user.id}:{user.first_name}")
    
    await update.message.reply_text(
        f"✅ **{room_number}-xona** navbatchiligi tasdiqlandi!\n"
        f"👤 Tasdiqlagan: {user.first_name}",
        parse_mode='Markdown'
    )


async def send_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guruhlarga navbatchilik xabarini yuborish"""
    user = update.effective_user
    admin_id = os.getenv('ADMIN_ID')
    
    if str(user.id) != admin_id:
        await update.message.reply_text("❌ Bu buyruq faqat admin uchun!")
        return
    
    await generate_duty_schedule()
    
    groups = {
        (2, 3): os.getenv('GROUP_2_3'),
        (4, 5): os.getenv('GROUP_4_5'),
        (6, 7): os.getenv('GROUP_6_7'),
        (8, 9): os.getenv('GROUP_8_9'),
    }
    
    sent_count = 0
    for floors, group_id in groups.items():
        if not group_id:
            continue
            
        message = f"🏢 **{floors[0]}-{floors[1]} QAVATLAR NAVBATCHILIGI**\n\n"
        
        for floor in floors:
            duty = await db.get_today_duty(floor)
            if duty:
                room_num = duty['room_number']
                message += f"📍 {floor}-qavat: **{room_num}-xona**"
                if is_general_cleaning_room(room_num):
                    message += "\n   🧹 *Bugun xonadagi yashovchilar soni 5 tani tashkil qilgani uchun bugun GLAVNI UBORKA qilasiz!*"
                message += "\n"
        
        message += f"\n⏰ Deadline: 22:50"
        message += f"\n✅ Bajarilgach sardorga tasdiqlating!"
        
        try:
            await context.bot.send_message(
                chat_id=group_id,
                text=message,
                parse_mode='Markdown'
            )
            sent_count += 1
        except Exception as e:
            logger.error(f"Xabar yuborishda xato: {e}")
    
    await update.message.reply_text(f"✅ {sent_count} ta guruhga xabar yuborildi!")


async def admin_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tarbiyachiga hisobot"""
    duties = await db.get_all_today_duties()
    completed = [d for d in duties if d['status'] == 'completed']
    pending = [d for d in duties if d['status'] == 'pending']
    
    message = f"📊 **KUNLIK HISOBOT** - {date.today().strftime('%d.%m.%Y')}\n\n"
    
    if completed:
        message += "✅ **Bajarildi:**\n"
        for d in completed:
            message += f"   • {d['floor']}-qavat ({d['room_number']}-xona)\n"
    
    if pending:
        message += "\n❌ **Bajarilmadi:**\n"
        for d in pending:
            message += f"   • {d['floor']}-qavat ({d['room_number']}-xona)\n"
    
    total = len(duties) if duties else 1
    pct = len(completed)*100//total
    message += f"\n📈 Natija: {len(completed)}/{len(duties)} ({pct}%)"
    
    keyboard = []
    for d in pending:
        keyboard.append([
            InlineKeyboardButton(
                f"⚠️ {d['room_number']}-xonaga jazo",
                callback_data=f"penalty_{d['room_number']}"
            )
        ])
    
    if keyboard:
        keyboard.append([InlineKeyboardButton("✅ OK", callback_data="dismiss_report")])
        reply_markup = InlineKeyboardMarkup(keyboard)
    else:
        reply_markup = None
    
    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)


async def set_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guruhni qavat bilan ulash"""
    if update.effective_chat.type not in ['group', 'supergroup']:
        await update.message.reply_text("❌ Bu buyruq faqat guruhda ishlaydi!")
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ Qavat raqamini kiriting!\n"
            "Misol: `/setgroup 2` yoki `/setgroup 2-3`",
            parse_mode='Markdown'
        )
        return
    
    group_id = str(update.effective_chat.id)
    floors_arg = context.args[0]
    
    if '-' in floors_arg:
        start, end = floors_arg.split('-')
        floors = list(range(int(start), int(end) + 1))
    else:
        floors = [int(floors_arg)]
    
    for floor in floors:
        await db.set_floor_group(floor, group_id)
    
    await update.message.reply_text(
        f"✅ Bu guruh **{floors_arg}**-qavat(lar) uchun belgilandi!\n"
        f"🆔 Guruh ID: `{group_id}`",
        parse_mode='Markdown'
    )


async def set_supervisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sardorni belgilash"""
    if not context.args:
        await update.message.reply_text(
            "❌ Qavat raqamini kiriting!\n"
            "Misol: `/setsardor 2`",
            parse_mode='Markdown'
        )
        return
    
    floor = int(context.args[0])
    user = update.effective_user
    
    await db.set_floor_supervisor(floor, str(user.id), user.first_name)
    
    await update.message.reply_text(
        f"✅ {user.first_name} **{floor}**-qavat sardori sifatida belgilandi!",
        parse_mode='Markdown'
    )


async def set_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tarbiyachini belgilash"""
    user = update.effective_user
    
    await update.message.reply_text(
        f"✅ {user.first_name} tarbiyachi sifatida belgilandi!\n\n"
        f"🆔 Sizning ID: `{user.id}`\n\n"
        f"📝 `.env` faylidagi ADMIN_ID ga shu IDni yozing.",
        parse_mode='Markdown'
    )


async def add_penalty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Jazo berish"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Format: `/jazo [xona] [kun_soni]`\n"
            "Misol: `/jazo 201 3`",
            parse_mode='Markdown'
        )
        return
    
    room_number = int(context.args[0])
    days = int(context.args[1])
    user = update.effective_user
    
    await db.add_penalty(
        room_number, 
        f"{days} kun navbatchilik",
        "Navbatchilikni bajarmaganligi uchun",
        days,
        f"{user.id}:{user.first_name}"
    )
    
    await update.message.reply_text(
        f"⚠️ **{room_number}-xona jazolandi!**\n"
        f"📋 Jazo: {days} kun ketma-ket navbatchilik",
        parse_mode='Markdown'
    )


# ============= CALLBACK HANDLERS =============

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "today":
        await generate_duty_schedule()
        message = f"📅 **BUGUNGI NAVBATCHILAR**\n\n"
        for floor in range(2, 10):
            duty = await db.get_today_duty(floor)
            if duty:
                status = "✅" if duty['status'] == 'completed' else "⏳"
                room_num = duty['room_number']
                message += f"{status} {floor}-qavat: **{room_num}-xona**"
                if is_general_cleaning_room(room_num):
                    message += " 🧹 (Glavni uborka)"
                message += "\n"
        await query.edit_message_text(message, parse_mode='Markdown')
    
    elif query.data == "schedule":
        await query.edit_message_text(
            "📋 Jadval uchun /jadval buyrug'ini ishlating yoki\n"
            "/navbat buyrug'i bilan bugungi navbatchilarni ko'ring",
            parse_mode='Markdown'
        )
    
    elif query.data == "help":
        await query.edit_message_text(
            "🆘 Yordam uchun /help buyrug'ini ishlating",
            parse_mode='Markdown'
        )
    
    elif query.data.startswith("penalty_"):
        room_number = int(query.data.split("_")[1])
        keyboard = [
            [InlineKeyboardButton("3 kun", callback_data=f"penalize_{room_number}_3")],
            [InlineKeyboardButton("5 kun", callback_data=f"penalize_{room_number}_5")],
            [InlineKeyboardButton("🔙 Bekor", callback_data="dismiss_report")]
        ]
        await query.edit_message_text(
            f"⚠️ **{room_number}-xonaga jazo**\n\nNecha kun navbatchilik?",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data.startswith("penalize_"):
        parts = query.data.split("_")
        room_number = int(parts[1])
        days = int(parts[2])
        user = update.effective_user
        
        await db.add_penalty(
            room_number, 
            f"{days} kun navbatchilik",
            "Navbatchilikni bajarmaganligi uchun",
            days,
            f"{user.id}:{user.first_name}"
        )
        
        await query.edit_message_text(
            f"✅ **{room_number}-xona jazolandi!**\n"
            f"📋 Jazo: {days} kun ketma-ket navbatchilik",
            parse_mode='Markdown'
        )
    
    elif query.data == "dismiss_report":
        await query.edit_message_text("✅ Hisobot yopildi.")


# ============= MAIN =============

async def post_init(application):
    """Bot ishga tushganda"""
    await db.init_db()
    logger.info("✅ Database initialized")
    logger.info("🤖 Talaba Bot tayyor!")


# ============= ATTENDANCE CONVERSATION =============

SELECTING_FLOOR, ENTERING_COUNT = range(2)

async def start_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Davomat kiritishni boshlash"""
    user = update.effective_user
    supervisor = await db.get_floor_supervisor_by_telegram(str(user.id))
    
    if not supervisor:
        await update.message.reply_text(
            "❌ Siz sardor sifatida ro'yxatdan o'tmagansiz!\n"
            "Admin panelda sardor sifatida ro'yxatdan o'ting."
        )
        return ConversationHandler.END
    
    floors = supervisor['floors'].split(',')
    context.user_data['floors_to_submit'] = floors
    context.user_data['submitted_floors'] = []
    context.user_data['supervisor_name'] = supervisor['name']
    
    keyboard = [[InlineKeyboardButton(f"{f}-qavat", callback_data=f"att_floor_{f}")] for f in floors]
    
    await update.message.reply_text(
        f"📊 **DAVOMAT KIRITISH**\n\n"
        f"👤 Sardor: {supervisor['name']}\n"
        f"🏢 Qavatlar: {supervisor['floors']}\n\n"
        "Qaysi qavat uchun kiritasiz? 👇",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECTING_FLOOR


async def floor_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qavat tanlanganda"""
    query = update.callback_query
    await query.answer()
    
    floor = query.data.replace("att_floor_", "")
    context.user_data['current_floor'] = floor
    
    await query.edit_message_text(
        f"🏢 **{floor}-QAVAT**\n\n"
        "Nechta talaba bor? (faqat son yozing)",
        parse_mode='Markdown'
    )
    return ENTERING_COUNT


async def count_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Talabalar soni kiritilganda"""
    try:
        count = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ Faqat son kiriting!")
        return ENTERING_COUNT
    
    floor = int(context.user_data['current_floor'])
    supervisor_name = context.user_data['supervisor_name']
    
    await db.save_attendance(floor, count, supervisor_name)
    
    context.user_data['submitted_floors'].append(str(floor))
    remaining = [f for f in context.user_data['floors_to_submit'] 
                 if f not in context.user_data['submitted_floors']]
    
    if remaining:
        keyboard = [[InlineKeyboardButton(f"{f}-qavat", callback_data=f"att_floor_{f}")] for f in remaining]
        await update.message.reply_text(
            f"✅ **{floor}-qavat:** {count} ta\n\n"
            "Keyingi qavat uchun tanlang 👇",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SELECTING_FLOOR
    else:
        await update.message.reply_text(
            f"✅ **Davomat kiritildi!**\n\n"
            f"Rahmat, {supervisor_name}! 🎉",
            parse_mode='Markdown'
        )
        return ConversationHandler.END


async def cancel_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bekor qilish"""
    await update.message.reply_text("❌ Bekor qilindi.")
    return ConversationHandler.END


async def test_attendance_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test uchun davomat so'rovini yuborish (admin uchun)"""
    user = update.effective_user
    admin_id = os.getenv('ADMIN_ID')
    
    if str(user.id) != admin_id:
        await update.message.reply_text("❌ Bu buyruq faqat admin uchun!")
        return
    
    supervisors = await db.get_all_floor_supervisors()
    sent = 0
    
    for sup in supervisors:
        floors = sup['floors'].split(',')
        keyboard = [[InlineKeyboardButton(f"{f}-qavat", callback_data=f"att_floor_{f}")] for f in floors]
        
        try:
            await context.bot.send_message(
                chat_id=sup['telegram_id'],
                text=f"📊 **DAVOMAT VAQTI!**\n\n"
                     f"Assalomu alaykum, {sup['name']}!\n"
                     f"Iltimos, qavatlaringiz uchun talabalar sonini kiriting.\n\n"
                     "Qavat tanlang 👇",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            sent += 1
        except Exception as e:
            logger.error(f"Davomat so'rovi yuborishda xato: {e}")
    
    await update.message.reply_text(f"✅ {sent} ta sardorga davomat so'rovi yuborildi!")


async def send_attendance_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Davomat hisobotini yuborish"""
    attendance = await db.get_today_attendance()
    
    if not attendance:
        await update.message.reply_text("❌ Bugun hali davomat kiritilmagan!")
        return
    
    message = f"📊 **KUNLIK DAVOMAT**\n{date.today().strftime('%d.%m.%Y')}\n\n"
    total = 0
    
    for a in attendance:
        message += f"🏢 {a['floor']}-qavat: **{a['student_count']}** ta\n"
        total += a['student_count']
    
    message += f"\n────────────\n"
    message += f"📈 **JAMI:** {total} ta talaba"
    
    await update.message.reply_text(message, parse_mode='Markdown')


def main():
    """Start the bot"""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not found!")
        return
    
    # Create application without job_queue
    app = Application.builder().token(token).job_queue(None).post_init(post_init).build()
    
    # Attendance ConversationHandler
    attendance_conv = ConversationHandler(
        entry_points=[CommandHandler("davomat", start_attendance)],
        states={
            SELECTING_FLOOR: [CallbackQueryHandler(floor_selected, pattern=r"^att_floor_")],
            ENTERING_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, count_entered)],
        },
        fallbacks=[CommandHandler("bekor", cancel_attendance)],
    )
    app.add_handler(attendance_conv)
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("yordam", help_command))
    app.add_handler(CommandHandler("navbat", today_duty))
    app.add_handler(CommandHandler("bugun", today_duty))
    app.add_handler(CommandHandler("tasdiqlash", confirm_duty))
    app.add_handler(CommandHandler("xabar", send_notifications))
    app.add_handler(CommandHandler("hisobot", admin_report))
    app.add_handler(CommandHandler("setgroup", set_group))
    app.add_handler(CommandHandler("setsardor", set_supervisor))
    app.add_handler(CommandHandler("setadmin", set_admin))
    app.add_handler(CommandHandler("jazo", add_penalty))
    app.add_handler(CommandHandler("testdavomat", test_attendance_request))
    app.add_handler(CommandHandler("davomathisobot", send_attendance_report))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(button_callback))
    
    logger.info("🤖 Talaba Bot ishga tushdi!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
