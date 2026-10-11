# Employee Attendance & Analytics API

## How to run
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Configure environment variables in `.env` (or shell environment):
   - `MONGO_URI`: MongoDB connection string
   - `MONGO_DB`: Database name (e.g. `attendance_db`)
3. Run the application:
   ```bash
   uvicorn app.main:app --port 8000
   ```
   Interactive OpenAPI docs are available at `http://localhost:8000/docs`.

## Anything we should know
- **Single-file implementation:** All API endpoints, Pydantic models, and business logic (R1–R10) reside in `app/main.py`.
- **Database indexes:** Required unique and compound indexes are created idempotently on startup via FastAPI lifespan context on `employees` and `attendance_logs`.
- **Concurrency & Races:** Handled via MongoDB atomic updates and unique constraints, catching `DuplicateKeyError` to return `409 Conflict`.
- **Audit trail:** Manual regularizations (`PATCH`) maintain an immutable append-only audit trail in `history`.
