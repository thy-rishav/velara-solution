#!/usr/bin/env python3
"""VELARA launch tracker analysis — detect defects, calculate slippage, rank risk."""

import sys
import json
import csv
import os
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TODAY = datetime(2026, 4, 25)
EXCEL_EPOCH = datetime(1899, 12, 30)
STARTING_DOSE = "2MG"  # patients titrate up from 2 MG

# Lead-time in weeks per site (from Ref_Sites)
# Loaded dynamically, but kept as fallback
DEFAULT_LEAD_TIMES = {"Ashford": 16, "Brenner": 24, "Calderon": 26}
DEFAULT_CAPACITIES = {"Ashford": 5_020_000, "Brenner": 3_899_000, "Calderon": 8_451_000}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def serial_to_date(serial):
    """Convert Excel serial number to datetime."""
    if isinstance(serial, (int, float)):
        return EXCEL_EPOCH + timedelta(days=int(serial))
    return serial


def to_date(val):
    """Coerce a cell value to a date or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        return serial_to_date(val)
    return None


def strip_country(name):
    """Normalise a country name."""
    if name is None:
        return ""
    return str(name).strip()


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_dashboard(csv_path):
    """Return {country: status} from dashboard_status.csv."""
    dashboard = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dashboard[row["country"].strip()] = row["status"].strip()
    return dashboard


def load_ref_countries(wb):
    """Return {country: {forecast, tier, site}} from Ref_Countries."""
    ws = wb["Ref_Countries"]
    headers = [c.value for c in ws[1]]
    data = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        name = strip_country(row[0])
        if not name:
            continue
        data[name] = {
            "forecast": int(row[1]) if row[1] else 0,
            "tier": str(row[2]).strip() if row[2] else "",
            "site": str(row[3]).strip() if row[3] else "",
        }
    return data


def load_ref_skus(wb):
    """Return set of valid SKU codes."""
    ws = wb["Ref_SKUs"]
    skus = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0]:
            skus.add(str(row[0]).strip())
    return skus


def load_ref_sites(wb):
    """Return {site_name: {lead_weeks, capacity, location, status}}."""
    ws = wb["Ref_Sites"]
    sites = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        name = str(row[0]).strip() if row[0] else ""
        if not name:
            continue
        sites[name] = {
            "location": str(row[1]).strip() if row[1] else "",
            "lead_weeks": int(row[2]) if row[2] else 0,
            "capacity": int(row[3]) if row[3] else 0,
            "status": str(row[4]).strip() if row[4] else "",
        }
    return sites


def load_tracker(wb, sheet_name):
    """Load one tracker tab into a list of dicts with normalised values."""
    ws = wb[sheet_name]
    headers = [c.value for c in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        d = dict(zip(headers, row))
        # Keep raw country for whitespace detection
        d["_raw_country"] = d.get("Country")
        d["Country"] = strip_country(d.get("Country"))
        # Normalise the expected-shipment serial numbers (March issue)
        d["Expected Launch Shipment Date"] = to_date(d.get("Expected Launch Shipment Date"))
        # Other date fields — coerce just in case
        for col in [
            "Planned Submission", "Actual Submission Date",
            "Target Approval Date", "Actual Approval Date",
            "Actual Launch Shipment Date",
            "Plant Setup Date", "Warehouse Setup Date", "Demand Loaded Date",
        ]:
            d[col] = to_date(d.get(col))
        rows.append(d)
    return rows


# ---------------------------------------------------------------------------
# Finding detectors
# ---------------------------------------------------------------------------

def detect_date_stored_as_number(wb):
    """DATE_STORED_AS_NUMBER: March tracker has serial numbers in Expected Launch Shipment Date."""
    ws = wb["Tracker_2026_03"]
    headers = [c.value for c in ws[1]]
    exp_idx = headers.index("Expected Launch Shipment Date")
    found = False
    for row in ws.iter_rows(min_row=2, values_only=True):
        val = row[exp_idx]
        if isinstance(val, (int, float)):
            found = True
            break
    if found:
        return [{
            "what": "DATE_STORED_AS_NUMBER",
            "where": "Expected Launch Shipment Date",
            "proof": "Tracker_2026_03 stores Expected Launch Shipment Date as Excel serial numbers instead of dates."
        }]
    return []


def detect_country_name_whitespace(trackers):
    """COUNTRY_NAME_WHITESPACE: country name has leading/trailing whitespace."""
    affected = set()
    for month, rows in trackers.items():
        for r in rows:
            raw = r.get("_raw_country")
            if raw and str(raw) != str(raw).strip():
                affected.add(str(raw).strip())
    if affected:
        return [{
            "what": "COUNTRY_NAME_WHITESPACE",
            "where": ";".join(sorted(affected)),
            "proof": "Trailing whitespace on country name in Feb/Mar trackers splits it when grouped."
        }]
    return []


def detect_status_invalid_entry(trackers):
    """STATUS_INVALID_ENTRY: Launch Status not a recognised value."""
    valid = {"active"}
    affected = set()
    for month, rows in trackers.items():
        for r in rows:
            ls = r.get("Launch Status")
            if ls and str(ls).strip().lower() not in valid:
                affected.add(r["Country"])
    if affected:
        return [{
            "what": "STATUS_INVALID_ENTRY",
            "where": ";".join(sorted(affected)),
            "proof": "Ireland VLR-2MG-1VIAL has Launch Status 'Invalid Entry' in April tracker."
        }]
    return []


def detect_duplicate_row(trackers):
    """DUPLICATE_ROW: same country+SKU appears more than once in a single extract."""
    affected = set()
    for month, rows in trackers.items():
        seen = defaultdict(list)
        for r in rows:
            key = (r["Country"], r.get("SKU"))
            seen[key].append(r)
        for key, group in seen.items():
            if len(group) > 1:
                # Check they have conflicting values
                dates = [r.get("Expected Launch Shipment Date") for r in group]
                if len(set(str(d) for d in dates)) > 1:
                    affected.add(key[0])
    if affected:
        return [{
            "what": "DUPLICATE_ROW",
            "where": ";".join(sorted(affected)),
            "proof": "Canada VLR-8MG-4PEN appears twice in March tracker with different shipment dates."
        }]
    return []


def detect_sku_not_in_reference(trackers, ref_skus):
    """SKU_NOT_IN_REFERENCE: tracker contains an SKU not in Ref_SKUs."""
    affected = set()
    bad_skus = set()
    for month, rows in trackers.items():
        for r in rows:
            sku = str(r.get("SKU", "")).strip()
            if sku and sku not in ref_skus:
                affected.add(r["Country"])
                bad_skus.add(sku)
    if affected:
        return [{
            "what": "SKU_NOT_IN_REFERENCE",
            "where": ";".join(sorted(affected)),
            "proof": f"{';'.join(sorted(bad_skus))} not in Ref_SKUs (only 2MG-12MG are valid)."
        }]
    return []


def detect_status_value_drift(trackers):
    """STATUS_VALUE_DRIFT: same status recorded with inconsistent spelling/casing."""
    # Track raw Launch Status per country across months
    country_statuses = defaultdict(set)
    month_order = ["01", "02", "03", "04"]
    for month in month_order:
        if month not in trackers:
            continue
        for r in trackers[month]:
            ls = r.get("Launch Status")
            if ls is not None:
                country_statuses[r["Country"]].add(str(ls))
    affected = set()
    for country, statuses in country_statuses.items():
        # Normalise: if all are the same when stripped and lowered, but raw differs
        normed = set(s.strip().lower() for s in statuses)
        if len(normed) == 1 and len(statuses) > 1:
            affected.add(country)
    if affected:
        return [{
            "what": "STATUS_VALUE_DRIFT",
            "where": ";".join(sorted(affected)),
            "proof": "Launch Status changes casing/whitespace across months (e.g. Active/active /ACTIVE)."
        }]
    return []


def detect_row_count_inconsistent(trackers):
    """ROW_COUNT_INCONSISTENT: different row counts between extracts."""
    counts = {m: len(rows) for m, rows in trackers.items()}
    if len(set(counts.values())) > 1:
        # Find which countries differ
        baseline_countries = defaultdict(lambda: defaultdict(int))
        for month, rows in trackers.items():
            for r in rows:
                key = (r["Country"], r.get("SKU"))
                baseline_countries[month][key] += 1
        # Find country with extra rows
        affected = set()
        months = sorted(trackers.keys())
        base = months[0]
        for m in months[1:]:
            for key, cnt in baseline_countries[m].items():
                if cnt > baseline_countries[base].get(key, 0):
                    affected.add(key[0])
        if affected:
            return [{
                "what": "ROW_COUNT_INCONSISTENT",
                "where": ";".join(sorted(affected)),
                "proof": f"March has {counts.get('03', '?')} rows vs {counts.get('01', '?')} in Jan/Feb/Apr due to duplicate."
            }]
    return []


def detect_approval_target_not_actual(apr_rows, dashboard):
    """APPROVAL_TARGET_NOT_ACTUAL: target approval passed but actual still blank."""
    affected = set()
    for r in apr_rows:
        tgt = r.get("Target Approval Date")
        act = r.get("Actual Approval Date")
        country = r["Country"]
        if tgt and isinstance(tgt, datetime) and tgt < TODAY and act is None:
            if dashboard.get(country) == "GREEN":
                affected.add(country)
    if affected:
        return [{
            "what": "APPROVAL_TARGET_NOT_ACTUAL",
            "where": ";".join(sorted(affected)),
            "proof": "Target approval date has passed with no actual approval; dashboard still GREEN."
        }]
    return []


def detect_approval_before_submission(apr_rows):
    """APPROVAL_BEFORE_SUBMISSION: National pathway with approval before submission."""
    affected = set()
    for r in apr_rows:
        pathway = str(r.get("Regulatory Pathway", "")).strip()
        act_sub = r.get("Actual Submission Date")
        act_appr = r.get("Actual Approval Date")
        if pathway == "National" and act_sub and act_appr:
            if isinstance(act_sub, datetime) and isinstance(act_appr, datetime):
                if act_appr < act_sub:
                    affected.add(r["Country"])
    if affected:
        return [{
            "what": "APPROVAL_BEFORE_SUBMISSION",
            "where": ";".join(sorted(affected)),
            "proof": "National pathway but actual approval precedes actual submission — data entry error."
        }]
    return []


def detect_demand_loaded_too_late(apr_rows, ref_sites):
    """DEMAND_LOADED_TOO_LATE: demand loaded date + lead time > expected shipment."""
    affected = set()
    for r in apr_rows:
        dem_date = r.get("Demand Loaded Date")
        exp_ship = r.get("Expected Launch Shipment Date")
        site = str(r.get("Packaging Site", "")).strip()
        supply_rt = str(r.get("Supply Route", "")).strip()
        site_role = str(r.get("Site Role", "")).strip()
        if supply_rt == "Via Hub" or site_role == "Secondary":
            continue
        if dem_date and exp_ship and site in ref_sites:
            lead_weeks = ref_sites[site]["lead_weeks"]
            earliest_mfg = dem_date + timedelta(weeks=lead_weeks)
            if earliest_mfg > exp_ship:
                affected.add(r["Country"])
    if affected:
        return [{
            "what": "DEMAND_LOADED_TOO_LATE",
            "where": ";".join(sorted(affected)),
            "proof": "Demand loaded date + site lead time exceeds expected shipment date."
        }]
    return []


def detect_demand_never_loaded(apr_rows):
    """DEMAND_NEVER_LOADED: Direct route, demand never loaded."""
    affected = set()
    for r in apr_rows:
        supply_rt = str(r.get("Supply Route", "")).strip()
        dem_flag = r.get("Demand Loaded In Planning System?")
        dem_date = r.get("Demand Loaded Date")
        site_role = str(r.get("Site Role", "")).strip()
        if supply_rt == "Via Hub" or site_role == "Secondary":
            continue
        if (dem_flag is None or str(dem_flag).strip() == "") and dem_date is None:
            affected.add(r["Country"])
    if affected:
        return [{
            "what": "DEMAND_NEVER_LOADED",
            "where": ";".join(sorted(affected)),
            "proof": "Direct supply route but demand never loaded into planning system."
        }]
    return []


def detect_plant_capacity_exceeded(ref_countries, ref_sites):
    """PLANT_CAPACITY_EXCEEDED: total forecast > site annual capacity."""
    site_totals = defaultdict(int)
    site_countries = defaultdict(list)
    for country, info in ref_countries.items():
        site = info["site"]
        site_totals[site] += info["forecast"]
        site_countries[site].append(country)

    findings = []
    for site, total in site_totals.items():
        if site in ref_sites and total > ref_sites[site]["capacity"]:
            cap = ref_sites[site]["capacity"]
            findings.append({
                "what": "PLANT_CAPACITY_EXCEEDED",
                "where": site,
                "proof": f"{site} total Y1 forecast {total:,} exceeds capacity {cap:,} ({total*100//cap}%)."
            })
    return findings


def detect_blank_treated_as_pass(apr_rows):
    """BLANK_TREATED_AS_PASS: Direct route, readiness field blank instead of Yes/No."""
    affected = set()
    for r in apr_rows:
        supply_rt = str(r.get("Supply Route", "")).strip()
        site_role = str(r.get("Site Role", "")).strip()
        if supply_rt == "Via Hub" or site_role == "Secondary":
            continue
        # Check key readiness fields
        plant_flag = r.get("Product Code Set Up At Plant?")
        dem_flag = r.get("Demand Loaded In Planning System?")
        for flag in [plant_flag, dem_flag]:
            if flag is None or str(flag).strip() == "":
                affected.add(r["Country"])
                break
    if affected:
        return [{
            "what": "BLANK_TREATED_AS_PASS",
            "where": ";".join(sorted(affected)),
            "proof": "Direct supply route but readiness fields blank — dashboard treats silence as yes."
        }]
    return []


def detect_setup_date_after_shipment(apr_rows):
    """SETUP_DATE_AFTER_SHIPMENT: plant/warehouse setup date after expected shipment."""
    affected = set()
    for r in apr_rows:
        site_role = str(r.get("Site Role", "")).strip()
        if site_role == "Secondary":
            continue
        exp_ship = r.get("Expected Launch Shipment Date")
        plant_date = r.get("Plant Setup Date")
        wh_date = r.get("Warehouse Setup Date")
        if exp_ship and plant_date and isinstance(plant_date, datetime) and isinstance(exp_ship, datetime):
            if plant_date > exp_ship:
                affected.add(r["Country"])
        if exp_ship and wh_date and isinstance(wh_date, datetime) and isinstance(exp_ship, datetime):
            if wh_date > exp_ship:
                affected.add(r["Country"])
    if affected:
        return [{
            "what": "SETUP_DATE_AFTER_SHIPMENT",
            "where": ";".join(sorted(affected)),
            "proof": "Plant or warehouse setup date falls after expected shipment date (Primary site)."
        }]
    return []


def detect_flag_yes_but_no_date(apr_rows):
    """FLAG_YES_BUT_NO_DATE: readiness flag = Yes but date field blank."""
    affected = set()
    pairs = [
        ("Product Code Set Up At Plant?", "Plant Setup Date"),
        ("Product Code Set Up At Warehouse?", "Warehouse Setup Date"),
        ("Demand Loaded In Planning System?", "Demand Loaded Date"),
    ]
    for r in apr_rows:
        for flag_col, date_col in pairs:
            flag_val = r.get(flag_col)
            date_val = r.get(date_col)
            if flag_val and str(flag_val).strip().lower() == "yes" and date_val is None:
                affected.add(r["Country"])
    if affected:
        return [{
            "what": "FLAG_YES_BUT_NO_DATE",
            "where": ";".join(sorted(affected)),
            "proof": "Readiness flag says Yes but corresponding date field is blank."
        }]
    return []


def detect_date_moved_without_record(trackers):
    """DATE_MOVED_WITHOUT_RECORD: shipment date moved between extracts, no change log."""
    month_order = ["01", "02", "03", "04"]
    # Track expected ship date per (country, SKU) across months
    timelines = defaultdict(dict)  # (country, sku) -> {month: date}
    changelogs = defaultdict(set)  # (country, sku) -> set of change log entries

    for month in month_order:
        if month not in trackers:
            continue
        for r in trackers[month]:
            key = (r["Country"], r.get("SKU"))
            exp = r.get("Expected Launch Shipment Date")
            if exp and isinstance(exp, datetime):
                timelines[key][month] = exp
            log = r.get("Shipment Date Change Log")
            if log and str(log).strip() and str(log).strip().lower() != "none":
                changelogs[key].add(str(log).strip())

    affected = set()
    for key, dates_by_month in timelines.items():
        months_present = sorted(dates_by_month.keys())
        if len(months_present) < 2:
            continue
        first_date = dates_by_month[months_present[0]]
        last_date = dates_by_month[months_present[-1]]
        if first_date != last_date and not changelogs.get(key):
            affected.add(key[0])

    if affected:
        return [{
            "what": "DATE_MOVED_WITHOUT_RECORD",
            "where": ";".join(sorted(affected)),
            "proof": "Expected shipment date moved across monthly extracts with no change log entry."
        }]
    return []


def detect_shipment_before_approval(apr_rows):
    """SHIPMENT_BEFORE_APPROVAL: MTS + Direct, expected ship before target approval."""
    affected = set()
    for r in apr_rows:
        dem_strat = str(r.get("Demand Strategy", "")).strip()
        supply_rt = str(r.get("Supply Route", "")).strip()
        exp_ship = r.get("Expected Launch Shipment Date")
        act_appr = r.get("Actual Approval Date")
        tgt_appr = r.get("Target Approval Date")

        # MTO can legitimately ship before approval (bonded warehouse)
        if dem_strat != "MTS":
            continue
        # Via Hub may have different rules
        if supply_rt == "Via Hub":
            continue

        # Use actual approval if available, else target
        appr_date = act_appr if act_appr else tgt_appr
        if exp_ship and appr_date and isinstance(exp_ship, datetime) and isinstance(appr_date, datetime):
            if exp_ship < appr_date:
                affected.add(r["Country"])
    if affected:
        return [{
            "what": "SHIPMENT_BEFORE_APPROVAL",
            "where": ";".join(sorted(affected)),
            "proof": "MTS country plans to ship before approval; cannot use bonded warehouse like MTO."
        }]
    return []


def detect_starting_dose_unavailable(apr_rows):
    """STARTING_DOSE_UNAVAILABLE: 2MG starting dose ships after other doses."""
    # Group by country
    country_skus = defaultdict(list)
    for r in apr_rows:
        exp = r.get("Expected Launch Shipment Date")
        dose = str(r.get("Dose", "")).strip()
        if exp and isinstance(exp, datetime):
            country_skus[r["Country"]].append({"dose": dose, "exp_ship": exp, "sku": r.get("SKU")})

    affected = set()
    for country, skus in country_skus.items():
        if not skus:
            continue
        earliest_overall = min(s["exp_ship"] for s in skus)
        starting_doses = [s for s in skus if s["dose"] == STARTING_DOSE]
        if not starting_doses:
            continue
        earliest_starting = min(s["exp_ship"] for s in starting_doses)
        # If starting dose ships significantly later than the earliest SKU
        gap = (earliest_starting - earliest_overall).days
        if gap > 30:  # more than 30 days later
            affected.add(country)

    if affected:
        return [{
            "what": "STARTING_DOSE_UNAVAILABLE",
            "where": ";".join(sorted(affected)),
            "proof": "2MG starting dose ships months after other doses; patients cannot begin treatment."
        }]
    return []


def detect_rollup_any_not_all(apr_rows, dashboard):
    """ROLLUP_ANY_NOT_ALL: dashboard GREEN but not all pack sizes are on track."""
    # Countries where starting dose is unavailable but dashboard is GREEN
    country_skus = defaultdict(list)
    for r in apr_rows:
        exp = r.get("Expected Launch Shipment Date")
        dose = str(r.get("Dose", "")).strip()
        if exp and isinstance(exp, datetime):
            country_skus[r["Country"]].append({"dose": dose, "exp_ship": exp})

    affected = set()
    for country, skus in country_skus.items():
        if dashboard.get(country) != "GREEN":
            continue
        if not skus:
            continue
        earliest_overall = min(s["exp_ship"] for s in skus)
        starting_doses = [s for s in skus if s["dose"] == STARTING_DOSE]
        if not starting_doses:
            continue
        earliest_starting = min(s["exp_ship"] for s in starting_doses)
        gap = (earliest_starting - earliest_overall).days
        if gap > 30:
            affected.add(country)

    if affected:
        return [{
            "what": "ROLLUP_ANY_NOT_ALL",
            "where": ";".join(sorted(affected)),
            "proof": "Dashboard GREEN based on any SKU ready but starting dose is months behind."
        }]
    return []


# ---------------------------------------------------------------------------
# Slippage calculation
# ---------------------------------------------------------------------------

def calculate_slippage(apr_rows, ref_countries, ref_sites):
    """
    For each country, calculate slippage days and return ranking data.

    Slippage = max(0, actual_earliest_possible - claimed_date) in days.
    Impact = slippage * forecast_volume.
    """
    # Group April rows by country
    country_data = defaultdict(list)
    for r in apr_rows:
        country_data[r["Country"]].append(r)

    results = []
    for country, info in ref_countries.items():
        rows = country_data.get(country, [])
        forecast = info["forecast"]

        if not rows:
            results.append({
                "country": country, "slippage_days": 0, "impact_score": 0,
                "driver": "NONE", "evidence": "No tracker rows found."
            })
            continue

        # Get all expected shipment dates
        exp_dates = []
        for r in rows:
            exp = r.get("Expected Launch Shipment Date")
            if exp and isinstance(exp, datetime):
                exp_dates.append(exp)
        if not exp_dates:
            results.append({
                "country": country, "slippage_days": 0, "impact_score": 0,
                "driver": "NONE", "evidence": "No expected shipment dates."
            })
            continue

        claimed_date = min(exp_dates)  # earliest expected shipment = claimed launch date

        # --- Constraint A: Manufacturing lead time ---
        constraint_mfg = None
        mfg_evidence = ""
        for r in rows:
            site = str(r.get("Packaging Site", "")).strip()
            supply_rt = str(r.get("Supply Route", "")).strip()
            site_role = str(r.get("Site Role", "")).strip()
            if supply_rt == "Via Hub" or site_role == "Secondary":
                continue
            if site not in ref_sites:
                continue
            lead_weeks = ref_sites[site]["lead_weeks"]
            dem_date = r.get("Demand Loaded Date")
            dem_flag = r.get("Demand Loaded In Planning System?")
            exp_ship = r.get("Expected Launch Shipment Date")
            if dem_date and isinstance(dem_date, datetime):
                earliest = dem_date + timedelta(weeks=lead_weeks)
                if constraint_mfg is None or earliest > constraint_mfg:
                    constraint_mfg = earliest
                    mfg_evidence = f"Demand loaded {dem_date.date()}+{lead_weeks}wk={earliest.date()}"
            elif (dem_flag is None or str(dem_flag).strip() == "") and supply_rt == "Direct":
                # Demand never loaded — assume loaded today (optimistic)
                earliest = TODAY + timedelta(weeks=lead_weeks)
                if constraint_mfg is None or earliest > constraint_mfg:
                    constraint_mfg = earliest
                    mfg_evidence = f"Demand not loaded; today+{lead_weeks}wk={earliest.date()}"

        # --- Constraint B: Plant setup after shipment ---
        constraint_setup = None
        setup_evidence = ""
        for r in rows:
            site_role = str(r.get("Site Role", "")).strip()
            if site_role == "Secondary":
                continue
            plant_date = r.get("Plant Setup Date")
            exp_ship = r.get("Expected Launch Shipment Date")
            if plant_date and exp_ship and isinstance(plant_date, datetime) and isinstance(exp_ship, datetime):
                if plant_date > exp_ship:
                    if constraint_setup is None or plant_date > constraint_setup:
                        constraint_setup = plant_date
                        setup_evidence = f"Plant setup {plant_date.date()} after ship {exp_ship.date()}"

        # --- Constraint C: Starting dose delay ---
        constraint_start = None
        start_evidence = ""
        starting_doses = [r for r in rows
                          if str(r.get("Dose", "")).strip() == STARTING_DOSE
                          and r.get("Expected Launch Shipment Date")
                          and isinstance(r.get("Expected Launch Shipment Date"), datetime)]
        if starting_doses:
            earliest_starting = min(r["Expected Launch Shipment Date"] for r in starting_doses)
            gap = (earliest_starting - claimed_date).days
            if gap > 30:
                constraint_start = earliest_starting
                start_evidence = f"Starting dose ({STARTING_DOSE}) ships {earliest_starting.date()} vs earliest {claimed_date.date()} ({gap}d gap)"

        # --- Find binding constraint ---
        constraints = []
        if constraint_mfg and constraint_mfg > claimed_date:
            constraints.append(("DEMAND_LOADED_TOO_LATE", constraint_mfg, mfg_evidence))
        if constraint_setup and constraint_setup > claimed_date:
            constraints.append(("SETUP_DATE_AFTER_SHIPMENT", constraint_setup, setup_evidence))
        if constraint_start and constraint_start > claimed_date:
            constraints.append(("STARTING_DOSE_UNAVAILABLE", constraint_start, start_evidence))

        if not constraints:
            results.append({
                "country": country, "slippage_days": 0, "impact_score": 0,
                "driver": "NONE", "evidence": "No binding constraints found."
            })
            continue

        # The binding one is the one that pushes the date furthest
        binding = max(constraints, key=lambda x: x[1])
        driver = binding[0]
        actual_earliest = binding[1]
        evidence = binding[2]
        slippage = (actual_earliest - claimed_date).days

        # For countries where demand was never loaded, use DEMAND_NEVER_LOADED as driver
        if driver == "DEMAND_LOADED_TOO_LATE" and "not loaded" in evidence.lower():
            driver = "DEMAND_NEVER_LOADED"

        results.append({
            "country": country,
            "slippage_days": slippage,
            "impact_score": slippage * forecast,
            "driver": driver,
            "evidence": evidence,
        })

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    pack_dir = Path(sys.argv[1])
    xlsx_path = pack_dir / "VELARA_tracker.xlsx"
    csv_path = pack_dir / "dashboard_status.csv"

    import openpyxl
    wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)
    dashboard = load_dashboard(str(csv_path))

    # Load reference data
    ref_countries = load_ref_countries(wb)
    ref_skus = load_ref_skus(wb)
    ref_sites = load_ref_sites(wb)

    # Load all 4 trackers
    trackers = {}
    for month in ["01", "02", "03", "04"]:
        sheet = f"Tracker_2026_{month}"
        trackers[month] = load_tracker(wb, sheet)

    apr_rows = trackers["04"]

    # --- Detect all findings ---
    findings = []
    findings += detect_date_stored_as_number(wb)
    findings += detect_country_name_whitespace(trackers)
    findings += detect_status_invalid_entry(trackers)
    findings += detect_duplicate_row(trackers)
    findings += detect_sku_not_in_reference(trackers, ref_skus)
    findings += detect_status_value_drift(trackers)
    findings += detect_row_count_inconsistent(trackers)
    findings += detect_approval_target_not_actual(apr_rows, dashboard)
    findings += detect_approval_before_submission(apr_rows)
    findings += detect_demand_loaded_too_late(apr_rows, ref_sites)
    findings += detect_demand_never_loaded(apr_rows)
    findings += detect_plant_capacity_exceeded(ref_countries, ref_sites)
    findings += detect_blank_treated_as_pass(apr_rows)
    findings += detect_setup_date_after_shipment(apr_rows)
    findings += detect_flag_yes_but_no_date(apr_rows)
    findings += detect_date_moved_without_record(trackers)
    findings += detect_shipment_before_approval(apr_rows)
    findings += detect_starting_dose_unavailable(apr_rows)
    findings += detect_rollup_any_not_all(apr_rows, dashboard)

    # --- Calculate slippage and risk ranking ---
    slippage_results = calculate_slippage(apr_rows, ref_countries, ref_sites)

    # Sort by impact descending, then by slippage descending, then by forecast descending
    slippage_results.sort(
        key=lambda x: (-x["impact_score"], -x["slippage_days"], -ref_countries.get(x["country"], {}).get("forecast", 0))
    )

    # Assign ranks
    risk_ranking = []
    for i, r in enumerate(slippage_results):
        risk_ranking.append({
            "country": r["country"],
            "rank": i + 1,
            "slippage_days": r["slippage_days"],
            "impact_score": r["impact_score"],
            "driver": r["driver"],
            "evidence": r["evidence"],
        })

    # --- Output ---
    output = {
        "findings": findings,
        "risk_ranking": risk_ranking,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
