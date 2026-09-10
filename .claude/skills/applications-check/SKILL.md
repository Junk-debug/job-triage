---
name: applications-check
description: Find out whether a vacancy is already known — either duplicated inside the current batch, or already in the candidate's onetap pipeline. Collapses the copies of one job that the aggregators return from several boards, keeping the copy with the most information; flips keepers that are already saved, applied to or rejected into NOT_INTERESTED; and raises the ambiguous cases as questions instead of guessing. Use during a triage run, before judging and again before verdicts are written, or on its own to answer whether a specific job is already in the pipeline.
---

# Applications check

Two different questions, one answer each run: **is this job already in the
batch twice, and is it already in the pipeline?**

Neither is a matter of taste. Both have a right answer, and both are cheap to
get wrong in the same expensive way — a vacancy collapsed into another one is
a vacancy the candidate never learns existed. So the rule throughout is:

> Merge what the evidence settles. Report what it does not. Never drop
> anything in silence.

Nothing here is ever deleted. A duplicate keeps its ref, inherits the verdict
of the copy that was judged, and is written back to its own source so the feed
stops serving it.

```
batch -> cluster -> judge the primaries -> queries -> get_my_applications
      -> match -> route
```

---

## Part 1 — duplicates inside the batch

The aggregators are the reason this exists. One posting arrives from Just Join
IT, from Indeed and from the employer's own feed. None of the three is in the
pipeline yet, so the pipeline half cannot catch them — and reading three copies
of one job costs three reads and produces three pipeline entries.

```bash
C=.claude/skills/applications-check
python3 $C/check.py cluster --batch /tmp/batch.json -o /tmp/batch.clustered.json
```

Run it **after `triage.py batch` and before judging**, and judge
`items` from the clustered file.

### What counts as the same job

Measured on 50 live onetap records, 2026-09-10. The metric is word 3-shingle
**containment** — the shared shingles over the *shorter* description, so a
board that publishes a truncated teaser of the same posting still scores high.

| | containment |
|---|---|
| Company A / Company A Ltd, `Senior Frontend Engineer` | **1.000** |
| Company B, `SWE Manager, GCE` — Indeed vs employer feed | **0.990** |
| Company Q, `Angular Developer` — Indeed vs Just Join IT | **0.929** |
| Company D / Company D Ltd, `Software Engineer` — Indeed vs JJIT | **0.926** |
| — | |
| Company C, `Senior Full Stack Developer (Angular & Node.js)` ×2 | 0.595 |
| Company C, `Senior Full-stack (PostgreSQL)` vs `(Python)` | 0.578 |
| Company B, `Senior SWE App Hub` vs `SWE Manager GCE` | 0.516 |
| Company C, `Lead Java` vs `Senior Full Stack Angular` | 0.376 |
| P&P Solutions, `Full Stack` vs `Senior Fullstack` | 0.003 |

Two things follow, and both contradict the obvious rule:

**A shared title does not make one job.** Company C posted *Senior Full Stack
Developer (Angular & Node.js)* twice on one day with the description rewritten
in different words — 0.595. It may well be two openings.

**A high score does not make one job either, below a point.** Company C's
boilerplate alone carries two genuinely different roles to 0.578.

So the auto-merge line sits at **0.85**, clear of every observed different-job
pair, and the band beneath it is reported rather than merged. A merge also
requires the same normalised company (`Company E Ltd` = `Company E`, `Company D Ltd` =
`Company D`) and no seniority conflict — `Junior` and `Senior` at one company are two
jobs whatever the text shares. An identical link merges on sight; a posting
with no description at all (a LinkedIn 404) merges on company plus title,
since it carries nothing the other copy lacks.

### Which copy survives, and what it inherits

The primary is the copy that tells the judge the most, scored on what it
actually carries: salary 40 (Gate 4 turns on it and most postings omit it),
description length up to 30, location 8, workplace 8, experience 8, employment
5, keywords up to 8, published 4, and 3 for being an onetap record — a
tiebreak only, but it carries the application status and the onetap link, which
is the only URL the candidate may be given.

The real Company D pair shows why this matters: both boards had the same job, and only
the Just Join IT copy stated `130–140 PLN/h`. It won 109 to 53.

The primary then absorbs the rest:

- **blank fields are filled** from the other copies (`enriched_from` says which)
- **keyword and salary-hint lists are pooled** — more tags are strictly more
- **fields the copies dispute** are reported on the record as
  `duplicates_disagree_on`, never resolved. One board saying Remote and another
  Warsaw is exactly what Gate 3 needs to see.
- descriptions are never merged. The judge reads one posting, not a collage.

### The two things it refuses to decide

**`suspected_duplicates`** — same company, too close to ignore, not close
enough to merge. Reported as one group per company, not one row per pair:
Company C's five spun postings are nine pairs and one useful line. Judge them all,
and read them as a set — if the gates land differently on two of them, say why
in the notes.

**`same_job_different_company`** — the agency case. Two agencies advertise one
end client's role under their own names, so the company names never match and
the text nearly does. Across 1196 cross-company pairs in the sample the
containment never exceeded 0.060, so anything above **0.35** is extraordinary
and is flagged.

These are **never merged, and never decided by this skill.** Which agency to go
through is the candidate's call, and applying to one end client through two of
them can sink both applications. **Ask the candidate**, quote both companies
and both links, and judge the postings separately until they answer. If they
settle it as a general rule, that rule belongs in `criteria.md`.

---

## Part 2 — is it already in the pipeline?

A keeper is not finished when it passes the gates. It still has to be new: the
candidate may have saved this job last week from the other source, applied to
it a month ago, or rejected it in an earlier batch.

### 1. Ask what to check

```bash
python3 $C/check.py queries --verdicts /tmp/verdicts.json --batch /tmp/batch.clustered.json
```

Only `SAVED` verdicts are checked — a rejection is already out, and an
`UNDECIDED` is written nowhere, so neither can be made worse by a duplicate.
Source makes no difference: an onetap keeper and a LinkedIn keeper are checked
the same way, against the same pipeline.

The output names one query per company, collapsed across sources and spellings
— LinkedIn's `Company E` and onetap's `Company E Ltd` are one call.

### 2. Run those queries

For each, call `get_my_applications` with `query: "<query>"` and `limit: 100`.
Pass **no** `status` filter: a job rejected in September is exactly what this
is looking for, and so is one already applied to.

There are **6000+ applications** on the account, 5700 of them
`NOT_INTERESTED`, so dumping them is not an option — the free-text query is
what makes this cheap. It searches title, company name **and** notes, so it
over-returns; step 3 is what makes it precise.

Save every response into one JSON object keyed by the query string:

```json
{ "Company E": { "total": 63, "applications": [ ... ] } }
```

If a response's `total` exceeds the number of `applications` it returned, the
pool is truncated and a match can be missed. `match` warns about it — re-run
that query with the distinctive words of the title added.

### 3. Match

```bash
python3 $C/check.py match --verdicts /tmp/verdicts.json --batch /tmp/batch.clustered.json \
        --candidates /tmp/candidates.json -o /tmp/verdicts.checked.json
```

This does two things. First it **expands the clusters**: every duplicate
collapsed in part 1 inherits its primary's verdict as `NOT_INTERESTED`, so it
is dismissed on its own source and stops coming back. A duplicate is
`NOT_INTERESTED` even when its primary is saved — one job belongs in the
pipeline once.

Then it matches the keepers against the pipeline, strongest evidence first:

| Matched on | What it means |
|---|---|
| `link` | the same URL is already tracked |
| `linkedin job id` | the id appears in a tracked vacancy's link or notes — saved from LinkedIn on an earlier run |
| `company + title` | normalised company and title are equal |

Each hit is flipped with a note naming what it duplicates, its status, and the
fact that it *passed* the gates:

```
Duplicate - already in the pipeline as APPLIED since 2026-09-01: Jit Team /
Senior Frontend Developer - https://onetap.work/job/senior-fe-cmtjit1/.
Matched on company + title. Passed the gates on its own merits (...), so this
is a duplicate rather than a rejection.
```

That wording matters. A future audit reading `NOT_INTERESTED` must be able to
tell a duplicate from a rejection on the merits, or the role looks twice
rejected.

---

## Reporting — not optional

Every collapse and every flip is reported to the candidate. A keeper dropped
silently is indistinguishable from a keeper that never existed.

- **collapsed in the batch**: the company, the title, how many copies, which
  one was kept and what it gained (`enriched_from`)
- **disputed fields**: where two copies disagreed on salary, location or
  workplace
- **already in the pipeline**: company, title, the existing status, and the
  onetap link of the record it duplicates
- **suspected duplicates**: what was judged separately and why
- **cross-company pairs**: as a question, with both links

Two cases deserve calling out rather than listing:

- the existing record is `NOT_INTERESTED` while the fresh posting **passed**
  the gates. Either the earlier verdict was wrong, or they are different roles
  sharing a title. Say so, and offer to re-open it.
- the existing record is `APPLIED`, `INTERVIEW` or later. The candidate is in
  that process already; the duplicate usually means the role was re-listed.

## Then route

Hand `route` the **checked** verdicts, never the raw ones:

```bash
python3 $S/triage.py route /tmp/verdicts.checked.json --batch /tmp/batch.clustered.json \
        --apply-linkedin --write-ledger
```

## When the candidate overrules

A collapse or a flip is a duplicate call, not a judgement, so nothing goes into
`criteria.md` — except an agency rule, which is a policy and does belong there.
Restore the record instead:

- on onetap, `update_application_status` back to `SAVED` with a note saying why
  it is not a duplicate
- on LinkedIn, `sources/linkedin_actions.py undo-dismiss <job_id>` and re-record
  the ledger verdict, or the correction never reaches the site

If the same pair is wrongly matched twice, the fix belongs in `check.py` — a
threshold, or a word that should not have been stripped. Re-measure before
moving a threshold; the numbers above are what they are because they were
measured, not chosen.
