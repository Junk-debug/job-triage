# Job filtering

Working directory for the candidate's job search on onetap.work, accessed
through the `onetap` MCP server.

## What is here

- `.claude/skills/job-triage/SKILL.md` — the triage procedure. Invoke with
  `/job-triage`, or just ask to check new vacancies.
- `.claude/skills/job-triage/criteria.md` — what the candidate accepts and
  rejects, why, and every case already adjudicated. **Read this before judging
  any vacancy**, including a one-off question about a single posting.

## Where the rules live

The candidate's rules live in `criteria.md`; this file does not repeat them.

## Working here

- **Never judge a vacancy from its title.** The stack that disqualifies a role is
  usually only in the description body. This is the most common failure mode.
- Every status change gets a note in English naming the reason.
- Roughly 10% of results are duplicates from different sources — deduplicate
  before writing verdicts, and keep the record with more information.
- When the candidate corrects a verdict, update `criteria.md` so the correction
  survives the session.
