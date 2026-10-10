
# SpendLab — Smart Expense Tracker

A local Adaptive Software Engineering college project built with Flask,
SQLite, HTML, CSS, JavaScript and Chart.js.

## Features

- Monthly dashboard: income, expenses, balance and savings rate
- Create, search, filter, edit and delete transactions
- Overall and category-based monthly budgets
- Savings goals with progress tracking
- Spending-category doughnut chart and monthly cash-flow chart
- CSV transaction export **and import** (SpendLab format or bank statements)
- Adaptive advisor: health score, month-end forecast, plain-language insights
- "Ask the advisor" question box (answers come from your own data)
- Budget recommendations with priority and confidence, based on last month
- Accept or reject recommendations
- Responsive Neo-Brutalist dashboard
- Local SQLite database; no API key required

## Requirements

- Python 3.10 or newer
- Internet connection for the Chart.js CDN and optional Google Fonts
- Windows 10 or 11

## Setup on Windows

Open Command Prompt or PowerShell and navigate to this folder.

Create a virtual environment:

    py -m venv .venv

Activate it in Command Prompt:

    .venv\Scripts\activate.bat

Or activate it in PowerShell:

    .\.venv\Scripts\Activate.ps1

Install dependencies:

    python -m pip install -r requirements.txt

Start the application:

    python app.py

Open this address in your browser:

    http://127.0.0.1:5000

The SQLite database `expense_tracker.db` is created automatically
in the project directory.

## Project layout

    app.py            Flask routes
    adaptive.py       Forecasting, recommendations, advisor, Q&A
    importer.py       CSV import parsing and validation
    database.py       SQLite schema and automatic migration
    templates/index.html
    public/app.js, public/style.css   (static files, served from /)
    tests/test_app.py

Existing `expense_tracker.db` files are upgraded automatically on start.

## Deploy to Vercel (demo)

1. Push this folder to a GitHub repository (`*.db` is git-ignored).
2. On vercel.com choose **Add New > Project**, import the repository and
   click **Deploy**. No settings are needed: Vercel finds `app.py`.
3. Open the generated `.vercel.app` URL.

Limits: Vercel's filesystem is read-only except `/tmp`, which is not
permanent. The app stores its SQLite file in `/tmp` there, so data can
reset at any time and is not shared reliably between server instances.
Treat it as a demo (import the sample CSVs to load data). For permanent
storage use a persistent-disk host, or move to a hosted database.
The app has no login, so do not put real financial data on a public URL.

## Run the tests

Stop the application if it is running, then run:

    python -m unittest discover -s tests -v

Tests use a separate temporary database.

## Importing transactions from CSV

Click **Import CSV** (top bar or Transactions section).

- Required columns: **Date** and **Amount** (or **Debit** / **Credit**).
- Optional columns: Type, Category, Description.
- Column names are matched flexibly, e.g. `Txn Date`, `Narration`,
  `Withdrawal`, `Deposit`. Comma, semicolon and tab separators work.
- Dates: `YYYY-MM-DD` or day-first (`10/10/2026`, `10 Oct 2026`).
- Amounts may contain `₹`, `Rs`, commas, a minus sign or `(brackets)`.
- If there is no Type column, choose how to read the amounts: by sign
  (negative = expense, positive = income), all expenses, or all income.
- A missing or unknown category is guessed from the description
  (for example "Swiggy" becomes Food); otherwise it becomes Other.
- Rows already in SpendLab (same date, type, amount and description)
  are skipped, so importing the same file twice is safe.
- Invalid rows are reported with their row number; valid rows are still
  imported. Limits: 2 MB and 5,000 rows per file.

A sample file is available from the import dialog.

## Adaptive advisor

The advisor is local and rule/statistics based. It does **not** use an
external AI service or API key, and every result is explained in
plain language. The baseline is the **previous one month**.

1. Compares each category's spending this month with last month.
2. Forecasts month-end spending per category. It blends the current
   daily pace with last month's total (the baseline dominates early in
   the month, the live pace late in the month). Categories with fewer
   than three transactions, such as rent, are not extrapolated.
3. Flags categories that are over budget, or on pace to go over.
4. Suggests new budgets (with a 10% buffer), higher limits for real
   overspending, or lower limits when a budget is clearly underused.
5. Gives each suggestion a priority (high / medium / info) and a
   confidence score based on how much data supports it.
6. Produces a 0-100 financial health score and insights such as
   month-over-month change, largest expense, fastest-rising category,
   budget status, savings rate and what a savings goal needs per month.
7. Answers questions such as "Will I stay within budget?" or "Where can
   I cut spending?" from your own data.
8. Saves your accept/reject decision. Accepting updates the category
   budget; rejecting leaves it unchanged.

Only category budgets are used for category suggestions. The overall
monthly budget is tracked separately in the health score and forecast.

These are educational heuristics, not financial advice.

## Notes

- All monetary values are in Indian rupees.
- No sample transactions are inserted automatically.
- Add (or import) last month's transactions so the advisor has a
  baseline to compare against.
- The dashboard displays totals for the selected month.
- CSV export downloads transactions for the selected month.
- Chart.js and Google Fonts load from external CDNs.
- This starter project is intended for local use. It does not include
  user accounts, multi-user access controls, or production security
  configuration.
- Debug mode is enabled for local development in `app.py`.
