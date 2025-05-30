from flask import Flask, abort, render_template, request, redirect, url_for, flash, Response, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
import psycopg2
from datetime import date, datetime, timedelta
import io
from functools import wraps
from openpyxl import Workbook
from flask import send_file
from blueprints.hr_staff_contact import hr_staff_contact_bp



app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.permanent_session_lifetime = timedelta(minutes=30)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, username, employee_id, role):
        self.id = id
        self.username = username
        self.employee_id = employee_id
        self.role = role

@login_manager.user_loader
def load_user(user_id):
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("SELECT id, username, employee_id, role FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    if user:
        return User(user[0], user[1], user[2], user[3])
    return None

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = psycopg2.connect(
            dbname="hr_management", user="postgres", password="postgres", host="localhost"
        )
        cur = conn.cursor()
        cur.execute("SELECT id, username, password, employee_id, role FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        if user and user[2] == password:
            login_user(User(user[0], user[1], user[3], user[4]), remember=False)
            # Перенаправление по роли
            if user[4] == 'employee':
                return redirect(url_for('employee_dashboard'))
            elif user[4] == 'hr_manager':
                return redirect(url_for('index'))
            elif user[4] == 'hr_staff':
                return redirect(url_for('hr_staff_dashboard'))
            elif user[4] == 'superadmin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('index'))
        else:
            flash('Неверный логин или пароль', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

def get_monthly_stats():
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("""
        WITH months AS (
            SELECT generate_series(1, 12) AS month
        )
        SELECT
            m.month,
            COALESCE(COUNT(t.id), 0) AS total_tasks,
            COALESCE(SUM(CASE WHEN t.is_done THEN 1 ELSE 0 END), 0) AS completed_tasks
        FROM months m
        LEFT JOIN task t
            ON EXTRACT(MONTH FROM t.due_date) = m.month
            AND EXTRACT(YEAR FROM t.due_date) = %s
        GROUP BY m.month
        ORDER BY m.month
    """, (date.today().year,))
    stats = cur.fetchall()
    cur.close()
    conn.close()
    months = [row[1] for row in stats]
    completed = [row[2] for row in stats]
    return months, completed

@app.route('/')
@login_required
@role_required('hr_manager', 'superadmin')
def index():
    months, completed = get_monthly_stats()
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM people")
    total_employees = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM people WHERE статус = 'в отпуске'")
    on_vacation = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM people WHERE статус = 'на месте'")
    at_work = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM task WHERE employee_id = %s", (current_user.employee_id,))
    total_tasks = cur.fetchone()[0]
    cur.execute("""
        SELECT due_date::date, title, description
        FROM task
        WHERE employee_id = %s
    """, (current_user.employee_id,))
    tasks = cur.fetchall()
    cur.execute("SELECT id, название, контактный_номер, фио_руководителя FROM departament ORDER BY id")
    departments = cur.fetchall()
    cur.close()
    conn.close()
    tasks_by_date = {}
    for due_date, title, description in tasks:
        key = due_date.strftime('%Y-%m-%d')
        tasks_by_date.setdefault(key, []).append({'title': title, 'description': description})
    return render_template(
        'index.html',
        months=months,
        completed=completed,
        total_employees=total_employees,
        on_vacation=on_vacation,
        at_work=at_work,
        total_tasks=total_tasks,
        tasks_by_date=tasks_by_date,
        departments=departments
    )

def get_departments():
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("SELECT id, название FROM departament ORDER BY название")
    departments = cur.fetchall()
    cur.close()
    conn.close()
    return departments

def get_employees(search=None, sort=None, order='asc', status=None, department_id=None):
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    query = """
        SELECT p.id, p.фио, d.название as отдел, p.должность, p.дата_приема, p.отработанные_часы,
               p.контактный_номер, p.электронная_почта, p.статус
        FROM people p
        LEFT JOIN departament d ON p.отдел_id = d.id
    """
    params = []
    where = []
    if search:
        where.append("LOWER(p.фио) LIKE %s")
        params.append(f"%{search.lower()}%")
    if status:
        where.append("p.статус = %s")
        params.append(status)
    if department_id:
        where.append("p.отдел_id = %s")
        params.append(department_id)
    if where:
        query += " WHERE " + " AND ".join(where)
    sort_fields = {
        'дата_приема': 'p.дата_приема',
        'отдел': 'd.название',
        'отработанные_часы': 'p.отработанные_часы',
        'статус': 'p.статус'
    }
    if sort in sort_fields:
        query += f" ORDER BY {sort_fields[sort]} {'DESC' if order == 'desc' else 'ASC'}"
    else:
        query += " ORDER BY p.id ASC"
    cur.execute(query, params)
    employees = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    cur.close()
    conn.close()
    return employees, columns

@app.route('/employees')
@login_required
def employees():
    search = request.args.get('search', '')
    sort = request.args.get('sort')
    order = request.args.get('order', 'asc')
    status = request.args.get('status')
    department_id = request.args.get('department_id')
    employees, columns = get_employees(search, sort, order, status, department_id)
    departments = get_departments()
    STATUSES = ['на месте', 'в отпуске', 'на больничном']
    return render_template(
        'employees.html',
        employees=employees,
        columns=columns,
        departments=departments,
        statuses=STATUSES,
        selected_department=department_id,
        selected_status=status
    )

@app.route('/employees/<int:id>')
@login_required
def employee_detail(id):
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.фио, d.название as отдел, p.должность, p.дата_приема, p.отработанные_часы,
               p.контактный_номер, p.электронная_почта, p.статус
        FROM people p
        LEFT JOIN departament d ON p.отдел_id = d.id
        WHERE p.id = %s
    """, (id,))
    employee = cur.fetchone()
    cur.close()
    conn.close()
    return render_template('employee_detail.html', employee=employee)

@app.route('/employees/add', methods=['GET', 'POST'])
@login_required
def add_employee():
    if request.method == 'POST':
        fio = request.form['фио']
        email = request.form['электронная_почта']
        conn = psycopg2.connect(
            dbname="hr_management", user="postgres", password="postgres", host="localhost"
        )
        cur = conn.cursor()
        cur.execute("SELECT id FROM people WHERE фио=%s OR электронная_почта=%s", (fio, email))
        exists = cur.fetchone()
        if exists:
            cur.close()
            conn.close()
            flash('Сотрудник с таким ФИО или электронной почтой уже существует!', 'error')
            return redirect(url_for('add_employee'))
        data = (
            fio,
            request.form['отдел_id'],
            request.form['должность'],
            request.form['дата_приема'],
            request.form['отработанные_часы'],
            request.form['контактный_номер'],
            email,
            request.form['статус']
        )
        try:
            cur.execute(
                "INSERT INTO people (фио, отдел_id, должность, дата_приема, отработанные_часы, контактный_номер, электронная_почта, статус) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                data
            )
            conn.commit()
            flash('Сотрудник успешно добавлен!', 'success')
        except Exception as e:
            flash('Ошибка при добавлении сотрудника: ' + str(e), 'error')
        cur.close()
        conn.close()
        return redirect(url_for('employees'))
    departments = get_departments()
    return render_template('add_employee.html', departments=departments)

@app.route('/employees/delete/<int:id>', methods=['POST'])
@login_required
def delete_employee(id):
    try:
        conn = psycopg2.connect(
            dbname="hr_management", user="postgres", password="postgres", host="localhost"
        )
        cur = conn.cursor()
        cur.execute("DELETE FROM people WHERE id = %s", (id,))
        conn.commit()
        cur.close()
        conn.close()
        flash('Сотрудник удалён.', 'success')
    except Exception as e:
        flash('Ошибка при удалении: ' + str(e), 'error')
    return redirect(url_for('employees'))

@app.route('/employees/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_employee(id):
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    if request.method == 'POST':
        fio = request.form['фио']
        email = request.form['электронная_почта']
        cur.execute("SELECT id FROM people WHERE (фио=%s OR электронная_почта=%s) AND id<>%s", (fio, email, id))
        exists = cur.fetchone()
        if exists:
            flash('Сотрудник с таким ФИО или электронной почтой уже существует!', 'error')
        else:
            data = (
                fio,
                request.form['отдел_id'],
                request.form['должность'],
                request.form['дата_приема'],
                request.form['отработанные_часы'],
                request.form['контактный_номер'],
                email,
                request.form['статус'],
                id
            )
            try:
                cur.execute(
                    "UPDATE people SET фио=%s, отдел_id=%s, должность=%s, дата_приема=%s, отработанные_часы=%s, контактный_номер=%s, электронная_почта=%s, статус=%s WHERE id=%s",
                    data
                )
                conn.commit()
                flash('Данные сотрудника обновлены.', 'success')
            except Exception as e:
                flash('Ошибка при обновлении: ' + str(e), 'error')
        cur.close()
        conn.close()
        return redirect(url_for('employees'))
    else:
        cur.execute("""
            SELECT p.id, p.фио, p.отдел_id, p.должность, p.дата_приема, p.отработанные_часы,
                   p.контактный_номер, p.электронная_почта, p.статус
            FROM people p
            WHERE p.id = %s
        """, (id,))
        employee = cur.fetchone()
        departments = get_departments()
        cur.close()
        conn.close()
        return render_template('edit_employee.html', employee=employee, departments=departments)

@app.route('/employees/<int:employee_id>/tasks', methods=['GET', 'POST'])
@login_required
def employee_tasks(employee_id):
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    if request.method == 'POST':
        task_id = request.form.get('task_id')
        cur.execute("UPDATE task SET is_done = TRUE WHERE id = %s AND employee_id = %s", (task_id, employee_id))
        conn.commit()
    cur.execute("SELECT id, title, description, due_date, is_done FROM task WHERE employee_id = %s ORDER BY is_done, due_date", (employee_id,))
    tasks = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('tasks.html', tasks=tasks, employee_id=employee_id, today=date.today())

@app.route('/employees/<int:employee_id>/tasks/add', methods=['GET', 'POST'])
@login_required
def add_task(employee_id):
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        due_date = request.form['due_date']
        conn = psycopg2.connect(
            dbname="hr_management", user="postgres", password="postgres", host="localhost"
        )
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO task (employee_id, title, description, due_date) VALUES (%s, %s, %s, %s)",
            (employee_id, title, description, due_date)
        )
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('employee_tasks', employee_id=employee_id))
    return render_template('add_task.html', employee_id=employee_id)

@app.route('/tasks', methods=['GET', 'POST'])
@login_required
def all_tasks():
    employee_id = current_user.employee_id
    if not employee_id:
        flash('У вас нет привязанных задач.', 'error')
        return redirect(url_for('index'))
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    if request.method == 'POST':
        task_id = request.form.get('task_id')
        cur.execute("UPDATE task SET is_done = TRUE WHERE id = %s AND employee_id = %s", (task_id, employee_id))
        conn.commit()
    cur.execute("SELECT id, title, description, due_date, is_done FROM task WHERE employee_id = %s ORDER BY is_done, due_date", (employee_id,))
    tasks = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('tasks.html', tasks=tasks, employee_id=employee_id, today=date.today())

@app.route('/recruiting')
@login_required
def recruiting():
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    # Открытые вакансии
    cur.execute("""
        SELECT v.id, v.title, d.название as department, v.status
        FROM vacancy v
        LEFT JOIN departament d ON v.department_id = d.id
        WHERE v.status = 'открыта'
        ORDER BY v.id DESC
    """)
    vacancies = cur.fetchall()
    # Закрытые вакансии
    cur.execute("""
        SELECT v.id, v.title, d.название as department, v.status
        FROM vacancy v
        LEFT JOIN departament d ON v.department_id = d.id
        WHERE v.status = 'закрыта'
        ORDER BY v.id DESC
    """)
    closed_vacancies = cur.fetchall()
    # Кандидаты
    cur.execute("""
        SELECT c.id, c.fio, c.status, v.title as vacancy
        FROM candidate c
        LEFT JOIN vacancy v ON c.vacancy_id = v.id
        ORDER BY c.id DESC
    """)
    candidates = cur.fetchall()
    # База резюме
    cur.execute("SELECT fio, file_path FROM resume_base ORDER BY id DESC")
    resumes = cur.fetchall()
    cur.close()
    conn.close()
    return render_template(
        'recruiting.html',
        vacancies=vacancies,
        closed_vacancies=closed_vacancies,
        candidates=candidates,
        resumes=resumes
    )

@app.route('/vacancy/<int:vacancy_id>')
@login_required
def vacancy_detail(vacancy_id):
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT v.id, v.title, v.description, v.status, d.название as department
        FROM vacancy v
        LEFT JOIN departament d ON v.department_id = d.id
        WHERE v.id = %s
    """, (vacancy_id,))
    vacancy = cur.fetchone()
    cur.close()
    conn.close()
    if not vacancy:
        flash('Вакансия не найдена', 'error')
        return redirect(url_for('recruiting'))
    return render_template('vacancy_detail.html', vacancy=vacancy)

@app.route('/vacancy/<int:vacancy_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_vacancy(vacancy_id):
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        status = request.form['status']
        department_id = request.form['department_id']
        cur.execute("""
            UPDATE vacancy
            SET title=%s, description=%s, status=%s, department_id=%s
            WHERE id=%s
        """, (title, description, status, department_id, vacancy_id))
        conn.commit()
        cur.close()
        conn.close()
        flash('Вакансия обновлена.', 'success')
        return redirect(url_for('vacancy_detail', vacancy_id=vacancy_id))
    else:
        cur.execute("""
            SELECT v.id, v.title, v.description, v.status, v.department_id, d.название
            FROM vacancy v
            LEFT JOIN departament d ON v.department_id = d.id
            WHERE v.id = %s
        """, (vacancy_id,))
        vacancy = cur.fetchone()
        cur.execute("SELECT id, название FROM departament ORDER BY название")
        departments = cur.fetchall()
        cur.close()
        conn.close()
        if not vacancy:
            flash('Вакансия не найдена', 'error')
            return redirect(url_for('recruiting'))
        return render_template('edit_vacancy.html', vacancy=vacancy, departments=departments)

@app.route('/company')
@login_required
def company():
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    # Общая информация
    cur.execute("SELECT юридический_адрес, фактический_адрес, инн, огрн, руководитель_компании, контактный_номер, электронная_почта, сайт FROM информация_о_компании LIMIT 1")
    company_info = cur.fetchone()
    # Важные даты
    cur.execute("""
        SELECT vd.тип, vd.описание, vd.дата, p.фио
        FROM важные_даты vd
        LEFT JOIN people p ON vd.сотрудник_id = p.id
        ORDER BY vd.дата DESC
    """)
    important_dates = cur.fetchall()
    # Форматируем дату в строку
    important_dates_fmt = []
    for d in important_dates:
        if isinstance(d[2], (datetime, date)):
            date_str = d[2].strftime('%d.%m.%Y')
        else:
            date_str = str(d[2])
        important_dates_fmt.append((d[0], d[1], date_str, d[3]))
    # Политики и документы (пример: таблица company_docs)
    cur.execute("""
        SELECT название, file_path
        FROM company_docs
        ORDER BY id
    """)
    docs = cur.fetchall()
    cur.close()
    conn.close()
    return render_template(
        'company.html',
        company_info=company_info,
        important_dates=important_dates_fmt,
        docs=docs
    )

@app.route('/profile')
@login_required
def profile():
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    # Получаем данные из таблицы people по employee_id пользователя
    cur.execute("""
        SELECT фио, должность, электронная_почта, контактный_номер
        FROM people
        WHERE id = %s
    """, (current_user.employee_id,))
    person = cur.fetchone()
    cur.close()
    conn.close()
    return render_template('profile.html', person=person, avatar_url=getattr(current_user, 'avatar_url', None))

@app.route('/employee/dashboard', methods=['GET', 'POST'])
@login_required
@role_required('employee')
def employee_dashboard():
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("SELECT * FROM people WHERE id = %s", (current_user.employee_id,))
    person = cur.fetchone()
    if request.method == 'POST':
        req_type = request.form['type']
        start_date = request.form['start_date']
        end_date = request.form['end_date']
        comment = request.form['comment']
        cur.execute("""
            INSERT INTO requests (employee_id, type, start_date, end_date, comment)
            VALUES (%s, %s, %s, %s, %s)
        """, (current_user.employee_id, req_type, start_date, end_date, comment))
        conn.commit()
        flash('Заявка отправлена!', 'success')
    cur.execute("SELECT * FROM requests WHERE employee_id = %s ORDER BY created_at DESC", (current_user.employee_id,))
    requests = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('employee_dashboard.html', person=person, requests=requests)

@app.route('/requests')
@login_required
@role_required('hr_manager', 'superadmin', 'hr_staff')
def requests_list():
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '')
    req_type = request.args.get('type', '')
    department = request.args.get('department', '')
    sort = request.args.get('sort', 'created_at')
    order = request.args.get('order', 'desc')
    page = int(request.args.get('page', 1))
    per_page = 10
    offset = (page - 1) * per_page

    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    query = """
        SELECT r.id, p.фио, r.type, r.start_date, r.end_date, r.status, r.comment, r.created_at, d.название
        FROM requests r
        JOIN people p ON r.employee_id = p.id
        LEFT JOIN departament d ON p.отдел_id = d.id
    """
    filters = []
    params = []
    if search:
        filters.append("LOWER(p.фио) LIKE %s")
        params.append(f"%{search.lower()}%")
    if status:
        filters.append("r.status = %s")
        params.append(status)
    if req_type:
        filters.append("r.type = %s")
        params.append(req_type)
    if department:
        filters.append("p.отдел_id = %s")
        params.append(department)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    if sort not in ['created_at', 'start_date', 'end_date', 'status']:
        sort = 'created_at'
    if order not in ['asc', 'desc']:
        order = 'desc'
    query += f" ORDER BY r.{sort} {order.upper()} LIMIT %s OFFSET %s"
    params.extend([per_page, offset])
    cur.execute(query, params)
    requests = cur.fetchall()
    # Пагинация
    count_query = "SELECT COUNT(*) FROM requests r JOIN people p ON r.employee_id = p.id"
    if filters:
        count_query += " WHERE " + " AND ".join(filters)
    cur.execute(count_query, params[:-2])
    total = cur.fetchone()[0]
    pages = (total + per_page - 1) // per_page
    # Список отделов
    cur.execute("SELECT id, название FROM departament ORDER BY название")
    departments = cur.fetchall()
    cur.close()
    conn.close()
    return render_template(
        'requests_list.html',
        requests=requests,
        search=search,
        status=status,
        type=req_type,
        department=department,
        sort=sort,
        order=order,
        departments=departments,
        page=page,
        pages=pages
    )

@app.route('/requests/action/<int:request_id>/<action>', methods=['POST'])
@login_required
@role_required('hr_manager', 'superadmin', 'hr_staff')
def request_action(request_id, action):
    if action not in ['approve', 'reject']:
        abort(400)
    new_status = 'одобрена' if action == 'approve' else 'отклонена'
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("UPDATE requests SET status = %s WHERE id = %s", (new_status, request_id))
    conn.commit()
    cur.close()
    conn.close()
    flash(f'Заявка {request_id} {new_status}.', 'success')
    return redirect(url_for('requests_list'))

@app.route('/requests/bulk_action', methods=['POST'])
@login_required
@role_required('hr_manager', 'superadmin', 'hr_staff')
def bulk_requests_action():
    ids = request.form.getlist('selected_requests')
    action = request.form.get('bulk_action')
    if not ids or action not in ['approve', 'reject']:
        flash('Не выбраны заявки или действие!', 'error')
        return redirect(url_for('requests_list'))
    new_status = 'одобрена' if action == 'approve' else 'отклонена'
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute(f"UPDATE requests SET status = %s WHERE id = ANY(%s)", (new_status, ids))
    conn.commit()
    cur.close()
    conn.close()
    flash(f'Массовое действие выполнено: {len(ids)} заявок {new_status}.', 'success')
    return redirect(url_for('requests_list'))

@app.route('/requests/details/<int:request_id>', methods=['GET'])
@login_required
@role_required('hr_manager', 'superadmin', 'hr_staff')
def request_details(request_id):
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT r.id, p.фио, d.название, r.type, r.start_date, r.end_date, r.status, r.comment, r.created_at
        FROM requests r
        JOIN people p ON r.employee_id = p.id
        LEFT JOIN departament d ON p.отдел_id = d.id
        WHERE r.id = %s
    """, (request_id,))
    req = cur.fetchone()
    cur.execute("SELECT id, comment, created_at FROM request_comments WHERE request_id = %s ORDER BY created_at DESC", (request_id,))
    comments = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('request_details_modal.html', req=req, comments=comments)

@app.route('/requests/<int:request_id>/add_comment', methods=['POST'])
@login_required
@role_required('hr_manager', 'superadmin', 'hr_staff')
def add_comment(request_id):
    comment = request.form['comment']
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("INSERT INTO request_comments (request_id, comment, created_at) VALUES (%s, %s, NOW())", (request_id, comment))
    conn.commit()
    cur.close()
    conn.close()
    flash('Комментарий добавлен.', 'success')
    return '', 204

@app.route('/requests/export')
@login_required
@role_required('hr_manager', 'superadmin', 'hr_staff')
def export_requests():
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT r.id, p.фио, d.название, r.type, r.start_date, r.end_date, r.status, r.comment, r.created_at
        FROM requests r
        JOIN people p ON r.employee_id = p.id
        LEFT JOIN departament d ON p.отдел_id = d.id
        ORDER BY r.created_at DESC
    """)
    requests = cur.fetchall()
    cur.close()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Заявки"
    ws.append(['ID', 'Сотрудник', 'Отдел', 'Тип', 'Дата начала', 'Дата окончания', 'Статус', 'Комментарий', 'Создана'])
    for req in requests:
        ws.append(list(req))

    file_stream = io.BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)
    return send_file(
        file_stream,
        as_attachment=True,
        download_name='requests.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/requests/search_suggestions')
@login_required
@role_required('hr_manager', 'superadmin', 'hr_staff')
def search_suggestions():
    query = request.args.get('query', '').strip().lower()
    if not query or len(query) < 2:
        return jsonify([])
    conn = psycopg2.connect(
        dbname="hr_management", user="postgres", password="postgres", host="localhost"
    )
    cur = conn.cursor()
    cur.execute("SELECT фио FROM people WHERE LOWER(фио) LIKE %s LIMIT 7", (f"%{query}%",))
    results = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(results)

app.register_blueprint(hr_staff_contact_bp)

if __name__ == '__main__':
    app.run(debug=True)