from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
import psycopg2
from werkzeug.utils import secure_filename
import os

hr_staff_contact_bp = Blueprint('hr_staff_contact', __name__)

UPLOAD_FOLDER = 'static/uploads'

@hr_staff_contact_bp.route('/hr_staff_contact', methods=['GET', 'POST'])
@login_required
def hr_staff_contact():
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()

    # Получаем список сотрудников отдела кадров с ФИО
    cur.execute("""
        SELECT u.id, p.фио, u.email
        FROM users u
        JOIN people p ON u.employee_id = p.id
        WHERE u.role = 'hr_staff'
    """)
    hr_staff = cur.fetchall()

    # Фильтрация и сортировка
    q = request.args.get('q', '').strip()
    sort = request.args.get('sort', 'date_desc')
    order_by = "m.created_at DESC"
    if sort == "date_asc":
        order_by = "m.created_at ASC"
    elif sort == "fio":
        order_by = "p.фио ASC"

    search_filter = ""
    params = [current_user.id]
    if q:
        search_filter = "AND m.message ILIKE %s"
        params.append(f"%{q}%")

    # Обработка отправки сообщения и ответа
    if request.method == 'POST':
        recipient_id = request.form['recipient_id']
        message = request.form['message']
        file = request.files.get('attachment')
        filename = None
        if file and file.filename:
            filename = secure_filename(file.filename)
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            file.save(os.path.join(UPLOAD_FOLDER, filename))
        cur.execute(
            "INSERT INTO hr_messages (sender_id, recipient_id, message, created_at, attachment, is_read) VALUES (%s, %s, %s, NOW(), %s, FALSE)",
            (current_user.id, recipient_id, message, filename)
        )
        conn.commit()
        flash('Сообщение отправлено!', 'success')
        return redirect(url_for('hr_staff_contact.hr_staff_contact'))

    # Отправленные сообщения
    cur.execute(f"""
        SELECT m.id, p.фио, m.message, m.created_at, m.is_read, m.attachment, u.id
        FROM hr_messages m
        JOIN users u ON m.recipient_id = u.id
        JOIN people p ON u.employee_id = p.id
        WHERE m.sender_id = %s {search_filter}
        ORDER BY {order_by}
    """, params)
    sent_messages = cur.fetchall()

    # Входящие сообщения (и отмечаем их как прочитанные)
    cur.execute(f"""
        SELECT m.id, p.фио, m.message, m.created_at, m.is_read, m.attachment, u.id
        FROM hr_messages m
        JOIN users u ON m.sender_id = u.id
        JOIN people p ON u.employee_id = p.id
        WHERE m.recipient_id = %s {search_filter}
        ORDER BY {order_by}
    """, params)
    inbox_messages = cur.fetchall()
    cur.execute("UPDATE hr_messages SET is_read = TRUE WHERE recipient_id = %s", (current_user.id,))
    conn.commit()

    # История обращений (все сообщения пользователя)
    cur.execute("""
        SELECT m.id, p.фио, m.message, m.created_at, m.is_read, m.attachment
        FROM hr_messages m
        JOIN users u ON (m.sender_id = u.id OR m.recipient_id = u.id)
        JOIN people p ON u.employee_id = p.id
        WHERE m.sender_id = %s OR m.recipient_id = %s
        ORDER BY m.created_at DESC
    """, (current_user.id, current_user.id))
    all_messages = cur.fetchall()

    # FAQ/документы
    faq_links = [
        ("Правила оформления отпуска", "/static/docs/otpusk.pdf"),
        ("Оформление больничного", "/static/docs/bolnichny.pdf"),
        ("Образец заявления на отпуск", "/static/docs/zayavlenie_otpusk.docx"),
    ]

    # Планы обучения и развития персонала
    cur.execute("SELECT title, description, date_start, date_end FROM training_plans ORDER BY date_start")
    training_plans = cur.fetchall()

    # Журнал задач и событий отдела кадров
    cur.execute("SELECT title, description, event_date FROM hr_events ORDER BY event_date DESC")
    hr_events = cur.fetchall()

    cur.close()
    conn.close()
    return render_template(
        'hr_staff_contact.html',
        hr_staff=hr_staff,
        sent_messages=sent_messages,
        inbox_messages=inbox_messages,
        all_messages=all_messages,
        faq_links=faq_links,
        training_plans=training_plans,
        hr_events=hr_events,
        request=request
    )