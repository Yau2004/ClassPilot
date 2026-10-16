# Project instructions

- Keep this project on Python 3.12 in the `steam-classroom` Conda environment. Do not install project packages in Conda base or create a `.venv`.
- PostgreSQL is the only supported application database. Do not add SQLite or MongoDB fallbacks.
- Keep database credentials in the project-root `.env`; never print, commit, or copy secret values into code, logs, or examples.
- Read `.env` through the absolute project-root path defined in `backend/app/core/config.py`.
- Keep development and test database URLs separate. Tests that need a database must require `POSTGRES_TEST_DB=steam_classroom_test` and must fail closed when test configuration is missing. Never fall back to the development database.
- Before test writes or cleanup, verify `current_database()` is exactly `steam_classroom_test` and the expected Alembic revision is applied; keep test writes inside transactions that are rolled back.
- Use Alembic for schema changes. Require an explicit target and verify the connected database name before migration DDL. Do not apply migrations without explicit task authorization and review.
- Keep business APIs and the React frontend out of the environment foundation until requested.
