# mcp/server.py
import argparse
import json
import logging
import os
import sys
from contextlib import suppress
from datetime import datetime
from typing import Optional, Any, Dict, List

from pydantic import BaseModel, Field, ValidationError

# MCP library (install 'mcp' package)
from mcp.server.fastmcp import FastMCP

# Shared DB layer and Pydantic models you already created
from shared import db as shared_db
from shared.models import Dentist, Patient, Appointment

logger = logging.getLogger(__name__)

# region --- Pydantic Payloads for Tools ---
# While shared.models defines the DB schema, these payloads define the API for the tools.
# They can be slightly different, e.g. accepting whatsapp_number instead of patient_id.

class UpdatePatientPayload(Patient):
    """Payload to update a patient's profile. Uses whatsapp_number for identification."""
    whatsapp_number: str = Field(..., description="The patient's WhatsApp number (including country code).")
    name: Optional[str] = Field(None, description="The patient's full name.")
    age: Optional[int] = Field(None, description="The patient's age.")
    gender: Optional[str] = Field(None, description="The patient's gender.")


class BookAppointmentPayload(BaseModel):
    """Payload for booking a new appointment."""
    patient_whatsapp: str = Field(..., description="The patient's WhatsApp number.")
    dentist_id: int = Field(..., description="The ID of the dentist for the appointment.")
    appointment_time: str = Field(..., description="The desired appointment time in ISO 8601 format (e.g., '2025-08-31T14:30:00').")
    patient_name: Optional[str] = Field(None, description="The patient's full name (required if the patient is new).")
    patient_age: Optional[int] = Field(None, description="The patient's age (optional, for new patients).")
    patient_gender: Optional[str] = Field(None, description="The patient's gender (optional, for new patients).")


class CancelAppointmentPayload(BaseModel):
    """Payload for cancelling an appointment. Can use either appointment_id or a combination of other details."""
    appointment_id: Optional[int] = Field(None, description="The unique ID of the appointment to cancel.")
    patient_whatsapp: Optional[str] = Field(None, description="The patient's WhatsApp number (used if appointment_id is unknown).")
    dentist_id: Optional[int] = Field(None, description="The dentist's ID (used if appointment_id is unknown).")
    appointment_time: Optional[str] = Field(None, description="The appointment time in ISO 8601 format (used if appointment_id is unknown).")


class ReschedulePayload(BaseModel):
    """Payload for rescheduling an existing appointment."""
    appointment_id: int = Field(..., description="The unique ID of the appointment to reschedule.")
    new_appointment_time: str = Field(..., description="The new desired appointment time in ISO 8601 format.")


class CloseConversationPayload(BaseModel):
    """Payload for closing a conversation."""
    conversation_id: int = Field(..., description="The ID of the conversation to close.")
    reason: str = Field("user_confirmed", description="The reason for closing the conversation.")

# endregion


# -------------------------
# MCP instance
# -------------------------
mcp = FastMCP("dentist-mcp")


# -------------------------
# Prompts
# -------------------------
BASE_SYSTEM_PROMPT = ("You are a helpful dental assistant. Your name is 'Sia'. You can help patients book, reschedule, or cancel appointments with dentists. "
                    "You have access to the following tools. "
                    "IMPORTANT: If the patient's name in the current state is 'New Patient', "
                    "it means they are a new user. Your first and most important task is to greet them warmly, "
                    "introduce yourself, and ask for their full name, age, and gender to complete their registration. "
                    "Once you have this information, you MUST use the `update_patient_profile` tool to save their details. "
                    "Do not proceed with any other request until the patient is fully registered. "
                    "After you have successfully fulfilled a user's request (like booking an appointment or answering a question), "
                    "you must always confirm with the user if there is anything else they need help with. "
                    "For example, ask 'Is there anything else I can help you with today?'. \n"
                    "If the user indicates they are done (e.g., 'no, thanks', 'thats all', 'I am good'), "
                    "you MUST use the `close_conversation` tool to end the chat. When calling this tool, use the `conversation_id` "
                    "from the state and set the reason to 'user_confirmed'. "
                    "VERY IMPORTANT: Before booking, cancelling, or rescheduling any appointment, you MUST call the `get_current_time` "
                    "tool to know the current date and time. All appointments must be scheduled for a future time relative to the current time. "
                    "Do not book, cancel or reschedule appointments in the past.")

@mcp.prompt()
def system_prompt() -> str:
    """Returns the base system prompt for the dental assistant agent."""
    return BASE_SYSTEM_PROMPT



# -------------------------
# Utility helpers
# -------------------------
def _ensure_patient(whatsapp: str, name: Optional[str] = None, age: Optional[int] = None, gender: Optional[str] = None) -> Patient:
    """Finds a patient by WhatsApp number. If not found, creates a new patient record."""
    patient = shared_db.get_patient_by_phone(whatsapp)
    if patient:
        return patient
    
    if not name:
        raise ValueError("Patient name is required for new patient registration.")

    new_patient_data = Patient(phone_number=whatsapp, name=name, age=age, gender=gender)
    return shared_db.create_patient(new_patient_data)


# -------------------------
# MCP Tools
# -------------------------

@mcp.tool()
def get_current_time() -> str:
    """
    Returns the current date and time in ISO 8601 format.
    This must be called before any time-sensitive operations like booking, rescheduling or cancelling
    to ensure the agent has accurate knowledge of the present moment.
    """
    now = datetime.now().isoformat()
    logger.debug("Tool: get_current_time, returning: %s", now)
    return now

@mcp.tool()
def list_dentists(specialization: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves a list of all available dentists. 
    You can optionally filter the list by specialization (e.g., 'Orthodontist', 'Endodontist').
    """
    logger.debug("Tool: list_dentists, specialization=%s", specialization)
    dentists = shared_db.get_all_dentists()
    if specialization:
        filtered = [d for d in dentists if specialization.lower() in d.specialization.lower()]
    else:
        filtered = dentists
    return [d.model_dump() for d in filtered]


@mcp.tool()
def get_dentist_profile(dentist_id: Optional[int] = None, name: Optional[str] = None) -> Dict[str, Any]:
    """
    Gets the detailed profile of a specific dentist, either by their unique ID or by their name.
    Providing a name will return the first dentist that matches.
    """
    logger.debug("Tool: get_dentist_profile, id=%s, name=%s", dentist_id, name)
    if dentist_id:
        dentist = shared_db.get_dentist(dentist_id)
        return dentist.model_dump() if dentist else {"error": "dentist_not_found"}
    if name:
        with shared_db.db() as conn:
            row = conn.execute("SELECT * FROM dentists WHERE name LIKE ? LIMIT 1", (f"%{name}%",)).fetchone()
            if row:
                return dict(row)
    return {"error": "A dentist_id or name must be provided."}


@mcp.tool()
def get_availability(dentist_id: int) -> Dict[str, Any]:
    """
    Fetches the weekly availability schedule for a specific dentist, identified by their ID.
    """
    logger.debug("Tool: get_availability, dentist_id=%s", dentist_id)
    dentist = shared_db.get_dentist(dentist_id)
    if not dentist:
        return {"error": "dentist_not_found"}
    return {"availability_schedule": dentist.availability_schedule}


@mcp.tool()
def update_patient_profile(payload: UpdatePatientPayload) -> Dict[str, Any]:
    """
    Updates a patient's profile details (name, age, gender) using their WhatsApp number.
    This should be used to register the full details of a newly identified patient.
    """
    logger.debug("Tool: update_patient_profile, payload=%s", payload)
    with shared_db.db() as conn:
        patient_row = conn.execute("SELECT * FROM patients WHERE phone_number = ?", (payload.whatsapp_number,)).fetchone()
        if not patient_row:
            return {"error": "patient_not_found", "details": f"No patient with WhatsApp number {payload.whatsapp_number}"}
        
        patient_id = patient_row["id"]
        updates = []
        params = []
        if payload.name is not None:
            updates.append("name = ?")
            params.append(payload.name)
        if payload.age is not None:
            updates.append("age = ?")
            params.append(payload.age)
        if payload.gender is not None:
            updates.append("gender = ?")
            params.append(payload.gender)

        if not updates:
            return {"error": "no_update_fields_provided", "details": "You must provide at least one field to update."}

        params.append(patient_id)
        query = f"UPDATE patients SET {', '.join(updates)} WHERE id = ?"
        conn.execute(query, tuple(params))

    logger.info(f"Updated patient profile for patient id {patient_id}")
    return {"status": "success", "patient_id": patient_id}


@mcp.tool()
def upcoming_appointments(patient_whatsapp: str) -> List[Dict[str, Any]]:
    """
    Returns a list of all upcoming scheduled appointments for a patient, identified by their WhatsApp number.
    """
    logger.debug("Tool: upcoming_appointments, patient_whatsapp=%s", patient_whatsapp)
    patient = shared_db.get_patient_by_phone(patient_whatsapp)
    if not patient:
        return []  # Return an empty list if the patient is not found
    
    appointments = shared_db.get_patient_appointments(patient.id)
    return [appt.model_dump() for appt in appointments if appt.status == 'scheduled']


@mcp.tool()
def book_appointment(payload: BookAppointmentPayload) -> Dict[str, Any]:
    """
    Books a new appointment for a patient with a specific dentist at a given time.
    If the patient does not exist, their name must be provided to create a new patient record.
    """
    logger.debug("Tool: book_appointment, payload=%s", payload)
    try:
        dentist = shared_db.get_dentist(payload.dentist_id)
        if not dentist:
            return {"error": "dentist_not_found"}

        patient = _ensure_patient(
            whatsapp=payload.patient_whatsapp,
            name=payload.patient_name,
            age=payload.patient_age,
            gender=payload.patient_gender
        )

        with shared_db.db() as conn:
            clash = conn.execute(
                "SELECT id FROM appointments WHERE dentist_id = ? AND appointment_time = ? AND status = 'scheduled'",
                (payload.dentist_id, payload.appointment_time),
            ).fetchone()
            if clash:
                return {"error": "slot_unavailable", "details": "The requested time slot is already booked."}

            new_appointment = Appointment(
                patient_id=patient.id,
                dentist_id=payload.dentist_id,
                appointment_time=datetime.fromisoformat(payload.appointment_time),
                status='scheduled'
            )
            created_appt = shared_db.create_appointment(new_appointment)

        logger.info("Booked appointment id=%s for patient=%s", created_appt.id, patient.id)
        return created_appt.model_dump()

    except ValueError as ve:
        return {"error": "validation_error", "details": str(ve)}
    except Exception as e:
        logger.error("Error in book_appointment: %s", e, exc_info=True)
        return {"error": "internal_server_error", "details": str(e)}


@mcp.tool()
def cancel_appointment(payload: CancelAppointmentPayload) -> Dict[str, Any]:
    """
    Cancels an existing appointment.
    This can be done by providing the unique appointment_id, or by providing the patient's WhatsApp number, the dentist's ID, and the appointment time.
    """
    logger.debug("Tool: cancel_appointment, payload=%s", payload)
    if payload.appointment_id:
        updated = shared_db.update_appointment_status(payload.appointment_id, 'cancelled')
        if updated:
            logger.info("Canceled appointment id=%s", payload.appointment_id)
            return {"status": "cancelled", "appointment_id": payload.appointment_id}
        return {"error": "not_found", "details": "Appointment ID not found or already cancelled."}

    if payload.patient_whatsapp and payload.dentist_id and payload.appointment_time:
        patient = shared_db.get_patient_by_phone(payload.patient_whatsapp)
        if not patient:
            return {"error": "patient_not_found"}
        
        with shared_db.db() as conn:
            # Find the specific appointment to cancel
            appt_to_cancel = conn.execute(
                "SELECT id FROM appointments WHERE patient_id=? AND dentist_id=? AND appointment_time=? AND status='scheduled'",
                (patient.id, payload.dentist_id, payload.appointment_time)
            ).fetchone()

            if not appt_to_cancel:
                return {"error": "not_found", "details": "No matching scheduled appointment found for the given details."}
            
            updated = shared_db.update_appointment_status(appt_to_cancel['id'], 'cancelled')
            if updated:
                logger.info("Cancelled appointment id=%s", appt_to_cancel['id'])
                return {"status": "cancelled", "appointment_id": appt_to_cancel['id']}

    return {"error": "invalid_payload", "details": "You must provide either an appointment_id or the trio of patient_whatsapp, dentist_id, and appointment_time."}


@mcp.tool()
def reschedule_appointment(payload: ReschedulePayload) -> Dict[str, Any]:
    """
    Reschedules an existing appointment to a new time. Requires the unique appointment_id.
    """
    logger.debug("Tool: reschedule_appointment, payload=%s", payload)
    with shared_db.db() as conn:
        appt_row = conn.execute("SELECT * FROM appointments WHERE id = ?", (payload.appointment_id,)).fetchone()
        if not appt_row or appt_row["status"] != "scheduled":
            return {"error": "appointment_not_found_or_not_scheduled"}

        clash = conn.execute(
            "SELECT id FROM appointments WHERE dentist_id = ? AND appointment_time = ? AND status = 'scheduled' AND id <> ?",
            (appt_row["dentist_id"], payload.new_appointment_time, payload.appointment_id),
        ).fetchone()
        if clash:
            return {"error": "new_slot_unavailable"}

        conn.execute(
            "UPDATE appointments SET appointment_time = ?, status = 'rescheduled' WHERE id = ?",
            (payload.new_appointment_time, payload.appointment_id),
        )
    logger.info("Rescheduled appointment id=%s to %s", payload.appointment_id, payload.new_appointment_time)
    return {"status": "rescheduled", "appointment_id": payload.appointment_id}


@mcp.tool()
def close_conversation(payload: CloseConversationPayload) -> Dict[str, Any]:
    """
    Closes the current conversation when the user has confirmed they have no more requests.
    Use this tool when the user says "no", "that's all", "I'm done", etc.
    """
    logger.debug("Tool: close_conversation, payload=%s", payload)
    try:
        shared_db.close_conversation(payload.conversation_id, payload.reason)
        logger.info(f"Conversation {payload.conversation_id} closed by agent with reason: {payload.reason}")
        return {"status": "success", "conversation_id": payload.conversation_id}
    except Exception as e:
        logger.error(f"Failed to close conversation {payload.conversation_id}: {e}", exc_info=True)
        return {"error": "db_error", "details": str(e)}


# -------------------------
# Bootstrap and run
# -------------------------
def setup_mcp_logging(level=logging.INFO):
    """
    Configures logging specifically for the MCP server process.
    """
    log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, "mcp_server.log")

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    file_handler = logging.FileHandler(log_file_path, mode='a')
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)


def main():
    parser = argparse.ArgumentParser(prog="mcp.server", description="MCP Server for Dentist App (stdio transport)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    setup_mcp_logging(log_level)
    
    logger.info("MCP server starting (stdio transport). Verbose=%s", args.verbose)

    shared_db.init_db(seed=True)

    try:
        mcp.run(transport="stdio")
    except Exception as e:
        logger.exception("MCP server exited with error: %s", e)


if __name__ == "__main__":
    main()


"""
Running
# normal logging
uv run python -m mcp.server

# verbose (debug logs)
uv run python -m mcp.server --verbose
"""
