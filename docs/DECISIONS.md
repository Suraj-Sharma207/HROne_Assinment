# DECISIONS.md

### 1. Indexes
We created:
- `employees`: Unique index on `emp_code` (prevents duplicate codes; O(1) lookups) and compound index on `{department: 1, joined_on: 1}` (accelerates department analytics).
- `attendance_logs`: Compound unique index on `{emp_code: 1, date: 1}` (enforces one record per shift), `{date: -1, emp_code: 1}` (cursor pagination), `{emp_code: 1, punch_in: -1}` (open punch-out matching), and `{date: 1, status: 1}` (trend aggregations).

We considered and rejected an index on `history.at`: regularizations are infrequent append-only audit entries; indexing embedded arrays adds write amplification without query utility.

### 2. Punch-in race
When two identical punch-in requests arrive simultaneously, both compute the shift date and execute `insert_one`. MongoDB's WiredTiger engine atomically commits the first write. The second write trips the compound unique index on `{emp_code, date}` and throws `DuplicateKeyError`. The application intercepts this exception and returns `HTTP 409 Conflict` ("Attendance already logged for this date"), preventing duplicate records without distributed locks.

### 3. Ties
We implement standard competition ranking via MongoDB's `$setWindowFields` / `$rank` (e.g., 1, 2, 2, 4). When employees tie on late minutes at the boundary of `limit`, both share the identical rank number. We filter by `rank <= limit` and break visual ordering ties with `emp_code` ascending, ensuring all tied candidates within the rank ceiling are preserved rather than randomly truncated.

### 4. Headcount
The department summary pipeline originates from the `employees` collection (filtered by `joined_on <= month_end`) rather than `attendance_logs`. It executes a `$lookup` left outer join to fetch monthly logs. Consequently, every employee employed on or before month-end is retained, accurately including staff with zero logs in the active headcount and working days calculations.

### 5. At 100x scale
We would maintain pre-aggregated daily and monthly rollup collections updated incrementally on punch events via Change Streams or outbox events, and shard `attendance_logs` using a compound shard key `{department: 1, emp_code: 1}` to distribute IO across shards.
