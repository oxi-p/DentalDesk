from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# -----------------------
# Dentist Models
# -----------------------
class Dentist(BaseModel):
    id: Optional[int] = Field(default=None, description="Primary key in DB")
    name: str
    specialization: str
    languages_spoken: str
    qualifications: Optional[str] = None
    years_experience: Optional[int] = None
    availability_schedule: Optional[str] = None


# -----------------------
# Patient Models
# -----------------------
class Patient(BaseModel):
    id: Optional[int] = Field(default=None, description="Primary key in DB")
    name: str
    age: Optional[int] = None
    gender: Optional[str] = Field(default=None, description="Male/Female/Other")
    phone_number: str


# -----------------------
# Appointment Models
# -----------------------
class Appointment(BaseModel):
    id: Optional[int] = Field(default=None, description="Primary key in DB")
    patient_id: int
    dentist_id: int
    appointment_time: datetime
    status: str = Field(default="scheduled", description="scheduled/cancelled/completed/rescheduled")


# -----------------------
# Conversation Models
# -----------------------
class Conversation(BaseModel):
    id: Optional[int] = Field(default=None, description="Primary key in DB")
    patient_id: Optional[int] = None
    status: str = Field(default="open", description="open/closed")
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    closed_reason: Optional[str] = None


# -----------------------
# Single Message Models
# -----------------------
class Message(BaseModel):
    id: int
    conversation_id: int
    sender: str               # "user" , "agent" , "tool" or "agent_tool_call"
    message: str
    created_at: datetime

# -----------------------
# Composite / API models
# -----------------------

class AppointmentWithDetails(BaseModel):
    """Convenience model to return appointments joined with doctor/patient info"""
    appointment_id: int
    appointment_time: datetime
    status: str
    dentist_name: str
    patient_name: str


class DentistListResponse(BaseModel):
    dentists: List[Dentist]


class AppointmentListResponse(BaseModel):
    appointments: List[AppointmentWithDetails]
