from app.normalise import employer_norm, lead_key, location_norm, title_norm


def test_employer_norm():
    assert employer_norm("Harlow Group Ltd") == "harlow group"
    assert employer_norm("Northgate Foods Limited") == "northgate foods"
    assert employer_norm("Acme UK Ltd.") == "acme"
    assert employer_norm("Smith & Jones LLP") == "smith and jones"
    assert employer_norm("Kirkstall Engineering plc") == "kirkstall engineering"
    assert employer_norm("Big Holdings Ltd") == "big holdings"
    assert employer_norm("Ltd") == "ltd"  # never empties a name


def test_title_norm_basics():
    assert title_norm("Management Accountant - Hybrid (Leeds) £45k", "Leeds") == "management accountant"
    assert title_norm("Senior Management Accountant") == "senior management accountant"
    assert title_norm("Credit Controller (Maternity Cover)") == "credit controller"
    assert title_norm("URGENT Accounts Assistant - Immediate Start") == "accounts assistant"


def test_location_and_key():
    assert location_norm("Leeds, West Yorkshire") == "leeds"
    assert location_norm(None) == ""
    assert lead_key("a", "b", "c") == lead_key("a", "b", "c")
    assert len(lead_key("a", "b", "c")) == 40
