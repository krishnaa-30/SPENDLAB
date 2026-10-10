
import csv
import io
import re
from collections import Counter
from datetime import date
from flask import (
    Flask, jsonify, render_template, request,
    Response
)
from database import init_db, get_db, CATEGORIES, IS_VERCEL
from adaptive import refresh_suggestions, advisor_summary, answer_question
from importer import parse_csv, MODES, SAMPLE_CSV

# Static files live in public/ (Vercel serves public/** from its CDN).
app = Flask(__name__, static_folder="public", static_url_path="")
app.config["JSON_SORT_KEYS"] = False
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB upload limit

init_db()


def error(message, status=400):
    return jsonify({"error": message}), status


def valid_month(value):
    return bool(value and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value))


def current_month():
    return date.today().strftime("%Y-%m")


def read_json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Send a valid JSON object.")
    return data


def validate_transaction(data):
    kind = data.get("type")
    category = str(data.get("category", "")).strip()
    description = str(data.get("description", "")).strip()
    day = str(data.get("date", "")).strip()

    if kind not in CATEGORIES:
        raise ValueError("Type must be income or expense.")
    if category not in CATEGORIES[kind]:
        raise ValueError("Choose a valid category for this transaction.")
    try:
        amount = float(data.get("amount"))
    except (ValueError, TypeError):
        raise ValueError("Amount must be a valid number.")
    if not 0 < amount <= 1000000000:
        raise ValueError("Amount must be greater than zero and at most 1 billion.")
    try:
        date.fromisoformat(day)
    except (ValueError, TypeError):
        raise ValueError("Date must be a valid YYYY-MM-DD date.")
    if len(description) > 200:
        raise ValueError("Description must be 200 characters or fewer.")

    return kind, round(amount, 2), category, description, day


def refresh(conn, month):
    refresh_suggestions(conn, month)


@app.get("/")
def index():
    return render_template("index.html", categories=CATEGORIES, demo=IS_VERCEL)


@app.get("/api/categories")
def categories():
    return jsonify(CATEGORIES)


@app.get("/api/dashboard")
def dashboard():
    month = request.args.get("month", current_month())
    if not valid_month(month):
        return error("Month must use YYYY-MM format.")

    with get_db() as conn:
        refresh(conn, month)
        totals = conn.execute(
            """SELECT type, COALESCE(SUM(amount),0) AS total
               FROM transactions WHERE substr(date,1,7)=?
               GROUP BY type""", (month,)
        ).fetchall()
        amounts = {r["type"]: float(r["total"]) for r in totals}
        income = amounts.get("income", 0)
        expenses = amounts.get("expense", 0)

        overall = conn.execute(
            "SELECT amount FROM budgets WHERE month=? AND category='*'",
            (month,)
        ).fetchone()
        overall_budget = float(overall["amount"]) if overall else 0

        category_budgets = conn.execute(
            """SELECT category, amount FROM budgets
               WHERE month=? AND category!='*'""", (month,)
        ).fetchall()

        category_spending = conn.execute(
            """SELECT category, SUM(amount) AS total FROM transactions
               WHERE type='expense' AND substr(date,1,7)=?
               GROUP BY category ORDER BY total DESC""", (month,)
        ).fetchall()

        trends = conn.execute(
            """SELECT substr(date,1,7) AS month,
               SUM(CASE WHEN type='income' THEN amount ELSE 0 END) AS income,
               SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) AS expenses
               FROM transactions
               WHERE date >= date(?, '-5 months', 'start of month')
                 AND date < date(?, '+1 month')
               GROUP BY substr(date,1,7) ORDER BY month""",
            (month + "-01", month + "-01")
        ).fetchall()

        goals = conn.execute("SELECT * FROM savings_goals ORDER BY id DESC").fetchall()
        saved_goals = [dict(g) for g in goals]
        goal_target = sum(g["target"] for g in saved_goals)
        goal_saved = sum(g["saved"] for g in saved_goals)

        category_data = []
        for b in category_budgets:
            spent = next((float(x["total"]) for x in category_spending
                          if x["category"] == b["category"]), 0)
            category_data.append({
                "category": b["category"],
                "budget": float(b["amount"]),
                "spent": spent,
                "usage": round(spent / b["amount"] * 100, 1)
            })

        recent = conn.execute(
            """SELECT * FROM transactions WHERE substr(date,1,7)=?
               ORDER BY date DESC, id DESC LIMIT 8""", (month,)
        ).fetchall()

        return jsonify({
            "month": month,
            "income": round(income, 2),
            "expenses": round(expenses, 2),
            "balance": round(income-expenses, 2),
            "savings": round(income-expenses, 2),
            "budget": overall_budget,
            "budget_usage": round(expenses/overall_budget*100, 1)
                if overall_budget else 0,
            "budget_remaining": round(overall_budget-expenses, 2)
                if overall_budget else None,
            "category_budgets": category_data,
            "categories": [dict(r) for r in category_spending],
            "trends": [dict(r) for r in trends],
            "goals": saved_goals,
            "goal_target": goal_target,
            "goal_saved": goal_saved,
            "recent": [dict(r) for r in recent]
        })


@app.get("/api/transactions")
def list_transactions():
    month = request.args.get("month", "")
    search = request.args.get("search", "").strip()[:100]
    kind = request.args.get("type", "")
    category = request.args.get("category", "")
    if month and not valid_month(month):
        return error("Invalid month.")
    if kind and kind not in CATEGORIES:
        return error("Invalid transaction type.")
    if category and not any(category in values for values in CATEGORIES.values()):
        return error("Invalid category.")

    sql = "SELECT * FROM transactions WHERE 1=1"
    params = []
    if month:
        sql += " AND substr(date,1,7)=?"
        params.append(month)
    if kind:
        sql += " AND type=?"
        params.append(kind)
    if category:
        sql += " AND category=?"
        params.append(category)
    if search:
        sql += " AND (description LIKE ? OR category LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    sql += " ORDER BY date DESC, id DESC LIMIT 500"

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        return jsonify([dict(r) for r in rows])


@app.post("/api/transactions")
def create_transaction():
    try:
        values = validate_transaction(read_json())
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO transactions
                   (type, amount, category, description, date)
                   VALUES (?, ?, ?, ?, ?)""", values
            )
            refresh(conn, values[4][:7])
            return jsonify({"id": cur.lastrowid, "message": "Transaction added."}), 201
    except ValueError as exc:
        return error(str(exc))


@app.put("/api/transactions/<int:transaction_id>")
def update_transaction(transaction_id):
    try:
        values = validate_transaction(read_json())
        with get_db() as conn:
            old = conn.execute(
                "SELECT date FROM transactions WHERE id=?", (transaction_id,)
            ).fetchone()
            if not old:
                return error("Transaction not found.", 404)
            conn.execute(
                """UPDATE transactions SET type=?, amount=?, category=?,
                   description=?, date=? WHERE id=?""",
                (*values, transaction_id)
            )
            refresh(conn, old["date"][:7])
            refresh(conn, values[4][:7])
        return jsonify({"message": "Transaction updated."})
    except ValueError as exc:
        return error(str(exc))


@app.delete("/api/transactions/<int:transaction_id>")
def delete_transaction(transaction_id):
    with get_db() as conn:
        old = conn.execute(
            "SELECT date FROM transactions WHERE id=?", (transaction_id,)
        ).fetchone()
        if not old:
            return error("Transaction not found.", 404)
        conn.execute("DELETE FROM transactions WHERE id=?", (transaction_id,))
        refresh(conn, old["date"][:7])
    return jsonify({"message": "Transaction deleted."})


@app.route("/api/budgets", methods=["GET", "POST"])
def budgets():
    if request.method == "GET":
        month = request.args.get("month", current_month())
        if not valid_month(month):
            return error("Invalid month.")
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM budgets WHERE month=? ORDER BY category", (month,)
            ).fetchall()
            return jsonify([dict(r) for r in rows])

    try:
        data = read_json()
        month = data.get("month", current_month())
        category = str(data.get("category", "*")).strip()
        amount = float(data.get("amount"))
        valid_categories = set(CATEGORIES["expense"])
        if not valid_month(month):
            raise ValueError("Invalid month.")
        if category != "*" and category not in valid_categories:
            raise ValueError("Invalid expense category.")
        if not 0 < amount <= 1000000000:
            raise ValueError("Budget must be greater than zero.")
        with get_db() as conn:
            conn.execute(
                """INSERT INTO budgets(month,category,amount) VALUES(?,?,?)
                   ON CONFLICT(month,category) DO UPDATE SET amount=excluded.amount""",
                (month, category, round(amount, 2))
            )
            refresh(conn, month)
        return jsonify({"message": "Budget saved."})
    except (ValueError, TypeError) as exc:
        return error(str(exc))


@app.delete("/api/budgets/<int:budget_id>")
def delete_budget(budget_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT month FROM budgets WHERE id=?", (budget_id,)
        ).fetchone()
        if not row:
            return error("Budget not found.", 404)
        conn.execute("DELETE FROM budgets WHERE id=?", (budget_id,))
        refresh(conn, row["month"])
    return jsonify({"message": "Budget deleted."})


@app.route("/api/goals", methods=["GET", "POST"])
def goals():
    if request.method == "GET":
        with get_db() as conn:
            return jsonify([dict(r) for r in conn.execute(
                "SELECT * FROM savings_goals ORDER BY id DESC"
            ).fetchall()])

    try:
        data = read_json()
        name = str(data.get("name", "")).strip()
        target = float(data.get("target"))
        saved = float(data.get("saved", 0))
        deadline = str(data.get("deadline", "")).strip()
        if not name or len(name) > 100:
            raise ValueError("Goal name is required (max 100 characters).")
        if not 0 < target <= 1000000000 or not 0 <= saved <= target:
            raise ValueError("Enter valid target and saved amounts.")
        if deadline:
            date.fromisoformat(deadline)
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO savings_goals(name,target,saved,deadline)
                   VALUES(?,?,?,?)""",
                (name, target, saved, deadline)
            )
            return jsonify({"id": cur.lastrowid, "message": "Goal added."}), 201
    except (ValueError, TypeError) as exc:
        return error(str(exc))


@app.put("/api/goals/<int:goal_id>")
def update_goal(goal_id):
    try:
        data = read_json()
        name = str(data.get("name", "")).strip()
        target, saved = float(data.get("target")), float(data.get("saved", 0))
        deadline = str(data.get("deadline", "")).strip()
        if not name or len(name) > 100 or not 0 < target <= 1e9 or not 0 <= saved <= target:
            raise ValueError("Enter a valid name, target and saved amount.")
        if deadline:
            date.fromisoformat(deadline)
        with get_db() as conn:
            cur = conn.execute(
                """UPDATE savings_goals SET name=?,target=?,saved=?,deadline=?
                   WHERE id=?""", (name, target, saved, deadline, goal_id)
            )
            if not cur.rowcount:
                return error("Goal not found.", 404)
        return jsonify({"message": "Goal updated."})
    except (ValueError, TypeError) as exc:
        return error(str(exc))


@app.delete("/api/goals/<int:goal_id>")
def delete_goal(goal_id):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM savings_goals WHERE id=?", (goal_id,))
        if not cur.rowcount:
            return error("Goal not found.", 404)
    return jsonify({"message": "Goal deleted."})


@app.get("/api/suggestions")
def suggestions():
    month = request.args.get("month", current_month())
    if not valid_month(month):
        return error("Invalid month.")
    with get_db() as conn:
        refresh(conn, month)
        rows = conn.execute(
            """SELECT * FROM suggestions WHERE month=?
               ORDER BY CASE severity WHEN 'high' THEN 0
                                      WHEN 'medium' THEN 1 ELSE 2 END,
                        id DESC""", (month,)
        ).fetchall()
        return jsonify([dict(r) for r in rows])


@app.post("/api/suggestions/<int:suggestion_id>/decision")
def decide_suggestion(suggestion_id):
    try:
        status = read_json().get("status")
        if status not in ("accepted", "rejected"):
            raise ValueError("Decision must be accepted or rejected.")
        with get_db() as conn:
            item = conn.execute(
                "SELECT * FROM suggestions WHERE id=?", (suggestion_id,)
            ).fetchone()
            if not item:
                return error("Suggestion not found.", 404)
            if item["status"] != "pending":
                return error("This suggestion has already been decided.", 409)
            if status == "accepted":
                conn.execute(
                    """INSERT INTO budgets(month,category,amount) VALUES(?,?,?)
                       ON CONFLICT(month,category)
                       DO UPDATE SET amount=excluded.amount""",
                    (item["month"], item["category"], item["suggested_budget"])
                )
            conn.execute(
                "UPDATE suggestions SET status=? WHERE id=?",
                (status, suggestion_id)
            )
        return jsonify({"message": f"Suggestion {status}."})
    except ValueError as exc:
        return error(str(exc))


@app.get("/api/advisor")
def advisor():
    month = request.args.get("month", current_month())
    if not valid_month(month):
        return error("Invalid month.")
    with get_db() as conn:
        return jsonify(advisor_summary(conn, month))


@app.post("/api/advisor/ask")
def advisor_ask():
    try:
        data = read_json()
    except ValueError as exc:
        return error(str(exc))
    question = str(data.get("question", "")).strip()[:300]
    month = data.get("month", current_month())
    if not question:
        return error("Type a question for the advisor.")
    if not valid_month(month):
        return error("Invalid month.")
    with get_db() as conn:
        return jsonify(answer_question(conn, month, question))


@app.get("/api/import/template")
def import_template():
    return Response(
        SAMPLE_CSV,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sample_import.csv"'}
    )


@app.post("/api/import")
def import_transactions():
    file = request.files.get("file")
    if not file or not file.filename:
        return error("Choose a CSV file to import.")
    if not file.filename.lower().endswith((".csv", ".txt")):
        return error("Only .csv files can be imported.")
    mode = request.form.get("mode", "auto")
    if mode not in MODES:
        return error("Invalid import mode.")

    try:
        parsed = parse_csv(file.read(), mode)
    except ValueError as exc:
        return error(str(exc))

    rows = parsed["rows"]
    if not rows:
        first = parsed["errors"][0] if parsed["errors"] else None
        detail = f" First problem: row {first['row']}: {first['message']}" if first else ""
        return error("No valid transactions were found in this file." + detail)

    with get_db() as conn:
        # Skip rows already in the database so re-importing is safe.
        # Identical rows are compared by count, so two genuine identical
        # purchases in one file are both kept.
        lo, hi = min(r[4] for r in rows), max(r[4] for r in rows)
        existing = Counter(
            (r["date"], r["type"], round(r["amount"], 2), r["description"])
            for r in conn.execute(
                """SELECT date, type, amount, description FROM transactions
                   WHERE date BETWEEN ? AND ?""", (lo, hi))
        )
        seen, fresh, duplicates = Counter(), [], 0
        for row in rows:
            key = (row[4], row[0], row[1], row[3])
            seen[key] += 1
            if seen[key] <= existing[key]:
                duplicates += 1
            else:
                fresh.append(row)

        conn.executemany(
            """INSERT INTO transactions
               (type, amount, category, description, date)
               VALUES (?, ?, ?, ?, ?)""", fresh
        )
        months = sorted({r[4][:7] for r in fresh})
        for m in months:
            refresh(conn, m)

    return jsonify({
        "message": f"Imported {len(fresh)} transaction(s).",
        "imported": len(fresh),
        "duplicates": duplicates,
        "error_count": parsed["error_count"],
        "errors": parsed["errors"],
        "auto_categorized": parsed["auto_categorized"],
        "total_rows": parsed["total_rows"],
        "months": months,
    })


@app.get("/api/export")
def export_csv():
    month = request.args.get("month", "")
    if month and not valid_month(month):
        return error("Invalid month.")
    with get_db() as conn:
        sql = "SELECT id,date,type,category,description,amount FROM transactions"
        params = []
        if month:
            sql += " WHERE substr(date,1,7)=?"
            params.append(month)
        sql += " ORDER BY date DESC, id DESC"
        rows = conn.execute(sql, params).fetchall()

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["ID", "Date", "Type", "Category", "Description", "Amount (INR)"])
    writer.writerows([tuple(r) for r in rows])
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="transactions.csv"'}
    )


@app.errorhandler(404)
def not_found(_):
    if request.path.startswith("/api/"):
        return error("API endpoint not found.", 404)
    return "Page not found", 404


@app.errorhandler(413)
def too_large(_):
    return error("File is too large. The limit is 2 MB.", 413)


@app.errorhandler(500)
def server_error(_):
    return error("Internal server error. Check the application console.", 500)


if __name__ == "__main__":
    app.run(debug=True)
