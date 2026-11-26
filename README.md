# Tenge 2.1

Tenge is a personal finance management web application built with Flask, HTML, CSS, JavaScript, and SQLite. The interface is in Kazakh.

## Features

### Accounts and security
- User registration and login
- Password reset by email with single-use, 30-minute tokens
  - Without `SMTP_HOST`, the reset link is displayed on screen in demo mode
  - Resend requests are rate-limited to one per minute
- Editable profile, password change, and account deletion with confirmation
- Security notifications for password changes and login from a new device
- Preferences: hide balances and choose `DD.MM.YYYY` or `YYYY-MM-DD` date format

### Cards and transactions
- Bank card management with balances and currency formatting
- Income and expense tracking with dates, descriptions, and categories
- Transaction history with filters and deletion
- Card-level totals and monthly financial summary

### Categories
- Default income and expense categories seeded for every user
- Create, rename, recolour, and reassign categories
- Archive and restore categories; delete custom categories when unused
- Category usage counters shown on the categories page

### Statistics
- Monthly income, expense, and net savings KPIs
- Six-month income/expense bar chart with month navigation
- Category share breakdown with progress bars
- Automatic, data-driven financial insights

### Reports
- Four report types: detailed operations, category breakdown, monthly summary, and cards
- Date range, card, and export-format filters
- CSV export (UTF-8 BOM, `;` separator) and a print-ready HTML view
- Save, re-download, and delete generated reports

### Budgets
- Monthly or custom-period spending limits, overall or per category
- Real-time spent/remaining progress and status tabs
- Configurable alert threshold (e.g. 80%) with one notification per threshold crossing
- Optional monthly rollover with carry-over of unused funds
- Create, edit, and delete budgets

### Notifications
- Unified inbox for system, budget, and security messages
- Filtering by type and free-text search
- Mark single or all messages as read; unread badge in the header
- Preferences to enable/disable site, email, and budget notifications

### Help and support
- Searchable FAQ organised by topic
- Support request form with email, subject, message, and optional attachment
- History of the user's recent support messages

## Technologies

- Python
- Flask
- SQLite
- HTML
- CSS
- JavaScript

## Installation

```bash
pip install -r requirements.txt
python app.py
```

The app starts at `http://127.0.0.1:5000`. The SQLite database (`tenge.db`) is created automatically and existing databases are migrated in place on startup.

## Optional environment variables

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Flask session signing key |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM` | SMTP server for password-reset and notification emails |
| `SMTP_USER`, `SMTP_PASSWORD` | SMTP credentials |
| `SMTP_EMAIL_NOTIFICATIONS` | Set to `1` to send notification emails as well |
| `SUPPORT_EMAIL` | Address shown on the help page (default `support@tenge.kz`) |

Money is stored internally in tiyn. CSV exports use a UTF-8 BOM and semicolon separators so they open correctly in Excel.
