# LOGIC: business rules with worked examples

## 1. Permission matrix (`projects/permissions.py::ROLE_PERMISSIONS`)

| Action | viewer | member | manager | owner |
|---|---|---|---|---|
| See project, tasks, comments, activity, reports | yes | yes | yes | yes |
| Create/edit tasks, change status, comment, log own time, manage dependencies | no | yes | yes | yes |
| Delete tasks | no | only own (reporter) | any | any |
| Delete any comment / any time entry | no | no | yes | yes |
| Manage sprints and labels, edit project name/description | no | no | yes | yes |
| Manage members and roles | no | no | yes (never owners) | yes |
| Archive/delete project, transfer ownership | no | no | no | yes |

- Non-member → `Http404` from `projects/permissions.py::get_membership_or_404` (same answer as a missing project).
- Member without the right → `PermissionDenied` (403) from `projects/permissions.py::require_permission`.
- Archived project → only viewing and deleting it (`ARCHIVED_ALLOWED_ACTIONS`).
- Managers can't touch owners or create owners: `projects/permissions.py::can_manage_member`.
- Last owner: `projects/services.py::_ensure_another_owner_exists`, run while the project row is locked.

Example: Meera (member) created WEB-1, Olivia (owner) created WEB-2.
Meera deletes WEB-2 → 403; deletes WEB-1 → 204; Manoj (manager) deletes WEB-2 → 204.

## 2. Task numbering (`tasks/services.py::create_task`)
1. Lock the project row (`SELECT ... FOR UPDATE`).
2. `task_counter += 1`; the new task gets `number = task_counter`.
3. If anything fails later, the transaction rolls back the counter too: no gaps.

Example: counter 11 → new task WEB-12, counter 12.

## 3. Optimistic locking (`tasks/services.py::update_task`, `tasks/services.py::move_task`)
```
UPDATE tasks_task SET ..., version = version + 1 WHERE id = 5 AND version = 3
```
1 row → saved. 0 rows → `VersionConflict` → **409** with `details.current` (the latest task).

Example: Asha and Ravi load WEB-5 at version 3. Asha saves → version 4. Ravi saves with 3 → 409.

## 4. Workflow (`tasks/workflow.py`)
`backlog → todo → in_progress → in_review → done`; forward one step, backward any step.

| from \ to | backlog | todo | in_progress | in_review | done |
|---|---|---|---|---|---|
| backlog | - | ✔ | ✘ | ✘ | ✘ |
| todo | ✔ | - | ✔ | ✘ | ✘ |
| in_progress | ✔ | ✔ | - | ✔ | ✘ |
| in_review | ✔ | ✔ | ✔ | - | ✔ |
| done | ✔ | ✔ | ✔ | ✔ | - |

- `in_progress` needs an assignee (may be set in the same request).
- `done` is refused while any blocker is open: `"Blocked by WEB-1, WEB-3."` (code `blocked`).
- Entering done sets `completed_at = clock.now()`; leaving done clears it (also a DB CheckConstraint).
- Assignee must be owner/manager/member of the project (`is_assignable`).
- Assigning someone else creates an `assigned` notification; assigning yourself does not.

## 5. Board positions (`tasks/services.py::move_task`)
Take the target column's cards in order (without the moved card), insert at
`min(position, len)`, write positions 0..n-1. Example: todo = [A0, B1, C2]; move D to 1 → [A0, D1, B2, C3].
A status change through the edit form puts the card at the end of the new column.

## 6. Dependencies (`tasks/dependency_services.py`)
- Same project only (both numbers are looked up in the actor's project), no self, no duplicate,
  no open blocker on a done task.
- No cycles: `would_create_cycle(graph, blocker, blocked)` runs a breadth-first search from
  `blocked`; reaching `blocker` means the new edge would close a loop.

Example: edges 1→2, 2→3. Add 3→1: search from 1 reaches 2, then 3 → **rejected**. Add 1→3: search from 3 reaches nothing → allowed.

## 7. Sprints (`sprints/services.py`)
- `start_date <= end_date` (service + DB CheckConstraint).
- One active sprint per project: checked under the project lock, and the partial unique index
  `one_active_sprint_per_project` refuses a second active row.
- Start: `committed_points` = sum of the sprint's story points (null = 0). Example: 3 + 5 + null = 8.
- Complete: `completed_points` = points of done tasks; unfinished tasks go to the backlog
  (`sprint = None`) or to a chosen **planned** sprint, all in one transaction.
- A task is in at most one sprint; done tasks cannot be planned.
- While a sprint is active, every scope change is stored in `SprintScopeChange`:
  task added (+points), removed (−points), deleted (−points), re-estimated (± difference).

## 8. Burndown (`sprints/selectors.py::burndown`)
For each day D from `start_date` to `min(today, end_date)` (days in Asia/Kolkata):

```
remaining(D) = committed_points
             + points added on or before D
             - points removed on or before D
             - points of sprint tasks completed on or before D
ideal(D)     = committed_points × (end_date − D) / (end_date − start_date)     (0 for a 1-day sprint)
```
Future days get `remaining = None` but still have an ideal value.

Worked example: Mon 2 – Fri 6 March, committed 10 (A=3, B=5, C=2); A done Tue, B done Thu.

| Day | Mon | Tue | Wed | Thu | Fri |
|---|---|---|---|---|---|
| remaining | 10 | 7 | 7 | 2 | 2 |
| ideal | 10 | 7.5 | 5 | 2.5 | 0 |

If 4 points are added on Wednesday, remaining becomes 10, 7, 11, 6, 6.
Timezone: a task done at 18:40 UTC on Monday is 00:10 IST Tuesday, so it counts on Tuesday.

## 9. Velocity (`sprints/selectors.py::velocity`)
Velocity of a sprint = its `completed_points`. Average velocity = mean of the last 3 completed
sprints, 0 when there are none. Example: 20, 8, 5, 6 (oldest first) → (8 + 5 + 6) / 3 = 6.33.

## 10. Mentions (`tasks/mentions.py`, `tasks/comment_services.py`)
- `@username` is found with a regex that skips the `@` inside emails (`a@b.com`).
- Only project members are notified (case-insensitive), never the author, once per comment per user.
- Unknown names are ignored silently, so nothing reveals whether a username exists.
- Editing notifies only people newly mentioned. Comments are always rendered autoescaped.

Example: Meera writes "@manoj and @oscar, thanks @manoj" → Manoj gets one notification; Oscar (not a member) gets none.

## 11. Notifications (`activity/services.py`)
- Types: `assigned`, `mentioned`, `due_soon`. In-app only.
- `create_due_soon_notifications` (command `notify_due_tasks`): open, assigned tasks in active
  projects with `today <= due_date <= local date of (now + 24 h)`. One per task per user per day,
  guaranteed by the partial unique constraint `one_due_soon_per_task_user_day`.

Example (now = 10 March 15:00 IST): due 10 or 11 March → reminded; 9 March (overdue) or 12 March → not.

## 12. Time tracking (`tasks/time_services.py`)
- Integer minutes 1..1440 per entry, no future dates.
- A user's total per day across all projects ≤ 1440 (the user row is locked while checking).
- Viewers cannot log time; users edit/delete only their own entries; managers/owners may delete any.

Example: 600 + 840 = 1440 is fine; one more minute (even in another project) → `daily_limit`.

## 13. Reports and dashboard
- `tasks/report_selectors.py::time_by_task`: estimate vs logged; `percent_of_estimate = logged × 100 / estimate`,
  `None` without an estimate (no division by zero). Example: estimate 120, logged 150 → 125%.
- `tasks/report_selectors.py::workload`: open (not done) tasks and story points per assignable member.
- `projects/dashboard_selectors.py::project_dashboard`: tasks per status + overdue (one query),
  active sprint progress (`done_points × 100 / committed_points`, 0 if committed is 0), average velocity, workload.

## 14. Task list filters (`tasks/selectors.py::list_tasks`)
`status`, `priority` (comma lists), `assignee` (`me` / `none` / id), `sprint` (`none` / id), `label`,
`due_before`, `due_after`, `overdue`, `q` (title/description or a key like `WEB-12`), `ordering`
(`priority` follows urgent > high > medium > low, not the alphabet).
