# Employee Attendance & Analytics API — Comprehensive Audit & Implementation Blueprint

## 1. Executive Summary & Assignment Context

* **Project:** Employee Attendance & Analytics Service (FastAPI + MongoDB 6.0/7.0).
* **Source Specification:** HROne Software Engineer Hiring Assignment — Contract v2.0 (`PROBLEM_STATEMENT.docx`, `openapi.yaml`, `DATA_MODEL.md`, `PRD.md`).
* **Core Mandate:** Build a dependable attendance backend tracking punch logs, computing shift metrics under strict IST business rules, maintaining immutable audit trails, and executing high-performance analytical aggregations on collections scaling up to 100,000+ records.
* **Architecture Constraint:** All application code must reside inside a single file: `app/main.py`. The server must boot cleanly via `uvicorn app.main:app --port 8000`.
* **Deliverables:**
  1. `app/main.py`: Complete production-grade application code.
  2. `requirements.txt`: Locked dependencies.
  3. `REVIEW.md`: Line-by-line audit of starter defects.
  4. `DECISIONS.md`: Design rationale (150–200 words addressing 5 architectural questions).
  5. `README.md`: Startup instructions and technical notes.

### Evaluation Breakdown

| Area | Weight | Verification Method |
| :--- | :---: | :--- |
| **Review (Part A)** | 10% | `REVIEW.md` completeness & automated verification of fixes |
| **CRUD, Rules & Concurrency** | 30% | Automated test suite validating status codes, R1–R5, pagination, and parallel races |
| **Analytics Correctness** | 30% | Hidden dataset benchmarks (ties, 0-log staff, mid-month joiners, overnight shifts) |
| **MongoDB Usage & Speed** | 15% | `/admin/explain` validation: `IXSCAN` plans without `COLLSCAN` on ~100k records |
| **Decisions & Code Quality** | 15% | `DECISIONS.md`, clean single-file structure, robust input validation |

---

## 2. Data Model & Conventions

### 2.1 Entity Identifiers
* **Employees (`employees` collection):** Natural identifier is `emp_code` (e.g., `EMP0001`, regex: `^EMP\d{4,6}$`). Unique, immutable, client-specified. MongoDB `_id` is strictly internal and must never be exposed or accepted.
* **Attendance Records (`attendance_logs` collection):** Addressed by composite key `(emp_code, date)`. No synthetic ID. At most one document per `(emp_code, date)`.

### 2.2 Time & Calendar Representations
* **Instants (Points in Time):**
  * **API Boundary:** Integer count of milliseconds since Unix epoch (`1970-01-01T00:00:00Z`).
  * **Validation:** Accepted range `[100000000000, 4102444800000]`. Values below 10^11 (seconds-like), floats, or strings are rejected with `422`.
  * **MongoDB Boundary:** Stored as UTC BSON datetimes (`datetime` with UTC tzinfo).
  * **Truncation Rule (R1):** Instants are truncated to whole seconds (`(ms // 1000) * 1000`) before calculations and persistence.
* **Calendar Values:**
  * Dates: Plain strings `"YYYY-MM-DD"` representing IST calendar dates.
  * Months: Plain strings `"YYYY-MM"`.
  * Shift Times: Plain strings `"HH:MM"` (24-hour, IST).

### 2.3 Status Values & Semantics

| Status | Meaning | Punch Times Required | Counts as Present (R6)? | Creation Source |
| :--- | :--- | :--- | :---: | :--- |
| `PRESENT` | Worked on site | `punch_in` required | Yes | `POST /attendance/punch-in` |
| `WFH` | Worked from home | `punch_in` required | Yes | `POST /attendance/punch-in` |
| `ON_DUTY` | Official duty off-site | `punch_in` required | Yes | `POST /attendance/punch-in` |
| `ABSENT` | Absent without leave | Both `null` | No | Seeded data / manual correction |
| `LEAVE` | Approved leave | Both `null` | No | Seeded data / manual correction |

*Note: No API endpoint creates `ABSENT` or `LEAVE` directly. An unrecorded workday is distinct from an `ABSENT` record.*

### 2.4 Authoritative Derived Fields
The API alone writes `work_hours`, `late_minutes`, `overtime_minutes`, and `half_day`. Analytics queries must read stored values rather than recomputing them from punch times.

| Scenario | `work_hours` | `late_minutes` | `overtime_minutes` | `half_day` |
| :--- | :---: | :---: | :---: | :---: |
| Open record (punched in, no punch out) | `null` | Calculated (R2) | `0` | `false` |
| Closed record (punch out present) | Calculated (R4) | Calculated (R2) | Calculated (R3) | Calculated (R5) |
| `ABSENT` or `LEAVE` | `null` | `0` | `0` | `false` |

### 2.5 History (Audit Trail)
* The `history` array tracks **manual regularizations only** (`PATCH`).
* Standard punches never write to `history`.
* Each correction appends an immutable object: `{ at, by, reason, changes }`.
* `changes` records only fields whose values genuinely differed: `{"from": ..., "to": ...}`.
* In MongoDB, nested punch timestamps inside `changes` are BSON datetimes; in API JSON, they are epoch milliseconds.

---

## 3. The 10 Core Business Rules (R1 – R10)

1. **R1 — Time Zone & Attendance Date:**
   * Reference timezone is Indian Standard Time (IST, UTC+05:30).
   * Attendance `date` is the IST calendar date of `punch_in`.
   * **Overnight Shifts (`shift_end <= shift_start`, e.g., 22:00–06:00):** A punch-in whose IST time of day is strictly *earlier than* `shift_end` belongs to the shift that started on the previous calendar day. Shift end falls on the next calendar day.
   * All instants must be truncated to whole seconds before calculations and persistence.
2. **R2 — Late Calculation:**
   * Strict 10-minute grace period.
   * Punch-in is late only if strictly more than 10 minutes past `shift_start` (`punch_in > shift_start + 10m`).
   * When late: `late_minutes = floor((punch_in - shift_start) in seconds / 60)` elapsed from `shift_start` (not from grace end). Otherwise `0`.
   * *Examples (09:30 Shift):* `09:40:00` $\rightarrow$ 0 min; `09:40:01` $\rightarrow$ 10 min; `10:15:59` $\rightarrow$ 45 min.
3. **R3 — Overtime:**
   * `overtime_minutes = floor((punch_out - shift_end) in seconds / 60)`.
   * Counted **only if $\ge 30$ minutes**; otherwise `0`.
4. **R4 — Work Hours:**
   * `(punch_out - punch_in).total_seconds() / 3600.0`.
   * Rounded to 2 decimal places **half-up** (e.g., using `decimal.Decimal` with `ROUND_HALF_UP`). `null` until punch-out.
5. **R5 — Half Day:**
   * After punch-out, `half_day = true` when rounded `work_hours < 4.50`.
   * A half day contributes `0.5` to present day totals.
6. **R6 — Presence Classification:**
   * `PRESENT`, `WFH`, and `ON_DUTY` count as present. `ABSENT` and `LEAVE` do not.
7. **R7 — Working Days:**
   * Monday through Friday only (no holiday calendar).
   * For mid-month joiners, working days in a month start counting from `joined_on`.
   * `present_days` counts only Mon–Fri records. Weekend records still add to `late_minutes` and `overtime_minutes` totals.
8. **R8 — Number Rounding:**
   * General metrics (hours, averages, percentages): 2 decimals half-up.
   * Rates (`attendance_rate`, `moving_avg_7d`): 4 decimals half-up.
9. **R9 — Headcount:**
   * Employees with `joined_on <= period_end` (end of month, or daily trend date), whether or not they have logs.
10. **R10 — Pagination:**
    * 1-indexed (`page >= 1`).
    * Default `page_size = 20`, maximum `100`.
    * `total` must reflect the count of filtered items, not total collection documents.

---

## 4. Starter Code Defect Audit (`app/main.py`)

| # | Location | Defect Description | Repro / Impact | Planned Fix |
| :-: | :--- | :--- | :--- | :--- |
| **1** | `GET /health` (L72–74) | Returns static `{"status": "ok"}` without pinging MongoDB. | If MongoDB is unreachable, returns 200 instead of 503. | Execute `client.admin.command('ping')`; return 503 on exception. |
| **2** | Global / Startup | Zero index creation at startup. | Concurrency violations, duplicate documents, and `COLLSCAN` on analytics queries. | Implement FastAPI `@asynccontextmanager` lifespan creating unique & query indexes idempotently. |
| **3** | `POST /employees` (L79–85) | Check-then-insert race condition (`find_one` followed by `insert_one`). | Simultaneous POSTs with duplicate `emp_code` both succeed, corrupting data. | Enforce database unique index on `emp_code`; catch `DuplicateKeyError` and return 409. |
| **4** | `POST /employees` (L53–61) | Missing schema validations: `shift_start == shift_end`, `emp_code` pattern regex. | Client can create employee with `shift_start == shift_end` (forbidden by contract). | Add Pydantic model validator verifying `shift_start != shift_end` and `^EMP\d{4,6}$`. |
| **5** | `POST /employees` (L82–85) | `created_at` stored as local naive datetime and returned as unformatted string. | Fails schema contract requiring epoch milliseconds integer. | Store UTC BSON datetime; serialize response to integer epoch milliseconds. |
| **6** | `GET /employees` (L93) | Off-by-one pagination bug: `skip = page * page_size`. | Requesting `page=1` skips 20 records; the first page of employees is invisible. | Correct offset calculation: `skip = (page - 1) * page_size`. |
| **7** | `GET /employees` (L94) | `total` counts whole collection unconditionally (`count_documents({})`). | Filtering by `?department=Sales` returns total count of all company employees. | Pass filter query dictionary: `count_documents(q)`. |
| **8** | `GET /employees` (L95) | Missing sort order. | Fails specification requiring records sorted by `emp_code` ascending. | Add `.sort("emp_code", 1)` to cursor. |
| **9** | `POST /attendance/punch-in` (L101) | Does not verify employee existence. | Unknown `emp_code` causes 500 error (`KeyError: 'shift_start'`). | If `emp` is None, raise `HTTPException(404, "Employee not found")`. |
| **10** | `POST /attendance/punch-in` (L104–105) | Check-then-insert race condition for same employee and date. | Parallel punch-in requests create duplicate logs. | Create compound unique index `{"emp_code": 1, "date": 1}`; catch `DuplicateKeyError` -> 409. |
| **11** | `POST /attendance/punch-in` (L102–103) | `datetime.fromtimestamp(punched_at/1000)` ignores IST and overnight shift rules. | Punches before `shift_end` on overnight shifts are misassigned to current date. | Parse instant to IST, truncate ms, apply overnight check against `shift_end` (R1). |
| **12** | `compute_late_minutes` (L33–38) | Truncates fractional minutes before comparison: `minutes = int(secs/60); return minutes if minutes > 10 else 0`. | At `09:40:01` (shift `09:30`), elapsed is 601s (10.016 min). `int(10.016) == 10`, fails `> 10`, returning 0 instead of 10! | Compare elapsed seconds directly: `if total_seconds > 600: return int(total_seconds // 60)`. |
| **13** | `compute_late_minutes` (L35) | `start = punch_in.replace(hour=h, minute=m)`. | For overnight punch-in belonging to previous day, references the wrong calendar date. | Calculate shift start datetime using the record's resolved attendance date. |
| **14** | `compute_work_hours` (L40–41) | Uses Python built-in `round()`, implementing banker's rounding (round-to-even). | Half-way numbers (e.g. 8.125) round to 8.12 instead of 8.13, violating R4 half-up rule. | Use `Decimal` with `ROUND_HALF_UP` to round to 2 decimals. |
| **15** | `compute_overtime` (L44–48) | Missing 30-minute threshold and overnight end date calculation. | Overtime of 15 min incorrectly counted; overnight shifts calculate negative end time. | Resolve shift end relative to shift start; return 0 if elapsed minutes < 30 (R3). |
| **16** | `punch-in` & `list_attendance` | Injects `doc["id"] = str(res.inserted_id)`. | Contract strictly forbids `id` and `_id` in responses; requires epoch ms instants. | Strip `_id`, exclude `id`, map datetimes to epoch milliseconds in output schema. |
| **17** | `GET /attendance` (L144–147) | Loads entire database into memory: `docs = list(db.attendance_logs.find(q))`. | At 100,000 documents, causes severe memory exhaustion, latency spikes, and timeouts. | Execute `.sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size)` in MongoDB. |
| **18** | `GET /attendance` (L125–132) | Missing date range validation (`date_from > date_to`). | Inverted date query returns empty array instead of required 422 validation error. | Validate `if date_from and date_to and date_from > date_to: raise HTTPException(422)`. |

---

## 5. Endpoints Implementation Specification

### 5.1 System Endpoints
* **`GET /health`:**
  * Action: Ping MongoDB server admin database.
  * Status: `200 {"status": "ok"}` on success, `503` on error.

### 5.2 Employee Endpoints
* **`POST /employees`:**
  * Validation: `emp_code` regex `^EMP\d{4,6}$`, valid email, `shift_start != shift_end`.
  * Persistence: Insert with UTC `created_at`.
  * Return: `201` with `Employee` schema (`created_at` as epoch ms). Duplicate `emp_code` yields `409`.
* **`GET /employees`:**
  * Filters: Optional exact `department`.
  * Sort: `emp_code` ascending.
  * Pagination: `(page - 1) * page_size`, total reflects filtered count.

### 5.3 Attendance Lifecycle Endpoints
* **`POST /attendance/punch-in`:**
  * Input: `emp_code`, optional `punched_at` (epoch ms), `status` (default `PRESENT`, allowed: `PRESENT`, `WFH`, `ON_DUTY`).
  * Validations: Employee must exist (404); epoch range check (422); duplicate for date (409).
  * Date Determination: Truncate ms to whole seconds. If overnight shift and time < `shift_end`, assign previous calendar date.
  * Initialize: `punch_out = null`, `work_hours = null`, `overtime_minutes = 0`, `half_day = false`, `history = []`.
* **`POST /attendance/punch-out`:**
  * Input: `emp_code`, optional `punched_at` (epoch ms, defaults to now).
  * Validations:
    * Employee exists (404).
    * Locate most recent attendance record where `punch_in <= punched_at` (404 if none).
    * Record must not already be punched out (409 if `punch_out` is not null).
    * `punched_at > punch_in` and `punched_at <= punch_in + 24 hours` (422 if violated).
  * Atomic Update: Utilize `find_one_and_update({"_id": doc["_id"], "punch_out": None}, ...)` to prevent double-punch race conditions.
  * Compute: `work_hours` (R4), `overtime_minutes` (R3), `half_day` (R5).
* **`GET /attendance`:**
  * Filters: `emp_code`, `date_from`, `date_to`, `status`.
  * Validation: `date_from > date_to` $\rightarrow$ 422.
  * Sort: `date` DESCENDING, then `emp_code` ASCENDING.
* **`PATCH /attendance/{emp_code}/{date}` (Regularization):**
  * Target: Natural key `(emp_code, date)`.
  * Validation:
    * Record exists (404).
    * Setting `ABSENT` or `LEAVE` clears punches; passing punch times with them $\rightarrow$ 422.
    * Presence status requires `punch_in` (existing or provided) $\rightarrow$ 422.
    * New `punch_in` must preserve attendance date under R1 $\rightarrow$ 422.
    * `punch_out` must be strictly after `punch_in` and $\le 24$ hours $\rightarrow$ 422.
    * No field changed $\rightarrow$ 422.
  * Derived Fields: Recomputed from final times.
  * History: Atomically append `{at, by, reason, changes}` capturing only changed fields.

---

## 6. The Three Complex Analytics Pipelines

### 6.1 Department Summary (`GET /analytics/departments/summary`)
* **Challenge:** Count employees who have **zero attendance logs** in headcount, but exclude employees joining after the target month. Compute record-level average work hours.
* **Pipeline Strategy:**
  1. Base aggregation starts on the `employees` collection.
  2. `$match`: Filter `joined_on <= month_end`.
  3. Optional `$match`: Exact `department` if query parameter supplied.
  4. `$lookup`: Join with `attendance_logs` matching `emp_code` and `date` matching `^YYYY-MM`.
  5. `$unwind`: Unwind logs with `preserveNullAndEmptyArrays: true`.
  6. `$group`: Group by `department`:
     * `headcount`: `$addToSet: "$emp_code"` $\rightarrow$ `$size`.
     * `present_days`: Sum condition (Mon–Fri records with presence status: full day = 1.0, half day = 0.5).
     * `avg_work_hours`: Average over records with presence status and non-null `work_hours`.
     * `late_count`, `total_late_minutes`, `leave_count`, `on_duty_count`.
  7. Filter out departments with `headcount == 0`.
  8. `$sort`: `department` ascending.

### 6.2 Late Leaderboard (`GET /analytics/leaderboard/late`)
* **Challenge:** Standard competition ranking (`1, 2, 2, 4`), post-ranking `limit` filter (ties at cutoff both returned), secondary sort by `emp_code` ascending.
* **Pipeline Strategy:**
  1. `$match` on `attendance_logs`: `date` in month, `late_minutes > 0`.
  2. `$group` by `emp_code`:
     * `total_late_minutes`: `$sum: "$late_minutes"`.
     * `late_count`: `$sum: 1`.
  3. `$lookup` on `employees`: Fetch `name` and `department`. Unwind.
  4. If `department` parameter provided, filter matching department.
  5. `$setWindowFields`:
     * Partition: `null` (or by department).
     * Sort: `total_late_minutes: -1`.
     * Window function: `rank: { $rank: {} }` (produces 1, 2, 2, 4).
  6. `$match`: `rank <= limit`.
  7. `$sort`: `total_late_minutes: -1, emp_code: 1`.

### 6.3 Department Trend (`GET /analytics/departments/{department}/trend`)
* **Challenge:** Exact daily series across `from` to `to` (max 92 days) with **no Python loops**. Gap-filling for weekends and zero-record days. 7-day moving average bounded by the range start.
* **Pipeline Strategy:**
  1. Verify department exists in `employees` (return 404 if absent).
  2. Generate date range documents via MongoDB `$densify` or an embedded range sequence stage.
  3. Join department logs for each day.
  4. Compute daily `headcount` (`joined_on <= current_day`), `present_count`, `late_count`.
  5. Compute `attendance_rate`: `present_count / headcount` (Mon–Fri only with headcount > 0; else `null`).
  6. Use `$setWindowFields` with `documents: [-6, 0]` window to compute `moving_avg_7d` over non-null rates.
  7. Round rates to 4 decimal places half-up.

---

## 7. Indexing Strategy & Admin Explain (`GET /admin/explain/{endpoint}`)

### 7.1 Required Database Indexes

```python
# employees collection
db.employees.create_index([("emp_code", 1)], unique=True)
db.employees.create_index([("department", 1), ("joined_on", 1)])

# attendance_logs collection
db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)])
db.attendance_logs.create_index([("date", 1), ("status", 1)])
```

### 7.2 Index Plan Justification
* `employees.emp_code` (Unique): Enforces natural identity and guarantees race-safe employee creation.
* `attendance_logs.(emp_code, date)` (Unique): Prevents double punch-in races and serves natural key lookups for regularization.
* `attendance_logs.(date, emp_code)`: Backs `GET /attendance` with index prefix matching and index-assisted sorting without memory allocation.
* `attendance_logs.(emp_code, punch_in)`: Provides instant lookup for open punches in `POST /attendance/punch-out`.
* `attendance_logs.(date, status)`: Accelerates monthly and date-range aggregations (`department_summary`, `trend`, `leaderboard`).

### 7.3 `/admin/explain/{endpoint}` Endpoint
* Implements execution stats retrieval: `db.command("explain", pipeline_or_query, verbosity="executionStats")`.
* Validates that all critical read operations resolve via `IXSCAN` and contain zero `COLLSCAN` operations at 100,000 documents.

---

## 8. Architectural Blueprint for `DECISIONS.md`

1. **Indexes:**
   Created compound unique `(emp_code, date)` and `emp_code` for conflict guarantees. Created `(date, emp_code)` and `(emp_code, punch_in)` for fast query sorting and punch-out lookups. Considered indexing `history.at` but rejected because history is accessed solely within single records.
2. **Punch-in Race:**
   Simultaneous requests attempt `insert_one`. The database unique index on `(emp_code, date)` allows exactly one document to commit while rejecting the second with `DuplicateKeyError`. The exception handler intercepts the error and returns HTTP 409 without check-then-insert flaws.
3. **Ties:**
   Leaderboard utilizes standard competition ranking (`$rank: {}`), allocating tied employees identical rank numbers and skipping subsequent numbers (1, 2, 2, 4). The `limit` condition is evaluated against `rank <= limit`, ensuring all tied candidates at the cutoff are included, ordered by `emp_code` ascending.
4. **Headcount:**
   Aggregations initiate from the `employees` collection filtered by `joined_on <= period_end` rather than from `attendance_logs`. A left outer join (`$lookup` with null preservation) ensures employees with zero recorded logs remain present in the group stage.
5. **Scaling to 100x Data:**
   At 10,000,000+ records, shift from on-the-fly aggregations to pre-aggregated daily/monthly rollup tables updated on punch-out/regularization, combined with collection sharding on `(department, emp_code)`.

---

## 9. Verification & Submission Checklist

* [ ] `app/main.py` starts from clean clone via `uvicorn app.main:app --port 8000`.
* [ ] All indexes initialized automatically in lifespan startup.
* [ ] Environment variables `MONGO_URI` and `MONGO_DB` read with fallback to `.env`.
* [ ] No secrets, `.env`, Dockerfile, or virtual environments tracked in Git.
* [ ] All 10 business rules (R1–R10) strictly verified with custom test cases.
* [ ] `REVIEW.md` and `DECISIONS.md` completed.
