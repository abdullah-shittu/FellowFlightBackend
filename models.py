# models.py

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, time, datetime
import uuid


# --- User Models ---
class UserBase(BaseModel):
    name: str
    linkedin_url: Optional[str] = None


class UserUpdate(BaseModel):
    linkedin_url: str


class UserInDB(UserBase):
    id: uuid.UUID
    slack_id: str
    created_at: datetime


class UserResponse(BaseModel):
    id: str
    slack_id: str
    name: str
    linkedin_url: Optional[str] = None
    created_at: datetime


# --- Flight Models ---
class FlightCreate(BaseModel):
    flight_number: str = Field(..., max_length=10)
    date: date
    dep_airport: str = Field(..., max_length=5)
    departure_time: time
    hours_early: float = Field(..., gt=0, le=12)


class FlightResponse(FlightCreate):
    id: int
    user_id: uuid.UUID


# --- Auth Models ---
class Token(BaseModel):
    access_token: str
    token_type: str


# --- Match Models ---
class MatchProfile(BaseModel):
    name: str
    linkedin_url: Optional[str]
    slack_id: str


class MatchResponse(BaseModel):
    same_flight: List[MatchProfile]
    time_overlap: List[MatchProfile]


class FormDataModel(BaseModel):
    airport: str
    flightNumber: str
    linkedInTag: str
    hoursEarly: float = Field(..., gt=0, le=12)
    dateTimeFlight: str  # you could also use datetime or time
    _end: Optional[str]
    _rid: Optional[str]
    _sheetName: Optional[str]
    _submitted: Optional[str]
