
import io
import os
import tempfile
import unittest
from datetime import date

import database
import app as application
import adaptive


class ExpenseTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database.DB_PATH = os.path.join(self.temp.name, "test.db")
        database.init_db()
        self.client = application.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def transaction(self, amount=500, **extra):
        data = {
            "type": "expense",
            "amount": amount,
            "category": "Food",
            "description": "Lunch",
            "date": "2026-10-10"
        }
        data.update(extra)
        return data

    def test_create_and_list_transaction(self):
        response = self.client.post(
            "/api/transactions", json=self.transaction()
        )
        self.assertEqual(response.status_code, 201)
        rows = self.client.get("/api/transactions?month=2026-10").json
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "Food")

    def test_invalid_transaction_rejected(self):
        response = self.client.post(
            "/api/transactions", json=self.transaction(amount=-5)
        )
        self.assertEqual(response.status_code, 400)

    def test_edit_and_delete_transaction(self):
        created = self.client.post(
            "/api/transactions", json=self.transaction()
        ).json
        transaction_id = created["id"]

        updated = self.client.put(
            f"/api/transactions/{transaction_id}",
            json=self.transaction(750, description="Dinner")
        )
        self.assertEqual(updated.status_code, 200)

        rows = self.client.get("/api/transactions?month=2026-10").json
        self.assertEqual(rows[0]["amount"], 750)
        self.assertEqual(rows[0]["description"], "Dinner")

        deleted = self.client.delete(
            f"/api/transactions/{transaction_id}"
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            self.client.get("/api/transactions?month=2026-10").json, []
        )

    def test_dashboard_totals(self):
        self.client.post("/api/transactions", json={
            "type": "income", "amount": 20000, "category": "Salary",
            "description": "Pay", "date": "2026-10-01"
        })
        self.client.post(
            "/api/transactions", json=self.transaction(5000)
        )
        data = self.client.get("/api/dashboard?month=2026-10").json
        self.assertEqual(data["income"], 20000)
        self.assertEqual(data["expenses"], 5000)
        self.assertEqual(data["balance"], 15000)

    def test_budget_and_goal(self):
        budget = self.client.post("/api/budgets", json={
            "month": "2026-10", "category": "Food", "amount": 3000
        })
        self.assertEqual(budget.status_code, 200)

        goal = self.client.post("/api/goals", json={
            "name": "Laptop", "target": 50000, "saved": 5000, "deadline": ""
        })
        self.assertEqual(goal.status_code, 201)
        self.assertEqual(len(self.client.get("/api/goals").json), 1)

    def test_csv_export(self):
        self.client.post("/api/transactions", json=self.transaction())
        response = self.client.get("/api/export?month=2026-10")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.content_type)
        self.assertIn(b"Amount (INR)", response.data)

    def test_invalid_month(self):
        response = self.client.get("/api/dashboard?month=October")
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # CSV import
    # ------------------------------------------------------------------
    def upload(self, text, name="data.csv", mode="auto"):
        return self.client.post("/api/import", data={
            "file": (io.BytesIO(text.encode("utf-8")), name),
            "mode": mode,
        }, content_type="multipart/form-data")

    def test_import_spendlab_format(self):
        csv_text = (
            "ID,Date,Type,Category,Description,Amount (INR)\n"
            "1,2026-09-01,income,Salary,Pay,40000\n"
            "2,2026-09-02,expense,Food,Lunch,250.50\n"
        )
        res = self.upload(csv_text)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["imported"], 2)
        self.assertEqual(res.json["months"], ["2026-09"])
        rows = self.client.get("/api/transactions?month=2026-09").json
        self.assertEqual(len(rows), 2)

    def test_import_is_idempotent(self):
        csv_text = "Date,Type,Category,Description,Amount\n2026-09-02,expense,Food,Lunch,250\n"
        self.assertEqual(self.upload(csv_text).json["imported"], 1)
        again = self.upload(csv_text).json
        self.assertEqual(again["imported"], 0)
        self.assertEqual(again["duplicates"], 1)

    def test_import_keeps_genuine_repeat_rows(self):
        csv_text = (
            "Date,Type,Category,Description,Amount\n"
            "2026-09-02,expense,Food,Tea,20\n"
            "2026-09-02,expense,Food,Tea,20\n"
        )
        self.assertEqual(self.upload(csv_text).json["imported"], 2)

    def test_import_bank_statement_and_autocategorise(self):
        csv_text = (
            "Txn Date;Narration;Debit;Credit\n"
            "05/09/2026;Swiggy order;\"1,200.00\";\n"
            "06/09/2026;Salary credit;;50000\n"
            "07/09/2026;Mystery shop;300;\n"
        )
        res = self.upload(csv_text)
        self.assertEqual(res.status_code, 200, res.json)
        self.assertEqual(res.json["imported"], 3)
        rows = {r["description"]: r for r in
                self.client.get("/api/transactions?month=2026-09").json}
        self.assertEqual(rows["Swiggy order"]["category"], "Food")
        self.assertEqual(rows["Swiggy order"]["type"], "expense")
        self.assertEqual(rows["Swiggy order"]["amount"], 1200)
        self.assertEqual(rows["Salary credit"]["type"], "income")
        self.assertEqual(rows["Salary credit"]["category"], "Salary")
        self.assertEqual(rows["Mystery shop"]["category"], "Other")

    def test_import_signed_amounts_and_mode(self):
        csv_text = "Date,Description,Amount\n2026-09-01,Coffee,-80\n2026-09-02,Refund,500\n"
        self.upload(csv_text)
        rows = {r["description"]: r["type"] for r in
                self.client.get("/api/transactions?month=2026-09").json}
        self.assertEqual(rows, {"Coffee": "expense", "Refund": "income"})

        res = self.upload("Date,Description,Amount\n2026-08-01,Lunch,90\n", mode="expense")
        self.assertEqual(res.json["imported"], 1)
        row = self.client.get("/api/transactions?month=2026-08").json[0]
        self.assertEqual(row["type"], "expense")

    def test_import_reports_bad_rows_but_keeps_good_ones(self):
        csv_text = (
            "Date,Type,Description,Amount\n"
            "2026-09-01,expense,Good,100\n"
            "not-a-date,expense,Bad date,100\n"
            "2026-09-03,expense,Bad amount,abc\n"
            "2026-09-04,sideways,Bad type,10\n"
        )
        res = self.upload(csv_text)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["imported"], 1)
        self.assertEqual(res.json["error_count"], 3)

    def test_import_rejects_bad_files(self):
        self.assertEqual(self.upload("Name,Age\nA,1\n").status_code, 400)
        self.assertEqual(self.upload("", name="x.csv").status_code, 400)
        self.assertEqual(self.upload("Date,Amount\n2026-01-01,5\n", name="x.exe").status_code, 400)
        self.assertEqual(self.client.post("/api/import").status_code, 400)

    # ------------------------------------------------------------------
    # Adaptive engine: one-month baseline
    # ------------------------------------------------------------------
    def add(self, day, amount, category="Food", kind="expense"):
        self.client.post("/api/transactions", json={
            "type": kind, "amount": amount, "category": category,
            "description": "x", "date": day})

    def test_baseline_is_last_month_only(self):
        # Two months back is huge; it must be ignored.
        self.add("2026-08-10", 90000)
        self.add("2026-09-10", 1000)
        with database.get_db() as conn:
            a = adaptive.analyze(conn, "2026-10", date(2026, 10, 31))
        food = next(c for c in a["categories"] if c["category"] == "Food")
        self.assertEqual(food["last"], 1000)

    def test_previous_month_wraps_year(self):
        self.assertEqual(adaptive.previous_months("2026-01"), ["2025-12"])

    def test_over_budget_recommendation(self):
        self.add("2026-09-10", 1000)
        self.add("2026-10-05", 3500)
        self.client.post("/api/budgets", json={"month": "2026-10", "category": "Food", "amount": 3000})
        with database.get_db() as conn:
            recs = adaptive.calculate_recommendations(conn, "2026-10", date(2026, 10, 20))
        rec = recs[0]
        self.assertEqual((rec["category"], rec["kind"], rec["severity"]), ("Food", "over", "high"))
        self.assertGreaterEqual(rec["suggested_budget"], 3500)
        self.assertIn("over your", rec["reason"])

    def test_pace_forecast_warns_before_overspending(self):
        for day in ("2026-10-02", "2026-10-05", "2026-10-08"):
            self.add(day, 700)
        self.client.post("/api/budgets", json={"month": "2026-10", "category": "Food", "amount": 3000})
        with database.get_db() as conn:
            recs = adaptive.calculate_recommendations(conn, "2026-10", date(2026, 10, 10))
        self.assertEqual(recs[0]["kind"], "pace")
        self.assertGreater(recs[0]["projected"], 3000)

    def test_overall_budget_not_treated_as_category_budget(self):
        self.add("2026-10-02", 2000)
        self.client.post("/api/budgets", json={"month": "2026-10", "category": "*", "amount": 1000})
        with database.get_db() as conn:
            recs = adaptive.calculate_recommendations(conn, "2026-10", date(2026, 10, 31))
        self.assertEqual(recs[0]["kind"], "new")  # no category budget yet

    def test_single_fixed_bill_is_not_extrapolated(self):
        # Rent paid once early in the month must not be projected x3.
        self.add("2026-09-01", 12000, "Rent")
        self.add("2026-10-02", 12000, "Rent")
        with database.get_db() as conn:
            a = adaptive.analyze(conn, "2026-10", date(2026, 10, 5))
        rent = next(c for c in a["categories"] if c["category"] == "Rent")
        self.assertEqual(rent["projected"], 12000)

    def test_ask_intents(self):
        self.add("2026-10-02", 700)
        for question, intent in [("Where can I cut spending?", "tips"),
                                 ("What are my biggest expenses?", "top"),
                                 ("Compare with last month", "compare"),
                                 ("Will I stay within budget this month?", "forecast"),
                                 ("How are my savings?", "savings")]:
            res = self.client.post("/api/advisor/ask", json={"question": question, "month": "2026-10"}).json
            self.assertEqual(res["intent"], intent, question)

    def test_lower_budget_when_underused(self):
        self.add("2026-09-10", 500)
        self.add("2026-10-03", 400)
        self.client.post("/api/budgets", json={"month": "2026-10", "category": "Food", "amount": 3000})
        with database.get_db() as conn:
            recs = adaptive.calculate_recommendations(conn, "2026-10", date(2026, 10, 28))
        self.assertEqual(recs[0]["kind"], "lower")
        self.assertLess(recs[0]["suggested_budget"], 3000)

    def test_decision_is_preserved_after_refresh(self):
        self.add("2026-09-10", 1000)
        self.add("2026-10-05", 3500)
        self.client.post("/api/budgets", json={"month": "2026-10", "category": "Food", "amount": 3000})
        item = self.client.get("/api/suggestions?month=2026-10").json[0]
        self.assertIn("confidence", item)
        self.client.post(f"/api/suggestions/{item['id']}/decision", json={"status": "rejected"})
        self.add("2026-10-06", 100)
        again = self.client.get("/api/suggestions?month=2026-10").json
        self.assertEqual(again[0]["status"], "rejected")

    def test_advisor_summary_and_ask(self):
        empty = self.client.get("/api/advisor?month=2026-10").json
        self.assertIsNone(empty["score"])

        self.add("2026-10-01", 30000, "Salary", "income")
        self.add("2026-09-10", 1000)
        self.add("2026-10-05", 2500)
        data = self.client.get("/api/advisor?month=2026-10").json
        self.assertIsNotNone(data["score"])
        self.assertTrue(data["insights"])

        ask = self.client.post("/api/advisor/ask", json={
            "question": "What are my biggest expenses?", "month": "2026-10"}).json
        self.assertEqual(ask["intent"], "top")
        self.assertIn("Food", ask["answer"])
        self.assertEqual(self.client.post("/api/advisor/ask", json={"question": " "}).status_code, 400)

    def test_old_database_is_migrated(self):
        import sqlite3
        path = os.path.join(self.temp.name, "old.db")
        conn = sqlite3.connect(path)
        conn.executescript("""CREATE TABLE suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL,
            category TEXT NOT NULL, old_budget REAL NOT NULL,
            suggested_budget REAL NOT NULL, reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', UNIQUE(month, category));""")
        conn.commit(); conn.close()
        database.DB_PATH = path
        database.init_db()
        with database.get_db() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(suggestions)")}
        self.assertTrue({"kind", "severity", "confidence", "projected", "last_month"} <= cols)


if __name__ == "__main__":
    unittest.main()
