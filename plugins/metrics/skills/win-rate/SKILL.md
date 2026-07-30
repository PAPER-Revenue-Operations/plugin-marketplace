---
name: win-rate
description: "Calculates count-based and dollar-based sales win rates from Salesforce for  Paper's opportunities. Use this whenever the user asks for a win-rate analysis,  win rate, close rate, or win/loss breakdown, or types the /win-rate command —  even if they don't specify a time frame, product, or sales motion (the skill  will ask). Trigger this for any request involving how often deals are won vs.  lost, win percentages by product (GROW, On-Demand) or by sales motion (NBEX,  Renewal/Rebuy), or comparisons of win rates across quarters, halves, or fiscal  years. Also trigger for per-stage or funnel win-rate questions, e.g. \"of the  deals that reached Stage X, how many closed won?\" or \"win rate by stage\" —  this skill supports that breakdown too. Prefer this skill over ad-hoc SOQL  whenever win rate is the metric."
---

# Win-Rate Analysis

This skill produces a clean win-rate table for Paper's Salesforce opportunities.
It reports two rates side by side so the user sees both deal-count momentum and
revenue-weighted outcomes:

- **Count win rate** = Closed Won ÷ (Closed Won + Closed Lost)
- **Dollar win rate** = Σ Won `Amount` ÷ Σ all Closed `Amount`

Both are computed over opportunities that *closed within the chosen time frame*,
so the analysis reflects decisions that actually landed in the period rather than
deals that are still open.

A related but distinct question is the **per-stage (funnel) win rate**: of the
opps that ever reached stage X, how many went on to close won? If the user asks
for that directly, see Step 6 for the methodology. If they didn't ask for it,
run the standard analysis below first and offer it afterward per Step 6.

## Step 1 — Confirm Salesforce is connected

This analysis is impossible without Salesforce. If the Salesforce connector/tools
aren't available, stop and tell the user you need them to connect Salesforce
before you can continue. Don't try to approximate the numbers from anywhere else —
Salesforce is Paper's source of truth and a wrong win rate is worse than none.

## Step 2 — Gather the three parameters

The analysis needs a time frame, a product, and a sales motion. If the user
already supplied any of these in their request, use them and don't re-ask. Only
ask about what's missing. Ask concisely — ideally batch the missing questions
together rather than interrogating one at a time.

These same three parameters drive the per-stage breakdown too, so gather them
once regardless of which analysis was requested. If the user's original request
explicitly asked for a per-stage/funnel win rate (rather than the standard
won/closed rate), skip Steps 3–5 below and go straight to Step 6 once you have
the parameters — there's no need to produce the standard table first in that
case.

### Time frame
Paper's fiscal year matches the calendar year, so quarters and halves map to
calendar months. Accept any of:

- A fiscal **quarter**: Q1 (Jan 1 – Mar 31), Q2 (Apr 1 – Jun 30), Q3 (Jul 1 – Sep 30), Q4 (Oct 1 – Dec 31)
- A fiscal **half**: H1 (Jan 1 – Jun 30), H2 (Jul 1 – Dec 31)
- A fiscal **year**: e.g. FY26 = Jan 1 – Dec 31, 2026
- A **custom** range the user gives you

If the user names a quarter/half without a year (e.g. "Q2"), ask which year, or
assume the current fiscal year if context makes it obvious — but say which year
you used. The filter is on `CloseDate` (the date the opp closed).

### Product (`Opportunity.Product_Type__c`)
Ask whether they want **GROW**, **On-Demand**, or **both**. Map the raw field
values into buckets:

- **GROW** bucket ← `GROW`, `GROW AI`
- **On-Demand** bucket ← `On-Demand`, `On-Demand + MC`, `On-Demand AI`
- **Ignore** every other value (notably standalone `MC`, which is a divested product, and anything unrecognized).

Merge `GROW`+`GROW AI` together, and merge the three On-Demand variants together,
*unless* the user explicitly asks to see a variant separately or on its own (e.g.
"show GROW AI separately", "just On-Demand + MC"). Honor that request when made,
but the default is the merged buckets above.

If the user asks for **both** products, ask whether they want GROW and On-Demand
**merged into one number or shown as separate rows**. Default to **separate** if
they don't care.

### Sales motion (`Opportunity.Type`)
Ask whether they want **NBEX**, **Renewal/Rebuy**, or **both**. Map `Type` into:

- **NBEX** ← `New Business`, `Expansion`, `New Logo`, `New Logo Rebuy`, `Cross-Sell`, `Pilot`
- **Renewal/Rebuy** ← everything else (e.g. `Renewal`, `Rebuy`, `Short-Term Renewal`)

Match `Type` values case-insensitively and tolerate small spelling variations —
Salesforce stores `Cross-sell` (lowercase s), so a strict match would silently
drop those deals. When in doubt, treat any type you can't confidently place in
NBEX as Renewal, since NBEX is the explicitly enumerated set.

If the user asks for **both** motions, ask whether they want them **merged or
shown as separate rows**. Default to **separate**.

## Step 3 — Query Salesforce

Build one SOQL query filtered to closed opps in the time frame, excluding test
data, then bucket and aggregate. Always apply these exclusions — test records
would inflate or distort the rate:

- Exclude any opp where `Is_Test__c = true`
- Exclude any opp whose account name contains "test" (case-insensitive). In SOQL
  use `Account.Name` with a `(NOT Account.Name LIKE '%test%')` style filter; be
  aware SOQL `LIKE` is already case-insensitive, so this catches "Test", "TEST",
  "testing", etc.
- Exclude any opp in the **`Closed Cleanup`** stage. This stage exists for
  opps that need to be closed out for administrative reasons — merged
  duplicates, data-entry errors, reorgs — and are neither a real win nor a
  real loss. Unlike other stage exclusions (see the denominator note below),
  this one is a standing default: apply it even if the user doesn't ask,
  since including it would misrepresent the win rate either way.

A good base query (adjust the product/type filters and date range to the chosen
parameters):

```sql
SELECT Id, Name, Account.Name, Product_Type__c, Type, StageName, CloseDate,
       IsWon, Amount
FROM Opportunity
WHERE IsClosed = true
  AND CloseDate >= <start> AND CloseDate <= <end>
  AND Is_Test__c = false
  AND (NOT Account.Name LIKE '%test%')
  AND StageName != 'Closed Cleanup'
```

`Id`, `Name`, `Account.Name`, `StageName`, and `CloseDate` aren't needed for the
rates themselves — they're there so the rows stay traceable back to real deals in
Salesforce if the user later wants the audit CSV from Step 9. Drop them if you
take the aggregate path below.

Then, in your own aggregation, keep only opps whose `Product_Type__c` falls in a
requested bucket and whose `Type` falls in a requested motion. You can also push
the bucketing into SOQL with `IN (...)` lists if you prefer, but doing the
bucketing after the fetch makes the ignore-rules and case-insensitive type
matching easier to get right. Prefer counting/summing in SOQL with `GROUP BY`
plus `COUNT(Id)` and `SUM(Amount)` for large periods to stay well under row
limits.

Denominator note: "closed" = won + lost. Salesforce marks both with
`IsClosed = true`; `IsWon` separates them. Every closed opp is therefore either a
win or a loss, including stages like "Closed Lost – No Decision" — those count as
losses. Don't exclude any closed stage unless the user explicitly asks — **with
one standing exception: `Closed Cleanup`.** That stage is `IsClosed = true` /
`IsWon = false` by necessity (there's no third boolean state), but it isn't a
real sales outcome, so it's excluded from the population by default rather than
counted as a loss. If a user explicitly asks to include it anyway (e.g. to audit
how many opps got routed there), you can, but flag that doing so mixes
administrative closures into the loss count.

## Step 4 — Compute the win rates

For each output row (a product × motion cell, or a merged group per the user's
choices):

- Count win rate = won_count / (won_count + lost_count), i.e. won_count / closed_count
- Dollar win rate = won_amount / closed_amount

Guard against divide-by-zero: if a cell has no closed opps, show "—" rather than
0% or an error, and note the empty segment.

## Step 5 — Present the table in chat

Output the result **directly in the chat as a rendered Markdown table** — this is
the primary deliverable, so lead with it rather than burying it under a file.
Include the counts and dollars behind each rate, because a rate without its
denominator is easy to misread (a 100% win rate on 1 deal is not the same story
as 60% on 200 deals). Don't include a separate "Lost" column — the closed count
already implies it (Lost = Closed − Won), so it would just be redundant.

Use a layout like this (columns/rows depend on what the user asked for):

| Segment | Won | Closed | Count Win Rate | Won $ | Closed $ | Dollar Win Rate |
|---|---|---|---|---|---|---|
| GROW – NBEX | 42 | 100 | 42.0% | $1.2M | $3.0M | 40.0% |
| On-Demand – Renewal/Rebuy | … | … | … | … | … | … |

Above or below the table, state the exact time frame (with dates), the product
and motion scope, and note that test accounts and `Is_Test__c` records were
excluded. If you had to assume a fiscal year or default a merged/separate choice,
say so plainly so the user can correct you.

**Flag low-sample segments below the table.** A win rate computed on a handful of
closed deals swings wildly on a single win or loss and shouldn't be read the same
way as one built on hundreds. After the table, add a short note calling out any
segment with a small closed-deal count so the reader takes it with a grain of
salt. Use roughly **fewer than 10 closed opps** as the trigger for a caveat, and
treat **fewer than ~30** as "interpret with some caution" — but use judgment
rather than a hard cutoff, and name the specific segment(s) and their counts, e.g.
"⚠️ GROW – NBEX is based on only 6 closed deals, so its 50% rate is not
statistically meaningful." If every segment is well-populated, no note is needed.

## Step 6 — Offer (or run) the per-stage win-rate breakdown

This step answers a different question from the main table: **of the opps that
ever reached stage X, how many went on to close won?** Use it either because the
user explicitly asked for a per-stage/funnel breakdown (per the routing note in
Step 2 — in that case this replaces Steps 3–5), or as a follow-up offer after
presenting the standard table. If offering, ask a single concise question, e.g.
"Want me to also break this down by stage — of the opps that reached each stage,
how many closed won?" Don't run it unless the user asked for it or agreed to the
offer.

### Definitions
- **Reached stage X** = the opp's history shows it passed through (or currently
  sits in) stage X at any point, regardless of where it ultimately closed.
- **Denominator** (per stage) = count/dollar total of closed opps — from the
  same population as the main analysis (same time frame via `CloseDate`, same
  product/motion buckets, same `Is_Test__c`/test-account exclusions) — that
  reached that stage.
- **Numerator** (per stage) = the subset of that denominator that is `IsWon = true`.
- An opp reaches every stage up to and including the furthest one it got to
  before closing, so it will contribute to multiple stage rows — that's expected
  for a funnel view and isn't double-counting to dedupe away.
- **Exclude terminal stages as rows.** Closed Won / Closed Lost (and any of
  their variants, e.g. "Closed Lost – No Decision") are the destination, not a
  stage an opp "reached along the way" — leave them out of the per-stage row
  list, even though they still determine each opp's `IsWon` outcome.
  `Closed Cleanup` is excluded from this breakdown entirely (it's already
  filtered out of the base population per Step 3), so it won't appear as a
  row either.
- **Fold legacy/renamed stage names by numeric prefix.** `OpportunityHistory`
  can contain `StageName` values that no longer appear in the current
  `StageName` picklist, because stages get renamed over time (e.g. an old run
  showed a retired "1. Discovery" stage that's since been renamed to
  "1. Sales Qualified"). For the past couple of years, stage names have carried
  a leading number (e.g. "1. Sales Qualified", "3. Proposal") — that number is
  the stable identifier even when the text after it changes. When a history
  `StageName` doesn't match anything in the current picklist, extract its
  leading number and fold it into whichever current picklist stage shares that
  same number, rather than dropping the record or creating a phantom row for
  the retired name. If a history value has no numeric prefix at all (older
  than the numbering convention), treat it as unmappable and note in the
  caveats that a handful of records predate reliable stage tracking, rather
  than guessing which current stage it corresponds to.
- **The Renewal pipeline has its own stage names — don't mix them with NBEX.**
  Paper's `StageName` picklist actually holds two parallel sequences that
  happen to share some numbers: an NBEX path (0. SDR Holding → 1. Sales
  Qualified → 2. Demo → 3. Proposal → 4. Planning → 5. Negotiation) and a
  Renewal path (1. Pre-Renewal → 2. Renewal Planning → 3. Negotiation →
  4. Purchase Approval → 5. Contracting). If the per-stage population is
  NBEX-only or Renewal/Rebuy-only, use that path's stage list. If the scope
  mixes both motions, build **two separate per-stage tables** (one per
  pipeline) rather than one table — folding by number alone would silently
  merge unrelated stages like NBEX's "3. Proposal" with Renewal's
  "3. Negotiation" just because they share a leading digit.

### Query approach
1. Start from the same closed-opp `Id` set the main analysis already produced
   (same filters). If running per-stage standalone (user asked for it directly
   without the main table), build that same base query first — it's the
   population you're funneling.
2. Query `OpportunityHistory` for those `OpportunityId`s, pulling `OpportunityId`
   and `StageName`. Build a per-opp set of stages reached, including the opp's
   current `StageName` from `Opportunity` itself (history rows aren't always
   guaranteed to capture the very first stage cleanly).
3. Pull the `StageName` picklist (schema tool) to get Paper's current stage
   names. Split it into the NBEX sequence and the Renewal sequence (they're
   distinguishable by label — "Pre-Renewal," "Renewal Planning," "Purchase
   Approval," and "Contracting" only exist on the Renewal side), and use
   whichever sequence(s) match the population's motion(s) per the pipeline
   note above. Drop terminal stages from the row list per above.
4. For each history `StageName` that isn't in the current picklist, extract
   its leading number and map it to the current-picklist stage with that same
   number on the relevant sequence. Values with no leading number are
   unmappable — exclude them from stage rows and mention the count in the
   caveats rather than guessing.
5. For each non-terminal stage, filter to opps whose reached-set (after
   folding) includes that stage, then compute won/closed counts and dollars
   exactly as in Step 4.

### Segment scope
If the user's main analysis has multiple segments (e.g. GROW and On-Demand
shown separately), default to **one combined per-stage table** across the
requested scope rather than a per-stage table per segment — that's usually
what "break it down by stage" means. Note this choice, and offer to split the
per-stage view by segment too if they want that level of detail (mention that
it multiplies the row count — e.g. 2 products × 6 stages = 12 rows). This is
independent of the NBEX/Renewal pipeline split above — if the scope spans both
motions, produce two pipeline tables *and* apply this same segment-combining
default within each.

### Output format
Render in the **same table shape as the main table**, just with stages as the
rows instead of product/motion segments:

| Segment | Won | Closed | Count Win Rate | Won $ | Closed $ | Dollar Win Rate |
|---|---|---|---|---|---|---|
| 0. SDR Holding | … | … | … | … | … | … |
| 1. Sales Qualified | … | … | … | … | … | … |
| 2. Demo | … | … | … | … | … | … |

Here `Segment` holds the stage name. Apply the same divide-by-zero handling
("—" for empty stages) and the same low-sample caveat logic from Step 4/Step 5
on a per-stage-row basis — a stage reached by only a handful of opps deserves
the same grain-of-salt flag. If any history records were unmappable (no
numeric prefix, per Step 4 above), mention the count once beneath the table
rather than working them into a row.

## Step 7 — Offer to export

After showing a table (standard or per-stage), offer to send it somewhere —
e.g. Google Drive (as a Sheet or doc), Notion, or Slack — using whichever of
those connectors is available. Don't push it anywhere without being asked; just
make the offer.

## Step 8 — Offer a trend comparison

A single period's win rate is a snapshot; the more useful question is usually
"which way is it moving?" So once the main table is delivered, ask the user
whether they'd like to compare it against another period to see how the win rate
is changing over time. Suggest two options:

1. The **adjacent period before or after** the one they requested (the quickest
   apples-to-apples comparison).
2. **Any other period they name**, as long as it's the same granularity as the
   original request (quarter-to-quarter, half-to-half, year-to-year,
   month-to-month, etc.) and the period is complete. Comparing a quarter against
   a full year, for instance, isn't meaningful, so keep the granularity matched.

**Only ever recommend or accept comparison periods that are entirely in the
past.** Check today's date and skip any period that hasn't fully closed — a
partial or future period has incomplete closed-deal data and would produce a
misleading rate. For example, if the current fiscal quarter is Q3 and the user
asked about Q2, the period *after* (Q3) is still in progress, so offer only the
period *before* (Q1) among the adjacent options. If the user picks a period of
their own that isn't yet complete, point that out and suggest the most recent
complete period of that granularity instead. If both adjacent periods are fully
in the past, you may offer either or both.

If the user agrees, rerun the exact same analysis for the comparison period —
**identical product bucket(s) and sales motion(s), identical exclusions and
math** — so the only thing that changes is the date range. Render those results
in a Markdown table formatted just like the first one (same columns), and label
each table clearly with its period.

Then, underneath, add a few plain-language comments highlighting any interesting
trends: which segments' win rates rose or fell and by how many points, whether
count and dollar rates moved in the same direction or diverged (e.g. winning more
deals but smaller ones), shifts in deal volume, and anything that looks like a
meaningful change versus noise. Keep the low-sample caveat in mind here too — a
swing driven by a segment with only a few closed deals is likely noise, so say so
rather than over-interpreting it.

## Step 9 — Offer an audit CSV set

Once the analysis is complete, offer to package the underlying data as
downloadable CSVs. Stakeholders sometimes question the methodology or a
surprising rate and want to rebuild the analysis in Google Sheets themselves,
and the same files are the fastest way to troubleshoot the skill when a number
looks wrong. Ask once, concisely — e.g. "Want an audit CSV set: the raw
Salesforce rows behind these numbers plus every SOQL query I ran, so this can be
rebuilt in Sheets?" Don't create anything unless the user asks or accepts.

This is **not** the same as the export offer in Step 7. That one ships the
finished *table* to Drive/Notion/Slack; this one ships the *raw data and the
queries*. You can make both offers in the same message, but keep them distinct
so the user knows which they're getting.

### Always include these two files

- **`queries.csv`** — every SOQL statement sent to the Salesforce connector this
  run, in the order you ran them. Columns: `query_number`, `purpose`, `sobject`,
  `soql`, `rows_returned`, `total_size`, `done_flag`. Collapse each statement to
  a single line with single spaces so it lands in one cell, and write the query
  **exactly as sent**, with the real dates and value lists substituted in — not
  the placeholder version from this skill.
- **`run-metadata.csv`** — the methodology record, as `key`,`value` rows: skill
  name, run date, the exact date range with both endpoints, the product buckets
  *and* the raw `Product_Type__c` values behind them, the motion buckets and
  their raw `Type` values, every exclusion applied, any default or assumption
  you made (fiscal year, merged vs. separate), whether the run used row-level or
  aggregate queries, and each data file's row count.

### Data files

- **`opportunities.csv`** — one row per opp returned by the Step 3 query. Raw
  fields first (`Id`, `Name`, `Account.Name`, `Product_Type__c`, `Type`,
  `StageName`, `CloseDate`, `IsClosed`, `IsWon`, `Amount`), then the columns you
  derived: `product_bucket`, `motion_bucket`, `segment`, `amount_used`,
  `included_in_math`, `excluded_reason`. The derived columns are what make the
  file auditable — they show the bucketing decisions from Step 2 rather than
  leaving the reader to guess them.
- **`aggregates.csv`** — only if the run used the `GROUP BY` path instead of
  row-level rows. Write the aggregate rows exactly as returned and note in
  `run-metadata.csv` that per-deal detail wasn't fetched. **Don't re-query to
  manufacture row-level rows**, and don't reconstruct per-deal rows from the
  aggregates — say plainly that the CSV is aggregate-only and offer a fresh
  row-level run if the user wants per-deal detail.
- **`stage-history.csv`** — only if Step 6 ran. One row per `OpportunityHistory`
  record: `OpportunityId`, the raw history `StageName`, `prefix_number`,
  `folded_stage`, `pipeline` (NBEX or Renewal), `included_in_math`,
  `excluded_reason`. This is where the stage-folding from Step 6 becomes
  inspectable, including the unmappable records you caveated.

### Keep the rows honest

- Include the rows you excluded rather than dropping them — that's what
  `included_in_math` (`TRUE`/`FALSE`) and `excluded_reason` are for (e.g.
  unrecognized `Product_Type__c`, standalone `MC`, a terminal stage in the
  per-stage file). "Why isn't my deal in this number?" is the most common
  challenge, and a filtered-out row can't answer it.
- **Only rows a query actually returned this run.** Never infer, reconstruct, or
  backfill a row to make a file look complete — record the gap in
  `run-metadata.csv` instead.
- **Assert the row counts.** Each data file's row count must equal that query's
  `totalSize`. If any query came back `done: false`, the page was truncated and
  rows are silently missing — say so in `run-metadata.csv` *and* in chat, since
  that invalidates the rates, not just the CSV.

### CSV formatting

- Header row, UTF-8, one row per record.
- Quote any field containing a comma, double quote, or newline, and double any
  embedded double quotes. The `soql` column needs this.
- Dates as ISO `YYYY-MM-DD` and datetimes as ISO 8601, so Sheets parses them as
  dates rather than text.
- Numbers raw: `1200000`, never `$1.2M`. No currency symbols, thousands
  separators, or `%` signs — the chat table abbreviates, the CSV must not. Write
  rates as decimals if you include them at all.
- Null `Amount` → an empty cell in the raw column and `0` in `amount_used`, so
  the null-vs-zero distinction survives into Sheets.
- Booleans as `TRUE`/`FALSE`.

### Delivery

Create each file with `create_file` inside a run-scoped directory named for the
metric and period (e.g. `win-rate-audit-Q2-2026/`), then hand them over with
`present_files` so the user can download them. Don't use a fixed path —
`create_file` fails outright on an existing path, and a prior run's files may
still be present. **Don't paste CSV contents inline as a code block**; the point
is a downloadable file. If a trend comparison from Step 8 was also run, produce
a separate labelled folder per period rather than mixing periods in one file.

These files carry account names and deal amounts, so they're internal-only —
mention that when you hand them over, and don't upload them anywhere unless the
user asks.

## Notes and gotchas

- **Source of truth is Salesforce.** Never fabricate or estimate; if a query
  fails, say so and retry rather than guessing.
- **Field reference:** `Product_Type__c` (product), `Type` (sales motion),
  `IsWon`/`IsClosed` (outcome), `CloseDate` (period filter), `Amount` (dollar
  weighting), `Is_Test__c` and `Account.Name` (test exclusions). These are the
  confirmed API names on Paper's Opportunity object.
- **`Closed Cleanup` is never a win or a loss.** It's a `StageName` value
  for opps closed for administrative reasons (merges, duplicates, data
  cleanup) rather than a genuine sales outcome. Always excluded from the base
  population by default — see Step 3. If this exact stage label changes in
  Salesforce, update the `StageName != 'Closed Cleanup'` filter in Step 3
  and the per-stage exclusion note in Step 6 to match.
- **`Amount` is the dollar field** for the dollar-based rate — not a custom
  ARR/TCV field — unless the user specifies otherwise.
- **Pressure-test surprising results.** If a rate looks implausible (e.g. 0% or
  100%, or a segment with far fewer deals than expected), double-check the filters
  before presenting, and flag the anomaly to the user rather than reporting it
  flatly.