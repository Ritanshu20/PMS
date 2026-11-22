# SETUP: run PMS on Windows (no admin rights needed)

This machine had no database server, no Docker and no git. PostgreSQL is installed from the
**official EDB portable binaries zip** (no installer, no Windows service, no UAC prompt).
Why not winget: see decision 2 in `docs/DECISIONS.md`.

## 1. Python virtual environment

All Python packages live in `venv\` inside the project.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Python on this machine: 3.11.0, so Django 5.2 LTS is used (Django 6.x needs 3.12+).

## 2. Install PostgreSQL 17 (portable)

1. Download `postgresql-17.11-5-windows-x64-binaries.zip` from
   https://www.enterprisedb.com/download-postgresql-binaries
2. Extract it to `C:\Users\<you>\pgsql17` (you get `C:\Users\<you>\pgsql17\pgsql\bin`).
3. Create the data folder (cluster). Put a superuser password in a temporary file first:

```powershell
$pg = "C:\Users\<you>\pgsql17"
& "$pg\pgsql\bin\initdb.exe" -D "$pg\data" -U postgres -A scram-sha-256 -E UTF8 --locale=C --pwfile="$pg\superuser_password.txt"
```

On this machine a random superuser password was generated into
`C:\Users\Dell\pgsql17\superuser_password.txt` (outside the project, never in git).
**Change it yourself** and then delete that file:

```powershell
& "$pg\pgsql\bin\psql.exe" -h localhost -U postgres -d postgres
# inside psql:
\password postgres
\q
```

## 3. Start / stop the server

It is not a Windows service, so start it after every reboot:

```powershell
$pg = "C:\Users\<you>\pgsql17"
& "$pg\pgsql\bin\pg_ctl.exe" -D "$pg\data" -l "$pg\server.log" -o "-p 5432" start
& "$pg\pgsql\bin\pg_ctl.exe" -D "$pg\data" status
& "$pg\pgsql\bin\pg_ctl.exe" -D "$pg\data" stop
```

It listens on `localhost:5432` only.

## 4. Create the database and the app role

The app role is **not** a superuser. It has `CREATEDB` so Django's test runner can create and
drop `test_pms`.

```sql
-- psql -h localhost -U postgres -d postgres
CREATE ROLE pms_app LOGIN CREATEDB PASSWORD 'choose-a-strong-password';
CREATE DATABASE pms OWNER pms_app ENCODING 'UTF8' TEMPLATE template0;
```

## 5. Configure `.env`

Copy `.env.example` to `.env` and set `SECRET_KEY` and
`DATABASE_URL=postgres://pms_app:<password>@localhost:5432/pms`.
On this machine `.env` was generated with random values; it is git-ignored.

## 6. Run

```powershell
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py seed_demo          # demo users + project (idempotent)
.\venv\Scripts\python.exe manage.py createsuperuser    # optional, for /admin/
.\venv\Scripts\python.exe manage.py runserver
```

Open http://127.0.0.1:8000/ and log in as `demo_owner`, `demo_manager`, `demo_member` or
`demo_viewer` (password `demo-pass-2026`, local demo only). API docs: http://127.0.0.1:8000/api/docs/

## 7. Tests and checks

```powershell
.\venv\Scripts\ruff.exe check .
.\venv\Scripts\ruff.exe format --check .
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\venv\Scripts\python.exe -W error::DeprecationWarning manage.py test --noinput
.\venv\Scripts\python.exe manage.py test --tag concurrency --noinput   # only the race tests
.\venv\Scripts\pip-audit.exe -r requirements-dev.txt
```

Concurrency suite 20 times in a row (PowerShell):

```powershell
$fail = 0; for ($i = 1; $i -le 20; $i++) { $out = .\venv\Scripts\python.exe manage.py test --tag concurrency --noinput 2>&1 | Out-String; if ($out -notmatch "(?m)^OK") { $fail++ } }; "failures: $fail"
```

If a test run is interrupted, the next run may say `database "test_pms" already exists`;
`--noinput` drops it automatically.

## 8. Scheduled job

Run `manage.py notify_due_tasks` once a day (Windows Task Scheduler, or cron on Linux). It is
idempotent: running it twice the same day creates no duplicates.

## 9. Reset the database (if needed)

```sql
-- as postgres
DROP DATABASE pms;
CREATE DATABASE pms OWNER pms_app ENCODING 'UTF8' TEMPLATE template0;
```
Then run `manage.py migrate` and `manage.py seed_demo` again.

## 10. Git (needed for you to push)

git is not installed on this machine. Install it (`winget install Git.Git` or
https://git-scm.com/download/win), then:

```powershell
git init -b main
git add .
git status        # .env, venv/, __pycache__/ must NOT be listed
git commit -m "PMS phases 0-17"
git remote add origin <your GitHub repo URL>
git push -u origin main
```
