"""
Admin Panel Web Server for Talaba Bot
Simple Flask-based admin interface for push notifications
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for
import os
import sqlite3
from datetime import date
import requests

app = Flask(__name__)

# Configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
DATABASE_PATH = 'talaba.db'


def init_db_sync():
    """Initialize database with tables (sync version for Flask)"""
    conn = sqlite3.connect(DATABASE_PATH)
    
    # Qavatlar jadvali
    conn.execute("""
        CREATE TABLE IF NOT EXISTS floors (
            id INTEGER PRIMARY KEY,
            group_id TEXT,
            supervisor_id TEXT,
            supervisor_name TEXT
        )
    """)
    
    # Xonalar jadvali
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            number INTEGER PRIMARY KEY,
            floor INTEGER,
            duty_days INTEGER DEFAULT 1
        )
    """)
    
    # Navbat jadvali
    conn.execute("""
        CREATE TABLE IF NOT EXISTS duty_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            room_number INTEGER,
            floor INTEGER,
            status TEXT DEFAULT 'pending',
            confirmed_by TEXT,
            confirmed_at TEXT
        )
    """)
    
    # Jazolar jadvali
    conn.execute("""
        CREATE TABLE IF NOT EXISTS penalties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_number INTEGER,
            type TEXT,
            reason TEXT,
            start_date TEXT,
            end_date TEXT,
            issued_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Talabalar ro'yxati
    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT,
            name TEXT,
            room_number INTEGER
        )
    """)
    
    # Qavat sardorlari
    conn.execute("""
        CREATE TABLE IF NOT EXISTS floor_supervisors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT UNIQUE,
            name TEXT,
            floors TEXT
        )
    """)
    
    # Davomat jadvali
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            floor INTEGER,
            student_count INTEGER,
            submitted_by TEXT,
            submitted_at TEXT
        )
    """)
    
    # Navbat navbati (skip qilingan xonalar)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS duty_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            floor INTEGER,
            room_number INTEGER,
            reason TEXT,
            skipped_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    
    # Agar xonalar yo'q bo'lsa, yaratamiz
    cursor = conn.execute("SELECT COUNT(*) FROM rooms")
    count = cursor.fetchone()[0]
    if count == 0:
        # Qavatlarni yaratish (2-9)
        for floor_num in range(2, 10):
            conn.execute("INSERT OR IGNORE INTO floors (id) VALUES (?)", (floor_num,))
        
        # Xonalarni yaratish
        for floor_num in range(2, 10):
            for room_idx in range(1, 13):
                room_number = floor_num * 100 + room_idx
                conn.execute(
                    "INSERT OR IGNORE INTO rooms (number, floor, duty_days) VALUES (?, ?, ?)",
                    (room_number, floor_num, 1)
                )
        conn.commit()
    
    conn.close()
    print("✅ Database initialized")


# Initialize database on startup
init_db_sync()


def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def send_telegram_message(chat_id, text):
    """Send message via Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=data)
        return response.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route('/')
def index():
    """Main dashboard"""
    conn = get_db()
    
    # Get today's duties
    today = date.today().isoformat()
    duties = conn.execute(
        "SELECT * FROM duty_schedule WHERE date = ?", (today,)
    ).fetchall()
    
    # Get floors with groups
    floors = conn.execute("SELECT * FROM floors ORDER BY id").fetchall()
    
    # Get recent penalties
    penalties = conn.execute(
        "SELECT * FROM penalties ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    
    conn.close()
    
    return render_template('index.html', 
                          duties=duties, 
                          floors=floors, 
                          penalties=penalties,
                          today=date.today().strftime('%d.%m.%Y'))


@app.route('/send_notification', methods=['POST'])
def send_notification():
    """Send push notification to groups"""
    message = request.form.get('message', '')
    target = request.form.get('target', 'all')  # all, 2-3, 4-5, etc.
    
    if not message:
        return jsonify({"success": False, "error": "Xabar bo'sh!"})
    
    conn = get_db()
    
    if target == 'all':
        floors = conn.execute("SELECT DISTINCT group_id FROM floors WHERE group_id IS NOT NULL").fetchall()
    else:
        start, end = target.split('-')
        floors = conn.execute(
            "SELECT DISTINCT group_id FROM floors WHERE id BETWEEN ? AND ? AND group_id IS NOT NULL",
            (int(start), int(end))
        ).fetchall()
    
    conn.close()
    
    sent = 0
    for floor in floors:
        if floor['group_id']:
            result = send_telegram_message(floor['group_id'], message)
            if result.get('ok'):
                sent += 1
    
    return jsonify({"success": True, "sent": sent})


@app.route('/send_duty_reminder', methods=['POST'])
def send_duty_reminder():
    """Send today's duty reminder to all groups"""
    conn = get_db()
    today = date.today().isoformat()
    
    # Get groups
    groups = {
        (2, 3): None,
        (4, 5): None,
        (6, 7): None,
        (8, 9): None,
    }
    
    for floor_range in groups.keys():
        floor = conn.execute(
            "SELECT group_id FROM floors WHERE id = ?", (floor_range[0],)
        ).fetchone()
        if floor and floor['group_id']:
            groups[floor_range] = floor['group_id']
    
    sent = 0
    for floors, group_id in groups.items():
        if not group_id:
            continue
        
        message = f"🏢 **{floors[0]}-{floors[1]} QAVATLAR NAVBATCHILIGI**\n\n"
        
        for floor in floors:
            duty = conn.execute(
                "SELECT * FROM duty_schedule WHERE date = ? AND floor = ?",
                (today, floor)
            ).fetchone()
            if duty:
                status = "✅" if duty['status'] == 'completed' else "⏳"
                message += f"{status} {floor}-qavat: **{duty['room_number']}-xona**\n"
        
        message += f"\n⏰ Deadline: 22:50"
        message += f"\n✅ Bajarilgach sardorga tasdiqlating!"
        
        result = send_telegram_message(group_id, message)
        if result.get('ok'):
            sent += 1
    
    conn.close()
    return jsonify({"success": True, "sent": sent})


@app.route('/add_penalty', methods=['POST'])
def add_penalty():
    """Add penalty to a room"""
    room_number = request.form.get('room_number')
    days = request.form.get('days', 3)
    
    if not room_number:
        return jsonify({"success": False, "error": "Xona raqami kerak!"})
    
    conn = get_db()
    today = date.today().isoformat()
    
    conn.execute(
        """INSERT INTO penalties (room_number, type, reason, start_date, issued_by)
           VALUES (?, ?, ?, ?, ?)""",
        (room_number, f"{days} kun navbatchilik", "Admin panel orqali", today, "admin")
    )
    conn.commit()
    conn.close()
    
    return jsonify({"success": True})


@app.route('/api/stats')
def get_stats():
    """Get statistics"""
    conn = get_db()
    today = date.today().isoformat()
    
    total_duties = conn.execute(
        "SELECT COUNT(*) as count FROM duty_schedule WHERE date = ?", (today,)
    ).fetchone()['count']
    
    completed = conn.execute(
        "SELECT COUNT(*) as count FROM duty_schedule WHERE date = ? AND status = 'completed'", 
        (today,)
    ).fetchone()['count']
    
    penalties_count = conn.execute(
        "SELECT COUNT(*) as count FROM penalties"
    ).fetchone()['count']
    
    conn.close()
    
    return jsonify({
        "total_duties": total_duties,
        "completed": completed,
        "pending": total_duties - completed,
        "completion_rate": round(completed / total_duties * 100) if total_duties > 0 else 0,
        "total_penalties": penalties_count
    })


# ========== SARDORLAR ==========

@app.route('/sardorlar')
def sardorlar():
    """Sardorlar boshqaruvi sahifasi"""
    conn = get_db()
    supervisors = conn.execute("SELECT * FROM floor_supervisors ORDER BY id").fetchall()
    conn.close()
    return render_template('sardorlar.html', 
                          supervisors=supervisors,
                          today=date.today().strftime('%d.%m.%Y'))


@app.route('/add_supervisor', methods=['POST'])
def add_supervisor():
    """Sardor qo'shish"""
    telegram_id = request.form.get('telegram_id', '').strip()
    name = request.form.get('name', '').strip()
    floors = request.form.get('floors', '').strip()
    
    if not all([telegram_id, name, floors]):
        return jsonify({"success": False, "error": "Barcha maydonlarni to'ldiring!"})
    
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO floor_supervisors (telegram_id, name, floors) VALUES (?, ?, ?)",
            (telegram_id, name, floors)
        )
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "error": str(e)})
    
    conn.close()
    return jsonify({"success": True})


@app.route('/delete_supervisor/<int:supervisor_id>', methods=['POST'])
def delete_supervisor(supervisor_id):
    """Sardorni o'chirish"""
    conn = get_db()
    conn.execute("DELETE FROM floor_supervisors WHERE id = ?", (supervisor_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ========== DAVOMAT ==========

@app.route('/davomat')
def davomat():
    """Davomat sahifasi"""
    conn = get_db()
    today = date.today().isoformat()
    
    attendance = conn.execute(
        "SELECT * FROM attendance WHERE date = ? ORDER BY floor", (today,)
    ).fetchall()
    
    # Jami son
    total = conn.execute(
        "SELECT SUM(student_count) as total FROM attendance WHERE date = ?", (today,)
    ).fetchone()['total'] or 0
    
    conn.close()
    return render_template('davomat.html', 
                          attendance=attendance,
                          total=total,
                          today=date.today().strftime('%d.%m.%Y'))


@app.route('/api/attendance')
def api_attendance():
    """Davomat API"""
    conn = get_db()
    today = date.today().isoformat()
    
    attendance = conn.execute(
        "SELECT * FROM attendance WHERE date = ? ORDER BY floor", (today,)
    ).fetchall()
    
    total = conn.execute(
        "SELECT SUM(student_count) as total FROM attendance WHERE date = ?", (today,)
    ).fetchone()['total'] or 0
    
    conn.close()
    
    return jsonify({
        "attendance": [dict(a) for a in attendance],
        "total": total,
        "floors_submitted": len(attendance),
        "floors_total": 8
    })


# ========== GURUHLAR ==========

@app.route('/guruhlar')
def guruhlar():
    """Guruhlarni boshqarish sahifasi"""
    conn = get_db()
    
    # Guruhlar ro'yxati
    groups = []
    floor_ranges = [(2, 3), (4, 5), (6, 7), (8, 9)]
    
    for start, end in floor_ranges:
        # Birinchi qavatning group_id sini olish
        floor = conn.execute(
            "SELECT group_id FROM floors WHERE id = ?", (start,)
        ).fetchone()
        
        groups.append({
            'floors': f'{start}-{end}',
            'floor_start': start,
            'floor_end': end,
            'group_id': floor['group_id'] if floor else None
        })
    
    conn.close()
    return render_template('guruhlar.html', 
                          groups=groups,
                          today=date.today().strftime('%d.%m.%Y'))


@app.route('/save_group', methods=['POST'])
def save_group():
    """Guruh ID saqlash"""
    floors = request.form.get('floors', '')
    group_id = request.form.get('group_id', '').strip()
    
    if not floors:
        return jsonify({"success": False, "error": "Qavatlar ko'rsatilmagan!"})
    
    start, end = floors.split('-')
    
    conn = get_db()
    for floor in range(int(start), int(end) + 1):
        conn.execute(
            "UPDATE floors SET group_id = ? WHERE id = ?",
            (group_id if group_id else None, floor)
        )
    conn.commit()
    conn.close()
    
    return jsonify({"success": True})


@app.route('/send_test_message', methods=['POST'])
def send_test_message():
    """Test xabarini guruhlarga yuborish"""
    conn = get_db()
    
    floor_ranges = [(2, 3), (4, 5), (6, 7), (8, 9)]
    sent = 0
    
    for start, end in floor_ranges:
        floor = conn.execute(
            "SELECT group_id FROM floors WHERE id = ?", (start,)
        ).fetchone()
        
        if floor and floor['group_id']:
            message = f"🧪 **TEST XABARI**\n\n✅ {start}-{end} qavatlar guruhi muvaffaqiyatli ulangan!"
            result = send_telegram_message(floor['group_id'], message)
            if result.get('ok'):
                sent += 1
    
    conn.close()
    return jsonify({"success": True, "sent": sent})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
