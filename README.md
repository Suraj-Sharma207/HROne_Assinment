# Employee Attendance & Analytics API (HROne Contract v2.0)

A high-performance Employee Attendance and Analytics API built with FastAPI and MongoDB Atlas. Complies strictly with OpenAPI specification contracts and business rules (R1–R10).

## Tech Stack
- **Framework:** FastAPI / Pydantic v2
- **Database:** MongoDB Atlas (MongoDB 6.0+)
- **Driver:** PyMongo 4.18+

## Setup & Running Locally

1. **Clone and create a virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment:**
   Copy `.env.example` to `.env` and provide your MongoDB connection string:
   ```bash
   cp .env.example .env
   ```
   ```ini
   MONGO_URI=mongodb+srv://<username>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
   MONGO_DB=attendance_db
   ```

4. **Seed Sample Data (Optional):**
   ```bash
   python sample_seed.py
   ```

5. **Start the API server:**
   ```bash
   uvicorn app.main:app --port 8000
   ```
   Interactive OpenAPI docs are available at `http://localhost:8000/docs`.

## Architecture & Deliverables
- `app/main.py`: Complete API implementation, business logic engine, lifespan indexing, and explain queries.
- `REVIEW.md`: Detailed defect analysis and fixes identified in the starter codebase.
- `DECISIONS.md`: Architectural decisions regarding database indexing, race conditions, leaderboard ties, zero-log headcounts, and scaling.
- `requirements.txt`: Python package dependencies.
