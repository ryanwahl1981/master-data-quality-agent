"""
Master Data Quality Agent

What it does:
  1. Reads the problems your rules found (issues.csv, made by checker.py)
  2. For each flagged record, Claude investigates using TOOLS:
       - get_record            look up the full record
       - get_rule_issues       see which rules the record broke
       - find_similar_records  look for possible duplicates
  3. Claude decides what is wrong and drafts ONE cleanup ticket (submit_ticket)
  4. All tickets are saved to tickets.csv for a human data steward to review

Important: the agent NEVER changes the item master. It only recommends.
That human-approval step is a core governance control.

Run order:   python checker.py     (makes issues.csv)
             python agent.py       (makes tickets.csv)
"""
import difflib
import json
import re

import anthropic
import pandas as pd

# ------------------------------------------------------------------
# SETTINGS (edit these)
# ------------------------------------------------------------------
MODEL = "claude-haiku-4-5-20251001"  # smallest, cheapest Claude model
MAX_RECORDS = 40    # how many flagged records to review. Raise it after a test run.
ONLY_ITEMS = []    # or pick records yourself, e.g. ["MAT-00205", "MAT-00201"]
MAX_TURNS = 6      # safety limit: max back-and-forth steps per record
PRICE_IN = 1.00    # $ per million input tokens  (rough estimate, check current pricing)
PRICE_OUT = 5.00   # $ per million output tokens (rough estimate, check current pricing)

# Approved unit-of-measure fixes (your rule). Keys are written in UPPERCASE.
# Keep VALID_UOM in checker.py and these fixes consistent with each other.
UOM_FIXES = {"EA": "EA", "EACH": "EA", "PC": "PC", "PCS": "PC"}

# Severity is decided by RULE, not by the model (your rule). A record gets the
# highest severity among the issues it has.
SEVERITY_BY_ISSUE = {
    "POSSIBLE_DUPLICATE": "HIGH", "INVALID_LEAD_TIME": "HIGH",
    "INVALID_COST": "HIGH", "MISSING_SUPPLIER": "HIGH",
    "MISSING_DESCRIPTION": "MEDIUM", "INVALID_UOM": "MEDIUM", "INVALID_STATUS": "MEDIUM",
    "NAMING_STANDARD": "LOW", "CATEGORY_MISMATCH": "LOW", "ABBREVIATION": "LOW",
}
SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}

# Keep this list the same as in checker.py
ABBREVIATIONS = {"RES": "RESISTOR", "CAP": "CAPACITOR", "CONN": "CONNECTOR",
                 "FSTNR": "FASTENER", "PKG": "PACKAGING"}

# The agent's instructions. This is where YOUR business knowledge goes.
SYSTEM_PROMPT = """You are a master data steward assistant for an electronics manufacturer.
You will be given one item ID that failed data quality rules. Do this:
1. Call get_record and get_rule_issues for the item.
2. Call find_similar_records to check whether it duplicates another record.
3. Call submit_ticket exactly once with your findings.

Business rules:
- DUPLICATES: two records with the same description (ignoring capitalization, spacing
  and abbreviations) AND the same supplier_id are the same material, even at different
  plants. One material should have one item ID. The lowest item ID is the record to keep.
  When find_similar_records returns a match with same_supplier_as_this_item true and a
  duplicate_role, follow the role:
    - If THIS item is redundant: set possible_duplicate_of to the record to keep and
      recommend consolidating under that item ID.
    - If THIS item is the record to keep: leave possible_duplicate_of empty, and say in
      the summary that the other record duplicates this one and should be merged into it.
- Same description but a DIFFERENT supplier is NOT treated as a duplicate (it may be a
  second source of supply). Do not put it in possible_duplicate_of, but mention it in
  the summary so the steward is aware.
- Different revision numbers (REV2 vs REV3) are different items, not duplicates.
- UNIT OF MEASURE: when get_rule_issues returns a suggested_uom, recommend exactly that
  value. Never suggest any other unit of measure and never tell the steward to "verify"
  the unit when a suggested_uom is given. If suggested_uom starts with NONE, say the
  steward must confirm the correct unit.
- Use only facts from the tool results. Never invent values, suppliers or costs.
- You cannot change data. Your ticket is a recommendation for a human steward.
- Do not decide severity. The system sets it automatically.
- If you are not sure, say so in the ticket instead of guessing."""

# ------------------------------------------------------------------
# DATA
# ------------------------------------------------------------------
items = pd.read_csv("item_master_sample.csv", dtype=str, keep_default_na=False)
issues = pd.read_csv("issues.csv", dtype=str, keep_default_na=False)
items_by_id = items.set_index("item_id")


# ------------------------------------------------------------------
# TOOLS: ordinary Python functions that Claude is allowed to call
# ------------------------------------------------------------------
def get_record(item_id):
    if item_id not in items_by_id.index:
        return {"error": f"{item_id} not found"}
    return {"item_id": item_id, **items_by_id.loc[item_id].to_dict()}


def get_rule_issues(item_id):
    rows = issues[issues["item_id"] == item_id]
    result = rows[["issue", "detail"]].to_dict("records")
    if item_id in items_by_id.index:
        current_uom = items_by_id.loc[item_id]["uom"]
        for r in result:
            if r["issue"] == "INVALID_UOM":
                fix = UOM_FIXES.get(current_uom.strip().upper())
                r["suggested_uom"] = fix if fix else \
                    "NONE - no approved mapping, steward must confirm"
    return result


def normalize(text):
    """Uppercase, collapse spaces and spell out abbreviations before comparing."""
    return " ".join(ABBREVIATIONS.get(w, w) for w in text.upper().split())


def find_similar_records(item_id, top_n=5):
    """Find records in the same category with similar descriptions."""
    if item_id not in items_by_id.index:
        return {"error": f"{item_id} not found"}
    me = items_by_id.loc[item_id]
    my_desc = normalize(me["description"])
    if my_desc == "":
        return []  # nothing to compare when the description is blank
    same_category = items[(items["category"] == me["category"]) &
                          (items["item_id"] != item_id)]
    matches = []
    for _, other in same_category.iterrows():
        score = difflib.SequenceMatcher(None, my_desc, normalize(other["description"])).ratio()
        if score >= 0.6:
            same_supplier = (me["supplier_id"].strip() != "" and
                             other["supplier_id"] == me["supplier_id"])
            exact = normalize(other["description"]) == my_desc
            role = ""
            if exact and same_supplier:
                role = ("THIS ITEM IS THE RECORD TO KEEP; the other record is redundant"
                        if item_id < other["item_id"]
                        else f"THIS ITEM IS REDUNDANT; keep {other['item_id']}")
            matches.append({
                "item_id": other["item_id"],
                "description": other["description"],
                "plant": other["plant"],
                "supplier_id": other["supplier_id"],
                "same_plant_as_this_item": other["plant"] == me["plant"],
                "same_supplier_as_this_item": same_supplier,
                "similarity": round(score, 2),
                "duplicate_role": role,
            })
    # same-supplier matches first, then by similarity
    matches.sort(key=lambda m: (m["same_supplier_as_this_item"], m["similarity"]),
                 reverse=True)
    return matches[:top_n]


def rule_severity(item_id):
    """Highest severity among the issues the rules found for this record."""
    found = issues[issues["item_id"] == item_id]["issue"]
    levels = [SEVERITY_BY_ISSUE.get(i, "LOW") for i in found]
    return max(levels, key=SEVERITY_RANK.get) if levels else "LOW"


def rule_duplicate_keeper(item_id):
    """The record to keep, if the rules flagged this record as redundant. Else empty."""
    rows = issues[(issues["item_id"] == item_id) & (issues["issue"] == "POSSIBLE_DUPLICATE")]
    for detail in rows["detail"]:
        match = re.search(r"as (MAT-\d+);", detail)
        if match:
            return match.group(1)
    return ""


def run_tool(name, args, tickets):
    """Run whichever tool Claude asked for and return its result."""
    if name == "get_record":
        return get_record(args["item_id"])
    if name == "get_rule_issues":
        return get_rule_issues(args["item_id"])
    if name == "find_similar_records":
        return find_similar_records(args["item_id"])
    if name == "submit_ticket":
        # Guardrails: rule-driven fields are set by code, whatever the model wrote
        args["severity"] = rule_severity(args["item_id"])
        args["possible_duplicate_of"] = rule_duplicate_keeper(args["item_id"])
        tickets.append(args)
        return {"status": "ticket saved"}
    return {"error": f"unknown tool {name}"}


# The "menu" that tells Claude what each tool does and what it needs.
ID_ONLY = {"type": "object",
           "properties": {"item_id": {"type": "string"}},
           "required": ["item_id"]}

TOOLS = [
    {"name": "get_record",
     "description": "Get the full item master record for an item ID.",
     "input_schema": ID_ONLY},
    {"name": "get_rule_issues",
     "description": "Get the data quality rules this item broke, with details.",
     "input_schema": ID_ONLY},
    {"name": "find_similar_records",
     "description": "Find other records in the same category with similar descriptions, "
                    "to check for possible duplicates. Includes plant information.",
     "input_schema": ID_ONLY},
    {"name": "submit_ticket",
     "description": "Submit the cleanup ticket for this item. Call exactly once.",
     "input_schema": {
         "type": "object",
         "properties": {
             "item_id": {"type": "string"},
             "summary": {"type": "string", "description": "What is wrong, in plain English."},
             "recommended_action": {"type": "string", "description": "What the steward should do."},
             "possible_duplicate_of": {"type": "string",
                                       "description": "Item ID to keep if THIS item is redundant, else empty."},
         },
         "required": ["item_id", "summary", "recommended_action"]}},
]


# ------------------------------------------------------------------
# THE AGENT LOOP
# ------------------------------------------------------------------
def review_record(client, item_id, tickets, usage):
    """Let Claude investigate one record. Returns when a ticket is submitted."""
    messages = [{"role": "user",
                 "content": f"Review item {item_id} and submit one cleanup ticket."}]
    tickets_before = len(tickets)

    for _ in range(MAX_TURNS):
        reply = client.messages.create(
            model=MODEL, max_tokens=1000, system=SYSTEM_PROMPT,
            tools=TOOLS, messages=messages,
        )
        usage["in"] += reply.usage.input_tokens
        usage["out"] += reply.usage.output_tokens
        messages.append({"role": "assistant", "content": reply.content})

        if reply.stop_reason != "tool_use":
            break  # Claude stopped asking for tools

        # Run every tool Claude asked for and send the results back
        results = []
        for block in reply.content:
            if block.type == "tool_use":
                output = run_tool(block.name, block.input, tickets)
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(output)})
        messages.append({"role": "user", "content": results})

        if len(tickets) > tickets_before:
            break  # ticket submitted, this record is done

    if len(tickets) == tickets_before:
        print(f"  WARNING: no ticket was submitted for {item_id}")


def main():
    client = anthropic.Anthropic()  # reads your key from ANTHROPIC_API_KEY
    flagged = list(dict.fromkeys(issues["item_id"]))
    flagged = ONLY_ITEMS if ONLY_ITEMS else flagged[:MAX_RECORDS]
    tickets = []
    usage = {"in": 0, "out": 0}

    print(f"Reviewing {len(flagged)} flagged records with {MODEL}...\n")
    for item_id in flagged:
        print(f"Reviewing {item_id}...")
        try:
            review_record(client, item_id, tickets, usage)
        except anthropic.APIError as error:
            print(f"  API problem: {error}\n  Stopping early and saving what we have.")
            break

    if tickets:
        out = pd.DataFrame(tickets)
        if "possible_duplicate_of" not in out.columns:
            out["possible_duplicate_of"] = ""
        out = out.fillna("")
        out = out[["item_id", "severity", "summary",
                   "recommended_action", "possible_duplicate_of"]]
        out.to_csv("tickets.csv", index=False)

        print("\n" + "=" * 60)
        for t in tickets:
            print(f"\n{t['item_id']}  [{t['severity']}]")
            print(f"  Problem: {t['summary']}")
            print(f"  Action:  {t['recommended_action']}")
            if t.get("possible_duplicate_of"):
                print(f"  Possible duplicate of: {t['possible_duplicate_of']}")
        print(f"\nSaved {len(tickets)} tickets to tickets.csv")

    cost = usage["in"] / 1e6 * PRICE_IN + usage["out"] / 1e6 * PRICE_OUT
    print(f"Tokens used: {usage['in']:,} in, {usage['out']:,} out "
          f"(rough cost: ${cost:.3f})")


if __name__ == "__main__":
    main()
