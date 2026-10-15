# REVIEW.md

List every defect found in the starter `app/main.py` (helpers and endpoints). For each defect, describe how it was identified and fixed.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `explain_endpoint` function signature | Missing `Query` aliases for `from_date` and `to_date` parameters | Calling `/admin/explain/department_trend?from=2026-10-12&to=2026-10-18` resulted in HTTP 422 because the endpoint did not recognize the required query parameters. | Added `Query(None, alias="from")` and `Query(None, alias="to")` to the parameters. |
| 2 | `startup_event` function | Missing indexes on `employees.joined_on` and `employees.department` | The required indexes were absent from startup initialization, potentially affecting query performance on larger datasets. | Added `db.employees.create_index("joined_on")` and `db.employees.create_index("department")`. Verified the resulting explain plans against the local test dataset; larger-dataset performance still requires validation. |
| 3 | `EmployeeIn.joined_on` model validator | The value was not validated as a real calendar date, allowing impossible dates such as `2026-02-30`. | Attempting to create an employee with `joined_on: "2026-02-30"` would incorrectly succeed. | Added `joined_on_must_be_valid_date`, using `datetime.strptime` to validate real calendar dates in `YYYY-MM-DD` format. |
| 4 | `PunchInIn.punched_at` and `PunchOutIn.punched_at` field validators | Validation after Pydantic coercion allowed numeric strings to be converted to integers. | Sending a string instead of an integer could be silently accepted. | Changed validators to `mode="before"`. Added `type(v) is int` to reject strings, floats, and booleans; preserved `None` where allowed; and enforced the epoch-millisecond range `100000000000`–`4102444800000`. |
| 5 | `regularize_attendance` function — history timestamp | Manual correction history timestamps retained millisecond precision instead of being truncated to whole seconds as required by R1. | The `at` field in history entries could contain milliseconds, violating the timestamp precision requirement. | Changed `history_entry["at"]` to use `truncate_to_seconds(int(datetime.now(timezone.utc).timestamp() * 1000))`, reusing the existing helper. |
| 6 | `health` | Returned `{"status":"ok"}` without checking MongoDB, so the readiness probe passed while the database was down. | Stop MongoDB and call `GET /health`: it still returned 200 instead of 503. | Added `db.command("ping")`; any failure raises HTTP 503 "Database unavailable". |
| 7 | `punch_out` response | Leaked MongoDB's internal `_id` as an `id` field. The contract says `_id` never appears in a response. | Punch out and inspect the JSON: it contained an `id` key that is not in `AttendanceRecord`. | Removed `_id` from the returned document (`pop("_id", None)`) instead of renaming it. |
| 8 | `get_late_leaderboard` | Ranking was done in a Python loop over fetched documents, not in the MongoDB aggregation, and the limit was applied in Python. The contract requires analytics to be aggregation pipelines. | Reading the code; large months would be pulled into memory. | Rank is now computed with `$setWindowFields` using `$rank` (ties share a rank, next rank skipped), then `$match` on `rank <= limit`, then sorted by `total_late_minutes` desc and `emp_code` asc. |
| 9 | `regularize_attendance` — no-op check and clearing | The "changes nothing" check compared only the request body against the stored values. Setting `ABSENT` or `LEAVE` clears both punch times, but that effect was not detected or written to `history`. | PATCH a record that has punch times with `{"status":"ABSENT"}`: the cleared `punch_in` / `punch_out` were missing from `changes`. | Compute the effective new status and punch times first, compare them with the stored record, and record every field that actually changed, including times set to null. |
| 10 | `punch_in` — attendance date (R1) | The record `date` was always the IST calendar date of the punch. For an overnight shift (`shift_end <= shift_start`), a punch-in earlier than `shift_end` belongs to the previous day's shift. | Shift 22:00–06:00, punch-in at 00:30 IST on 15 Oct was stored with date 15 Oct instead of 14 Oct. | Added `attendance_date_for(punch_in, shift_start, shift_end)` implementing R1 and used it in `punch_in`. |
| 11 | `compute_late_minutes` (R2) | Anchored the shift start on the punch-in's own calendar day, so an overnight punch-in after midnight produced a negative value and `late_minutes` was 0. | Same punch: expected 150 late minutes, got 0. | The function now takes the record's attendance date and anchors `shift_start` on that date. All callers pass the record date. |
| 12 | `punch_out` | No 422 when `punched_at` was equal to the punch-in, or more than 24 hours after it, as the contract requires. | Punch out at exactly the punch-in instant returned 200 with 0 hours worked. | Added checks after the open record is found: `ts <= punch_in` -> 422, more than 86400 seconds -> 422. |
| 13 | `RegularizeRequest.punch_in` / `punch_out` | The range validator ran after Pydantic coercion, so numeric strings, floats and booleans were accepted as timestamps (same flaw as #4, but in the PATCH body). | PATCH with `"punch_in": "1790827200000"` or `1790827200000.0` was accepted. | Validator is now `mode="before"`: `None` allowed, `type(v) is int` required (rejects bool), range `100000000000`–`4102444800000`. |
| 14 | `regularize_attendance` — attendance-date check (R1) | "punch_in must stay on the record's attendance date" compared the plain IST date, so a valid correction on an overnight record was rejected with 422. | Correct an overnight record's punch-in to 00:45 IST on the next calendar day: 422 even though it belongs to the same shift. | The new punch-in's date is derived with `attendance_date_for(...)` and compared with the record's `date`. Late minutes are recomputed with the record's date. |

## Not defects — inspected and confirmed correct

- **On-duty count calculation in `department_summary`:** Inspected both `/analytics/departments/summary` and the `department_summary` branch of the explain endpoint. Both calculate employee-level `on_duty_count` in the first `$group` stage using `{"$sum": {"$cond": ["$is_on_duty", 1, 0]}}`, then sum the resulting field in the second `$group` stage using `{"$sum": "$on_duty_count"}`. The second stage correctly references the field produced by the first stage.

- **Explain endpoint `employee_monthly` branch:** Inspected the reconstructed aggregation pipeline and compared it with the real monthly analytics endpoint. The pipeline contains the expected matching, field-calculation, and grouping stages. No defect was identified in the inspected logic.

- **Explain endpoint operation calls:** Inspected the `attendance_list`, `employee_monthly`, `department_summary`, `late_leaderboard`, and `department_trend` branches. Each uses the PyMongo database command interface to request MongoDB explain output with execution statistics. No incorrect cursor `.explain()` calls were identified in these branches.

  The explain operation follows this structure, with `collection_name` replaced by the appropriate collection name:

```python
  db.command({
      "explain": {
          "aggregate": "collection_name",
          "pipeline": pipeline,
          "cursor": {}
      },
      "verbosity": "executionStats"
  })
```

- **`round_half_up`:** uses `Decimal` with `ROUND_HALF_UP`, so work hours and rates are not subject to float or banker's rounding.

## Validation notes

- `python -m py_compile app/main.py` completed successfully.
- Manual tests against a local MongoDB through `/docs`: create employee (201); punch-in and punch-out on a day shift (200, correct `late_minutes`, `work_hours`, `half_day`); overnight 22:00–06:00 punch-in at 00:30 IST gave the previous day's `date` and `late_minutes` 150; overnight punch-out at 06:00 returned 200 with `work_hours` 5.5; punch-out at the punch-in instant returned 422.
- `compute_overtime` was checked directly: punch-out at 19:00 IST on an 18:30 shift end returned 30.
- Earlier validation confirmed rejection of impossible calendar dates, out-of-range timestamps, numeric-string timestamps, floats, and booleans.
- Not verified: explain plans (IXSCAN / no COLLSCAN) and endpoint speed on a dataset of about 100,000 records, and concurrent request behaviour under load.
- These checks do not establish correctness for every edge case or for the grader's larger dataset.