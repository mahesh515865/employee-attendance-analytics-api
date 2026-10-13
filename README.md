# Candidate kit

Read in this order:

1. `PROBLEM_STATEMENT.docx`: what you are asked to do, how it is evaluated.
2. `openapi.yaml`: the API contract and the business rules (R1 - R10).
3. `DATA_MODEL.md`: what is stored in MongoDB, with nine sample documents.
4. `app/main.py`: the implemented Employee Attendance & Analytics API. Put **all** your code in this one file. It must start with `uvicorn app.main:app --port 8000`.
5. `REVIEW.md`, `DECISIONS.md`: templates to fill in.

`sample_data/` holds the nine sample documents as MongoDB Extended JSON; `sample_seed.py` loads them. The grader's dataset is much larger and contains cases they do not show.

## Local setup

Requirements: Python 3.11+ and MongoDB 6.0+.

From Windows Command Prompt, run:

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set MONGO_URI=mongodb://localhost:27017
set MONGO_DB=attendance_db
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```

Keep the environment variables set in the same terminal where you run the application. The MongoDB server must be running before seeding or starting the API.

To use PowerShell instead, activate the environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

MongoDB Atlas can also be used by setting `MONGO_URI` to the appropriate connection string.


## Submit
A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and a short `README.md`.
No `.env`, no secrets, no Dockerfile.