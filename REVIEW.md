# REVIEW.md

Defects found in the starter `app/main.py` (helpers and endpoints). Line numbers refer to the starter file.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health` (L72) | Returns ok without checking MongoDB. | Stop MongoDB: `/health` still returns 200 instead of 503. | `db.command("ping")`; any failure raises 503. |
| 2 | `compute_late_minutes` (L33-37) | The shift start is built on the UTC datetime, not IST. Minutes are floored before the `> 10` check, so 09:40:01 gives 10 and `10 > 10` is false. Seconds are not truncated. | Shift 09:30, punch-in 09:40:01 IST returns 0, expected 10. | Convert to IST, anchor shift start on the record's attendance date, compare the unrounded elapsed minutes with 10, then floor. |
| 3 | `compute_work_hours` (L40-41) | Python `round()` is not half-up (banker's rounding plus float error). | 450 seconds is 0.125 h; `round` gives 0.12, expected 0.13. | `Decimal` with `ROUND_HALF_UP` (`round_half_up`). |
| 4 | `compute_overtime` (L44-47) | No 30-minute minimum, so any positive minutes count. Subtracts a naive datetime from an aware one. Overnight shifts are ignored. | Punch-out at 18:40 on an 18:30 shift reports 10 overtime minutes, expected 0. A naive punch-out raises TypeError. | Convert to IST, floor minutes, return them only if >= 30, and move the shift end to the next day for overnight shifts. |
| 5 | `create_employee` (L77-85) | Check-then-insert with no unique index, so parallel requests create duplicates. | Send two identical POSTs at once: two 201s. | Unique index on `emp_code` at startup; insert and catch `DuplicateKeyError`, return 409. |
| 6 | `create_employee` (L82) | `created_at` is `datetime.now()`, naive local time, and is returned as a datetime instead of epoch ms. | Response `created_at` is an ISO string, not an integer. | Store UTC truncated to seconds; return epoch milliseconds. |
| 7 | `EmployeeIn` (L53-60) | No validation: `emp_code` format, email, `HH:MM` shifts, a real `joined_on` date, and `shift_start == shift_end` are all accepted. | POST `joined_on: "2026-02-30"` returns 201 instead of 422. | Field patterns and validators matching `openapi.yaml`. |
| 8 | `list_employees` (L93) | `skip = page * page_size` skips the first page. | `page=1` returns the second page of data. | `(page - 1) * page_size`. |
| 9 | `list_employees` (L94) | `total` counts all employees, ignoring the department filter. | `department=Eng` shows the same `total` as no filter. | `count_documents(q)` with the same filter. |
| 10 | `list_employees` | No sort by `emp_code` and no `page` / `page_size` validation (max 100). | `page_size=1000` or `page=0` is accepted. | Sort by `emp_code`; 422 for `page < 1` or `page_size` outside 1-100. |
| 11 | `punch_in` (L101) | Unknown employee gives `emp = None`, then `emp["shift_start"]` raises, so 500. | Punch-in for `EMP9999` returns 500, expected 404. | Return 404 when the employee is missing. |
| 12 | `punch_in` (L102-103) | Uses server-local naive time. The date is not the IST date. Seconds are not truncated. `punched_at=0` is treated as missing. Seconds, floats and strings are not rejected. | `punched_at` given in seconds is accepted. | Validators (`mode="before"`, `type(v) is int`, range 1e11 to 4.1e12). Convert to UTC, truncate to seconds, derive the date in IST. |
| 13 | `punch_in` (L66) | `status` is not validated, so `ABSENT` or any string can be created. | POST `status: "ABSENT"` returns 201. | Only `PRESENT`, `WFH` and `ON_DUTY` are accepted (422 otherwise). |
| 14 | `punch_in` (L104-118) | Check-then-insert race: two simultaneous punch-ins can both return 201. | Two parallel identical requests give two 201s or an extra record. | Unique compound index (`emp_code`, `date`); `DuplicateKeyError` becomes 409. |
| 15 | `punch_in` (L103, L119-121) | The record date ignores overnight shifts (R1), and `_id` leaks as `id`. `punch_in` is returned as a datetime, not epoch ms. | Shift 22:00-06:00, punch-in at 00:30 IST gets the wrong date and late 0. | `attendance_date_for()` implements R1. `_id` is removed. Instants are converted to epoch ms. |
| 16 | `list_attendance` (L144-150) | Loads the whole collection, sorts in Python, slices in memory. The sort has no `emp_code` tie-break. `_id` leaks as `id`. Datetimes are not epoch ms. Seeded records missing `history`, `half_day` and similar are returned as-is. No `date_from > date_to` check. | 100k records means a full scan on every request; the order is not stable. | DB-side `sort([("date", -1), ("emp_code", 1)])`, `skip` and `limit`, plus the index `(date -1, emp_code 1)`. `_id` removed, epoch ms, defaults filled, 422 for a bad range. |
| 17 | Startup | No indexes are created. | `explain` shows COLLSCAN. | Create all indexes idempotently at startup. |

## Examined and found correct
- `load_dotenv()` does not override real environment variables, so `MONGO_URI` and `MONGO_DB` from the environment win.
- The `date_from` / `date_to` filter itself (string comparison on `YYYY-MM-DD`) is inclusive and correct.
- The department-summary `on_duty_count` is summed correctly across the two `$group` stages.

## Validation notes
- `python -m py_compile app/main.py` passes.
- Helper checks run directly: late 09:40:00 gives 0, 09:40:01 gives 10, 10:15:59 gives 45; overtime 18:59:59 gives 0 and 19:00:00 gives 30; overnight overtime and attendance date are correct; half-up work hours match an exact calculation for every whole second.
- Manual HTTP tests: create employee, punch-in and punch-out, overnight punch-in at 00:30 IST (previous-day date, late 150), overnight punch-out (200), and punch-out at the punch-in instant (422).
- Not verified: explain plans (IXSCAN, no COLLSCAN) and timings on about 100,000 records, behaviour under heavy concurrency, and analytics results against the grader's hidden dataset.