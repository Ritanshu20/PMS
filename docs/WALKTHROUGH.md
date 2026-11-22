# WALKTHROUGH: the key flows, step by step

Each flow goes **URL → view → permission check → service → database**, with the real file and
function at every step.

## 1. Create a task and move it on the board

1. Browser posts the "New task" form to `/projects/<id>/tasks/new/`
   (`tasks/urls_web.py` → `tasks/views.py::task_create`).
2. The view gets the membership (`projects/permissions.py::get_membership_or_404`, 404 for outsiders)
   and checks `CREATE_TASK` (`projects/permissions.py::require_permission`).
3. `tasks/forms.py::TaskForm` checks the input shape; then `core/web.py::run_service` calls
   `tasks/services.py::create_task`.
4. `create_task` opens a transaction, locks the project row
   (`projects/services.py::lock_project_and_recheck`), increments `task_counter`, inserts the task,
   writes an `ActivityLog` row (`activity/services.py::log_activity`) and, if someone else was
   assigned, a notification.
5. Later the user drags the card. `static/js/board.js` posts JSON `{status, position, version}` to
   `/projects/<id>/tasks/<n>/move/` (`tasks/views.py::board_move`).
6. `tasks/services.py::move_task` locks the project, compares `version`, asks
   `tasks/workflow.py::validate_transition`, renumbers the column and saves with
   `tasks/services.py::_apply_versioned_update`. The JSON reply carries the new version; on an error
   the card snaps back.

```mermaid
sequenceDiagram
    participant B as Browser (board.js)
    participant V as tasks/views.py::board_move
    participant P as projects/permissions.py
    participant S as tasks/services.py::move_task
    participant W as tasks/workflow.py
    participant DB as PostgreSQL
    B->>V: POST /projects/1/tasks/3/move/ {status, position, version}
    V->>P: get_membership_or_404(user, 1)
    V->>S: move_task(...)
    S->>DB: SELECT project FOR UPDATE
    S->>W: validate_transition(task, "in_progress")
    S->>DB: UPDATE positions; UPDATE task ... WHERE version = v
    S->>DB: INSERT activity_log; COMMIT
    V-->>B: 200 {"version": v+1}  (or 400/409 -> card snaps back)
```

## 2. Add a dependency

1. API: `POST /api/projects/<id>/tasks/<n>/dependencies/ {"blocker_number": 3}`
   (`tasks/api_views.py::DependencyCreateView`). Page: the "Blocked by #" form on the task page
   (`tasks/action_views.py::dependency_add`).
2. `tasks/dependency_services.py::add_dependency` locks the project and loads both tasks inside that
   project (so another project's task is a 404).
3. `tasks/dependency_services.py::_validate_new_edge` refuses self, duplicate, "done task with open
   blocker", and cycles using `tasks/dependency_services.py::would_create_cycle` on the graph from
   `tasks/selectors.py::dependency_graph`.
4. The edge is inserted with an activity row. Later, `tasks/workflow.py::validate_transition` uses
   `tasks/selectors.py::open_blockers` to refuse "done" while a blocker is open.

```mermaid
sequenceDiagram
    participant C as Client
    participant V as DependencyCreateView
    participant S as dependency_services.add_dependency
    participant DB as PostgreSQL
    C->>V: POST {"blocker_number": 3}
    V->>S: add_dependency(membership, 3, 5)
    S->>DB: SELECT project FOR UPDATE
    S->>DB: SELECT all edges of the project
    S->>S: BFS from 5: can it reach 3?
    alt reaches 3
        S-->>C: 400 dependency_cycle
    else
        S->>DB: INSERT dependency + activity; COMMIT
        S-->>C: 201 task with blocked_by
    end
```

## 3. Run a sprint

1. Create: `sprints/views.py::sprint_create` → `sprints/services.py::create_sprint` (dates checked).
2. Plan: `sprints/views.py::sprint_add_task` → `sprints/services.py::add_task_to_sprint`
   (one sprint per task; if the sprint is active, `sprints/services.py::record_scope_change`).
3. Start: `sprints/views.py::sprint_start` → `sprints/services.py::start_sprint`: project lock,
   "no other active sprint", `committed_points`, save (the partial unique index is the backup).
4. Watch: `sprints/views.py::sprint_detail` reads `sprints/selectors.py::burndown` and
   `sprints/selectors.py::velocity`; `static/js/charts.js` draws them.
5. Complete: `sprints/views.py::sprint_complete` → `sprints/services.py::complete_sprint`: computes
   `completed_points`, moves unfinished tasks to the backlog or a planned sprint, one transaction.

```mermaid
sequenceDiagram
    participant M as Manager
    participant S as sprints/services.py
    participant DB as PostgreSQL
    M->>S: start_sprint(sprint 7)
    S->>DB: SELECT project FOR UPDATE
    S->>DB: any active sprint? -> no
    S->>DB: SUM(story_points) -> committed_points
    S->>DB: UPDATE sprint SET status='active' (partial unique index checks)
    M->>S: complete_sprint(7, "backlog")
    S->>DB: SUM(points of done tasks) -> completed_points
    S->>DB: UPDATE tasks SET sprint=NULL WHERE not done
    S->>DB: UPDATE sprint SET status='completed'; COMMIT
```

## 4. Mention → notification

1. Comment form posts to `/projects/<id>/tasks/<n>/comments/` (`tasks/action_views.py::comment_create`),
   or the API `POST .../comments/` (`tasks/comment_api_views.py::CommentListCreateView`).
2. `tasks/comment_services.py::create_comment` checks `COMMENT`, saves the comment and logs activity.
3. `tasks/mentions.py::mentioned_members` extracts `@names` (`tasks/mentions.py::extract_usernames`)
   and keeps only project members, excluding the author.
4. `activity/services.py::notify` creates one `mentioned` notification per person.
5. The navbar badge comes from `core/context_processors.py::unread_notifications`; the list is
   `activity/views.py::notifications_page`; "Open" (`activity/views.py::notification_open`) marks it
   read and goes to the task.

```mermaid
sequenceDiagram
    participant U as Meera
    participant CS as comment_services.create_comment
    participant MN as mentions.mentioned_members
    participant N as activity.services.notify
    participant DB as PostgreSQL
    U->>CS: "@manoj please review"
    CS->>DB: INSERT comment, activity
    CS->>MN: find members named in the text
    MN->>DB: members WHERE lower(username) IN (...)
    CS->>N: notify(manoj, "mentioned")
    N->>DB: INSERT notification; COMMIT
```

## 5. Due-soon reminders

`manage.py notify_due_tasks` (`activity/management/commands/notify_due_tasks.py`) calls
`activity/services.py::create_due_soon_notifications`, which finds open assigned tasks due today or
tomorrow (project timezone) and uses `get_or_create` per (user, task, day). Running it twice changes
nothing.
