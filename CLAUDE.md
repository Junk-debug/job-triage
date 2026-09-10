# Job filtering

Working directory for the candidate's job search on onetap.work, accessed
through the `onetap` MCP server.

## What is here

- `.claude/skills/job-triage/SKILL.md` — the triage procedure, covering every
  source in one run. Invoke with `/job-triage`, or just ask to check new
  vacancies.
- `.claude/skills/job-triage/criteria.md` — what the candidate accepts and
  rejects, why, and every case already adjudicated. **Read this before judging
  any vacancy**, including a one-off question about a single posting, and
  whichever source it came from.
- `.claude/skills/job-triage/triage.py` — merges every source into one batch,
  deduplicates across them, and routes the verdicts back to the right call.
- `.claude/skills/job-triage/sources/` — one adapter per source, split into a
  read half and a write half. `linkedin.py` pulls postings from LinkedIn's
  public `jobs-guest` endpoints (no login, stdlib only); `linkedin_actions.py`
  bookmarks and dismisses jobs on LinkedIn itself and is the only piece that
  needs a logged-in session.
- `linkedin/` — the LinkedIn data, kept in the repo rather than under
  `.claude/` because the candidate reads and edits it:
  - `filters.json` — the search itself. LinkedIn has no saved search, so this
    file is the equivalent. Edit it, or regenerate it from onetap with
    `linkedin.py sync-filters`.
  - `ledger.json` — every LinkedIn job already judged, with its verdict and
    reason. LinkedIn has no application status of its own, so without this
    every run would re-read everything. It is also the only record of the
    rejections, which are deliberately not written to onetap.
  - `session.json` — the browser cookies the write half needs. Gitignored, and
    the only credential in the project.

## Where the rules live

The candidate's rules live in `criteria.md`; this file does not repeat them.

## Working here

- **Never judge a vacancy from its title — or its `keywords`.** The stack that
  disqualifies a role is usually only in the description body, and the title and
  keyword tags routinely name a technology the body demotes to "podstawy" or
  drops entirely. Judge on the requirements section. Every one of the seven
  wrong verdicts found in the 2026-09-04 re-audit failed this way.
- Every status change gets a note in English naming the reason. Skipping this is
  expensive: 235 of the 245 September rejections had no note, so all of them had
  to be re-read from scratch to audit the batch.
- Roughly 10% of results are duplicates from different sources — deduplicate
  before writing verdicts, and keep the record with more information.
- When the candidate corrects a verdict, update `criteria.md` so the correction
  survives the session.
- One judgement, many sources. A source changes only how a vacancy is fetched
  and how its verdict is written; `criteria.md` decides in every case. Adding a
  source means a `normalise_*` function and a `route` branch in `triage.py`,
  and nothing else.
- Sources run independently or together — `triage.py sources` lists them,
  `merge` takes whichever `--onetap` / `--linkedin` files you have, and
  `route --only <source>` writes one source's verdicts at a time. Run both
  together for a full sweep, though: that is the only way cross-source
  duplicates get collapsed.
- The two sources keep their search in different places. onetap has a real
  saved search on the account (`useSavedFilters: true`); LinkedIn's guest API
  has none at all, so `linkedin/filters.json` **is** the saved filter.
  Keep it in sync with `linkedin.py sync-filters <get_my_filters dump>`.
- LinkedIn's search is a **matrix**, because it has one location box and one
  workplace toggle and so cannot express "remote in Poland OR hybrid/onsite in
  Warsaw or Gdańsk" in one query. Keywords × location groups × time windows are
  crossed and each cell is fired separately; the union is the feed. Check the
  cost with `search --dry-run` before spending it. Each cell pages **to the
  end** — there is no page cap, because the old 3-page one silently hid 374 of
  402 postings on a broad query. A full sweep is ~600-900 requests; narrow it
  with `--keyword`/`--location`, not with `--max-pages`.
- On LinkedIn only **keywords, geoId/location and `f_TPR`** actually bite.
  `f_WT` is *proven ignored* — remote and on-site are mutually exclusive yet
  shared 30 of 50 results, more than the control. `f_E` is unreliable, `f_JT`
  unproven, and the endpoint returns a shifting sample. Measurements are
  recorded in `linkedin/filters.json`; do not re-add those filters because they
  look sensible.
- `workplace` in the LinkedIn config is declared intent, not a filter. It rides
  along as `expected_workplace` so the judge knows what a query aimed at — but
  it is **not evidence**, and Gate 3 still reads the location out of the body.
- LinkedIn's own filters and tags are unreliable — roles tagged remote are
  frequently on-site, senior roles are tagged `Associate`, and salary is absent
  from the guest view entirely. The queries cast wide on purpose and the
  description body decides.
- Deduplicate across sources, not just within one. The same job is often in the
  onetap feed already from another board; the onetap record wins, because it
  carries the application status and the user-facing link.
- Verdicts on LinkedIn jobs go in `linkedin/ledger.json` whichever way
  they fall. Rejections are deliberately not pushed to onetap — the ledger is
  their record, and it is what stops them coming back on the next run.
- A LinkedIn verdict also has to reach LinkedIn: `route --apply-linkedin` saves
  every keeper and **dismisses** every rejection on the site, so the candidate's
  own feed stops showing what was already judged. The ledger only stops the
  script from re-reading a job; only a dismiss stops LinkedIn re-showing it. A
  keeper is therefore written twice — bookmarked on LinkedIn, and created as an
  onetap custom vacancy.
- Reading LinkedIn needs no login; writing to it needs `linkedin/session.json`
  with `li_at` and `jsessionid`. `JSESSIONID` **is** the csrf token, so both
  cookies are required and `li_at` alone gets a 403. Keep the two halves in
  separate modules — a collection run must never fail on an expired login.
- Every dismiss is reversible (`linkedin_actions.py undo-dismiss`), and every
  save (`unsave`). When the candidate corrects a verdict, undo it on LinkedIn
  too, or the correction never reaches the site.
- A gate that cannot be settled from the posting gets verdict `UNDECIDED` with a
  question, not a guess. Undecided items are written nowhere and resurface next
  run. `criteria.md` §5 names City X and City Y as exactly this case.
