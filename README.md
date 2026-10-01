# Master Data Quality Agent

A small AI agent that reviews an item master file for data quality problems and drafts a cleanup ticket for each problem record, for a human data steward to review.

Built as a learning project by a supply chain and demand planning professional moving into data governance. It was built with AI assistance (Claude), and every business rule in it is mine. All data in this repository is **synthetic**. No employer or customer data is used.

## What it does

1. **Rules engine (`checker.py`)** applies deterministic data governance rules to every record and writes `issues.csv`.
2. **Agent (`agent.py`)** takes each flagged record and lets Claude investigate it using tools: look up the record, read the rules it broke, and search for possible duplicates. Claude then writes one cleanup ticket (`tickets.csv`).
3. **Evaluation (`evaluate.py`)** compares the tickets with a known answer key.

The agent **never edits the item master**. It only recommends, so a human approves every change.

## Rules enforced

| Rule | Example |
|---|---|
| Missing description or supplier | Blank `description`, blank `supplier_id` |
| Invalid unit of measure | `EACH`, `each`, `Ea` (approved: `EA`, `PC`) |
| Invalid status code | `ACTV` instead of `ACTIVE` |
| Lead time or unit cost not positive | `-7` days, `0` cost |
| Naming standard | Lowercase, extra spaces |
| Category mismatch / abbreviations | `CONN JST 4PIN` should be `CONNECTOR JST 4PIN` |
| Possible duplicate | Same description (after spelling out abbreviations) and same supplier means the same material, so it should share one item ID, even across plants |

## Design choices

- **Rules decide what can be written down.** Severity levels, unit-of-measure fixes and the record to keep in a duplicate pair are set by lookup tables and code, not by the model. In an early version, Claude rated similar errors inconsistently and suggested units of measure nobody had approved, so those decisions moved into code.
- **Claude does the judgment and writing.** It investigates each record, weighs the evidence, and writes the summary and recommended action.
- **Guardrails over trust.** Whatever the model writes in the rule-driven fields, the code overrides it.
- **Human in the loop.** Output is a ticket, never a data change.

## Results (development score, synthetic data)

Test file: 206 item master records with 39 known problem records, including 10 duplicate cases.

| Measure | Result |
|---|---|
| Known-bad records that got a ticket | 39 / 39 |
| Duplicates matched to the correct record to keep | 10 / 10 |
| Records wrongly called duplicates | 0 |
| Severity matches expected level | 39 / 39 |
| Cost of one full run (Claude Haiku 4.5) | about $0.24 |

**How to read this:** the data and the answer key were written by me, and I tuned the rules over several rounds, so this is a development score and not a guarantee for real data. Because severity and duplicate links are rule-enforced, those two measures test the pipeline more than the model. The wording of Claude's summaries and recommended actions is not scored automatically and should be reviewed by a person.

## Development notes

- The first answer key missed 4 duplicate pairs that the random data generator had created by accident. A manual review caught one of them, and the key was corrected and the rules extended.
- A manual review also found that the agent suggested unit-of-measure fixes that were not on the approved list, which led to the `UOM_FIXES` lookup table.

## Known limitations

- Only flagged records are reviewed. A duplicate that no rule flags will not reach the agent.
- Duplicate detection needs an exact match after spelling out abbreviations and ignoring case and spacing. Typos such as `10KOHM` vs `10K OHM` would not match.
- "Keep the lowest item ID" is a simple default. A real process might keep the oldest, most used, or most complete record.
- Tested only on a small synthetic file with one category family (electronic components and packaging).

## How to run

Requires Python 3.11 or newer and an [Anthropic API key](https://console.anthropic.com).

```
python -m pip install -r requirements.txt
```

Set your API key as an environment variable (do not put it in any file):

```
# Windows (then open a NEW terminal)
setx ANTHROPIC_API_KEY "your-key-here"

# Mac / Linux
export ANTHROPIC_API_KEY="your-key-here"
```

Then run the three steps in order:

```
python checker.py      # writes issues.csv
python agent.py        # writes tickets.csv (set MAX_RECORDS near the top to review more records)
python evaluate.py     # scores the tickets against answer_key.csv
```

## Files

| File | Purpose |
|---|---|
| `checker.py` | Rules engine |
| `agent.py` | Claude agent with tools and guardrails |
| `evaluate.py` | Scoring against the answer key |
| `item_master_sample.csv` | Synthetic item master with planted errors |
| `answer_key.csv` | The planted errors, used only for scoring |
| `test_api.py` | One-message check that your API key works |

## Author

Ryan Wahl, [LinkedIn](https://www.linkedin/in/ryanmwahl)
