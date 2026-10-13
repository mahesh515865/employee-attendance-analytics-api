# DECISIONS.md

1. **Indexes.** Created: employees.emp_code (unique), employees.joined_on (for department_summary joined_on ≤ month_end filter), employees.department (for department_trend department lookup), attendance_logs.emp_code_1_date_1 (unique), attendance_logs.date_-1_emp_code_1 (for recent-first queries). Considered but rejected compound index (department, joined_on) as single-field indexes suffice.

2. **Punch-in race.** First identical request succeeds via insert_one(); second throws pymongo.errors.DuplicateKeyError on unique emp_code/date index, caught and returned as HTTP 409 Conflict.

3. **Ties.** Leaderboard uses standard competition ranking: equal late_minutes share rank. When applying limit, all employees with rank ≤ limit are included. Tied groups are all-in/all-out, possibly exceeding limit if tie at cutoff.

4. **Headcount.** Department summary uses $lookup from employees to attendance_logs with $unwind preserveNullAndEmptyArrays:True. Zero-log employees produce one null document, contributing 1 to employee_count (headcount) but 0 to metrics via null-handling in conditionals.

5. **100x dataset.** Add materialized view: pre-compute monthly department summaries in separate collection updated via background jobs/changes streams. Serve endpoint via lookups instead of real-time aggregation, trading freshness for scale performance.
