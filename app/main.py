"""Employee Attendance & Analytics API (FastAPI + MongoDB)"""

import os
import math
import decimal
from datetime import datetime, timedelta, timezone, time, date
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator
from pymongo import MongoClient
import pymongo.errors

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str, attendance_date: str) -> int:
    """Compute late minutes in IST, grace period is 10 minutes."""
    # attendance_date is string YYYY-MM-DD
    year, month, day = map(int, attendance_date.split("-"))
    h, m = map(int, shift_start.split(":"))
    # Create shift start datetime in IST on the attendance_date
    start_ist = datetime(year, month, day, hour=h, minute=m, second=0, microsecond=0, tzinfo=IST)
    # Convert punch_in to IST
    punch_in_ist = utc_dt_to_ist_dt(punch_in)
    # Calculate elapsed minutes from shift start to punch_in
    elapsed_minutes = (punch_in_ist - start_ist).total_seconds() / 60
    # Return floor of elapsed minutes only if strictly greater than 10
    return int(elapsed_minutes) if elapsed_minutes > 10 else 0

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """Work hours in hours, rounded half-up to 2 decimals."""
    hours = (punch_out - punch_in).total_seconds() / 3600
    return round_half_up(hours, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str, shift_start: str) -> int:
    """Overtime minutes (floor) after shift_end, counted only if >=30."""
    # punch_out is timezone-aware UTC datetime (truncated to seconds)
    punch_out_ist = utc_dt_to_ist_dt(punch_out)
    h, m = map(int, shift_end.split(":"))
    shift_end_time = time(hour=h, minute=m)
    # date_str is attendance date (IST) of shift start
    dt_date = date.fromisoformat(date_str)
    # Determine if shift is overnight: shift_end <= shift_start (time comparison)
    shift_start_h, shift_start_m = map(int, shift_start.split(":"))
    shift_start_time = time(hour=shift_start_h, minute=shift_start_m)
    overnight = shift_end_time <= shift_start_time
    # Compute shift_end datetime in IST
    if overnight:
        dt_date = dt_date + timedelta(days=1)
    end_ist = datetime.combine(dt_date, shift_end_time, tzinfo=IST)
    # Compute difference in minutes
    diff_minutes = (punch_out_ist - end_ist).total_seconds() / 60
    minutes = int(diff_minutes)  # floor
    return minutes if minutes >= 30 else 0


def truncate_to_seconds(epoch_millis: int) -> int:
    """Truncate epoch milliseconds to whole seconds."""
    return (epoch_millis // 1000) * 1000


def epoch_millis_to_utc_dt(epoch_millis: int) -> datetime:
    """Convert epoch milliseconds to timezone-aware UTC datetime, truncated to seconds."""
    ts = truncate_to_seconds(epoch_millis)
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)


def utc_dt_to_epoch_millis(dt: datetime) -> int:
    """Convert timezone-aware UTC datetime to epoch milliseconds."""
    if dt.tzinfo is None:
        # Assume UTC if naive
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp() * 1000)


def utc_dt_to_ist_dt(dt: datetime) -> datetime:
    """Convert timezone-aware UTC datetime to IST."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def ist_dt_to_date_str(dt: datetime) -> str:
    """Convert IST datetime to YYYY-MM-DD string."""
    return dt.date().isoformat()


def round_half_up(value: float, decimals: int) -> float:
    """Round half-up to given decimal places using decimal for exactness."""
    # Convert to Decimal for precise rounding
    decimal_value = decimal.Decimal(str(value))
    # Create the rounding context with ROUND_HALF_UP
    rounded = decimal_value.quantize(
        decimal.Decimal('0.' + '0' * decimals),
        rounding=decimal.ROUND_HALF_UP
    )
    return float(rounded)
def attendance_date_for(punch_in_utc_dt: datetime, shift_start: str, shift_end: str) -> str:
    """Return the attendance date (YYYY-MM-DD) for a punch-in according to R1."""
    punch_in_ist = utc_dt_to_ist_dt(punch_in_utc_dt)
    h_start, m_start = map(int, shift_start.split(":"))
    h_end, m_end = map(int, shift_end.split(":"))
    shift_start_time = time(hour=h_start, minute=m_start)
    shift_end_time = time(hour=h_end, minute=m_end)
    overnight = shift_end_time <= shift_start_time
    if overnight and punch_in_ist.time() < shift_end_time:
        # Belongs to previous day
        attendance_date = punch_in_ist.date() - timedelta(days=1)
    else:
        attendance_date = punch_in_ist.date()
    return attendance_date.isoformat()

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r'^EMP\d{4,6}$')
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field(default="18:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str

    @model_validator(mode='after')
    def shift_start_must_differ_from_shift_end(self):
        if self.shift_start == self.shift_end:
            raise ValueError('shift_start must differ from shift_end')
        return self

    @model_validator(mode='after')
    def joined_on_must_be_valid_date(self):
        try:
            datetime.strptime(self.joined_on, '%Y-%m-%d')
        except ValueError:
            raise ValueError('joined_on must be a valid date in YYYY-MM-DD format')
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "emp_code": "EMP0001",
                    "name": "Asha Rao",
                    "email": "asha@example.com",
                    "department": "Engineering",
                    "shift_start": "09:30",
                    "shift_end": "18:30",
                    "joined_on": "2026-01-05"
                }
            ]
        }
    }

@app.get("/analytics/departments/summary")
def get_department_summary(month: str, department: Optional[str] = None):
    # Validate month format strictly as YYYY-MM
    parts = month.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        raise HTTPException(422, "Invalid month format. Expected YYYY-MM")
    try:
        year, month_num = map(int, parts)
        if month_num < 1 or month_num > 12:
            raise ValueError
        # Validate it's a real calendar month
        first_day = datetime(year, month_num, 1)
        if month_num == 12:
            last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = datetime(year, month_num + 1, 1) - timedelta(days=1)
    except ValueError:
        raise HTTPException(422, "Invalid month format. Expected YYYY-MM")

    # Compute month boundaries as strings for matching (YYYY-MM-DD)
    month_start_str = first_day.strftime("%Y-%m-%d")
    month_end_str = last_day.strftime("%Y-%m-%d")

    # Build the aggregation pipeline
    pipeline = []

    # Stage 1: Match employees by department (if provided) and joined_on <= month_end
    match_stage = {}
    if department is not None:
        match_stage["department"] = department
    match_stage["joined_on"] = {"$lte": month_end_str}
    if match_stage:
        pipeline.append({"$match": match_stage})

    # Stage 2: Lookup attendance logs for the month
    pipeline.append({
        "$lookup": {
            "from": "attendance_logs",
            "let": {
                "emp_code": "$emp_code",
                "month_start": month_start_str,
                "month_end": month_end_str
            },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$emp_code", "$$emp_code"]},
                                {"$gte": ["$date", "$$month_start"]},
                                {"$lte": ["$date", "$$month_end"]}
                            ]
                        }
                    }
                }
            ],
            "as": "logs"
        }
    })

    # Stage 3: Unwind logs to process each attendance record (preserve null and empty arrays)
    pipeline.append({
        "$unwind": {
            "path": "$logs",
            "preserveNullAndEmptyArrays": True
        }
    })

    # Stage 4: Add fields for per-log metrics (handle null logs)
    pipeline.append({
        "$addFields": {
            "status": "$logs.status",
            "work_hours": "$logs.work_hours",
            "late_minutes": "$logs.late_minutes",
            "half_day": "$logs.half_day",
            "date": "$logs.date"
        }
    })

    # Stage 5: Compute per-log contributions
    # Stage 5: Compute base flags
    pipeline.append({
        "$addFields": {
            "is_present": {
                "$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]
            },
            "is_weekday": {
                "$cond": [
                    {"$ne": ["$date", None]},
                    {
                        "$in": [
                            {
                                "$dayOfWeek": {
                                    "$dateFromString": {
                                        "dateString": "$date",
                                        "timezone": "Etc/UTC"
                                    }
                                }
                            },
                            [2, 3, 4, 5, 6]
                        ]
                    },
                    False
                ]
            },
            "is_late": {"$gt": ["$late_minutes", 0]},
            "is_leave": {"$eq": ["$status", "LEAVE"]},
            "is_on_duty": {"$eq": ["$status", "ON_DUTY"]}
        }
    })

    # Stage 5b: Compute contributions from the flags above
    pipeline.append({
        "$addFields": {
            "present_value": {
                "$cond": [
                    {"$and": ["$is_present", "$is_weekday"]},
                    {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                    0
                ]
            },
            "eligible_work_hours": {
                "$cond": [
                    {"$and": [
                        "$is_present",
                        {"$ne": ["$work_hours", None]}
                    ]},
                    "$work_hours",
                    None
                ]
            }
        }
    })


    # Stage 6: Group by employee and department to get per-employee aggregates
    pipeline.append({
        "$group": {
            "_id": {
                "department": "$department",
                "emp_code": "$emp_code"
            },
            # Headcount per employee: 1
            "employee_count": {"$sum": 1},
            # Present days: sum of present_value across logs
            "present_days": {"$sum": "$present_value"},
            # For work_hours average: sum and count of eligible_work_hours (non-null)
            "total_work_hours": {
                "$sum": {
                    "$cond": [
                        {"$ne": ["$eligible_work_hours", None]},
                        "$eligible_work_hours",
                        0
                    ]
                }
            },
            "work_hours_count": {
                "$sum": {
                    "$cond": [
                        {"$ne": ["$eligible_work_hours", None]},
                        1,
                        0
                    ]
                }
            },
            # Late count and total late minutes
            "late_count": {"$sum": {"$cond": ["$is_late", 1, 0]}},
            "total_late_minutes": {"$sum": "$late_minutes"},
            # Leave count
            "leave_count": {"$sum": {"$cond": ["$is_leave", 1, 0]}},
            # On duty count
            "on_duty_count": {"$sum": {"$cond": ["$is_on_duty", 1, 0]}}
        }
    })


    # Stage 7: Group by department to get department-level aggregates
    pipeline.append({
        "$group": {
            "_id": "$_id.department",
            # Headcount: sum of employee_count (each employee counted once)
            "headcount": {"$sum": "$employee_count"},
            # Present days: sum of present_days
            "present_days": {"$sum": "$present_days"},
            # Total work_hours and count for avg_work_hours calculation
            "total_work_hours": {"$sum": "$total_work_hours"},
            "work_hours_count": {"$sum": "$work_hours_count"},
            # Late metrics
            "late_count": {"$sum": "$late_count"},
            "total_late_minutes": {"$sum": "$total_late_minutes"},
            # Leave and on duty counts
            "leave_count": {"$sum": "$leave_count"},
            "on_duty_count": {"$sum": "$on_duty_count"}
        }
    })

    # Stage 8: Match to exclude departments with zero headcount
    pipeline.append({"$match": {"headcount": {"$gt": 0}}})

    # Stage 9: Sort by department ascending
    pipeline.append({"$sort": {"_id": 1}})

    # Stage 10: Project to final format (we'll compute avg_work_hours in Python)
    pipeline.append({
        "$project": {
            "_id": 0,
            "department": "$_id",
            "headcount": 1,
            "present_days": 1,
            "total_work_hours": 1,
            "work_hours_count": 1,
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1
        }
    })

    # Execute the aggregation
    #result = list(db.employees.aggregate(pipeline))
    result = list(db.employees.aggregate(pipeline))

    # Build the final response with computed avg_work_hours (application-level rounding)
    items = []
    for dept in result:
        # Compute avg_work_hours
        if dept["work_hours_count"] > 0:
            avg_work_hours = (dept["total_work_hours"] / dept["work_hours_count"])
            avg_work_hours = round_half_up(avg_work_hours, 2)
        else:
            avg_work_hours = None
        items.append({
            "department": dept["department"],
            "headcount": dept["headcount"],
            "present_days": dept["present_days"],
            "avg_work_hours": avg_work_hours,
            "late_count": dept["late_count"],
            "total_late_minutes": dept["total_late_minutes"],
            "leave_count": dept["leave_count"],
            "on_duty_count": dept["on_duty_count"]
        })
    return {"month": month, "items": items}



class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

    @field_validator('status')
    def status_must_be_valid(cls, v):
        allowed_statuses = ["PRESENT", "WFH", "ON_DUTY"]
        if v not in allowed_statuses:
            raise ValueError(f'status must be one of {allowed_statuses}')
        return v

    @field_validator('punched_at', mode='before')
    def punched_at_must_be_valid_epoch_millis(cls, v):
        if v is None:
            return v
        if type(v) is not int:
            raise ValueError('punched_at must be an integer')
        if v < 100000000000 or v > 4102444800000:
            raise ValueError('punched_at must be a valid epoch millisecond value (100000000000-4102444800000)')
        return v


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator('punched_at', mode='before')
    def punched_at_must_be_valid_epoch_millis(cls, v):
        if v is None:
            return v
        if type(v) is not int:
            raise ValueError('punched_at must be an integer')
        if v < 100000000000 or v > 4102444800000:
            raise ValueError('punched_at must be a valid epoch millisecond value (100000000000-4102444800000)')
        return v


class RegularizeRequest(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator('status')
    def status_must_be_valid(cls, v):
        if v is not None:
            allowed_statuses = ["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
            if v not in allowed_statuses:
                raise ValueError(f'status must be one of {allowed_statuses}')
        return v

    @field_validator('punch_in', 'punch_out', mode='before')
    def punch_time_must_be_valid_epoch_millis(cls, v):
        if v is None:
            return v
        if type(v) is not int:
            raise ValueError('punch time must be an integer')
        if v < 100000000000 or v > 4102444800000:
            raise ValueError('punch time must be a valid epoch millisecond value (100000000000-4102444800000)')
        return v
# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    # Store created_at as timezone-aware UTC datetime truncated to whole seconds
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)
    except pymongo.errors.DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    # Convert created_at to epoch milliseconds for API response
    doc["created_at"] = utc_dt_to_epoch_millis(doc["created_at"])
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    # Validate pagination parameters
    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(422, "page_size must be between 1 and 100")

    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = []
    for emp in db.employees.find(q, {"_id": 0}).sort("emp_code").skip(skip).limit(page_size):
        if emp.get("created_at") is not None:
            emp["created_at"] = utc_dt_to_epoch_millis(emp["created_at"])
        items.append(emp)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
    # Convert punched_at to timezone-aware UTC datetime truncated to seconds
    if body.punched_at is not None:
        ts = epoch_millis_to_utc_dt(body.punched_at)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    # Derive attendance date according to R1
    d = attendance_date_for(ts, emp["shift_start"], emp["shift_end"])
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], d),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except pymongo.errors.DuplicateKeyError:
        raise HTTPException(409, "Attendance record already exists for this employee and date")
    doc.pop("_id", None)
    # Convert datetime fields to epoch milliseconds for response
    if doc["punch_in"] is not None:
        doc["punch_in"] = utc_dt_to_epoch_millis(doc["punch_in"])
    if doc["punch_out"] is not None:
        doc["punch_out"] = utc_dt_to_epoch_millis(doc["punch_out"])
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    # Validate pagination parameters
    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(422, "page_size must be between 1 and 100")
    # Validate date range
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be before or equal to date_to")
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    page_docs = list(db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size))
    for d in page_docs:
        d.pop("_id", None)
        # Convert datetime fields to epoch milliseconds for API response
        if d.get("punch_in") is not None:
            d["punch_in"] = utc_dt_to_epoch_millis(d["punch_in"])
        if d.get("punch_out") is not None:
            d["punch_out"] = utc_dt_to_epoch_millis(d["punch_out"])
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    # Convert punched_at to timezone-aware UTC datetime truncated to seconds
    if body.punched_at is not None:
        ts = epoch_millis_to_utc_dt(body.punched_at)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)

    # Find the most recent attendance record for this employee whose punch_in is at or before ts
    # and whose punch_out is still null or absent (open record)
    # Per contract: "Closes the employee's most recent record whose punch_in is at or before punched_at"
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
            "$or": [
                {"punch_out": None},
                {"punch_out": {"$exists": False}}
            ]
        },
        sort=[("punch_in", -1)]  # Most recent punch_in first
    )

    if not record:
        raise HTTPException(404, "No punch-in found for this employee")

    # Check if the record already has a punch_out (race condition check)
    if record.get("punch_out") is not None:
        raise HTTPException(409, "Record already punched out")

    # Calculate derived values using the record's own punch_in and date
    punch_in_dt = record["punch_in"]
    # Ensure it's a datetime object (PyMongo returns timezone-aware UTC datetime for BSON date)
    if isinstance(punch_in_dt, str):
        punch_in_dt = datetime.fromisoformat(punch_in_dt.replace('Z', '+00:00'))
    # If it's still naive, assume UTC (shouldn't happen with our storage, but safe)
    if punch_in_dt.tzinfo is None:
        punch_in_dt = punch_in_dt.replace(tzinfo=timezone.utc)
    if ts <= punch_in_dt:
        raise HTTPException(422, "punched_at must be after punch_in")
    if (ts - punch_in_dt).total_seconds() > 86400:
        raise HTTPException(422, "punched_at must be within 24 hours of punch_in")

    # Use the record's own date for overtime calculation (IST date when shift started)
    date_str = record["date"]

    work_hours = compute_work_hours(punch_in_dt, ts)
    overtime_minutes = compute_overtime(ts, emp["shift_end"], date_str, emp["shift_start"])
    half_day = work_hours < 4.50

    # Prepare the update
    update_data = {
        "punch_out": ts,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day
    }

    # Perform the update conditionally - only update if record still has no punch_out (null or absent)
    # This handles the race condition where two punch-out requests happen simultaneously
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "$or": [
                {"punch_out": None},
                {"punch_out": {"$exists": False}}
            ]
        },
        {"$set": update_data}
    )

    if result.matched_count == 0:
        # No record matched our filter, meaning either:
        # 1. No record with that _id exists (shouldn't happen)
        # 2. The record exists but punch_out is not None anymore (lost the race)
        raise HTTPException(409, "Record already punched out")
    if result.modified_count == 0:
        # This is unusual but could happen if the record already had these values
        # Treat as success since the record is in the desired state
        pass

    # Fetch the updated record to return
    updated_record = db.attendance_logs.find_one({"_id": record["_id"]})

    # Remove MongoDB's internal identifier; it is not part of AttendanceRecord
    updated_record.pop("_id", None)

    # Convert datetime fields to epoch milliseconds for response
    if updated_record.get("punch_in") is not None:
        updated_record["punch_in"] = utc_dt_to_epoch_millis(updated_record["punch_in"])
    if updated_record.get("punch_out") is not None:
        updated_record["punch_out"] = utc_dt_to_epoch_millis(updated_record["punch_out"])

    return updated_record


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    # Validate employee exists
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    # Validate date format (YYYY-MM-DD)
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "Invalid date format. Expected YYYY-MM-DD")

    # Find the attendance record
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "Attendance record not found for this employee and date")

    # Extract current values for comparison
    current_status = record.get("status")
    current_punch_in = record.get("punch_in")
    current_punch_out = record.get("punch_out")

    # Determine effective new values after applying business rules
    eff_status = body.status if body.status is not None else current_status
    eff_punch_in = body.punch_in if body.punch_in is not None else current_punch_in
    eff_punch_out = body.punch_out if body.punch_out is not None else current_punch_out

    # Rule: ABSENT/LEAVE clear both punch times
    if eff_status in ["ABSENT", "LEAVE"]:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "Cannot specify punch_in or punch_out when status is ABSENT or LEAVE")
        # Clear existing punch times when setting to ABSENT/LEAVE
        eff_punch_in = None
        eff_punch_out = None
    # Rule: Presence status requires a punch_in
    elif eff_status in ["PRESENT", "WFH", "ON_DUTY"]:
        if eff_punch_in is None:
            raise HTTPException(422, f"Status {eff_status} requires a punch_in")

    # Rule: punch_in must stay on the record's attendance date (R1)
    if eff_punch_in is not None:
        # Convert punch_in to datetime for validation
        if isinstance(eff_punch_in, str):
            punch_in_dt = datetime.fromisoformat(eff_punch_in.replace('Z', '+00:00'))
        elif isinstance(eff_punch_in, int):
            punch_in_dt = epoch_millis_to_utc_dt(eff_punch_in)
        else:
            punch_in_dt = eff_punch_in
        # Ensure it's timezone-aware UTC
        if punch_in_dt.tzinfo is None:
            punch_in_dt = punch_in_dt.replace(tzinfo=timezone.utc)

        # Compute expected attendance date using helper (R1)
        expected_date = attendance_date_for(punch_in_dt, emp["shift_start"], emp["shift_end"])

        # Must match the record's date
        if expected_date != date:
            raise HTTPException(422, "punch_in must be on the same date as the attendance record's date (R1)")

    # Rule: punch_out must be after punch_in and within 24 hours
    if eff_punch_out is not None and eff_punch_in is not None:
        # Convert to datetime objects for comparison
        if isinstance(eff_punch_in, str):
            punch_in_dt = datetime.fromisoformat(eff_punch_in.replace('Z', '+00:00'))
        elif isinstance(eff_punch_in, int):
            punch_in_dt = epoch_millis_to_utc_dt(eff_punch_in)
        else:
            punch_in_dt = eff_punch_in
        if isinstance(eff_punch_out, str):
            punch_out_dt = datetime.fromisoformat(eff_punch_out.replace('Z', '+00:00'))
        elif isinstance(eff_punch_out, int):
            punch_out_dt = epoch_millis_to_utc_dt(eff_punch_out)
        else:
            punch_out_dt = eff_punch_out

        # Ensure timezone-aware UTC
        if punch_in_dt.tzinfo is None:
            punch_in_dt = punch_in_dt.replace(tzinfo=timezone.utc)
        if punch_out_dt.tzinfo is None:
            punch_out_dt = punch_out_dt.replace(tzinfo=timezone.utc)

        # Check that punch_out is after punch_in
        if punch_out_dt <= punch_in_dt:
            raise HTTPException(422, "punch_out must be after punch_in")

        # Check that punch_out is within 24 hours of punch_in
        time_diff = punch_out_dt - punch_in_dt
        if time_diff.total_seconds() > 24 * 3600:  # 24 hours in seconds
            raise HTTPException(422, "punch_out must be within 24 hours of punch_in")

    # Check if this is a no-op (no actual changes)
    # Compare effective new values to current values
    status_changed = eff_status != current_status
    # For punch times, we need to compare actual values, handling None properly
    # Normalize to epoch milliseconds for proper comparison
    def to_epoch_millis_opt(dt):
        if dt is None:
            return None
        if isinstance(dt, int):
            return dt  # Already epoch milliseconds
        return utc_dt_to_epoch_millis(dt)  # Convert datetime to epoch milliseconds

    current_punch_in_epoch = to_epoch_millis_opt(current_punch_in)
    current_punch_out_epoch = to_epoch_millis_opt(current_punch_out)
    eff_punch_in_epoch = to_epoch_millis_opt(eff_punch_in)
    eff_punch_out_epoch = to_epoch_millis_opt(eff_punch_out)

    punch_in_changed = eff_punch_in_epoch != current_punch_in_epoch
    punch_out_changed = eff_punch_out_epoch != current_punch_out_epoch

    if not (status_changed or punch_in_changed or punch_out_changed):
        raise HTTPException(422, "Request changes nothing")

    # Prepare update data
    update_data = {}
    changes = {}  # For history entry

    # Status change
    if eff_status != current_status:
        update_data["status"] = eff_status
        changes["status"] = {"from": current_status, "to": eff_status}

    # Punch_in change - include if effective value differs from current
    if eff_punch_in != current_punch_in:
        if eff_punch_in is not None:
            update_data["punch_in"] = epoch_millis_to_utc_dt(eff_punch_in)
            changes["punch_in"] = {"from": utc_dt_to_epoch_millis(current_punch_in) if current_punch_in is not None else None, "to": eff_punch_in}
        else:
            # Setting to None - explicitly store null in MongoDB
            update_data["punch_in"] = None
            changes["punch_in"] = {"from": utc_dt_to_epoch_millis(current_punch_in) if current_punch_in is not None else None, "to": None}

    # Punch_out change - include if effective value differs from current
    if eff_punch_out != current_punch_out:
        if eff_punch_out is not None:
            update_data["punch_out"] = epoch_millis_to_utc_dt(eff_punch_out)
            changes["punch_out"] = {"from": utc_dt_to_epoch_millis(current_punch_out) if current_punch_out is not None else None, "to": eff_punch_out}
        else:
            # Setting to None - explicitly store null in MongoDB
            update_data["punch_out"] = None
            changes["punch_out"] = {"from": utc_dt_to_epoch_millis(current_punch_out) if current_punch_out is not None else None, "to": None}

    # If there are changes to apply, update the record and recompute derived fields
    if changes:
        # Store original values for recomputation and history tracking
        original_punch_in = record.get("punch_in")
        original_punch_out = record.get("punch_out")
        original_work_hours = record.get("work_hours")
        original_late_minutes = record.get("late_minutes")
        original_overtime_minutes = record.get("overtime_minutes")
        original_half_day = record.get("half_day")

        # Use effective new values (after applying business rules)
        effective_punch_in = eff_punch_in
        effective_punch_out = eff_punch_out

        # Convert to datetime objects for computation (if not None)
        punch_in_dt = None
        if effective_punch_in is not None:
            if isinstance(effective_punch_in, str):
                punch_in_dt = datetime.fromisoformat(effective_punch_in.replace('Z', '+00:00'))
            elif isinstance(effective_punch_in, int):
                punch_in_dt = epoch_millis_to_utc_dt(effective_punch_in)
            else:
                punch_in_dt = effective_punch_in
            if punch_in_dt.tzinfo is None:
                punch_in_dt = punch_in_dt.replace(tzinfo=timezone.utc)

        punch_out_dt = None
        if effective_punch_out is not None:
            if isinstance(effective_punch_out, str):
                punch_out_dt = datetime.fromisoformat(effective_punch_out.replace('Z', '+00:00'))
            elif isinstance(effective_punch_out, int):
                punch_out_dt = epoch_millis_to_utc_dt(effective_punch_out)
            else:
                punch_out_dt = effective_punch_out
            if punch_out_dt.tzinfo is None:
                punch_out_dt = punch_out_dt.replace(tzinfo=timezone.utc)

        # Recompute derived fields using helper functions
        if punch_in_dt is not None and punch_out_dt is not None:
            # Both punch_in and punch_out are present
            work_hours = compute_work_hours(punch_in_dt, punch_out_dt)
            overtime_minutes = compute_overtime(punch_out_dt, emp["shift_end"], date, emp["shift_start"])
            half_day = work_hours < 4.50
            late_minutes = compute_late_minutes(punch_in_dt, emp["shift_start"], date)
        elif punch_in_dt is not None and punch_out_dt is None:
            # Only punch_in is present (open record)
            work_hours = None
            overtime_minutes = 0
            half_day = False
            late_minutes = compute_late_minutes(punch_in_dt, emp["shift_start"], date)
        else:
            # No punch_in (absent/leave)
            work_hours = None
            late_minutes = 0
            overtime_minutes = 0
            half_day = False

        # Add derived fields to update
        update_data["work_hours"] = work_hours
        update_data["late_minutes"] = late_minutes
        update_data["overtime_minutes"] = overtime_minutes
        update_data["half_day"] = half_day

        # Track changes to derived fields for history
        if work_hours != original_work_hours:
            changes["work_hours"] = {"from": original_work_hours, "to": work_hours}
        if late_minutes != original_late_minutes:
            changes["late_minutes"] = {"from": original_late_minutes, "to": late_minutes}
        if overtime_minutes != original_overtime_minutes:
            changes["overtime_minutes"] = {"from": original_overtime_minutes, "to": overtime_minutes}
        if half_day != original_half_day:
            changes["half_day"] = {"from": original_half_day, "to": half_day}

        # Create history entry
        history_entry = {
            "at": truncate_to_seconds(int(datetime.now(timezone.utc).timestamp() * 1000)),  # Current time in epoch ms, truncated to seconds
            "by": body.regularized_by,
            "reason": body.reason,
            "changes": changes
        }

        # Perform conditional update to prevent race conditions
        # Only update if the record is still in the state we read
        # Use a single operation with both $set and $push for atomicity
        result = db.attendance_logs.update_one(
            {
                "_id": record["_id"],
                "emp_code": emp_code,
                "date": date,
                # Match on current state to prevent lost updates
                "status": current_status,
                "punch_in": current_punch_in,
                "punch_out": current_punch_out
            },
            {"$set": update_data, "$push": {"history": history_entry}}
        )

        if result.matched_count == 0:
            # Another request modified the record first
            raise HTTPException(409, "Record already updated by another request")

    # Fetch the updated record to return
    updated_record = db.attendance_logs.find_one({"_id": record["_id"]})

    # Format response (remove _id, convert datetime fields to epoch milliseconds)
    if updated_record:
        updated_record.pop("_id", None)
        if updated_record.get("punch_in") is not None:
            updated_record["punch_in"] = utc_dt_to_epoch_millis(updated_record["punch_in"])
        if updated_record.get("punch_out") is not None:
            updated_record["punch_out"] = utc_dt_to_epoch_millis(updated_record["punch_out"])

    return updated_record or {}


@app.get("/analytics/employees/{emp_code}/monthly")
def get_employee_monthly(emp_code: str, month: str):
    # Validate employee exists
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    # Validate month format strictly as YYYY-MM
    parts = month.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        raise HTTPException(422, "Invalid month format. Expected YYYY-MM")
    try:
        year, month_num = map(int, parts)
        if month_num < 1 or month_num > 12:
            raise ValueError
        # Validate it's a real calendar month
        first_day = datetime(year, month_num, 1)
        if month_num == 12:
            last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = datetime(year, month_num + 1, 1) - timedelta(days=1)
    except ValueError:
        raise HTTPException(422, "Invalid month format. Expected YYYY-MM")

    # Convert to datetime objects for comparison (start of month, end of month) in UTC
    month_start = datetime(year, month_num, 1, tzinfo=timezone.utc)
    month_end = datetime(year, month_num, last_day.day, 23, 59, 59, tzinfo=timezone.utc)

    # Convert joined_on to datetime for comparison
    joined_on_dt = datetime.fromisoformat(emp["joined_on"]).replace(tzinfo=timezone.utc)

    # Calculate effective start date (later of joined_on or month start) for working days eligibility
    effective_start = max(joined_on_dt, month_start)

    # Calculate working days: count of weekdays (Mon-Fri) from effective_start to month_end (inclusive)
    working_days_count = 0
    current = effective_start
    while current <= month_end:
        # Check if it's a weekday (Monday=0, Sunday=6)
        if current.weekday() < 5:  # Monday to Friday
            working_days_count += 1
        current += timedelta(days=1)

    # Format month_start and month_end as strings for the match (YYYY-MM-DD)
    month_start_str = month_start.strftime("%Y-%m-%d")
    month_end_str = month_end.strftime("%Y-%m-%d")

    # MongoDB aggregation pipeline for attendance metrics
    pipeline = [
        # Match records for this employee in the month (using month boundaries)
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start_str,
                    "$lte": month_end_str
                }
            }
        },
        # Add computed fields
        {
    "$addFields": {
        "date_obj": {
            "$dateFromString": {
                "dateString": "$date",
                "timezone": "Etc/UTC"
            }
        },
        "is_present": {
            "$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]
        },
        "is_leave": {"$eq": ["$status", "LEAVE"]},
        "is_late": {"$gt": ["$late_minutes", 0]}
    }
},
{
    "$addFields": {
        "is_weekday": {
            "$in": [{"$dayOfWeek": "$date_obj"}, [2, 3, 4, 5, 6]]
        }
    }
},
        # Group by employee to calculate aggregates
        {
            "$group": {
                "_id": "$emp_code",
                # Present days: sum of presence values (1 for full day, 0.5 for half day on weekdays only)
                "present_days": {
                    "$sum": {
                        "$cond": [
                            { "$and": [ "$is_present", "$is_weekday" ] },
                            { "$cond": [ { "$eq": [ "$half_day", True ] }, 0.5, 1 ] },
                            0
                        ]
                    }
                },
                # Leave days: count of LEAVE records
                "leave_days": { "$sum": { "$cond": [ "$is_leave", 1, 0 ] } },
                # Late count: records with late_minutes > 0
                "late_count": { "$sum": { "$cond": [ "$is_late", 1, 0 ] } },
                # Total late minutes: sum of late_minutes
                "total_late_minutes": { "$sum": "$late_minutes" },
                # Total overtime minutes: sum of overtime_minutes
                "total_overtime_minutes": { "$sum": "$overtime_minutes" }
            }
        },
        # Project to final format
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "present_days": 1,
                "leave_days": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "total_overtime_minutes": 1
            }
        }
    ]

    # Execute the aggregation
    result = list(db.attendance_logs.aggregate(pipeline))


    # Extract metrics from aggregation result (if any)
    if result:
        metrics = result[0]
        present_days = metrics.get("present_days", 0)
        leave_days = metrics.get("leave_days", 0)
        late_count = metrics.get("late_count", 0)
        total_late_minutes = metrics.get("total_late_minutes", 0)
        total_overtime_minutes = metrics.get("total_overtime_minutes", 0)
    else:
        present_days = 0.0
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0

    # Calculate attendance percentage
    if working_days_count > 0:
        attendance_pct = (present_days / working_days_count) * 100
        attendance_pct = round_half_up(attendance_pct, 2)
    else:
        attendance_pct = None

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days_count,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct
    }


@app.get("/analytics/leaderboard/late")
def get_late_leaderboard(month: str, limit: int = 10, department: Optional[str] = None):
    # Validate month format strictly as YYYY-MM
    parts = month.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        raise HTTPException(422, "Invalid month format. Expected YYYY-MM")
    try:
        year, month_num = map(int, parts)
        if month_num < 1 or month_num > 12:
            raise ValueError
        # Validate it's a real calendar month
        first_day = datetime(year, month_num, 1)
        if month_num == 12:
            last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = datetime(year, month_num + 1, 1) - timedelta(days=1)
    except ValueError:
        raise HTTPException(422, "Invalid month format. Expected YYYY-MM")

    # Validate limit parameter
    if limit < 1 or limit > 50:
        raise HTTPException(422, "limit must be between 1 and 50")

    # Compute month boundaries as strings for matching (YYYY-MM-DD)
    month_start_str = first_day.strftime("%Y-%m-%d")
    month_end_str = last_day.strftime("%Y-%m-%d")

    # Build the aggregation pipeline
    pipeline = []

    # Stage 1: Match attendance logs for the month
    match_stage = {
        "date": {"$gte": month_start_str, "$lte": month_end_str}
    }
    pipeline.append({"$match": match_stage})

    # Stage 2: Lookup employee details to get name and department
    # This also allows us to filter by department if specified
    lookup_stage = {
        "$lookup": {
            "from": "employees",
            "let": {"emp_code": "$emp_code"},
            "pipeline": [
                {
                    "$match": {
                        "$expr": {"$eq": ["$emp_code", "$$emp_code"]}
                    }
                }
            ],
            "as": "employee_info"
        }
    }
    pipeline.append(lookup_stage)

    # Stage 3: Filter out records where employee_info is empty (employee doesn't exist)
    pipeline.append({
        "$match": {
            "employee_info": {"$ne": []}
        }
    })

    # Stage 4: If department filter is provided, apply it
    if department is not None:
        pipeline.append({
            "$match": {
                "employee_info.department": department
            }
        })

    # Stage 5: Add fields for processing (extract first element from employee_info array)
    pipeline.append({
        "$addFields": {
            "employee_info": {"$arrayElemAt": ["$employee_info", 0]},
            "late_minutes": {"$ifNull": ["$late_minutes", 0]}  # Treat missing as 0
        }
    })

    # Stage 6: Group by employee to calculate totals
    pipeline.append({
        "$group": {
            "_id": {
                "emp_code": "$emp_code",
                "name": "$employee_info.name",
                "department": "$employee_info.department"
            },
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {
                "$sum": {
                    "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                }
            }
        }
    })

    # Stage 7: Filter out employees with zero total late minutes
    pipeline.append({
        "$match": {
            "total_late_minutes": {"$gt": 0}
        }
    })

    # Stage 8: Sort by total_late_minutes descending, then emp_code ascending
    pipeline.append({
        "$sort": {
            "total_late_minutes": -1,
            "_id.emp_code": 1
        }
    })

    # Stage 9: Add rank using standard competition ranking (same total_late_minutes gets same rank)
    pipeline.append({
        "$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {
                "rank": {
                    "$rank": {}
                }
            }
        }
    })

    # Stage 10: Filter to keep only items where rank <= limit
    pipeline.append({
        "$match": {
            "rank": {"$lte": limit}
        }
    })

    # Stage 11: Sort by total_late_minutes descending, then emp_code ascending (final display order)
    pipeline.append({
        "$sort": {
            "total_late_minutes": -1,
            "_id.emp_code": 1
        }
    })

    # Stage 12: Project to match the expected response format
    pipeline.append({
        "$project": {
            "_id": 0,
            "rank": 1,
            "emp_code": "$_id.emp_code",
            "name": "$_id.name",
            "department": "$_id.department",
            "total_late_minutes": 1,
            "late_count": 1
        }
    })

    # Execute the aggregation
    result = list(db.attendance_logs.aggregate(pipeline))

    return {"month": month, "items": result}


@app.get("/analytics/departments/{department}/trend")
def get_department_trend(
    department: str,
    from_date: str = Query(alias="from"),
    to_date: str = Query(alias="to")
):
    # Validate department exists (has at least one employee)
    employee_count = db.employees.count_documents({"department": department})
    if employee_count == 0:
        raise HTTPException(404, f"Unknown department: {department}")

    # Validate date format (YYYY-MM-DD)
    def validate_date(date_str):
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(422, "Invalid date format. Expected YYYY-MM-DD")

    from_dt = validate_date(from_date)
    to_dt = validate_date(to_date)

    # Validate to >= from
    if to_dt < from_dt:
        raise HTTPException(422, "'to' date must be greater than or equal to 'from' date")

    # Validate date range <= 92 days
    delta = to_dt - from_dt
    if delta.days > 92:
        raise HTTPException(422, "Date range must not exceed 92 days")

    # Build the aggregation pipeline
    pipeline = []

    # Stage 1: Get one employee from the department (we know it exists from count check)
    pipeline.append({
        "$match": {
            "department": department
        }
    })
    pipeline.append({
        "$limit": 1
    })

    # Stage 2: Add the from and to dates as ISO dates for calculation
    pipeline.append({
        "$addFields": {
            "from_date_iso": {"$dateFromString": {"dateString": from_date}},
            "to_date_iso": {"$dateFromString": {"dateString": to_date}}
        }
    })

    # Stage 3: Calculate number of days difference
    pipeline.append({
        "$addFields": {
            "day_diff": {
                "$divide": [
                    {"$subtract": ["$to_date_iso", "$from_date_iso"]},
                    86400000
                ]
            }
        }
    })

    # Stage 4: Generate array of day offsets [0, 1, 2, ..., day_diff]
    pipeline.append({
        "$addFields": {
            "day_offsets": {
                "$range": [0, {"$add": ["$day_diff", 1]}, 1]
            }
        }
    })

    # Stage 5: Map offsets to actual dates (as ISO dates)
    pipeline.append({
        "$addFields": {
            "date_array": {
                "$map": {
                    "input": "$day_offsets",
                    "as": "offset",
                    "in": {
                        "$add": ["$from_date_iso", {"$multiply": ["$$offset", 86400000]}]
                    }
                }
            }
        }
    })

    # Stage 6: Explode the date array into separate documents (one per date)
    pipeline.append({
        "$unwind": "$date_array"
    })

    # Stage 7: Rename for clarity and clean up temporary fields
    pipeline.append({
        "$addFields": {
            "date": "$date_array"
        }
    })
    pipeline.append({
        "$project": {
            "date_array": 0,
            "day_offsets": 0,
            "day_diff": 0,
            "from_date_iso": 0,
            "to_date_iso": 0
        }
    })

    # Stage 8: Add back the department field for use in lookups
    pipeline.append({
        "$addFields": {
            "department": department
        }
    })

    # Stage 9: Left join employees to calculate headcount for this date
    # Headcount = employees in department with joined_on <= current date
    pipeline.append({
        "$lookup": {
            "from": "employees",
            "let": {
                "dept": department,
                "current_date": "$date"
            },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$department", "$$dept"]},
                                {"$lte": [{"$dateFromString": {"dateString": "$joined_on"}}, "$$current_date"]}
                            ]
                        }
                    }
                }
            ],
            "as": "employees_for_headcount"
        }
    })

    # Stage 10: Calculate headcount
    pipeline.append({
        "$addFields": {
            "headcount": {"$size": "$employees_for_headcount"}
        }
    })

    # Stage 10b: Extract employee codes for attendance lookup
    pipeline.append({
        "$addFields": {
            "emp_codes": {
                "$map": {
                    "input": "$employees_for_headcount",
                    "as": "emp",
                    "in": "$$emp.emp_code"
                }
            }
        }
    })

    # Stage 11: Now left join attendance logs for this date and employees in department
    pipeline.append({
        "$lookup": {
            "from": "attendance_logs",
            "let": {
                "current_date": "$date",
                "emp_codes": "$emp_codes"
            },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$in": ["$emp_code", "$$emp_codes"]},
                                {"$eq": ["$date", {"$dateToString": {"format": "%Y-%m-%d", "date": "$$current_date"}}]}
                            ]
                        }
                    }
                }
            ],
            "as": "attendance_for_date"
        }
    })

    # Stage 12: Calculate present_count
    pipeline.append({
        "$addFields": {
            "present_count": {
                "$sum": {
                    "$map": {
                        "input": "$attendance_for_date",
                        "as": "record",
                        "in": {
                            "$cond": [
                                {"$in": ["$$record.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$cond": [{"$eq": ["$$record.half_day", True]}, 0.5, 1]},
                                0
                            ]
                        }
                    }
                }
            }
        }
    })

    # Stage 13: Calculate late_count
    pipeline.append({
        "$addFields": {
            "late_count": {
                "$sum": {
                    "$map": {
                        "input": "$attendance_for_date",
                        "as": "record",
                        "in": {
                            "$cond": [{
                                "$gt": ["$$record.late_minutes", 0]
                            }, 1, 0]
                        }
                    }
                }
            }
        }
    }
    )

    # Stage 14: Calculate is_working_day (Sunday=1, Monday=2, ..., Saturday=7 in MongoDB's dayOfWeek)
    # Monday-Friday are working days (2-6), Saturday-Sunday are not (1,7)
    pipeline.append({
        "$addFields": {
            "is_working_day": {
                "$and": [
                    {"$gte": [{"$dayOfWeek": "$date"}, 2]},
                    {"$lte": [{"$dayOfWeek": "$date"}, 6]}
                ]
            }
        }
    })

    # Stage 15: Calculate attendance_rate
    pipeline.append({
        "$addFields": {
            "attendance_rate": {
                "$cond": [
                    # If working day
                    {"$eq": ["$is_working_day", True]},
                    # Then: if headcount > 0, present_count/headcount; else 0.0
                    {
                        "$cond": [
                            {"$gt": ["$headcount", 0]},
                            {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]},
                            0.0
                        ]
                    },
                    # Else (not working day): null
                    None
                ]
            }
        }
    }
    )

    # Stage 16: Prepare for moving average calculation
    # Create helper fields for sum and count of non-null attendance_rate values
    pipeline.append({
        "$addFields": {
            "attendance_rate_not_null": {
                "$cond": [
                    {"$ne": ["$attendance_rate", None]},
                    "$attendance_rate",
                    0
                ]
            },
            "is_attendance_rate_not_null": {
                "$cond": [
                    {"$ne": ["$attendance_rate", None]},
                    1,
                    0
                ]
            }
        }
    })

    # Stage 17: Sort by date ascending for window calculations
    pipeline.append({
        "$sort": {
            "date": 1
        }
    })

    # Stage 18: Apply setWindowFields for moving average calculations
    # We need to compute the mean of non-null attendance_rate values in the window
    # Window: current date and up to 6 preceding days (total 7 days)
    pipeline.append({
        "$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {
                "sum_7d": {
                    "$sum": "$attendance_rate_not_null",
                    "window": {
                        "range": [-6, 0],  # 6 preceding + current
                        "unit": "day"
                    }
                },
                "count_7d": {
                    "$sum": "$is_attendance_rate_not_null",
                    "window": {
                        "range": [-6, 0],
                        "unit": "day"
                    }
                }
            }
        }
    })

    # Stage 19: Calculate moving_avg_7d = sum_7d / count_7d if count_7d > 0, else null
    pipeline.append({
        "$addFields": {
            "moving_avg_7d": {
                "$cond": [
                    {"$gt": ["$count_7d", 0]},
                    {"$round": [{"$divide": ["$sum_7d", "$count_7d"]}, 4]},
                    None
                ]
            }
        }
    })

    # Stage 20: Project out helper fields we don't want in the final output
    pipeline.append({
        "$project": {
            "attendance_rate_not_null": 0,
            "is_attendance_rate_not_null": 0,
            "sum_7d": 0,
            "count_7d": 0
        }
    })

    # Stage 21: Finally, project the fields in the required format
    pipeline.append({
        "$project": {
            "_id": 0,
            "date": {
                "$dateToString": {
                    "format": "%Y-%m-%d",
                    "date": "$date"
                }
            },
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": 1
        }
    })

    # Execute the aggregation
    result = list(db.employees.aggregate(pipeline))

    # Format the response
    return {
        "department": department,
        "items": result
    }


# TODO - the rest of the contract (see openapi.yaml):

# --------------------------------------------------------------------------- #
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/departments/summary
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: Optional[int] = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: Optional[int] = Query(1, ge=1),
    page_size: Optional[int] = Query(20, ge=1, le=100),
):
    # Validate endpoint (FastAPI enum validation will handle this via route, but double-checking)
    valid_endpoints = ["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"]
    if endpoint not in valid_endpoints:
        raise HTTPException(status_code=422, detail=f"Invalid endpoint. Must be one of: {valid_endpoints}")

    # Build and explain the query based on endpoint
    if endpoint == "attendance_list":
        # Recreate the exact same query as GET /attendance
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        if status:
            q["status"] = status

        skip = (page - 1) * page_size if page and page_size else 0

        # Explain the find operation
        explain_result = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size).explain()
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_result
        }

    elif endpoint == "employee_monthly":
        # Recreate the exact same aggregation as GET /analytics/employees/{emp_code}/monthly
        if not emp_code or not month:
            raise HTTPException(status_code=422, detail="emp_code and month are required for employee_monthly endpoint")

        # Validate month format
        parts = month.split("-")
        if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
            raise HTTPException(status_code=422, detail="Invalid month format. Expected YYYY-MM")
        try:
            year, month_num = map(int, parts)
            if month_num < 1 or month_num > 12:
                raise ValueError
            first_day = datetime(year, month_num, 1)
            if month_num == 12:
                last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
            else:
                last_day = datetime(year, month_num + 1, 1) - timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid month format. Expected YYYY-MM")

        month_start_str = first_day.strftime("%Y-%m-%d")
        month_end_str = last_day.strftime("%Y-%m-%d")

        # Build the exact same pipeline as the original endpoint
        pipeline = [
            # Match records for this employee in the month (using month boundaries)
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {
                        "$gte": month_start_str,
                        "$lte": month_end_str
                    }
                }
            },
            # Add computed fields
            {
                "$addFields": {
                    "date_obj": {
                        "$dateFromString": {
                            "dateString": "$date",
                            "timezone": "Etc/UTC"
                        }
                    },
                    "is_present": {
                        "$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]
                    },
                    "is_leave": {"$eq": ["$status", "LEAVE"]},
                    "is_late": {"$gt": ["$late_minutes", 0]}
                }
            },
            {
                "$addFields": {
                    "is_weekday": {
                        "$in": [{"$dayOfWeek": "$date_obj"}, [2, 3, 4, 5, 6]]
                    }
                }
            },
            # Group by employee to calculate aggregates
            {
                "$group": {
                    "_id": "$emp_code",
                    # Present days: sum of presence values (1 for full day, 0.5 for half day on weekdays only)
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                { "$and": [ "$is_present", "$is_weekday" ] },
                                { "$cond": [ { "$eq": [ "$half_day", True ] }, 0.5, 1 ] },
                                0
                            ]
                        }
                    },
                    # Leave days: count of LEAVE records
                    "leave_days": { "$sum": { "$cond": [ "$is_leave", 1, 0 ] } },
                    # Late count: records with late_minutes > 0
                    "late_count": { "$sum": { "$cond": [ "$is_late", 1, 0 ] } },
                    # Total late minutes: sum of late_minutes
                    "total_late_minutes": { "$sum": "$late_minutes" },
                    # Total overtime minutes: sum of overtime_minutes
                    "total_overtime_minutes": { "$sum": "$overtime_minutes" }
                }
            },
            # Project to final format
            {
                "$project": {
                    "_id": 0,
                    "emp_code": "$_id",
                    "present_days": 1,
                    "leave_days": 1,
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "total_overtime_minutes": 1
                }
            }
        ]

        # Explain the aggregate operation using database command
        explain_result = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {}
            },
            "verbosity": "executionStats"
        })
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_result
        }

    elif endpoint == "department_summary":
        # Recreate the exact same aggregation as GET /analytics/departments/summary
        if not month:
            raise HTTPException(status_code=422, detail="month is required for department_summary endpoint")

        # Validate month format (copied from the original endpoint)
        parts = month.split("-")
        if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
            raise HTTPException(status_code=422, detail="Invalid month format. Expected YYYY-MM")
        try:
            year, month_num = map(int, parts)
            if month_num < 1 or month_num > 12:
                raise ValueError
            # Validate it's a real calendar month
            first_day = datetime(year, month_num, 1)
            if month_num == 12:
                last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
            else:
                last_day = datetime(year, month_num + 1, 1) - timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid month format. Expected YYYY-MM")

        # Compute month boundaries as strings for matching (YYYY-MM-DD)
        month_start_str = first_day.strftime("%Y-%m-%d")
        month_end_str = last_day.strftime("%Y-%m-%d")

        # Build the exact same pipeline as the original endpoint
        pipeline = []

        # Stage 1: Match employees by department (if provided) and joined_on <= month_end
        match_stage = {}
        if department is not None:
            match_stage["department"] = department
        match_stage["joined_on"] = {"$lte": month_end_str}
        if match_stage:
            pipeline.append({"$match": match_stage})

        # Stage 2: Lookup attendance logs for the month
        pipeline.append({
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "emp_code": "$emp_code",
                    "month_start": month_start_str,
                    "month_end": month_end_str
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$emp_code"]},
                                    {"$gte": ["$date", "$$month_start"]},
                                    {"$lte": ["$date", "$$month_end"]}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        })

        # Stage 3: Unwind logs to process each attendance record (preserve null and empty arrays)
        pipeline.append({
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True
            }
        })

        # Stage 4: Add fields for per-log metrics (handle null logs)
        pipeline.append({
            "$addFields": {
                "status": "$logs.status",
                "work_hours": "$logs.work_hours",
                "late_minutes": "$logs.late_minutes",
                "half_day": "$logs.half_day",
                "date": "$logs.date"
            }
        })

        # Stage 5: Compute per-log contributions

        # Stage 5: Compute base flags
        pipeline.append({
            "$addFields": {
                "is_present": {
                    "$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]
                },
                "is_weekday": {
                    "$cond": [
                        {"$ne": ["$date", None]},
                        {
                            "$in": [
                                {
                                    "$dayOfWeek": {
                                        "$dateFromString": {
                                            "dateString": "$date",
                                            "timezone": "Etc/UTC"
                                        }
                                    }
                                },
                                [2, 3, 4, 5, 6]
                            ]
                        },
                        False
                    ]
                },
                "is_late": {"$gt": ["$late_minutes", 0]},
                "is_leave": {"$eq": ["$status", "LEAVE"]},
                "is_on_duty": {"$eq": ["$status", "ON_DUTY"]}
            }
        })

        # Stage 5b: Compute contributions from the flags above
        pipeline.append({
            "$addFields": {
                "present_value": {
                    "$cond": [
                        {"$and": ["$is_present", "$is_weekday"]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                        0
                    ]
                },
                "eligible_work_hours": {
                    "$cond": [
                        {"$and": [
                            "$is_present",
                            {"$ne": ["$work_hours", None]}
                        ]},
                        "$work_hours",
                        None
                    ]
                }
            }
        })

        # Stage 6: Group by employee and department to get per-employee aggregates
        pipeline.append({
            "$group": {
                "_id": {
                    "department": "$department",
                    "emp_code": "$emp_code"
                },
                # Headcount per employee: 1
                "employee_count": {"$sum": 1},
                # Present days: sum of present_value across logs
                "present_days": {"$sum": "$present_value"},
                # For work_hours average: sum and count of eligible_work_hours (non-null)
                "total_work_hours": {
                    "$sum": {
                        "$cond": [
                            {"$ne": ["$eligible_work_hours", None]},
                            "$eligible_work_hours",
                            0
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {"$ne": ["$eligible_work_hours", None]},
                            1,
                            0
                        ]
                    }
                },
                # Late count and total late minutes
                "late_count": {"$sum": {"$cond": ["$is_late", 1, 0]}},
                "total_late_minutes": {"$sum": "$late_minutes"},
                # Leave count
                "leave_count": {"$sum": {"$cond": ["$is_leave", 1, 0]}},
                # On duty count
                "on_duty_count": {"$sum": {"$cond": ["$is_on_duty", 1, 0]}}
            }
        })

        # Stage 7: Group by department to get department-level aggregates
        pipeline.append({
            "$group": {
                "_id": "$_id.department",
                # Headcount: sum of employee_count (each employee counted once)
                "headcount": {"$sum": "$employee_count"},
                # Present days: sum of present_days
                "present_days": {"$sum": "$present_days"},
                # Total work_hours and count for avg_work_hours calculation
                "total_work_hours": {"$sum": "$total_work_hours"},
                "work_hours_count": {"$sum": "$work_hours_count"},
                # Late metrics
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                # Leave and on duty counts
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"}
            }
        })

        # Stage 8: Match to exclude departments with zero headcount
        pipeline.append({"$match": {"headcount": {"$gt": 0}}})

        # Stage 9: Sort by department ascending
        pipeline.append({"$sort": {"_id": 1}})

        # Stage 10: Project to final format (we'll compute avg_work_hours in Python)
        pipeline.append({
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "total_work_hours": 1,
                "work_hours_count": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        })

        # Explain the aggregate operation using database command
        explain_result = db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {}
            },
            "verbosity": "executionStats"
        })
        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_result
        }

    elif endpoint == "late_leaderboard":
        # Recreate the exact same aggregation as GET /analytics/leaderboard/late
        if not month:
            raise HTTPException(status_code=422, detail="month is required for late_leaderboard endpoint")

        # Validate month format (copied from the original endpoint)
        parts = month.split("-")
        if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
            raise HTTPException(status_code=422, detail="Invalid month format. Expected YYYY-MM")
        try:
            year, month_num = map(int, parts)
            if month_num < 1 or month_num > 12:
                raise ValueError
            # Validate it's a real calendar month
            first_day = datetime(year, month_num, 1)
            if month_num == 12:
                last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
            else:
                last_day = datetime(year, month_num + 1, 1) - timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid month format. Expected YYYY-MM")

        # Validate limit parameter
        if limit < 1 or limit > 50:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 50")

        # Compute month boundaries as strings for matching (YYYY-MM-DD)
        month_start_str = first_day.strftime("%Y-%m-%d")
        month_end_str = last_day.strftime("%Y-%m-%d")

        # Build the exact same pipeline as the original endpoint
        pipeline = []

        # Stage 1: Match attendance logs for the month
        match_stage = {
            "date": {"$gte": month_start_str, "$lte": month_end_str}
        }
        pipeline.append({"$match": match_stage})

        # Stage 2: Lookup employee details to get name and department
        lookup_stage = {
            "$lookup": {
                "from": "employees",
                "let": {"emp_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$emp_code", "$$emp_code"]}
                        }
                    }
                ],
                "as": "employee_info"
            }
        }
        pipeline.append(lookup_stage)

        # Stage 3: Filter out records where employee_info is empty (employee doesn't exist)
        pipeline.append({
            "$match": {
                "employee_info": {"$ne": []}
            }
        })

        # Stage 4: If department filter is provided, apply it
        if department is not None:
            pipeline.append({
                "$match": {
                    "employee_info.department": department
                }
            })

        # Stage 5: Add fields for processing (extract first element from employee_info array)
        pipeline.append({
            "$addFields": {
                "employee_info": {"$arrayElemAt": ["$employee_info", 0]},
                "late_minutes": {"$ifNull": ["$late_minutes", 0]}  # Treat missing as 0
            }
        })

        # Stage 6: Group by employee to calculate totals
        pipeline.append({
            "$group": {
                "_id": {
                    "emp_code": "$emp_code",
                    "name": "$employee_info.name",
                    "department": "$employee_info.department"
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                }
            }
        })

        # Stage 7: Filter out employees with zero total late minutes
        pipeline.append({
            "$match": {
                "total_late_minutes": {"$gt": 0}
            }
        })

        # Stage 8: Sort by total_late_minutes descending, then emp_code ascending
        pipeline.append({
            "$sort": {
                "total_late_minutes": -1,
                "_id.emp_code": 1
            }
        })

        # Explain the aggregate operation using database command
        explain_result = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {}
            },
            "verbosity": "executionStats"
        })
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_result
        }

    elif endpoint == "department_trend":
        # Recreate the exact same aggregation as GET /analytics/departments/{department}/trend
        if not department:
            raise HTTPException(status_code=422, detail="department is required for department_trend endpoint")
        if not from_date or not to_date:
            raise HTTPException(status_code=422, detail="from and to dates are required for department_trend endpoint")

        # Validate department exists (has at least one employee)
        employee_count = db.employees.count_documents({"department": department})
        if employee_count == 0:
            raise HTTPException(status_code=404, detail=f"Unknown department: {department}")

        # Validate date format (YYYY-MM-DD)
        def validate_date(date_str):
            try:
                return datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=422, detail="Invalid date format. Expected YYYY-MM-DD")

        from_dt = validate_date(from_date)
        to_dt = validate_date(to_date)

        # Validate to >= from
        if to_dt < from_dt:
            raise HTTPException(status_code=422, detail="'to' date must be greater than or equal to 'from' date")

        # Validate date range <= 92 days
        delta = to_dt - from_dt
        if delta.days > 92:
            raise HTTPException(status_code=422, detail="Date range must not exceed 92 days")

        # Build the exact same pipeline as the original endpoint
        pipeline = []

        # Stage 1: Get one employee from the department (we know it exists from count check)
        pipeline.append({
            "$match": {
                "department": department
            }
        })
        pipeline.append({
            "$limit": 1
        })

        # Stage 2: Add the from and to dates as ISO dates for calculation
        pipeline.append({
            "$addFields": {
                "from_date_iso": {"$dateFromString": {"dateString": from_date}},
                "to_date_iso": {"$dateFromString": {"dateString": to_date}}
            }
        })

        # Stage 3: Calculate number of days difference
        pipeline.append({
            "$addFields": {
                "day_diff": {
                    "$divide": [
                        {"$subtract": ["$to_date_iso", "$from_date_iso"]},
                        86400000
                    ]
                }
            }
        })

        # Stage 4: Generate array of day offsets [0, 1, 2, ..., day_diff]
        pipeline.append({
            "$addFields": {
                "day_offsets": {
                    "$range": [0, {"$add": ["$day_diff", 1]}, 1]
                }
            }
        })

        # Stage 5: Map offsets to actual dates (as ISO dates)
        pipeline.append({
            "$addFields": {
                "date_array": {
                    "$map": {
                        "input": "$day_offsets",
                        "as": "offset",
                        "in": {
                            "$add": ["$from_date_iso", {"$multiply": ["$$offset", 86400000]}]
                        }
                    }
                }
            }
        })

        # Stage 6: Explode the date array into separate documents (one per date)
        pipeline.append({
            "$unwind": "$date_array"
        })

        # Stage 7: Rename for clarity and clean up temporary fields
        pipeline.append({
            "$addFields": {
                "date": "$date_array"
            }
        })
        pipeline.append({
            "$project": {
                "date_array": 0,
                "day_offsets": 0,
                "day_diff": 0,
                "from_date_iso": 0,
                "to_date_iso": 0
            }
        })

        # Stage 8: Add back the department field for use in lookups
        pipeline.append({
            "$addFields": {
                "department": department
            }
        })

        # Stage 9: Left join employees to calculate headcount for this date
        # Headcount = employees in department with joined_on <= current date
        pipeline.append({
            "$lookup": {
                "from": "employees",
                "let": {
                    "dept": department,
                    "current_date": "$date"
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", "$$dept"]},
                                    {"$lte": [{"$dateFromString": {"dateString": "$joined_on"}}, "$$current_date"]}
                                ]
                            }
                        }
                    }
                ],
                "as": "employees_for_headcount"
            }
        })

        # Stage 10: Calculate headcount
        pipeline.append({
            "$addFields": {
                "headcount": {"$size": "$employees_for_headcount"}
            }
        })

        # Stage 10b: Extract employee codes for attendance lookup
        pipeline.append({
            "$addFields": {
                "emp_codes": {
                    "$map": {
                        "input": "$employees_for_headcount",
                        "as": "emp",
                        "in": "$$emp.emp_code"
                    }
                }
            }
        })

        # Stage 11: Now left join attendance logs for this date and employees in department
        pipeline.append({
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "current_date": "$date",
                    "emp_codes": "$emp_codes"
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$in": ["$emp_code", "$$emp_codes"]},
                                    {"$eq": ["$date", {"$dateToString": {"format": "%Y-%m-%d", "date": "$$current_date"}}]}
                                ]
                            }
                        }
                    }
                ],
                "as": "attendance_for_date"
            }
        })

        # Stage 12: Calculate present_count
        pipeline.append({
            "$addFields": {
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": "$attendance_for_date",
                            "as": "record",
                            "in": {
                                "$cond": [
                                    {"$in": ["$$record.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$cond": [{"$eq": ["$$record.half_day", True]}, 0.5, 1]},
                                    0
                                ]
                            }
                        }
                    }
                }
            }
        })

        # Stage 13: Calculate late_count
        pipeline.append({
            "$addFields": {
                "late_count": {
                    "$sum": {
                        "$map": {
                            "input": "$attendance_for_date",
                            "as": "record",
                            "in": {
                                "$cond": [{
                                    "$gt": ["$$record.late_minutes", 0]
                                }, 1, 0]
                            }
                        }
                    }
                }
            }
        })

        # Stage 14: Calculate is_working_day (Sunday=1, Monday=2, ..., Saturday=7 in MongoDB's dayOfWeek)
        # Monday-Friday are working days (2-6), Saturday-Sunday are not (1,7)
        pipeline.append({
            "$addFields": {
                "is_working_day": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$date"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$date"}, 6]}
                    ]
                }
            }
        })

        # Stage 15: Calculate attendance_rate
        pipeline.append({
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        # If working day
                        {"$eq": ["$is_working_day", True]},
                        # Then: if headcount > 0, present_count/headcount; else 0.0
                        {
                            "$cond": [
                                {"$gt": ["$headcount", 0]},
                                {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]},
                                0.0
                            ]
                        },
                        # Else (not working day): null
                        None
                    ]
                }
            }
        })

        # Stage 16: Prepare for moving average calculation
        # Create helper fields for sum and count of non-null attendance_rate values
        pipeline.append({
            "$addFields": {
                "attendance_rate_not_null": {
                    "$cond": [
                        {"$ne": ["$attendance_rate", None]},
                        "$attendance_rate",
                        0
                    ]
                },
                "is_attendance_rate_not_null": {
                    "$cond": [
                        {"$ne": ["$attendance_rate", None]},
                        1,
                        0
                    ]
                }
            }
        })

        # Stage 17: Sort by date ascending for window calculations
        pipeline.append({
            "$sort": {
                "date": 1
            }
        })

        # Stage 18: Apply setWindowFields for moving average calculations
        # We need to compute the mean of non-null attendance_rate values in the window
        # Window: current date and up to 6 preceding days (total 7 days)
        pipeline.append({
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "sum_7d": {
                        "$sum": "$attendance_rate_not_null",
                        "window": {
                            "range": [-6, 0],  # 6 preceding + current
                            "unit": "day"
                        }
                    },
                    "count_7d": {
                        "$sum": "$is_attendance_rate_not_null",
                        "window": {
                            "range": [-6, 0],
                            "unit": "day"
                        }
                    }
                }
            }
        })

        # Stage 19: Calculate moving_avg_7d = sum_7d / count_7d if count_7d > 0, else null
        pipeline.append({
            "$addFields": {
                "moving_avg_7d": {
                    "$cond": [
                        {"$gt": ["$count_7d", 0]},
                        {"$round": [{"$divide": ["$sum_7d", "$count_7d"]}, 4]},
                        None
                    ]
                }
            }
        })

        # Stage 20: Project out helper fields we don't want in the final output
        pipeline.append({
            "$project": {
                "attendance_rate_not_null": 0,
                "is_attendance_rate_not_null": 0,
                "sum_7d": 0,
                "count_7d": 0
            }
        })

        # Stage 21: Finally, project the fields in the required format
        pipeline.append({
            "$project": {
                "_id": 0,
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date"
                    }
                },
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1
            }
        })

        # Explain the aggregate operation using database command
        explain_result = db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {}
            },
            "verbosity": "executionStats"
        })
        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_result
        }
@app.on_event("startup")
def startup_event():
    # Create unique index on employees.emp_code
    db.employees.create_index("emp_code", unique=True)
    # Create index on employees.joined_on for department_summary joined_on <= month_end filter
    db.employees.create_index("joined_on")
    # Create index on employees.department for department_trend department lookup
    db.employees.create_index("department")
    # Create unique compound index on attendance_logs.emp_code and attendance_logs.date
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    # Create compound index to support attendance list queries: date descending, emp_code ascending
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])