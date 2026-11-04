"""Industries a client agency can buy, sharp enough for Claude Haiku to judge each ad.

search:  job titles searched on every board (each costs a search per board, so keep it focused).
in/out:  what an agency specialising in this industry places, and what it doesn't. Haiku reads
         every candidate ad against this and says yes or no.
"""

from __future__ import annotations

INDUSTRIES: dict[str, dict] = {
    "Sales & Marketing": {
        "search": ["sales executive", "sales manager", "business development", "account manager",
                   "sales consultant", "area sales manager", "telesales", "marketing manager",
                   "marketing executive", "digital marketing"],
        "in": "B2B and B2C sales jobs (sales executive, business development, account manager/executive, key "
              "account, field/area sales, telesales/inside sales, sales manager/director, estate and lettings agency "
              "negotiators, car/vehicle sales, recruitment-free sales roles in any sector) and marketing jobs "
              "(marketing executive/manager, digital marketing, SEO/PPC, content, brand, product marketing, CRM "
              "marketing, marketing director).",
        "out": "Door-to-door or face-to-face charity fundraising and outsourced field-marketing teams (they are "
               "agencies, not employers), shop-floor retail (sales assistant, store/shop manager, cashier, "
               "optical/pharmacy assistant, bed/furniture showroom staff), "
               "customer service and call-centre support that isn't selling, hospitality, care, admin, "
               "engineering, IT, finance, legal and any job that only mentions sales in passing.",
    },
    "Accountancy & Finance": {
        "search": ["accountant", "finance manager", "financial controller", "credit controller", "payroll",
                   "accounts assistant", "finance director"],
        "in": "Accountants (management, financial, management/assistant, part-qualified, ACCA/ACA/CIMA), accounts "
              "assistants/payable/receivable, bookkeepers, credit control, payroll, finance managers, financial "
              "controllers, FP&A, finance business partners, finance directors/CFO, tax and audit roles.",
        "out": "Bank branch/cashier jobs, insurance sales, mortgage advisers, sales jobs, admin that isn't finance.",
    },
    "IT & Tech": {
        "search": ["software developer", "software engineer", "devops", "data analyst", "it support",
                   "network engineer", "data engineer"],
        "in": "Software/web/mobile developers and engineers, DevOps/cloud/SRE, data analysts/engineers/scientists, "
              "IT support and service desk, infrastructure/network/systems engineers, cyber security, QA/testers, "
              "IT/technical project managers, product managers in tech, CTO/IT managers.",
        "out": "Electrical/mechanical engineers, telecoms field installers, sales jobs at tech firms, admin roles "
               "that only use computers.",
    },
    "Engineering & Manufacturing": {
        "search": ["mechanical engineer", "electrical engineer", "maintenance engineer", "quality engineer",
                   "production manager", "cnc machinist", "design engineer"],
        "in": "Mechanical, electrical, electronic, design, process, quality, manufacturing, maintenance and "
              "controls engineers, CNC/machinists, toolmakers, fitters, technicians, production/operations/plant "
              "managers, engineering managers in manufacturing.",
        "out": "Software/IT engineers, construction site roles, warehouse operatives, cleaners, sales engineers "
               "selling products, domestic appliance repair call-out jobs.",
    },
    "Construction & Property": {
        "search": ["site manager", "quantity surveyor", "estimator", "contracts manager", "building surveyor",
                   "project manager construction", "civil engineer"],
        "in": "Site/contracts/project managers in construction, quantity surveyors, estimators, building and "
              "property surveyors, civil/structural engineers, construction planners, design managers, "
              "facilities and property managers, skilled trades supervisors.",
        "out": "Estate agency sales, letting negotiators, cleaners, general labourers via agencies, IT project managers.",
    },
    "HR": {
        "search": ["hr advisor", "hr manager", "hr business partner", "hr administrator", "learning and development"],
        "in": "HR advisors/officers/administrators/managers/business partners/directors, people partners, L&D, "
              "reward and benefits, employee relations, HR systems, in-house talent acquisition.",
        "out": "Recruitment consultants at agencies, payroll-only roles, general admin.",
    },
    "Legal": {
        "search": ["solicitor", "paralegal", "legal secretary", "conveyancer", "legal counsel"],
        "in": "Solicitors, associates, partners, legal counsel, paralegals, legal secretaries/assistants, "
              "conveyancers, legal executives, compliance lawyers.",
        "out": "Court/security staff, non-legal admin at law firms, debt collection, claims handlers.",
    },
    "Healthcare & Care": {
        "search": ["nurse", "care assistant", "support worker", "healthcare assistant", "care home manager",
                   "occupational therapist"],
        "in": "Nurses, care/support workers, healthcare assistants, care home and domiciliary care managers, "
              "allied health (OT, physio, speech therapy), pharmacists, dental nurses, social workers.",
        "out": "Pharmacy retail assistants, healthcare sales reps, receptionists, cleaners, admin.",
    },
    "Logistics & Warehouse": {
        "search": ["warehouse operative", "hgv driver", "transport manager", "logistics coordinator",
                   "forklift driver", "warehouse manager"],
        "in": "Warehouse operatives/supervisors/managers, forklift/FLT drivers, HGV/LGV/van drivers, transport "
              "planners/managers, logistics/supply-chain coordinators and managers, stock controllers.",
        "out": "Retail store staff, couriers who are self-employed, delivery gig work, manufacturing engineers.",
    },
    "Office & Admin": {
        "search": ["office manager", "administrator", "receptionist", "personal assistant", "office administrator"],
        "in": "Administrators, office managers, receptionists, PAs/EAs, secretaries, data entry, office "
              "coordinators, customer service administrators.",
        "out": "Specialist roles that happen to include admin (accounts, HR, legal secretary), sales, care.",
    },
    "Hospitality": {
        "search": ["chef", "restaurant manager", "hotel manager", "head chef", "bar manager"],
        "in": "Chefs (commis to head/executive), kitchen managers, restaurant/bar/hotel/general managers, front "
              "of house managers, events/catering managers.",
        "out": "Retail, cleaning-only jobs, care home cooks hired via care agencies, delivery drivers.",
    },
    "Education": {
        "search": ["teacher", "teaching assistant", "lecturer", "senco"],
        "in": "Teachers, teaching assistants, SENCOs, lecturers, tutors employed by schools/colleges, school "
              "business managers, head/deputy heads.",
        "out": "Private tutoring platforms, nursery chains' admin, sports coaches booked per session.",
    },
}


def get(name: str) -> dict | None:
    for k, v in INDUSTRIES.items():
        if k.lower() == (name or "").strip().lower():
            return {"name": k, **v}
    return None


def definition(name: str, custom_keywords: list[str] | None = None) -> dict:
    """The industry definition for an order; a free-text industry becomes a plain definition."""
    d = get(name)
    if d:
        return d
    kws = list(custom_keywords or []) or [name.strip().lower()]
    return {"name": name.strip(), "search": kws,
            "in": f"Jobs that belong to the {name.strip()} field, such as: {', '.join(kws)}.",
            "out": "Jobs from other fields, even if the ad mentions this field in passing."}
