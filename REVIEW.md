# REVIEW.md

List every defect found in the starter `app/main.py` (helpers and endpoints). For each defect, describe how it was identified and fixed.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `explain_endpoint` function signature | Missing `Query` aliases for `from_date` and `to_date` parameters | Calling `/admin/explain/department_trend?from=2026-10-12&to=2026-10-18` resulted in HTTP 422 because the endpoint did not recognize the required query parameters. | Added `Query(None, alias="from")` and `Query(None, alias="to")` to the parameters. |
| 2 | `startup_event` function | Missing indexes on `employees.joined_on` and `employees.department` | The required indexes were absent from startup initialization, potentially affecting query performance on larger datasets. | Added `db.employees.create_index("joined_on")` and `db.employees.create_index("department")`. Verified the resulting explain plans against the local test dataset; larger-dataset performance still requires validation. |
| 3 | `EmployeeIn.joined_on` model validator | The value was not validated as a real calendar date, allowing impossible dates such as `2026-02-30`. | Attempting to create an employee with `joined_on: "2026-02-30"` would incorrectly succeed. | Added `joined_on_must_be_valid_date`, using `datetime.strptime` to validate real calendar dates in `YYYY-MM-DD` format. |
| 4 | `PunchInIn.punched_at` and `PunchOutIn.punched_at` field validators | Validation after Pydantic coercion allowed numeric strings to be converted to integers. | Sending a string instead of an integer could be silently accepted. | Changed validators to `mode="before"`. Added `type(v) is int` to reject strings, floats, and booleans; preserved `None` where allowed; and enforced the epoch-millisecond range `100000000000`–`4102444800000`. |
| 5 | `regularize_attendance` function — history timestamp | Manual correction history timestamps retained millisecond precision instead of being truncated to whole seconds as required by R1. | The `at` field in history entries could contain milliseconds, violating the timestamp precision requirement. | Changed `history_entry["at"]` to use `truncate_to_seconds(int(datetime.now(timezone.utc).timestamp() * 1000))`, reusing the existing helper. |

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

## Validation notes

- `python -m py_compile app/main.py` completed successfully.
- The `/health` endpoint returned HTTP 200 when MongoDB was available.
- The inspected explain branches returned HTTP 200 against the local test dataset.
- Validation tests confirmed rejection of impossible calendar dates, out-of-range timestamps, numeric-string timestamps, floats, and booleans.
- These checks do not establish performance or correctness for every edge case or for the grader's larger dataset.