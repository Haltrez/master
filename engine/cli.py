"""Repost Radar command line.

    python cli.py add-search --label "Accountancy - Leeds" \
        --keywords "management accountant,financial accountant,finance manager,credit controller" \
        --location Leeds --radius 15
    python cli.py run --search-id 1
    python cli.py run-all
    python cli.py reed-sample --keywords "management accountant" --location Leeds

Role Match (Phase 2.5):
    python cli.py brief add --agency "MCM Recruitment Solutions" --role "Electrical Estimator" \
        --location Warrington --radius 30
    python cli.py brief import briefs.yaml
    python cli.py employers check --all
    python cli.py match --agency "MCM Recruitment Solutions" --out matches.csv
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.config import get_settings, setup_logging
from app.reed_client import ReedClient

FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "reed_search_sample.json"
LOCAL_STORE = Path(__file__).parent / ".role_match_local.json"


def _reed(settings) -> ReedClient:
    return ReedClient(
        settings.reed_api_key,
        requests_per_second=settings.reed_requests_per_second,
        max_tries=settings.reed_max_tries,
        backoff_base=settings.reed_backoff_base_seconds,
    )


def _repo(settings):
    from app.pg import PgStore

    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set (see .env.example)")
    return PgStore(settings.database_url)


def _print_result(r) -> None:
    print(f"run {r.run_id} (search {r.search_id}): {r.status}")
    print(f"  api_calls={r.api_calls} listings_seen={r.listings_seen} new_listings={r.new_listings}")
    print(f"  employer={r.by_posted_by['employer']} agency={r.by_posted_by['agency']} "
          f"reclassified={r.reclassified}")
    if r.error:
        print(f"  error: {r.error}")


def _print_rebuild(out: dict) -> None:
    if not out:
        return
    if "error" in out:
        print(f"  rebuild failed: {out['error']}")
        return
    h, a = out.get("hot_leads", {}), out.get("agencies", {})
    print(f"  hot leads: {h.get('hot', 0)} ({h.get('new', 0)} new)  agencies: {a.get('agencies', 0)} "
          f"({a.get('new', 0)} new)")


def cmd_run(args, settings) -> int:
    from app.pipeline import collect_one

    r = collect_one(_repo(settings), settings, args.search_id)
    _print_result(r)
    _print_rebuild(r.after_run)
    return 0 if r.status == "ok" else 1


def cmd_run_all(args, settings) -> int:
    from app.pipeline import collect_all

    results = collect_all(_repo(settings), settings)
    if not results:
        print("No active searches. Add one with: python cli.py add-search ...")
    for r in results:
        _print_result(r)
    if results:
        _print_rebuild(results[-1].after_run)
    return 0 if all(r.status == "ok" for r in results) else 1


def cmd_rebuild(args, settings) -> int:
    from app.pipeline import make_reed, rebuild_all

    _print_rebuild(rebuild_all(_repo(settings), settings, make_reed(settings)))
    return 0


def cmd_hot_leads(args, settings) -> int:
    rows = _repo(settings).hot_leads(search_id=args.search_id, min_score=args.min_score, status=args.status,
                                     limit=args.top)
    if not rows:
        print("No hot leads yet. They appear once the same role is posted again, an ad stays open 30+ days, "
              "or two copies run at once.")
        return 0
    print(f"{'score':>5}  {'employer':<32} {'role':<36} {'town':<14} {'posts':>5}  {'since':<10} live  status")
    for h in rows:
        print(f"{h['score']:>5}  {h['employer_name'][:32]:<32} {h['title'][:36]:<36} {(h['location_name'] or '')[:14]:<14} "
              f"{h['posting_count_90d']:>5}  {h['first_seen_at']:%d %b %Y}  {'yes ' if h['live_now'] else 'no  '}  "
              f"{h['status']}")
    return 0


def cmd_add_search(args, settings) -> int:
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if not keywords:
        print("error: --keywords needs at least one keyword", file=sys.stderr)
        return 2
    s = _repo(settings).create_search(args.label, keywords, args.location, args.radius, args.min_salary)
    print(f"Created search {s.id}: {s.label} | {', '.join(s.keywords)} | "
          f"{s.location} +{s.radius_miles} miles" + (f" | min £{s.min_salary}" if s.min_salary else ""))
    print(f"Run it with: python cli.py run --search-id {s.id}")
    return 0


def _enrichers(settings, store):
    from app.ch_client import CachedCompaniesHouse, CompaniesHouseClient
    from app.web_lookup import Brave, Hunter

    ch = CachedCompaniesHouse(CompaniesHouseClient(settings.companies_house_api_key), store)
    brave = Brave(settings.brave_api_key) if settings.brave_api_key else None
    hunter = Hunter(settings.hunter_api_key) if settings.hunter_api_key else None
    return ch, brave, hunter


def cmd_agencies(args, settings) -> int:
    from app.agencies import compliance_label

    rows = _repo(settings).agencies_list(search_id=args.search_id, stage=args.stage)
    if not rows:
        print("No agencies yet. They come from agency ads collected by your searches.")
    print(f"{'id':>5}  {'agency':<36} {'ads':>4}  {'stage':<14} {'director':<22} email outreach")
    for a in rows:
        print(f"{a['id']:>5}  {a['name'][:36]:<36} {a['ads_count_90d']:>4}  {a['stage']:<14} "
              f"{(a['director_name'] or '-')[:22]:<22} {compliance_label(a)}")
    return 0


def cmd_enrich(args, settings) -> int:
    from app.agencies import enrich_agency

    store = _repo(settings)
    ch, brave, hunter = _enrichers(settings, store)
    if args.agency_id:
        ids = args.agency_id
    else:
        ids = [a["id"] for a in store.agencies_to_enrich(limit=args.limit)]
    for i in ids:
        r = enrich_agency(store, i, ch, brave=brave, hunter=hunter)
        extra = "".join(f", {k.replace('_', ' ')}: {v}" for k, v in r.items()
                        if k in ("directors_added", "website", "emails_found"))
        print(f"{i:>5}  {r['name'][:36]:<36} {r['companies_house']}{extra}")
    if not ids:
        print("Nothing to enrich.")
    return 0


def cmd_employers_find_careers(args, settings) -> int:
    from app.web_lookup import Brave

    if not settings.brave_api_key:
        print("error: BRAVE_API_KEY is not set", file=sys.stderr)
        return 2
    svc = _role_services(args, settings)
    brave = Brave(settings.brave_api_key)
    todo = [e for e in svc.repo.employers() if not e.get("careers_url")
            and (args.id is None or e["id"] in args.id)][: args.limit]
    for e in todo:
        url = brave.find_careers_url(e["name"], e.get("town"), (e.get("website") or "").replace("https://", "")
                                     .replace("http://", "").strip("/") or None)
        if url:
            svc.repo.update_employer(e["id"], {"careers_url": url, "status": "active"})
        print(f"{e['id']:>5}  {e['name'][:40]:<40} {url or 'no careers page found'}")
    svc.repo.save()
    return 0


def cmd_reed_sample(args, settings) -> int:
    """One real search call; saves the raw JSON fixture and prints the field names."""
    with _reed(settings) as reed:
        body = reed.search_raw_page(args.keywords, args.location, args.radius, None, args.posted_by)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    results = body.get("results") or []
    print(f"Saved {len(results)} results (totalResults={body.get('totalResults')}) to {out}")
    print("Top-level fields:", ", ".join(sorted(body)))
    fields: dict[str, set[str]] = {}
    for item in results:
        for k, v in item.items():
            fields.setdefault(k, set()).add(type(v).__name__)
    print("Result fields:")
    for k in sorted(fields):
        print(f"  {k}: {' | '.join(sorted(fields[k]))}")
    return 0


# ---------------------------------------------------------------------------
# Role Match
# ---------------------------------------------------------------------------

def _role_services(args, settings, *, need_fetcher=False, need_wayback=False, need_reed=False):
    from app.geo import Geocoder
    from app.polite import PoliteFetcher
    from app.role_match import Services
    from app.role_repo import LocalRoleRepo
    from app.wayback import Wayback

    store = args.store
    if store == "auto":
        store = "db" if settings.database_url else "local"
    if store == "db":
        repo = _repo(settings)
    else:
        repo = LocalRoleRepo(LOCAL_STORE)
        print(f"(using local store {LOCAL_STORE.name}; set DATABASE_URL to use the database)",
              file=sys.stderr)
    reed = None
    if need_reed and settings.reed_api_key:
        reed = _reed(settings)
    elif need_reed:
        print("(REED_API_KEY not set: skipping Reed discovery and market demand)", file=sys.stderr)
    return Services(repo=repo, settings=settings, geocoder=Geocoder(repo),
                    fetcher=PoliteFetcher() if need_fetcher else None,
                    wayback=Wayback(repo) if need_wayback else None, reed=reed)


def cmd_brief_add(args, settings) -> int:
    svc = _role_services(args, settings)
    kws = [k.strip() for k in (args.sector or "").split(",") if k.strip()]
    from app.role_match import add_brief

    b = add_brief(svc, args.agency, args.role, args.location, args.radius, kws, args.seniority)
    svc.repo.save()
    loc = "" if b.get("lat") is not None else " (location not geocoded; distances will be unknown)"
    print(f"Brief {b['id']}: {b['label']} within {b['radius_miles']} miles{loc}")
    return 0


def cmd_brief_import(args, settings) -> int:
    from app.role_match import import_briefs

    svc = _role_services(args, settings)
    briefs = import_briefs(svc, Path(args.file))
    svc.repo.save()
    for b in briefs:
        loc = "" if b.get("lat") is not None else "  [location not geocoded]"
        print(f"Brief {b['id']}: {b['label']} within {b['radius_miles']} miles{loc}")
    print(f"{len(briefs)} briefs loaded")
    return 0


def cmd_brief_list(args, settings) -> int:
    svc = _role_services(args, settings)
    ag = svc.repo.agency_by_name(args.agency)
    if not ag:
        print(f"No agency called '{args.agency}'")
        return 1
    for b in svc.repo.briefs(ag["id"]):
        print(f"{b['id']:>4}  {b['label']:<50} {b['radius_miles']:>3} mi  {'active' if b.get('active', True) else 'off'}")
    return 0


def cmd_employers_add(args, settings) -> int:
    svc = _role_services(args, settings)
    e = svc.repo.add_employer({"name": args.name, "careers_url": args.careers_url, "website": args.website,
                               "town": args.town, "source": "manual", "status": "active" if args.careers_url else "research"})
    svc.repo.save()
    print(f"Employer {e['id']}: {e['name']} | {e.get('careers_url') or 'no careers URL (research queue)'}")
    return 0


def cmd_employers_list(args, settings) -> int:
    svc = _role_services(args, settings)
    jobs = svc.repo.jobs()
    for e in svc.repo.employers():
        if args.status and e.get("status") != args.status:
            continue
        n = sum(1 for j in jobs if j["employer_id"] == e["id"])
        print(f"{e['id']:>4}  {e['name'][:40]:<40} {(e.get('town') or '')[:22]:<22} {e.get('status'):<17} "
              f"{n:>3} roles  checked {str(e.get('last_checked_at') or 'never')[:16]}")
    return 0


def cmd_employers_check(args, settings) -> int:
    from app.role_match import check_employers

    if not args.all and not args.id:
        print("error: pass --all or --id N", file=sys.stderr)
        return 2
    svc = _role_services(args, settings, need_fetcher=True)
    results = check_employers(svc, None if args.all else args.id, force=args.force)
    svc.repo.save()
    for r in results:
        print(f"{r.employer_id:>4}  {r.name[:40]:<40} {r.outcome:<17} {r.jobs:>3} roles  {r.pages} pages  {r.note}")
    if not results:
        print("No employers with a careers URL to check.")
    return 0 if all(r.outcome != "error" for r in results) else 1


def cmd_employers_import_ch(args, settings) -> int:
    from app.ch_client import CompaniesHouseClient, employer_rows_from_search
    from app.role_seed import SEED_SIC_CODES

    svc = _role_services(args, settings)
    ch = CompaniesHouseClient(settings.companies_house_api_key)
    sics = [s.strip() for s in (args.sic or ",".join(SEED_SIC_CODES)).split(",") if s.strip()]
    rows = employer_rows_from_search(ch.advanced_search(sics, args.location, args.size))
    for r in rows:
        svc.repo.add_employer(r)
    svc.repo.save()
    print(f"{len(rows)} companies added to the research queue (SIC {', '.join(sics)}"
          + (f", {args.location}" if args.location else "") + ")")
    return 0


def cmd_match(args, settings) -> int:
    from app.role_match import check_employers, run_match, signal, write_csv

    svc = _role_services(args, settings, need_fetcher=args.check, need_wayback=True, need_reed=not args.no_reed)
    if args.check:
        for r in check_employers(svc):
            print(f"checked {r.name}: {r.outcome}, {r.jobs} roles {r.note}", file=sys.stderr)
    report = run_match(svc, args.agency, use_reed=not args.no_reed)
    svc.repo.save()
    n = write_csv(report, Path(args.out))
    print(f"{n} matches for {report.agency['name']} written to {args.out}")
    current = None
    for m in report.rows:
        if m.brief["label"] != current:
            current = m.brief["label"]
            print(f"\n{current}")
        dist = f"{m.distance:.0f} mi" if m.distance is not None else "? mi"
        print(f"  {m.result.points:>3}  {m.employer['name'][:34]:<34} {m.job['title'][:40]:<40} {dist:>6}  {signal(m)}")
    missing = [b["label"] for b in report.briefs if not any(m.brief["id"] == b["id"] for m in report.rows)]
    for label in missing:
        print(f"\n{label}\n  (no matches yet)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cli.py", description="Repost Radar engine")
    p.add_argument("--store", choices=["auto", "local", "db"], default="auto",
                   help="Role Match storage (auto = the database if DATABASE_URL is set, else a local JSON file)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run one saved search")
    r.add_argument("--search-id", type=int, required=True)
    r.set_defaults(fn=cmd_run)

    ra = sub.add_parser("run-all", help="run every active search")
    ra.set_defaults(fn=cmd_run_all)

    a = sub.add_parser("add-search", help="create a saved search (niche + region)")
    a.add_argument("--label", required=True)
    a.add_argument("--keywords", required=True, help="comma-separated; each is queried separately")
    a.add_argument("--location", required=True)
    a.add_argument("--radius", type=int, default=15, help="miles (default 15)")
    a.add_argument("--min-salary", type=int, default=None)
    a.set_defaults(fn=cmd_add_search)

    rb = sub.add_parser("rebuild", help="recompute hot leads and agencies now")
    rb.set_defaults(fn=cmd_rebuild)

    hl = sub.add_parser("hot-leads", help="print hot leads")
    hl.add_argument("--search-id", type=int)
    hl.add_argument("--top", type=int, default=20)
    hl.add_argument("--min-score", type=int)
    hl.add_argument("--status", choices=["new", "used", "ignored"])
    hl.set_defaults(fn=cmd_hot_leads)

    ag = sub.add_parser("agencies", help="list prospect agencies")
    ag.add_argument("--search-id", type=int)
    ag.add_argument("--stage")
    ag.set_defaults(fn=cmd_agencies)

    en = sub.add_parser("enrich", help="Companies House (+ Brave website, Hunter emails if keys set)")
    en.add_argument("--agency-id", type=int, action="append")
    en.add_argument("--all-new", action="store_true", help="every agency not checked yet (default)")
    en.add_argument("--limit", type=int, default=50)
    en.set_defaults(fn=cmd_enrich)

    s = sub.add_parser("reed-sample", help="save one real Reed response as the test fixture")
    s.add_argument("--keywords", default="management accountant")
    s.add_argument("--location", default="Leeds")
    s.add_argument("--radius", type=int, default=15)
    s.add_argument("--posted-by", choices=["employer", "agency"], default="employer")
    s.add_argument("--out", default=str(FIXTURE))
    s.set_defaults(fn=cmd_reed_sample)

    b = sub.add_parser("brief", help="candidate briefs (role profiles only, no personal data)")
    bsub = b.add_subparsers(dest="brief_cmd", required=True)
    ba = bsub.add_parser("add")
    ba.add_argument("--agency", required=True)
    ba.add_argument("--role", required=True, help="a role family name, e.g. 'Electrical Estimator'")
    ba.add_argument("--location", required=True)
    ba.add_argument("--radius", type=int, default=30)
    ba.add_argument("--sector", help="comma-separated sector keywords, e.g. 'm&e,building services'")
    ba.add_argument("--seniority")
    ba.set_defaults(fn=cmd_brief_add)
    bi = bsub.add_parser("import")
    bi.add_argument("file")
    bi.set_defaults(fn=cmd_brief_import)
    bl = bsub.add_parser("list")
    bl.add_argument("--agency", required=True)
    bl.set_defaults(fn=cmd_brief_list)

    e = sub.add_parser("employers", help="employer directory and careers-page checks")
    esub = e.add_subparsers(dest="emp_cmd", required=True)
    ea = esub.add_parser("add")
    ea.add_argument("--name", required=True)
    ea.add_argument("--careers-url")
    ea.add_argument("--website")
    ea.add_argument("--town")
    ea.set_defaults(fn=cmd_employers_add)
    el = esub.add_parser("list")
    el.add_argument("--status")
    el.set_defaults(fn=cmd_employers_list)
    ec = esub.add_parser("check", help="fetch careers pages + ATS boards politely")
    ec.add_argument("--all", action="store_true", help="every employer with a careers URL that is due")
    ec.add_argument("--id", type=int, action="append", help="one employer (repeatable)")
    ec.add_argument("--force", action="store_true", help="ignore the weekly schedule (never more than once a day)")
    ec.set_defaults(fn=cmd_employers_check)
    ei = esub.add_parser("import-ch", help="Companies House SIC search into the research queue")
    ei.add_argument("--sic", help="comma-separated SIC codes (default: the four seed codes)")
    ei.add_argument("--location")
    ei.add_argument("--size", type=int, default=100)
    ei.set_defaults(fn=cmd_employers_import_ch)

    ef = esub.add_parser("find-careers", help="find careers pages for research-queue employers (Brave)")
    ef.add_argument("--id", type=int, action="append")
    ef.add_argument("--limit", type=int, default=25)
    ef.set_defaults(fn=cmd_employers_find_careers)

    m = sub.add_parser("match", help="match an agency's briefs to direct employers")
    m.add_argument("--agency", required=True)
    m.add_argument("--out", default="matches.csv")
    m.add_argument("--check", action="store_true", help="check due employers first")
    m.add_argument("--no-reed", action="store_true", help="skip Reed discovery and market demand")
    m.set_defaults(fn=cmd_match)

    args = p.parse_args(argv)
    settings = get_settings()
    setup_logging(settings)
    try:
        return args.fn(args, settings)
    except Exception as exc:  # friendly top-level error, details already logged where relevant
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
