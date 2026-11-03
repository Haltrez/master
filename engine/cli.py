"""Repost Radar command line.

    python cli.py add-search --label "Accountancy - Leeds" \
        --keywords "management accountant,financial accountant,finance manager,credit controller" \
        --location Leeds --radius 15
    python cli.py run --search-id 1
    python cli.py run-all
    python cli.py reed-sample --keywords "management accountant" --location Leeds
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.collect import run_all, run_search
from app.config import get_settings, setup_logging
from app.db import SupabaseRepo
from app.reed_client import ReedClient

FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "reed_search_sample.json"


def _reed(settings) -> ReedClient:
    return ReedClient(
        settings.reed_api_key,
        requests_per_second=settings.reed_requests_per_second,
        max_tries=settings.reed_max_tries,
        backoff_base=settings.reed_backoff_base_seconds,
    )


def _repo(settings) -> SupabaseRepo:
    return SupabaseRepo(settings.supabase_url, settings.supabase_service_role_key)


def _print_result(r) -> None:
    print(f"run {r.run_id} (search {r.search_id}): {r.status}")
    print(f"  api_calls={r.api_calls} listings_seen={r.listings_seen} new_listings={r.new_listings}")
    print(f"  employer={r.by_posted_by['employer']} agency={r.by_posted_by['agency']} "
          f"reclassified={r.reclassified}")
    if r.error:
        print(f"  error: {r.error}")


def cmd_run(args, settings) -> int:
    with _reed(settings) as reed:
        r = run_search(args.search_id, repo=_repo(settings), reed=reed, settings=settings)
    _print_result(r)
    return 0 if r.status == "ok" else 1


def cmd_run_all(args, settings) -> int:
    with _reed(settings) as reed:
        results = run_all(repo=_repo(settings), reed=reed, settings=settings)
    if not results:
        print("No active searches. Add one with: python cli.py add-search ...")
    for r in results:
        _print_result(r)
    return 0 if all(r.status == "ok" for r in results) else 1


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cli.py", description="Repost Radar engine")
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

    s = sub.add_parser("reed-sample", help="save one real Reed response as the test fixture")
    s.add_argument("--keywords", default="management accountant")
    s.add_argument("--location", default="Leeds")
    s.add_argument("--radius", type=int, default=15)
    s.add_argument("--posted-by", choices=["employer", "agency"], default="employer")
    s.add_argument("--out", default=str(FIXTURE))
    s.set_defaults(fn=cmd_reed_sample)

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
