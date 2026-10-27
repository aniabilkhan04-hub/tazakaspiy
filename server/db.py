# Подключение к базе данных SQLite и функции для работы с ней.
# SQLite используется вместо PostgreSQL/PostGIS для простоты запуска —
# не требует установки отдельного сервера БД.

import os
import sqlite3
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'eco.db')
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')

# Время из SQLite хранится в UTC без указания пояса; отдаём его в ISO-формате с «Z»,
# чтобы браузер корректно перевёл его в местное время.
ISO_CREATED_AT = "strftime('%Y-%m-%dT%H:%M:%SZ', reports.created_at) AS created_at"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id TEXT UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    rating INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('dump','cleaned','recycling','shop','tree')),
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    description TEXT,
    photo_path TEXT,
    user_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_type ON reports(type);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id, created_at);
"""


def get_conn():
    # timeout: сайт и бот пишут в базу из разных потоков — ждём, а не падаем с «database is locked»
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    conn = get_conn()
    conn.execute('PRAGMA journal_mode = WAL')
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def delete_upload(photo_path):
    """Удаляет файл фото из uploads/ по пути вида /uploads/photo_1_2.jpg."""
    if not photo_path:
        return
    name = os.path.basename(photo_path)
    try:
        os.remove(os.path.join(UPLOADS_DIR, name))
    except OSError:
        pass


def cleanup_orphan_uploads(min_age_seconds=24 * 3600):
    """Удаляет фото, на которые не ссылается ни одна заявка (например, после отмены в боте)."""
    if not os.path.isdir(UPLOADS_DIR):
        return 0
    conn = get_conn()
    try:
        used = {
            os.path.basename(r['photo_path'])
            for r in conn.execute('SELECT photo_path FROM reports WHERE photo_path IS NOT NULL')
        }
    finally:
        conn.close()
    removed = 0
    now = time.time()
    for name in os.listdir(UPLOADS_DIR):
        if name == '.gitkeep' or name in used:
            continue
        path = os.path.join(UPLOADS_DIR, name)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > min_age_seconds:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


def get_or_create_user(telegram_id, username=None, first_name=None):
    telegram_id = str(telegram_id)
    conn = get_conn()
    try:
        row = conn.execute(
            'SELECT * FROM users WHERE telegram_id = ?', (telegram_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                'INSERT OR IGNORE INTO users (telegram_id, username, first_name) VALUES (?, ?, ?)',
                (telegram_id, username, first_name or 'Волонтёр'),
            )
            conn.commit()
            row = conn.execute(
                'SELECT * FROM users WHERE telegram_id = ?', (telegram_id,)
            ).fetchone()
        return dict(row)
    finally:
        conn.close()


def count_recent_reports(user_id, hours=24):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM reports "
            "WHERE user_id = ? AND created_at >= datetime('now', ?)",
            (user_id, f'-{int(hours)} hours'),
        ).fetchone()
        return row['n']
    finally:
        conn.close()


def insert_report(type_, lat, lon, description, photo_path, user_id):
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO reports (type, lat, lon, description, photo_path, user_id, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (type_, lat, lon, description, photo_path, user_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_approved_reports(type_=None, limit=2000):
    conn = get_conn()
    try:
        query = f"""
            SELECT reports.*, {ISO_CREATED_AT},
                   users.first_name AS author_name, users.username AS author_username
            FROM reports LEFT JOIN users ON reports.user_id = users.id
            WHERE reports.status = 'approved'
        """
        params = []
        if type_:
            query += ' AND reports.type = ?'
            params.append(type_)
        query += ' ORDER BY reports.created_at DESC LIMIT ?'
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pending_reports():
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""SELECT reports.*, {ISO_CREATED_AT},
                       users.first_name AS author_name, users.username AS author_username
                FROM reports LEFT JOIN users ON reports.user_id = users.id
                WHERE reports.status = 'pending' ORDER BY reports.created_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _moderate(report_id, new_status):
    """Переводит заявку из pending в approved/rejected.

    Возвращает 'ok', 'not_found' или 'not_pending' (заявка уже обработана).
    Смена статуса — одним атомарным UPDATE, поэтому двойной клик не начислит рейтинг дважды.
    """
    conn = get_conn()
    try:
        report = conn.execute('SELECT * FROM reports WHERE id = ?', (report_id,)).fetchone()
        if report is None:
            return 'not_found', None
        cur = conn.execute(
            'UPDATE reports SET status = ? WHERE id = ? AND status = ?',
            (new_status, report_id, 'pending'),
        )
        if cur.rowcount == 0:
            return 'not_pending', None
        if new_status == 'approved' and report['type'] == 'cleaned' and report['user_id']:
            conn.execute(
                'UPDATE users SET rating = rating + 1 WHERE id = ?', (report['user_id'],)
            )
        conn.commit()
        return 'ok', report
    finally:
        conn.close()


def approve_report(report_id):
    result, _ = _moderate(report_id, 'approved')
    return result


def reject_report(report_id):
    result, report = _moderate(report_id, 'rejected')
    if result == 'ok':
        delete_upload(report['photo_path'])
    return result


def delete_report(report_id):
    """Полностью удаляет заявку (и фото). Для одобренной «Я убрал мусор» откатывает рейтинг автора."""
    conn = get_conn()
    try:
        report = conn.execute('SELECT * FROM reports WHERE id = ?', (report_id,)).fetchone()
        if report is None:
            return 'not_found'
        conn.execute('DELETE FROM reports WHERE id = ?', (report_id,))
        if report['status'] == 'approved' and report['type'] == 'cleaned' and report['user_id']:
            conn.execute(
                'UPDATE users SET rating = MAX(rating - 1, 0) WHERE id = ?', (report['user_id'],)
            )
        conn.commit()
    finally:
        conn.close()
    delete_upload(report['photo_path'])
    return 'ok'


def get_leaderboard():
    conn = get_conn()
    try:
        rows = conn.execute(
            'SELECT first_name, username, rating FROM users WHERE rating > 0 ORDER BY rating DESC LIMIT 50'
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
