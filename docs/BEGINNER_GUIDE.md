# BEGINNER GUIDE: understand PMS from zero

This guide is for someone who knows basic Python but is new to Django, databases or this project.
Read it top to bottom once. After it you should be able to open any file, know why it exists, and
explain the main logic to someone else.

Other docs go deeper: `docs/LOGIC.md` (all rules and formulas), `docs/WALKTHROUGH.md` (flows with
diagrams), `docs/GLOSSARY.md` (every term).

---

## 1. What is PMS?

PMS is a small **Jira/Trello**: a place where a team keeps its work.

- A **project** (for example "Website", key `WEB`) has **members**, each with a **role**.
- Work items are **tasks** with numbers: `WEB-1`, `WEB-2`, … Each task has a status, priority,
  assignee, due date and story points (a size estimate).
- Tasks sit on a **kanban board** with five columns: Backlog → To do → In progress → In review → Done.
- A task can be **blocked** by another task (a dependency).
- The team plans work in **sprints** (time boxes of 1–2 weeks) and watches a **burndown chart**.
- People **comment**, **@mention** each other, get **notifications** and **log time**.

You can use it in two ways:
1. **Web pages** in a browser (http://127.0.0.1:8000/), server-rendered HTML with Bootstrap.
2. **REST API** with JSON (http://127.0.0.1:8000/api/docs/), for apps and scripts.

Both use exactly the same rules, because both call the same Python functions.

### Try it
Start the app (see `docs/SETUP.md`), run `manage.py seed_demo`, and log in as `demo_member`
(password `demo-pass-2026`, local only). Open **Demo Website**.

---

## 2. Ten words you need first

| Word | Plain meaning |
|---|---|
| Request | What the browser or an app sends to the server ("show me the board", "save this task") |
| URL | The address of a request, e.g. `/projects/1/board/` |
| View | The Python function that receives a request and returns a page or JSON |
| Model | A Python class that describes a database table (`Task`, `Project`, …) |
| Database | Where data lives permanently. Here: **PostgreSQL** |
| Query | One question to the database (SQL), e.g. "give me all tasks of project 1" |
| Transaction | A group of database changes that succeed together or are all undone |
| Lock | A way to say "wait, I'm changing this row" so two requests don't clash |
| Service | Our function that changes data and checks the rules |
| Selector | Our function that only reads data |

---

## 3. The big picture

```
 Browser (HTML pages)          App / script (JSON API)
          \                           /
           \  session cookie          /  JWT token
            v                        v
      +------------------------------------+
      |              Django                |
      |  urls  ->  views  ->  permissions  |
      |                 \                  |
      |          services / selectors      |
      +------------------|-----------------+
                         v
                   PostgreSQL
```

### Folder tour

| Folder | What lives there |
|---|---|
| `config/` | Settings (`config/settings.py`) and the list of URLs (`config/urls.py`) |
| `accounts/` | Users, register, login, logout |
| `projects/` | Projects, members, roles, the **permission module**, the dashboard |
| `tasks/` | Tasks, labels, board, workflow, dependencies, comments, mentions, time tracking, reports |
| `sprints/` | Sprints, backlog planning, burndown, velocity |
| `activity/` | Activity log (history) and notifications |
| `core/` | Shared tools: clock, error handling, pagination, demo data, test helpers |
| `templates/` | HTML pages |
| `static/` | CSS and JavaScript (drag-and-drop, charts) |

Inside an app the files always have the same jobs:

| File (in every app) | Job |
|---|---|
| models.py | Tables |
| services.py (and *_services.py) | Writes + business rules |
| selectors.py (and *_selectors.py) | Reads |
| api_views.py + serializers.py + urls.py | JSON API |
| views.py + forms.py + urls_web.py | HTML pages |
| tests/ folder | Automatic tests |

For example, the task app has `tasks/models.py`, `tasks/services.py`, `tasks/selectors.py` and so on.

**The golden rule:** views are thin. They never contain business rules. Rules live in services, and
the permission decision lives only in `projects/permissions.py`.

---

## 4. Follow one request: "create a task"

Let's follow what happens when Meera clicks **New task → Create** on the website.

1. **URL** `/projects/1/tasks/new/` is matched in `tasks/urls_web.py` to the view
   `tasks/views.py::task_create`.
2. **Who are you?** `@login_required` makes sure Meera is logged in (session cookie).
3. **Are you in this project?** `projects/permissions.py::get_membership_or_404` looks up Meera's
   membership in project 1. If she is not a member, she gets **404** ("not found"), so she can't even
   learn the project exists.
4. **May you create tasks?** `projects/permissions.py::require_permission` checks her role. A viewer
   would get **403**.
5. **Is the form filled correctly?** `tasks/forms.py::TaskForm` checks the shape (title present,
   points 0–100, …).
6. **Do the work:** `core/web.py::run_service` calls `tasks/services.py::create_task`, which:
   - starts a **transaction**,
   - **locks** the project row (so two people creating tasks at once get different numbers),
   - adds 1 to `task_counter` and uses it as the task number,
   - inserts the task,
   - writes an **activity log** row,
   - notifies the assignee if it is someone else.
7. If a rule is broken, the service raises an error and `run_service` shows it as a red message.
8. Success → redirect to the new task's page.

The **API** version (`POST /api/projects/1/tasks/`) goes through `tasks/api_views.py::TaskListCreateView`,
uses `tasks/serializers.py::TaskCreateSerializer` instead of the form, and calls the **same**
`tasks/services.py::create_task`. That is why both behave identically.

---

## 5. The data (tables) in one picture

```
User ──< ProjectMember >── Project ──< Task >── Label (many-to-many)
                              │          │
                              │          ├──< Comment
                              │          ├──< TimeEntry
                              │          └──< TaskDependency (blocker → blocked)
                              ├──< Sprint ──< SprintScopeChange
                              ├──< ActivityLog
                              └──< Notification >── User
```
(`──<` means "one to many".)

A tiny example:
- Project `WEB` with members Olivia (owner), Manoj (manager), Meera (member), Vikram (viewer).
- Tasks `WEB-1 Design login` (done) and `WEB-2 Build login API` (in progress); WEB-1 **blocks** WEB-2.
- Sprint 1 (active) contains both tasks.

The database itself protects important rules with **constraints**, for example: task numbers are
unique per project, a task can't block itself, only one sprint per project can be active.
Full list: `docs/DATABASE.md`.

---

## 6. Every feature: what, rule, where, try it

### 6.1 Accounts and login
- **What:** register, log in, log out. Pages use a session cookie; the API uses JWT tokens.
- **Rules:** emails are unique (ignoring upper/lower case); strong passwords; too many failed logins
  from one address are blocked for 15 minutes.
- **Code:** `accounts/services.py::register_user`, `accounts/views.py::ThrottledLoginView`,
  `accounts/api_views.py::LogoutView`.
- **Try:** register a new user at `/accounts/register/`.

### 6.2 Projects and roles
- **What:** create a project (you become owner), add members by email, change roles, archive.
- **Rules (who can do what):**

| Role | Can do |
|---|---|
| viewer | Look at everything, change nothing |
| member | Create/edit/move tasks, comment, log time, add dependencies, delete own tasks |
| manager | Everything a member can + sprints, labels, members (but never touch owners) |
| owner | Everything + archive/delete project, transfer ownership |

  A project always keeps at least one owner. Archived projects are read-only.
- **Code:** `projects/permissions.py` (the table is `ROLE_PERMISSIONS`), `projects/services.py::change_member_role`.
- **Try:** as `demo_manager`, open **Members** and try to change `demo_owner`'s role → "not allowed".

### 6.3 Tasks and numbers
- **What:** each task gets the next number of its project: WEB-1, WEB-2, …
- **Rule:** never two tasks with the same number, even if 20 people click "Create" at the same moment.
- **How:** the project row is locked while the counter goes up (`tasks/services.py::create_task`).
- **Try:** create two tasks and look at their keys.

### 6.4 Editing safely (version conflict)
- **What:** if two people edit the same task, the second save is **refused** instead of silently
  erasing the first person's work.
- **How:** every task has a `version` number. A save says "I edited version 3". If the database
  already has version 4, the save fails with **409 Conflict** and the page shows the latest data
  (`tasks/services.py::_apply_versioned_update`).
- **Try:** open the same task's **Edit** page in two browser tabs, save in tab 1, then save in tab 2
  → "Someone else changed this task…".

### 6.5 Workflow and the board
- **What:** drag cards between columns.
- **Rules:** move forward one step at a time (To do → In progress, not To do → Done); move back
  freely; "In progress" needs an assignee; "Done" is refused while a blocking task is still open.
- **Code:** `tasks/workflow.py::validate_transition`, `tasks/services.py::move_task`,
  browser part in `static/js/board.js`.
- **How the board works:** the browser moves the card immediately, sends the move to the server,
  and if the server says no, the card jumps back and the reason is shown.
- **Try:** drag a "To do" card straight to "Done" → it jumps back.

### 6.6 Dependencies
- **What:** "WEB-1 blocks WEB-2" means WEB-2 can't be done until WEB-1 is done.
- **Rules:** same project only, no task blocks itself, no duplicates, and **no loops**
  (WEB-1 blocks WEB-2 blocks WEB-3 blocks WEB-1 would mean nothing can ever finish).
- **How loops are found:** before adding "A blocks B", we walk the existing arrows starting from B.
  If we can reach A, the new arrow would close a loop, so it is refused
  (`tasks/dependency_services.py::would_create_cycle`).
- **Try:** on DEMO-1's page, add "Blocked by 3" → refused, because DEMO-1 → DEMO-2 → DEMO-3 already exists.

### 6.7 Sprints and backlog
- **What:** the backlog is the to-do pile; a sprint is a time box you plan tasks into.
- **Rules:** only one active sprint per project; starting a sprint records the **committed points**
  (sum of story points); completing it asks what to do with unfinished tasks (back to backlog or into
  the next planned sprint) and records the **completed points**.
- **Code:** `sprints/services.py::start_sprint`, `sprints/services.py::complete_sprint`.
- **Try:** as `demo_manager`, open **Backlog & sprints**, plan a task into Sprint 1.

### 6.8 Burndown and velocity
- **Burndown:** a line chart of "how many points are still left" for each day of the sprint, next to
  an "ideal" straight line from the committed points down to zero.

  `remaining(day) = committed + added so far − removed so far − completed so far`

  Example: committed 10; 3 points done on Tuesday, 5 on Thursday → 10, 7, 7, 2, 2.
- **Velocity:** points completed per sprint; the average of the last 3 sprints helps plan the next one.
- **Code:** `sprints/selectors.py::burndown`, `sprints/selectors.py::velocity`, chart in `static/js/charts.js`.
- **Try:** open Sprint 1 to see both charts.

### 6.9 Comments, mentions, notifications
- **What:** comment on tasks; `@username` notifies that person; the bell badge shows unread notifications.
- **Rules:** only project members are notified; never yourself; one notification per comment per person;
  deleted comments show "[deleted]".
- **Due-soon reminders:** `manage.py notify_due_tasks` reminds assignees of tasks due today or tomorrow.
  Running it twice does not create duplicates.
- **Code:** `tasks/comment_services.py::create_comment`, `tasks/mentions.py::mentioned_members`,
  `activity/services.py::create_due_soon_notifications`.
- **Try:** as `demo_member` comment `@demo_manager hello`, then log in as `demo_manager` → badge.

### 6.10 Time tracking and reports
- **What:** log minutes on a task; see time per task (vs estimate), per person, per sprint, and workload.
- **Rules:** 1 to 1440 minutes per entry; at most 1440 minutes per person per day (all projects
  together); no future dates; viewers can't log time.
- **Code:** `tasks/time_services.py::log_time`, `tasks/report_selectors.py::time_by_task`.
- **Try:** log 30 minutes on a task, then open the **Time** tab.

### 6.11 Dashboard, my tasks, activity
- **Dashboard:** counts per status, overdue tasks, sprint progress, average velocity, workload
  (`projects/dashboard_selectors.py::project_dashboard`).
- **My tasks:** everything assigned to you across your projects (`tasks/selectors.py::my_tasks`).
- **Activity:** the history of everything that changed (`activity/selectors.py::project_activity`).

### 6.12 The API
- Log in: `POST /api/auth/login/` with username and password → `access` and `refresh` tokens.
- Send `Authorization: Bearer <access>` with every request.
- Try everything in Swagger at `/api/docs/`.
- Every error looks the same: `{"code": "version_conflict", "detail": "…"}`
  (`core/exceptions.py::api_exception_handler`).

---

## 7. Concurrency explained simply

Imagine a cinema with one ticket counter and two cashiers. If both look at "last ticket number = 11"
at the same moment, both might print ticket 12. To stop that, one cashier must **hold the ticket book**
while printing. In PMS:

- **Holding the book = row lock.** Before changing a project, a service runs
  `SELECT … FOR UPDATE` on the project row (`projects/services.py::lock_project_and_recheck`).
  A second request waits a few milliseconds until the first one finishes.
- **Version numbers = "are you looking at the latest copy?"** When saving a task we check the
  version did not change since we read it; if it did, we refuse instead of overwriting.
- **Database rules = the safety net.** Even if code had a bug, PostgreSQL refuses duplicates
  (two task numbers, two active sprints, two reminders for the same day).

How we **prove** it: tests start several real threads at the same instant (a `threading.Barrier`)
and check the result, e.g. 20 parallel creations give exactly numbers 1..20
(`tasks/tests/test_concurrency.py`). The whole race suite ran 20 times in a row without a failure.

---

## 8. Security in plain words

- **Who you are:** session cookie for pages, JWT for the API; passwords are hashed by Django.
- **What you may do:** one permission module; outsiders always get 404.
- **Guessing ids (IDOR):** every lookup is limited to the project in the URL; a test tries every
  URL as an outsider (`core/tests/test_full_suite.py`, `core/tests/test_security.py`).
- **Injected HTML/JS (XSS):** Django templates escape all text; a test fails if anyone disables it.
- **Forged forms (CSRF):** every form and the drag-and-drop call send a secret CSRF token.
- **Brute force:** login attempts are rate-limited.
- **Production:** HTTPS redirect, secure cookies, HSTS (see `config/settings.py`).

---

## 9. Tests: how to run and read them

```powershell
.\venv\Scripts\python.exe manage.py test --noinput                    # all 270+ tests
.\venv\Scripts\python.exe manage.py test tasks --noinput              # one app
.\venv\Scripts\python.exe manage.py test --tag concurrency --noinput  # only the race tests
```

Reading a test: the name says what it proves, and the first line (docstring) explains it.

```python
def test_stale_version_returns_409_with_current_task(self):
    """Second editor with an old version gets 409 and the current data."""
```

Useful helpers in `core/testing.py`: `ProjectWithRolesMixin` creates a project with one user per
role, so a test can say `self.client_for(self.viewer)`.

---

## 10. Recipe: how to add a new rule

Example: "tasks with priority *urgent* must have a due date".

1. **Write the rule in the service**, not in the view: in `tasks/services.py::create_task` (and the
   update path in `tasks/services.py::_build_update_fields`), raise
   `ValidationError({"due_date": ["Urgent tasks need a due date."]})` when the rule is broken.
2. **Nothing to change in views**: the API turns the error into a 400 (`core/exceptions.py`), and the
   page shows it as a message (`core/web.py::run_service`).
3. **Add a test** in `tasks/tests/test_tasks_api.py` with a full-sentence name, e.g.
   `test_urgent_task_without_due_date_is_rejected`.
4. **Document it** in `docs/LOGIC.md`.
5. Run `ruff check .` and the tests.

---

## 11. Where to go next

1. `docs/WALKTHROUGH.md`: the main flows with sequence diagrams.
2. `docs/LOGIC.md`: every rule with worked examples.
3. `docs/CONCURRENCY.md`: locks and race tests.
4. Then read `tasks/services.py` top to bottom: it is the heart of the project.
