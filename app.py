from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template,
    render_template_string,
    send_file,
    session,
    flash,
)
import sqlite3
from datetime import datetime, date, timedelta
import io
from openpyxl import Workbook

from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import os

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "gate_app.db")

app = Flask(__name__)


@app.template_filter('date_sr')
def date_sr_filter(value):

    if not value:
        return ''
    s = str(value)

    #  1: Samo datum (2025-12-03)
    if len(s) == 10 and s[4] == '-' and s[7] == '-':
        return f"{s[8:10]}.{s[5:7]}.{s[0:4]}."

    #  2: Datum i vreme (2025-12-03 14:30:00)
    if len(s) >= 16 and s[4] == '-' and s[10] == ' ':
        date_part = f"{s[8:10]}.{s[5:7]}.{s[0:4]}."
        time_part = s[11:16]
        return f"{date_part} {time_part}"

    return s

app.secret_key = "0bd153a46b5adae96c0b202355ece91bff0743114d6122759ddcfd4ba8a0fcbb"

ALL_ROLES = ("admin", "employee", "portirnica", "security_chief")



FORM_LOOKUP_CONFIG = {
    "posete_najava": {
        "label": "Najava posete",
        "fields": [
            {"code": "employee", "label": "Kod koga dolazi"},
            {"code": "object", "label": "Objekat"},
        ],
    },
    "kamioni_unos": {
        "label": "Unos kamiona",
        "fields": [
            {"code": "destination", "label": "Odredište kamiona"},
        ],
    },

}


# DB helperi

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()

    # POSETE
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS visits (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        arrival_date     TEXT NOT NULL,
        expected_time    TEXT,
        host_employee    TEXT NOT NULL,
        phone            TEXT,
        object_name      TEXT NOT NULL,
        guest_name       TEXT NOT NULL,
        document_number  TEXT,
        vehicle_plate    TEXT,
        note             TEXT,
        persons_count    INTEGER,
        entry_time       TEXT,
        exit_time        TEXT
    );
    """
    )

    # KAMIONI

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS trucks (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_name             TEXT NOT NULL,
        driver_document         TEXT,
        codriver_name           TEXT,
        codriver_document       TEXT,
        driver_phone            TEXT,         
        plate                   TEXT NOT NULL,
        destination             TEXT NOT NULL,
        arrival_date            TEXT NOT NULL,
        arrival_time            TEXT NOT NULL,
        departure_datetime      TEXT
    );
    """
    )


    try:
        cur.execute("ALTER TABLE trucks ADD COLUMN driver_phone TEXT")
    except sqlite3.OperationalError:

        pass


    # LOOKUP vrednosti (zaposleni, objekti, odredišta)
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS lookups (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        type    TEXT NOT NULL,
        value   TEXT NOT NULL
    );
    """
    )

    # USERS – login + role
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        email         TEXT UNIQUE NOT NULL,
        full_name     TEXT,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL,
        is_active     INTEGER NOT NULL DEFAULT 1
    );
    """
    )

    # inicijalni korisnici
    default_users = [
        ("nikola.lakovic@logistar.rs", "Nikola Laković", "admin"),
        ("vlado.popovic@logistar.rs", "Vlado Popović", "employee"),
        ("portirnica@logistar.rs", "Portirnica", "portirnica"),
        ("dragisa.removic@logistar.rs", "Šef obezbeđenja", "security_chief"),
    ]

    for email, full_name, role in default_users:
        cur.execute(
            """
            INSERT OR IGNORE INTO users (email, full_name, password_hash, role)
            VALUES (?, ?, ?, ?)
            """,
            (email, full_name, generate_password_hash("1"), role),
        )


    # inicijalne vrednosti za padajuće menije
    initial_employees = [
        "Maja Bogunović",
        "Olivera Radivojević",
        "Vlado Popović",
    ]
    initial_objects = [
        "Upravna zgrada",
        "Skladište",
        "Gigatron",
        "Objekat 9",
    ]


    has_emps = cur.execute(
        "SELECT COUNT(*) AS cnt FROM lookups WHERE type = 'employee'"
    ).fetchone()["cnt"]
    if has_emps == 0:
        cur.executemany(
            "INSERT INTO lookups (type, value) VALUES ('employee', ?)",
            [(e,) for e in initial_employees],
        )

    has_objs = cur.execute(
        "SELECT COUNT(*) AS cnt FROM lookups WHERE type = 'object'"
    ).fetchone()["cnt"]
    if has_objs == 0:
        cur.executemany(
            "INSERT INTO lookups (type, value) VALUES ('object', ?)",
            [(o,) for o in initial_objects],
        )
    conn.commit()
    conn.close()


# Auth helperi i dekoratori

def get_current_user():
    email = session.get("user_email")
    if not email:
        return None

    conn = get_db()
    cur = conn.cursor()
    user = cur.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    conn.close()
    return user


def require_role(*allowed_roles):
    """Dekorator: traži login i odgovarajuću rolu."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            if "user_email" not in session:
                next_url = request.path
                return redirect(url_for("login", next=next_url))

            role = session.get("role")
            if allowed_roles and role not in allowed_roles:
                return redirect(url_for("no_access"))

            return view_func(*args, **kwargs)

        return wrapped_view
    return decorator

@app.route("/debug/users")
def debug_users():
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute("SELECT id, email, full_name, role, is_active FROM users").fetchall()
    conn.close()

    out_lines = []
    for r in rows:
        out_lines.append(
            f"{r['id']} | {r['email']} | {r['full_name']} | {r['role']} | active={r['is_active']}"
        )
    if not out_lines:
        out_lines.append("NEMA NIJEDNOG USERA U TABELI users")

    return "<pre>" + "\n".join(out_lines) + "</pre>"

# Login / logout / profil


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()
        user = cur.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1",
            (email,),
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_email"] = user["email"]
            session["role"] = user["role"]
            session["full_name"] = user["full_name"] or user["email"]

            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)
        else:
            error = "Pogrešan email ili lozinka."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/no-access")
def no_access():
    return render_template("no_access.html"), 403


@app.route("/profile", methods=["GET", "POST"])
@require_role("admin", "employee", "portirnica", "security_chief")
def change_password():
    email = session.get("user_email")
    if not email:
        return redirect(url_for("login"))

    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        old_pw = request.form.get("old_password")
        new_pw = request.form.get("new_password")
        repeat_pw = request.form.get("repeat_password")

        # Provera ponavljanja
        if new_pw != repeat_pw:
            conn.close()
            return render_template("profile.html",
                                   error="Nove lozinke se ne poklapaju!")

        # Provera stare lozinke
        user = cur.execute(
            "SELECT * FROM users WHERE email=?", (email,)
        ).fetchone()


        if not user or not check_password_hash(user["password_hash"], old_pw):
            conn.close()
            return render_template("profile.html",
                                   error="Trenutna lozinka nije ispravna!")

        new_pw_hash = generate_password_hash(new_pw)
        cur.execute("UPDATE users SET password_hash=? WHERE email=?",
                    (new_pw_hash, email))
        conn.commit()
        conn.close()

        return render_template("profile.html",
                               message="Lozinka uspešno promenjena!")

    conn.close()
    return render_template("profile.html")


# Admin – upravljanje korisnicima

@app.route("/admin/users", methods=["GET", "POST"])
@require_role("admin")
def admin_users():
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        ids = [
            row["id"]
            for row in cur.execute("SELECT id FROM users").fetchall()
        ]

        for user_id in ids:
            role_key = f"role_{user_id}"
            active_key = f"active_{user_id}"
            pass_key = f"password_{user_id}"

            role_val = request.form.get(role_key)
            is_active_val = 1 if request.form.get(active_key) == "on" else 0
            new_password = request.form.get(pass_key, "").strip()

            if role_val:
                cur.execute(
                    "UPDATE users SET role = ?, is_active = ? WHERE id = ?",
                    (role_val, is_active_val, user_id),
                )

            if new_password:
                cur.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new_password), user_id),
                )

        # dodavanje novog korisnika
        email_new = request.form.get("email_new", "").strip().lower()
        full_name_new = request.form.get("full_name_new", "").strip() or None
        password_new = request.form.get("password_new", "").strip()
        role_new = request.form.get("role_new", "employee")

        if email_new and password_new:
            try:
                cur.execute(
                    """
                    INSERT INTO users (email, full_name, password_hash, role, is_active)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (
                        email_new,
                        full_name_new,
                        generate_password_hash(password_new),
                        role_new,
                    ),
                )
            except sqlite3.IntegrityError:
                pass

        conn.commit()

    users = cur.execute(
        "SELECT * FROM users ORDER BY email"
    ).fetchall()
    conn.close()

    return render_template(
        "admin_users.html",
        users=users,
        date_today=date.today().strftime("%d.%m.%Y."),
    )


# Početna

@app.route("/")
@require_role(*ALL_ROLES)
def index():
    user = get_current_user()
    return render_template(
        "index.html",
        user=user,
        date_today=date.today().strftime("%d.%m.%Y."),
    )


# 1) Forma za najavu posete (zaposleni)

@app.route("/posete/najava", methods=["GET", "POST"])
@require_role("admin", "employee", "security_chief")
def posete_najava():
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        # Zajednički podaci
        arrival_date_str = request.form["arrival_date"]
        expected_time = request.form["expected_time"]
        host_employee = request.form["host_employee"]
        phone = request.form["phone"]
        object_name = request.form["object_name"]
        guest_name = request.form["guest_name"]
        document_number = request.form["document_number"]
        vehicle_plate = request.form["vehicle_plate"]
        note = request.form["note"]
        persons_count = request.form.get("persons_count") or None
        created_by = session.get("user_email")

        # Provera moda: single ili recurring
        visit_mode = request.form.get("visit_mode", "single")

        visits_to_create = []

        if visit_mode == "recurring":
            date_end_str = request.form.get("date_end")
            # Dani u nedelji koji su čekirani (0=Pon, 1=Uto... 6=Ned)
            allowed_days = request.form.getlist("days")
            allowed_days = [int(d) for d in allowed_days]  # konverzija u int

            if not date_end_str:
                flash("Morate uneti krajnji datum za ponavljanje.", "danger")
                return redirect(url_for("posete_najava"))

            # Konverzija stringova u datum objekte
            start_date = datetime.strptime(arrival_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(date_end_str, "%Y-%m-%d").date()

            if end_date < start_date:
                flash("Krajnji datum ne može biti pre početnog.", "danger")
                return redirect(url_for("posete_najava"))

            # Zaštita od prevelikog broja unosa (max 365 dana)
            if (end_date - start_date).days > 365:
                flash("Period ponavljanja ne može biti duži od godinu dana.", "danger")
                return redirect(url_for("posete_najava"))

            # PETLJA: Idemo dan po dan
            current_date = start_date
            while current_date <= end_date:
                # current_date.weekday() vraća 0 za Ponedeljak, 6 za Nedelju
                if current_date.weekday() in allowed_days:
                    visits_to_create.append(current_date.strftime("%Y-%m-%d"))

                current_date += timedelta(days=1)

            if not visits_to_create:
                flash("Nije izabran nijedan validan datum u zadatom periodu.", "warning")
                return redirect(url_for("posete_najava"))

        else:
            # Samo jedan datum (obična najava)
            visits_to_create.append(arrival_date_str)

        # UPIS U BAZU (Sve pripremljene datume upisujemo)
        try:
            count = 0
            for date_val in visits_to_create:
                cur.execute(
                    """
                    INSERT INTO visits (
                        created_by, arrival_date, expected_time, host_employee, phone, object_name,
                        guest_name, document_number, vehicle_plate, note, persons_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        created_by,
                        date_val,
                        expected_time,
                        host_employee,
                        phone,
                        object_name,
                        guest_name,
                        document_number,
                        vehicle_plate,
                        note,
                        persons_count,
                    ),
                )
                count += 1

            conn.commit()

            if count > 1:
                flash(f"Uspešno kreirano {count} poseta za gosta: {guest_name} (Period).", "success")
            else:
                flash(f"Uspešno sačuvana najava za gosta: {guest_name}", "success")

        except Exception as e:
            conn.rollback()
            flash(f"Došlo je do greške prilikom upisa: {str(e)}", "danger")
        finally:
            conn.close()

        return redirect(url_for("posete_najava"))

    # GET deo ostaje isti
    employees = cur.execute(
        "SELECT value FROM lookups WHERE type='employee' ORDER BY value"
    ).fetchall()
    objects = cur.execute(
        "SELECT value FROM lookups WHERE type='object' ORDER BY value"
    ).fetchall()
    conn.close()

    return render_template(
        "posete_najava.html",
        employees=employees,
        objects=objects,
        date_today=date.today().strftime("%d.%m.%Y."),
    )

# 1b) Poseta bez najave – portirnica ručno unosi gosta


@app.route("/posete/nenajavljena", methods=["GET", "POST"])
@require_role("admin", "portirnica", "security_chief")
def posete_nenajavljena():
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        arrival_date = request.form["arrival_date"]
        host_employee = request.form["host_employee"]
        phone = request.form["phone"]
        object_name = request.form["object_name"]
        guest_name = request.form["guest_name"]
        document_number = request.form["document_number"]
        vehicle_plate = request.form["vehicle_plate"]
        note = request.form["note"]
        persons_count = request.form.get("persons_count") or None
        now = datetime.now().isoformat(sep=" ", timespec="seconds")


        created_by = session.get("user_email")

        cur.execute(
            """
            INSERT INTO visits (
                created_by, arrival_date, expected_time, host_employee, phone, object_name,
                guest_name, document_number, vehicle_plate, note, persons_count,
                entry_time
            )
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_by,
                arrival_date,
                host_employee,
                phone,
                object_name,
                guest_name,
                document_number,
                vehicle_plate,
                note,
                persons_count,
                now,
            ),
        )
        conn.commit()
        conn.close()

        flash(f"Uspešno evidentiran ulaz za gosta: {guest_name}", "success")

        return redirect(url_for("posete_nenajavljena"))

    employees = cur.execute(
        "SELECT value FROM lookups WHERE type='employee' ORDER BY value"
    ).fetchall()
    objects = cur.execute(
        "SELECT value FROM lookups WHERE type='object' ORDER BY value"
    ).fetchall()
    conn.close()

    return render_template(
        "posete_nenajavljena.html",
        employees=employees,
        objects=objects,
        date_today=date.today().strftime("%d.%m.%Y."),
    )


# 2) Portirnica – pregled najavljenih danas

@app.route("/posete/portirnica")
@require_role("admin", "portirnica", "security_chief")
def posete_portirnica():
    today_str = date.today().isoformat()
    conn = get_db()
    cur = conn.cursor()


    rows = cur.execute(
        """
        SELECT * FROM visits
        WHERE arrival_date = ?
          AND NOT (entry_time IS NOT NULL AND exit_time IS NOT NULL)
          AND (status IS NULL OR status != 'cancelled')
        ORDER BY expected_time
        """,
        (today_str,),
    ).fetchall()
    conn.close()

    return render_template(
        "posete_portirnica.html",
        rows=rows,
        date_today=date.today().strftime("%d.%m.%Y."),
    )


@app.route("/posete/evidentiraj-ulaz/<int:visit_id>", methods=["POST"])
@require_role("admin", "portirnica", "security_chief")
def evidentiraj_ulaz(visit_id: int):
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE visits SET entry_time = ? WHERE id = ?", (now, visit_id))
    conn.commit()
    conn.close()
    return redirect(url_for("posete_portirnica"))


@app.route("/posete/evidentiraj-izlaz/<int:visit_id>", methods=["POST"])
@require_role("admin", "portirnica", "security_chief")
def evidentiraj_izlaz(visit_id: int):
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE visits SET exit_time = ? WHERE id = ?", (now, visit_id))
    conn.commit()
    conn.close()
    return redirect(url_for("posete_portirnica"))

# 3) Forma za kamione

@app.route("/kamioni/unos", methods=["GET", "POST"])
@require_role("admin", "portirnica", "security_chief")
def kamioni_unos():
    conn = get_db()
    cur = conn.cursor()
    destinations = cur.execute(
        "SELECT value FROM lookups WHERE type='destination' ORDER BY value"
    ).fetchall()

    if request.method == "POST":
        driver_name = request.form["driver_name"]
        driver_document = request.form["driver_document"]
        codriver_name = request.form["codriver_name"]
        driver_phone = request.form.get("driver_phone")
        codriver_document = request.form["codriver_document"]
        plate = request.form["plate"]
        destination = request.form["destination"]

        #Automatsko generisanje vremena
        now = datetime.now()
        arrival_date = now.strftime("%Y-%m-%d")  # Format za bazu
        arrival_time = now.strftime("%H:%M")  # Format za bazu


        created_by = session.get("user_email")

        cur.execute(
            """
            INSERT INTO trucks (
                created_by, driver_name, driver_document, codriver_name, codriver_document,
                driver_phone, plate, destination, arrival_date, arrival_time
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_by,
                driver_name,
                driver_document,
                codriver_name,
                codriver_document,
                driver_phone,
                plate,
                destination,
                arrival_date,
                arrival_time,
            ),
        )

        conn.commit()
        conn.close()
        flash(f"Uspešno evidentiran ulaz kamiona: {plate}", "success")

        return redirect(url_for("kamioni_unos"))

    conn.close()

    return render_template(
        "kamioni_unos.html",
        destinations=destinations,
        date_today=date.today().strftime("%d.%m.%Y."),
    )

# 4) Portirnica – kamioni na placu


@app.route("/kamioni/portirnica")
@require_role("admin", "portirnica", "security_chief")
def kamioni_portirnica():
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT * FROM trucks
        WHERE departure_datetime IS NULL
        ORDER BY arrival_date, arrival_time
        """
    ).fetchall()
    conn.close()

    return render_template(
        "kamioni_portirnica.html",
        rows=rows,
        date_today=date.today().strftime("%d.%m.%Y."),
    )


@app.route("/kamioni/evidentiraj-izlaz/<int:truck_id>", methods=["POST"])
@require_role("admin", "portirnica", "security_chief")
def kamion_evidentiraj_izlaz(truck_id: int):
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE trucks SET departure_datetime = ? WHERE id = ?",
        (now, truck_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("kamioni_portirnica"))


# 5) Šef obezbeđenja – detaljne evidencije


@app.route("/security/posete", methods=["GET"])
@require_role("admin", "security_chief")
def security_posete():
    conn = get_db()
    cur = conn.cursor()
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    host = (request.args.get("host") or "").strip()
    obj = (request.args.get("object_name") or "").strip()
    guest = (request.args.get("guest_name") or "").strip()

    query = "SELECT * FROM visits WHERE 1=1"
    params = []

    if date_from:
        query += " AND arrival_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND arrival_date <= ?"
        params.append(date_to)
    if host:
        query += " AND host_employee LIKE ?"
        params.append(f"%{host}%")
    if obj:
        query += " AND object_name LIKE ?"
        params.append(f"%{obj}%")
    if guest:
        query += " AND guest_name LIKE ?"
        params.append(f"%{guest}%")

    query += " ORDER BY arrival_date DESC, expected_time"

    rows = cur.execute(query, params).fetchall()
    conn.close()

    return render_template(
        "security_posete.html",
        page_title="Baza poseta",
        rows=rows,
        filters={
            "date_from": date_from or "",
            "date_to": date_to or "",
            "host": host,
            "object_name": obj,
            "guest_name": guest,
        },
        date_today=date.today().strftime("%d.%m.%Y."),
    )


@app.route("/security/kamioni/export")
@require_role("admin", "security_chief")
def security_kamioni_export():
    conn = get_db()
    cur = conn.cursor()

    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    plate = (request.args.get("plate") or "").strip()
    destination = (request.args.get("destination") or "").strip()

    query = "SELECT * FROM trucks WHERE 1=1"
    params = []

    if date_from:
        query += " AND arrival_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND arrival_date <= ?"
        params.append(date_to)
    if plate:
        query += " AND plate LIKE ?"
        params.append(f"%{plate}%")
    if destination:
        query += " AND destination LIKE ?"
        params.append(f"%{destination}%")

    query += " ORDER BY arrival_date DESC, arrival_time DESC"

    rows = cur.execute(query, params).fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Kamioni"

    headers = [
        "ID",
        "Vozač",
        "Dokument vozača",
        "Telefon vozača",
        "Suvozač",
        "Dokument suvozača",
        "Registracija",
        "Odredište",
        "Datum dolaska",
        "Vreme dolaska",
        "Datum/vreme odlaska",
    ]
    ws.append(headers)

    for r in rows:
        ws.append([
            r["id"],
            r["driver_name"],
            r["driver_document"],
            r["driver_phone"],
            r["codriver_name"],
            r["codriver_document"],
            r["plate"],
            r["destination"],
            r["arrival_date"],
            r["arrival_time"],
            r["departure_datetime"],
        ])


    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"baza_kamiona_{date.today().isoformat()}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/security/kamioni/<int:truck_id>/edit", methods=["GET", "POST"])
@require_role("admin")
def security_kamioni_edit(truck_id: int):
    conn = get_db()
    cur = conn.cursor()

    # uzmi kamion
    row = cur.execute(
        "SELECT * FROM trucks WHERE id = ?",
        (truck_id,),
    ).fetchone()

    if row is None:
        conn.close()
        return "Kamion nije pronađen.", 404

    if request.method == "POST":
        driver_name = request.form["driver_name"]
        driver_document = request.form.get("driver_document") or None
        driver_phone = request.form.get("driver_phone") or None
        codriver_name = request.form.get("codriver_name") or None
        codriver_document = request.form.get("codriver_document") or None
        plate = request.form["plate"]
        destination = request.form["destination"]
        arrival_date = request.form["arrival_date"]
        arrival_time = request.form["arrival_time"]
        departure_datetime = request.form.get("departure_datetime") or None

        cur.execute(
            """
            UPDATE trucks
               SET driver_name        = ?,
                   driver_document    = ?,
                   codriver_name      = ?,
                   codriver_document  = ?,
                   driver_phone       = ?,
                   plate              = ?,
                   destination        = ?,
                   arrival_date       = ?,
                   arrival_time       = ?,
                   departure_datetime = ?
             WHERE id = ?
            """,
            (
                driver_name,
                driver_document,
                codriver_name,
                codriver_document,
                driver_phone,
                plate,
                destination,
                arrival_date,
                arrival_time,
                departure_datetime,
                truck_id,
            ),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("security_kamioni"))

    # GET – puni dropdown za odredišta
    destinations = cur.execute(
        "SELECT value FROM lookups WHERE type='destination' ORDER BY value",
    ).fetchall()
    conn.close()

    return render_template(
        "security_kamioni_edit.html",
        truck=row,
        destinations=destinations,
    )


@app.post("/security/kamioni/delete/<int:truck_id>")
@require_role("admin")
def security_kamioni_delete(truck_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM trucks WHERE id = ?", (truck_id,))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("security_kamioni"))


@app.route("/security/posete/export")
@require_role("admin")
def security_posete_export():
    conn = get_db()
    cur = conn.cursor()


    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    host = (request.args.get("host") or "").strip()
    obj = (request.args.get("object_name") or "").strip()
    guest = (request.args.get("guest_name") or "").strip()

    query = "SELECT * FROM visits WHERE 1=1"
    params = []

    if date_from:
        query += " AND arrival_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND arrival_date <= ?"
        params.append(date_to)
    if host:
        query += " AND host_employee LIKE ?"
        params.append(f"%{host}%")
    if obj:
        query += " AND object_name LIKE ?"
        params.append(f"%{obj}%")
    if guest:
        query += " AND guest_name LIKE ?"
        params.append(f"%{guest}%")

    query += " ORDER BY arrival_date DESC, expected_time"

    rows = cur.execute(query, params).fetchall()
    conn.close()


    wb = Workbook()
    ws = wb.active
    ws.title = "Posete"

    headers = [
        "ID",
        "Datum najave",
        "Očekivano vreme",
        "Kod koga dolazi",
        "Objekat",
        "Gost",
        "Telefon",
        "Broj dokumenta",
        "Registracija",
        "Broj osoba",
        "Vreme ulaska",
        "Vreme izlaska",
        "Napomena",
    ]
    ws.append(headers)

    for r in rows:
        ws.append([
            r["id"],
            r["arrival_date"],
            r["expected_time"],
            r["host_employee"],
            r["object_name"],
            r["guest_name"],
            r["phone"],
            r["document_number"],
            r["vehicle_plate"],
            r["persons_count"],
            r["entry_time"],
            r["exit_time"],
            r["note"],
        ])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"baza_poseta_{date.today().isoformat()}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/security/posete/<int:visit_id>/edit", methods=["GET", "POST"])
@require_role("admin")
def security_posete_edit(visit_id: int):
    conn = get_db()
    cur = conn.cursor()

    # uzmi posetu
    row = cur.execute(
        "SELECT * FROM visits WHERE id = ?",
        (visit_id,)
    ).fetchone()

    if row is None:
        conn.close()
        return "Poseta nije pronađena.", 404

    if request.method == "POST":
        arrival_date = request.form["arrival_date"]
        expected_time = request.form.get("expected_time") or None
        host_employee = request.form["host_employee"]
        guest_name = request.form["guest_name"]
        object_name = request.form["object_name"]
        phone = request.form.get("phone") or None
        document_number = request.form.get("document_number") or None
        vehicle_plate = request.form.get("vehicle_plate") or None
        persons_count = request.form.get("persons_count") or None
        note = request.form.get("note") or None
        entry_time = request.form.get("entry_time") or None
        exit_time = request.form.get("exit_time") or None

        cur.execute(
            """
            UPDATE visits
               SET arrival_date    = ?,
                   expected_time   = ?,
                   host_employee   = ?,
                   guest_name      = ?,
                   object_name     = ?,
                   phone           = ?,
                   document_number = ?,
                   vehicle_plate   = ?,
                   persons_count   = ?,
                   note            = ?,
                   entry_time      = ?,
                   exit_time       = ?
             WHERE id = ?
            """,
            (
                arrival_date,
                expected_time,
                host_employee,
                guest_name,
                object_name,
                phone,
                document_number,
                vehicle_plate,
                persons_count,
                note,
                entry_time,
                exit_time,
                visit_id,
            ),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("security_posete"))

    # GET – puni dropdownove
    employees = cur.execute(
        "SELECT value FROM lookups WHERE type='employee' ORDER BY value"
    ).fetchall()
    objects = cur.execute(
        "SELECT value FROM lookups WHERE type='object' ORDER BY value"
    ).fetchall()
    conn.close()

    return render_template(
        "security_posete_edit.html",
        visit=row,
        employees=employees,
        objects=objects,
    )


@app.post("/security/posete/delete/<int:visit_id>")
@require_role("admin", "security_chief")
def security_posete_delete(visit_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM visits WHERE id = ?", (visit_id,))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("security_posete"))

@app.route("/security/kamioni", methods=["GET"])
@require_role("admin", "security_chief")
def security_kamioni():
    conn = get_db()
    cur = conn.cursor()

    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    plate = (request.args.get("plate") or "").strip()
    destination = (request.args.get("destination") or "").strip()

    query = "SELECT * FROM trucks WHERE 1=1"
    params = []

    if date_from:
        query += " AND arrival_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND arrival_date <= ?"
        params.append(date_to)
    if plate:
        query += " AND plate LIKE ?"
        params.append(f"%{plate}%")
    if destination:
        query += " AND destination LIKE ?"
        params.append(f"%{destination}%")

    query += " ORDER BY arrival_date DESC, arrival_time DESC"

    rows = cur.execute(query, params).fetchall()
    conn.close()

    return render_template(
        "security_kamioni.html",
        page_title="Baza kamiona",
        rows=rows,
        filters={
            "date_from": date_from or "",
            "date_to": date_to or "",
            "plate": plate,
            "destination": destination,
        },
        date_today=date.today().strftime("%d.%m.%Y."),
    )


@app.route("/moje-najave")
@require_role("admin", "employee", "portirnica", "security_chief")
def moje_najave():
    user_email = session.get("user_email")
    if not user_email:
        return redirect(url_for("login"))

    conn = get_db()
    cur = conn.cursor()
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    query = "SELECT * FROM visits WHERE created_by = ?"
    params = [user_email]

    if date_from:
        query += " AND arrival_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND arrival_date <= ?"
        params.append(date_to)

    query += """
        ORDER BY 
            CASE WHEN exit_time IS NULL THEN 0 ELSE 1 END, 
            arrival_date DESC, 
            expected_time DESC
    """

    rows = cur.execute(query, params).fetchall()
    conn.close()

    return render_template(
        "moje_najave.html",
        rows=rows,
        filters={"date_from": date_from or "", "date_to": date_to or ""},
        date_today=date.today().strftime("%d.%m.%Y."),
        page_title="Moje najave"
    )


@app.route("/moje-najave/otkazi/<int:visit_id>", methods=["POST"])
@require_role("admin", "employee", "portirnica", "security_chief")
def moje_najave_otkazi(visit_id):
    # Provera da li je to poseta ulogovanog korisnika (ili admina)
    user_email = session.get("user_email")
    role = session.get("role")

    conn = get_db()
    cur = conn.cursor()

    # Proveravamo vlasnistvo
    visit = cur.execute("SELECT created_by FROM visits WHERE id = ?", (visit_id,)).fetchone()

    if not visit:
        conn.close()
        flash("Poseta ne postoji.", "danger")
        return redirect(url_for("moje_najave"))

    # Samo kreator ili admin moze da otkaze
    if visit["created_by"] != user_email and role != "admin":
        conn.close()
        flash("Nemate pravo da otkažete ovu posetu.", "danger")
        return redirect(url_for("moje_najave"))

    # Postavljanje statusa na cancelled
    cur.execute("UPDATE visits SET status = 'cancelled' WHERE id = ?", (visit_id,))
    conn.commit()
    conn.close()

    flash("Poseta uspešno otkazana.", "success")
    return redirect(url_for("moje_najave"))


@app.route("/moje-najave/promeni-datum/<int:visit_id>", methods=["POST"])
@require_role("admin", "employee", "portirnica", "security_chief")
def moje_najave_promeni_datum(visit_id):
    user_email = session.get("user_email")
    role = session.get("role")
    new_date = request.form.get("new_date")

    if not new_date:
        flash("Morate izabrati novi datum.", "warning")
        return redirect(url_for("moje_najave"))

    conn = get_db()
    cur = conn.cursor()

    visit = cur.execute("SELECT created_by FROM visits WHERE id = ?", (visit_id,)).fetchone()

    if not visit:
        conn.close()
        flash("Poseta ne postoji.", "danger")
        return redirect(url_for("moje_najave"))

    if visit["created_by"] != user_email and role != "admin":
        conn.close()
        flash("Nemate pravo izmene.", "danger")
        return redirect(url_for("moje_najave"))

    cur.execute("UPDATE visits SET arrival_date = ? WHERE id = ?", (new_date, visit_id))
    conn.commit()
    conn.close()

    flash(f"Datum posete uspešno promenjen na {new_date}.", "success")
    return redirect(url_for("moje_najave"))


# Basic smoke tests

def _run_basic_tests() -> None:
    init_db()

    with app.test_client() as client:
        resp = client.post(
            "/login",
            data={"email": "nikola.lakovic@logistar.rs", "password": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        for path in [
            "/",
            "/posete/najava",
            "/posete/nenajavljena",
            "/posete/portirnica",
            "/kamioni/unos",
            "/kamioni/portirnica",
            "/admin/users",
            "/security/posete",
            "/security/kamioni",
        ]:
            r = client.get(path)
            assert r.status_code == 200, f"GET {path} failed with {r.status_code}"

    print("Basic Flask route tests passed.")

@app.route("/admin/lookups", methods=["GET", "POST"])
@require_role("admin")
def admin_lookups():
    form_code = request.args.get("form") or "posete_najava"
    field_code = request.args.get("field")

    form_cfg = FORM_LOOKUP_CONFIG.get(form_code)
    if not form_cfg:
        form_cfg = next(iter(FORM_LOOKUP_CONFIG.values()))

    if request.method == "POST" and field_code:
        new_value = (request.form.get("new_value") or "").strip()
        if new_value:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO lookups (type, value) VALUES (?, ?)",
                (field_code, new_value),
            )
            conn.commit()
            conn.close()


        return redirect(url_for("admin_lookups", form=form_code, field=field_code))

    rows = []
    if field_code:
        conn = get_db()
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT id, value FROM lookups WHERE type = ? ORDER BY value",
            (field_code,),
        ).fetchall()
        conn.close()

    return render_template(
        "admin_lookups.html",
        form_configs=FORM_LOOKUP_CONFIG,
        form_code=form_code,
        field_code=field_code,
        form_cfg=form_cfg,
        rows=rows,
        date_today=date.today().strftime("%d.%m.%Y."),
    )

if __name__ == "__main__":
    _run_basic_tests()
