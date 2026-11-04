"""Seed data for Role Match. supabase/seed_role_match.sql mirrors this (a test checks)."""

ROLE_FAMILIES: list[dict] = [
    {"name": "Electrical Estimator",
     "exact_titles": ["electrical estimator", "senior electrical estimator", "m&e estimator", "estimator (electrical)"],
     "related_titles": ["mechanical estimator", "quantity surveyor (m&e)", "pre-construction engineer (electrical)"]},
    {"name": "Mechanical Pre-Construction Manager",
     "exact_titles": ["mechanical pre-construction manager", "pre-construction manager (mechanical)",
                      "mechanical precon manager", "mechanical pre-contracts manager"],
     "related_titles": ["pre-construction manager", "mep preconstruction manager", "mechanical design manager",
                        "mechanical estimating manager"]},
    {"name": "Electrical Pre-Construction Manager",
     "exact_titles": ["electrical pre-construction manager", "pre-construction manager (electrical)",
                      "electrical precon manager", "electrical pre-contracts manager"],
     "related_titles": ["pre-construction manager", "mep preconstruction manager", "electrical design manager",
                        "electrical estimating manager"]},
    {"name": "Mechanical Design Engineer",
     "exact_titles": ["mechanical design engineer", "mechanical building services engineer",
                      "mechanical engineer (building services)", "hvac design engineer"],
     "related_titles": ["senior mechanical engineer (building services)", "mechanical design manager",
                        "building services design engineer"]},
    {"name": "Electrical Site Manager",
     "exact_titles": ["electrical site manager", "m&e site manager", "electrical site supervisor"],
     "related_titles": ["electrical supervisor", "m&e supervisor", "electrical contracts manager"]},
    {"name": "Electrical Project Manager",
     "exact_titles": ["electrical project manager", "m&e project manager"],
     "related_titles": ["electrical contracts manager", "trainee electrical project manager",
                        "electrical project engineer"]},
]

SEED_EMPLOYERS: list[dict] = [
    {"name": "DL M&E Building Services Ltd", "town": "Westhoughton, Bolton",
     "careers_url": "https://www.dl-bsl.co.uk/job-board", "website": "https://www.dl-bsl.co.uk"},
    {"name": "Central Electrical & Mechanical NW Ltd", "town": "Manchester",
     "careers_url": "https://celectrical.com/careers/", "website": "https://celectrical.com"},
    {"name": "B-engineering (NW) Ltd", "town": "Warrington",
     "careers_url": "https://b-enggroup.co.uk/jobs/", "website": "https://b-enggroup.co.uk"},
]

# Sector keywords from the spec's scoring table; briefs can add their own.
DEFAULT_SECTOR_KEYWORDS = ["m&e", "building services", "hvac", "electrical installation", "commercial"]

# Companies House SIC codes for the "to research" queue.
SEED_SIC_CODES = {
    "43210": "Electrical installation",
    "43220": "Plumbing, heat and air-conditioning installation",
    "43290": "Other construction installation",
    "71129": "Other engineering activities",
}

ROLE_MATCH_DM = (
    "Hey {first_name}, quick one. Got any candidates you're sitting on right now? Send me the roles "
    "and locations (no names needed) and I'll find direct employers hiring those exact roles nearby. "
    "Free, no strings."
)
