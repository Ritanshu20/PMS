# GLOSSARY

One plain line per term, and where it is used.

| Term | Meaning | Where |
|---|---|---|
| Django app | A folder with models, views and logic for one area | `accounts/`, `projects/`, `tasks/`, `sprints/`, `activity/`, `core/` |
| Model | A Python class that maps to a database table | `tasks/models.py` |
| Migration | A file that changes the database schema step by step | `tasks/migrations/` |
| View | A function/class that receives a request and returns a response | `tasks/views.py`, `tasks/api_views.py` |
| Service | Our function that performs a write and enforces business rules | `tasks/services.py` |
| Selector | Our function that only reads data (with the right joins) | `tasks/selectors.py` |
| Serializer | DRF class that validates JSON input and shapes JSON output | `tasks/serializers.py` |
| Form | Django class that validates HTML form input | `tasks/forms.py` |
| Template | HTML file with Django tags, rendered on the server | `templates/tasks/board.html` |
| DRF | Django REST Framework: toolkit for JSON APIs | `tasks/api_views.py` |
| JWT | Signed token for API login (access token + refresh token) | `accounts/api_views.py` |
| Token blacklist | Table of refresh tokens that may never be used again (logout) | `accounts/services.py::logout_user` |
| Session auth | Cookie-based login used by the HTML pages | `accounts/views.py::ThrottledLoginView` |
| CSRF | Attack where another site submits a form as you; blocked by a secret token | `templates/base.html` (meta tag), `static/js/board.js` |
| XSS | Injecting script into a page; blocked by template autoescaping | `core/tests/test_security.py` |
| IDOR | Reading someone else's object by changing an id in the URL | `core/tests/test_full_suite.py::IdorSweepTests` |
| Throttling | Limiting requests per minute | `config/settings.py` (`DEFAULT_THROTTLE_RATES`) |
| `transaction.atomic()` | All database changes inside succeed together or are all undone | `tasks/services.py::create_task` |
| `select_for_update` | Row lock: other transactions wait until we commit | `projects/services.py::lock_project_and_recheck` |
| Pessimistic locking | Lock first, then change (used for the project row) | `projects/services.py::lock_project_and_recheck` |
| Optimistic locking | Change only if `version` is unchanged; otherwise 409 | `tasks/services.py::_apply_versioned_update` |
| `F()` expression | Let the database compute a value (`version + 1`) instead of Python | `tasks/services.py::_apply_versioned_update` |
| 409 Conflict | HTTP status for "someone else changed this first" | `core/exceptions.py::VersionConflict` |
| Race condition | Two requests at the same time producing a wrong result | `docs/CONCURRENCY.md` |
| `TransactionTestCase` | Test case that really commits, so threads see each other's data | `tasks/tests/test_concurrency.py` |
| `threading.Barrier` | Makes several threads start at the same moment | `core/testing.py::run_concurrently` |
| Idempotent | Running it again changes nothing | `activity/services.py::create_due_soon_notifications`, `core/management/commands/seed_demo.py` |
| `get_or_create` | Fetch a row or create it if missing (safe with a unique constraint) | `activity/services.py::_create_due_soon` |
| CheckConstraint | Database rule on one row (e.g. start ≤ end) | `sprints/models.py::Sprint` |
| UniqueConstraint | Database rule: no two rows with the same values | `tasks/models.py::Task` |
| Partial unique index | Unique only for rows matching a condition (only one active sprint) | `sprints/models.py::Sprint` |
| Index | Database lookup structure that makes filters/sorts fast | `activity/models.py::ActivityLog` |
| `EXPLAIN ANALYZE` | PostgreSQL command showing how a query ran and how long it took | `docs/DECISIONS.md` (decision 39, the `activity_target_created` index) |
| N+1 queries | One query for a list plus one per row; slow | `core/tests/test_query_counts.py` |
| `select_related` | Load a foreign-key row in the same SQL (JOIN) | `tasks/selectors.py::list_tasks` |
| `prefetch_related` | Load related lists in one extra query each | `tasks/selectors.py::list_tasks` |
| `annotate` / `aggregate` | Let PostgreSQL compute counts/sums per row / for the whole set | `projects/dashboard_selectors.py::task_counts` |
| `Count(..., filter=Q(...))` | `COUNT(*) FILTER (WHERE ...)`: several counts in one scan | `projects/dashboard_selectors.py::task_counts` |
| `TruncDate` | Cut a timestamp to its date in a given timezone | `sprints/selectors.py::burndown` |
| `TextChoices` | Named constants for a field's allowed values | `tasks/models.py::Task` |
| Timezone-aware datetime | A time with its UTC offset; stored in UTC, shown in Asia/Kolkata | `core/clock.py::today` |
| Patchable clock | One `now()` function tests can replace to freeze time | `core/clock.py::now` |
| BFS | Breadth-first search: visit graph nodes level by level | `tasks/dependency_services.py::would_create_cycle` |
| Cycle | A loop in the dependency graph (A→B→C→A) | `tasks/dependency_services.py::would_create_cycle` |
| Burndown | Remaining story points per day of a sprint | `sprints/selectors.py::burndown` |
| Velocity | Story points a team completes per sprint | `sprints/selectors.py::velocity` |
| Story points | Relative size of a task | `tasks/models.py::Task` |
| Scope change | Work added to or removed from a running sprint | `sprints/models.py::SprintScopeChange` |
| Soft delete | Mark as deleted instead of removing the row | `tasks/comment_services.py::delete_comment` |
| Activity log | Audit trail row written with every change | `activity/services.py::log_activity` |
| Context processor | Adds a value to every template (unread badge) | `core/context_processors.py::unread_notifications` |
| `json_script` | Template filter that safely embeds data for JavaScript | `templates/sprints/sprint_detail.html` |
| OpenAPI / Swagger | Machine-readable API description and its web UI | `core/urls.py` (`/api/docs/`) |
| Pagination | Returning a list page by page | `core/pagination.py::StandardPagination` |
| HSTS | Header telling browsers to always use HTTPS | `config/settings.py` |
| Open redirect | Redirecting to an attacker's site via a link parameter | `activity/views.py::notification_open` |
| Ruff | Fast Python linter and formatter | `pyproject.toml` |
| pip-audit | Checks dependencies for known vulnerabilities | `.github/workflows/ci.yml` |
| CI | Automatic checks on every push | `.github/workflows/ci.yml` |
| venv | Isolated Python environment for this project | `docs/SETUP.md` |
