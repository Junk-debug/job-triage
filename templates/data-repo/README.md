# Job triage — my data

Private data for the `job-triage` code repository. The code finds this folder
through `JOB_TRIAGE_DATA_DIR`, a `.data-dir` file in the code repo, or a sibling
folder named `<code repo>-data`.

| Path | What it is |
|---|---|
| `criteria.md` | my rules and every decision that set a precedent |
| `linkedin/filters.json` | the saved LinkedIn search |
| `linkedin/ledger.json` | every LinkedIn job already judged, with verdict and reason |
| `linkedin/session.json` | LinkedIn cookies for save and dismiss (git-ignored, optional) |
| `triage/` | one report per run |

Keep this repository private. It holds my salary floor, my company decisions
and a record of every job I rejected.
