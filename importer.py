"""CSV import for SpendLab.

Accepts SpendLab's own export as well as typical bank-statement CSVs:
  * column names are matched flexibly ("Txn Date", "Narration", "Debit"...)
  * amounts may have ₹ / Rs / commas, or be negative / in (brackets)
  * dates may be YYYY-MM-DD or day-first (DD/MM/YYYY, 10 Oct 2026...)
  * a missing or unknown category is guessed from the description
"""
import csv
import io
import math
import re
from datetime import date, datetime

from database import CATEGORIES

MAX_ROWS = 5000
MODES = ("auto", "expense", "income")

ALIASES = {
    "date": {"date", "transaction date", "txn date", "value date",
             "posting date", "booking date"},
    "description": {"description", "desc", "note", "notes", "narration",
                    "details", "memo", "remarks", "particulars", "payee"},
    "category": {"category", "cat"},
    "type": {"type", "kind", "transaction type", "txn type", "dr/cr", "drcr"},
    "amount": {"amount", "amount inr", "amt", "value", "transaction amount"},
    "debit": {"debit", "withdrawal", "withdrawals", "debit amount",
              "paid out", "dr"},
    "credit": {"credit", "deposit", "deposits", "credit amount",
               "paid in", "cr"},
}

INCOME_WORDS = {"income", "credit", "cr", "deposit", "in", "received"}
EXPENSE_WORDS = {"expense", "debit", "dr", "withdrawal", "out", "spent"}

DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y",
    "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
    "%d-%b-%Y", "%d-%b-%y",
)

# Whole-word keyword hints used when a category is missing or unrecognised.
KEYWORDS = {
    "expense": {
        "Food": {"swiggy", "zomato", "restaurant", "cafe", "coffee", "lunch",
                 "dinner", "breakfast", "grocery", "groceries", "food",
                 "pizza", "biryani", "snack", "snacks", "tea", "bigbasket",
                 "blinkit", "zepto", "bakery"},
        "Transport": {"uber", "ola", "rapido", "petrol", "diesel", "fuel",
                      "metro", "bus", "train", "auto", "cab", "parking",
                      "toll", "fastag"},
        "Shopping": {"amazon", "flipkart", "myntra", "shopping", "mall",
                     "clothes", "shoes", "meesho"},
        "Bills": {"electricity", "recharge", "wifi", "broadband", "internet",
                  "bill", "water", "gas", "insurance", "jio", "airtel",
                  "postpaid", "prepaid"},
        "Education": {"tuition", "course", "books", "book", "exam", "college",
                      "fees", "fee", "udemy", "school", "coaching"},
        "Health": {"pharmacy", "medicine", "medicines", "doctor", "hospital",
                   "clinic", "medical", "gym", "apollo", "dentist"},
        "Entertainment": {"netflix", "movie", "movies", "cinema", "spotify",
                          "prime", "hotstar", "game", "games", "concert",
                          "bookmyshow"},
        "Rent": {"rent", "landlord", "hostel", "pg"},
        "Travel": {"flight", "hotel", "irctc", "makemytrip", "trip",
                   "holiday", "airbnb", "resort"},
    },
    "income": {
        "Salary": {"salary", "payroll", "wages", "stipend"},
        "Freelance": {"freelance", "client", "invoice"},
        "Business": {"business", "sales", "revenue"},
        "Investment": {"dividend", "interest", "mutual", "sip", "stocks",
                       "returns"},
    },
}


# --------------------------------------------------------------------------
# Cell parsers
# --------------------------------------------------------------------------
def normalise_header(text):
    text = text.replace("\ufeff", "").lower()
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^a-z/ ]", " ", text)
    return " ".join(text.split())


def parse_amount(text):
    """Return (absolute_value, is_negative) or None if not a number."""
    s = str(text or "").strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    s = re.sub(r"(?i)rs\.?|inr|₹|\s|,", "", s)
    if s.startswith("-"):
        negative, s = True, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    try:
        value = float(s)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return abs(value), negative


def parse_date(text):
    s = str(text or "").strip()
    if not s:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}", s):          # ISO, maybe with a time
        s = s[:10]
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        if 1990 <= parsed.year <= 2100:
            return parsed.isoformat()
    return None


def parse_type(text):
    t = str(text or "").strip().lower()
    if t in INCOME_WORDS:
        return "income"
    if t in EXPENSE_WORDS:
        return "expense"
    return None


def guess_category(kind, *texts):
    words = set(re.findall(r"[a-z]+", " ".join(texts).lower()))
    for category, hints in KEYWORDS[kind].items():
        if words & hints:
            return category
    return None


# --------------------------------------------------------------------------
# File parsing
# --------------------------------------------------------------------------
def decode(raw):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def parse_csv(raw, mode="auto"):
    """Parse CSV bytes.

    Returns dict(rows, errors, error_count, total_rows, auto_categorized)
    where rows are (type, amount, category, description, date) tuples
    ready for insertion. Raises ValueError for unusable files.
    """
    if mode not in MODES:
        raise ValueError("Invalid import mode.")
    text = decode(raw)
    if not text.strip():
        raise ValueError("The file is empty.")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text, newline=""), dialect)

    header = None
    for cells in reader:
        if any(c.strip() for c in cells):
            header = cells
            break
    if header is None:
        raise ValueError("The file is empty.")

    index = {}
    for position, cell in enumerate(header):
        name = normalise_header(cell)
        for field, names in ALIASES.items():
            if name in names and field not in index:
                index[field] = position
    has_split = "debit" in index or "credit" in index
    if "date" not in index or ("amount" not in index and not has_split):
        found = ", ".join(c.strip() for c in header if c.strip()) or "none"
        raise ValueError(
            "The CSV needs at least a Date column and an Amount column "
            f"(or Debit/Credit columns). Columns found: {found}."
        )

    def cell(cells, field):
        pos = index.get(field)
        return cells[pos].strip() if pos is not None and pos < len(cells) else ""

    rows, errors, auto_categorized, total = [], [], 0, 0
    error_count = 0

    def fail(line, message):
        nonlocal error_count
        error_count += 1
        if len(errors) < 20:
            errors.append({"row": line, "message": message})

    for cells in reader:
        if not any(c.strip() for c in cells):
            continue
        total += 1
        if total > MAX_ROWS:
            raise ValueError(f"Too many rows. Import at most {MAX_ROWS} at a time.")
        line = reader.line_num

        day = parse_date(cell(cells, "date"))
        if not day:
            fail(line, f"Unrecognised date “{cell(cells, 'date')}”.")
            continue

        # ---- work out type and amount ----------------------------------
        kind, amount = None, None
        if has_split and "amount" not in index:
            debit = parse_amount(cell(cells, "debit"))
            credit = parse_amount(cell(cells, "credit"))
            d = debit[0] if debit else 0
            c = credit[0] if credit else 0
            if d > 0 and c > 0:
                fail(line, "Row has both a debit and a credit amount.")
                continue
            if d > 0:
                kind, amount = "expense", d
            elif c > 0:
                kind, amount = "income", c
            else:
                fail(line, "No debit or credit amount.")
                continue
        else:
            parsed = parse_amount(cell(cells, "amount"))
            if parsed is None:
                fail(line, f"Invalid amount “{cell(cells, 'amount')}”.")
                continue
            amount, negative = parsed
            raw_type = cell(cells, "type")
            if raw_type:
                kind = parse_type(raw_type)
                if kind is None:
                    fail(line, f"Unknown type “{raw_type}” (use income or expense).")
                    continue
            elif mode == "expense":
                kind = "expense"
            elif mode == "income":
                kind = "income"
            else:
                kind = "expense" if negative else "income"

        if not 0 < amount <= 1_000_000_000:
            fail(line, "Amount must be greater than zero and at most 1 billion.")
            continue

        description = cell(cells, "description")[:200]
        raw_category = cell(cells, "category")
        category = next((c for c in CATEGORIES[kind]
                         if c.lower() == raw_category.lower()), None)
        if category is None:
            category = guess_category(kind, raw_category, description)
            if category:
                auto_categorized += 1
            else:
                category = "Other"

        rows.append((kind, round(amount, 2), category, description, day))

    return {
        "rows": rows,
        "errors": errors,
        "error_count": error_count,
        "total_rows": total,
        "auto_categorized": auto_categorized,
    }


SAMPLE_CSV = (
    "Date,Type,Category,Description,Amount\r\n"
    "2026-09-01,income,Salary,Monthly salary,45000\r\n"
    "2026-09-03,expense,Food,Groceries,1850.50\r\n"
    "2026-09-05,expense,Transport,Metro card recharge,500\r\n"
    "2026-09-12,expense,,Netflix subscription,649\r\n"
)
