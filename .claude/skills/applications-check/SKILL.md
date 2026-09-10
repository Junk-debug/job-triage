---
name: applications-check
description: Check vacancies that passed the criteria against onetap's application pipeline before their verdicts are written. Anything already tracked — saved, applied, rejected, or dismissed earlier — is flipped to NOT_INTERESTED as a duplicate and reported to the candidate. Use at the end of a triage run once the gates have been applied, or on its own to answer whether a specific job is already in the pipeline.
---

# Applications check

A keeper is not finished when it passes the gates. It still has to be new.

The same posting reaches a batch again for ordinary reasons: two boards
aggregate it, LinkedIn lists what onetap already carries, or a company re-lists
a role weeks after it was judged. Written through unchecked, it becomes a
second pipeline entry for one job — the candidate applies twice, or re-reads a
role they already rejected in September.

This check runs **after** the gates, not before them, because "is this already
in the pipeline?" is a question only onetap can answer. Guessing it from the
batch — two cards that merely look alike — silently hides a vacancy the judge
never sees. Here the answer is a record with an id and a status.

```
judge -> queries -> get_my_applications -> match -> report -> route
```

## Why it is not a full dump

There are **6000+ applications** on the account, 5700 of them
`NOT_INTERESTED`. Paging all of that per run is out of the question, so the
check is targeted: `get_my_applications` takes a free-text `query` matching
title, company name **and** notes, and one call per company covers every keeper
from that company. A batch with five keepers across four companies costs four
calls.

The search over-returns — a query for `Company E` returns postings whose *notes*
merely mention Company E. `check.py` is what makes it precise.

## Procedure

```bash
C=.claude/skills/applications-check
```

### 1. Ask what to check

```bash
python3 $C/check.py queries --verdicts /tmp/verdicts.json --batch /tmp/batch.json
```

Only `SAVED` verdicts are checked. A rejection is already out, and an
`UNDECIDED` is written nowhere, so neither can be made worse by a duplicate.

The output names one query per company, already collapsed across sources —
LinkedIn's `Company E` and onetap's `Company E Ltd` are one call.

### 2. Run those queries

For each one, call `get_my_applications` with `query: "<query>"` and
`limit: 100`. Do **not** pass a `status` filter: a job the candidate rejected
in September is exactly what this is looking for, and so is one they were
hired for.

Save every response into one JSON object keyed by the query string:

```json
{
  "Company E": { "total": 63, "applications": [ ... ] },
  "Company K": { "total": 4, "applications": [ ... ] }
}
```

If a response's `total` is larger than the number of `applications` it
returned, the pool is truncated and a match can be missed. `match` warns about
this. Re-run that query with the distinctive words of the title added, so the
result set fits.

### 3. Match

```bash
python3 $C/check.py match --verdicts /tmp/verdicts.json --batch /tmp/batch.json \
        --candidates /tmp/candidates.json -o /tmp/verdicts.checked.json
```

Three ways a keeper is judged a duplicate, strongest first:

| Matched on | What it means |
|---|---|
| `link` | the same URL is already tracked |
| `linkedin job id` | the id appears in a tracked vacancy's link or notes — a job saved from LinkedIn on an earlier run |
| `company + title` | normalised company and title are equal |

Cosmetic noise is stripped before comparing: `(f/m/x)`, `remote`, `B2B`,
parenthetical stack glosses, and company suffixes (`Company E Ltd` = `Company E`).

**Seniority words are never stripped.** `Junior Frontend Developer` and
`Senior Frontend Developer` at one company are two postings, and treating them
as one drops a keeper the candidate never learns about. A missed match costs a
duplicate entry the candidate can delete; a false match costs a vacancy.

Each hit is flipped to `NOT_INTERESTED` with a note that names what it
duplicates, its status, and the fact that it *passed* the gates:

```
Duplicate - already in the pipeline as APPLIED since 2026-09-01: Jit Team /
Senior Frontend Developer - https://onetap.work/job/senior-fe-cmtjit1/.
Matched on company + title. Passed the gates on its own merits (...), so this
is a duplicate rather than a rejection.
```

That wording matters. A future audit reading `NOT_INTERESTED` must be able to
tell a duplicate from a rejection on the merits, or the role looks rejected
twice.

### 4. Report every flip

**Always tell the candidate what was dropped and what it duplicates** — this
is not a silent step. For each hit: company, title, the existing status, and
the onetap link of the record it duplicates.

Two cases are worth calling out explicitly rather than listing:

- the existing record is `NOT_INTERESTED` while the fresh posting **passed**
  the gates. Either the earlier verdict was wrong, or the two are different
  roles that share a title. Say so, and offer to re-open it.
- the existing record is `APPLIED`, `INTERVIEW` or later. The candidate is
  already in that process; the duplicate is worth knowing about because it
  usually means the company re-listed the role.

### 5. Route the checked file

Hand `route` the **checked** verdicts, never the raw ones:

```bash
python3 $S/triage.py route /tmp/verdicts.checked.json --batch /tmp/batch.json \
        --apply-linkedin --write-ledger
```

A flipped LinkedIn keeper is then dismissed on LinkedIn and recorded in the
ledger, and no custom vacancy is created for it — which is the whole point.

## When the candidate overrules a flip

The flip is a duplicate call, not a judgement, so nothing goes into
`criteria.md`. Restore it instead:

- on onetap, `update_application_status` back to `SAVED` with a note saying why
  it is not a duplicate
- on LinkedIn, `sources/linkedin_actions.py undo-dismiss <job_id>` and re-record
  the ledger verdict, or the correction never reaches the site

If the same pair is wrongly matched twice, the fix belongs in `check.py` —
usually a word that should not have been stripped.
