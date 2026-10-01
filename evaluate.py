"""
Evaluation: how well did the agent do compared with the answer key?

Reads tickets.csv (from agent.py) and answer_key.csv, then reports:
  1. Coverage        - did every known-bad record get a ticket?
  2. Duplicates      - did the agent name the right record to keep?
  3. False duplicate - did it call something a duplicate that is not one?
  4. Severity        - does the severity match the levels defined in the agent's prompt?
"""
import pandas as pd

tickets = pd.read_csv("tickets.csv", dtype=str, keep_default_na=False)
key = pd.read_csv("answer_key.csv", dtype=str, keep_default_na=False)

# Expected severity per issue type (same definitions as the agent's instructions)
SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
EXPECTED = {
    "MISSING_DESCRIPTION": "MEDIUM", "INVALID_UOM": "MEDIUM", "INVALID_STATUS": "MEDIUM",
    "INVALID_LEAD_TIME": "HIGH", "MISSING_SUPPLIER": "HIGH", "INVALID_COST": "HIGH",
    "NAMING_STANDARD": "LOW",
}


def expected_severity(issue):
    if "DUPLICATE" in issue:
        return "HIGH"
    return EXPECTED[issue]


# Work out, per record, the expected severity (the highest) and expected duplicate target
expected = {}
for _, row in key.iterrows():
    sev = expected_severity(row["issue"])
    info = expected.setdefault(row["item_id"], {"severity": sev, "dup_of": ""})
    if SEVERITY_RANK[sev] > SEVERITY_RANK[info["severity"]]:
        info["severity"] = sev
    if "DUPLICATE" in row["issue"]:
        info["dup_of"] = row["issue"].split("_")[-1]  # e.g. MAT-00059

ticketed = tickets.drop_duplicates("item_id").set_index("item_id")
total = len(expected)

# 1. Coverage
covered = [i for i in expected if i in ticketed.index]
missing = [i for i in expected if i not in ticketed.index]

# 2. Duplicates: right record named?
dup_expected = {i: v["dup_of"] for i, v in expected.items() if v["dup_of"]}
dup_ok = [i for i, d in dup_expected.items()
          if i in ticketed.index and ticketed.loc[i, "possible_duplicate_of"] == d]
dup_wrong = [i for i in dup_expected if i in ticketed.index and i not in dup_ok]

# 3. False duplicate claims
false_dups = [i for i in ticketed.index
              if ticketed.loc[i, "possible_duplicate_of"] and i not in dup_expected]

# 4. Severity
sev_checked = [i for i in covered]
sev_ok = [i for i in sev_checked if ticketed.loc[i, "severity"] == expected[i]["severity"]]
sev_off = [i for i in sev_checked if i not in sev_ok]

print(f"Known-bad records in answer key: {total}")
print(f"Tickets written:                 {len(ticketed)}\n")
print(f"1. Coverage:         {len(covered)}/{total} records have a ticket")
if missing:
    print(f"   No ticket for: {', '.join(missing)}")
print(f"2. Duplicates:       {len(dup_ok)}/{len(dup_expected)} correctly matched to the record to keep")
for i in dup_wrong:
    print(f"   {i}: expected {dup_expected[i]}, agent said "
          f"'{ticketed.loc[i, 'possible_duplicate_of']}'")
print(f"3. False duplicates: {len(false_dups)} records wrongly called duplicates")
for i in false_dups:
    print(f"   {i}: pointed to {ticketed.loc[i, 'possible_duplicate_of']}")
if sev_checked:
    print(f"4. Severity:         {len(sev_ok)}/{len(sev_checked)} match the expected level")
for i in sev_off:
    print(f"   {i}: agent said {ticketed.loc[i, 'severity']}, expected {expected[i]['severity']}")
