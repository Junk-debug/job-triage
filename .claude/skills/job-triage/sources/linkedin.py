#!/usr/bin/env python3
"""Pull vacancies off LinkedIn's public jobs-guest endpoints.

No login, no cookies, no LinkedIn session — the two endpoints used here are the
ones that serve LinkedIn's logged-out job pages, and they answer plain HTML:

  seeMoreJobPostings/search  -> a page of 10 job cards
  jobPosting/<jobId>         -> one full posting, description included

Subcommands
  search   run the configured queries, drop anything already in the ledger,
           print the new job ids as JSON
  fetch    fetch full postings for job ids, print them as JSON
  record   append verdicts to the ledger so the next run skips them
  ledger   summarise what has been judged so far

Everything is stdlib. Run with python3, no install step.
"""

import argparse
import gzip
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# The config and the ledger are data the candidate reads and edits, so they
# live in the repo proper rather than buried in .claude/ with the machinery.
# REPO is .claude/skills/job-triage/sources -> up four.
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
DATA = os.path.join(REPO, "linkedin")
FILTERS_PATH = os.path.join(DATA, "filters.json")
LEDGER_PATH = os.path.join(DATA, "ledger.json")

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
POSTING_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")

PAGE_SIZE = 10          # the endpoint returns 10 cards regardless of what you ask
MAX_START = 975         # start=1000 answers 400
MAX_PAGES = MAX_START // PAGE_SIZE + 1   # the backstop; the empty page ends it first
STALL_PAGES = 2         # consecutive all-repeat pages before calling it done


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------

def get(url, tries=4, timeout=30):
    """GET with backoff. Returns decoded text, or None when the job is gone."""
    delay = 2.0
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # 404/410 means the posting was pulled; that is an answer, not a failure.
            if e.code in (404, 410):
                return None
            # 429 and 5xx are worth waiting out.
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                sys.stderr.write(f"  http {e.code}, retrying in {delay:.0f}s\n")
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < tries - 1:
                sys.stderr.write(f"  {e}, retrying in {delay:.0f}s\n")
                time.sleep(delay)
                delay *= 2
                continue
            raise
    return None


def polite():
    """LinkedIn throttles hard on bursts. Space the calls out."""
    time.sleep(random.uniform(1.1, 2.4))


# --------------------------------------------------------------------------
# html -> text
# --------------------------------------------------------------------------

def strip_tags(fragment):
    """Turn a description fragment into readable plain text.

    onetap's description field takes plain text with blank lines between
    paragraphs, so <br> and <li> become newlines and everything else goes.
    """
    t = fragment
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</(p|div|h[1-6]|tr)\s*>", "\n\n", t)
    t = re.sub(r"(?i)<li\b[^>]*>", "\n- ", t)
    t = re.sub(r"(?i)</(ul|ol)\s*>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = t.replace(" ", " ").replace("​", "")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# Amounts as postings actually write them: "20 000 - 25 000 PLN", "150 zł/h",
# "€6,500/month", "PLN 18000-24000". Deliberately loose — these are hints for
# the reader, not a parsed figure. Gate 4 still reads the line in context.
MONEY_RE = re.compile(
    r"(?:(?:PLN|EUR|USD|GBP|z\u0142|\u20ac|\$|\u00a3)\s?[\d][\d  .,]{2,}"
    r"|[\d][\d  .,]{2,}\s?(?:PLN|EUR|USD|GBP|z\u0142|\u20ac|\$|\u00a3|k\b))"
    r"(?:\s?(?:-|\u2013|\u2014|to|do)\s?[\d][\d  .,]{2,}\s?(?:PLN|EUR|USD|GBP|z\u0142)?)?"
    r"(?:\s?(?:/|per\s|za\s)?\s?(?:h|hour|godz|month|mies|year|rok|net|brutto|gross)\b)?",
    re.I)


def salary_hints(description, limit=6):
    """Lines from the description that look like they state pay."""
    if not description:
        return []
    hits = []
    for line in description.splitlines():
        if not MONEY_RE.search(line):
            continue
        line = line.strip()
        if line and line not in hits:
            hits.append(line[:240])
        if len(hits) >= limit:
            break
    return hits


def first(pattern, text, group=1, flags=re.S):
    m = re.search(pattern, text, flags)
    return html.unescape(re.sub(r"\s+", " ", m.group(group)).strip()) if m else None


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

def canonical_link(job_id):
    """The stable per-job URL.

    Search cards carry per-request tracking noise (?position=&refId=&trackingId=)
    and a country host (pl.linkedin.com). Both vary between runs for the same
    job, so neither can be used as an identity key. onetap enforces one vacancy
    per link, which makes this canonical form the dedup key for the whole
    pipeline.
    """
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def parse_cards(page_html):
    """Pull one page of job cards out of the search HTML."""
    out = []
    chunks = re.split(r"(?=<div class=\"base-card)", page_html)
    for c in chunks:
        m = re.search(r'data-entity-urn="urn:li:jobPosting:(\d+)"', c)
        if not m:
            continue
        job_id = m.group(1)
        out.append({
            "job_id": job_id,
            "link": canonical_link(job_id),
            "title": first(r'class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</h3>', c),
            "company": first(r'class="[^"]*base-search-card__subtitle[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a>', c)
                       or first(r'class="[^"]*base-search-card__subtitle[^"]*"[^>]*>(.*?)</h4>', c),
            "location": first(r'class="[^"]*job-search-card__location[^"]*"[^>]*>(.*?)</span>', c),
            "posted_at": first(r'<time[^>]*datetime="([^"]+)"', c),
        })
    return out


def resolve_pages(cli_cap, cfg_cap):
    """Reconcile --max-pages with the config. None on both sides means no cap."""
    caps = [c for c in (cli_cap, cfg_cap) if c]
    return min(caps) if caps else None


def run_search(query, max_pages=None, verbose=True):
    """Page through one configured query until it runs dry.

    max_pages=None means "to the end", which is the intended mode: the depth of
    a result set is not knowable up front, and capping it silently truncates.
    Measured 2026-09-10, keywords=React geoId=105076658 f_TPR=r1209600: the
    query holds 68 postings over 9 pages, of which a 3-page cap saw 28. Every
    page up to the last was full; the end announced itself with a 0-card page.

    Two stop conditions, because the endpoint has no total count:

      an empty page      the real terminator, and definitive.
      repeated pages     the sample drifts between requests, so a page can
                         re-serve cards already held without the set being
                         exhausted. One such page is normal and is pushed
                         through; STALL_PAGES in a row means the endpoint is
                         out of new results.
    """
    params = {k: v for k, v in query.get("params", {}).items() if v not in (None, "")}
    found = {}
    stalled = 0
    limit = MAX_PAGES if max_pages is None else min(max_pages, MAX_PAGES)
    for page in range(limit):
        start = page * PAGE_SIZE
        if start > MAX_START:
            break
        url = SEARCH_URL + "?" + urllib.parse.urlencode({**params, "start": start})
        page_html = get(url)
        if not page_html:
            break
        cards = parse_cards(page_html)
        if not cards:
            break
        fresh = 0
        for c in cards:
            if c["job_id"] not in found:
                found[c["job_id"]] = c
                fresh += 1
        if verbose:
            sys.stderr.write(f"  [{query['name']}] start={start} cards={len(cards)} "
                             f"new={fresh} total={len(found)}\n")
        stalled = stalled + 1 if fresh == 0 else 0
        if stalled >= STALL_PAGES:
            break
        polite()
    return list(found.values())


# --------------------------------------------------------------------------
# posting detail
# --------------------------------------------------------------------------

def parse_posting(job_id, page_html):
    """Pull the full posting out of the jobPosting HTML."""
    job = {"job_id": job_id, "link": canonical_link(job_id)}

    job["title"] = (first(r'class="[^"]*topcard__title[^"]*"[^>]*>(.*?)</h[12]>', page_html)
                    or first(r'<h2[^>]*class="[^"]*top-card-layout__title[^"]*"[^>]*>(.*?)</h2>', page_html))
    job["company"] = first(r'class="[^"]*topcard__org-name-link[^"]*"[^>]*>(.*?)</a>', page_html)
    job["company_link"] = first(r'class="[^"]*topcard__org-name-link[^"]*"[^>]*href="([^"?]+)"', page_html)
    job["location"] = first(r'class="[^"]*topcard__flavor--bullet[^"]*"[^>]*>(.*?)</span>', page_html)
    job["posted_text"] = first(r'class="[^"]*posted-time-ago__text[^"]*"[^>]*>(.*?)</span>', page_html)
    job["applicants"] = first(r'class="[^"]*num-applicants__caption[^"]*"[^>]*>(.*?)</(?:span|figcaption)>', page_html)

    # description
    desc = None
    m = re.search(r'(?s)class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', page_html)
    if m:
        desc = strip_tags(m.group(1))
    job["description"] = desc

    # "Seniority level", "Employment type", "Job function", "Industries"
    criteria = {}
    for blk in re.findall(r'(?s)<li class="description__job-criteria-item">(.*?)</li>', page_html):
        k = first(r'(?s)job-criteria-subheader[^>]*>(.*?)</h3>', blk)
        v = first(r'(?s)job-criteria-text[^"]*"[^>]*>(.*?)</span>', blk)
        if k and v:
            criteria[k.rstrip(":").strip()] = v
    job["criteria"] = criteria
    job["seniority"] = criteria.get("Seniority level")
    job["employment_type"] = criteria.get("Employment type")
    job["industries"] = criteria.get("Industries")

    # LinkedIn's logged-out view carries a compensation module only rarely (it is
    # absent from every Polish posting sampled), so the salary that matters for
    # Gate 4 nearly always has to come out of the description prose instead.
    job["salary_text"] = first(r'class="[^"]*compensation__salary[^"]*"[^>]*>(.*?)</div>', page_html)
    job["salary_hints"] = salary_hints(desc)

    job["ok"] = bool(job["description"])
    return job


def fetch_posting(job_id):
    page_html = get(POSTING_URL.format(job_id=job_id))
    if page_html is None:
        return {"job_id": job_id, "link": canonical_link(job_id), "ok": False,
                "error": "gone (404) — posting removed or closed"}
    job = parse_posting(job_id, page_html)
    if not job["ok"]:
        job["error"] = "no description block — likely an auth wall or a changed layout"
    return job


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------

def load_ledger():
    if not os.path.exists(LEDGER_PATH):
        return {"jobs": {}}
    with open(LEDGER_PATH) as f:
        return json.load(f)


def save_ledger(led):
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(led, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(tmp, LEDGER_PATH)


def load_filters():
    if not os.path.exists(FILTERS_PATH):
        sys.exit(f"no filters file at {FILTERS_PATH}")
    with open(FILTERS_PATH) as f:
        return json.load(f)


# Parameters the guest backend demonstrably ignores. Sending them would not
# break anything, but it would produce several identical requests dressed up as
# different searches — so they are dropped, and the cells that differ only by
# them are collapsed into one. See the measurements in linkedin/filters.json.
GUEST_IGNORES = {"workplace"}


def expand_queries(filters):
    """Cross keywords x locations x time windows into concrete queries.

    LinkedIn has one location box and one workplace toggle, so it cannot
    express "remote anywhere in Poland OR hybrid/onsite in Warsaw and Gdansk"
    in a single search. The matrix is how that gets covered: each cell is its
    own request, and the union of their results is the feed.
    """
    d = filters.get("defaults", {})
    times = d.get("timePosted") or [None]
    sort_by = d.get("sortBy", "DD")
    backend = filters.get("backend", "guest")

    out, collapsed = [], 0
    seen_params = {}

    for loc in filters["locations"]:
        for kw in filters["keywords"]:
            for tpr in times:
                params = {"keywords": kw, "sortBy": sort_by}
                if loc.get("geoId"):
                    params["geoId"] = loc["geoId"]
                elif loc.get("location"):
                    params["location"] = loc["location"]
                else:
                    sys.exit(f"location group {loc['name']!r} has neither "
                             f"geoId nor location")
                if tpr:
                    params["f_TPR"] = tpr
                if backend != "guest" and loc.get("workplace"):
                    params["f_WT"] = ",".join(
                        {"ONSITE": "1", "REMOTE": "2", "HYBRID": "3"}[w]
                        for w in loc["workplace"])

                name = f"{loc['name']}/{kw}" + (f"/{tpr}" if tpr and len(times) > 1 else "")
                key = tuple(sorted(params.items()))
                if key in seen_params:
                    # Same request as an earlier cell — only a dimension this
                    # backend ignores differed. Merge the names, fire once.
                    seen_params[key]["also_covers"].append(name)
                    seen_params[key]["expected_workplace"] = sorted(set(
                        seen_params[key]["expected_workplace"]) | set(loc.get("workplace") or []))
                    collapsed += 1
                    continue
                q = {
                    "name": name,
                    "params": params,
                    "expected_workplace": sorted(loc.get("workplace") or []),
                    "location_group": loc["name"],
                    "location_label": loc.get("label", loc["name"]),
                    "keyword": kw,
                    "also_covers": [],
                    "max_pages": loc.get("maxPages", d.get("maxPages")),
                }
                seen_params[key] = q
                out.append(q)

    return out, collapsed


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_search(args):
    filters = load_filters()
    queries, collapsed = expand_queries(filters)

    if args.location:
        queries = [q for q in queries if q["location_group"] in args.location]
    if args.keyword:
        queries = [q for q in queries if q["keyword"] in args.keyword]
    if args.query:
        queries = [q for q in queries if q["name"] in args.query]
    if not queries:
        sys.exit("no queries left after filtering — check --location / --keyword / --query")

    if args.dry_run:
        caps = [resolve_pages(args.max_pages, q["max_pages"]) for q in queries]
        pages_note = "to the end" if not any(caps) else {
            q["name"]: cap or "to the end" for q, cap in zip(queries, caps)}
        print(json.dumps({
            "backend": filters.get("backend", "guest"),
            "queries": len(queries),
            "collapsed_as_duplicates": collapsed,
            "pages_per_query": pages_note,
            "estimated_requests": (sum(caps) if all(caps)
                                   else "unknown — uncapped queries page until "
                                        "the endpoint runs dry"),
            "expansion": [{
                "name": q["name"],
                "params": q["params"],
                "expected_workplace": q["expected_workplace"],
                "also_covers": q["also_covers"],
            } for q in queries],
        }, indent=2, ensure_ascii=False))
        return

    led = load_ledger()
    known = led["jobs"]

    sys.stderr.write(f"{len(queries)} queries "
                     f"({collapsed} collapsed as duplicate requests)\n")
    seen, new = {}, {}
    for n, q in enumerate(queries, 1):
        sys.stderr.write(f"[{n}/{len(queries)}] {q['name']}\n")
        pages = resolve_pages(args.max_pages, q["max_pages"])
        for card in run_search(q, pages):
            jid = card["job_id"]
            if jid in seen:
                seen[jid]["queries"].append(q["name"])
                continue
            card["queries"] = [q["name"]]
            card["expected_workplace"] = q["expected_workplace"]
            card["location_group"] = q["location_group"]
            seen[jid] = card
            if jid not in known:
                new[jid] = card

    stale = None
    if args.max_age_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.max_age_days)
        stale = []
        for jid, c in list(new.items()):
            p = c.get("posted_at")
            if not p:
                continue
            try:
                d = datetime.fromisoformat(p).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if d < cutoff:
                stale.append(jid)
                del new[jid]

    per_group = {}
    for c in new.values():
        g = c.get("location_group", "?")
        per_group[g] = per_group.get(g, 0) + 1

    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "queries_run": len(queries),
        "queries_collapsed": collapsed,
        "new_per_location_group": per_group,
        "total_seen": len(seen),
        "already_judged": len(seen) - len(new) - (len(stale) if stale else 0),
        "dropped_as_stale": len(stale) if stale else 0,
        "new_count": len(new),
        "new": sorted(new.values(), key=lambda c: c.get("posted_at") or "", reverse=True),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


# onetap datePosted -> LinkedIn f_TPR seconds
DATE_POSTED = {
    "LAST_24_HOURS": "r86400",
    "LAST_7_DAYS": "r604800",
    "LAST_14_DAYS": "r1209600",
    "LAST_21_DAYS": "r1814400",
    "LAST_MONTH": "r2592000",
    "ALL": None,
}


def cmd_sync_filters(args):
    """Rebuild the LinkedIn search from onetap's saved filters.

    onetap is the source of truth for what the candidate is looking for. This
    copies the parts LinkedIn can act on — keywords, the three workplace groups
    with their cities and countries, and the date window — into the local
    filters file, which is LinkedIn's only notion of a saved search.

    What cannot cross over: categories (LinkedIn has no equivalent),
    experienceLevels (its f_E filter is unreliable), and green/red words (they
    are judgement, and judgement lives in criteria.md).
    """
    with open(args.onetap_filters) as f:
        src = json.load(f)

    filters = load_filters()
    geo = {k: v for k, v in filters.get("geo_ids", {}).items() if not k.startswith("_")}

    before_kw = list(filters.get("keywords", []))
    before_loc = [l["name"] for l in filters.get("locations", [])]

    if src.get("keywords"):
        filters["keywords"] = list(dict.fromkeys(src["keywords"]))

    # Each onetap workplace group becomes one location group per place. Groups
    # for the same place are then merged, because the workplace dimension is
    # not something the guest backend can act on anyway.
    places = {}
    for group, wp in (("remoteWorkplace", "REMOTE"),
                      ("hybridWorkplace", "HYBRID"),
                      ("onsiteWorkplace", "ONSITE")):
        g = src.get(group) or {}
        if not g.get("enabled", True):
            continue
        entries = [(c["name"], c.get("asciiName") or c["name"], "city")
                   for c in g.get("cities") or []]
        entries += [(c["name"], c["name"], "country")
                    for c in g.get("countries") or []]
        for name, ascii_name, kind in entries:
            slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
            key = slug if kind == "city" else f"remote-{slug}"
            p = places.setdefault(key, {
                "name": key,
                "label": name + (" (remote)" if kind == "country" else ""),
                "workplace": [],
            })
            if wp not in p["workplace"]:
                p["workplace"].append(wp)
            gid = geo.get(name) or geo.get(ascii_name)
            if gid:
                p["geoId"] = gid
                p.pop("location", None)
            elif "geoId" not in p:
                # No verified geoId — a plain location string works just as
                # well, it is only less precise about which "Warsaw" is meant.
                country = ""
                if kind == "city":
                    country = ", Poland"
                p["location"] = f"{ascii_name}{country}"

    if places:
        for p in places.values():
            p["workplace"] = sorted(p["workplace"])
        # remote groups first — they are the widest net
        filters["locations"] = sorted(
            places.values(),
            key=lambda p: (0 if "REMOTE" in p["workplace"] else 1, p["name"]))

    dp = src.get("datePosted")
    if dp in DATE_POSTED:
        tpr = DATE_POSTED[dp]
        filters.setdefault("defaults", {})["timePosted"] = [tpr] if tpr else []

    filters["sync"] = {
        "from": "onetap get_my_filters",
        "at": datetime.now(timezone.utc).isoformat(),
        "datePosted": dp,
    }

    queries, collapsed = expand_queries(filters)
    report = {
        "keywords": {"before": len(before_kw), "after": len(filters["keywords"]),
                     "value": filters["keywords"]},
        "locations": {"before": before_loc,
                      "after": [{"name": l["name"],
                                 "target": l.get("geoId") or l.get("location"),
                                 "workplace": l["workplace"]}
                                for l in filters["locations"]]},
        "timePosted": filters["defaults"].get("timePosted"),
        "expands_to": len(queries),
        "collapsed_as_duplicates": collapsed,
        "not_carried_over": [
            "categories — LinkedIn has no equivalent",
            "experienceLevels — its f_E filter is unreliable (see linkedin/filters.json)",
            "greenWords / redWords — that is judgement, and it lives in criteria.md",
        ],
    }

    if args.dry_run:
        report["dry_run"] = True
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    tmp = FILTERS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(filters, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, FILTERS_PATH)
    report["written_to"] = os.path.relpath(FILTERS_PATH)
    print(json.dumps(report, indent=2, ensure_ascii=False))


# Card fields the posting page does not carry, or carries worse. posted_at is
# the important one: the card gives an ISO date, the posting page only says
# "19 hours ago".
CARRY_OVER = ("posted_at", "expected_workplace", "location_group", "queries")


def cmd_fetch(args):
    ids = list(args.job_ids)
    cards = {}
    if args.from_search:
        with open(args.from_search) as f:
            for c in json.load(f)["new"]:
                cards[c["job_id"]] = c
                ids.append(c["job_id"])
    ids = list(dict.fromkeys(ids))
    if args.limit:
        ids = ids[:args.limit]

    jobs = []
    for i, jid in enumerate(ids, 1):
        sys.stderr.write(f"  [{i}/{len(ids)}] {jid}\n")
        job = fetch_posting(jid)
        card = cards.get(jid)
        if card:
            for k in CARRY_OVER:
                if card.get(k) is not None:
                    job[k] = card[k]
        jobs.append(job)
        if i < len(ids):
            polite()

    ok = [j for j in jobs if j.get("ok")]
    print(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "requested": len(ids),
        "ok": len(ok),
        "failed": len(jobs) - len(ok),
        "jobs": jobs,
    }, indent=2, ensure_ascii=False))


def cmd_record(args):
    """Write verdicts into the ledger so the next search skips them."""
    if args.json_file:
        with open(args.json_file) as f:
            entries = json.load(f)
    else:
        entries = json.loads(sys.stdin.read())
    if isinstance(entries, dict):
        entries = entries.get("verdicts", [])

    led = load_ledger()
    now = datetime.now(timezone.utc).isoformat()
    added = updated = 0
    for e in entries:
        jid = str(e["job_id"])
        rec = {
            "job_id": jid,
            "link": canonical_link(jid),
            "title": e.get("title"),
            "company": e.get("company"),
            "verdict": e["verdict"],           # SAVED | NOT_INTERESTED | UNREADABLE
            "reason": e.get("reason"),         # the gate and the line that decided it
            "onetap_vacancy_id": e.get("onetap_vacancy_id"),
            "linkedin_action": e.get("linkedin_action"),   # save | dismiss | None
            "judged_at": e.get("judged_at", now),
        }
        if jid in led["jobs"]:
            updated += 1
        else:
            added += 1
        led["jobs"][jid] = rec
    save_ledger(led)
    sys.stderr.write(f"ledger: +{added} new, {updated} updated, {len(led['jobs'])} total\n")
    print(json.dumps({"added": added, "updated": updated, "total": len(led["jobs"])}, indent=2))


def cmd_ledger(args):
    led = load_ledger()
    jobs = list(led["jobs"].values())
    counts = {}
    for j in jobs:
        counts[j.get("verdict", "?")] = counts.get(j.get("verdict", "?"), 0) + 1
    if args.saved:
        print(json.dumps([j for j in jobs if j.get("verdict") == "SAVED"],
                         indent=2, ensure_ascii=False))
        return
    print(json.dumps({"total": len(jobs), "by_verdict": counts}, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="run the filter matrix, list unjudged jobs")
    s.add_argument("--query", action="append", help="run only this expanded cell (repeatable)")
    s.add_argument("--location", action="append", help="run only this location group (repeatable)")
    s.add_argument("--keyword", action="append", help="run only this keyword (repeatable)")
    s.add_argument("--dry-run", action="store_true",
                   help="print the expansion without calling LinkedIn")
    s.add_argument("--max-pages", type=int, default=None,
                   help="cap pages per query (10 jobs each). Default is uncapped: "
                        "each query pages until the endpoint runs dry. Use this to "
                        "sample cheaply, not to shorten a real run — a cap silently "
                        "truncates (measured: a 3-page cap saw 28 of 402 postings)")
    s.add_argument("--max-age-days", type=int, default=None, help="drop cards older than this")
    s.set_defaults(func=cmd_search)

    f = sub.add_parser("fetch", help="fetch full postings")
    f.add_argument("job_ids", nargs="*")
    f.add_argument("--from-search", help="read job ids from a search output file")
    f.add_argument("--limit", type=int, default=None)
    f.set_defaults(func=cmd_fetch)

    r = sub.add_parser("record", help="write verdicts to the ledger")
    r.add_argument("json_file", nargs="?", help="verdicts file (default: stdin)")
    r.set_defaults(func=cmd_record)

    y = sub.add_parser("sync-filters", help="rebuild the search from onetap's saved filters")
    y.add_argument("onetap_filters", help="a saved get_my_filters response")
    y.add_argument("--dry-run", action="store_true", help="show the diff without writing")
    y.set_defaults(func=cmd_sync_filters)

    l = sub.add_parser("ledger", help="summarise the ledger")
    l.add_argument("--saved", action="store_true", help="print the SAVED records")
    l.set_defaults(func=cmd_ledger)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
