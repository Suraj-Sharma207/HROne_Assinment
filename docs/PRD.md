# Product Requirements Document

## 1. Product Overview

This product is an employee attendance and analytics service for an HR operations team. It supports the management of employee records, daily punch-in and punch-out tracking, manual corrections to attendance records, and operational analytics that help managers understand attendance trends, late arrivals, department performance, and headcount coverage.

The system is built as a FastAPI service backed by MongoDB. It is designed to process attendance data reliably, support concurrent updates safely, and provide fast reporting at scale.

## 2. Product Goal

The goal is to provide a dependable attendance system that does the following:

- Records employee work schedules and employment details
- Tracks attendance events for each workday
- Computes derived values such as late minutes, overtime, work hours, and half-day status
- Supports manual corrections with an audit trail
- Produces monthly and trend-based analytics for employees and departments
- Helps operations teams identify issues such as lateness, attendance drift, and department trends

## 3. Problem Statement

HR and operations teams need a single source of truth for attendance data. The system must combine employee metadata, daily attendance logs, and business rules around work schedules and attendance calculations. It must also support operational reporting on late arrivals, overtime, department trends, and monthly attendance rates.

The product must be accurate under business rules that are more detailed than simple punch times. Examples include:

- IST-based shift logic and overnight shifts
- Different treatment for weekend and weekday records
- Grace-period handling for late punch-ins
- Half-day and overtime calculation rules
- Department and employee-level analytics that include employees with no attendance logs
- Correct handling of edge cases such as ties, mid-month joiners, and missing weekdays

## 4. Users and Personas

### HR Administrator
- Creates and maintains employee master data
- Reviews attendance correction activity
- Validates data quality for monthly reporting and policy compliance

### Operations Manager
- Monitors lateness and attendance patterns by department
- Reviews trends and department performance over a time range
- Uses reporting to make staffing and scheduling decisions

### Support / Back-office User
- Corrects inaccurate punch records
- Logs reason and person responsible for each correction
- Ensures historical audit trail remains intact

## 5. Core Use Cases

### 5.1 Employee onboarding
- Add a new employee with their employee code, personal details, department, shift schedule, and join date
- Ensure the employee code is unique and immutable
- Persist the employee record in the system

### 5.2 Attendance capture
- Record a punch-in event for an employee on a specific attendance date
- Compute late status based on shift start and grace period rules
- Initialize attendance fields for the day as required
- Record a punch-out event for the most recent relevant shift
- Compute work hours, overtime, and half-day indicators

### 5.3 Manual regularization
- Correct an attendance record for a date when the punch-in or punch-out data is inaccurate
- Recompute derived values after the update
- Append a history entry that records when the correction happened, who made it, why it was made, and exactly which fields changed
- Ensuring previous history entries are preserved and not overwritten

### 5.4 Analytics and reporting
- Generate monthly employee summaries
- Generate department summaries for a month
- Rank employees by late minutes with tie-aware ranking
- Produce a daily department trend over a requested date range including gaps where no records exist

### 5.5 Debugging and operational visibility
- Provide an explain endpoint that returns MongoDB execution details for the main query or aggregation used by the service
- Confirm that the production patterns use indexes and avoid inefficient collection scans

## 6. Functional Requirements

### 6.1 Employee management
- The system must support employee creation using an employee code that is unique and customer-generated
- Employee records must store:
  - employee code
  - name
  - email
  - department
  - shift start time
  - shift end time
  - join date
  - creation timestamp
- The employee code is the natural identity used across the API and is not the MongoDB internal object ID
- Duplicate employee creation must fail with a conflict response

### 6.2 Attendance record lifecycle
- A daily record is identified by employee code and attendance date
- Each attendance record may be in one of the allowed statuses:
  - PRESENT
  - WFH
  - ON_DUTY
  - ABSENT
  - LEAVE
- A punch-in is used to create a record for the day when the employee is present
- Punch-in is rejected when the same employee and date already has a record within the same concurrent request set
- Punch-out closes the employee’s most recent relevant record, including overnight-shift scenarios
- Derived values must be stored and returned with the record

### 6.3 Business rules and calculations
- Shift times and attendance dates are treated as IST-based values
- Overnight shifts are recognized when the shift end time is less than or equal to the shift start time
- A punch-in can belong to the previous day’s shift when appropriate under overnight logic
- Late calculation uses a strict grace period and floors elapsed minutes since shift start
- Overtime is calculated only when a minimum threshold is met
- Work hours are calculated from punch-in to punch-out and rounded using half-up rules
- Half-day status is determined from the rounded work hours after punch-out
- Presence classification determines whether the record contributes to attendance totals

### 6.4 Corrections and history
- Manual corrections are allowed for one attendance record at a time
- Only the relevant fields may be updated
- After an update, the system recalculates all derived fields from the final times
- A new history entry is appended with before-and-after values for each changed field
- History entries are immutable and preserved across future corrections

### 6.5 Analytics requirements
- Monthly employee summary must include working days, present days, leave days, late count, late total, overtime total, and attendance percentage
- Department summary must include:
  - department name
  - headcount using joined_on rules
  - present-day totals
  - average work hours for logged records
  - late totals
  - leave counts
  - on-duty counts
- Leaderboard must show employees ranked by total late minutes, with a tie-aware ranking system
- Department trend must show daily attendance and moving averages across a date range, including weekends and gaps with no records

### 6.6 Query and pagination behavior
- List endpoints support filtering and pagination
- Pagination must start at 1 and use the configured page size with an upper limit
- total must reflect the filtered result set, not the entire collection
- Sorting rules must match business requirements and remain efficient on large datasets

### 6.7 Explainability and operational validation
- The system must expose an endpoint that runs MongoDB explain on the main query or aggregation used by a specified endpoint
- The output must allow validation that the query uses indexes and does not degrade to full collection scans

## 7. Non-Functional Requirements

### 7.1 Reliability
- The service must return correct status codes for success, not found, validation issues, and conflicts
- It must behave correctly under concurrent requests
- It must avoid silent data loss during simultaneous corrections

### 7.2 Correctness
- Calculations must align with the business rules documented in the contract
- Hidden datasets include edge cases such as ties, missing records, overnight shifts, weekend logs, mid-month joiners, and employees with zero logs
- A plausible-looking answer is not enough; outputs must match the expected logic precisely

### 7.3 Performance
- The service must scale to roughly 100,000 attendance records without relying on full collection scans for list and analytics endpoints
- Required indexes must be created at startup in an idempotent way
- Queries and aggregations must be optimized for large data volumes

### 7.4 Security and data handling
- No secrets or credentials are committed to the repository
- Environment variables must be used for database connectivity
- The API accepts client input from business data only and validates it before processing

## 8. Business Rules Summary

The system must obey the following core interpretation rules:

- Calendar dates are treated as IST dates, not as UTC dates
- Attendance records are associated with the employee and the attendance date, not with MongoDB document IDs
- Derived values are authoritative once stored by the API
- Weekend records are still counted in late and overtime totals, though not in weekday present-day totals
- Employees who joined after the period start still count toward headcount if they joined on or before the period end
- Employees with no logs must still be included in department headcount when eligible
- A record with no log is different from an ABSENT record
- History tracks only manual corrections and not original punch events

## 9. Data Model Requirements

The product relies on two primary collections:

- Employees
  - employee code
  - name
  - email
  - department
  - shift start
  - shift end
  - join date
  - created timestamp

- Attendance logs
  - employee code
  - attendance date
  - status
  - punch-in time
  - punch-out time
  - work hours
  - late minutes
  - overtime minutes
  - half-day indicator
  - history array

The model must preserve the distinction between:
- field values stored in MongoDB as BSON datetimes and API values exposed as epoch millisecond integers
- date and time strings stored as plain strings in ISO-like formats
- derived fields that are computed and stored, versus user-supplied input that must be ignored or rejected

## 10. Edge Cases and Risk Areas

The following cases are critical to product correctness:

- Employees without any attendance logs
- Department headcount calculations when there are gap days or no records at all
- Ties in late-minute leaderboard rankings
- Overnight shifts and cross-midnight attendance behavior
- Weekend records affecting totals without counting toward weekday present-day metrics
- Mid-month employees who joined partway through the reporting period
- Concurrent duplicate punch-in or punch-out operations
- Simultaneous corrections that must preserve the history correctly

## 11. Functional Acceptance Criteria

The product is successful when all of the following are true:

- Employee creation and attendance event recording work as expected
- Duplicate employee codes are rejected correctly
- Attendance rules match the defined business logic and edge cases
- Manual corrections produce valid audit trails without data loss
- Attendance summaries and department analytics reflect the policy rules exactly
- Leaderboard ranking handles ties correctly and applies the limit after ranking
- Department trend includes both working and non-working days, with proper gap filling and moving averages
- Explain output confirms the use of indexes on main queries and aggregations
- Performance is acceptable for a dataset around 100,000 attendance records

## 12. Success Metrics

- Correctness of attendance calculations across edge cases
- Correct status codes for all API responses
- Correct analytics values on hidden datasets
- Reliable handling of concurrent requests
- Efficient query plans with index usage
- Visible, reproducible audit trail for all corrections

## 13. Scope

### In Scope
- Employee management
- Punch-in and punch-out attendance tracking
- Correction and audit-history support
- Monthly and department-level analytics
- Leaderboard for late employees
- Daily trend analysis
- Query explain diagnostics

### Out of Scope
- Authentication and authorization
- User login sessions or role-based access control
- Frontend interface or dashboard UI
- External payroll integrations
- Mobile applications
- Holiday calendars beyond the defined business rules

## 14. Definition of Done

The product is considered complete when:

- The API contract is followed exactly
- Time and calculation rules are implemented correctly
- Analytics outputs match expected results across hidden data cases
- Concurrency and correction race conditions are handled correctly
- Indexes and explain plans demonstrate efficient database usage
- Documentation is clear and reflects the actual product behavior

## 15. Summary

This project is a focused but operationally important internal attendance API for HR and department management. The product must be correct, concurrency-safe, and analytics-driven. It must follow precise business logic, retain detailed history for corrections, and support efficient reporting on large datasets without sacrificing accuracy.
