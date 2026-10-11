# SANO pydantic models - match frontend Medical Records / Meds / Wellness pages
from pydantic import BaseModel
from typing import List, Optional


class ChatIn(BaseModel):
    message: str
    record_id: Optional[str] = None


class MedicationIn(BaseModel):
    name: str
    dose: str = ""
    schedule: str = ""
    status: str = "current"


class AppointmentIn(BaseModel):
    title: str
    date: str
    status: str = "upcoming"


class SignupIn(BaseModel):
    name: str
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str
