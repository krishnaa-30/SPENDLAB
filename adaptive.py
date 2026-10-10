"""SpendLab adaptive engine.

A local, explainable "AI-style" advisor. It does not call any external
API or language model: it learns from the transactions you record using
statistics (run-rate forecasting, month-over-month comparison, anomaly
detection) and explains every result in plain language.

Baseline: the PREVIOUS ONE MONTH of spending.
"""
import calendar
import math
import re
from datetime import date


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def previous_months(month, count=1):
    year, mon = map(int, month.split("-"))
    result = []
    for _ in range(count):
        mon -= 1
        if mon == 0:
            mon = 12
            year -= 1
        result.append(f"{year:04d}-{mon:02d}")
    return result


def inr(value):
    return f"₹{value:,.0f}"


def nice(value):
    """Round a budget up to a tidy figure (nearest 10 once it is >= 100)."""
    if value >= 100:
        return float(math.ceil(value / 10) * 10)
    return round(value, 2)


def month_progress(month, today):
    """Return (days_in_month, days_elapsed) for `month` as of `today`."""
    year, mon = map(int, month.split("-"))
    days = calendar.monthrange(year, mon)[1]
    current = today.strftime("%Y-%m")
    if month < current:
        return days, days
    if month == current:
        return days, today.day
    return days, 0


MIN_TRANSACTIONS_FOR_RUN_RATE = 3


def project(actual, baseline, days, elapsed, count):
    """Forecast month-end spending for one category.

    * Month finished -> the actual total. Future month -> last month.
    * Few transactions (e.g. rent, a one-off bill): do NOT extrapolate a
      daily rate; assume the month will match last month at least.
    * Otherwise blend the daily run-rate with last month's total. Early in
      the month the baseline dominates; late in the month the run-rate does.
    """
    if elapsed >= days:
        return actual
    if elapsed == 0:
        return baseline
    if count < MIN_TRANSACTIONS_FOR_RUN_RATE:
        return max(actual, baseline)
    if baseline <= 0 and elapsed < 7:
        return actual  # too little data to extrapolate safely
    run_rate = actual / elapsed * days
    if baseline <= 0:
        return max(actual, run_rate)
    weight = elapsed / days
    return max(actual, weight * run_rate + (1 - weight) * baseline)


# --------------------------------------------------------------------------
# Analysis: gather everything once, reuse for recommendations/summary/Q&A
# --------------------------------------------------------------------------
def analyze(conn, month, today=None):
    today = today or date.today()
    last_month = previous_months(month, 1)[0]
    days, elapsed = month_progress(month, today)

    expense_rows = conn.execute(
        """SELECT substr(date,1,7) AS m, category,
                  SUM(amount) AS total, COUNT(*) AS n
           FROM transactions
           WHERE type='expense' AND substr(date,1,7) IN (?, ?)
           GROUP BY substr(date,1,7), category""",
        (month, last_month),
    ).fetchall()
    income_rows = conn.execute(
        """SELECT substr(date,1,7) AS m, SUM(amount) AS total
           FROM transactions
           WHERE type='income' AND substr(date,1,7) IN (?, ?)
           GROUP BY substr(date,1,7)""",
        (month, last_month),
    ).fetchall()
    budget_rows = conn.execute(
        "SELECT category, amount FROM budgets WHERE month=?", (month,)
    ).fetchall()

    spent, last, counts = {}, {}, {}
    for r in expense_rows:
        if r["m"] == month:
            spent[r["category"]] = float(r["total"])
            counts[r["category"]] = int(r["n"])
        else:
            last[r["category"]] = float(r["total"])

    income = sum(float(r["total"]) for r in income_rows if r["m"] == month)
    last_income = sum(float(r["total"]) for r in income_rows if r["m"] == last_month)

    budgets = {r["category"]: float(r["amount"]) for r in budget_rows}
    overall_budget = budgets.pop("*", 0.0)

    categories = []
    for name in set(spent) | set(last) | set(budgets):
        actual = spent.get(name, 0.0)
        previous = last.get(name, 0.0)
        categories.append({
            "category": name,
            "actual": actual,
            "last": previous,
            "budget": budgets.get(name, 0.0),
            "count": counts.get(name, 0),
            "projected": project(actual, previous, days, elapsed,
                                 counts.get(name, 0)),
        })
    categories.sort(key=lambda c: (-c["actual"], c["category"]))

    return {
        "month": month,
        "last_month": last_month,
        "days": days,
        "elapsed": elapsed,
        "days_left": days - elapsed,
        "income": income,
        "last_income": last_income,
        "expenses": sum(c["actual"] for c in categories),
        "last_expenses": sum(c["last"] for c in categories),
        "forecast": sum(c["projected"] for c in categories),
        "overall_budget": overall_budget,
        "categories": categories,
    }


def confidence(category, a):
    """0-95 score: how much evidence supports this suggestion."""
    fraction = a["elapsed"] / a["days"]
    score = 30
    score += 30 if category["last"] > 0 else 0       # have a baseline
    score += round(25 * fraction)                    # month is progressing
    score += min(15, category["count"] * 3)          # enough transactions
    return min(score, 95)


# --------------------------------------------------------------------------
# Budget recommendations
# --------------------------------------------------------------------------
def calculate_recommendations(conn, month, today=None):
    """Return explainable category budget suggestions (one-month baseline)."""
    a = analyze(conn, month, today)
    results = []

    for c in a["categories"]:
        name, actual, last = c["category"], c["actual"], c["last"]
        budget, projected = c["budget"], c["projected"]
        days_left = a["days_left"]

        kind = severity = None
        proposed = 0.0
        reason = ""

        if budget > 0 and actual > budget:
            kind, severity = "over", "high"
            proposed = nice(max(actual, projected) * 1.05)
            reason = (
                f"{name} is at {inr(actual)}, which is {inr(actual - budget)} "
                f"over your {inr(budget)} budget."
            )
            if last > 0:
                reason += f" Last month you spent {inr(last)} here."
            reason += (
                f" A limit of {inr(proposed)} would match your real pattern"
                + ("; the alternative is to cut back for the rest of the month."
                   if days_left > 0 else ".")
            )

        elif budget > 0 and days_left > 0 and projected > budget:
            kind, severity = "pace", "medium"
            proposed = nice(projected * 1.05)
            per_day = max(budget - actual, 0) / days_left
            reason = (
                f"At your current pace {name} will reach about {inr(projected)} "
                f"by month end, above your {inr(budget)} budget. "
                f"To stay inside it, keep spending under {inr(per_day)} a day "
                f"for the next {days_left} days, or raise the limit to "
                f"{inr(proposed)}."
            )

        elif budget == 0 and max(last, projected) > 0:
            kind, severity = "new", "info"
            basis = max(last, projected)
            proposed = nice(basis * 1.10)
            if last > 0:
                reason = (
                    f"No budget is set for {name}. Last month you spent "
                    f"{inr(last)} here"
                )
                reason += (f" and this month is on track for {inr(projected)}. "
                           if a["elapsed"] > 0 and actual > 0 else ". ")
            else:
                reason = (
                    f"No budget is set for {name}, and it is on track for "
                    f"{inr(projected)} this month. "
                )
            reason += f"A limit of {inr(proposed)} includes a 10% buffer."

        elif (budget > 0 and last > 0 and a["elapsed"] > 0
              and last < budget * 0.6 and projected < budget * 0.6):
            candidate = nice(max(last, projected) * 1.15)
            if candidate < budget * 0.9:
                kind, severity = "lower", "info"
                proposed = candidate
                reason = (
                    f"You used only {last / budget * 100:.0f}% of your "
                    f"{name} budget last month and are on track for "
                    f"{projected / budget * 100:.0f}% this month. Lowering the "
                    f"limit to {inr(proposed)} frees up {inr(budget - proposed)} "
                    "that could go towards a savings goal."
                )

        if kind is None or proposed <= 0:
            continue

        results.append({
            "month": month,
            "category": name,
            "kind": kind,
            "severity": severity,
            "old_budget": budget,
            "suggested_budget": proposed,
            "projected": round(projected, 2),
            "last_month": round(last, 2),
            "confidence": confidence(c, a),
            "reason": reason,
        })

    order = {"high": 0, "medium": 1, "info": 2}
    results.sort(key=lambda r: (order[r["severity"]], r["category"]))
    return results


def refresh_suggestions(conn, month, today=None):
    """Refresh pending recommendations; preserve user decisions."""
    recommendations = calculate_recommendations(conn, month, today)

    for item in recommendations:
        existing = conn.execute(
            "SELECT status FROM suggestions WHERE month=? AND category=?",
            (month, item["category"]),
        ).fetchone()
        if existing and existing["status"] != "pending":
            continue

        conn.execute(
            """INSERT INTO suggestions
               (month, category, old_budget, suggested_budget, reason, status,
                kind, severity, confidence, projected, last_month)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
               ON CONFLICT(month, category) DO UPDATE SET
                 old_budget=excluded.old_budget,
                 suggested_budget=excluded.suggested_budget,
                 reason=excluded.reason,
                 kind=excluded.kind,
                 severity=excluded.severity,
                 confidence=excluded.confidence,
                 projected=excluded.projected,
                 last_month=excluded.last_month
               WHERE suggestions.status='pending'""",
            (item["month"], item["category"], item["old_budget"],
             item["suggested_budget"], item["reason"], item["kind"],
             item["severity"], item["confidence"], item["projected"],
             item["last_month"]),
        )

    # Remove obsolete pending suggestions if their rules no longer apply.
    active = {x["category"] for x in recommendations}
    pending = conn.execute(
        "SELECT id, category FROM suggestions WHERE month=? AND status='pending'",
        (month,),
    ).fetchall()
    for row in pending:
        if row["category"] not in active:
            conn.execute("DELETE FROM suggestions WHERE id=?", (row["id"],))


# --------------------------------------------------------------------------
# Advisor summary (health score + plain-language insights)
# --------------------------------------------------------------------------
def _pct(new, old):
    return (new - old) / old * 100 if old else 0.0


def _goal_insight(conn, today):
    goal = conn.execute(
        """SELECT name, target, saved, deadline FROM savings_goals
           WHERE deadline != '' AND saved < target AND deadline >= ?
           ORDER BY deadline LIMIT 1""",
        (today.isoformat(),),
    ).fetchone()
    if not goal:
        return None
    try:
        due = date.fromisoformat(goal["deadline"])
    except ValueError:
        return None
    months_left = max(1, math.ceil((due - today).days / 30))
    need = (goal["target"] - goal["saved"]) / months_left
    return (
        f"To reach your “{goal['name']}” goal by {goal['deadline']}, "
        f"set aside about {inr(need)} a month."
    )


def advisor_summary(conn, month, today=None):
    today = today or date.today()
    a = analyze(conn, month, today)
    cats = a["categories"]
    expenses, income = a["expenses"], a["income"]
    forecast, last_total = a["forecast"], a["last_expenses"]
    is_open = a["days_left"] > 0
    insights = []

    over = [c for c in cats if c["budget"] > 0 and c["actual"] > c["budget"]]
    savings_rate = (income - expenses) / income * 100 if income > 0 else None

    # ---- health score (starts at 100, explained deductions) -------------
    score = 100
    if a["overall_budget"] > 0:
        if expenses > a["overall_budget"]:
            score -= 25
        elif forecast > a["overall_budget"]:
            score -= 12
    score -= min(24, len(over) * 8)
    heading_over = [c for c in cats if c["budget"] > 0 and is_open
                    and c["actual"] <= c["budget"] < c["projected"]]
    score -= min(12, len(heading_over) * 4)
    if savings_rate is not None:
        if savings_rate < 0:
            score -= 20
        elif savings_rate < 10:
            score -= 10
        elif savings_rate < 20:
            score -= 4
    if last_total > 0:
        if forecast > last_total * 1.3:
            score -= 8
        elif forecast > last_total * 1.1:
            score -= 4
    score = max(0, min(100, score))
    label = ("Healthy" if score >= 85 else "Okay" if score >= 65
             else "Needs attention" if score >= 40 else "At risk")

    has_data = expenses > 0 or income > 0 or last_total > 0
    if not has_data:
        return {
            "month": month, "score": None, "label": "No data yet",
            "headline": "Add a few transactions and I'll start learning your spending pattern.",
            "insights": [], "expenses": 0, "income": 0, "forecast": 0,
            "last_expenses": 0, "savings_rate": None,
            "overall_budget": a["overall_budget"],
        }

    # ---- insights -------------------------------------------------------
    if last_total > 0:
        change = _pct(forecast, last_total)
        if change > 10:
            insights.append(("warn",
                f"You're on track to spend {change:.0f}% more than last month "
                f"({inr(forecast)} vs {inr(last_total)})."))
        elif change < -10:
            insights.append(("good",
                f"You're on track to spend {abs(change):.0f}% less than last "
                f"month ({inr(forecast)} vs {inr(last_total)}). Nice work."))
        else:
            insights.append(("info",
                f"Spending is steady compared with last month "
                f"({inr(forecast)} vs {inr(last_total)})."))

    if expenses > 0 and cats and cats[0]["actual"] > 0:
        top = cats[0]
        insights.append(("info",
            f"{top['category']} is your largest expense at {inr(top['actual'])} "
            f"({top['actual'] / expenses * 100:.0f}% of spending)."))

    risers = [c for c in cats if c["last"] > 0
              and c["projected"] - c["last"] >= 100
              and _pct(c["projected"], c["last"]) >= 25]
    if risers:
        r = max(risers, key=lambda c: c["projected"] - c["last"])
        insights.append(("warn",
            f"{r['category']} is up {_pct(r['projected'], r['last']):.0f}% on "
            f"last month ({inr(r['projected'])} projected vs {inr(r['last'])})."))

    if a["overall_budget"] > 0:
        if expenses > a["overall_budget"]:
            insights.append(("warn",
                f"You've passed your overall budget by "
                f"{inr(expenses - a['overall_budget'])}."))
        elif is_open and forecast > a["overall_budget"]:
            per_day = (a["overall_budget"] - expenses) / a["days_left"]
            insights.append(("warn",
                f"At this pace you'll finish about "
                f"{inr(forecast - a['overall_budget'])} over your overall "
                f"budget. Keep daily spending under {inr(per_day)} to stay in."))
        else:
            insights.append(("good",
                f"You're within your overall budget with "
                f"{inr(a['overall_budget'] - expenses)} to spare."))

    if savings_rate is not None:
        tone = "good" if savings_rate >= 20 else "warn" if savings_rate < 10 else "info"
        insights.append((tone,
            f"Your savings rate is {savings_rate:.0f}%. "
            + ("That's a strong buffer." if savings_rate >= 20 else
               "Aim for 20% or more if you can." if savings_rate >= 0 else
               "You're spending more than you earn this month.")))

    goal = _goal_insight(conn, today)
    if goal:
        insights.append(("info", goal))

    headline = (
        f"{label}: {inr(expenses)} spent so far, projected {inr(forecast)} by month end."
        if is_open else f"{label}: {inr(expenses)} spent in {month}."
    )
    return {
        "month": month, "score": score, "label": label, "headline": headline,
        "insights": [{"tone": t, "text": x} for t, x in insights],
        "expenses": round(expenses, 2), "income": round(income, 2),
        "forecast": round(forecast, 2), "last_expenses": round(last_total, 2),
        "savings_rate": None if savings_rate is None else round(savings_rate, 1),
        "overall_budget": a["overall_budget"],
    }


# --------------------------------------------------------------------------
# "Ask the advisor": keyword-intent question answering over your own data
# --------------------------------------------------------------------------
FIXED_COSTS = {"Rent", "Bills"}  # not sensible to suggest cutting these

INTENTS = [
    ("forecast", ("forecast", "predict", "project", "month end", "end of month",
                  "will i spend", "on track", "stay within", "stay in")),
    ("compare", ("last month", "previous", "compare", "versus", "vs", "change")),
    ("tips", ("reduce", "cut", "tip", "advice", "improve", "help", "overspend")),
    ("top", ("biggest", "largest", "most", "top", "where", "highest")),
    ("savings", ("save", "saving", "goal")),
    ("budget", ("budget", "limit", "over")),
    ("health", ("score", "health", "how am i", "how'm i", "doing")),
]


def answer_question(conn, month, question, today=None):
    today = today or date.today()
    q = f" {question.lower().strip()} "
    a = analyze(conn, month, today)
    summary = advisor_summary(conn, month, today)
    cats = a["categories"]

    if summary["score"] is None:
        return {"intent": "empty", "answer":
                "I don't have any transactions for this month yet. Add a few "
                "and I can forecast, compare and give advice."}

    intent = next((name for name, words in INTENTS
                   if any(re.search(r"\b" + re.escape(w), q) for w in words)),
                  "unknown")

    if intent == "forecast":
        if a["days_left"] <= 0:
            text = f"{month} is complete: you spent {inr(a['expenses'])}."
        else:
            text = (f"You've spent {inr(a['expenses'])} so far. "
                    f"I project about {inr(a['forecast'])} by month end.")
        if a["overall_budget"] > 0 and a["days_left"] > 0:
            gap = a["overall_budget"] - a["forecast"]
            text += (f" That's {inr(gap)} under your overall budget."
                     if gap >= 0 else
                     f" That's {inr(-gap)} over your overall budget.")
        return {"intent": intent, "answer": text}

    if intent == "compare":
        if a["last_expenses"] <= 0:
            return {"intent": intent, "answer":
                    f"There's no spending recorded for {a['last_month']}, so I can't compare yet."}
        change = _pct(a["forecast"], a["last_expenses"])
        text = (f"{a['last_month']}: {inr(a['last_expenses'])}. "
                f"{month}: {inr(a['forecast'])} "
                f"({'projected' if a['days_left'] > 0 else 'actual'}), "
                f"a {'rise' if change >= 0 else 'drop'} of {abs(change):.0f}%.")
        moves = sorted((c for c in cats if c["last"] > 0 or c["projected"] > 0),
                       key=lambda c: abs(c["projected"] - c["last"]), reverse=True)[:2]
        if moves:
            text += " Biggest moves: " + "; ".join(
                f"{m['category']} {inr(m['last'])} → {inr(m['projected'])}"
                for m in moves) + "."
        return {"intent": intent, "answer": text}

    if intent == "top":
        top = [c for c in cats if c["actual"] > 0][:3]
        if not top:
            return {"intent": intent, "answer": "No expenses recorded this month yet."}
        text = "Your biggest expenses: " + ", ".join(
            f"{c['category']} {inr(c['actual'])} "
            f"({c['actual'] / a['expenses'] * 100:.0f}%)" for c in top) + "."
        return {"intent": intent, "answer": text}

    if intent == "tips":
        over = [c for c in cats if c["budget"] > 0 and c["projected"] > c["budget"]]
        if over:
            c = max(over, key=lambda x: x["projected"] - x["budget"])
            return {"intent": intent, "answer":
                    f"Start with {c['category']}: it's heading for {inr(c['projected'])} "
                    f"against a {inr(c['budget'])} budget. Trimming "
                    f"{inr(c['projected'] - c['budget'])} there brings it back on track."}
        top = [c for c in cats if c["actual"] > 0
               and c["category"] not in FIXED_COSTS]
        if top:
            return {"intent": intent, "answer":
                    f"Nothing is heading over budget. Your biggest flexible expense is "
                    f"{top[0]['category']} "
                    f"({inr(top[0]['actual'])}); a 10% cut would save "
                    f"{inr(top[0]['actual'] * 0.1)}."}
        return {"intent": intent, "answer": "Add some expenses and I'll point out where to cut."}

    if intent == "savings":
        rate = summary["savings_rate"]
        text = ("I can't work out a savings rate without income this month."
                if rate is None else
                f"Your savings rate this month is {rate:.0f}% "
                f"({inr(a['income'] - a['expenses'])} saved of {inr(a['income'])}).")
        goal = _goal_insight(conn, today)
        return {"intent": intent, "answer": f"{text} {goal}" if goal else text}

    if intent == "budget":
        over = [c for c in cats if c["budget"] > 0 and c["actual"] > c["budget"]]
        parts = []
        if a["overall_budget"] > 0:
            parts.append(f"Overall: {inr(a['expenses'])} of {inr(a['overall_budget'])} used.")
        parts.append(
            "Over budget: " + ", ".join(
                f"{c['category']} (+{inr(c['actual'] - c['budget'])})" for c in over) + "."
            if over else "No category is over its budget.")
        return {"intent": intent, "answer": " ".join(parts)}

    if intent == "health":
        return {"intent": intent, "answer":
                f"Your health score is {summary['score']}/100 ({summary['label']}). "
                + summary["headline"]}

    return {"intent": "unknown", "answer":
            "I can help with: forecasts (“Will I stay within budget?”), "
            "comparisons (“Compare to last month”), top spending, saving tips "
            "and budget status. Try one of those."}
