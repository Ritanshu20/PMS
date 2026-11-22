# CODE MAPPING: where everything lives

(This file replaces `docs/CODE_MAP.md` from the spec.)

## The one rule to remember

```
URL  ->  view (thin)  ->  permissions  ->  service (writes) / selector (reads)  ->  PostgreSQL
          api_views.py      projects/        services.py        selectors.py
          views.py          permissions.py
```

- **API** views live in `*/api_views.py` and use DRF serializers (`*/serializers.py`).
- **Web page** views live in `*/views.py` (plus a few `*_views.py`) and use Django forms (`*/forms.py`).
- Both kinds of views call the **same** services and selectors, so a rule is written once.
- `projects/permissions.py` is the only place that decides who may do what.

## Project root

| Path | Responsibility |
|---|---|
| `manage.py` | Django command line entry point |
| `config/settings.py` | All settings from env vars; PostgreSQL only; DRF/JWT/throttles; security; test-only speed-ups |
| `config/urls.py` | Joins API routes (`/api/...`) and web page routes (`/`, `/projects/...`) |
| `requirements.txt`, `requirements-dev.txt` | Pinned dependencies (runtime / dev tools) |
| `pyproject.toml` | Ruff lint + format settings |
| `.github/workflows/ci.yml` | CI: ruff, check, makemigrations --check, tests on PostgreSQL 17, pip-audit |
| `.env.example` | Template for the local, git-ignored `.env` |
| `templates/` | All HTML templates (base layout, partials, one folder per app) |
| `static/` | `static/css/app.css`, `static/js/app.js`, `static/js/board.js`, `static/js/charts.js`, vendored Bootstrap + Chart.js in `static/vendor/` |

## `core/`: shared infrastructure (no models)

| File | What it holds |
|---|---|
| `core/clock.py` | `now()`, `today()`: the only source of time for business rules (tests patch it) |
| `core/exceptions.py` | `BusinessRuleError` (400), `VersionConflict` (409), `api_exception_handler` (one error format) |
| `core/pagination.py` | `StandardPagination` (`?page=`, `?page_size=` up to 100) |
| `core/schema.py` | `paginated()` and `ERROR_RESPONSE` for the Swagger description |
| `core/web.py` | `run_service` (service errors → flash messages), `paginate`, `validation_messages` |
| `core/forms.py` | `BootstrapFormMixin` (adds Bootstrap CSS classes to form widgets) |
| `core/context_processors.py` | `unread_notifications` for the navbar badge |
| `core/api_views.py`, `core/urls.py` | `/api/health/`, `/api/schema/`, `/api/docs/` (Swagger UI) |
| `core/management/commands/seed_demo.py` | `manage.py seed_demo`: idempotent demo data |
| `core/testing.py` | Test helpers: `make_user`, `make_project`, `ProjectWithRolesMixin`, `run_concurrently` |

## `accounts/`: users and login

| File | What it holds |
|---|---|
| `accounts/models.py` | `User` (custom, case-insensitive unique email, `display_name`) |
| `accounts/services.py` | `register_user`, `logout_user` (JWT blacklist) |
| `accounts/api_views.py`, `accounts/urls.py` | `/api/auth/register|login|refresh|logout|me/` |
| `accounts/views.py`, `accounts/urls_web.py`, `accounts/forms.py` | Session pages: `ThrottledLoginView`, `register`, logout |

## `projects/`: projects, members, permissions, dashboard

| File | What it holds |
|---|---|
| `projects/models.py` | `Project` (key, status, `task_counter`), `ProjectMember` (role) |
| `projects/permissions.py` | `Action`, `ROLE_PERMISSIONS`, `get_membership_or_404`, `has_permission`, `require_permission`, `permission_flags`, `can_delete_task`, `can_edit_comment`, `can_delete_comment`, `can_change_time_entry`, `can_manage_member`, `is_assignable`, `visible_projects_filter` |
| `projects/services.py` | `lock_project_and_recheck` (the project row lock), `create_project`, `update_project`, `archive_project`, `delete_project`, `add_member`, `change_member_role`, `remove_member`, `transfer_ownership` |
| `projects/selectors.py` | `projects_for_user`, `project_members` |
| `projects/dashboard_selectors.py` | `project_dashboard`, `task_counts`, `current_sprint_progress` |
| `projects/api_views.py`, `projects/urls.py` | `/api/projects/...` (CRUD, archive, transfer, members, dashboard) |
| `projects/views.py`, `projects/urls_web.py`, `projects/forms.py` | Pages: my projects, create, dashboard, members, activity |

## `tasks/`: tasks, labels, workflow, dependencies, comments, time

| File | What it holds |
|---|---|
| `tasks/models.py` | `Task`, `Label`, `TaskDependency`, `Comment`, `TimeEntry` |
| `tasks/services.py` | `create_task` (numbering), `update_task` (optimistic lock), `move_task` (board), `delete_task`, labels |
| `tasks/workflow.py` | `STATUS_ORDER`, `allowed_next_statuses`, `validate_transition`, `completed_at_after` |
| `tasks/dependency_services.py` | `would_create_cycle` (BFS), `add_dependency`, `remove_dependency` |
| `tasks/comment_services.py` | `create_comment`, `edit_comment`, `delete_comment` (soft) + mention notifications |
| `tasks/mentions.py` | `extract_usernames`, `mentioned_members` |
| `tasks/time_services.py` | `log_time`, `update_time_entry`, `delete_time_entry` (daily 1440-minute cap) |
| `tasks/selectors.py` | `list_tasks` (filters/search/order), `get_task_or_404`, `board`, `backlog`, `my_tasks`, `open_blockers`, `dependency_graph`, `task_comments`, `task_time_entries`, `project_labels` |
| `tasks/report_selectors.py` | `time_by_task`, `time_by_user`, `time_by_sprint`, `workload` |
| `tasks/serializers.py` | API input/output shapes, `TaskFilterSerializer` (query params) |
| `tasks/api_views.py` | Tasks, move, board, labels, dependencies, my tasks |
| `tasks/comment_api_views.py`, `tasks/time_api_views.py` | Comment API; time entry + report API |
| `tasks/urls.py` | All task API routes under `/api/projects/<id>/` |
| `tasks/views.py` | Pages: board, `board_move` (JSON for drag-and-drop), task create/detail/edit/delete |
| `tasks/action_views.py` | POST actions from the task page: comments, dependencies, time |
| `tasks/report_views.py` | Pages: time report, my tasks |
| `tasks/forms.py`, `tasks/urls_web.py` | Web forms and routes |

## `sprints/`: sprints, burndown, velocity

| File | What it holds |
|---|---|
| `sprints/models.py` | `Sprint` (one active per project: partial unique index), `SprintScopeChange` |
| `sprints/services.py` | `create_sprint`, `update_sprint`, `delete_sprint`, `add_task_to_sprint`, `remove_task_from_sprint`, `start_sprint`, `complete_sprint`, `record_scope_change`, `record_story_point_change` |
| `sprints/selectors.py` | `sprints_for_project`, `active_sprint`, `burndown`, `ideal_remaining`, `velocity` |
| `sprints/api_views.py`, `sprints/urls.py` | `/api/projects/<id>/sprints/...`, `/velocity/`, `/backlog/` |
| `sprints/views.py`, `sprints/urls_web.py`, `sprints/forms.py` | Pages: backlog & planning, sprint detail with charts |

## `activity/`: audit log and notifications

| File | What it holds |
|---|---|
| `activity/models.py` | `ActivityLog`, `Notification` (+ unique due-soon constraint) |
| `activity/services.py` | `log_activity`, `notify`, `mark_notification_read`, `mark_all_notifications_read`, `create_due_soon_notifications` |
| `activity/selectors.py` | `notifications_for_user`, `unread_notification_count`, `task_activity`, `project_activity` |
| `activity/management/commands/notify_due_tasks.py` | `manage.py notify_due_tasks` |
| `activity/api_views.py`, `activity/urls.py` | `/api/notifications/...`, project activity feed |
| `activity/views.py`, `activity/urls_web.py` | Notifications page and mark-read actions |

## Templates

| Folder / file | Pages |
|---|---|
| `templates/base.html` | Layout, navbar (role-aware), messages, scripts |
| `templates/partials/` | `templates/partials/messages.html`, `templates/partials/form_fields.html`, `templates/partials/project_nav.html`, `templates/partials/pagination.html` |
| `templates/registration/login.html`, `templates/accounts/register.html` | Login, register |
| `templates/projects/` | Project list/form, dashboard, members, activity |
| `templates/tasks/` | Board, task detail, task form, time report, my tasks |
| `templates/sprints/` | Backlog & planning, sprint detail |
| `templates/activity/notifications.html` | Notifications |
| `templates/403.html`, `templates/404.html`, `templates/500.html` | Error pages |

## Tests (270)

| File | Covers |
|---|---|
| `core/tests/test_foundation.py` | Health, PostgreSQL only, clock timezone |
| `core/tests/test_api_polish.py` | OpenAPI schema, error format for every status, throttling |
| `core/tests/test_full_suite.py` | URL resolution, automatic API IDOR sweep |
| `core/tests/test_security.py` | Deploy settings, headers, cookies, login throttle, XSS, web IDOR sweep |
| `core/tests/test_query_counts.py` | Exact query budgets |
| `core/tests/test_planning_pages.py` | Planning/sprint/members/notifications/report pages |
| `core/tests/test_docs_accuracy.py` | Every code path in the docs exists |
| `accounts/tests/test_auth_api.py` | Register, JWT login/refresh/logout, throttle |
| `projects/tests/` | Permission matrix, project + member API, dashboard, concurrency test 4 |
| `tasks/tests/` | Tasks, workflow, dependencies, comments, time & reports, web pages, concurrency tests 1, 2, 5 + daily cap |
| `sprints/tests/` | Sprints, burndown & velocity, concurrency test 3 |
| `activity/tests/` | Notifications, due-soon job, concurrency test 6 |
