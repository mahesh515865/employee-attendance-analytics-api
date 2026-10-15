# DECISIONS.md

## 1. Indexes
A unique index on `employees.emp_code` prevents duplicate employee codes. The unique compound index on `attendance_logs.emp_code` and `date` prevents duplicate daily records. Indexes on `employees.joined_on`, `employees.department`, and attendance date support filtering and analytics. Query plans must be verified using execution statistics.

## 2. Punch-in race
Concurrent punch-ins rely on the unique employee/date index. MongoDB accepts one insert; duplicate-key errors return HTTP 409, preventing duplicate records.

## 3. Leaderboard ties
The leaderboard uses competition ranking (1, 2, 2, 4). The limit is applied after ranking, so all employees tied at the cutoff are included, potentially exceeding the requested count.

## 4. Headcount
The department summary starts from eligible employees and uses `$lookup` with `$unwind` and `preserveNullAndEmptyArrays: true`. Employees without attendance records still count toward headcount but contribute zero attendance metrics.

## 5. Scaling to 100x data
For substantially larger datasets, precomputed monthly summaries could reduce repeated aggregation work. Background refreshes or change streams introduce additional complexity and possible data freshness delays. Before adopting this design, benchmark representative workloads, inspect execution plans, and evaluate compound indexes against measured query patterns.