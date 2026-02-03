"""
Database module for Talaba Bot
SQLite database for storing rooms, schedules, and penalties
"""

import aiosqlite
import os
from datetime import datetime, date

DATABASE_PATH = "talaba.db"


async def init_db():
    """Initialize database with tables"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Qavatlar jadvali
        await db.execute("""
            CREATE TABLE IF NOT EXISTS floors (
                id INTEGER PRIMARY KEY,
                group_id TEXT,
                supervisor_id TEXT,
                supervisor_name TEXT
            )
        """)
        
        # Xonalar jadvali
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                number INTEGER PRIMARY KEY,
                floor INTEGER,
                duty_days INTEGER DEFAULT 1,
                FOREIGN KEY (floor) REFERENCES floors(id)
            )
        """)
        
        # Navbat jadvali
        await db.execute("""
            CREATE TABLE IF NOT EXISTS duty_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                room_number INTEGER,
                floor INTEGER,
                status TEXT DEFAULT 'pending',
                confirmed_by TEXT,
                confirmed_at TEXT,
                FOREIGN KEY (room_number) REFERENCES rooms(number)
            )
        """)
        
        # Jazolar jadvali
        await db.execute("""
            CREATE TABLE IF NOT EXISTS penalties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_number INTEGER,
                type TEXT,
                reason TEXT,
                start_date TEXT,
                end_date TEXT,
                issued_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (room_number) REFERENCES rooms(number)
            )
        """)
        
        # Talabalar ro'yxati (ixtiyoriy)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT,
                name TEXT,
                room_number INTEGER,
                FOREIGN KEY (room_number) REFERENCES rooms(number)
            )
        """)
        
        # Qavat sardorlari (davomat uchun)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS floor_supervisors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT UNIQUE,
                name TEXT,
                floors TEXT
            )
        """)
        
        # Davomat jadvali
        await db.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                floor INTEGER,
                student_count INTEGER,
                notes TEXT,
                submitted_by TEXT,
                submitted_at TEXT
            )
        """)
        
        # Navbat navbati (skip qilingan xonalar)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS duty_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                floor INTEGER,
                room_number INTEGER,
                reason TEXT,
                skipped_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await db.commit()
        
        # Agar xonalar yo'q bo'lsa, yaratamiz
        cursor = await db.execute("SELECT COUNT(*) FROM rooms")
        count = await cursor.fetchone()
        if count[0] == 0:
            await seed_data(db)


async def seed_data(db):
    """Ma'lumotlarni boshlang'ich holatga keltirish"""
    # Qavatlarni yaratish (2-9)
    for floor_num in range(2, 10):
        await db.execute(
            "INSERT OR IGNORE INTO floors (id) VALUES (?)",
            (floor_num,)
        )
    
    # Xonalarni yaratish
    for floor_num in range(2, 10):
        for room_idx in range(1, 13):  # 1-12 xonalar
            room_number = floor_num * 100 + room_idx
            # Barcha xonalar 1 kun navbatchilik (12 kunlik davr)
            duty_days = 1
            await db.execute(
                "INSERT OR IGNORE INTO rooms (number, floor, duty_days) VALUES (?, ?, ?)",
                (room_number, floor_num, duty_days)
            )
    
    await db.commit()


# ========== CRUD Operations ==========

async def get_today_duty(floor: int) -> dict:
    """Bugungi navbatchi xonani olish"""
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM duty_schedule WHERE date = ? AND floor = ?",
            (today, floor)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_today_duties() -> list:
    """Barcha qavatlarning bugungi navbatchilari"""
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM duty_schedule WHERE date = ?",
            (today,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def confirm_duty(room_number: int, confirmed_by: str) -> bool:
    """Navbatchilikni tasdiqlash"""
    today = date.today().isoformat()
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """UPDATE duty_schedule 
               SET status = 'completed', confirmed_by = ?, confirmed_at = ?
               WHERE date = ? AND room_number = ?""",
            (confirmed_by, now, today, room_number)
        )
        await db.commit()
        return True


async def get_floor_rooms(floor: int) -> list:
    """Qavatdagi barcha xonalar"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM rooms WHERE floor = ? ORDER BY number",
            (floor,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_pending_duties() -> list:
    """Bajarilmagan navbatchiliklar"""
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM duty_schedule WHERE date = ? AND status = 'pending'",
            (today,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def add_penalty(room_number: int, penalty_type: str, reason: str, 
                     days: int, issued_by: str):
    """Jazo qo'shish"""
    today = date.today()
    end_date = date(today.year, today.month, today.day + days)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO penalties (room_number, type, reason, start_date, end_date, issued_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (room_number, penalty_type, reason, today.isoformat(), 
             end_date.isoformat(), issued_by)
        )
        await db.commit()


async def set_floor_group(floor: int, group_id: str):
    """Qavat guruh IDsini o'rnatish"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE floors SET group_id = ? WHERE id = ?",
            (group_id, floor)
        )
        await db.commit()


async def set_floor_supervisor(floor: int, supervisor_id: str, name: str):
    """Qavat sardorini o'rnatish"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE floors SET supervisor_id = ?, supervisor_name = ? WHERE id = ?",
            (supervisor_id, name, floor)
        )
        await db.commit()


async def get_floor_info(floor: int) -> dict:
    """Qavat ma'lumotlari"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM floors WHERE id = ?",
            (floor,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


# ========== Floor Supervisors (Sardorlar) ==========

async def add_floor_supervisor(telegram_id: str, name: str, floors: str):
    """Sardor qo'shish"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO floor_supervisors (telegram_id, name, floors)
               VALUES (?, ?, ?)""",
            (telegram_id, name, floors)
        )
        await db.commit()


async def get_all_floor_supervisors() -> list:
    """Barcha sardorlar"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM floor_supervisors ORDER BY id")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_floor_supervisor_by_telegram(telegram_id: str) -> dict:
    """Telegram ID bo'yicha sardorni olish"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM floor_supervisors WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_floor_supervisor(supervisor_id: int):
    """Sardorni o'chirish"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM floor_supervisors WHERE id = ?", (supervisor_id,))
        await db.commit()


# ========== Attendance (Davomat) ==========

async def save_attendance(floor: int, student_count: int, submitted_by: str, notes: str = None):
    """Davomatni saqlash"""
    today = date.today().isoformat()
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Avval mavjudini tekshirish
        existing = await db.execute(
            "SELECT id FROM attendance WHERE date = ? AND floor = ?",
            (today, floor)
        )
        if await existing.fetchone():
            # Yangilash
            await db.execute(
                """UPDATE attendance SET student_count = ?, notes = ?, submitted_by = ?, submitted_at = ?
                   WHERE date = ? AND floor = ?""",
                (student_count, notes, submitted_by, now, today, floor)
            )
        else:
            # Yangi qo'shish
            await db.execute(
                """INSERT INTO attendance (date, floor, student_count, notes, submitted_by, submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (today, floor, student_count, notes, submitted_by, now)
            )
        await db.commit()


async def get_today_attendance() -> list:
    """Bugungi davomat"""
    today = date.today().isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM attendance WHERE date = ? ORDER BY floor",
            (today,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_attendance_by_date(target_date: str) -> list:
    """Berilgan sanadagi davomat"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM attendance WHERE date = ? ORDER BY floor",
            (target_date,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_floor_attendance_for_date(floor: int, target_date: str) -> dict:
    """Ma'lum qavat uchun berilgan sanadagi davomat"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM attendance WHERE date = ? AND floor = ?",
            (target_date, floor)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


# ========== Duty Queue (Skip) ==========

async def skip_duty_room(floor: int, room_number: int, reason: str, skipped_by: str):
    """Xonani o'tkazish va navbatga qo'shish"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO duty_queue (floor, room_number, reason, skipped_by)
               VALUES (?, ?, ?, ?)""",
            (floor, room_number, reason, skipped_by)
        )
        await db.commit()


async def get_queued_room(floor: int) -> dict:
    """Navbatdagi birinchi xonani olish (FIFO)"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM duty_queue WHERE floor = ? ORDER BY id LIMIT 1",
            (floor,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def clear_duty_queue(floor: int, room_number: int):
    """Xonani navbatdan o'chirish (bajarilgandan keyin)"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "DELETE FROM duty_queue WHERE floor = ? AND room_number = ? ORDER BY id LIMIT 1",
            (floor, room_number)
        )
        await db.commit()


async def get_all_queued_rooms() -> list:
    """Barcha navbatdagi xonalar"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM duty_queue ORDER BY floor, id")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_next_room_in_sequence(floor: int, current_room: int) -> int:
    """Keyingi xona raqamini olish"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT number FROM rooms WHERE floor = ? ORDER BY number",
            (floor,)
        )
        rooms = await cursor.fetchall()
        room_numbers = [r[0] for r in rooms]
        
        if current_room in room_numbers:
            idx = room_numbers.index(current_room)
            next_idx = (idx + 1) % len(room_numbers)
            return room_numbers[next_idx]
        return room_numbers[0] if room_numbers else None
