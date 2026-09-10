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
- `.claude/skills/job-triage/triage.py` — normalises every source into one
  batch to judge, and routes the verdicts back to the right call.
- `.claude/skills/applications-check/SKILL.md` — whether a job is already
  known, in both senses: duplicated inside the batch (it clusters those before
  judging, keeping the copy with the most information) and already in the
  onetap pipeline (it flips those to `NOT_INTERESTED` after the gates). Invoke
  with `/applications-check`; the triage run calls it at steps 3 and 7. The
  measured thresholds live in that file — re-measure before changing one.
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
- Roughly 10% of a batch is one job returned twice — the aggregators serve the
  same posting from Just Join IT, Indeed and the employer feed, and none of the
  copies is in the pipeline yet. They are collapsed before judging, but only on
  evidence: same company, no seniority conflict, and 3-shingle containment of
  the descriptions at or above 0.85. A shared title is **not** enough (Company C
  posted one title twice at 0.595) and neither is a high score alone (Company C's
  boilerplate carries different roles to 0.578). Everything between 0.50 and
  0.85 is reported and judged separately.
- The copy that survives a collapse is the one carrying the most information,
  not the first one seen — salary weighs most, because Gate 4 turns on it and
  most postings omit it. Of the real Company D pair only the Just Join IT copy stated
  the rate. The survivor absorbs the others' fields, and anything they disagree
  on rides along as `duplicates_disagree_on` for Gates 3 and 4 to read.
- Nothing is ever dropped in silence. A collapsed copy keeps its ref, inherits
  its primary's verdict, and is written back to its own source — that is what
  stops the feed serving it again, and it is why a wrong collapse is the
  expensive mistake: the candidate never learns the vacancy existed.
- Two agencies advertising one end client's role under their own names are
  **never** merged — the company names differ and the text nearly matches
  (cross-company containment topped out at 0.060 across 1196 real pairs, so
  anything above 0.35 is flagged). Ask the candidate which agency to go
  through: applying via both can sink both applications. If they settle it as
  a rule, that rule goes in `criteria.md`.
- When the candidate corrects a verdict, update `criteria.md` so the correction
  survives the session.
- One judgement, many sources. A source changes only how a vacancy is fetched
  and how its verdict is written; `criteria.md` decides in every case. Adding a
  source means a `normalise_*` function and a `route` branch in `triage.py`,
  and nothing else.
- Sources run independently or together — `triage.py sources` lists them,
  `batch` takes whichever `--onetap` / `--linkedin` files you have, and
  `route --only <source>` writes one source's verdicts at a time. Prefer a full
  sweep anyway — the feeds overlap, and the applications check collapses what
  the sweep sees twice.
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
- A keeper is not finished when it passes the gates — it still has to be new.
  Every `SAVED` verdict goes through the applications check before it is
  written: the same job is often already in the pipeline, saved from another
  board, applied to weeks ago, or rejected in an earlier batch. A hit becomes
  `NOT_INTERESTED` with a note naming what it duplicates, and **is always
  reported to the candidate** — a keeper dropped silently is indistinguishable
  from one that never existed. There are 6000+ applications on the account, so
  the check queries `get_my_applications` per company; it never dumps them all.
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
