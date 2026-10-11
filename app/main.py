"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
from enum import Enum
import json
import math
import os
from contextlib import asynccontextmanager
from bson import json_util
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, status as http_status
from pydantic import BaseModel, Field, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client: MongoClient = MongoClient(MONGO_URI, tz_aware=True)
db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.employees.create_index([("emp_code", 1)], unique=True)
    db.employees.create_index([("department", 1), ("joined_on", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)])
    db.attendance_logs.create_index([("date", 1), ("status", 1)])
    yield


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


def truncate_instant_ms(ms: int) -> int:
    return (ms // 1000) * 1000


def epoch_ms_to_utc_dt(ms: int) -> datetime:
    truncated_ms = truncate_instant_ms(ms)
    return datetime.fromtimestamp(truncated_ms / 1000.0, tz=timezone.utc)


def utc_dt_to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def resolve_attendance_date(punch_in_dt: datetime, shift_start: str, shift_end: str) -> str:
    punch_in_ist = punch_in_dt.astimezone(IST)
    sh_h, sh_m = map(int, shift_start.split(":"))
    eh_h, eh_m = map(int, shift_end.split(":"))
    start_time = time(sh_h, sh_m)
    end_time = time(eh_h, eh_m)

    if end_time <= start_time:
        if punch_in_ist.time() < end_time:
            return (punch_in_ist.date() - timedelta(days=1)).isoformat()
    return punch_in_ist.date().isoformat()


def compute_late_minutes(punch_in_dt: datetime, shift_start: str, date_str: str) -> int:
    punch_in_ist = punch_in_dt.astimezone(IST)
    h, m = map(int, shift_start.split(":"))
    shift_date = date.fromisoformat(date_str)
    start_dt = datetime.combine(shift_date, time(h, m), tzinfo=IST)

    diff_seconds = (punch_in_ist - start_dt).total_seconds()
    if diff_seconds > 600:
        return int(math.floor(diff_seconds / 60.0))
    return 0


def compute_overtime(
    punch_out_dt: datetime,
    shift_end: str,
    shift_start: str,
    date_str: str,
) -> int:
    punch_out_ist = punch_out_dt.astimezone(IST)
    sh_h, sh_m = map(int, shift_start.split(":"))
    eh_h, eh_m = map(int, shift_end.split(":"))

    shift_date = date.fromisoformat(date_str)
    if time(eh_h, eh_m) <= time(sh_h, sh_m):
        end_date = shift_date + timedelta(days=1)
    else:
        end_date = shift_date

    end_dt = datetime.combine(end_date, time(eh_h, eh_m), tzinfo=IST)
    diff_seconds = (punch_out_ist - end_dt).total_seconds()
    if diff_seconds >= 1800:
        return int(math.floor(diff_seconds / 60.0))
    return 0


def compute_work_hours(punch_in_dt: datetime, punch_out_dt: datetime) -> float:
    diff_seconds = (punch_out_dt - punch_in_dt).total_seconds()
    hours = Decimal(str(diff_seconds)) / Decimal("3600")
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_half_day(work_hours: Optional[float]) -> bool:
    if work_hours is None:
        return False
    return work_hours < 4.50


def round_half_up(value: Union[float, Decimal], decimals: int = 2) -> float:
    pattern = "0." + "0" * decimals if decimals > 0 else "1"
    return float(Decimal(str(value)).quantize(Decimal(pattern), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- #
# Models & Schemas
# --------------------------------------------------------------------------- #
class StatusEnum(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"
    WFH = "WFH"
    ON_DUTY = "ON_DUTY"


class PresenceStatusEnum(str, Enum):
    PRESENT = "PRESENT"
    WFH = "WFH"
    ON_DUTY = "ON_DUTY"


class EmployeeCreate(BaseModel):
    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field("09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field("18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

    @model_validator(mode="after")
    def validate_shift_times(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class Employee(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str
    shift_end: str
    joined_on: str
    created_at: int


class EmployeePage(BaseModel):
    items: List[Employee]
    total: int
    page: int
    page_size: int


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    status: PresenceStatusEnum = PresenceStatusEnum.PRESENT


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)


class RegularizeRequest(BaseModel):
    status: Optional[StatusEnum] = None
    punch_in: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    punch_out: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)


class HistoryEntry(BaseModel):
    at: int
    by: str
    reason: str
    changes: Dict[str, Dict[str, Any]]


class AttendanceRecord(BaseModel):
    emp_code: str
    date: str
    status: str
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    work_hours: Optional[float] = None
    late_minutes: int
    overtime_minutes: int
    half_day: bool
    history: List[HistoryEntry] = []


class AttendancePage(BaseModel):
    items: List[AttendanceRecord]
    total: int
    page: int
    page_size: int


class EmployeeMonthly(BaseModel):
    emp_code: str
    month: str
    working_days: int
    present_days: float
    leave_days: int
    late_count: int
    total_late_minutes: int
    total_overtime_minutes: int
    attendance_pct: Optional[float] = None


class DepartmentSummaryItem(BaseModel):
    department: str
    headcount: int
    present_days: float
    avg_work_hours: Optional[float] = None
    late_count: int
    total_late_minutes: int
    leave_count: int
    on_duty_count: int


class DepartmentSummary(BaseModel):
    month: str
    items: List[DepartmentSummaryItem]


class LeaderboardItem(BaseModel):
    rank: int
    emp_code: str
    name: str
    department: str
    total_late_minutes: int
    late_count: int


class Leaderboard(BaseModel):
    month: str
    items: List[LeaderboardItem]


class TrendItem(BaseModel):
    date: str
    is_working_day: bool
    headcount: int
    present_count: float
    late_count: int
    attendance_rate: Optional[float] = None
    moving_avg_7d: Optional[float] = None


class Trend(BaseModel):
    department: str
    items: List[TrendItem]


def calculate_working_days(joined_on_str: str, month_str: str) -> int:
    y, m = map(int, month_str.split("-"))
    first_day = date(y, m, 1)
    if m == 12:
        last_day = date(y, 12, 31)
    else:
        last_day = date(y, m + 1, 1) - timedelta(days=1)

    join_date = date.fromisoformat(joined_on_str)
    start_date = max(first_day, join_date)

    if start_date > last_day:
        return 0

    count = 0
    curr = start_date
    while curr <= last_day:
        if curr.weekday() < 5:
            count += 1
        curr += timedelta(days=1)
    return count


# --------------------------------------------------------------------------- #
# Formatters
# --------------------------------------------------------------------------- #
def format_employee(doc: Dict[str, Any]) -> Dict[str, Any]:
    c_at = doc.get("created_at")
    return {
        "emp_code": doc.get("emp_code"),
        "name": doc.get("name"),
        "email": doc.get("email"),
        "department": doc.get("department"),
        "shift_start": doc.get("shift_start"),
        "shift_end": doc.get("shift_end"),
        "joined_on": doc.get("joined_on"),
        "created_at": utc_dt_to_epoch_ms(c_at) if isinstance(c_at, datetime) else c_at,
    }


def format_attendance_record(doc: Dict[str, Any]) -> Dict[str, Any]:
    history_list = []
    for h in doc.get("history", []):
        h_changes = {}
        for field, change in h.get("changes", {}).items():
            f_val = change.get("from")
            t_val = change.get("to")
            if field in ("punch_in", "punch_out"):
                if isinstance(f_val, datetime):
                    f_val = utc_dt_to_epoch_ms(f_val)
                if isinstance(t_val, datetime):
                    t_val = utc_dt_to_epoch_ms(t_val)
            h_changes[field] = {"from": f_val, "to": t_val}

        h_at = h.get("at")
        if isinstance(h_at, datetime):
            h_at = utc_dt_to_epoch_ms(h_at)

        history_list.append({
            "at": h_at,
            "by": h.get("by"),
            "reason": h.get("reason"),
            "changes": h_changes,
        })

    p_in = doc.get("punch_in")
    p_out = doc.get("punch_out")

    return {
        "emp_code": doc.get("emp_code"),
        "date": doc.get("date"),
        "status": doc.get("status"),
        "punch_in": utc_dt_to_epoch_ms(p_in) if isinstance(p_in, datetime) else p_in,
        "punch_out": utc_dt_to_epoch_ms(p_out) if isinstance(p_out, datetime) else p_out,
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": history_list,
    }


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["system"])
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service unavailable",
        )


@app.post(
    "/employees",
    response_model=Employee,
    status_code=http_status.HTTP_201_CREATED,
    tags=["employees"],
)
def create_employee(body: EmployeeCreate):
    created_at_dt = datetime.now(timezone.utc)
    doc = body.model_dump()
    doc["created_at"] = created_at_dt

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="emp_code already exists",
        )

    return format_employee(doc)


@app.get("/employees", response_model=EmployeePage, tags=["employees"])
def list_employees(
    department: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if department is not None:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    cursor = db.employees.find(q).sort("emp_code", 1).skip(skip).limit(page_size)
    items = [format_employee(d) for d in cursor]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post(
    "/attendance/punch-in",
    response_model=AttendanceRecord,
    status_code=http_status.HTTP_201_CREATED,
    tags=["attendance"],
)
def punch_in(body: PunchInRequest):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Employee not found",
        )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    raw_ms = body.punched_at if body.punched_at is not None else now_ms
    punch_in_dt = epoch_ms_to_utc_dt(raw_ms)

    att_date = resolve_attendance_date(punch_in_dt, emp["shift_start"], emp["shift_end"])
    late_mins = compute_late_minutes(punch_in_dt, emp["shift_start"], att_date)

    doc = {
        "emp_code": body.emp_code,
        "date": att_date,
        "status": body.status.value,
        "punch_in": punch_in_dt,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_mins,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="already punched in for this date",
        )

    return format_attendance_record(doc)


@app.post(
    "/attendance/punch-out",
    response_model=AttendanceRecord,
    status_code=http_status.HTTP_200_OK,
    tags=["attendance"],
)
def punch_out(body: PunchOutRequest):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Employee not found",
        )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    raw_ms = body.punched_at if body.punched_at is not None else now_ms
    punch_out_dt = epoch_ms_to_utc_dt(raw_ms)

    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punch_out_dt}},
        sort=[("punch_in", -1)],
    )

    if not record:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="No punch-in found",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="already punched out",
        )

    punch_in_dt = record["punch_in"]
    if punch_in_dt.tzinfo is None:
        punch_in_dt = punch_in_dt.replace(tzinfo=timezone.utc)

    if punch_out_dt <= punch_in_dt or punch_out_dt > punch_in_dt + timedelta(hours=24):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="punched_at must be strictly after punch_in and within 24 hours",
        )

    work_hours = compute_work_hours(punch_in_dt, punch_out_dt)
    overtime_mins = compute_overtime(punch_out_dt, emp["shift_end"], emp["shift_start"], record["date"])
    half_day = compute_half_day(work_hours)

    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": punch_out_dt,
                "work_hours": work_hours,
                "overtime_minutes": overtime_mins,
                "half_day": half_day,
            }
        },
        return_document=True,
    )

    if not updated:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="already punched out",
        )

    return format_attendance_record(updated)


@app.get("/attendance", response_model=AttendancePage, tags=["attendance"])
def list_attendance(
    emp_code: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    status: Optional[StatusEnum] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date_from cannot be greater than date_to",
        )

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
        q["status"] = status.value

    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size)

    items = [format_attendance_record(d) for d in cursor]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.patch(
    "/attendance/{emp_code}/{date}",
    response_model=AttendanceRecord,
    tags=["attendance"],
)
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Employee not found",
        )

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Record not found",
        )

    curr_status = record.get("status")
    curr_punch_in = record.get("punch_in")
    curr_punch_out = record.get("punch_out")

    target_status = body.status.value if body.status is not None else curr_status

    if target_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="punch_in and punch_out cannot be supplied when status is ABSENT or LEAVE",
            )
        target_punch_in = None
        target_punch_out = None
    else:
        if body.punch_in is not None:
            target_punch_in = epoch_ms_to_utc_dt(body.punch_in)
        else:
            target_punch_in = curr_punch_in

        if body.punch_out is not None:
            target_punch_out = epoch_ms_to_utc_dt(body.punch_out)
        else:
            target_punch_out = curr_punch_out

        if target_punch_in is None:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="punch_in is required for presence status",
            )

        resolved_date = resolve_attendance_date(target_punch_in, emp["shift_start"], emp["shift_end"])
        if resolved_date != date:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="punch_in must stay on the record's attendance date",
            )

        if target_punch_out is not None:
            p_in = target_punch_in.replace(tzinfo=timezone.utc) if target_punch_in.tzinfo is None else target_punch_in
            p_out = target_punch_out.replace(tzinfo=timezone.utc) if target_punch_out.tzinfo is None else target_punch_out
            if p_out <= p_in or p_out > p_in + timedelta(hours=24):
                raise HTTPException(
                    status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="punch_out must be after punch_in and within 24 hours",
                )

    if target_status in ("ABSENT", "LEAVE"):
        target_work_hours = None
        target_late_minutes = 0
        target_overtime_minutes = 0
        target_half_day = False
    else:
        target_late_minutes = compute_late_minutes(target_punch_in, emp["shift_start"], date)
        if target_punch_out is not None:
            target_work_hours = compute_work_hours(target_punch_in, target_punch_out)
            target_overtime_minutes = compute_overtime(target_punch_out, emp["shift_end"], emp["shift_start"], date)
            target_half_day = compute_half_day(target_work_hours)
        else:
            target_work_hours = None
            target_overtime_minutes = 0
            target_half_day = False

    changes = {}
    if target_status != curr_status:
        changes["status"] = {"from": curr_status, "to": target_status}

    if target_punch_in != curr_punch_in:
        changes["punch_in"] = {"from": curr_punch_in, "to": target_punch_in}

    if target_punch_out != curr_punch_out:
        changes["punch_out"] = {"from": curr_punch_out, "to": target_punch_out}

    if target_work_hours != record.get("work_hours"):
        changes["work_hours"] = {"from": record.get("work_hours"), "to": target_work_hours}

    if target_late_minutes != record.get("late_minutes", 0):
        changes["late_minutes"] = {"from": record.get("late_minutes", 0), "to": target_late_minutes}

    if target_overtime_minutes != record.get("overtime_minutes", 0):
        changes["overtime_minutes"] = {"from": record.get("overtime_minutes", 0), "to": target_overtime_minutes}

    if target_half_day != record.get("half_day", False):
        changes["half_day"] = {"from": record.get("half_day", False), "to": target_half_day}

    if not changes:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="request changes nothing",
        )

    now_ms = truncate_instant_ms(int(datetime.now(timezone.utc).timestamp() * 1000))
    now_dt = epoch_ms_to_utc_dt(now_ms)

    history_entry = {
        "at": now_dt,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    current_history = record.get("history", [])

    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            f"history.{len(current_history)}": {"$exists": False},
        },
        {
            "$set": {
                "status": target_status,
                "punch_in": target_punch_in,
                "punch_out": target_punch_out,
                "work_hours": target_work_hours,
                "late_minutes": target_late_minutes,
                "overtime_minutes": target_overtime_minutes,
                "half_day": target_half_day,
            },
            "$push": {
                "history": history_entry,
            },
        },
        return_document=True,
    )

    if not updated:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Concurrent modification detected",
        )

    return format_attendance_record(updated)


# --------------------------------------------------------------------------- #
# Analytics Endpoints
# --------------------------------------------------------------------------- #
@app.get(
    "/analytics/employees/{emp_code}/monthly",
    response_model=EmployeeMonthly,
    tags=["analytics"],
)
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Employee not found",
        )

    working_days = calculate_working_days(emp["joined_on"], month)

    y, m = map(int, month.split("-"))
    last_day_str = (date(y, 12, 31) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)).isoformat()
    start_day_str = f"{month}-01"

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": start_day_str, "$lte": last_day_str},
            }
        },
        {
            "$project": {
                "date": 1,
                "status": 1,
                "late_minutes": {"$ifNull": ["$late_minutes", 0]},
                "overtime_minutes": {"$ifNull": ["$overtime_minutes", 0]},
                "half_day": {"$ifNull": ["$half_day", False]},
                "day_of_week": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$gte": ["$day_of_week", 2]},
                                    {"$lte": ["$day_of_week", 6]},
                                ]
                            },
                            {"$cond": ["$half_day", 0.5, 1.0]},
                            0.0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
    ]

    results = list(db.attendance_logs.aggregate(pipeline))
    if results:
        res = results[0]
        p_days = round_half_up(res.get("present_days", 0.0), 2)
        l_days = res.get("leave_days", 0)
        l_count = res.get("late_count", 0)
        tot_late = res.get("total_late_minutes", 0)
        tot_ot = res.get("total_overtime_minutes", 0)
    else:
        p_days = 0.0
        l_days = 0
        l_count = 0
        tot_late = 0
        tot_ot = 0

    if working_days > 0:
        pct = round_half_up((p_days / working_days) * 100, 2)
    else:
        pct = None

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": p_days,
        "leave_days": l_days,
        "late_count": l_count,
        "total_late_minutes": tot_late,
        "total_overtime_minutes": tot_ot,
        "attendance_pct": pct,
    }


@app.get(
    "/analytics/departments/summary",
    response_model=DepartmentSummary,
    tags=["analytics"],
)
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = Query(None),
):
    y, m = map(int, month.split("-"))
    last_day_str = (date(y, 12, 31) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)).isoformat()
    start_day_str = f"{month}-01"

    match_emp = {"joined_on": {"$lte": last_day_str}}
    if department is not None:
        match_emp["department"] = department

    pipeline = [
        {"$match": match_emp},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"emp": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$emp"]},
                                    {"$gte": ["$date", start_day_str]},
                                    {"$lte": ["$date", last_day_str]},
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": "$department",
                "employees_set": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ifNull": ["$logs", False]},
                                    {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {
                                        "$gte": [
                                            {"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}},
                                            2,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            {"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}},
                                            6,
                                        ]
                                    },
                                ]
                            },
                            {"$cond": ["$logs.half_day", 0.5, 1.0]},
                            0.0,
                        ]
                    }
                },
                "work_hours_list": {
                    "$push": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ifNull": ["$logs", False]},
                                    {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": ["$logs.work_hours", None]},
                                ]
                            },
                            "$logs.work_hours",
                            "$$REMOVE",
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$employees_set"},
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": [{"$size": "$work_hours_list"}, 0]},
                        {"$avg": "$work_hours_list"},
                        None,
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]

    raw_items = list(db.employees.aggregate(pipeline))
    items = []
    for item in raw_items:
        if item.get("headcount", 0) == 0:
            continue
        avg_wh = item.get("avg_work_hours")
        items.append({
            "department": item["department"],
            "headcount": item["headcount"],
            "present_days": round_half_up(item.get("present_days", 0.0), 2),
            "avg_work_hours": round_half_up(avg_wh, 2) if avg_wh is not None else None,
            "late_count": item.get("late_count", 0),
            "total_late_minutes": item.get("total_late_minutes", 0),
            "leave_count": item.get("leave_count", 0),
            "on_duty_count": item.get("on_duty_count", 0),
        })

    return {
        "month": month,
        "items": items,
    }


@app.get(
    "/analytics/leaderboard/late",
    response_model=Leaderboard,
    tags=["analytics"],
)
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = Query(None),
):
    y, m = map(int, month.split("-"))
    last_day_str = (date(y, 12, 31) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)).isoformat()
    start_day_str = f"{month}-01"

    pipeline = [
        {
            "$match": {
                "date": {"$gte": start_day_str, "$lte": last_day_str},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp",
            }
        },
        {"$unwind": "$emp"},
    ]

    if department is not None:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {
                        "$rank": {}
                    }
                },
            }
        },
        {
            "$match": {
                "rank": {"$lte": limit}
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$emp.name",
                "department": "$emp.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "emp_code": 1,
            }
        },
    ])

    items = list(db.attendance_logs.aggregate(pipeline))
    return {
        "month": month,
        "items": items,
    }


@app.get(
    "/analytics/departments/{department}/trend",
    response_model=Trend,
    tags=["analytics"],
)
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    to_date: str = Query(..., alias="to", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
):
    dept_exists = db.employees.find_one({"department": department})
    if not dept_exists:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Department not found",
        )

    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)
    if d_to < d_from:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="to date cannot be earlier than from date",
        )

    diff_days = (d_to - d_from).days + 1
    if diff_days > 92:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Date range cannot exceed 92 days",
        )

    pipeline = [
        {"$match": {"department": department}},
        {"$limit": 1},
        {
            "$project": {
                "days": {
                    "$map": {
                        "input": {"$range": [0, diff_days]},
                        "as": "d",
                        "in": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": {
                                    "$dateAdd": {
                                        "startDate": {"$dateFromString": {"dateString": from_date}},
                                        "unit": "day",
                                        "amount": "$$d",
                                    }
                                },
                            }
                        },
                    }
                }
            }
        },
        {"$unwind": "$days"},
        {
            "$project": {
                "_id": 0,
                "date": "$days",
                "day_of_week": {
                    "$dayOfWeek": {"$dateFromString": {"dateString": "$days"}}
                },
            }
        },
        {
            "$addFields": {
                "is_working_day": {
                    "$and": [
                        {"$gte": ["$day_of_week", 2]},
                        {"$lte": ["$day_of_week", 6]},
                    ]
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"day": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$day"]},
                                ]
                            }
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "hc",
            }
        },
        {
            "$addFields": {
                "headcount": {"$ifNull": [{"$first": "$hc.count"}, 0]},
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"day": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$date", "$$day"]}
                        }
                    },
                    {
                        "$lookup": {
                            "from": "employees",
                            "localField": "emp_code",
                            "foreignField": "emp_code",
                            "as": "emp",
                        }
                    },
                    {"$unwind": "$emp"},
                    {
                        "$match": {
                            "emp.department": department
                        }
                    },
                ],
                "as": "day_logs",
            }
        },
        {
            "$project": {
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$day_logs",
                                    "as": "l",
                                    "cond": {
                                        "$in": ["$$l.status", ["PRESENT", "WFH", "ON_DUTY"]]
                                    },
                                }
                            },
                            "as": "pl",
                            "in": {
                                "$cond": ["$$pl.half_day", 0.5, 1.0]
                            },
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$day_logs",
                            "as": "l",
                            "cond": {
                                "$gt": [{"$ifNull": ["$$l.late_minutes", 0]}, 0]
                            },
                        }
                    }
                },
            }
        },
        {
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {"$divide": ["$present_count", "$headcount"]},
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, 0]
                        }
                    }
                }
            }
        },
        {"$sort": {"date": 1}},
    ]

    raw_items = list(db.employees.aggregate(pipeline))
    formatted_items = []
    for item in raw_items:
        att_rate = item.get("attendance_rate")
        m_avg = item.get("moving_avg_7d")
        formatted_items.append({
            "date": item["date"],
            "is_working_day": item["is_working_day"],
            "headcount": item["headcount"],
            "present_count": round_half_up(item["present_count"], 2),
            "late_count": item["late_count"],
            "attendance_rate": round_half_up(att_rate, 4) if att_rate is not None else None,
            "moving_avg_7d": round_half_up(m_avg, 4) if m_avg is not None else None,
        })

    return {
        "department": department,
        "items": formatted_items,
    }


# --------------------------------------------------------------------------- #
# Admin Explain Endpoint
# --------------------------------------------------------------------------- #
class ExplainEndpointEnum(str, Enum):
    attendance_list = "attendance_list"
    employee_monthly = "employee_monthly"
    department_summary = "department_summary"
    late_leaderboard = "late_leaderboard"
    department_trend = "department_trend"


class ExplainResponse(BaseModel):
    endpoint: str
    collection: str
    explain: Dict[str, Any]


@app.get(
    "/admin/explain/{endpoint}",
    response_model=ExplainResponse,
    tags=["admin"],
)
def explain_endpoint(
    endpoint: ExplainEndpointEnum,
    emp_code: Optional[str] = Query(None),
    month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = Query(None),
    limit: Optional[int] = Query(10, ge=1, le=50),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    status: Optional[StatusEnum] = Query(None),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: Optional[int] = Query(1, ge=1),
    page_size: Optional[int] = Query(20, ge=1, le=100),
):
    if endpoint == ExplainEndpointEnum.attendance_list:
        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="date_from cannot be greater than date_to",
            )
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
            q["status"] = status.value

        p = page or 1
        ps = page_size or 20
        cmd = {
            "find": "attendance_logs",
            "filter": q,
            "sort": {"date": -1, "emp_code": 1},
            "skip": (p - 1) * ps,
            "limit": ps,
        }
        collection = "attendance_logs"

    elif endpoint == ExplainEndpointEnum.employee_monthly:
        if not emp_code or not month:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="emp_code and month are required",
            )
        y, m = map(int, month.split("-"))
        last_day_str = (date(y, 12, 31) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)).isoformat()
        start_day_str = f"{month}-01"

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {"$gte": start_day_str, "$lte": last_day_str},
                }
            },
            {
                "$project": {
                    "date": 1,
                    "status": 1,
                    "late_minutes": {"$ifNull": ["$late_minutes", 0]},
                    "overtime_minutes": {"$ifNull": ["$overtime_minutes", 0]},
                    "half_day": {"$ifNull": ["$half_day", False]},
                    "day_of_week": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                        {"$gte": ["$day_of_week", 2]},
                                        {"$lte": ["$day_of_week", 6]},
                                    ]
                                },
                                {"$cond": ["$half_day", 0.5, 1.0]},
                                0.0,
                            ]
                        }
                    },
                    "leave_days": {
                        "$sum": {
                            "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                        }
                    },
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "total_overtime_minutes": {"$sum": "$overtime_minutes"},
                }
            },
        ]
        cmd = {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }
        collection = "attendance_logs"

    elif endpoint == ExplainEndpointEnum.department_summary:
        if not month:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="month is required",
            )
        y, m = map(int, month.split("-"))
        last_day_str = (date(y, 12, 31) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)).isoformat()
        start_day_str = f"{month}-01"

        match_emp = {"joined_on": {"$lte": last_day_str}}
        if department is not None:
            match_emp["department"] = department

        pipeline = [
            {"$match": match_emp},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"emp": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$emp_code", "$$emp"]},
                                        {"$gte": ["$date", start_day_str]},
                                        {"$lte": ["$date", last_day_str]},
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "logs",
                }
            },
            {
                "$unwind": {
                    "path": "$logs",
                    "preserveNullAndEmptyArrays": True,
                }
            },
            {
                "$group": {
                    "_id": "$department",
                    "employees_set": {"$addToSet": "$emp_code"},
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$ifNull": ["$logs", False]},
                                        {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                        {
                                            "$gte": [
                                                {"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}},
                                                2,
                                            ]
                                        },
                                        {
                                            "$lte": [
                                                {"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}},
                                                6,
                                            ]
                                        },
                                    ]
                                },
                                {"$cond": ["$logs.half_day", 0.5, 1.0]},
                                0.0,
                            ]
                        }
                    },
                    "work_hours_list": {
                        "$push": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$ifNull": ["$logs", False]},
                                        {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                        {"$ne": ["$logs.work_hours", None]},
                                    ]
                                },
                                "$logs.work_hours",
                                "$$REMOVE",
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]},
                                1,
                                0,
                            ]
                        }
                    },
                    "total_late_minutes": {
                        "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                    },
                    "leave_count": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$logs.status", "LEAVE"]},
                                1,
                                0,
                            ]
                        }
                    },
                    "on_duty_count": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$logs.status", "ON_DUTY"]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "department": "$_id",
                    "headcount": {"$size": "$employees_set"},
                    "present_days": 1,
                    "avg_work_hours": {
                        "$cond": [
                            {"$gt": [{"$size": "$work_hours_list"}, 0]},
                            {"$avg": "$work_hours_list"},
                            None,
                        ]
                    },
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "leave_count": 1,
                    "on_duty_count": 1,
                }
            },
            {"$sort": {"department": 1}},
        ]
        cmd = {
            "aggregate": "employees",
            "pipeline": pipeline,
            "cursor": {},
        }
        collection = "employees"

    elif endpoint == ExplainEndpointEnum.late_leaderboard:
        if not month:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="month is required",
            )
        y, m = map(int, month.split("-"))
        last_day_str = (date(y, 12, 31) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)).isoformat()
        start_day_str = f"{month}-01"

        pipeline = [
            {
                "$match": {
                    "date": {"$gte": start_day_str, "$lte": last_day_str},
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {"$sum": 1},
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "emp",
                }
            },
            {"$unwind": "$emp"},
        ]

        if department is not None:
            pipeline.append({"$match": {"emp.department": department}})

        pipeline.extend([
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {
                        "rank": {
                            "$rank": {}
                        }
                    },
                }
            },
            {
                "$match": {
                    "rank": {"$lte": limit or 10}
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "rank": 1,
                    "emp_code": "$_id",
                    "name": "$emp.name",
                    "department": "$emp.department",
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
            {
                "$sort": {
                    "total_late_minutes": -1,
                    "emp_code": 1,
                }
            },
        ])
        cmd = {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }
        collection = "attendance_logs"

    elif endpoint == ExplainEndpointEnum.department_trend:
        if not department or not from_date or not to_date:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="department, from, and to are required",
            )
        d_from = date.fromisoformat(from_date)
        d_to = date.fromisoformat(to_date)
        if d_to < d_from:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="to date cannot be earlier than from date",
            )
        diff_days = (d_to - d_from).days + 1
        if diff_days > 92:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Date range cannot exceed 92 days",
            )

        pipeline = [
            {"$match": {"department": department}},
            {"$limit": 1},
            {
                "$project": {
                    "days": {
                        "$map": {
                            "input": {"$range": [0, diff_days]},
                            "as": "d",
                            "in": {
                                "$dateToString": {
                                    "format": "%Y-%m-%d",
                                    "date": {
                                        "$dateAdd": {
                                            "startDate": {"$dateFromString": {"dateString": from_date}},
                                            "unit": "day",
                                            "amount": "$$d",
                                        }
                                    },
                                }
                            },
                        }
                    }
                }
            },
            {"$unwind": "$days"},
            {
                "$project": {
                    "_id": 0,
                    "date": "$days",
                    "day_of_week": {
                        "$dayOfWeek": {"$dateFromString": {"dateString": "$days"}}
                    },
                }
            },
            {
                "$addFields": {
                    "is_working_day": {
                        "$and": [
                            {"$gte": ["$day_of_week", 2]},
                            {"$lte": ["$day_of_week", 6]},
                        ]
                    }
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "let": {"day": "$date"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$department", department]},
                                        {"$lte": ["$joined_on", "$$day"]},
                                    ]
                                }
                            }
                        },
                        {"$count": "count"},
                    ],
                    "as": "hc",
                }
            },
            {
                "$addFields": {
                    "headcount": {"$ifNull": [{"$first": "$hc.count"}, 0]},
                }
            },
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"day": "$date"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {"$eq": ["$date", "$$day"]}
                            }
                        },
                        {
                            "$lookup": {
                                "from": "employees",
                                "localField": "emp_code",
                                "foreignField": "emp_code",
                                "as": "emp",
                            }
                        },
                        {"$unwind": "$emp"},
                        {
                            "$match": {
                                "emp.department": department
                            }
                        },
                    ],
                    "as": "day_logs",
                }
            },
            {
                "$project": {
                    "date": 1,
                    "is_working_day": 1,
                    "headcount": 1,
                    "present_count": {
                        "$sum": {
                            "$map": {
                                "input": {
                                    "$filter": {
                                        "input": "$day_logs",
                                        "as": "l",
                                        "cond": {
                                            "$in": ["$$l.status", ["PRESENT", "WFH", "ON_DUTY"]]
                                        },
                                    }
                                },
                                "as": "pl",
                                "in": {
                                    "$cond": ["$$pl.half_day", 0.5, 1.0]
                                },
                            }
                        }
                    },
                    "late_count": {
                        "$size": {
                            "$filter": {
                                "input": "$day_logs",
                                "as": "l",
                                "cond": {
                                    "$gt": [{"$ifNull": ["$$l.late_minutes", 0]}, 0]
                                },
                            }
                        }
                    },
                }
            },
            {
                "$addFields": {
                    "attendance_rate": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_working_day",
                                    {"$gt": ["$headcount", 0]},
                                ]
                            },
                            {"$divide": ["$present_count", "$headcount"]},
                            None,
                        ]
                    }
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"date": 1},
                    "output": {
                        "moving_avg_7d": {
                            "$avg": "$attendance_rate",
                            "window": {
                                "documents": [-6, 0]
                            }
                        }
                    }
                }
            },
            {"$sort": {"date": 1}},
        ]
        cmd = {
            "aggregate": "employees",
            "pipeline": pipeline,
            "cursor": {},
        }
        collection = "employees"

    raw_explain = db.command("explain", cmd, verbosity="executionStats")
    clean_explain = json.loads(json_util.dumps(raw_explain))
    return {
        "endpoint": endpoint.value,
        "collection": collection,
        "explain": clean_explain,
    }



