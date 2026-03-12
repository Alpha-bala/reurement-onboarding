def calculate_new_tax_annually(ctc):
    slabs = [
        (0, 1200000, 0.00),
        (1200001, 1600000, 0.15),
        (1600001, 2000000, 0.20),
        (2000001, 2400000, 0.25),
        (2400001, float('inf'), 0.30),
    ]
    for lower, upper, rate in slabs:
        if lower < ctc <= upper:
            return round(ctc * rate, 2)
    return 0.0


def calculate_old_tax_annually(ctc):
    if ctc <= 0:
        return 0.0

    slabs = [
        (0, 400000, 0.00),
        (400001, 800000, 0.05),
        (800001, 1200000, 0.10),
        (1200001, 1600000, 0.15),
        (1600001, 2000000, 0.20),
        (2000001, 2400000, 0.25),
        (2400001, float('inf'), 0.30),
    ]
    for lower, upper, rate in slabs:
        if lower <= ctc <= upper:
            return round(ctc * rate, 2)
    return 0.0


def compute_salary_components(annual_ctc: float, regime: str = "new") -> dict:
    monthly_ctc = round(annual_ctc / 12, 2)

    # Main earnings
    basic_monthly = round(monthly_ctc * 0.50, 2)
    basic_annual = round(basic_monthly * 12, 2)

    hra_monthly = round(basic_monthly * 0.50, 2)
    hra_annual = round(hra_monthly * 12, 2)

    food_monthly = round(basic_monthly * 0.15, 2)
    food_annual = round(food_monthly * 12, 2)

    special_monthly = round(basic_monthly * 0.15, 2)
    special_annual = round(special_monthly * 12, 2)

    other_monthly = round(basic_monthly * 0.20, 2)
    other_annual = round(other_monthly * 12, 2)

    # Gratuity (if any)

    # Employer contribution
    pf_employer = round(basic_monthly * 0.12, 2)
    total_ctc = round(annual_ctc + pf_employer * 12, 2)

    # Deductions
    pf_monthly = round(basic_monthly * 0.12, 2)
    pf_annual = round(pf_monthly * 12, 2)

    pt_monthly = 200.0
    pt_annual = round(pt_monthly * 12, 2)

    health_monthly = round(2220 / 12, 2)
    health_annual = round(health_monthly * 12, 2)

    # Tax Regime
    if regime == "new":
        tds_annual = calculate_new_tax_annually(annual_ctc)
    else:
        tds_annual = calculate_old_tax_annually(annual_ctc)
    tds_monthly = round(tds_annual / 12, 2)

    # Net salary
    gross_monthly = basic_monthly + hra_monthly + food_monthly + special_monthly + other_monthly
    total_deductions = pf_monthly + pt_monthly + tds_monthly + health_monthly
    net_monthly = round(gross_monthly - total_deductions, 2)
    net_annual = round(net_monthly * 12, 2)

    return {
        "monthly_ctc": monthly_ctc,
        "annual_ctc": annual_ctc,

        # Earnings
        "basic_monthly": basic_monthly,
        "basic_annual": basic_annual,
        "hra_monthly": hra_monthly,
        "hra_annual": hra_annual,
        "food_monthly": food_monthly,
        "food_annual": food_annual,
        "special_monthly": special_monthly,
        "special_annual": special_annual,
        "other_monthly": other_monthly,
        "other_annual": other_annual,

        # Deductions
        "pf_monthly": pf_monthly,
        "pf_annual": pf_annual,
        "pt_monthly": pt_monthly,
        "pt_annual": pt_annual,
        "health_monthly": health_monthly,
        "health_annual": health_annual,
        "tds_monthly": tds_monthly,
        "tds_annual": tds_annual,
        "net_monthly": net_monthly,
        "net_annual": net_annual,
    }