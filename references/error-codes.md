# Error Codes

## Stable error payload

All failures return:

```json
{
  "error_code": "some_error_code",
  "message": "human-readable explanation"
}
```

## Codes

### `max_concurrent_jobs_reached`

- Meaning: the number of active jobs already equals `max_concurrent_jobs`.
- Typical recovery: wait, cancel another running job, or increase the configured limit.

### `job_not_found`

- Meaning: the requested `job_id` does not exist under the runtime root.
- Typical recovery: verify the job ID and runtime root before retrying.

### `invalid_config`

- Meaning: config input or persisted config structure is invalid.
- Typical recovery: rerun onboarding or fix the invalid config values.

### `invalid_arguments`

- Meaning: CLI arguments or hook stdin did not satisfy the command contract.
- Typical recovery: fix the command shape, required flags, or stdin JSON and retry.

### `invalid_state_transition`

- Meaning: the requested action is not legal from the job's current state.
- Typical recovery: inspect `status` first, then use only the action allowed for that state.

### `hook_job_id_missing`

- Meaning: `hook finalize` did not receive `OPENCLAW_CLAUDE_CODE_JOB_ID`.
- Typical recovery: fix the Claude hook environment so the OpenClaw Claude Code job ID is injected.

### `cancel_handle_missing`

- Meaning: the job has no usable `process_pid` or tmux handle for cancellation.
- Typical recovery: inspect `job.json` and runtime setup; if the process already ended, finalize or query status instead.

### `result_not_ready`

- Meaning: the job has not entered a result-bearing terminal state yet.
- Typical recovery: poll `status`, inspect `logs`, or wait for hook finalization.

### `start_failed`

- Meaning: the bridge runtime failed before it could launch the headless runner or tmux session.
- Typical recovery: verify Python/tmux availability and runtime environment overrides.

### `internal_error`

- Meaning: an unexpected OpenClaw Claude Code runtime failure occurred.
- Typical recovery: inspect runtime files, logs, and the failing command inputs before retrying.
