import calendar
import csv
import hashlib
import io
import os
import re
import secrets
import smtplib
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
app = Flask(__name__)
from flask import (
    Flask,
    Response,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "tenge.db"
UPLOAD_DIR = BASE_DIR / "instance" / "uploads"

MAX_AMOUNT_TENGE = 1_000_000_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
RESET_TOKEN_LIFETIME = timedelta(minutes=30)
RESET_RESEND_COOLDOWN = 60
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

MONTHS_KZ = (
    "Қаңтар", "Ақпан", "Наурыз", "Сәуір", "Мамыр", "Маусым",
    "Шілде", "Тамыз", "Қыркүйек", "Қазан", "Қараша", "Желтоқсан",
)
MONTHS_SHORT_KZ = (
    "Қаң", "Ақп", "Нау", "Сәу", "Мам", "Мау",
    "Шіл", "Там", "Қыр", "Қаз", "Қар", "Жел",
)

CATEGORY_ICONS = (
    "tag", "shopping-basket", "house", "car", "shopping-bag", "clapperboard",
    "banknote", "wallet", "landmark", "shield-check", "globe", "clock",
    "piggy-bank", "credit-card", "alert-circle", "info",
)
CATEGORY_COLORS = (
    "#64cff9", "#36d489", "#f49a58", "#f37883", "#3a6ff8",
    "#a78bfa", "#f5c451", "#5eead4",
)

EXPENSE_CATEGORIES = {
    "food": "Тамақ",
    "transport": "Көлік",
    "housing": "Тұрғын үй",
    "utilities": "Коммуналдық төлемдер",
    "shopping": "Сауда",
    "health": "Денсаулық",
    "entertainment": "Ойын-сауық",
    "education": "Білім",
    "other": "Басқа",
}

INCOME_CATEGORIES = {
    "salary": "Жалақы",
    "side_income": "Қосымша табыс",
    "gift": "Сыйлық",
    "other": "Басқа",
}

CATEGORIES = {"expense": EXPENSE_CATEGORIES, "income": INCOME_CATEGORIES}
ALL_CATEGORIES = {**EXPENSE_CATEGORIES, **INCOME_CATEGORIES}

app = Flask(__name__)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True


def load_secret_key():
    """SECRET_KEY айнымалысын немесе instance/secret_key файлын қолданады."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key

    key_path = Path(app.instance_path) / "secret_key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        return key_path.read_text().strip()

    key = secrets.token_hex(32)
    key_path.write_text(key)
    return key


app.config["SECRET_KEY"] = load_secret_key()
csrf = CSRFProtect(app)

# Тіркелмеген email-мен кіру әрекетінде де хэш тексеріледі (уақыт айырмасы болмауы үшін).
DUMMY_HASH = generate_password_hash("tenge-dummy-password")
# База данных
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    username            TEXT NOT NULL,
    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    phone               TEXT NOT NULL DEFAULT '',
    city                TEXT NOT NULL DEFAULT '',
    birth_date          TEXT NOT NULL DEFAULT '',
    bio                 TEXT NOT NULL DEFAULT '',
    hide_balances       INTEGER NOT NULL DEFAULT 0,
    date_format         TEXT NOT NULL DEFAULT 'dmy',
    notify_site         INTEGER NOT NULL DEFAULT 1,
    notify_email        INTEGER NOT NULL DEFAULT 0,
    notify_budget       INTEGER NOT NULL DEFAULT 1,
    last_login_at       TEXT NOT NULL DEFAULT '',
    password_changed_at TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    bank_name       TEXT NOT NULL,
    last_four       TEXT NOT NULL CHECK (length(last_four) = 4),
    initial_balance INTEGER NOT NULL DEFAULT 0 CHECK (initial_balance >= 0),
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_id     INTEGER REFERENCES cards(id) ON DELETE SET NULL,
    type        TEXT NOT NULL CHECK (type IN ('income', 'expense')),
    amount      INTEGER NOT NULL CHECK (amount > 0),
    category    TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT CURRENT_DATE
);

-- Ішкі картадан картаға аударулар: қос жазба емес, бір ғана жазба.
-- request_key бір форманы екі рет жіберуден сақтайды.
CREATE TABLE IF NOT EXISTS transfers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_card_id      INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    destination_card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    amount              INTEGER NOT NULL CHECK (amount > 0),
    request_key         TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, request_key)
);

-- Тәуелсіз жинақ шоттары. Барлық сомалар тиынмен сақталады.
CREATE TABLE IF NOT EXISTS savings_accounts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    initial_amount      INTEGER NOT NULL CHECK (initial_amount >= 0),
    annual_rate         TEXT NOT NULL,
    start_date          TEXT NOT NULL,
    current_balance     INTEGER NOT NULL DEFAULT 0,
    accumulated_interest INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS savings_operations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES savings_accounts(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('deposit', 'withdrawal')),
    amount      INTEGER NOT NULL CHECK (amount > 0),
    occurred_on TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_initial  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Күндік пайыз есептеулері: бір күнге тек бір жазба болуы мүмкін.
CREATE TABLE IF NOT EXISTS savings_accruals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL REFERENCES savings_accounts(id) ON DELETE CASCADE,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    accrual_date    TEXT NOT NULL,
    opening_balance INTEGER NOT NULL,
    operation_delta INTEGER NOT NULL DEFAULT 0,
    interest        INTEGER NOT NULL,
    closing_balance INTEGER NOT NULL,
    UNIQUE (account_id, accrual_date)
);

CREATE TABLE IF NOT EXISTS user_categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('expense', 'income')),
    icon        TEXT NOT NULL DEFAULT 'tag',
    color       TEXT NOT NULL DEFAULT '#64cff9',
    is_default  INTEGER NOT NULL DEFAULT 0,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, key, type)
);

CREATE TABLE IF NOT EXISTS budgets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category_key    TEXT NOT NULL,
    category_type   TEXT NOT NULL DEFAULT 'expense',
    name            TEXT NOT NULL,
    amount          INTEGER NOT NULL CHECK (amount > 0),
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    alert_percent   INTEGER NOT NULL DEFAULT 80,
    notify          INTEGER NOT NULL DEFAULT 1,
    repeat_monthly  INTEGER NOT NULL DEFAULT 0,
    carry_over      INTEGER NOT NULL DEFAULT 0,
    notified_at     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, category_key, category_type, start_date)
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'system',
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    is_read     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS password_resets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    report_type TEXT NOT NULL,
    date_from   TEXT NOT NULL,
    date_to     TEXT NOT NULL,
    card_id     INTEGER,
    format      TEXT NOT NULL DEFAULT 'csv',
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS support_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email           TEXT NOT NULL,
    subject         TEXT NOT NULL,
    message         TEXT NOT NULL,
    attachment_name TEXT NOT NULL DEFAULT '',
    consent         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cards_user ON cards(user_id);
CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tx_card ON transactions(card_id);
CREATE INDEX IF NOT EXISTS idx_transfers_user ON transfers(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_savings_user ON savings_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_savings_ops ON savings_operations(account_id, occurred_on);
CREATE INDEX IF NOT EXISTS idx_savings_accruals ON savings_accruals(account_id, accrual_date);
CREATE INDEX IF NOT EXISTS idx_categories_user ON user_categories(user_id, type);
CREATE INDEX IF NOT EXISTS idx_budgets_user ON budgets(user_id, start_date);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read, created_at);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_resets_user ON password_resets(user_id, created_at);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def is_legacy_database():
    """Алғашқы нұсқадағы (user_id жоқ) ескі tenge.db файлын анықтайды."""
    if not DB_PATH.exists():
        return False

    con = sqlite3.connect(DB_PATH)
    try:
        tx_cols = {row[1] for row in con.execute("PRAGMA table_info(transactions)")}
        user_cols = {row[1] for row in con.execute("PRAGMA table_info(users)")}
    finally:
        con.close()

    if tx_cols and "user_id" not in tx_cols:
        return True
    if user_cols and "created_at" not in user_cols:
        return True
    return False


def ensure_user_columns(con):
    """Ескі tenge.db файлына профиль баптауларының бағандарын қосады."""
    migrations = {
        "phone": "TEXT NOT NULL DEFAULT ''",
        "city": "TEXT NOT NULL DEFAULT ''",
        "birth_date": "TEXT NOT NULL DEFAULT ''",
        "bio": "TEXT NOT NULL DEFAULT ''",
        "hide_balances": "INTEGER NOT NULL DEFAULT 0",
        "date_format": "TEXT NOT NULL DEFAULT 'dmy'",
        "notify_site": "INTEGER NOT NULL DEFAULT 1",
        "notify_email": "INTEGER NOT NULL DEFAULT 0",
        "notify_budget": "INTEGER NOT NULL DEFAULT 1",
        "last_login_at": "TEXT NOT NULL DEFAULT ''",
        "password_changed_at": "TEXT NOT NULL DEFAULT ''",
    }
    columns = {row[1] for row in con.execute("PRAGMA table_info(users)")}
    for name, declaration in migrations.items():
        if name not in columns:
            con.execute(f"ALTER TABLE users ADD COLUMN {name} {declaration}")

    con.execute(
        "UPDATE users SET password_changed_at = created_at WHERE password_changed_at = ''"
    )


def ensure_user_categories(con, user_id):
    """Әр пайдаланушыға стандартты санаттар жинағын қосады (бір рет)."""
    exists = con.execute(
        "SELECT 1 FROM user_categories WHERE user_id = ? LIMIT 1", (user_id,)
    ).fetchone()
    if exists:
        return

    rows = []
    for type_name, source in (("expense", EXPENSE_CATEGORIES), ("income", INCOME_CATEGORIES)):
        for index, (key, label) in enumerate(source.items()):
            rows.append(
                (
                    user_id,
                    key,
                    label,
                    type_name,
                    CATEGORY_ICONS[index % len(CATEGORY_ICONS)],
                    CATEGORY_COLORS[index % len(CATEGORY_COLORS)],
                    1,
                )
            )
    con.executemany(
        """
        INSERT OR IGNORE INTO user_categories
            (user_id, key, name, type, icon, color, is_default)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def init_db():
    """Кестелерді алғашқы іске қосуда автоматты түрде құрады."""
    if is_legacy_database():
        os.replace(DB_PATH, DB_PATH.with_name("tenge.legacy.db"))

    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(SCHEMA)
        ensure_user_columns(con)
        for row in con.execute("SELECT id FROM users").fetchall():
            ensure_user_categories(con, row[0])
        con.commit()
    finally:
        con.close()


init_db()

# Көмекші функциялар


def parse_amount(text, allow_zero=False):
    """'2500', '2 500,50' сияқты мәтінді тиынға (int) айналдырады. Қате болса None."""
    cleaned = (text or "").strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None

    if not value.is_finite():
        return None

    cents = value * 100
    if cents != cents.to_integral_value():
        return None

    cents = int(cents)
    if cents < 0 or (cents == 0 and not allow_zero):
        return None
    if cents > MAX_AMOUNT_TENGE * 100:
        return None
    return cents


def parse_date(text):
    text = (text or "").strip()
    if not text:
        return date.today()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Алдымен аккаунтыңызға кіріңіз.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.before_request
def load_current_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return

    g.user = get_db().execute(
        """
        SELECT id, username, email, phone, city, birth_date, bio,
               hide_balances, date_format, notify_site, notify_email, notify_budget,
               created_at, last_login_at, password_changed_at
        FROM users WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    if g.user is None:
        session.clear()


@app.template_filter("money")
def money_filter(cents):
    """Тиынды әдемі мәтінге айналдырады: 12500050 -> '125 000,50'."""
    if getattr(g, "user", None) and g.user["hide_balances"]:
        return "••••"

    try:
        cents = int(cents)
    except (TypeError, ValueError):
        cents = 0

    sign = "−" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    text = f"{whole:,}".replace(",", "\u00a0")
    if fraction:
        text += f",{fraction:02d}"
    return sign + text


@app.template_filter("date_kz")
def date_filter(value):
    try:
        if isinstance(value, str):
            value = datetime.strptime(value[:10], "%Y-%m-%d")
        pattern = "%Y-%m-%d" if (getattr(g, "user", None) and g.user["date_format"] == "iso") else "%d.%m.%Y"
        return value.strftime(pattern)
    except (TypeError, ValueError):
        return ""


@app.template_filter("initials")
def initials_filter(name):
    parts = (name or "").split()
    if not parts:
        return "?"
    if len(parts) > 1:
        return (parts[0][0] + parts[1][0]).upper()
    return parts[0][:2].upper()


def category_maps(db, user_id):
    """Пайдаланушы санаттарын түрі бойынша сөздіктерге айналдырады."""
    result = {"expense": {}, "income": {}, "meta": {}}
    if user_id is None:
        result["expense"] = dict(EXPENSE_CATEGORIES)
        result["income"] = dict(INCOME_CATEGORIES)
        return result

    for row in db.execute(
        """
        SELECT key, name, type, icon, color
        FROM user_categories WHERE user_id = ? AND archived = 0
        ORDER BY is_default DESC, name
        """,
        (user_id,),
    ):
        result[row["type"]][row["key"]] = row["name"]
        result["meta"][(row["type"], row["key"])] = {
            "icon": row["icon"],
            "color": row["color"],
            "name": row["name"],
        }

    # Архивтегі санаттар ескі операциялардың атауын көрсету үшін қажет.
    for row in db.execute(
        """
        SELECT key, name, type, icon, color
        FROM user_categories WHERE user_id = ? AND archived = 1
        """,
        (user_id,),
    ):
        result["meta"].setdefault(
            (row["type"], row["key"]),
            {"icon": row["icon"], "color": row["color"], "name": row["name"]},
        )
    return result


@app.context_processor
def inject_globals():
    user = getattr(g, "user", None)
    if user is not None:
        maps = category_maps(get_db(), user["id"])
        expense_categories = maps["expense"] or dict(EXPENSE_CATEGORIES)
        income_categories = maps["income"] or dict(INCOME_CATEGORIES)
        unread = get_db().execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
            (user["id"],),
        ).fetchone()[0]
    else:
        expense_categories = dict(EXPENSE_CATEGORIES)
        income_categories = dict(INCOME_CATEGORIES)
        unread = 0

    return {
        "all_categories": {**expense_categories, **income_categories},
        "expense_categories": expense_categories,
        "income_categories": income_categories,
        "category_meta": maps["meta"] if user is not None else {},
        "unread_notifications": unread,
        "current_year": date.today().year,
    }


def fetch_totals(db, user_id):
    """Депозит (кіріс) пен шығыс сомаларын бөлек-бөлек қайтарады.

    Ертеде екі сома бір кортежбен беріліп, реті ауысып кетуі мүмкін еді;
    енді әр мәннің аты бар, сондықтан депозит ешқашан шығыспен алмаспайды.
    """
    row = db.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income'  THEN amount END), 0) AS deposit,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount END), 0) AS expense
        FROM transactions
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    return {"deposit": row["deposit"], "expense": row["expense"]}


def fetch_cards(db, user_id):
    """Әр картаның ағымдағы балансымен: бастапқы баланс + кіріс − шығыс."""
    return db.execute(
        """
        SELECT
            c.id, c.name, c.bank_name, c.last_four, c.initial_balance, c.created_at,
            c.initial_balance + COALESCE(SUM(
                CASE t.type WHEN 'income' THEN t.amount
                            WHEN 'expense' THEN -t.amount END
            ), 0) AS balance,
            COALESCE(SUM(CASE WHEN t.type = 'income'  THEN t.amount END), 0) AS income,
            COALESCE(SUM(CASE WHEN t.type = 'expense' THEN t.amount END), 0) AS expense
        FROM cards c
        LEFT JOIN transactions t ON t.card_id = c.id
        WHERE c.user_id = ?
        GROUP BY c.id
        ORDER BY c.id DESC
        """,
        (user_id,),
    ).fetchall()

# Тәуелсіз жинақ жүйесі: барлық есептеулер backend-де, тек Decimal арқылы.


def parse_annual_rate(text):
    """Пайыз мәнін Decimal-ге айналдырады (0–100 аралығы)."""
    cleaned = (text or "").strip().replace(" ", "").replace(",", ".").replace("%", "")
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value < 0 or value > 100:
        return None
    return value


def savings_daily_rate(rate_text):
    return Decimal(rate_text) / Decimal(100) / Decimal(365)


def savings_operation_map(db, account_id):
    """Күнге қарай салым/шегерім операцияларын жинайды."""
    operations = db.execute(
        """
        SELECT occurred_on, kind, amount FROM savings_operations
        WHERE account_id = ? ORDER BY occurred_on, id
        """,
        (account_id,),
    ).fetchall()
    by_date = {}
    for op in operations:
        delta = op["amount"] if op["kind"] == "deposit" else -op["amount"]
        by_date.setdefault(op["occurred_on"], []).append(delta)
    return operations, by_date


def rebuild_savings_account(db, account_id, today=None):
    """Бастапқы күннен бүгінге дейін күндік пайызды қайта есептейді.

    Кейін салынған немесе алынған сомалар өз күнінен бастап есепке алынады.
    savings_accruals кестесінде бір күнге бір ғана жазба болады.
    """
    account = db.execute(
        "SELECT * FROM savings_accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if account is None:
        return None

    start = date.fromisoformat(account["start_date"])
    end = today or date.today()
    if end < start:
        end = start

    operations, by_date = savings_operation_map(db, account_id)
    daily_rate = savings_daily_rate(account["annual_rate"])

    balance = 0
    for op in operations:
        if op["occurred_on"] <= account["start_date"]:
            balance += op["amount"] if op["kind"] == "deposit" else -op["amount"]

    interest_total = 0
    minimum_balance = balance
    db.execute("DELETE FROM savings_accruals WHERE account_id = ?", (account_id,))

    day = start + timedelta(days=1)
    while day <= end:
        opening = balance
        delta = sum(by_date.get(day.isoformat(), []))
        balance += delta
        interest = 0
        if balance > 0:
            interest = int(
                (Decimal(balance) * daily_rate).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
        balance += interest
        interest_total += interest
        minimum_balance = min(minimum_balance, balance)

        db.execute(
            """
            INSERT INTO savings_accruals
                (account_id, user_id, accrual_date, opening_balance,
                 operation_delta, interest, closing_balance)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                account["user_id"],
                day.isoformat(),
                opening,
                delta,
                interest,
                balance,
            ),
        )
        day += timedelta(days=1)

    db.execute(
        "UPDATE savings_accounts SET current_balance = ?, accumulated_interest = ? WHERE id = ?",
        (balance, interest_total, account_id),
    )
    return {
        "current_balance": balance,
        "accumulated_interest": interest_total,
        "minimum_balance": minimum_balance,
    }


def accrue_savings_account(db, account_id, today=None):
    """Соңғы есептелген күннен бүгінге дейін пайыз қосады."""
    account = db.execute(
        "SELECT * FROM savings_accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if account is None:
        return None

    end = today or date.today()
    last = db.execute(
        "SELECT MAX(accrual_date) AS d FROM savings_accruals WHERE account_id = ?",
        (account_id,),
    ).fetchone()["d"]
    if last is None:
        return rebuild_savings_account(db, account_id, end)

    last_day = date.fromisoformat(last)
    if last_day >= end:
        return None

    _, by_date = savings_operation_map(db, account_id)
    daily_rate = savings_daily_rate(account["annual_rate"])
    balance = account["current_balance"]
    interest_total = account["accumulated_interest"]
    minimum_balance = balance

    day = last_day + timedelta(days=1)
    while day <= end:
        opening = balance
        delta = sum(by_date.get(day.isoformat(), []))
        balance += delta
        interest = 0
        if balance > 0:
            interest = int(
                (Decimal(balance) * daily_rate).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
        balance += interest
        interest_total += interest
        minimum_balance = min(minimum_balance, balance)

        db.execute(
            """
            INSERT OR IGNORE INTO savings_accruals
                (account_id, user_id, accrual_date, opening_balance,
                 operation_delta, interest, closing_balance)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                account["user_id"],
                day.isoformat(),
                opening,
                delta,
                interest,
                balance,
            ),
        )
        day += timedelta(days=1)

    db.execute(
        "UPDATE savings_accounts SET current_balance = ?, accumulated_interest = ? WHERE id = ?",
        (balance, interest_total, account_id),
    )
    return {
        "current_balance": balance,
        "accumulated_interest": interest_total,
        "minimum_balance": minimum_balance,
    }


def sync_savings_accounts(db, user_id, today=None):
    """Бет ашылған сайын пайызды ағымдағы күнге дейін жаңартады."""
    for row in db.execute(
        "SELECT id FROM savings_accounts WHERE user_id = ?", (user_id,)
    ).fetchall():
        accrue_savings_account(db, row["id"], today)


def fetch_savings_accounts(db, user_id):
    """Жинақ шоттарын бөлек сомаларымен және күндік пайызымен қайтарады."""
    return db.execute(
        """
        SELECT s.*,
               COALESCE(SUM(CASE WHEN o.kind = 'deposit' AND o.is_initial = 0
                                 THEN o.amount END), 0) AS additional_deposits,
               COALESCE(SUM(CASE WHEN o.kind = 'withdrawal'
                                 THEN o.amount END), 0) AS withdrawals,
               (SELECT a.interest FROM savings_accruals a
                WHERE a.account_id = s.id
                ORDER BY a.accrual_date DESC LIMIT 1) AS daily_interest
        FROM savings_accounts s
        LEFT JOIN savings_operations o ON o.account_id = s.id
        WHERE s.user_id = ?
        GROUP BY s.id
        ORDER BY s.id DESC
        """,
        (user_id,),
    ).fetchall()


def fetch_savings_summary(db, user_id):
    return db.execute(
        """
        SELECT COUNT(*) AS accounts_count,
               COALESCE(SUM(current_balance), 0) AS balance,
               COALESCE(SUM(accumulated_interest), 0) AS interest
        FROM savings_accounts WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()


def fetch_user_stats(db, user_id):
    return db.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM cards WHERE user_id = ?) AS cards_count,
            (SELECT COUNT(*) FROM transactions WHERE user_id = ?) AS tx_count,
            (SELECT COALESCE(SUM(initial_balance), 0) FROM cards WHERE user_id = ?) AS opening
        """,
        (user_id, user_id, user_id),
    ).fetchone()


def month_bounds(month_text):
    """'2026-11' -> (start_date, end_date, year, month), немесе None."""
    try:
        parsed = datetime.strptime(month_text or "", "%Y-%m").date()
    except (TypeError, ValueError):
        return None
    last_day = calendar.monthrange(parsed.year, parsed.month)[1]
    return (
        date(parsed.year, parsed.month, 1),
        date(parsed.year, parsed.month, last_day),
        parsed.year,
        parsed.month,
    )


def month_label(year, month):
    return f"{MONTHS_KZ[month - 1]}, {year}"


def shift_month(month_text, offset):
    bounds = month_bounds(month_text)
    if bounds is None:
        return date.today().strftime("%Y-%m")
    index = bounds[2] * 12 + (bounds[3] - 1) + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def send_email(to, subject, body):
    """SMTP_HOST т.б. орнатылса, нақты email жібереді. Әйтпесе False қайтарады."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        return False

    port = int(os.environ.get("SMTP_PORT", "587"))
    sender = os.environ.get("SMTP_FROM", "noreply@tenge.local")
    username = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return True
    except Exception:
        current_app.logger.exception("Email жіберу сәтсіз аяқталды")
        return False


def add_notification(db, user_id, kind, title, body="", url="", email_user=None):
    """Сайттағы хабарландыруды сақтайды (қажет болса email де жібереді)."""
    prefs = db.execute(
        "SELECT email, notify_site, notify_email, notify_budget FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if prefs is None or not prefs["notify_site"]:
        return
    if kind == "budget" and not prefs["notify_budget"]:
        return

    db.execute(
        """
        INSERT INTO notifications (user_id, kind, title, body, url)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, kind, title, body, url),
    )
    if email_user is None:
        email_user = os.environ.get("SMTP_EMAIL_NOTIFICATIONS", "") in ("1", "true", "True")
    if email_user and prefs["notify_email"]:
        send_email(prefs["email"], title, body)


def notify_budget_alerts(db, user_id, today=None):
    """Лимитке жеткен бюджеттер үшін бір реттік ескерту жасайды."""
    today = today or date.today()
    rows = db.execute(
        """
        SELECT b.*, c.name AS category_name
        FROM budgets b
        LEFT JOIN user_categories c
               ON c.user_id = b.user_id AND c.key = b.category_key AND c.type = b.category_type
        WHERE b.user_id = ? AND b.notify = 1 AND b.notified_at = ''
          AND b.start_date <= ? AND b.end_date >= ?
        """,
        (user_id, today.isoformat(), today.isoformat()),
    ).fetchall()

    for budget in rows:
        spent = db.execute(
            """
            SELECT COALESCE(SUM(amount), 0) FROM transactions
            WHERE user_id = ? AND type = 'expense' AND category = ?
              AND created_at BETWEEN ? AND ?
            """,
            (
                user_id,
                budget["category_key"],
                budget["start_date"],
                budget["end_date"],
            ),
        ).fetchone()[0]
        percent = round(spent * 100 / budget["amount"]) if budget["amount"] else 0
        if percent >= budget["alert_percent"]:
            add_notification(
                db,
                user_id,
                "budget",
                f"{budget['category_name'] or budget['name']} бюджетінің {percent}%-ы жұмсалды",
                (
                    f"{budget['amount']} ₸ бюджетінен {spent} ₸ жұмсалды. "
                    f"Қалған сома: {max(0, budget['amount'] - spent)} ₸."
                ),
                url_for("budgets", month=budget["start_date"][:7]),
            )
            db.execute(
                "UPDATE budgets SET notified_at = ? WHERE id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), budget["id"]),
            )
    db.commit()


def refresh_recurring_budgets(db, user_id, through_month):
    """Қайталанатын бюджеттерді ай сайын автоматты түрде жаңартады."""
    limit = month_bounds(through_month)
    if limit is None:
        return

    sources = db.execute(
        """
        SELECT * FROM budgets
        WHERE user_id = ? AND repeat_monthly = 1
        """,
        (user_id,),
    ).fetchall()

    for source in sources:
        cursor = month_bounds(source["end_date"][:7])
        if cursor is None:
            continue
        guard = 0
        while guard < 12:
            guard += 1
            next_month = shift_month(f"{cursor[2]:04d}-{cursor[3]:02d}", 1)
            bounds = month_bounds(next_month)
            if bounds is None or bounds[0] > limit[0]:
                break

            start, end = bounds[0], bounds[1]
            exists = db.execute(
                """
                SELECT 1 FROM budgets
                WHERE user_id = ? AND category_key = ? AND category_type = ? AND start_date = ?
                """,
                (user_id, source["category_key"], source["category_type"], start.isoformat()),
            ).fetchone()
            if not exists:
                remaining = max(0, source["amount"] - min(
                    source["amount"],
                    db.execute(
                        """
                        SELECT COALESCE(SUM(amount), 0) FROM transactions
                        WHERE user_id = ? AND type = 'expense' AND category = ?
                          AND created_at BETWEEN ? AND ?
                        """,
                        (user_id, source["category_key"], source["start_date"], source["end_date"]),
                    ).fetchone()[0],
                ))
                amount = source["amount"] + (remaining if source["carry_over"] else 0)
                db.execute(
                    """
                    INSERT OR IGNORE INTO budgets
                        (user_id, category_key, category_type, name, amount, start_date, end_date,
                         alert_percent, notify, repeat_monthly, carry_over)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        source["category_key"],
                        source["category_type"],
                        source["name"],
                        amount,
                        start.isoformat(),
                        end.isoformat(),
                        source["alert_percent"],
                        source["notify"],
                        1,
                        source["carry_over"],
                    ),
                )
            cursor = month_bounds(next_month)
    db.commit()


@app.errorhandler(CSRFError)
def handle_csrf_error(_error):
    flash("Сессия мерзімі өтті. Әрекетті қайталаңыз.", "error")
    return redirect(url_for("index"))


@app.errorhandler(404)
def not_found(_error):
    return render_template(
        "error.html", code=404, message="Бұл бет табылмады."
    ), 404


@app.errorhandler(500)
def server_error(_error):
    return render_template(
        "error.html", code=500, message="Серверде қате пайда болды. Кейінірек қайталап көріңіз."
    ), 500


# ---------------------------------------------------------------------------
# Авторизация
# ---------------------------------------------------------------------------


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("index"))

    if request.method == "GET":
        return render_template("register.html", form={})

    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")

    error = None
    if not username or not email or not password or not password_confirm:
        error = "Барлық өрістерді толтырыңыз."
    elif not 2 <= len(username) <= 50:
        error = "Аты 2-ден 50 таңбаға дейін болуы керек."
    elif len(email) > 254 or not EMAIL_RE.match(email):
        error = "Email мекенжайы дұрыс емес."
    elif len(password) < 8:
        error = "Құпиясөз кемінде 8 таңбадан тұруы керек."
    elif len(password) > 128:
        error = "Құпиясөз тым ұзын."
    elif password != password_confirm:
        error = "Құпиясөздер сәйкес келмейді."

    db = get_db()
    if error is None:
        exists = db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if exists:
            error = "Бұл email бұрын тіркелген."

    if error is None:
        try:
            cursor = db.execute(
                """
                INSERT INTO users (username, email, password_hash, password_changed_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    username,
                    email,
                    generate_password_hash(password),
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                ),
            )
            ensure_user_categories(db, cursor.lastrowid)
            db.commit()
        except sqlite3.IntegrityError:
            error = "Бұл email бұрын тіркелген."

    if error:
        flash(error, "error")
        return render_template(
            "register.html", form={"username": username, "email": email}
        ), 400

    flash("Тіркелу сәтті аяқталды. Енді жүйеге кіріңіз.", "success")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index"))

    if request.method == "GET":
        return render_template("login.html", form={})

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    db = get_db()
    user = db.execute(
        "SELECT id, password_hash, last_login_at FROM users WHERE email = ?", (email,)
    ).fetchone()

    password_hash = user["password_hash"] if user else DUMMY_HASH
    valid = check_password_hash(password_hash, password)

    if user is None or not valid:
        flash("Email немесе құпиясөз қате.", "error")
        return render_template("login.html", form={"email": email}), 401

    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    ensure_user_categories(db, user["id"])
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if user["last_login_at"]:
        add_notification(
            db,
            user["id"],
            "security",
            "Тіркелгіңізге жаңа кіру",
            "Аккаунтыңызға жаңа сессия ашылды. Бұл сіз болсаңыз, әрекет қажет емес.",
            url_for("settings", tab="security"),
        )
    db.execute(
        "UPDATE users SET last_login_at = ? WHERE id = ?", (now, user["id"])
    )
    db.commit()
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("Аккаунттан шықтыңыз.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Құпиясөзді қалпына келтіру
# ---------------------------------------------------------------------------


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if g.user:
        return redirect(url_for("index"))

    db = get_db()
    form = {"email": request.form.get("email", "").strip().lower()}
    sent = False
    reset_url = ""
    cooldown = 0

    if request.method == "POST":
        user = db.execute(
            "SELECT id, email FROM users WHERE email = ?", (form["email"],)
        ).fetchone()

        last = db.execute(
            "SELECT created_at FROM password_resets WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user["id"],) if user else (-1,),
        ).fetchone()
        if last:
            try:
                age = (datetime.now() - datetime.strptime(last["created_at"], "%Y-%m-%d %H:%M:%S")).total_seconds()
            except ValueError:
                age = RESET_RESEND_COOLDOWN
            cooldown = max(0, int(RESET_RESEND_COOLDOWN - age))

        if not form["email"] or len(form["email"]) > 254 or not EMAIL_RE.match(form["email"]):
            flash("Email мекенжайын дұрыс енгізіңіз.", "error")
        elif cooldown > 0:
            flash(f"Жіберу үшін {cooldown} секунд күте тұрыңыз.", "error")
        elif user is not None:
            token = secrets.token_urlsafe(32)
            now = datetime.now()
            db.execute(
                """
                INSERT INTO password_resets (user_id, token_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    user["id"],
                    hashlib.sha256(token.encode("utf-8")).hexdigest(),
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    (now + RESET_TOKEN_LIFETIME).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            db.commit()

            reset_url = url_for("reset_password", token=token, _external=True)
            delivered = send_email(
                user["email"],
                "Tenge — құпиясөзді қалпына келтіру",
                (
                    "Құпиясөзді қалпына келтіру үшін мына сілтемеге өтіңіз:\n"
                    f"{reset_url}\n\nСілтеме 30 минут жарамды. Егер сіз бұл сұранысты жібермеген "
                    "болсаңыз, бұл хабарламаны елемеңіз."
                ),
            )
            if not delivered:
                # SMTP конфигурациясы болмағанда сілтеме пайдаланушыға тікелей көрсетіледі.
                cooldown = RESET_RESEND_COOLDOWN
                sent = True
                flash(
                    "SMTP орнатылмағандықтан, қалпына келтіру сілтемесі төменде көрсетілді.",
                    "success",
                )
            else:
                flash("Қалпына келтіру нұсқаулығы email-ге жіберілді.", "success")
                sent = True
                cooldown = RESET_RESEND_COOLDOWN
        else:
            # Email бар-жоғын ашпаймыз, бірақ пайдаланушыға нақты хабар береміз.
            sent = True
            cooldown = RESET_RESEND_COOLDOWN
            flash("Егер бұл email тіркелген болса, нұсқаулық жіберілді.", "success")

    return render_template(
        "forgot_password.html",
        form=form,
        sent=sent,
        reset_url=reset_url,
        cooldown=cooldown,
        email=form["email"],
    )


def find_reset_token(token):
    if not token or len(token) > 200:
        return None
    db = get_db()
    row = db.execute(
        """
        SELECT r.*, u.email FROM password_resets r
        JOIN users u ON u.id = r.user_id
        WHERE r.token_hash = ? AND r.used_at = '' AND r.expires_at > ?
        """,
        (hashlib.sha256(token.encode("utf-8")).hexdigest(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ).fetchone()
    return row


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if g.user:
        return redirect(url_for("index"))

    db = get_db()
    row = find_reset_token(token)
    form = {}

    if request.method == "POST":
        if row is None:
            flash("Сілтеме жарамсыз немесе мерзімі өткен.", "error")
            return redirect(url_for("forgot_password"))

        password = request.form.get("password", "")
        confirmation = request.form.get("password_confirm", "")
        error = None
        if len(password) < 8:
            error = "Құпиясөз кемінде 8 таңбадан тұруы керек."
        elif len(password) > 128:
            error = "Құпиясөз тым ұзын."
        elif password != confirmation:
            error = "Құпиясөздер сәйкес келмейді."

        if error:
            flash(error, "error")
            form = {"password": password}
        else:
            db.execute(
                "UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?",
                (
                    generate_password_hash(password),
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                    row["user_id"],
                ),
            )
            db.execute("UPDATE password_resets SET used_at = ? WHERE id = ?", (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row["id"],
            ))
            db.execute(
                "DELETE FROM password_resets WHERE user_id = ? AND used_at = ''",
                (row["user_id"],),
            )
            add_notification(
                db,
                row["user_id"],
                "security",
                "Құпиясөз өзгертілді",
                "Тіркелгіңіздің құпиясөзі қалпына келтіру арқылы жаңартылды.",
                url_for("settings", tab="security"),
            )
            db.commit()
            flash("Құпиясөз жаңартылды. Енді кіре аласыз.", "success")
            return redirect(url_for("login"))

    return render_template(
        "reset_password.html",
        token=token,
        form=form,
        valid=row is not None,
    )


# ---------------------------------------------------------------------------
# Негізгі бет (Dashboard)
# ---------------------------------------------------------------------------


@app.route("/", methods=["GET"])
@login_required
def index():
    db = get_db()
    totals = fetch_totals(db, g.user["id"])
    deposit = totals["deposit"]  # тек кіріс операциялары
    income = deposit  # ескі шаблондар үшін сақталған атау
    expense = totals["expense"]
    cards_opening = db.execute(
        "SELECT COALESCE(SUM(initial_balance), 0) FROM cards WHERE user_id = ?",
        (g.user["id"],),
    ).fetchone()[0]

    if income > 0:
        spent_percent = min(100, round(expense * 100 / income))
    else:
        spent_percent = 100 if expense > 0 else 0

    transactions = db.execute(
        """
        SELECT t.*, c.name AS card_name, c.last_four AS card_last_four
        FROM transactions t
        LEFT JOIN cards c ON c.id = t.card_id
        WHERE t.user_id = ?
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 50
        """,
        (g.user["id"],),
    ).fetchall()

    cards = fetch_cards(db, g.user["id"])
    cards_total = sum(card["balance"] for card in cards)

    # Жинақ шығыстан тәуелсіз: тек өз шоттарымыз бен күндік пайыз.
    sync_savings_accounts(db, g.user["id"])
    db.commit()
    savings_accounts = fetch_savings_accounts(db, g.user["id"])
    savings_summary = fetch_savings_summary(db, g.user["id"])

    return render_template(
        "index.html",
        income=income,
        deposit=deposit,
        expense=expense,
        balance=cards_opening + deposit - expense,
        cards_opening=cards_opening,
        spent_percent=spent_percent,
        transactions=transactions,
        cards=cards,
        cards_total=cards_total,
        savings_accounts=savings_accounts,
        savings_balance=savings_summary["balance"],
        savings_interest=savings_summary["interest"],
        today=date.today(),
    )


@app.route("/add", methods=["POST"])
@login_required
def add_transaction():
    db = get_db()
    next_page = request.form.get("next", "index")
    raw_card_id = request.form.get("card_id", "").strip()

    card_id = None
    if raw_card_id:
        card = None
        if raw_card_id.isdigit():
            card = db.execute(
                "SELECT id FROM cards WHERE id = ? AND user_id = ?",
                (int(raw_card_id), g.user["id"]),
            ).fetchone()
        if card is None:
            flash("Карта табылмады.", "error")
            return redirect(url_for("cards"))
        card_id = card["id"]

    def back():
        if next_page == "cards" and card_id:
            return redirect(url_for("cards", card=card_id))
        if next_page == "transactions":
            return redirect(url_for("transactions"))
        return redirect(url_for("index"))

    tx_type = request.form.get("type", "")
    category = request.form.get("category", "")
    description = request.form.get("description", "").strip()
    amount = parse_amount(request.form.get("amount"))
    tx_date = parse_date(request.form.get("date"))

    if tx_type not in CATEGORIES:
        flash("Операция түрін таңдаңыз.", "error")
        return back()
    if amount is None:
        flash("Соманы дұрыс енгізіңіз: ол нөлден үлкен және 1 000 000 000 ₸-ден аспауы керек.", "error")
        return back()

    valid_category = db.execute(
        """
        SELECT 1 FROM user_categories
        WHERE user_id = ? AND key = ? AND type = ? AND archived = 0
        """,
        (g.user["id"], category, tx_type),
    ).fetchone()
    if valid_category is None:
        flash("Операция түріне сәйкес санатты таңдаңыз.", "error")
        return back()
    if len(description) > 300:
        flash("Сипаттама 300 таңбадан аспауы керек.", "error")
        return back()
    if tx_date is None:
        flash("Күн дұрыс енгізілмеген.", "error")
        return back()
    if tx_date > date.today():
        flash("Күн болашақта болмауы керек.", "error")
        return back()

    db.execute(
        """
        INSERT INTO transactions (user_id, card_id, type, amount, category, description, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (g.user["id"], card_id, tx_type, amount, category, description, tx_date.isoformat()),
    )
    db.commit()
    notify_budget_alerts(db, g.user["id"])

    flash("Операция сақталды.", "success")
    return back()


@app.route("/transactions/<int:transaction_id>/delete", methods=["POST"])
@login_required
def delete_transaction(transaction_id):
    db = get_db()
    row = db.execute(
        "SELECT card_id FROM transactions WHERE id = ? AND user_id = ?",
        (transaction_id, g.user["id"]),
    ).fetchone()

    if row is None:
        flash("Операция табылмады.", "error")
        return redirect(url_for("index"))

    db.execute(
        "DELETE FROM transactions WHERE id = ? AND user_id = ?",
        (transaction_id, g.user["id"]),
    )
    db.commit()
    flash("Операция жойылды.", "success")

    if request.form.get("next") == "cards" and row["card_id"]:
        return redirect(url_for("cards", card=row["card_id"]))
    if request.form.get("next") == "transactions":
        return redirect(url_for("transactions"))
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Банк карталары
# ---------------------------------------------------------------------------


@app.route("/cards", methods=["GET"])
@login_required
def cards():
    db = get_db()
    card_list = fetch_cards(db, g.user["id"])

    selected = None
    requested = request.args.get("card", type=int)
    for card in card_list:
        if card["id"] == requested:
            selected = card
    if selected is None and card_list:
        selected = card_list[0]

    card_transactions = []
    card_deposit = card_expense = 0
    if selected:
        card_transactions = db.execute(
            """
            SELECT * FROM transactions
            WHERE card_id = ? AND user_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (selected["id"], g.user["id"]),
        ).fetchall()
        for tx in card_transactions:
            if tx["type"] == "income":
                card_deposit += tx["amount"]
            else:
                card_expense += tx["amount"]

    return render_template(
        "cards.html",
        cards=card_list,
        selected=selected,
        transactions=card_transactions,
        card_deposit=card_deposit,
        card_expense=card_expense,
        total_balance=sum(card["balance"] for card in card_list),
        transfer_key=secrets.token_hex(16),
        today=date.today(),
    )


@app.route("/add_card", methods=["POST"])
@login_required
def add_card():
    name = request.form.get("name", "").strip()
    bank_name = request.form.get("bank_name", "").strip()
    last_four = request.form.get("last_four", "").strip()
    initial_balance = parse_amount(request.form.get("initial_balance", "0") or "0", allow_zero=True)

    if not name or len(name) > 50:
        flash("Карта атауын енгізіңіз (50 таңбаға дейін).", "error")
    elif not bank_name or len(bank_name) > 50:
        flash("Банк атауын енгізіңіз (50 таңбаға дейін).", "error")
    elif not (len(last_four) == 4 and last_four.isascii() and last_four.isdigit()):
        flash("Картаның соңғы 4 цифрын енгізіңіз.", "error")
    elif initial_balance is None:
        flash("Бастапқы баланс дұрыс емес: 0-ден 1 000 000 000 ₸-ге дейін болуы керек.", "error")
    else:
        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO cards (user_id, name, bank_name, last_four, initial_balance)
            VALUES (?, ?, ?, ?, ?)
            """,
            (g.user["id"], name, bank_name, last_four, initial_balance),
        )
        db.commit()
        flash("Карта қосылды.", "success")
        return redirect(url_for("cards", card=cursor.lastrowid))

    return redirect(url_for("cards"))


@app.route("/transactions", methods=["GET"])
@login_required
def transactions():
    """Операциялар беті — бүйірлік мәзірдегі «Операциялар» осы бетке апарады."""
    db = get_db()
    rows = db.execute(
        """
        SELECT t.*, c.name AS card_name, c.last_four AS card_last_four
        FROM transactions t
        LEFT JOIN cards c ON c.id = t.card_id
        WHERE t.user_id = ?
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 50
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template(
        "transactions.html",
        transactions=rows,
        cards=fetch_cards(db, g.user["id"]),
        today=date.today(),
    )


@app.route("/cards/<int:card_id>/delete", methods=["POST"])
@login_required
def delete_card(card_id):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM cards WHERE id = ? AND user_id = ?", (card_id, g.user["id"])
    )
    db.commit()

    if cursor.rowcount:
        flash("Карта жойылды. Оның операциялары жалпы тарихта қалды.", "success")
    else:
        flash("Карта табылмады.", "error")
    return redirect(url_for("cards"))


@app.route("/transfer", methods=["POST"])
@app.route("/cards/transfer", methods=["POST"])
@login_required
def transfer():
    """Екі карта арасында ішкі аудару.

    Қаражат карталардың сақталған бастапқы балансынан ауыстырылады: бір
    картадан шегеріліп, екіншісіне дәл сонша қосылады. Жалпы баланс өзгермейді,
    ал кіріс/шығыс статистикасына әсер етпейді (аудару — жаңа табыс емес).
    """
    db = get_db()
    source_raw = request.form.get("source_card_id", "").strip()
    destination_raw = request.form.get("destination_card_id", "").strip()
    amount = parse_amount(request.form.get("amount"))
    transfer_key = request.form.get("transfer_key", "").strip()

    def back(card_id=None):
        return redirect(url_for("cards", card=card_id) if card_id else url_for("cards"))

    if amount is None:
        flash("Аудару сомасы дұрыс енгізілмеген.", "error")
        return back()
    if not source_raw.isdigit() or not destination_raw.isdigit():
        flash("Карталарды таңдаңыз.", "error")
        return back()

    source_id, destination_id = int(source_raw), int(destination_raw)
    if source_id == destination_id:
        flash("Бір картадан өзіне аудару мүмкін емес.", "error")
        return back(source_id)

    owned = {
        row["id"]: row
        for row in db.execute(
            "SELECT id, name FROM cards WHERE id IN (?, ?) AND user_id = ?",
            (source_id, destination_id, g.user["id"]),
        ).fetchall()
    }
    if source_id not in owned or destination_id not in owned:
        flash("Таңдалған карталар сіздікі болуы керек.", "error")
        return back(source_id)

    balance_row = db.execute(
        """
        SELECT c.initial_balance + COALESCE(SUM(
                   CASE t.type WHEN 'income' THEN t.amount
                               WHEN 'expense' THEN -t.amount ELSE 0 END), 0) AS balance
        FROM cards c
        LEFT JOIN transactions t ON t.card_id = c.id
        WHERE c.id = ? AND c.user_id = ?
        GROUP BY c.id
        """,
        (source_id, g.user["id"]),
    ).fetchone()
    if balance_row is None or amount > balance_row["balance"]:
        flash("Картада аудару үшін жеткілікті қаражат жоқ.", "error")
        return back(source_id)

    # Формадағы бір реттік кілт қайта жіберуден сақтайды. Кілт жіберілмесе,
    # сол күнгі бірдей аударуды қайталау деп есептейміз.
    dedupe_key = transfer_key or hashlib.sha256(
        f"{g.user['id']}:{source_id}:{destination_id}:{amount}:{date.today().isoformat()}".encode()
    ).hexdigest()
    if db.execute(
        "SELECT 1 FROM transfers WHERE user_id = ? AND request_key = ?",
        (g.user["id"], dedupe_key),
    ).fetchone():
        flash("Бұл аудару бұрын сақталған.", "error")
        return back(source_id)

    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE cards SET initial_balance = initial_balance - ? WHERE id = ? AND user_id = ?",
            (amount, source_id, g.user["id"]),
        )
        db.execute(
            "UPDATE cards SET initial_balance = initial_balance + ? WHERE id = ? AND user_id = ?",
            (amount, destination_id, g.user["id"]),
        )
        db.execute(
            """
            INSERT INTO transfers
                (user_id, source_card_id, destination_card_id, amount, request_key)
            VALUES (?, ?, ?, ?, ?)
            """,
            (g.user["id"], source_id, destination_id, amount, dedupe_key),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash("Бұл аудару бұрын сақталған.", "error")
        return back(source_id)
    except sqlite3.Error:
        db.rollback()
        flash("Аудару сақталмады, қайталап көріңіз.", "error")
        return back(source_id)

    flash(
        f"{owned[source_id]['name']} → {owned[destination_id]['name']}: "
        f"{amount / 100:.2f} ₸ аударылды.",
        "success",
    )
    return back(source_id)


# ---------------------------------------------------------------------------
# Тәуелсіз жинақ шоттары
# ---------------------------------------------------------------------------


@app.route("/savings", methods=["GET"])
@login_required
def savings():
    db = get_db()
    sync_savings_accounts(db, g.user["id"])
    db.commit()
    return render_template(
        "savings.html",
        accounts=fetch_savings_accounts(db, g.user["id"]),
        summary=fetch_savings_summary(db, g.user["id"]),
        today=date.today(),
    )


@app.route("/savings", methods=["POST"])
@login_required
def create_savings():
    name = request.form.get("name", "").strip()
    initial = parse_amount(request.form.get("initial_amount") or "0", allow_zero=True)
    rate = parse_annual_rate(request.form.get("annual_rate"))
    start = parse_date(request.form.get("start_date"))

    if not name or len(name) > 50:
        flash("Жинақ атауын енгізіңіз (50 таңбаға дейін).", "error")
    elif initial is None:
        flash("Бастапқы салымы дұрыс енгізілмеген.", "error")
    elif rate is None:
        flash("Жылдық пайыз 0 мен 100 аралығында болуы керек.", "error")
    elif start is None or start > date.today():
        flash("Салым басталған күні дұрыс енгізілмеген.", "error")
    else:
        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO savings_accounts
                (user_id, name, initial_amount, annual_rate, start_date,
                 current_balance, accumulated_interest)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (g.user["id"], name, initial, str(rate), start.isoformat(), initial),
        )
        account_id = cursor.lastrowid
        db.execute(
            """
            INSERT INTO savings_operations
                (account_id, user_id, kind, amount, occurred_on, description, is_initial)
            VALUES (?, ?, 'deposit', ?, ?, 'Бастапқы салым', 1)
            """,
            (account_id, g.user["id"], initial, start.isoformat()),
        )
        rebuild_savings_account(db, account_id)
        db.commit()
        flash("Жинақ шоты ашылды.", "success")
        return redirect(url_for("savings"))

    return redirect(url_for("savings"))


@app.route("/savings/<int:account_id>/edit", methods=["POST"])
@login_required
def edit_savings(account_id):
    db = get_db()
    account = db.execute(
        "SELECT * FROM savings_accounts WHERE id = ? AND user_id = ?",
        (account_id, g.user["id"]),
    ).fetchone()
    if account is None:
        flash("Жинақ шоты табылмады.", "error")
        return redirect(url_for("savings"))

    name = request.form.get("name", "").strip()
    rate = parse_annual_rate(request.form.get("annual_rate"))
    start = parse_date(request.form.get("start_date"))
    earliest = db.execute(
        "SELECT MIN(occurred_on) FROM savings_operations WHERE account_id = ?",
        (account_id,),
    ).fetchone()[0]

    if not name or len(name) > 50:
        flash("Жинақ атауын енгізіңіз (50 таңбаға дейін).", "error")
    elif rate is None:
        flash("Жылдық пайыз 0 мен 100 аралығында болуы керек.", "error")
    elif start is None or start > date.today():
        flash("Салым басталған күні дұрыс енгізілмеген.", "error")
    elif earliest and earliest < start.isoformat():
        flash("Басталу күні салым операцияларынан кейін болмауы керек.", "error")
    else:
        db.execute(
            """
            UPDATE savings_accounts
            SET name = ?, annual_rate = ?, start_date = ?
            WHERE id = ? AND user_id = ?
            """,
            (name, str(rate), start.isoformat(), account_id, g.user["id"]),
        )
        rebuild_savings_account(db, account_id)
        db.commit()
        flash("Жинақ шоты жаңартылды; пайыз қайта есептелді.", "success")

    return redirect(url_for("savings"))


def apply_savings_operation(account_id, kind):
    db = get_db()
    account = db.execute(
        "SELECT * FROM savings_accounts WHERE id = ? AND user_id = ?",
        (account_id, g.user["id"]),
    ).fetchone()
    if account is None:
        flash("Жинақ шоты табылмады.", "error")
        return redirect(url_for("savings"))

    amount = parse_amount(request.form.get("amount"))
    occurred_on = parse_date(request.form.get("occurred_on"))
    start = date.fromisoformat(account["start_date"])

    if amount is None:
        flash("Сома дұрыс енгізілмеген.", "error")
    elif occurred_on is None or occurred_on > date.today() or occurred_on < start:
        flash("Операция күні дұрыс енгізілмеген.", "error")
    else:
        description = request.form.get("description", "").strip()[:300]
        db.execute(
            """
            INSERT INTO savings_operations
                (account_id, user_id, kind, amount, occurred_on, description, is_initial)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (account_id, g.user["id"], kind, amount, occurred_on.isoformat(), description),
        )
        result = rebuild_savings_account(db, account_id)
        if result is None or result["minimum_balance"] < 0:
            db.rollback()
            flash("Жинақта жеткілікті қаражат жоқ.", "error")
        else:
            db.commit()
            flash(
                "Салым қосылды." if kind == "deposit" else "Ақша жинақтан алынды.",
                "success",
            )

    return redirect(url_for("savings"))


@app.route("/savings/<int:account_id>/deposit", methods=["POST"])
@login_required
def deposit_savings(account_id):
    return apply_savings_operation(account_id, "deposit")


@app.route("/savings/<int:account_id>/withdraw", methods=["POST"])
@login_required
def withdraw_savings(account_id):
    return apply_savings_operation(account_id, "withdrawal")


@app.route("/savings/<int:account_id>/delete", methods=["POST"])
@login_required
def delete_savings(account_id):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM savings_accounts WHERE id = ? AND user_id = ?",
        (account_id, g.user["id"]),
    )
    db.commit()
    if cursor.rowcount:
        flash("Жинақ шоты жойылды.", "success")
    else:
        flash("Жинақ шоты табылмады.", "error")
    return redirect(url_for("savings"))


# ---------------------------------------------------------------------------
# Жеке профиль және баптаулар
# ---------------------------------------------------------------------------

PHONE_RE = re.compile(r"^\+?[\d\s()\-]{7,24}$")
VALID_DATE_FORMATS = ("dmy", "iso")


def split_name(name):
    """'Айдана Серікқызы' -> ('Айдана', 'Серікқызы')."""
    parts = (name or "").split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def count_active_months(created_at):
    """Тіркелгі қанша толық ай белсенді екенін есептейді."""
    try:
        started = datetime.strptime((created_at or "")[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return 0

    today = date.today()
    months = (today.year - started.year) * 12 + (today.month - started.month)
    if today.day < started.day:
        months -= 1
    return max(0, months)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    stats = fetch_user_stats(db, g.user["id"])
    months_active = count_active_months(g.user["created_at"])
    today = date.today().isoformat()

    if request.method == "GET":
        first_name, last_name = split_name(g.user["username"])
        form = {
            "first_name": first_name,
            "last_name": last_name,
            "email": g.user["email"],
            "phone": g.user["phone"],
            "city": g.user["city"],
            "birth_date": g.user["birth_date"],
            "bio": g.user["bio"],
        }
        return render_template(
            "profile.html",
            form=form,
            stats=stats,
            months_active=months_active,
            today=today,
        )

    form = {
        "first_name": request.form.get("first_name", "").strip(),
        "last_name": request.form.get("last_name", "").strip(),
        "email": request.form.get("email", "").strip().lower(),
        "phone": request.form.get("phone", "").strip(),
        "city": request.form.get("city", "").strip(),
        "birth_date": request.form.get("birth_date", "").strip(),
        "bio": request.form.get("bio", "").strip(),
    }

    error = None
    if not 2 <= len(form["first_name"]) <= 40:
        error = "Атыңызды енгізіңіз (2-ден 40 таңбаға дейін)."
    elif len(form["last_name"]) > 40:
        error = "Тегіңіз 40 таңбадан аспауы керек."
    elif not form["email"] or len(form["email"]) > 254 or not EMAIL_RE.match(form["email"]):
        error = "Email мекенжайы дұрыс емес."
    elif form["phone"] and (
        not PHONE_RE.match(form["phone"])
        or not 10 <= len(re.sub(r"\D", "", form["phone"])) <= 15
    ):
        error = "Телефон нөмірін +7 (707) 245-18-36 форматында енгізіңіз."
    elif len(form["city"]) > 60:
        error = "Қала атауы 60 таңбадан аспауы керек."
    elif len(form["bio"]) > 300:
        error = "Өзіңіз туралы мәтін 300 таңбадан аспауы керек."

    birth_date = None
    if error is None and form["birth_date"]:
        try:
            birth_date = datetime.strptime(form["birth_date"], "%Y-%m-%d").date()
        except ValueError:
            error = "Туған күні дұрыс енгізілмеген."
        else:
            if birth_date > date.today():
                error = "Туған күні болашақта болмауы керек."
            elif birth_date.year < 1900:
                error = "Туған күні 1900 жылдан кейін болуы керек."

    username = " ".join(part for part in (form["first_name"], form["last_name"]) if part)
    if error is None and not 2 <= len(username) <= 50:
        error = "Аты-жөні 50 таңбадан аспауы керек."

    if error is None:
        taken = db.execute(
            "SELECT 1 FROM users WHERE email = ? AND id <> ?",
            (form["email"], g.user["id"]),
        ).fetchone()
        if taken:
            error = "Бұл email басқа тіркелгіде қолданылады."

    if error:
        flash(error, "error")
        return render_template(
            "profile.html",
            form=form,
            stats=stats,
            months_active=months_active,
            today=today,
        ), 400

    try:
        db.execute(
            """
            UPDATE users
            SET username = ?, email = ?, phone = ?, city = ?, birth_date = ?, bio = ?
            WHERE id = ?
            """,
            (
                username,
                form["email"],
                form["phone"],
                form["city"],
                birth_date.isoformat() if birth_date else "",
                form["bio"],
                g.user["id"],
            ),
        )
        db.commit()
    except sqlite3.IntegrityError:
        flash("Бұл email басқа тіркелгіде қолданылады.", "error")
        return render_template(
            "profile.html",
            form=form,
            stats=stats,
            months_active=months_active,
            today=today,
        ), 400

    flash("Профиль өзгерістері сақталды.", "success")
    return redirect(url_for("profile"))


@app.route("/settings", methods=["GET"])
@login_required
def settings():
    active_tab = request.args.get("tab", "general")
    if active_tab not in ("general", "security", "data"):
        active_tab = "general"

    return render_template(
        "settings.html",
        stats=fetch_user_stats(get_db(), g.user["id"]),
        active_tab=active_tab,
    )


@app.route("/settings/preferences", methods=["POST"])
@login_required
def save_preferences():
    date_format = request.form.get("date_format", "dmy")
    if date_format not in VALID_DATE_FORMATS:
        date_format = "dmy"

    hide_balances = 1 if request.form.get("hide_balances") in ("1", "on", "true") else 0

    db = get_db()
    db.execute(
        "UPDATE users SET hide_balances = ?, date_format = ? WHERE id = ?",
        (hide_balances, date_format, g.user["id"]),
    )
    db.commit()

    flash("Баптаулар сақталды.", "success")
    return redirect(url_for("settings", tab="general") + "#general")


@app.route("/settings/password", methods=["POST"])
@login_required
def change_password():
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    password_confirm = request.form.get("new_password_confirm", "")

    db = get_db()
    row = db.execute(
        "SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)
    ).fetchone()

    error = None
    if not check_password_hash(row["password_hash"], current_password):
        error = "Ағымдағы құпиясөз қате."
    elif len(new_password) < 8:
        error = "Жаңа құпиясөз кемінде 8 таңбадан тұруы керек."
    elif len(new_password) > 128:
        error = "Жаңа құпиясөз тым ұзын."
    elif new_password == current_password:
        error = "Жаңа құпиясөз ағымдағыдан өзгеше болуы керек."
    elif new_password != password_confirm:
        error = "Жаңа құпиясөздер сәйкес келмейді."

    if error:
        flash(error, "error")
        return redirect(url_for("settings", tab="security") + "#security")

    db.execute(
        "UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?",
        (
            generate_password_hash(new_password),
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            g.user["id"],
        ),
    )
    db.commit()

    flash("Құпиясөз өзгертілді.", "success")
    return redirect(url_for("settings", tab="security") + "#security")


@app.route("/settings/export", methods=["GET"])
@login_required
def export_transactions():
    db = get_db()
    rows = db.execute(
        """
        SELECT t.created_at, t.type, t.category, t.amount, t.description,
               c.name AS card_name, c.last_four AS card_last_four
        FROM transactions t
        LEFT JOIN cards c ON c.id = t.card_id
        WHERE t.user_id = ?
        ORDER BY t.created_at, t.id
        """,
        (g.user["id"],),
    ).fetchall()

    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Күн", "Түрі", "Санат", "Сома, ₸", "Карта", "Сипаттама"])

    for row in rows:
        amount = f"{row['amount'] // 100}.{row['amount'] % 100:02d}"
        if row["card_name"]:
            card = f"{row['card_name']} · •••• {row['card_last_four']}"
        else:
            card = "Картасыз"
        writer.writerow(
            [
                row["created_at"],
                "Кіріс" if row["type"] == "income" else "Шығыс",
                ALL_CATEGORIES.get(row["category"], row["category"]),
                amount,
                card,
                row["description"],
            ]
        )

    response = Response(buffer.getvalue().encode("utf-8"))
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f"attachment; filename=tenge-operations-{date.today().isoformat()}.csv"
    )
    return response


@app.route("/settings/delete", methods=["POST"])
@login_required
def delete_account():
    db = get_db()
    password = request.form.get("password", "")
    confirmation = request.form.get("confirmation", "").strip()

    row = db.execute(
        "SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)
    ).fetchone()

    if not check_password_hash(row["password_hash"], password):
        flash("Құпиясөз қате. Тіркелгі жойылмады.", "error")
        return redirect(url_for("settings", tab="data") + "#data")

    if confirmation.upper() != "ЖОЮ":
        flash("Растау үшін «ЖОЮ» деп жазыңыз.", "error")
        return redirect(url_for("settings", tab="data") + "#data")

    db.execute("DELETE FROM users WHERE id = ?", (g.user["id"],))
    db.commit()
    session.clear()

    flash("Тіркелгі және барлық деректер жойылды.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Санаттар
# ---------------------------------------------------------------------------


@app.route("/categories", methods=["GET"])
@login_required
def categories():
    db = get_db()
    tab = request.args.get("tab", "expense")
    if tab not in ("expense", "income", "archive"):
        tab = "expense"

    counts = {
        row["type"]: row["n"]
        for row in db.execute(
            """
            SELECT type, COUNT(*) AS n FROM user_categories
            WHERE user_id = ? AND archived = 0 GROUP BY type
            """,
            (g.user["id"],),
        )
    }
    counts["expense"] = counts.get("expense", 0)
    counts["income"] = counts.get("income", 0)
    counts["archive"] = db.execute(
        "SELECT COUNT(*) FROM user_categories WHERE user_id = ? AND archived = 1",
        (g.user["id"],),
    ).fetchone()[0]

    if tab == "archive":
        where = "c.archived = 1"
    else:
        where = f"c.archived = 0 AND c.type = '{tab}'"

    rows = db.execute(
        f"""
        SELECT c.*, COUNT(t.id) AS tx_count,
               COALESCE(SUM(CASE WHEN t.type = 'expense' THEN t.amount END), 0) AS spent,
               COALESCE(SUM(CASE WHEN t.type = 'income' THEN t.amount END), 0) AS earned
        FROM user_categories c
        LEFT JOIN transactions t
               ON t.user_id = c.user_id AND t.category = c.key AND t.type = c.type
        WHERE c.user_id = ? AND {where}
        GROUP BY c.id
        ORDER BY c.is_default DESC, c.name
        """,
        (g.user["id"],),
    ).fetchall()

    edit_id = request.args.get("edit", type=int)
    selected = None
    for row in rows:
        if row["id"] == edit_id:
            selected = row
    if selected is None and rows:
        selected = rows[0]

    form = {
        "id": selected["id"] if selected else "",
        "name": selected["name"] if selected else "",
        "type": selected["type"] if selected else tab,
        "icon": selected["icon"] if selected else "tag",
        "color": selected["color"] if selected else CATEGORY_COLORS[0],
        "archived": bool(selected["archived"]) if selected else False,
    }

    return render_template(
        "categories.html",
        tab=tab,
        counts=counts,
        categories=rows,
        icon_choices=CATEGORY_ICONS,
        color_choices=CATEGORY_COLORS,
        form=form,
    )


@app.route("/categories/new", methods=["POST"])
@login_required
def create_category():
    db = get_db()
    name = request.form.get("name", "").strip()
    type_name = request.form.get("type", "expense")
    icon = request.form.get("icon", "tag")
    color = request.form.get("color", CATEGORY_COLORS[0])

    if not 2 <= len(name) <= 40:
        flash("Сат атауын енгізіңіз (2-ден 40 таңбаға дейін).", "error")
    elif type_name not in CATEGORIES:
        flash("Санат түрін таңдаңыз.", "error")
    elif icon not in CATEGORY_ICONS:
        flash("Белгіше таңдалмады.", "error")
    elif not HEX_COLOR_RE.match(color):
        flash("Түс дұрыс енгізілмеген.", "error")
    else:
        key = "cat_" + secrets.token_hex(4)
        db.execute(
            """
            INSERT INTO user_categories (user_id, key, name, type, icon, color)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (g.user["id"], key, name, type_name, icon, color),
        )
        db.commit()
        flash("Санат қосылды.", "success")
        return redirect(url_for("categories", tab=type_name, edit=""))

    return redirect(url_for("categories", tab=type_name if type_name in CATEGORIES else "expense"))


@app.route("/categories/<int:category_id>/edit", methods=["POST"])
@login_required
def edit_category(category_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM user_categories WHERE id = ? AND user_id = ?",
        (category_id, g.user["id"]),
    ).fetchone()
    if row is None:
        flash("Санат табылмады.", "error")
        return redirect(url_for("categories"))

    name = request.form.get("name", "").strip()
    type_name = request.form.get("type", row["type"])
    icon = request.form.get("icon", row["icon"])
    color = request.form.get("color", row["color"])
    archived = request.form.get("archived") in ("1", "on", "true")

    if not 2 <= len(name) <= 40:
        flash("Сат атауын енгізіңіз (2-ден 40 таңбаға дейін).", "error")
    elif type_name not in CATEGORIES:
        flash("Санат түрін таңдаңыз.", "error")
    elif icon not in CATEGORY_ICONS:
        flash("Белгіше таңдалмады.", "error")
    elif not HEX_COLOR_RE.match(color):
        flash("Түс дұрыс енгізілмеген.", "error")
    elif type_name != row["type"] and db.execute(
        "SELECT 1 FROM transactions WHERE user_id = ? AND category = ? AND type = ? LIMIT 1",
        (g.user["id"], row["key"], row["type"]),
    ).fetchone():
        flash("Операциялар бар санаттың түрін өзгертуге болмайды.", "error")
    else:
        db.execute(
            """
            UPDATE user_categories
            SET name = ?, type = ?, icon = ?, color = ?, archived = ?
            WHERE id = ? AND user_id = ?
            """,
            (name, type_name, icon, color, int(archived), category_id, g.user["id"]),
        )
        db.commit()
        flash("Санат сақталды.", "success")

    target_tab = "archive" if archived else type_name
    return redirect(url_for("categories", tab=target_tab, edit=category_id))


@app.route("/categories/<int:category_id>/delete", methods=["POST"])
@login_required
def delete_category(category_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM user_categories WHERE id = ? AND user_id = ?",
        (category_id, g.user["id"]),
    ).fetchone()
    if row is None:
        flash("Санат табылмады.", "error")
        return redirect(url_for("categories"))
    if row["is_default"]:
        flash("Стандартты санатты жоюға болмайды, оны архивтеуге болады.", "error")
        return redirect(url_for("categories", tab=row["type"]))
    if db.execute(
        "SELECT 1 FROM transactions WHERE user_id = ? AND category = ? AND type = ? LIMIT 1",
        (g.user["id"], row["key"], row["type"]),
    ).fetchone():
        flash("Операциялар бар санатты жоюға болмайды. Оны архивтеңіз.", "error")
        return redirect(url_for("categories", tab=row["type"]))

    db.execute(
        """
        DELETE FROM budgets
        WHERE user_id = ? AND category_key = ? AND category_type = ?
        """,
        (g.user["id"], row["key"], row["type"]),
    )
    db.execute(
        "DELETE FROM user_categories WHERE id = ? AND user_id = ?",
        (category_id, g.user["id"]),
    )
    db.commit()
    flash("Санат жойылды.", "success")
    return redirect(url_for("categories", tab=row["type"]))


# ---------------------------------------------------------------------------
# Статистика
# ---------------------------------------------------------------------------


@app.route("/statistics", methods=["GET"])
@login_required
def statistics():
    db = get_db()
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    bounds = month_bounds(month)
    if bounds is None:
        month = date.today().strftime("%Y-%m")
        bounds = month_bounds(month)
    month_start, month_end, year, month_number = bounds

    current = db.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income'  THEN amount END), 0) AS income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount END), 0) AS expense,
            COUNT(*) AS tx_count
        FROM transactions
        WHERE user_id = ? AND created_at BETWEEN ? AND ?
        """,
        (g.user["id"], month_start.isoformat(), month_end.isoformat()),
    ).fetchone()

    prev_bounds = month_bounds(shift_month(month, -1))
    previous = db.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income'  THEN amount END), 0) AS income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount END), 0) AS expense
        FROM transactions
        WHERE user_id = ? AND created_at BETWEEN ? AND ?
        """,
        (g.user["id"], prev_bounds[0].isoformat(), prev_bounds[1].isoformat()),
    ).fetchone()

    def change(now, before):
        return round((now - before) * 100 / before) if before else None

    income = current["income"]
    expense = current["expense"]
    net = income - expense
    stats = {
        "income": income,
        "expense": expense,
        "net": net,
        "tx_count": current["tx_count"],
        "savings_rate": round(net * 100 / income) if income else 0,
        "income_change": change(income, previous["income"]),
        "expense_change": change(expense, previous["expense"]),
    }

    months = []
    for offset in range(-5, 1):
        key = shift_month(month, offset)
        item_bounds = month_bounds(key)
        totals = db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN type = 'income'  THEN amount END), 0) AS income,
                COALESCE(SUM(CASE WHEN type = 'expense' THEN amount END), 0) AS expense
            FROM transactions
            WHERE user_id = ? AND created_at BETWEEN ? AND ?
            """,
            (g.user["id"], item_bounds[0].isoformat(), item_bounds[1].isoformat()),
        ).fetchone()
        months.append(
            {
                "key": key,
                "label": MONTHS_SHORT_KZ[item_bounds[3] - 1],
                "income": totals["income"],
                "expense": totals["expense"],
            }
        )

    shares = []
    if expense:
        for row in db.execute(
            """
            SELECT t.category, SUM(t.amount) AS amount, COUNT(*) AS tx_count,
                   c.name, c.icon, c.color
            FROM transactions t
            LEFT JOIN user_categories c
                   ON c.user_id = t.user_id AND c.key = t.category AND c.type = t.type
            WHERE t.user_id = ? AND t.type = 'expense'
              AND t.created_at BETWEEN ? AND ?
            GROUP BY t.category
            ORDER BY amount DESC
            LIMIT 8
            """,
            (g.user["id"], month_start.isoformat(), month_end.isoformat()),
        ):
            shares.append(
                {
                    "name": row["name"] or all_categories_fallback(row["category"]),
                    "amount": row["amount"],
                    "percent": round(row["amount"] * 100 / expense, 1),
                    "color": row["color"] or CATEGORY_COLORS[len(shares) % len(CATEGORY_COLORS)],
                    "icon": row["icon"] or "tag",
                }
            )

    insights = []
    if shares:
        top = shares[0]
        insights.append(
            {
                "title": "Ең үлкен шығын санаты",
                "text": f"{top['name']}: {top['amount']} ₸ — барлық шығынның {top['percent']}%-ы.",
                "icon": "wallet",
                "tone": "orange",
            }
        )
    if income:
        insights.append(
            {
                "title": "Жинақ қарқыны",
                "text": (
                    f"Ай ішіндегі кірістің {stats['savings_rate']}%-ы сақталды."
                    if net >= 0
                    else f"Шығын кірістен {abs(net)} ₸ асып түсті."
                ),
                "icon": "piggy-bank" if net >= 0 else "alert-triangle",
                "tone": "green" if net >= 0 else "orange",
            }
        )
    if stats["expense_change"] is not None:
        insights.append(
            {
                "title": "Өткен аймен салыстыру",
                "text": (
                    f"Шығын өткен айға қарағанда {abs(stats['expense_change'])}%-ға "
                    f"{'өсті' if stats['expense_change'] > 0 else 'азайды'}."
                ),
                "icon": "arrow-up-right" if stats["expense_change"] > 0 else "arrow-down-left",
                "tone": "orange" if stats["expense_change"] > 0 else "green",
            }
        )
    if not insights:
        insights.append(
            {
                "title": "Деректер жиналуда",
                "text": "Операция қосқан сайын осында нақты қаржылық талдау пайда болады.",
                "icon": "info",
                "tone": "cyan",
            }
        )

    return render_template(
        "statistics.html",
        month=month,
        month_label=month_label(year, month_number),
        stats=stats,
        months=months,
        shares=shares,
        insights=insights,
        prev_month=shift_month(month, -1),
        next_month=shift_month(month, 1),
        has_prev=True,
        has_next=month < date.today().strftime("%Y-%m"),
    )


def all_categories_fallback(key):
    return ALL_CATEGORIES.get(key, key)


# ---------------------------------------------------------------------------
# Қаржылық есептер
# ---------------------------------------------------------------------------

REPORT_TYPES = (
    {"key": "summary", "label": "Жалпы шолу", "description": "Барлық кіріс пен шығын", "icon": "database"},
    {"key": "category", "label": "Санаттар бойынша", "description": "Санаттардың үлесі", "icon": "wallet"},
    {"key": "card", "label": "Карталар бойынша", "description": "Әр картаның ағыны", "icon": "credit-card"},
    {"key": "transactions", "label": "Операциялар тізімі", "description": "Егжей-тегжейлі тарих", "icon": "arrow-left-right"},
)


def report_filters_from_request():
    today = date.today()
    filters = {
        "type": request.values.get("type", "summary"),
        "date_from": request.values.get("date_from", today.replace(day=1).isoformat()),
        "date_to": request.values.get("date_to", today.isoformat()),
        "card_id": request.values.get("card", request.values.get("card_id", "")),
        "format": request.values.get("format", "csv"),
    }
    if filters["type"] not in {item["key"] for item in REPORT_TYPES}:
        filters["type"] = "summary"
    if filters["format"] not in ("csv", "html"):
        filters["format"] = "csv"
    return filters


def validate_report_filters(db, filters):
    try:
        start = datetime.strptime(filters["date_from"], "%Y-%m-%d").date()
        end = datetime.strptime(filters["date_to"], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None, "Күндер дұрыс енгізілмеген."
    if start > end:
        return None, "Басталу күні аяқталу күнінен кейін болмауы керек."
    if (end - start).days > 3660:
        return None, "Есеп кезеңі 10 жылдан аспауы керек."

    card_id = None
    if filters["card_id"]:
        if not str(filters["card_id"]).isdigit():
            return None, "Карта таңдалмады."
        card_id = int(filters["card_id"])
        exists = db.execute(
            "SELECT 1 FROM cards WHERE id = ? AND user_id = ?",
            (card_id, g.user["id"]),
        ).fetchone()
        if exists is None:
            return None, "Карта табылмады."
    return (start, end, card_id), None


def build_report(db, user_id, filters, bounds):
    start, end, card_id = bounds
    where = ["user_id = ?", "created_at BETWEEN ? AND ?"]
    params = [user_id, start.isoformat(), end.isoformat()]
    if card_id is not None:
        where.append("card_id = ?")
        params.append(card_id)
    sql_where = " AND ".join(where)

    totals = db.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income'  THEN amount END), 0) AS income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount END), 0) AS expense,
            COUNT(*) AS tx_count
        FROM transactions WHERE {sql_where}
        """,
        params,
    ).fetchone()

    report_type = filters["type"]
    if report_type in ("summary", "transactions"):
        headers = ["Күн", "Түрі", "Санат", "Сома, ₸", "Карта", "Сипаттама"]
        rows = []
        for row in db.execute(
            f"""
            SELECT t.*, c.name AS card_name, c.last_four
            FROM transactions t LEFT JOIN cards c ON c.id = t.card_id
            WHERE {sql_where.replace('user_id =', 't.user_id =')
                            .replace('created_at BETWEEN', 't.created_at BETWEEN')
                            .replace('card_id =', 't.card_id =')}
            ORDER BY t.created_at, t.id
            """,
            params,
        ):
            card = f"{row['card_name']} •••• {row['last_four']}" if row["card_name"] else "Картасыз"
            rows.append(
                [
                    row["created_at"],
                    "Кіріс" if row["type"] == "income" else "Шығыс",
                    all_categories_fallback(row["category"]),
                    f"{row['amount'] / 100:.2f}",
                    card,
                    row["description"],
                ]
            )
    elif report_type == "category":
        headers = ["Түрі", "Санат", "Операциялар", "Сома, ₸"]
        rows = []
        for row in db.execute(
            f"""
            SELECT type, category, COUNT(*) AS n, SUM(amount) AS total
            FROM transactions WHERE {sql_where}
            GROUP BY type, category ORDER BY total DESC
            """,
            params,
        ):
            rows.append(
                [
                    "Кіріс" if row["type"] == "income" else "Шығыс",
                    all_categories_fallback(row["category"]),
                    row["n"],
                    f"{row['total'] / 100:.2f}",
                ]
            )
    else:
        headers = ["Карта", "Операциялар", "Кіріс, ₸", "Шығыс, ₸", "Өзгеріс, ₸"]
        rows = []
        for row in db.execute(
            f"""
            SELECT COALESCE(c.name, 'Картасыз') AS card_name, COUNT(*) AS n,
                   COALESCE(SUM(CASE WHEN t.type = 'income'  THEN t.amount END), 0) AS income,
                   COALESCE(SUM(CASE WHEN t.type = 'expense' THEN t.amount END), 0) AS expense
            FROM transactions t LEFT JOIN cards c ON c.id = t.card_id
            WHERE {sql_where.replace('user_id =', 't.user_id =')
                            .replace('created_at BETWEEN', 't.created_at BETWEEN')
                            .replace('card_id =', 't.card_id =')}
            GROUP BY t.card_id
            ORDER BY income DESC, expense DESC
            """,
            params,
        ):
            rows.append(
                [
                    row["card_name"],
                    row["n"],
                    f"{row['income'] / 100:.2f}",
                    f"{row['expense'] / 100:.2f}",
                    f"{(row['income'] - row['expense']) / 100:.2f}",
                ]
            )

    type_label = next(item["label"] for item in REPORT_TYPES if item["key"] == report_type)
    return {
        "title": f"{type_label} · {start.isoformat()} — {end.isoformat()}",
        "income": totals["income"],
        "expense": totals["expense"],
        "net": totals["income"] - totals["expense"],
        "tx_count": totals["tx_count"],
        "headers": headers,
        "rows": rows,
        "note": (
            f"Кезең: {start.isoformat()} – {end.isoformat()} · "
            f"{'Барлық карталар' if card_id is None else 'Таңдалған карта'}"
        ),
    }


def report_response(preview, fmt, filename):
    if fmt == "html":
        html = render_template(
            "report_print.html", preview=preview, generated_at=datetime.now().strftime("%d.%m.%Y %H:%M")
        )
        response = Response(html)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
    else:
        buffer = io.StringIO()
        buffer.write("\ufeff")
        writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
        writer.writerow([preview["title"]])
        writer.writerow(["Кіріс, ₸", f"{preview['income'] / 100:.2f}"])
        writer.writerow(["Шығыс, ₸", f"{preview['expense'] / 100:.2f}"])
        writer.writerow(["Таза жинақ, ₸", f"{preview['net'] / 100:.2f}"])
        writer.writerow([])
        writer.writerow(preview["headers"])
        writer.writerows(preview["rows"])
        response = Response(buffer.getvalue().encode("utf-8"))
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}.csv"
    return response


@app.route("/reports", methods=["GET"])
@login_required
def reports():
    db = get_db()
    filters = report_filters_from_request()
    bounds, error = validate_report_filters(db, filters)
    preview = build_report(db, g.user["id"], filters, bounds) if bounds else None
    if error:
        flash(error, "error")

    history = db.execute(
        """
        SELECT id, title, report_type, date_from, date_to, format, created_at
        FROM reports WHERE user_id = ? ORDER BY id DESC LIMIT 10
        """,
        (g.user["id"],),
    ).fetchall()

    return render_template(
        "reports.html",
        filters=filters,
        report_types=REPORT_TYPES,
        cards=fetch_cards(db, g.user["id"]),
        preview=preview,
        reports=history,
        filters_valid=bounds is not None,
    )


@app.route("/reports/download", methods=["POST"])
@login_required
def download_report():
    db = get_db()
    filters = report_filters_from_request()
    bounds, error = validate_report_filters(db, filters)
    if error:
        flash(error, "error")
        return redirect(url_for("reports"))

    preview = build_report(db, g.user["id"], filters, bounds)
    title = next(item["label"] for item in REPORT_TYPES if item["key"] == filters["type"])
    db.execute(
        """
        INSERT INTO reports (user_id, title, report_type, date_from, date_to, card_id, format)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["id"],
            title,
            filters["type"],
            filters["date_from"],
            filters["date_to"],
            int(filters["card_id"]) if filters["card_id"] else None,
            filters["format"],
        ),
    )
    db.commit()
    add_notification(
        db,
        g.user["id"],
        "report",
        f"{title} есебі дайын",
        f"{filters['date_from']} – {filters['date_to']} кезеңі үшін есеп құрылды.",
        url_for("reports"),
    )
    db.commit()
    return report_response(
        preview,
        filters["format"],
        f"tenge-{filters['type']}-{date.today().isoformat()}",
    )


@app.route("/reports/<int:report_id>/download", methods=["GET"])
@login_required
def download_saved_report(report_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM reports WHERE id = ? AND user_id = ?",
        (report_id, g.user["id"]),
    ).fetchone()
    if row is None:
        flash("Есеп табылмады.", "error")
        return redirect(url_for("reports"))

    filters = {
        "type": row["report_type"],
        "format": row["format"],
        "card_id": str(row["card_id"] or ""),
    }
    bounds, error = validate_report_filters(
        db,
        {
            "type": filters["type"],
            "format": filters["format"],
            "card_id": filters["card_id"],
            "date_from": row["date_from"],
            "date_to": row["date_to"],
        },
    )
    if error:
        flash(error, "error")
        return redirect(url_for("reports"))

    preview = build_report(db, g.user["id"], filters, bounds)
    return report_response(
        preview, filters["format"], f"tenge-{filters['type']}-{row['date_to']}"
    )


@app.route("/reports/<int:report_id>/delete", methods=["POST"])
@login_required
def delete_report(report_id):
    db = get_db()
    cursor = db.execute(
        "DELETE FROM reports WHERE id = ? AND user_id = ?", (report_id, g.user["id"])
    )
    db.commit()
    flash("Есеп жойылды." if cursor.rowcount else "Есеп табылмады.",
          "success" if cursor.rowcount else "error")
    return redirect(url_for("reports"))


# ---------------------------------------------------------------------------
# Бюджеттер
# ---------------------------------------------------------------------------


def fetch_budgets(db, user_id, month, status=None):
    bounds = month_bounds(month)
    rows = db.execute(
        """
        SELECT b.*, c.name AS category_name, c.icon, c.color,
               COALESCE((
                   SELECT SUM(t.amount) FROM transactions t
                   WHERE t.user_id = b.user_id AND t.type = 'expense'
                     AND t.category = b.category_key
                     AND t.created_at BETWEEN b.start_date AND b.end_date
               ), 0) AS spent
        FROM budgets b
        LEFT JOIN user_categories c
               ON c.user_id = b.user_id AND c.key = b.category_key AND c.type = b.category_type
        WHERE b.user_id = ? AND b.start_date BETWEEN ? AND ?
        ORDER BY b.start_date, b.id
        """,
        (user_id, bounds[0].isoformat(), bounds[1].isoformat()),
    ).fetchall()

    result = []
    today = date.today().isoformat()
    for row in rows:
        amount = row["amount"]
        spent = row["spent"]
        remaining = max(0, amount - spent)
        percent = min(100, round(spent * 100 / amount)) if amount else 0
        item = dict(row)
        item.update(
            {
                "spent": spent,
                "remaining": remaining,
                "percent": percent,
                "status": "active" if row["end_date"] >= today else "completed",
                "notified": bool(row["notified_at"]),
                "icon": row["icon"] or "tag",
                "color": row["color"] or CATEGORY_COLORS[0],
            }
        )
        if status is None or item["status"] == status:
            result.append(item)
    return result


@app.route("/budgets", methods=["GET"])
@login_required
def budgets():
    db = get_db()
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    if month_bounds(month) is None:
        month = date.today().strftime("%Y-%m")
    status = request.args.get("status", "active")
    if status not in ("active", "completed"):
        status = "active"

    refresh_recurring_budgets(db, g.user["id"], month)
    all_rows = fetch_budgets(db, g.user["id"], month)
    items = fetch_budgets(db, g.user["id"], month, status)
    counts = {
        "active": sum(1 for row in all_rows if row["status"] == "active"),
        "completed": sum(1 for row in all_rows if row["status"] == "completed"),
    }
    planned = sum(row["amount"] for row in all_rows)
    spent = sum(row["spent"] for row in all_rows)
    totals = {
        "planned": planned,
        "spent": spent,
        "remaining": max(0, planned - spent),
        "percent": min(100, round(spent * 100 / planned)) if planned else 0,
        "count": len(all_rows),
    }

    notify_budget_alerts(db, g.user["id"])
    alert = None
    for row in all_rows:
        if row["spent"] >= row["amount"] * row["alert_percent"] / 100:
            alert = {
                "title": f"{row['category_name'] or row['name']} бюджетінің {row['percent']}%-ы жұмсалды",
                "text": (
                    f"{row['amount'] - row['spent']} ₸ қалды. Келесі айға жоспар құрғанда "
                    "осы айдағы шығынды ескеріңіз."
                ),
            }
            break

    return render_template(
        "budgets.html",
        month=month,
        month_label=month_label(month_bounds(month)[2], month_bounds(month)[3]),
        status=status,
        totals=totals,
        budgets=items,
        counts=counts,
        prev_month=shift_month(month, -1),
        next_month=shift_month(month, 1),
        alert=alert,
    )


def budget_form_values(budget=None):
    if budget is None:
        month = request.args.get("month", date.today().strftime("%Y-%m"))
        if month_bounds(month) is None:
            month = date.today().strftime("%Y-%m")
        bounds = month_bounds(month)
        return {
            "name": "",
            "category": "",
            "amount": "",
            "start_date": bounds[0].isoformat(),
            "end_date": bounds[1].isoformat(),
            "alert_percent": "80",
            "notify": True,
            "repeat_monthly": False,
            "carry_over": False,
        }
    return {
        "name": budget["name"],
        "category": budget["category_key"],
        "amount": f"{budget['amount'] / 100:.2f}".replace(".", ","),
        "start_date": budget["start_date"],
        "end_date": budget["end_date"],
        "alert_percent": str(budget["alert_percent"]),
        "notify": bool(budget["notify"]),
        "repeat_monthly": bool(budget["repeat_monthly"]),
        "carry_over": bool(budget["carry_over"]),
    }


@app.route("/budgets/new", methods=["GET", "POST"])
@login_required
def new_budget():
    return budget_form(None)


@app.route("/budgets/<int:budget_id>/edit", methods=["GET", "POST"])
@login_required
def edit_budget(budget_id):
    db = get_db()
    budget = db.execute(
        "SELECT * FROM budgets WHERE id = ? AND user_id = ?",
        (budget_id, g.user["id"]),
    ).fetchone()
    if budget is None:
        flash("Бюджет табылмады.", "error")
        return redirect(url_for("budgets"))
    return budget_form(budget)


def budget_form(budget):
    db = get_db()
    requested_month = request.args.get("month", "")
    if budget is None and month_bounds(requested_month):
        requested_month = requested_month
    else:
        requested_month = ""
    categories = db.execute(
        """
        SELECT key, name, icon, color FROM user_categories
        WHERE user_id = ? AND type = 'expense' AND archived = 0
        ORDER BY is_default DESC, name
        """,
        (g.user["id"],),
    ).fetchall()

    if request.method == "GET":
        form = budget_form_values(budget)
        return render_template(
            "budget_form.html",
            budget=dict(budget) if budget else None,
            form=form,
            categories=categories,
            month=(budget["start_date"][:7] if budget else requested_month or date.today().strftime("%Y-%m")),
            preview=budget_preview(form, categories),
        )

    form = {
        "name": request.form.get("name", "").strip(),
        "category": request.form.get("category", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "start_date": request.form.get("start_date", "").strip(),
        "end_date": request.form.get("end_date", "").strip(),
        "alert_percent": request.form.get("alert_percent", "80"),
        "notify": request.form.get("notify") in ("1", "on", "true"),
        "repeat_monthly": request.form.get("repeat_monthly") in ("1", "on", "true"),
        "carry_over": request.form.get("carry_over") in ("1", "on", "true"),
    }
    category = None
    for row in categories:
        if row["key"] == form["category"]:
            category = row
    if category is None and budget:
        for row in db.execute(
            "SELECT key, name, icon, color FROM user_categories WHERE user_id = ? AND key = ? AND type = 'expense'",
            (g.user["id"], form["category"]),
        ):
            category = row

    amount = parse_amount(form["amount"])
    try:
        start = datetime.strptime(form["start_date"], "%Y-%m-%d").date()
        end = datetime.strptime(form["end_date"], "%Y-%m-%d").date()
    except ValueError:
        start = end = None
    error = None
    if not 2 <= len(form["name"]) <= 60:
        error = "Бюджет атауын енгізіңіз (2-ден 60 таңбаға дейін)."
    elif category is None:
        error = "Санатты таңдаңыз."
    elif amount is None:
        error = "Бюджет сомасын дұрыс енгізіңіз."
    elif start is None or end is None:
        error = "Күндерді дұрыс енгізіңіз."
    elif end < start:
        error = "Аяқталу күні басталу күнінен бұрын болмауы керек."
    elif (end - start).days > 366:
        error = "Бюджет кезеңі бір жылдан аспауы керек."
    elif form["alert_percent"] not in ("70", "80", "90"):
        error = "Ескерту шегін таңдаңыз."

    if error:
        flash(error, "error")
        return render_template(
            "budget_form.html",
            budget=dict(budget) if budget else None,
            form=form,
            categories=categories,
            month=(budget["start_date"][:7] if budget else requested_month or date.today().strftime("%Y-%m")),
            preview=budget_preview(form, categories),
        ), 400

    duplicate = db.execute(
        """
        SELECT 1 FROM budgets
        WHERE user_id = ? AND category_key = ? AND category_type = 'expense'
          AND start_date = ? AND id <> ?
        """,
        (g.user["id"], category["key"], start.isoformat(), budget["id"] if budget else -1),
    ).fetchone()
    if duplicate:
        flash("Осы санатқа аталған айда бюджет бар.", "error")
        return redirect(url_for("edit_budget", budget_id=budget["id"]) if budget else url_for("new_budget"))

    if budget:
        db.execute(
            """
            UPDATE budgets
            SET category_key = ?, name = ?, amount = ?, start_date = ?, end_date = ?,
                alert_percent = ?, notify = ?, repeat_monthly = ?, carry_over = ?, notified_at = ''
            WHERE id = ? AND user_id = ?
            """,
            (
                category["key"], form["name"], amount, start.isoformat(), end.isoformat(),
                int(form["alert_percent"]), int(form["notify"]), int(form["repeat_monthly"]),
                int(form["carry_over"]), budget["id"], g.user["id"],
            ),
        )
        flash("Бюджет сақталды.", "success")
    else:
        db.execute(
            """
            INSERT INTO budgets
                (user_id, category_key, category_type, name, amount, start_date, end_date,
                 alert_percent, notify, repeat_monthly, carry_over)
            VALUES (?, ?, 'expense', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                g.user["id"], category["key"], form["name"], amount, start.isoformat(),
                end.isoformat(), int(form["alert_percent"]), int(form["notify"]),
                int(form["repeat_monthly"]), int(form["carry_over"]),
            ),
        )
        flash("Бюджет құрылды.", "success")
    db.commit()
    return redirect(url_for("budgets", month=start.strftime("%Y-%m")))


def budget_preview(form, categories):
    amount = parse_amount(form.get("amount", ""))
    days = 0
    try:
        start = datetime.strptime(form.get("start_date", ""), "%Y-%m-%d").date()
        end = datetime.strptime(form.get("end_date", ""), "%Y-%m-%d").date()
        days = max(0, (end - start).days + 1)
    except ValueError:
        pass

    category_name = next(
        (row["name"] for row in categories if row["key"] == form.get("category")), "Санат таңдалмады"
    )
    try:
        alert_percent = int(form.get("alert_percent", 80))
    except (TypeError, ValueError):
        alert_percent = 80

    return {
        "name": form.get("name") or "Бюджет атауы",
        "category_name": category_name,
        "amount": amount or 0,
        "daily": (amount or 0) // days if days else 0,
        "threshold": (amount or 0) * alert_percent // 100,
        "days": days,
        "status_label": "Құруға дайын" if amount and days else "Мәндерді енгізіңіз",
    }


@app.route("/budgets/<int:budget_id>/delete", methods=["POST"])
@login_required
def delete_budget(budget_id):
    db = get_db()
    row = db.execute(
        "SELECT start_date FROM budgets WHERE id = ? AND user_id = ?",
        (budget_id, g.user["id"]),
    ).fetchone()
    cursor = db.execute(
        "DELETE FROM budgets WHERE id = ? AND user_id = ?", (budget_id, g.user["id"])
    )
    db.commit()
    flash("Бюджет жойылды." if cursor.rowcount else "Бюджет табылмады.",
          "success" if cursor.rowcount else "error")
    month = row["start_date"][:7] if row else date.today().strftime("%Y-%m")
    return redirect(url_for("budgets", month=month))


# ---------------------------------------------------------------------------
# Хабарландырулар
# ---------------------------------------------------------------------------


@app.route("/notifications", methods=["GET"])
@login_required
def notifications():
    db = get_db()
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "DELETE FROM notifications WHERE user_id = ? AND created_at < ?",
        (g.user["id"], cutoff),
    )
    db.commit()

    active_filter = request.args.get("filter", "all")
    if active_filter not in ("all", "unread", "budget", "security"):
        active_filter = "all"
    query = request.args.get("q", "").strip()[:100]

    counts = {
        "all": db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ?", (g.user["id"],)
        ).fetchone()[0],
        "unread": db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0", (g.user["id"],)
        ).fetchone()[0],
        "budget": db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND kind = 'budget'", (g.user["id"],)
        ).fetchone()[0],
        "security": db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND kind = 'security'", (g.user["id"],)
        ).fetchone()[0],
    }

    where = ["user_id = ?"]
    params = [g.user["id"]]
    if active_filter == "unread":
        where.append("is_read = 0")
    elif active_filter == "budget":
        where.append("kind = 'budget'")
    elif active_filter == "security":
        where.append("kind = 'security'")
    if query:
        where.append("(title LIKE ? OR body LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])

    rows = db.execute(
        f"""
        SELECT * FROM notifications WHERE {' AND '.join(where)}
        ORDER BY created_at DESC, id DESC LIMIT 100
        """,
        params,
    ).fetchall()

    notify_icons = {
        "budget": "alert-triangle",
        "report": "database",
        "security": "shield-check",
        "system": "info",
    }
    items = []
    for row in rows:
        try:
            created = datetime.strptime(row["created_at"][:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            created = datetime.now()
        if created.date() == date.today():
            time_label = f"Бүгін, {created.strftime('%H:%M')}"
        elif created.date() == date.today() - timedelta(days=1):
            time_label = f"Кеше, {created.strftime('%H:%M')}"
        else:
            time_label = created.strftime("%d.%m.%Y, %H:%M")
        item = dict(row)
        item["time_label"] = time_label
        item["icon"] = notify_icons.get(row["kind"], "info")
        items.append(item)

    prefs = {
        "notify_site": bool(g.user["notify_site"]),
        "notify_email": bool(g.user["notify_email"]),
        "notify_budget": bool(g.user["notify_budget"]),
    }
    return render_template(
        "notifications.html",
        notifications=items,
        counts=counts,
        filter=active_filter,
        q=query,
        prefs=prefs,
        unread_count=counts["unread"],
    )


@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def read_notification(notification_id):
    db = get_db()
    db.execute(
        "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
        (notification_id, g.user["id"]),
    )
    db.commit()
    return redirect(url_for("notifications", filter=request.form.get("filter", "all")))


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications():
    db = get_db()
    db.execute(
        "UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0",
        (g.user["id"],),
    )
    db.commit()
    flash("Барлық хабарландырулар оқылды деп белгіленді.", "success")
    return redirect(url_for("notifications"))


@app.route("/notifications/preferences", methods=["POST"])
@login_required
def notification_preferences():
    db = get_db()
    values = []
    for name in ("notify_site", "notify_email", "notify_budget"):
        values.append(1 if request.form.get(name) in ("1", "on", "true") else 0)
    db.execute(
        """
        UPDATE users SET notify_site = ?, notify_email = ?, notify_budget = ? WHERE id = ?
        """,
        (*values, g.user["id"]),
    )
    db.commit()
    flash("Хабарландыру баптаулары сақталды.", "success")
    return redirect(url_for("notifications"))


# ---------------------------------------------------------------------------
# Көмек және қолдау
# ---------------------------------------------------------------------------

FAQ_ITEMS = (
    {
        "id": "card-add",
        "topic": "cards",
        "q": "Картаны қалай қосамын?",
        "a": "«Карталар» бөліміне өтіп, «Карта қосу» түймесін таңдаңыз. Карта атауын, соңғы 4 санын және ағымдағы балансын енгізіңіз. Толық нөмір мен құпия код қажет емес.",
    },
    {
        "id": "card-delete",
        "topic": "cards",
        "q": "Картаны жойғанда операциялар қалай болады?",
        "a": "Карта жойылса да, оның операциялары жалпы тарихта сақталады. Есептер мен статистика бұрынғыдай есептеледі.",
    },
    {
        "id": "budget-limit",
        "topic": "budgets",
        "q": "Бюджеттің лимитін өзгерте аламын ба?",
        "a": "Иә. «Бюджеттер» бетіндегі бюджет картасындағы өңдеу түймесі арқылы соманы, кезеңді және ескерту шегін өзгертіңіз.",
    },
    {
        "id": "budget-repeat",
        "topic": "budgets",
        "q": "Қалған соманы келесі айға қалай көшіремін?",
        "a": "Бюджет құру кезінде «Келесі айда қайталау» және «Қалған соманы келесі айға көшіру» опцияларын қосыңыз. Жаңа бюджет автоматты түрде жасалады.",
    },
    {
        "id": "tx-edit",
        "topic": "budgets",
        "q": "Операцияны қате енгізсем не істеймін?",
        "a": "Операциялар тарихындағы жою түймесі арқылы жазбаны өшіріп, дұрыс мәні қайта енгізіңіз. Баланс пен бюджеттер автоматты түрде қайта есептеледі.",
    },
    {
        "id": "report-download",
        "topic": "budgets",
        "q": "Қаржылық есепті қалай жүктеймін?",
        "a": "«Есептер» бетінде кезең мен форматты таңдап, «Есепті жүктеу» түймесін басыңыз. Есеп CSV немесе баспаға дайын HTML түрінде алынады.",
    },
    {
        "id": "security-data",
        "topic": "security",
        "q": "Жеке деректерім қалай қорғалады?",
        "a": "Құпиясөз хэштеліп сақталады, барлық деректер тек сіздің тіркелгіңізге байланысты. Баптаулар бөлімінде деректерді экспорттап немесе тіркелгіні жоюға болады.",
    },
    {
        "id": "security-password",
        "topic": "security",
        "q": "Құпиясөзді ұмытып қалсам ше?",
        "a": "Кіру бетіндегі «Құпиясөзді ұмыттыңыз ба?» сілтемесі арқылы email-ге қалпына келтіру сілтемесін алыңыз. Сілтеме 30 минут жарамды.",
    },
)

HELP_TOPICS = (
    {"key": "cards", "label": "Карталар мен шоттар", "icon": "credit-card"},
    {"key": "budgets", "label": "Бюджет пен санаттар", "icon": "wallet"},
    {"key": "security", "label": "Тіркелгі қауіпсіздігі", "icon": "shield-check"},
)

SUPPORT_SUBJECTS = (
    "Бюджет туралы сұрақ",
    "Карталар мен шоттар",
    "Тіркелгі қауіпсіздігі",
    "Басқа",
)


@app.route("/help", methods=["GET"])
@login_required
def help():
    query = request.args.get("q", "").strip()[:100]
    active_topic = request.args.get("topic", "")
    if active_topic not in {item["key"] for item in HELP_TOPICS}:
        active_topic = ""

    items = []
    for item in FAQ_ITEMS:
        if active_topic and item["topic"] != active_topic:
            continue
        if query and query.lower() not in (item["q"] + " " + item["a"]).lower():
            continue
        items.append(item)

    topics = []
    for topic in HELP_TOPICS:
        entry = dict(topic)
        entry["count"] = sum(1 for item in FAQ_ITEMS if item["topic"] == topic["key"])
        topics.append(entry)

    messages = db_rows_to_dicts(
        get_db().execute(
            """
            SELECT subject, created_at FROM support_messages
            WHERE user_id = ? ORDER BY id DESC LIMIT 3
            """,
            (g.user["id"],),
        )
    )
    for row in messages:
        row["status"] = "Қабылданды"

    return render_template(
        "help.html",
        faq=items,
        topics=topics,
        active_topic=active_topic,
        q=query,
        message_form={"email": g.user["email"], "subject": "", "message": "", "attachment": ""},
        support_messages=messages,
        contact_email=os.environ.get("SUPPORT_EMAIL", "support@tenge.kz"),
    )


def db_rows_to_dicts(rows):
    return [dict(row) for row in rows]


@app.route("/help/message", methods=["POST"])
@login_required
def help_message():
    db = get_db()
    email = request.form.get("email", "").strip().lower()
    subject = request.form.get("subject", "").strip()
    message = request.form.get("message", "").strip()
    consent = request.form.get("consent") in ("1", "on", "true")
    attachment_name = ""

    uploaded = request.files.get("attachment")
    if uploaded and uploaded.filename:
        original = secure_filename(uploaded.filename)
        if not original:
            flash("Файл атауы дұрыс емес.", "error")
            return redirect(url_for("help") + "#contact")
        extension = original.rsplit(".", 1)[-1].lower() if "." in original else ""
        if extension not in ("pdf", "png", "jpg", "jpeg", "txt", "docx"):
            flash("Тек PDF, PNG, JPG, TXT немесе DOCX файлдарын тіркеңіз.", "error")
            return redirect(url_for("help") + "#contact")
        uploaded.seek(0, os.SEEK_END)
        size = uploaded.tell()
        uploaded.seek(0)
        if size > MAX_ATTACHMENT_BYTES:
            flash("Файл көлемі 10 МБ-ден аспауы керек.", "error")
            return redirect(url_for("help") + "#contact")
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        attachment_name = f"{secrets.token_hex(8)}_{original}"
        uploaded.save(UPLOAD_DIR / attachment_name)

    if not email or len(email) > 254 or not EMAIL_RE.match(email):
        error = "Email мекенжайын дұрыс енгізіңіз."
    elif subject not in SUPPORT_SUBJECTS:
        error = "Хабарлама тақырыбын таңдаңыз."
    elif not 10 <= len(message) <= 2000:
        error = "Хабарлама 10-нан 2000 таңбаға дейін болуы керек."
    elif not consent:
        error = "Дербес деректерді өңдеуге келісуіңіз керек."
    else:
        error = None

    if error:
        flash(error, "error")
        if attachment_name:
            (UPLOAD_DIR / attachment_name).unlink(missing_ok=True)
        return redirect(url_for("help") + "#contact")

    db.execute(
        """
        INSERT INTO support_messages (user_id, email, subject, message, attachment_name, consent)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (g.user["id"], email, subject, message, attachment_name),
    )
    db.commit()
    flash("Хабарлама қолдау тобына жіберілді.", "success")
    return redirect(url_for("help") + "#contact")


# ---------------------------------------------------------------------------
# Жоба туралы
# ---------------------------------------------------------------------------


@app.route("/about", methods=["GET"])
def about():
    return render_template("about.html")


if __name__ == "__main__":
    app.run(debug=True)
