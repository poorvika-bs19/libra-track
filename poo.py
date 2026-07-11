import functools
import json
import re
import threading
import time
from datetime import date, datetime

from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "dev-secret-key"


# ---------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------
def log_call(func):
    """functools.wraps preserves the original function's name/docs."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        print(f"[LOG] Calling {func._name_}{args[1:]}")
        result = func(*args, **kwargs)
        print(f"[LOG] {func._name_} -> {result}")
        return result

    return wrapper


# ---------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------
class Book:
    def __init__(self, title, author, isbn):
        self.title = title
        self.author = author
        self.isbn = isbn
        self.is_issued = False
        self.issued_to = None  # member_id
        self.issue_date = None

    def __str__(self):
        status = f"Issued to {self.issued_to}" if self.is_issued else "Available"
        return f"'{self.title}' by {self.author} (ISBN: {self.isbn}) [{status}]"

    def __lt__(self, other):
        return self.title.lower() < other.title.lower()


class Member:
    EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}$")
    PHONE_RE = re.compile(r"^\d{10}$")

    def __init__(self, name, member_id, email, phone):
        if not self.EMAIL_RE.match(email):
            raise ValueError(f"Invalid email format: {email}")
        if not self.PHONE_RE.match(phone):
            raise ValueError(f"Invalid phone number (expected 10 digits): {phone}")
        self.name = name
        self.member_id = member_id
        self.email = email
        self.phone = phone
        self.max_books = 2
        self.books_held = []

    def __str__(self):
        return f"{self.name} ({self.member_id}) - {self.__class__.__name__}"


class Student(Member):
    def __init__(self, name, member_id, email, phone, roll_no):
        super().__init__(name, member_id, email, phone)
        self.roll_no = roll_no
        self.max_books = 3


class Faculty(Member):
    def __init__(self, name, member_id, email, phone, department):
        super().__init__(name, member_id, email, phone)
        self.department = department
        self.max_books = 6


# ---------------------------------------------------------------------
# Fine hierarchy (polymorphism)
# ---------------------------------------------------------------------
class Fine:
    RATE_PER_DAY = 1.0

    def calculate(self, days_late):
        if days_late <= 0:
            return 0.0
        return days_late * self.RATE_PER_DAY


class StudentFine(Fine):
    RATE_PER_DAY = 0.5


class FacultyFine(Fine):
    def calculate(self, days_late):
        return 0.0  # faculty exempt from fines


def get_fine_calculator(member):
    """Return the correct Fine object based on member type (polymorphism)."""
    if isinstance(member, Student):
        return StudentFine()
    elif isinstance(member, Faculty):
        return FacultyFine()
    return Fine()


# ---------------------------------------------------------------------
# Library — the main orchestrator
# ---------------------------------------------------------------------
class Library:
    def __init__(self):
        self.books = {}    # isbn -> Book
        self.members = {}  # member_id -> Member

    def add_book(self, book):
        self.books[book.isbn] = book
        return f"Added book: {book}"

    def register_member(self, member):
        self.members[member.member_id] = member
        return f"Registered member: {member}"

    @log_call
    def issue_book(self, isbn, member_id):
        book = self.books.get(isbn)
        member = self.members.get(member_id)

        if not book:
            return "Error: Book not found."
        if not member:
            return "Error: Member not found."
        if book.is_issued:
            return f"Error: '{book.title}' is already issued."
        if len(member.books_held) >= member.max_books:
            return f"Error: {member.name} has reached the max limit of {member.max_books} books."

        book.is_issued = True
        book.issued_to = member_id
        book.issue_date = date.today()
        member.books_held.append(isbn)
        return f"'{book.title}' issued to {member.name}."

    @log_call
    def return_book(self, isbn, days_late=0):
        book = self.books.get(isbn)
        if not book or not book.is_issued:
            return "Error: Book not found or was not issued."

        member = self.members.get(book.issued_to)
        fine_calculator = get_fine_calculator(member)
        fine_amount = fine_calculator.calculate(days_late)

        member.books_held.remove(isbn)
        book.is_issued = False
        book.issued_to = None
        book.issue_date = None

        if fine_amount > 0:
            return f"'{book.title}' returned. Fine due: Rs.{fine_amount:.2f}"
        return f"'{book.title}' returned. No fine."

    def issued_titles(self):
        return [b.title for b in self.books.values() if b.is_issued]

    def isbn_to_title_map(self):
        return {isbn: b.title for isbn, b in self.books.items()}

    def member_books_map(self):
        return {m.member_id: list(m.books_held) for m in self.members.values() if m.books_held}

    def unique_authors(self):
        return {b.author for b in self.books.values()}

    def overdue_batches(self, max_days_allowed=14, batch_size=2):
        overdue = [
            b for b in self.books.values()
            if b.is_issued and (date.today() - b.issue_date).days > max_days_allowed
        ]
        for i in range(0, len(overdue), batch_size):
            yield overdue[i:i + batch_size]

    def search_by_title(self, pattern):
        regex = re.compile(pattern, re.IGNORECASE)
        return [b for b in self.books.values() if regex.search(b.title)]

    def send_overdue_reminders(self):
        overdue_books = [b for b in self.books.values() if b.is_issued]
        log = []
        lock = threading.Lock()

        def notify(member_id, book_title):
            time.sleep(0.3)  # simulate network/email delay
            member = self.members.get(member_id)
            name = member.name if member else member_id
            with lock:
                log.append(f"Reminder sent to {name} about '{book_title}'")

        threads = [
            threading.Thread(target=notify, args=(b.issued_to, b.title))
            for b in overdue_books
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        log.append("All reminders sent.")
        return log

    def save_state(self, filepath="library_state.json"):
        data = {
            "books": [
                {
                    "title": b.title, "author": b.author, "isbn": b.isbn,
                    "is_issued": b.is_issued, "issued_to": b.issued_to,
                    "issue_date": b.issue_date.isoformat() if b.issue_date else None,
                }
                for b in self.books.values()
            ],
            "members": [
                {
                    "type": m.__class__.__name__, "name": m.name, "member_id": m.member_id,
                    "email": m.email, "phone": m.phone, "books_held": m.books_held,
                    "roll_no": getattr(m, "roll_no", None),
                    "department": getattr(m, "department", None),
                }
                for m in self.members.values()
            ],
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        return f"State saved to {filepath}"

    def load_state(self, filepath="library_state.json"):
        with open(filepath, "r") as f:
            data = json.load(f)

        self.books.clear()
        self.members.clear()

        for bd in data["books"]:
            book = Book(bd["title"], bd["author"], bd["isbn"])
            book.is_issued = bd["is_issued"]
            book.issued_to = bd["issued_to"]
            book.issue_date = datetime.fromisoformat(bd["issue_date"]).date() if bd["issue_date"] else None
            self.books[book.isbn] = book

        for md in data["members"]:
            if md["type"] == "Student":
                member = Student(md["name"], md["member_id"], md["email"], md["phone"], md["roll_no"])
            elif md["type"] == "Faculty":
                member = Faculty(md["name"], md["member_id"], md["email"], md["phone"], md["department"])
            else:
                member = Member(md["name"], md["member_id"], md["email"], md["phone"])
            member.books_held = md["books_held"]
            self.members[member.member_id] = member

        return f"State loaded from {filepath}"


# ---------------------------------------------------------------------
# Seed data + global library instance (in-memory, resets on restart)
# ---------------------------------------------------------------------
library = Library()


def seed_demo_data():
    library.add_book(Book("Fluent Python", "Luciano Ramalho", "ISBN-001"))
    library.add_book(Book("Clean Code", "Robert Martin", "ISBN-002"))
    library.add_book(Book("Python Tricks", "Dan Bader", "ISBN-003"))
    library.add_book(Book("Automate the Boring Stuff", "Al Sweigart", "ISBN-004"))
    library.register_member(Student("Anand", "M001", "anand@example.com", "9876543210", roll_no="R101"))
    library.register_member(Faculty("Divya", "M002", "divya@example.com", "9123456789", department="CSE"))


seed_demo_data()


# ---------------------------------------------------------------------
# Flask routes (the website)
# ---------------------------------------------------------------------
@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    books = library.search_by_title(q) if q else list(library.books.values())
    books = sorted(books)
    return render_template(
        "index.html",
        books=books,
        members=list(library.members.values()),
        query=q,
        unique_authors=sorted(library.unique_authors()),
    )


@app.route("/add_book", methods=["POST"])
def add_book():
    title = request.form["title"].strip()
    author = request.form["author"].strip()
    isbn = request.form["isbn"].strip()
    if isbn in library.books:
        flash(f"A book with ISBN {isbn} already exists.", "error")
    else:
        flash(library.add_book(Book(title, author, isbn)), "success")
    return redirect(url_for("index"))


@app.route("/register_member", methods=["POST"])
def register_member():
    kind = request.form["kind"]
    name = request.form["name"].strip()
    member_id = request.form["member_id"].strip()
    email = request.form["email"].strip()
    phone = request.form["phone"].strip()
    try:
        if kind == "Student":
            member = Student(name, member_id, email, phone, request.form.get("roll_no", "").strip())
        else:
            member = Faculty(name, member_id, email, phone, request.form.get("department", "").strip())
        flash(library.register_member(member), "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("index"))


@app.route("/issue", methods=["POST"])
def issue():
    result = library.issue_book(request.form["isbn"], request.form["member_id"])
    flash(result, "error" if result.startswith("Error") else "success")
    return redirect(url_for("index"))


@app.route("/return", methods=["POST"])
def do_return():
    isbn = request.form["isbn"]
    days_late = int(request.form.get("days_late") or 0)
    result = library.return_book(isbn, days_late)
    flash(result, "error" if result.startswith("Error") else "success")
    return redirect(url_for("index"))


@app.route("/reminders")
def reminders():
    log = library.send_overdue_reminders()
    for line in log:
        flash(line, "info")
    return redirect(url_for("index"))


@app.route("/save")
def save():
    flash(library.save_state(), "success")
    return redirect(url_for("index"))


@app.route("/load")
def load():
    try:
        flash(library.load_state(), "success")
    except FileNotFoundError:
        flash("No saved state file found yet — click Save first.", "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)