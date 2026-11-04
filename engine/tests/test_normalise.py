import pytest

from app.normalise import employer_norm, lead_key, location_norm, title_norm


@pytest.mark.parametrize("raw,norm", [
    ("Harlow Group Ltd", "harlow group"),
    ("Northgate Foods Limited", "northgate foods"),
    ("Acme UK Ltd.", "acme"),
    ("Acme (UK) Limited", "acme"),
    ("Smith & Jones LLP", "smith and jones"),
    ("Kirkstall Engineering plc", "kirkstall engineering"),
    ("Kirkstall Engineering PLC", "kirkstall engineering"),
    ("Big Holdings Ltd", "big holdings"),
    ("  HARLOW   GROUP  LTD ", "harlow group"),
    ("O'Neill & Co. Ltd", "o neill and co"),
    ("Ltd", "ltd"),                    # never empties a name
    ("Jobs UK", "jobs uk"),            # 'uk' alone is kept
])
def test_employer_norm(raw, norm):
    assert employer_norm(raw) == norm


SAME_ROLE = [
    ("Management Accountant", "Leeds"),
    ("Management Accountant - Hybrid (Leeds) £45k", "Leeds"),
    ("URGENT: Management Accountant", "Leeds"),
    ("Management Accountant | £40,000 - £45,000 + Benefits", "Leeds"),
    ("Management Accountant (Maternity Cover)", "Leeds"),
    ("Management Accountant - 12 Month FTC", "Leeds"),
    ("Management Accountant - Leeds", "Leeds, West Yorkshire"),
    ("Management Accountant in Leeds", "Leeds"),
    ("Management Accountant x2", "Leeds"),
    ("Management Accountant - Immediate Start", "Leeds"),
    ("Management Accountant (Hybrid) - up to 45k DOE", "Leeds"),
    ("Management Accountant – Full Time, Permanent", "Leeds"),
    ("Management Accountant - Leeds City Centre", "Leeds"),
]


@pytest.mark.parametrize("title,loc", SAME_ROLE)
def test_reposts_of_the_same_role_normalise_identically(title, loc):
    assert title_norm(title, loc) == "management accountant"


@pytest.mark.parametrize("title,norm", [
    ("Senior Management Accountant", "senior management accountant"),
    ("Accounts Assistant", "accounts assistant"),
    ("Junior Accounts Assistant - Remote", "junior accounts assistant"),
    ("Head of Finance", "head of finance"),
    ("Lead Software Engineer (Python)", "lead software engineer"),
    ("Assistant Management Accountant £30k", "assistant management accountant"),
    ("Credit Controller - Part Time", "credit controller"),
    ("Finance Manager - £55k + Bonus", "finance manager"),
    ("M&E Project Manager", "m and e project manager"),
    ("Payroll Administrator £12.50 per hour", "payroll administrator"),
    ("Purchase Ledger Clerk / Accounts Payable", "purchase ledger clerk accounts payable"),
])
def test_title_norm_keeps_role_and_seniority(title, norm):
    assert title_norm(title) == norm


def test_seniority_makes_different_keys():
    a = title_norm("Senior Management Accountant")
    b = title_norm("Management Accountant")
    c = title_norm("Accounts Assistant")
    assert len({a, b, c}) == 3


@pytest.mark.parametrize("raw,norm", [
    ("Leeds", "leeds"), ("Leeds, West Yorkshire", "leeds"), ("Leeds LS1 4AP", "leeds"),
    ("Manchester City Centre", "manchester"), ("Milton Keynes", "milton keynes"), (None, ""), ("", ""),
])
def test_location_norm(raw, norm):
    assert location_norm(raw) == norm


def test_lead_key():
    k = lead_key("harlow group", "management accountant", "leeds")
    assert k == lead_key("harlow group", "management accountant", "leeds") and len(k) == 40
    assert k != lead_key("harlow group", "senior management accountant", "leeds")
