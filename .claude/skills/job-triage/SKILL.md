---
name: job-triage
description: Triage vacancies on onetap.work against the candidate's calibrated criteria — fetch new postings, read each full description, and set SAVED or NOT_INTERESTED with a reason. Use whenever the user asks to check, filter, review or sort through job postings, or to re-check earlier decisions.
---

# Job triage

Sort onetap.work vacancies into `SAVED` and `NOT_INTERESTED` the way the
candidate would, and record why.

Read `criteria.md` in this skill directory before making any verdict. It holds
the rules, the precedents, and the reasoning behind both. This file is the
procedure; that file is the judgement.

## Procedure

### 1. Load the filters

Call `get_my_filters` and pass **every** field through to `search_vacancies` —
`categoryKeys`, `keywords`, `strictCategoryKeywordMatch`, all three workplace
groups with their `citiesIds` and `countryIsos`, `experienceLevels`,
`employmentTypes`, company size, `datePosted`, `sortBy`, and each
`includeUnspecified*` flag. A field left out is not "kept as saved" — it falls
back to a permissive default and quietly widens the search.

Pass `applicationStatus: "NONE"` so already-decided vacancies do not come back.

### 2. Read every description

`search_vacancies` returns the full `descriptionText`. Read it. Do not decide
from the title.

This is the single most important step. On the calibration day, 40 of 45
rejected React/Node roles had a foreign stack visible in the title — but two
roles with perfect titles hid Angular and Java in the body, and would have been
saved by a title-only pass. See Gate 0 in `criteria.md`.

Work in pages. For each page, decide every vacancy before moving on.

### 3. Apply the gates

Run gates 1–6 from `criteria.md` in order: stack, role type, location, salary,
company, seniority. The first failure ends the evaluation and names the reason.

While reading, hold on to the specific line that decides it — the note needs it.

### 4. Deduplicate

Group the page by company + title. When the same posting appears more than once,
keep the record carrying more information and reject the rest, naming the kept
id in each note. See §10 of `criteria.md`.

### 5. Write the verdicts

One `bulk_update_applications` call per page, up to 50 items, each with its own
note in English. Never loop `update_application_status` over a page.

A note names the gate and quotes the disqualifying line:

```
Gate 1 — Angular is the frontend: "Współtworzenie frontendu w Angular,
w zależności od potrzeb projektu". Team is 3 Angular devs.
```

For a save that carries a caveat, say so — the caveat is what makes the shortlist
useful later:

```
Saved. TS/React/Node core, remote, 26.9-30.2k. Caveat: Kubernetes and Neo4j
in the stack, 12-month contract.
```

### 6. Report

Tell the user:

- how many were reviewed, saved, rejected
- the saved ones as a list, each with company, title, salary, work model, and
  the onetap link
- anything genuinely borderline, with the specific question you want answered
- any pattern that contradicts `criteria.md`

## Judgement calls

**Ask, do not guess, when:** the work model is unspecified and the city is
outside Warsaw/Gdańsk; the salary range straddles 20k; a foreign stack sits
ambiguously between "core" and "nice to have"; a role is web-adjacent but not
clearly web development.

Batch these into one question at the end rather than interrupting the run.

**Never** save a role for its salary when the stack fails Gate 1. That inversion
was checked and rejected explicitly by the candidate.

## Keeping criteria.md true

When a verdict of yours is corrected, or the candidate decides a case the file
does not cover, add it to §12 (case law) and amend the relevant rule. Say in
your report that you updated it. A criteria file that silently drifts from the
candidate's actual judgement is worse than none.

## Links

The vacancy `link` returned by the tools is its page on onetap.work and is the
only URL to show. Never assemble one, and never link to the original job board
or an employer's application form, even when asked.
