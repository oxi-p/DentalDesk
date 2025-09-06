import os
import sqlite3
import logging
from contextlib import contextmanager
from typing import List, Optional
from datetime import datetime

from shared.models import Dentist, Patient, Appointment, Message, Conversation, AppointmentWithDetails

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "../..", "data", "dentaldesk_app.db")
logger.info("Using DB at: %s", DB_PATH)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# -----------------------
# Dentist Queries
# -----------------------
def get_all_dentists() -> List[Dentist]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM dentists").fetchall()
        return [Dentist(**dict(row)) for row in rows]


def get_dentist(dentist_id: int) -> Optional[Dentist]:
    with db() as conn:
        row = conn.execute("SELECT * FROM dentists WHERE id=?", (dentist_id,)).fetchone()
        return Dentist(**dict(row)) if row else None


# -----------------------
# Patient Queries
# -----------------------
def create_patient(patient: Patient) -> Patient:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO patients (name, age, gender, phone_number) VALUES (?, ?, ?, ?)",
            (patient.name, patient.age, patient.gender, patient.phone_number),
        )
        patient.id = cur.lastrowid
        logger.info("Patient created with id=%s", patient.id)
        return patient


def get_patient(patient_id: int) -> Optional[Patient]:
    with db() as conn:
        row = conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
        return Patient(**dict(row)) if row else None


def get_patient_by_phone(phone_number: str) -> Optional[Patient]:
    with db() as conn:
        row = conn.execute("SELECT * FROM patients WHERE phone_number=?", (phone_number,)).fetchone()
        return Patient(**dict(row)) if row else None


# -----------------------
# Appointment Queries
# -----------------------
def create_appointment(appt: Appointment) -> Appointment:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO appointments (patient_id, dentist_id, appointment_time, status) VALUES (?, ?, ?, ?)",
            (appt.patient_id, appt.dentist_id, appt.appointment_time.isoformat(), appt.status),
        )
        appt.id = cur.lastrowid
        logger.info("Appointment created with id=%s", appt.id)
        return appt


def update_appointment_status(appt_id: int, status: str) -> bool:
    with db() as conn:
        cur = conn.execute("UPDATE appointments SET status=? WHERE id=?", (status, appt_id))
        if cur.rowcount > 0:
            logger.info("Appointment id=%s updated to status=%s", appt_id, status)
        else:
            logger.warning("No appointment found with id=%s", appt_id)
        return cur.rowcount > 0


def get_patient_appointments(patient_id: int) -> List[AppointmentWithDetails]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT 
                a.id as appointment_id,
                a.appointment_time,
                a.status,
                d.name as dentist_name,
                p.name as patient_name
            FROM appointments a
            JOIN dentists d ON a.dentist_id = d.id
            JOIN patients p ON a.patient_id = p.id
            WHERE a.patient_id = ?
            ORDER BY a.appointment_time
            """,
            (patient_id,),
        ).fetchall()
        return [AppointmentWithDetails(**dict(row)) for row in rows]


# -----------------------
# Conversation Queries
# -----------------------
def create_conversation(patient_id: Optional[int]) -> Conversation:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (patient_id, status, started_at) VALUES (?, 'open', ?)",
            (patient_id, datetime.now().isoformat()),
        )
        conv = Conversation(
            id=cur.lastrowid,
            patient_id=patient_id,
            status="open",
            started_at=datetime.now(),
        )
        logger.debug("Conversation created with id=%s", conv.id)
        return conv

def close_conversation(conversation_id: int, reason: str):
    with db() as conn:
        conn.execute(
            "UPDATE conversations SET status='closed', ended_at=?, closed_reason=? WHERE id=?",
            (datetime.now().isoformat(), reason, conversation_id),
        )
        logger.debug("Conversation %s closed", conversation_id)

def add_message(conversation_id: int, sender: str, message: str) -> Message:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender, message, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, sender, message, datetime.now().isoformat()),
        )
        msg = Message(
            id=cur.lastrowid,
            conversation_id=conversation_id,
            sender=sender,
            message=message,
            created_at=datetime.now(),
        )
        logger.debug("Message added to conversation_id=%s", conversation_id)
        return msg

def get_messages(conversation_id: int) -> List[Message]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at",
            (conversation_id,),
        ).fetchall()
        return [Message(**dict(row)) for row in rows]


def get_conversation(conversation_id: int) -> Optional[Conversation]:
    with db() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        return Conversation(**dict(row)) if row else None


def get_open_conversation(patient_id: int) -> Optional[Conversation]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM conversations
            WHERE patient_id=? AND status='open'
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (patient_id,),
        ).fetchone()
        return Conversation(**dict(row)) if row else None


def get_all_open_conversations() -> List[Conversation]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM conversations WHERE status='open'").fetchall()
        return [Conversation(**dict(row)) for row in rows]

def get_last_message_time(conversation_id: int) -> Optional[datetime]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT created_at FROM messages
            WHERE conversation_id=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        return datetime.fromisoformat(row["created_at"]) if row else None

def get_last_message_for_patient(patient_id: int) -> Optional[Message]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT m.*
            FROM messages m
            JOIN conversations c ON m.conversation_id = c.id
            WHERE c.patient_id=?
            ORDER BY m.created_at DESC
            LIMIT 1
            """,
            (patient_id,),
        ).fetchone()
        return Message(**dict(row)) if row else None


# -----------------------
# DB Initialization
# -----------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dentists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    specialization TEXT NOT NULL,
    languages_spoken TEXT,
    qualifications TEXT,
    years_experience INTEGER,
    availability_schedule TEXT
);

CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    age INTEGER,
    gender TEXT,
    phone_number TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    dentist_id INTEGER NOT NULL,
    appointment_time TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    FOREIGN KEY(patient_id) REFERENCES patients(id),
    FOREIGN KEY(dentist_id) REFERENCES dentists(id)
);

-- Conversation sessions
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    closed_reason TEXT,
    FOREIGN KEY(patient_id) REFERENCES patients(id)
);

-- Individual messages per conversation
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    sender TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);
"""

SEED_DENTISTS = [
    ("Dr. Asha Rao", "Orthodontist", "English, Hindi, Kannada", "BDS, MDS", 12, "Mon-Fri 10:00-17:00"),
    ("Dr. Ramesh Gupta", "Endodontist", "English, Hindi", "BDS, MDS", 15, "Tue-Sat 11:00-18:00"),
    ("Dr. Meera Nair", "Pediatric Dentist", "English, Malayalam", "BDS, MDS", 10, "Mon-Thu 09:00-14:00"),
    ("Dr. Vikram Singh", "Periodontist", "English, Hindi", "BDS, MDS", 8, "Wed-Fri 14:00-20:00"),
    ("Dr. Shalini Desai", "Prosthodontist", "English, Gujarati", "BDS, MDS", 20, "Mon-Sat 10:00-16:00"),
]


def init_db(seed: bool = True):
    """Create tables if missing, seed only if dentists table is empty."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA_SQL)
        if seed:
            existing = conn.execute("SELECT COUNT(*) FROM dentists").fetchone()[0]
            if existing == 0:
                conn.executemany(
                    "INSERT INTO dentists (name, specialization, languages_spoken, qualifications, years_experience, availability_schedule) VALUES (?, ?, ?, ?, ?, ?)",
                    SEED_DENTISTS,
                )
                logger.info("Seeded 5 dentists.")
            else:
                logger.info("Dentists already present, skipping seeding.")


def clean_db():
    if os.path.exists(DB_PATH):
        confirm = input(f"⚠️ Are you sure you want to delete {DB_PATH}? (y/N): ")
        if confirm.lower() == "y":
            os.remove(DB_PATH)
            logger.warning("Database deleted: %s", DB_PATH)
        else:
            logger.info("Cancelled database deletion.")
    else:
        logger.info("No DB found to clean.")


# -----------------------
# CLI
# -----------------------
if __name__ == "__main__":
    from shared.logger_config import setup_logging
    setup_logging()

    import argparse

    parser = argparse.ArgumentParser(description="Dentist App DB Utility")
    parser.add_argument("--init", action="store_true", help="Initialize DB if empty and seed dentists")
    parser.add_argument("--clean", action="store_true", help="Delete the database file with confirmation")
    parser.add_argument("--list-dentists", action="store_true", help="List all dentists")
    parser.add_argument("--list-patients", action="store_true", help="List all patients")
    parser.add_argument("--list-appointments", type=int, help="List appointments for a patient_id")
    parser.add_argument("--add-patient", action="store_true", help="Interactive: add a new patient")
    parser.add_argument("--book-appointment", action="store_true", help="Interactive: book an appointment")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Configure logging after args parsed
    # log_level = logging.DEBUG if args.verbose else logging.INFO
    # logging.basicConfig(
    #     level=log_level,
    #     format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    # )

    if args.init:
        init_db(seed=True)
        logger.info("Database initialized at: %s", DB_PATH)

    if args.clean:
        clean_db()

    if args.list_dentists:
        for d in get_all_dentists():
            logger.info(d)

    if args.list_patients:
        with db() as conn:
            rows = conn.execute("SELECT * FROM patients").fetchall()
            for r in rows:
                logger.info(dict(r))

    if args.list_appointments:
        for appt in get_patient_appointments(args.list_appointments):
            logger.info(appt)

    if args.add_patient:
        name = input("Name: ")
        age = int(input("Age: "))
        gender = input("Gender (Male/Female/Other): ")
        phone = input("Phone: ")
        p = create_patient(Patient(name=name, age=age, gender=gender, phone_number=phone))
        logger.info("Added patient: %s", p)

    if args.book_appointment:
        pid = int(input("Patient ID: "))
        did = int(input("Dentist ID: "))
        appt_time = input("Appointment time (YYYY-MM-DD HH:MM): ")
        appt = create_appointment(
            Appointment(patient_id=pid, dentist_id=did, appointment_time=datetime.fromisoformat(appt_time), status="scheduled")
        )
        logger.info("Appointment booked: %s", appt)
