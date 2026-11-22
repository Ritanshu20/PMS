# DECISIONS: what was decided and why

## Environment and process

| # | Decision | Why |
|---|---|---|
| 1 | **Django 5.2 LTS** (5.2.19), not 6.1 | Only Python 3.11 is installed; Django 6.x needs 3.12+. 5.2 LTS gets security fixes until April 2028 (allowed by spec §1C). Upgrade path: install Python 3.13, then Django 6.1. |
| 2 | **PostgreSQL 17.11 from the official EDB portable zip**, not winget | You chose winget, but `winget search/show` hung for more than 5 minutes while the network was fine. The zip is the same official build, needs no admin/UAC and installs no service. Docker was not available; SQLite/MySQL are forbidden. |
| 3 | Superuser password generated into `C:\Users\Dell\pgsql17\superuser_password.txt` | `initdb --pwprompt` needs an interactive terminal. The file is outside the project and was never printed; change it with `\password postgres` (see `docs/SETUP.md`). |
| 4 | All Python packages in `venv\` | You asked for it. PostgreSQL itself is a server and cannot live inside a venv. |
| 5 | Claude made no git commits or pushes | git is not installed on this machine and you push yourself. |
| 6 | Doc named `docs/CODE_MAPPING.md` | Your requested name; it replaces the spec's CODE_MAP file. |
| 7 | CI uses Python 3.11 and `SECURE_SSL_REDIRECT=False` | Same Python as the local venv; tests talk plain http, production keeps the redirect. |
| 8 | Bootstrap 5.3.3 and Chart.js 4.4.1 are vendored in `static/vendor/` | No CDN dependency, works offline, and a strict CSP (`script-src 'self'`) is possible later. |

## Architecture

| # | Decision | Why |
|---|---|---|
| 9 | **Every project write locks the project row** (`projects/services.py::lock_project_and_recheck`) | Numbering, board positions, the dependency graph, the active sprint and the last owner are project-wide rules. One lock per project is simple and correct; write rates are low. `version` still decides lost-update conflicts. |
| 10 | Re-read the actor's membership after taking the lock | A role removed a moment ago must be respected (makes the last-owner race safe twice over). |
| 11 | Services raise `Http404` / `PermissionDenied` / `ValidationError` / `BusinessRuleError` / `VersionConflict` | Services stay HTTP-free, so the API (`core/exceptions.py::api_exception_handler`) and the pages (`core/web.py::run_service`) reuse them. |
| 12 | One error format `{"code", "detail"[, "details"]}` | Clients branch on a stable `code` and show `detail`. |
| 13 | `created_at`/`updated_at` use `auto_now(_add)`; business times use `core/clock.py` | Timestamps are audit data; business rules (completed_at, overdue, due soon, burndown days) need a patchable clock. |
| 14 | Big view modules split (`tasks/api_views.py`, `tasks/comment_api_views.py`, `tasks/time_api_views.py`, `tasks/action_views.py`, `tasks/report_views.py`) | Keep files short and easy to explain. |

## Business rules where the spec left a choice

| # | Decision | Why |
|---|---|---|
| 15 | Project key input is uppercased (`web` → `WEB`) | Friendlier; the DB CheckConstraint still enforces `^[A-Z]{2,6}$`. |
| 16 | Managers may edit the project name/description | Not in the matrix; managers run the project, owners keep archive/delete/transfer. |
| 17 | Workflow: forward one step, backward any step | "Transitions in one place, backward allowed". One step forward keeps review in the loop. |
| 18 | New tasks start in `backlog` or `todo` only | Starting later would bypass the in_progress/done rules. |
| 19 | Removing a member or demoting to viewer unassigns their tasks (and bumps `version`) | Keeps "assignee is a non-viewer member" true; open editors get 409. |
| 20 | A done task cannot receive an open blocker | Otherwise a done task would have an open blocker. |
| 21 | Board reorder does not bump other cards' `version` | Reordering is not a content edit. |
| 22 | A task must leave its sprint before joining another; done tasks cannot be planned | Clear rule for "at most one sprint"; finished work is not planned. |
| 23 | Completing a sprint keeps the status of moved tasks; done tasks stay linked to the old sprint | History for velocity; "move to backlog" means "no sprint". |
| 24 | Story-point changes in an active sprint are scope changes of the difference | Keeps the burndown formula consistent (3 → 5 records "added 2"). Deleting an active-sprint task records "removed". |
| 25 | Single-day sprint: ideal line is 0 | The target is reached on the end date; avoids a division by zero. |
| 26 | `Task.estimate_minutes` added (nullable) | The spec asks for "estimate vs logged" but story points are not time. |
| 27 | Daily time cap is per user across **all** projects, protected by locking the user row | The project lock alone cannot see other projects. |
| 28 | Time cannot be logged for future dates | Time is logged after the work. |
| 29 | Due soon = open, assigned task in an active project with due date from today to the local date 24 h ahead | Due dates have no time of day; overdue tasks are not "due soon". |
| 30 | Mentions notify viewers too | Viewers are members and can read the task. |
| 31 | Editing a comment notifies only newly mentioned users | "Once per comment per user". |
| 32 | Soft-deleted comments clear their text | "[deleted]" must not hide readable content in the database. |
| 33 | Notifications are shown only for projects you are still a member of | No links into projects you lost access to. |
| 34 | Adding a member by an unknown email says "No registered user has this email" | Needed for usability; only managers/owners can call it. |

## Security, performance and tests

| # | Decision | Why |
|---|---|---|
| 35 | No CSP header | Django 5.2 has no built-in CSP (added in 6.0); the spec asks for CSP only then. Templates are already CSP-ready. |
| 36 | HSTS preload stays off (`security.W021`) | Joining the preload list is hard to undo; enable it on purpose with `SECURE_HSTS_PRELOAD=True`. |
| 37 | Session login throttle by `REMOTE_ADDR`, stored in the cache | `X-Forwarded-For` can be forged. Limitation: the local-memory cache is per process; with several workers use a shared cache. |
| 38 | Pages that show only number/title/status use `prefetch_related(None)` | Measured: backlog page 14 → 8 queries (Phase 16). |
| 39 | Composite index `activity_target_created` | `EXPLAIN ANALYZE`: 13.0 ms seq scan → 0.23 ms index scan on 50k rows. |
| 40 | Text search stays `icontains` | A `pg_trgm` index would need a PostgreSQL extension; next step for very large projects. |
| 41 | Tests use the MD5 hasher and quiet logs only when running `manage.py test` | Hundreds of users are created; production hashing is unchanged. |
| 42 | Concurrency test 4 written in Phase 2 (spec lists it in Phase 6) | Prove last-owner safety when it is built. |
| 43 | IDOR checks walk the URL resolver instead of a hand-written list | New endpoints are covered automatically. |
| 44 | `seed_demo` password is public (`DEMO_PASSWORD`, default `demo-pass-2026`) | Local demo only; never run `seed_demo` on a public server. |
