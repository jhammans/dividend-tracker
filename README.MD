Dividend Tracker — DB Models & Alembic Migration


What this folder provides:
- requirements.txt
- .env.example
- models.py (SQLAlchemy ORM models)
- alembic/ (alembic configuration + migration script)


Quick start (assumes Python 3.10+ and PostgreSQL available):


1. Create and activate a virtual environment


python -m venv .venv
source .venv/bin/activate # macOS/Linux
.venv\Scripts\activate # Windows


2. Install dependencies


pip install -r requirements.txt


3. Set your DATABASE_URL in environment


export DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/dividend_db
# or on Windows PowerShell
$env:DATABASE_URL = "postgresql+psycopg2://user:password@localhost:5432/dividend_db"


4. Initialize database (run alembic migrations)


alembic upgrade head


5. Verify tables in Postgres. Done!

---

## Next: Import Your Data

Once the database is running, see [IMPORT_GUIDE.md](IMPORT_GUIDE.md) for how to:
- Export holdings and transactions from your broker (Schwab)
- Use the import pipeline to load your data
- Verify the data was imported correctly

Quick command:
```bash
python import_pipeline.py accounts  # List your accounts
python import_pipeline.py full /path/to/holdings.csv /path/to/transactions.csv 2
```

---

## Notes
- The alembic env.py imports metadata from models.py so Alembic can autogenerate future revisions.
- If you prefer to autogenerate instead of using the provided migration file, run:

```bash
alembic revision --autogenerate -m "create base tables"
```