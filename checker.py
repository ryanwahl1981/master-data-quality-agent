"""
Master Data Quality Checker (rules only, no AI yet)

HOW TO READ THIS FILE
- Section 1 holds YOUR RULES. You can edit these without touching any code logic.
- Section 2 applies the rules to each row and records a problem when one is broken.
- Section 3 saves the results to issues.csv and prints a summary.
"""
import pandas as pd

# ------------------------------------------------------------------
# SECTION 1: YOUR RULES (edit these)
# ------------------------------------------------------------------
VALID_UOM = {"EA", "PC"}                            # allowed units of measure
VALID_STATUS = {"ACTIVE", "INACTIVE", "OBSOLETE"}   # allowed status codes

# Abbreviations that should be spelled out (your idea!)
ABBREVIATIONS = {
    "RES": "RESISTOR",
    "CAP": "CAPACITOR",
    "CONN": "CONNECTOR",
    "FSTNR": "FASTENER",
    "PKG": "PACKAGING",
}

# ------------------------------------------------------------------
# SECTION 2: APPLY THE RULES
# ------------------------------------------------------------------
df = pd.read_csv("item_master_sample.csv", dtype=str, keep_default_na=False)
problems = []  # each problem is one line: item_id, issue, detail


def add(row, issue, detail):
    problems.append({"item_id": row["item_id"], "issue": issue, "detail": detail})


for _, row in df.iterrows():
    desc_raw = row["description"]
    desc = desc_raw.strip().upper()  # cleaned version used for comparisons

    # Blank checks
    if desc == "":
        add(row, "MISSING_DESCRIPTION", "description is blank")
    if row["supplier_id"].strip() == "":
        add(row, "MISSING_SUPPLIER", "supplier_id is blank")

    # Unit of measure must match the approved list EXACTLY
    if row["uom"] not in VALID_UOM:
        add(row, "INVALID_UOM", f"uom is '{row['uom']}'")

    # Status must be an approved code
    if row["status"] not in VALID_STATUS:
        add(row, "INVALID_STATUS", f"status is '{row['status']}'")

    # Numbers must be positive
    if float(row["lead_time_days"]) <= 0:
        add(row, "INVALID_LEAD_TIME", f"lead time is {row['lead_time_days']}")
    if float(row["unit_cost"]) <= 0:
        add(row, "INVALID_COST", f"unit cost is {row['unit_cost']}")

    # Naming standard: uppercase, no leading/trailing or double spaces
    if desc_raw != "" and (desc_raw != desc or "  " in desc_raw):
        add(row, "NAMING_STANDARD", f"description is '{desc_raw}'")

    if desc != "":
        # Build a suggested description by spelling out any abbreviations
        words = desc.split()
        suggested = " ".join(ABBREVIATIONS.get(w, w) for w in words)
        has_abbreviation = suggested != " ".join(words)

        if not desc.startswith(row["category"]):
            # ONE issue per record: category mismatch, plus a suggested fix if we have one
            if has_abbreviation:
                detail = (f"category is {row['category']}; "
                          f"description '{desc}' should be '{suggested}'")
                if not suggested.startswith(row["category"]):
                    detail += " (still does not start with the category - review)"
            else:
                detail = (f"category is {row['category']} but description is "
                          f"'{desc}' - review manually")
            add(row, "CATEGORY_MISMATCH", detail)

        elif has_abbreviation:
            # Category matches, but an abbreviation appears later in the description
            add(row, "ABBREVIATION", f"description '{desc}' should be '{suggested}'")

# Duplicate check across records (your rule): the same description + the same
# supplier means the same material, so it should have ONE item ID, even when the
# records sit in different plants. Abbreviations are spelled out before comparing.
def normalize(description):
    return " ".join(ABBREVIATIONS.get(w, w) for w in description.upper().split())

df["_norm"] = df["description"].map(normalize)
comparable = df[(df["_norm"] != "") & (df["supplier_id"].str.strip() != "")]
for (_, supplier), group in comparable.groupby(["_norm", "supplier_id"]):
    if len(group) > 1:
        ids = sorted(group["item_id"])
        keep = ids[0]  # simple choice: the lowest item ID is the one to keep
        for other in ids[1:]:
            problems.append({
                "item_id": other, "issue": "POSSIBLE_DUPLICATE",
                "detail": f"same description and supplier ({supplier}) as {keep}; "
                          f"should likely share one item ID",
            })

# ------------------------------------------------------------------
# SECTION 3: SAVE AND SUMMARIZE
# ------------------------------------------------------------------
issues = pd.DataFrame(problems)
issues.to_csv("issues.csv", index=False)

print(f"Checked {len(df)} records.")
print(f"Found {len(issues)} issues across {issues['item_id'].nunique()} records.\n")
print(issues["issue"].value_counts().to_string())
print("\nFull list saved to issues.csv")
