"""
Scheduler module for Talaba Bot
Uses telegram.ext.JobQueue for scheduled tasks
"""

from datetime import date, time, datetime
import database as db
import aiosqlite
import os


async def generate_duty_schedule():
    """Navbat jadvalini yaratish (16 kunlik davr)"""
    today = date.today()
    
    async with aiosqlite.connect(db.DATABASE_PATH) as conn:
        # Har bir qavat uchun
        for floor in range(2, 10):
            # Avval mavjud jadval bor-yo'qligini tekshirish
            existing = await conn.execute(
                "SELECT id FROM duty_schedule WHERE date = ? AND floor = ?",
                (today.isoformat(), floor)
            )
            if await existing.fetchone():
                continue  # Allaqachon mavjud
            
            # 1. Avval queue'dagi xonalarni tekshirish (skip qilinganlar)
            queued = await db.get_queued_room(floor)
            if queued:
                duty_room = queued['room_number']
                await db.clear_duty_queue(floor, duty_room)
            else:
                # 2. Normal hisoblash (offset bilan)
                cursor = await conn.execute(
                    "SELECT number, duty_days FROM rooms WHERE floor = ? ORDER BY number",
                    (floor,)
                )
                rooms = await cursor.fetchall()
                
                # Navbat ketma-ketligini yaratish
                duty_sequence = []
                for room_num, duty_days in rooms:
                    duty_sequence.extend([room_num] * duty_days)
                
                # Bugungi kun uchun navbatchi xonani hisoblash
                day_of_year = today.timetuple().tm_yday
                # Har qavat uchun offset - shunda har qavatda har xil xona
                floor_offset = (floor - 2) * 3  # 2-qavat: 0, 3-qavat: 3, 4-qavat: 6...
                duty_index = (day_of_year + floor_offset) % len(duty_sequence)
                duty_room = duty_sequence[duty_index]
            
            # Bazaga yozish
            await conn.execute(
                """INSERT INTO duty_schedule (date, room_number, floor, status)
                   VALUES (?, ?, ?, 'pending')""",
                (today.isoformat(), duty_room, floor)
            )
        
        await conn.commit()


async def send_duty_notifications(context):
    """21:00 da navbatchilik xabarlarini yuborish"""
    bot = context.bot
    
    # Bugungi navbatlarni yaratish
    await generate_duty_schedule()
    
    groups = {
        (2, 3): os.getenv('GROUP_2_3'),
        (4, 5): os.getenv('GROUP_4_5'),
        (6, 7): os.getenv('GROUP_6_7'),
        (8, 9): os.getenv('GROUP_8_9'),
    }
    
    for floors, group_id in groups.items():
        if not group_id:
            continue
            
        message = f"🏢 **{floors[0]}-{floors[1]} QAVATLAR NAVBATCHILIGI**\n\n"
        
        for floor in floors:
            duty = await db.get_today_duty(floor)
            if duty:
                message += f"📍 {floor}-qavat: **{duty['room_number']}-xona**\n"
        
        message += f"\n⏰ Deadline: 22:50"
        message += f"\n✅ Bajarilgach sardorga tasdiqlating!"
        
        try:
            await bot.send_message(
                chat_id=group_id,
                text=message,
                parse_mode='Markdown'
            )
        except Exception as e:
            print(f"Xabar yuborishda xato: {e}")


async def send_admin_report(context):
    """23:00 da tarbiyachiga hisobot"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    bot = context.bot
    admin_id = os.getenv('ADMIN_ID')
    if not admin_id:
        return
    
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
    
    total = len(duties)
    pct = len(completed)*100//total if total else 0
    message += f"\n📈 Natija: {len(completed)}/{total} ({pct}%)"
    
    # Jazo tugmalari
    keyboard = []
    for d in pending:
        keyboard.append([
            InlineKeyboardButton(
                f"⚠️ {d['room_number']}-xonaga jazo",
                callback_data=f"penalty_{d['room_number']}"
            )
        ])
    
    if keyboard:
        keyboard.append([
            InlineKeyboardButton("✅ Hammasi OK", callback_data="dismiss_report")
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)
    else:
        reply_markup = None
    
    try:
        await bot.send_message(
            chat_id=admin_id,
            text=message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    except Exception as e:
        print(f"Admin hisobotda xato: {e}")


# ========== ATTENDANCE SCHEDULER ==========

async def send_attendance_request(context):
    """22:00 da sardorlarga davomat so'rovi yuborish"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    bot = context.bot
    
    async with aiosqlite.connect(db.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("SELECT * FROM floor_supervisors")
        supervisors = await cursor.fetchall()
    
    for sup in supervisors:
        floors = sup['floors'].split(',')
        keyboard = [[InlineKeyboardButton(f"{f}-qavat", callback_data=f"att_floor_{f}")] for f in floors]
        
        try:
            await bot.send_message(
                chat_id=sup['telegram_id'],
                text=f"📊 **DAVOMAT VAQTI!**\n\n"
                     f"Assalomu alaykum, {sup['name']}!\n"
                     f"Iltimos, qavatlaringiz uchun talabalar sonini kiriting.\n\n"
                     "Qavat tanlang 👇\n\n"
                     "_/davomat buyrug'ini yuboring yoki tugmani bosing_",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            print(f"Davomat so'rovi yuborishda xato: {e}")


async def send_full_attendance_report(context):
    """23:00 da admin-ga to'liq davomat hisoboti"""
    bot = context.bot
    admin_id = os.getenv('ADMIN_ID')
    
    if not admin_id:
        return
    
    today = date.today().isoformat()
    
    async with aiosqlite.connect(db.DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM attendance WHERE date = ? ORDER BY floor",
            (today,)
        )
        attendance = await cursor.fetchall()
        
        total_cursor = await conn.execute(
            "SELECT SUM(student_count) as total FROM attendance WHERE date = ?",
            (today,)
        )
        total_row = await total_cursor.fetchone()
        total = total_row['total'] or 0
    
    message = f"📊 **KUNLIK DAVOMAT HISOBOTI**\n{date.today().strftime('%d.%m.%Y')}\n\n"
    
    if attendance:
        for a in attendance:
            message += f"🏢 {a['floor']}-qavat: **{a['student_count']}** ta\n"
        
        message += f"\n────────────\n"
        message += f"📈 **JAMI:** {total} ta talaba\n"
        message += f"✅ Kiritildi: {len(attendance)}/8 qavat"
    else:
        message += "❌ Bugun davomat kiritilmagan!"
    
    try:
        await bot.send_message(
            chat_id=admin_id,
            text=message,
            parse_mode='Markdown'
        )
    except Exception as e:
        print(f"Davomat hisobotda xato: {e}")


def setup_scheduler(application):
    """Schedulerni sozlash (telegram.ext.JobQueue bilan)"""
    job_queue = application.job_queue
    
    # Har kuni 21:00 da navbatchilik xabari
    job_queue.run_daily(
        send_duty_notifications,
        time=time(hour=21, minute=0),
        name='duty_notifications'
    )
    
    # Har kuni 22:00 da davomat so'rovi
    job_queue.run_daily(
        send_attendance_request,
        time=time(hour=22, minute=0),
        name='attendance_request'
    )
    
    # Har kuni 23:00 da admin hisoboti + davomat
    job_queue.run_daily(
        send_admin_report,
        time=time(hour=23, minute=0),
        name='admin_report'
    )
    
    job_queue.run_daily(
        send_full_attendance_report,
        time=time(hour=23, minute=5),
        name='attendance_report'
    )
    
    print("✅ Scheduler o'rnatildi (21:00, 22:00, 23:00)")

