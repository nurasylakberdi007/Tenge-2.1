import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
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
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "tenge.db"

MAX_AMOUNT_TENGE = 1_000_000_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

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
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

CREATE INDEX IF NOT EXISTS idx_cards_user ON cards(user_id);
CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tx_card ON transactions(card_id);
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


def init_db():
    """Кестелерді алғашқы іске қосуда автоматты түрде құрады."""
    if is_legacy_database():
        os.replace(DB_PATH, DB_PATH.with_name("tenge.legacy.db"))

    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(SCHEMA)
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
        "SELECT id, username, email FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if g.user is None:
        session.clear()


@app.template_filter("money")
def money_filter(cents):
    """Тиынды әдемі мәтінге айналдырады: 12500050 -> '125 000,50'."""
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
        return value.strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return ""


@app.context_processor
def inject_globals():
    return {
        "all_categories": ALL_CATEGORIES,
        "expense_categories": EXPENSE_CATEGORIES,
        "income_categories": INCOME_CATEGORIES,
    }


def fetch_totals(db, user_id):
    row = db.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income'  THEN amount END), 0) AS income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount END), 0) AS expense
        FROM transactions
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    return row["income"], row["expense"]


def fetch_cards(db, user_id):
    """Әр картаның ағымдағы балансымен: бастапқы баланс + кіріс − шығыс."""
    return db.execute(
        """
        SELECT
            c.id, c.name, c.bank_name, c.last_four, c.initial_balance,
            c.initial_balance + COALESCE(SUM(
                CASE t.type WHEN 'income' THEN t.amount
                            WHEN 'expense' THEN -t.amount END
            ), 0) AS balance
        FROM cards c
        LEFT JOIN transactions t ON t.card_id = c.id
        WHERE c.user_id = ?
        GROUP BY c.id
        ORDER BY c.id DESC
        """,
        (user_id,),
    ).fetchall()

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
            db.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                (username, email, generate_password_hash(password)),
            )
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

    user = get_db().execute(
        "SELECT id, password_hash FROM users WHERE email = ?", (email,)
    ).fetchone()

    password_hash = user["password_hash"] if user else DUMMY_HASH
    valid = check_password_hash(password_hash, password)

    if user is None or not valid:
        flash("Email немесе құпиясөз қате.", "error")
        return render_template("login.html", form={"email": email}), 401

    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("Аккаунттан шықтыңыз.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Негізгі бет (Dashboard)
# ---------------------------------------------------------------------------


@app.route("/", methods=["GET"])
@login_required
def index():
    db = get_db()
    income, expense = fetch_totals(db, g.user["id"])
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

    return render_template(
        "index.html",
        income=income,
        expense=expense,
        balance=cards_opening + income - expense,
        cards_opening=cards_opening,
        spent_percent=spent_percent,
        transactions=transactions,
        cards=fetch_cards(db, g.user["id"]),
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
    if category not in CATEGORIES[tx_type]:
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
    card_income = card_expense = 0
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
                card_income += tx["amount"]
            else:
                card_expense += tx["amount"]

    return render_template(
        "cards.html",
        cards=card_list,
        selected=selected,
        transactions=card_transactions,
        card_income=card_income,
        card_expense=card_expense,
        total_balance=sum(card["balance"] for card in card_list),
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


# ---------------------------------------------------------------------------
# Жоба туралы
# ---------------------------------------------------------------------------


@app.route("/about", methods=["GET"])
def about():
    return render_template("about.html")


if __name__ == "__main__":
    app.run(debug=True)
