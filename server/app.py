# Flask-сервер: отдаёт сайт (public/) и REST API для карты, модерации и рейтинга.

import hmac
import os
import time
from collections import defaultdict

from flask import Flask, jsonify, request, send_from_directory

from db import (
    get_approved_reports,
    get_pending_reports,
    approve_report,
    reject_report,
    get_leaderboard,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_DIR = os.path.join(BASE_DIR, 'public')
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')

app = Flask(__name__, static_folder=PUBLIC_DIR, static_url_path='')

# Пароль задаётся только через .env. Если он пустой — модерация отключена.
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')

# Защита от подбора пароля: не больше MAX_FAILS неудачных попыток с одного IP за WINDOW секунд
MAX_FAILS = 5
WINDOW = 300
_fails = defaultdict(list)


def _client_ip():
    # За прокси (Netlify, Render) настоящий адрес приходит в X-Forwarded-For
    fwd = request.headers.get('X-Forwarded-For', '')
    return fwd.split(',')[0].strip() if fwd else (request.remote_addr or '')


def check_admin():
    """Возвращает None, если доступ разрешён, иначе готовый ответ с ошибкой."""
    if not ADMIN_PASSWORD:
        return jsonify({'error': 'Модерация отключена: задайте ADMIN_PASSWORD в .env'}), 503

    ip = _client_ip()
    now = time.time()
    _fails[ip] = [t for t in _fails[ip] if now - t < WINDOW]
    if len(_fails[ip]) >= MAX_FAILS:
        return jsonify({'error': 'Слишком много попыток. Подождите несколько минут.'}), 429

    # Пароль принимается только из заголовка (а не из URL — там он попадает в логи и историю)
    pw = request.headers.get('X-Admin-Password', '')
    if not hmac.compare_digest(pw.encode('utf-8'), ADMIN_PASSWORD.encode('utf-8')):
        _fails[ip].append(now)
        return jsonify({'error': 'Неверный пароль администратора'}), 401
    return None


def moderation_response(result):
    if result == 'ok':
        return jsonify({'ok': True})
    if result == 'not_found':
        return jsonify({'error': 'Заявка не найдена'}), 404
    return jsonify({'error': 'Заявка уже обработана'}), 409


# ---------- Страницы сайта ----------

@app.route('/')
def index():
    return send_from_directory(PUBLIC_DIR, 'index.html')


@app.route('/admin.html')
def admin_page():
    return send_from_directory(PUBLIC_DIR, 'admin.html')


@app.route('/uploads/<path:filename>')
def uploads(filename):
    return send_from_directory(UPLOADS_DIR, filename)


@app.route('/healthz')
def healthz():
    # Для внешних «пингов», чтобы бесплатный хостинг не усыплял сервис
    return 'ok'


# ---------- Публичные API ----------

@app.route('/api/config')
def api_config():
    return jsonify({'bot_username': os.environ.get('BOT_USERNAME', '').lstrip('@')})


@app.route('/api/reports')
def api_reports():
    type_ = request.args.get('type') or None
    return jsonify(get_approved_reports(type_))


@app.route('/api/leaderboard')
def api_leaderboard():
    return jsonify(get_leaderboard())


# ---------- Административные (модерация) API ----------

@app.route('/api/admin/pending')
def api_admin_pending():
    denied = check_admin()
    if denied:
        return denied
    return jsonify(get_pending_reports())


@app.route('/api/admin/reports/<int:report_id>/approve', methods=['POST'])
def api_admin_approve(report_id):
    denied = check_admin()
    if denied:
        return denied
    return moderation_response(approve_report(report_id))


@app.route('/api/admin/reports/<int:report_id>/reject', methods=['POST'])
def api_admin_reject(report_id):
    denied = check_admin()
    if denied:
        return denied
    return moderation_response(reject_report(report_id))
