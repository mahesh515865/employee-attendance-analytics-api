# Employee Attendance & Analytics API (FastAPI + MongoDB)

## Run instructions
1. Install dependencies: `pip install -r requirements.txt`
2. Set environment variables: `MONGO_URI` and `MONGO_DB`
3. Run the server: `uvicorn app.main:app --port 8000`

## Known limitations
- No automated tests
- Explain plans not verified on 100k records
- No authentication implemented