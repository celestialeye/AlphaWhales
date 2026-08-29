from __future__ import annotations

FORM_FAMILIES = {
    "institutional_holdings": (
        "13F-HR",
        "13F-HR/A",
        "13F-NT",
        "13F-NT/A",
    ),
    "registered_fund_portfolios": (
        "NPORT-P",
        "NPORT-P/A",
        "NPORT-EX",
        "N-Q",
        "N-Q/A",
    ),
    "money_market_funds": (
        "N-MFP",
        "N-MFP/A",
        "N-MFP1",
        "N-MFP1/A",
        "N-MFP2",
        "N-MFP2/A",
        "N-MFP3",
        "N-MFP3/A",
        "NT N-MFP",
        "NT N-MFP1",
        "NT N-MFP2",
        "NT N-MFP3",
        "N-CR",
        "N-CR/A",
    ),
    "fund_shareholder_reports": (
        "N-CSR",
        "N-CSR/A",
        "N-CSRS",
        "N-CSRS/A",
    ),
    "fund_census": (
        "N-CEN",
        "N-CEN/A",
    ),
    "insider_ownership": (
        "3",
        "3/A",
        "4",
        "4/A",
        "5",
        "5/A",
    ),
    "beneficial_ownership": (
        "SCHEDULE 13D",
        "SCHEDULE 13D/A",
        "SCHEDULE 13G",
        "SCHEDULE 13G/A",
        "SC 13D",
        "SC 13D/A",
        "SC 13G",
        "SC 13G/A",
    ),
    "proxy_voting": (
        "N-PX",
        "N-PX/A",
    ),
    "planned_insider_sales": (
        "144",
        "144/A",
    ),
}

FORM_TO_FAMILY = {
    form: family
    for family, forms in FORM_FAMILIES.items()
    for form in forms
}


def forms_for_family(family: str) -> tuple[str, ...]:
    if family == "all":
        return tuple(FORM_TO_FAMILY)
    try:
        return FORM_FAMILIES[family]
    except KeyError as exc:
        choices = ", ".join(sorted(FORM_FAMILIES))
        raise ValueError(f"Unknown filing family {family!r}; choose from {choices}") from exc


def family_for_form(form: str) -> str:
    return FORM_TO_FAMILY.get(form, "other")
