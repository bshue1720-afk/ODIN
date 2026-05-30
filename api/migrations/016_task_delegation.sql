-- Migration 016: Task Delegation
-- Adds assignee, escalation tracking, and auto-executable flag to tasks table.
-- Enables heartbeat to auto-assign, escalate to Eddie/Brock, and auto-run ODIN tasks.

ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS assignee          TEXT DEFAULT 'brock',
  ADD COLUMN IF NOT EXISTS escalated_at      TIMESTAMP WITH TIME ZONE,
  ADD COLUMN IF NOT EXISTS auto_executable   BOOLEAN DEFAULT FALSE;

-- Back-fill existing tasks: assign to brock by default
UPDATE tasks SET assignee = 'brock' WHERE assignee IS NULL;

-- Index for heartbeat scan (overdue tasks by assignee)
CREATE INDEX IF NOT EXISTS idx_tasks_assignee_due
  ON tasks (assignee, due_date, status);
