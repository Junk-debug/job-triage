#!/usr/bin/env python3
"""Check the keepers of a triage run against onetap's application pipeline.

A vacancy the candidate has already saved, applied to or rejected must not come
back as a fresh keeper. It reaches the batch again for ordinary reasons: the
same posting is aggregated by two boards, LinkedIn lists what onetap already
carries, or a company re-lists a role weeks after it was judged.

The question "is this already in the pipeline?" is answered by onetap, not
guessed from the batch, so this runs AFTER the gates rather than before them:

    judge -> queries -> get_my_applications (MCP) -> match -> route

    queries   what to ask onetap, one call per company holding a keeper
    match     compare the answers against the keepers, flip the ones that
              are already tracked to NOT_INTERESTED, and report every flip

There are 6000+ applications on the account, so a full dump is not on the
table — the free-text `query` on get_my_applications is what makes this cheap.
It searches title, company name AND notes, so it over-returns; the matching
below is what makes it precise.

A wrong match costs a vacancy: a keeper silently dropped is one the candidate
never sees. So matching is deliberately conservative, and every flip is
reported for the candidate to overrule.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone


# --------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------

# Noise that differs between boards for the same posting: gender markers,
# contract notes, work-model tags, the trailing "(TypeScript/React)" gloss.
#
# Seniority words are deliberately NOT stripped. They read like noise, but
# "Junior Frontend Developer" and "Senior Frontend Developer" at the same
# company are different postings, and treating them as one drops the keeper.
TITLE_NOISE = re.compile(
    r"\b(m\s*/\s*f|f\s*/\s*m|f/m/x|m/f/d|w/m/d|remote|hybrid|onsite|"
    r"b2b|uop|full[- ]?time|part[- ]?time|contract)\b", re.I)

# Company suffixes vary by board: "Company E Ltd" vs "Company E".
COMPANY_NOISE = re.compile(
    r"\b(sp z o o|s a|poland|polska|group|inc|ltd|llc|gmbh)\b")


def clean(s):
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)              # parenthetical stack glosses
    s = TITLE_NOISE.sub(" ", s)
    s = re.sub(r"[^a-z0-9Ѐ-ӿĀ-ſ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def company_key(name):
    return re.sub(r"\s+", " ", COMPANY_NOISE.sub(" ", clean(name))).strip()


def job_key(company, title):
    return company_key(company), clean(title)


def link_key(url):
    """Normalise a URL enough that two spellings of one page compare equal."""
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("?")[0].split("#")[0]
    return u.rstrip("/")


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def load_verdicts(path):
    with open(path) as f:
        v = json.load(f)
    return v.get("verdicts", v) if isinstance(v, dict) else v


def load_batch(path):
    if not path:
        return {}
    with open(path) as f:
        return {i["ref"]: i for i in json.load(f)["items"]}


def keepers(verdicts, batch):
    """The verdicts this check applies to: the ones that survived the gates.

    A rejection is already out, and an UNDECIDED is deliberately written
    nowhere, so neither can be made worse by a duplicate.
    """
    out = []
    for v in verdicts:
        if v.get("verdict") != "SAVED":
            continue
        item = batch.get(v["ref"], {})
        source, sid = v["ref"].split(":", 1)
        out.append({
            "ref": v["ref"],
            "source": source,
            "source_id": sid,
            "company": item.get("company") or v.get("company"),
            "title": item.get("title") or v.get("title"),
            "link": item.get("link") or v.get("link"),
            "verdict": v,
        })
    return out


def flatten_candidates(payload):
    """Accept whatever shape the get_my_applications answers were saved in.

    One response, a list of responses, or a dict keyed by query string — they
    all flatten to one pool, and every keeper is matched against all of it. A
    keeper found by another keeper's query is still caught that way.
    """
    pool, truncated = [], []

    def take(resp, label=None):
        if isinstance(resp, dict) and "applications" in resp:
            apps = resp["applications"]
            total = resp.get("total")
            if isinstance(total, int) and total > len(apps):
                truncated.append({"query": label, "returned": len(apps), "total": total})
            pool.extend(apps)
        elif isinstance(resp, list):
            for r in resp:
                take(r, label)
        elif isinstance(resp, dict) and "vacancy" in resp:
            pool.append(resp)                    # a bare application record

    if isinstance(payload, dict) and "applications" not in payload:
        for label, resp in payload.items():
            take(resp, label)
    else:
        take(payload)

    by_id = {a.get("id") or id(a): a for a in pool}
    return list(by_id.values()), truncated


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------

def match(keeper, applications):
    """Find the application this keeper duplicates, or None.

    Three ways in, strongest first. Each is a fact about the record rather
    than a resemblance, except the last, which is why the last one reports
    what it matched on.
    """
    k_link = link_key(keeper.get("link"))
    k_key = job_key(keeper.get("company"), keeper.get("title"))

    fallback = None
    for app in applications:
        vac = app.get("vacancy") or {}

        # The same onetap record, not a duplicate of it. Only reachable if the
        # feed handed back something already tracked; skip it either way.
        if keeper["source"] == "onetap" and app.get("vacancyId") == keeper["source_id"]:
            continue

        if k_link and link_key(vac.get("link")) == k_link:
            return app, "link"

        if keeper["source"] == "linkedin":
            sid = keeper["source_id"]
            haystack = f"{vac.get('link') or ''} {app.get('notes') or ''}"
            if sid and sid in haystack:
                return app, "linkedin job id"

        if k_key[0] and k_key[1] and job_key(vac.get("companyName"), vac.get("title")) == k_key:
            fallback = fallback or (app, "company + title")

    return fallback


def reason_for(keeper, app, matched_by):
    vac = app.get("vacancy") or {}
    when = (app.get("updatedAt") or app.get("createdAt") or "")[:10]
    original = (keeper["verdict"].get("reason") or "").strip()
    text = (f"Duplicate - already in the pipeline as {app.get('status')}"
            f"{' since ' + when if when else ''}: "
            f"{vac.get('companyName')} / {vac.get('title')}"
            f"{' - ' + vac.get('link') if vac.get('link') else ''}. "
            f"Matched on {matched_by}. Passed the gates on its own merits"
            f"{' (' + original + ')' if original else ''}, "
            f"so this is a duplicate rather than a rejection.")
    return text[:1000]


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_queries(args):
    """The get_my_applications calls to make — one per company, not per keeper."""
    ks = keepers(load_verdicts(args.verdicts), load_batch(args.batch))

    # Group on the normalised company, not the raw string: "Company E" from LinkedIn
    # and "Company E Ltd" from onetap are one company and deserve one call. The
    # shortest spelling is the one sent, because the search is a partial match
    # and the shorter string is the one both spellings contain.
    groups = {}
    for k in ks:
        raw = (k.get("company") or "").strip()
        if raw:
            key = company_key(raw)
        else:
            # No company to search on: fall back to the longest words of the
            # title, which is what the free-text search can still bite on.
            raw = " ".join([w for w in clean(k.get("title")).split() if len(w) > 4][:3])
            key = raw
        if not raw:
            continue
        g = groups.setdefault(key, {"query": raw, "for": []})
        if len(raw) < len(g["query"]):
            g["query"] = raw
        g["for"].append(k["ref"])
    by_query = {g["query"]: g["for"] for g in groups.values()}

    print(json.dumps({
        "keepers": len(ks),
        "calls": len(by_query),
        "how": "get_my_applications(query=<query>, limit=100). Save every "
               "response into one JSON object keyed by the query string, then "
               "run `check.py match --candidates <that file>`.",
        "queries": [{"query": q, "for": refs} for q, refs in sorted(by_query.items())],
    }, indent=2, ensure_ascii=False))


def cmd_match(args):
    verdicts = load_verdicts(args.verdicts)
    batch = load_batch(args.batch)
    ks = keepers(verdicts, batch)
    with open(args.candidates) as f:
        applications, truncated = flatten_candidates(json.load(f))

    hits, flipped_refs = [], {}
    for k in ks:
        found = match(k, applications)
        if not found:
            continue
        app, matched_by = found
        vac = app.get("vacancy") or {}
        reason = reason_for(k, app, matched_by)
        flipped_refs[k["ref"]] = reason
        hits.append({
            "ref": k["ref"],
            "title": k.get("title"),
            "company": k.get("company"),
            "link": k.get("link"),
            "matched_by": matched_by,
            "already_tracked_as": {
                "application_id": app.get("id"),
                "vacancy_id": app.get("vacancyId"),
                "status": app.get("status"),
                "title": vac.get("title"),
                "company": vac.get("companyName"),
                "link": vac.get("link"),
                "updated_at": app.get("updatedAt"),
            },
            "new_reason": reason,
        })

    out_verdicts = []
    for v in verdicts:
        if v.get("ref") in flipped_refs:
            v = dict(v, verdict="NOT_INTERESTED", reason=flipped_refs[v["ref"]],
                     was="SAVED", duplicate=True)
        out_verdicts.append(v)

    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "keepers_checked": len(ks),
        "applications_searched": len(applications),
        "duplicates_found": len(hits),
        "still_keepers": len(ks) - len(hits),
        "hits": hits,
        "truncated_queries": truncated,
        "verdicts": out_verdicts,
    }
    if args.out:
        open(args.out, "w").write(json.dumps(out_verdicts, indent=2, ensure_ascii=False) + "\n")
        result["verdicts"] = f"written to {args.out}"
        sys.stderr.write(f"{len(hits)} of {len(ks)} keepers were already in the "
                         f"pipeline -> {args.out}\n")
    if truncated:
        sys.stderr.write(f"WARNING: {len(truncated)} query(s) returned only part of "
                         f"their matches — narrow them and re-run before trusting "
                         f"a clean result\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("queries", help="what to ask onetap for this verdicts file")
    q.add_argument("--verdicts", required=True)
    q.add_argument("--batch", help="the triage.py batch, for company and title")
    q.set_defaults(func=cmd_queries)

    m = sub.add_parser("match", help="flip keepers that are already in the pipeline")
    m.add_argument("--verdicts", required=True)
    m.add_argument("--candidates", required=True,
                   help="the saved get_my_applications answers")
    m.add_argument("--batch", help="the triage.py batch, for company and title")
    m.add_argument("-o", "--out", help="write the patched verdicts here")
    m.set_defaults(func=cmd_match)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
