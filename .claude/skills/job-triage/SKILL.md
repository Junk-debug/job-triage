---
name: job-triage
description: Triage vacancies from every source — the onetap.work feed and LinkedIn — against the candidate's calibrated criteria. Collects new postings, reads each full description, and records SAVED or NOT_INTERESTED with a reason. Use whenever the user asks to check, filter, review or sort through job postings, to pull jobs from LinkedIn, or to re-check earlier decisions.
---

# Job triage

Sort vacancies into `SAVED` and `NOT_INTERESTED` the way the candidate would,
and record why.

Read `criteria.md` in this skill directory before making any verdict. It holds
the rules, the precedents, and the reasoning behind both. This file is the
procedure; that file is the judgement.

**One judgement, many sources.** `criteria.md` decides regardless of where a
posting came from. What differs per source is only how vacancies are fetched
and how a verdict is recorded, and `triage.py` absorbs both so the middle of
the run — reading and judging — is identical.

| Source | Fetched with | "Already judged" is tracked by | Verdict written with |
|---|---|---|---|
| onetap.work | `search_vacancies` (MCP) | server-side application status | `bulk_update_applications` |
| LinkedIn | `sources/linkedin.py` | `linkedin/ledger.json` | `sources/linkedin_actions.py` + `create_custom_vacancy` + ledger |

A LinkedIn verdict lands in **three** places, and all three matter:

| Verdict | On LinkedIn | On onetap | In the ledger |
|---|---|---|---|
| `SAVED` | bookmarked | `create_custom_vacancy` | recorded |
| `NOT_INTERESTED` | **dismissed** — stops coming back in search | nothing | recorded |
| `UNDECIDED` | nothing | nothing | nothing — it resurfaces next run |
| `UNREADABLE` | nothing — the posting is already gone | nothing | recorded |

LinkedIn has no pipeline of its own, so survivors are written into onetap as
custom vacancies and live in the normal pipeline from then on. Dismissing a
rejection is what keeps the candidate's own LinkedIn feed usable — the ledger
only stops *this script* from re-reading it, not LinkedIn from re-showing it.

## Running one source, or all of them

Sources are independent. Run whichever you want — the steps are the same, you
just pass fewer flags.

```bash
S=.claude/skills/job-triage
python3 $S/triage.py sources          # what exists, its state, its filters
```

**LinkedIn only:**

```bash
python3 $S/triage.py collect --source linkedin --max-age-days 7 -o /tmp/linkedin.json
python3 $S/triage.py batch --linkedin /tmp/linkedin.json -o /tmp/batch.json
# ... judge, then check the keepers against the pipeline ...
python3 $S/triage.py route /tmp/verdicts.checked.json --batch /tmp/batch.json
```

**onetap only:** save the `search_vacancies` response to `/tmp/onetap.json`, then

```bash
python3 $S/triage.py batch --onetap /tmp/onetap.json -o /tmp/batch.json
```

**Both:** pass both flags to `batch`. Prefer it for a full sweep — the two
feeds overlap, and a job seen twice is judged once per source but caught by the
applications check before either verdict is written.

`route` dispatches on each verdict's `ref`, so a mixed verdicts file needs no
flags. To write one source's verdicts at a time — say, the onetap updates now
and the LinkedIn creates after checking them — add `--only onetap` or
`--only linkedin`.

## How the filters differ

The two sources store their search in completely different places, and this
trips people up.

| | onetap.work | LinkedIn |
|---|---|---|
| Where the filter lives | server-side, on the account | `linkedin/filters.json`, in this repo |
| How it is applied | `search_vacancies` with `useSavedFilters: true` | params on each request |
| How to change it | `update_my_filters`, after confirmation | edit the file, or `sync-filters` |
| Shape | one structured query | a **matrix** of many queries |
| What it filters on | structured metadata onetap parsed | mostly full-text keyword search |

**onetap** has a real saved search: categories, keywords, three workplace groups
each with their own cities and countries, experience levels, employment types,
company size, date posted. Pass `useSavedFilters: true` rather than
transcribing it — a forgotten per-workplace `cities` silently returns worldwide
results.

**LinkedIn has no saved search at all** on the guest API. Job Alerts exist but
live behind a login. So `linkedin/filters.json` *is* the saved filter,
kept in the repo instead of on an account.

### Keep it in sync with onetap

onetap is the source of truth for what the candidate is looking for:

```bash
# save get_my_filters output to /tmp/onetap-filters.json first
python3 $S/sources/linkedin.py sync-filters /tmp/onetap-filters.json --dry-run
python3 $S/sources/linkedin.py sync-filters /tmp/onetap-filters.json
```

It copies over the keywords, the three workplace groups with their cities and
countries, and the date window. It deliberately does **not** copy categories
(LinkedIn has no equivalent), experience levels (its filter is unreliable), or
green/red words — those are judgement, and judgement lives in `criteria.md`.

### Why it is a matrix

LinkedIn has one location box and one workplace toggle, so it cannot express
what onetap expresses in a single query:

> remote anywhere in Poland **OR** hybrid/onsite in Warsaw **OR** Gdańsk

So the search is a cross product — every keyword × every location group × every
time window — and each cell is fired as its own request. The union of their
results is the feed. 9 keywords × 3 location groups is 27 queries; check the
expansion before spending them:

```bash
python3 $S/sources/linkedin.py search --dry-run
```

Narrow a run with `--location remote-poland`, `--keyword React`, or
`--max-pages`, all repeatable.

### Pagination: every query runs to the end

The endpoint serves 10 cards a page and reports no total, so a result set is
finished only when it says so — with a 0-card page. Every page before that
comes back full. There is **no page cap**, and adding one back is a mistake
that hides most of the feed. Measured 2026-09-10, run to the end:

| Query | Postings | Pages | A 3-page cap saw |
|---|---|---|---|
| `React` / Warsaw / 14d | 68 | 9 | 28 |
| `Typescript` / Poland / 14d | 402 | 45 | 28 |

The old `maxPages: 3` default read like a page limit but was really a silent
93% truncation on the broad queries. `--max-pages` still exists for cheap
sampling while iterating; do not use it for a real sweep.

That makes a full uncapped sweep of all 27 queries roughly **600–900 search
requests** at ~1.75s each — on the order of 20–30 minutes before a single
description is fetched. Run `--dry-run` first, and prefer narrowing by
`--keyword` / `--location` over capping pages when a full sweep is too much.

Because the sample drifts between requests, a page can re-serve cards already
held without the set being exhausted. One such page is pushed through; two in
a row ends the query (`STALL_PAGES` in `linkedin.py`).

### What actually works, measured 2026-09-10

The endpoint returns a **shifting sample**, not a stable list: the same query
twice, 50 cards each, shared only 27 of ~47 results. That drift is the control
any filter claim has to beat, so comparing result sets alone proves nothing.

- **`keywords` works** — the endpoint is search-driven. This is the main lever.
- **`geoId` works, precisely.** `105076658` returned 30/30 postings in Warsaw;
  `106191263` returned only Pomorskie. A plain `location` string is just as
  good where no geoId is known — `"Gdansk, Poland"` gave 20/20 in the tri-city.
- **`f_TPR` works.** With `r86400` every result was posted today or yesterday;
  with `r1209600` the oldest was 14 days out and nothing fell outside the
  window; without it the same pages reached back to 2025-06-30.
- **`f_WT` is ignored — proven, not assumed.** `f_WT=2` (remote) and `f_WT=1`
  (on-site) are mutually exclusive, yet over 50 cards each they shared 30
  results, *more* overlap than the control run.
- **`f_E` is unreliable** (`f_E=4` returns `Not Applicable` and `Associate`),
  and **`f_JT` is unproven**.

So the real levers are **keywords + geoId/location + `f_TPR`**. Everything else
stays wide and `criteria.md` filters from the description body.

`workplace` in the config is therefore **declared intent, not a filter**. It
records which onetap workplace group a location group came from and rides along
on every posting as `expected_workplace`, so the judge knows what the query was
aiming at. Because the guest backend ignores it, two location groups differing
*only* by workplace are collapsed into one request rather than fired twice —
and a `voyager` backend, which does honour it, would send them separately.

**`expected_workplace` is not evidence.** A posting found by the `remote-poland`
cell has not been confirmed remote by anyone; Gate 3 still reads the location
out of the body.

## Procedure

### 1. Collect

**onetap.** Call `get_my_filters`, then `search_vacancies` with
`useSavedFilters: true` and `applicationStatus: "NONE"`. Prefer
`useSavedFilters` over transcribing the fields by hand — a forgotten
per-workplace `cities` / `countries` silently returns worldwide results.
Save the response to `/tmp/onetap.json`.

**LinkedIn.** No login, no cookies — `sources/linkedin.py` uses LinkedIn's
public `jobs-guest` endpoints. (Only the *writes* in step 5 need a session.)

```bash
python3 $S/triage.py collect --source linkedin --max-age-days 7 -o /tmp/linkedin.json
```

That runs search and fetch together, skipping the fetch when nothing is new.
Run the two halves separately when you want to inspect what came back first:

```bash
python3 $S/sources/linkedin.py search --max-age-days 7 > /tmp/li-search.json
python3 $S/sources/linkedin.py fetch --from-search /tmp/li-search.json > /tmp/linkedin.json
```

`search` drops everything already in the ledger, and deduplicates across matrix
cells — a posting found by three queries is one record, with `found_by` naming
all three. Narrow a run with `--location`, `--keyword` or `--query`, and check
the cost first with `--dry-run`.

**One LinkedIn run is not complete coverage.** The guest endpoint serves a
shifting sample, not a stable ordered list: two runs of the same query seconds
apart returned 19 and 17 cards with 9 postings the first never showed. A
posting can surface days after it went up. Run daily rather than weekly, and
trust `posted_at` over position.

### 2. Batch

```bash
python3 $S/triage.py batch --onetap /tmp/onetap.json --linkedin /tmp/linkedin.json -o /tmp/batch.json
```

This normalises both shapes into one record type. That is all it does:
everything that came back is judged, including postings that look like each
other.

**Duplicates are not resolved here.** Roughly 10% of results are the same job
from two places, and the temptation is to collapse them now — but at this point
all there is to go on is a company and a title, and a wrong collapse hides a
vacancy the judge never sees. The same question is answered for real after the
gates, in step 6, by asking onetap whether the job is already in the pipeline.
There the answer is a record with an id and a status, and it covers the whole
application history rather than only the postings in this batch.

The cost of waiting is one extra read for a duplicate. The cost of guessing
early is a vacancy lost in silence.

### 3. Read every description

This is the single most important step, and the one every wrong verdict has
failed. Work through `items` in the batch and read `description` in full.

**Never judge from the title — or from `keywords`, or from any source's tags.**
On the calibration day, two roles with perfect titles hid Angular and Java in
the body. All seven wrong verdicts in the 2026-09-04 re-audit failed this way.
See Gate 0 in `criteria.md`.

Per-source traps, all the same trap:

- **onetap `keywords`** are populated by the aggregator and routinely overstate
  a technology the body demotes to "podstawy" or drops entirely.
- **LinkedIn workplace tags** are unreliable — the `f_WT=2` "remote" filter
  returns plenty of on-site Warsaw and City X roles. Gate 3 reads the location
  out of the body. A posting tagged `Warsaw` whose body opens "Location: Remote"
  is remote.
- **LinkedIn seniority tags** are unreliable — `Not Applicable` is common and
  genuine senior roles are tagged `Associate`. Gate 6 reads the stated years out
  of the body.
- **LinkedIn salary** is absent from the guest view, so `salary` is null and
  `salary_hints` carries the money-looking lines from the description instead —
  often a day rate (`1000–1300 PLN/MD B2B` ≈ 21–27k/month at ~21 man-days).
  Normalise per §6. A posting with no salary is not rejected on that basis.

Items marked `unreadable` were pulled or closed between search and fetch. Give
them verdict `UNREADABLE` so they stop coming back; never guess from the card.

### 4. Apply the gates

Gates 1–6 from `criteria.md`, in order. The first failure ends the evaluation
and names the reason. While reading, hold on to the specific line that decides
it — the note needs it.

When a gate genuinely cannot be settled from the posting — `criteria.md` names
City X and City Y under §5 as exactly this — use verdict `UNDECIDED` with a
`question`. Undecided items are written nowhere, so they resurface on the next
run once the candidate has answered. Do not guess and do not silently reject.

### 5. Write the verdicts

One verdicts file for the whole batch, whatever the source. It is not routed as
it stands — step 6 checks the keepers first and produces the file that is:

```json
[
 {"ref": "onetap:cmtsy...", "verdict": "NOT_INTERESTED",
  "reason": "Gate 1 - Angular is the frontend: 'Współtworzenie frontendu w Angular'. Team is 3 Angular devs."},
 {"ref": "linkedin:4438943404", "verdict": "SAVED",
  "reason": "Node.js/TS backend, fully remote from Poland, 4+ years. No salary posted - ask the rate.",
  "keywords": ["Node.js","TypeScript","AWS"], "workplaceType": ["REMOTE"],
  "experienceLevels": ["SENIOR"], "employmentTypes": ["FULL_TIME"]},
 {"ref": "linkedin:4464096634", "verdict": "UNDECIDED",
  "question": "Company H, City X, perfect Node/TS stack. §5 names Company H/City X as unresolved — accept or reject?"}
]
```

### 6. Check the keepers against the pipeline

Before anything is written, every `SAVED` verdict is checked against onetap's
application pipeline: a job already saved, applied to, or rejected earlier must
not enter it a second time.

Use the **`applications-check` skill** — `.claude/skills/applications-check/`.
It reads this verdicts file, asks `get_my_applications` one query per company,
flips whatever is already tracked to `NOT_INTERESTED` with a note naming what
it duplicates, and writes `/tmp/verdicts.checked.json`.

Report every flip to the candidate. A keeper dropped silently is
indistinguishable from a keeper that never existed.

### 7. Route the verdicts

Route the **checked** file, not the raw one:

```bash
python3 $S/triage.py route /tmp/verdicts.checked.json --batch /tmp/batch.json
```

That prints the exact arguments for each call:

- `onetap_bulk_update_applications` → one `bulk_update_applications` call, up to
  50 items. Never loop `update_application_status` over a page.
- `linkedin_create_custom_vacancy` → one `create_custom_vacancy` per entry.
- `linkedin_actions` → the save/dismiss calls to make on LinkedIn itself,
  sent by `--apply-linkedin` (next section).
- `undecided` → report these to the user as questions.

For a LinkedIn save, add to the verdict whatever the posting states —
`keywords`, `workplaceType`, `experienceLevels`, `employmentTypes`, `salary`,
`publishedAt`, and `cityIds` / `countryIds` / `categoryIds` sourced from
`get_my_filters` or `list_cities`. **Never guess an id.** A bare record is not
worth keeping months later. Title, company, link and description are filled in
from the batch automatically.

`link` is unique per user. A repeat answers `VACANCY_LINK_ALREADY_EXISTS` with
`payload.existingVacancyId` — never retry the create, call
`update_custom_vacancy` on that id instead.

#### Act on LinkedIn itself

A verdict that only lives in this repo leaves the candidate's own LinkedIn feed
full of jobs already rejected. `--apply-linkedin` closes that loop: every
`SAVED` job is bookmarked and every `NOT_INTERESTED` job is dismissed.

```bash
# see the calls first — these are writes on a real account
python3 $S/triage.py route /tmp/verdicts.checked.json --batch /tmp/batch.json \
        --apply-linkedin --dry-run

python3 $S/triage.py route /tmp/verdicts.checked.json --batch /tmp/batch.json --apply-linkedin
```

This is the one part of the LinkedIn source that needs a **login**. Set the
session up once:

```bash
python3 $S/sources/linkedin_actions.py check     # says who you are, changes nothing
```

If that fails, write `linkedin/session.json` (gitignored) from DevTools →
Application → Cookies on `linkedin.com` while logged in:

```json
{"li_at": "<li_at cookie>", "jsessionid": "ajax:<digits>"}
```

Both cookies are needed — `JSESSIONID` *is* the csrf token, so a session with
only `li_at` gets a 403. `$LINKEDIN_COOKIE` holding a whole `Cookie:` header
works too and wins over the file.

Single jobs, outside a triage run:

```bash
A=$S/sources/linkedin_actions.py
python3 $A dismiss 4443605886 4455410901   # "not interested", hides them from search
python3 $A undo-dismiss 4443605886         # the undo, when a verdict was wrong
python3 $A save 4455784336
python3 $A unsave 4455784336
```

**`undo-dismiss` is the repair tool.** When the candidate corrects a rejection,
undo the dismissal as well as fixing `criteria.md` and the ledger — otherwise
the job stays hidden on LinkedIn and the correction is invisible where it
matters.

Failures do not stop the run: each call is reported and the ones LinkedIn
accepted are stamped onto the ledger record as `linkedin_action`, so a failed
dismiss stays visible instead of looking done.

#### Then the ledger

Once the writes have gone through, commit the LinkedIn verdicts to the ledger:

```bash
python3 $S/triage.py route /tmp/verdicts.checked.json --batch /tmp/batch.json \
        --apply-linkedin --write-ledger
```

Do this last, and always — it is what makes the next run cheap. Rejections are
recorded in the ledger and deliberately **not** pushed to onetap: several
hundred rejections a week would cost a create call each and bury the real
applications. They *are* dismissed on LinkedIn, which costs nothing and is
where the candidate feels the difference.

### 8. Notes discipline

Every status change gets a note in English naming the gate and quoting the
disqualifying line. Skipping this is expensive: 235 of the 245 September
rejections had no note, so all of them had to be re-read from scratch to audit
the batch.

```
Gate 1 — Angular is the frontend: "Współtworzenie frontendu w Angular,
w zależności od potrzeb projektu". Team is 3 Angular devs.
```

For a save that carries a caveat, say so — the caveat is what makes the
shortlist useful later:

```
Saved. TS/React/Node core, remote, 26.9-30.2k. Caveat: Kubernetes and Neo4j
in the stack, 12-month contract.
```

### 9. Report

- how many were seen, how many were new, saved, rejected, per source
- for LinkedIn, how many were bookmarked and dismissed on the site itself, and
  any call that failed
- how many keepers were already in the pipeline, what each one duplicates, and
  the status it is tracked under — never drop one silently
- the saved ones as a list, each with company, title, salary, work model, and
  the **onetap** link — never the original job board or LinkedIn URL
- every `UNDECIDED` item with the question you want answered
- anything unreadable, and any source that failed outright

### When the candidate corrects a verdict

Update `criteria.md` so the correction survives the session. That file is the
memory of this whole system; a correction that lives only in the conversation
is lost.

For a LinkedIn job, undo the action too, or the correction never reaches the
site: `linkedin_actions.py undo-dismiss <job_id>` for a wrongly rejected one,
`unsave` for a wrongly saved one. Then re-record the verdict so the ledger
agrees.

## Adding a source

`triage.py` is the only place that knows about source shapes. A new source
needs a `normalise_*` function producing the unified record, a branch in
`route` for its write call, and its own "already judged" mechanism. Nothing
about the judgement changes.

Keep reading and writing in separate modules the way LinkedIn does —
`linkedin.py` needs no credentials and `linkedin_actions.py` needs a live
session, and a collection run should never be able to fail on an expired
login.

## When LinkedIn breaks

Undocumented HTML endpoints, changed without notice.

- **`search` returns zero cards for every query** — the search HTML changed, or
  the IP is throttled. Check by hand:
  `curl -A "Mozilla/5.0" "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=React&location=Poland&start=0" | head -50`
- **`fetch` returns `ok: false` for everything** — the description selector
  changed (`show-more-less-html__markup`) or the guest view went behind an auth
  wall. Re-derive the selector from live HTML before touching anything else.
- **Sustained HTTP 429** — the script backs off and retries four times. Beyond
  that, stop and come back later; leave the ledger unwritten so nothing is lost.
- **`--apply-linkedin` exits on 401/403/999** — the session died. `li_at`
  expires and `JSESSIONID` rotates on every logout, so re-copy both cookies.
  Judgements are unaffected: re-run `route` once the session is back.
- **A save or dismiss answers an unexpected status** — the Voyager endpoints are
  undocumented and versioned by nobody. Re-capture them from DevTools (filter on
  `voyagerJobsDashJobPostingRelevanceFeedback` and `voyagerFeedDashSaveStates`)
  rather than guessing at a new shape.

Report a breakage as a breakage, and carry on with onetap. Never fall back to
judging from search-card titles because the descriptions would not load.
