---
name: sales-cycle
description: >-
  Calculates average and median sales cycle length (time from opp creation/open
  to close) from Salesforce for Paper's opportunities. Use this whenever the
  user asks about sales cycle length, time-to-close, deal velocity, how long
  deals take to close, or types the /sales-cycle command — even if they don't
  specify a time frame, product, sales motion, or won/lost scope (the skill
  will ask). Trigger this for any request comparing cycle length across
  products (GROW, On-Demand), sales motions (NBEX, Renewal/Rebuy), or time
  periods (quarters, halves, fiscal years), or asking which stage deals spend
  the most time in. Prefer this skill over ad-hoc SOQL whenever sales cycle
  length is the metric.
---

# Sales Cycle Length Analysis

This skill produces a clean average/median sales-cycle table for Paper's
Salesforce opportunities. The headline number is simply `CloseDate` −
`CreatedDate` per opp — no stage history needed. Stage-history data
(`Opportunity_Stage__c` / `OpportunityFieldHistory`) only comes into play if
the user asks for a stage-by-stage breakdown (Step 10).

## Step 1 — Confirm Salesforce is connected

This analysis is impossible without Salesforce. If the Salesforce
connector/tools aren't available, stop and tell the user you need them to
connect Salesforce before you can continue. Don't approximate from anywhere
else — Salesforce is Paper's source of truth and a wrong sales-cycle number is
worse than none.

## Step 2 — Gather the parameters

The analysis needs a time frame, a product, a sales motion, and a won/lost
scope. If the user already supplied any of these in their request, use them
and don't re-ask. Only ask about what's missing, and batch the missing
questions together rather than interrogating one at a time.

### Time frame
Paper's fiscal year matches the calendar year, so quarters and halves map to
calendar months. Accept any of:

- A fiscal **quarter**: Q1 (Jan 1 – Mar 31), Q2 (Apr 1 – Jun 30), Q3 (Jul 1 –
  Sep 30), Q4 (Oct 1 – Dec 31)
- A fiscal **half**: H1 (Jan 1 – Jun 30), H2 (Jul 1 – Dec 31)
- A fiscal **year**: e.g. FY26 = Jan 1 – Dec 31, 2026
- A **custom** range the user gives you

If the user names a quarter/half without a year (e.g. "Q2"), ask which year, or
assume the current fiscal year if context makes it obvious — but say which
year you used. The time frame filters on `CloseDate` — only opps that *closed*
within the window are included (this is a cycle-length analysis of completed
journeys, not a snapshot of open pipeline).

### Product (`Opportunity.Product_Type__c`)
Ask whether they want **GROW**, **On-Demand**, or **both**. Map raw field
values into buckets:

- **GROW** bucket ← `GROW`, `GROW AI`
- **On-Demand** bucket ← `On-Demand`, `On-Demand + MC`, `On-Demand AI`
- **Ignore** every other value (notably standalone `MC`, a divested product,
  and anything unrecognized).

Merge `GROW`+`GROW AI` together, and merge the three On-Demand variants
together, *unless* the user explicitly asks to see a variant separately or on
its own. Honor that request when made, but the default is the merged buckets
above.

If the user asks for **both** products, ask whether they want GROW and
On-Demand **merged into one number or shown as separate rows**. Default to
**separate** if they don't care.

### Sales motion (`Opportunity.Type`)
Ask whether they want **NBEX**, **Renewal/Rebuy**, or **both**. Map `Type`
into:

- **NBEX** ← `New Business`, `Expansion`, `New Logo`, `New Logo Rebuy`,
  `Cross-Sell`, `Pilot`
- **Renewal/Rebuy** ← everything else (e.g. `Renewal`, `Rebuy`, `Short-Term
  Renewal`)

Match `Type` values case-insensitively and tolerate small spelling variations
(Salesforce stores `Cross-sell` with a lowercase s). When in doubt, treat any
type you can't confidently place in NBEX as Renewal, since NBEX is the
explicitly enumerated set.

If the user asks for **both** motions, ask whether they want them **merged or
shown as separate rows**. Default to **separate**.

### Won/lost scope
Ask whether to include **only opps won in the time period** (the default) or
**also include lost opps**. If they want lost opps included, ask whether lost
opp cycle times should be **shown as their own row(s)** or **merged with won**
opps in the same segment. Default to separate rows if they don't specify.
"Closed" for filtering purposes always means `IsClosed = true` within the
window; `IsWon` then determines which bucket (won vs. lost) an opp falls into
if lost opps are in scope.

## Step 3 — Compute per-opp cycle length from CreatedDate → CloseDate

For the headline average/median table, **you don't need stage history at
all**. Sales cycle length for an opp = `CloseDate` − `CreatedDate`, in days.
This is the default and simplest path, and it's all that's needed unless the
user specifically wants a stage-by-stage breakdown (Step 10) — don't pull
`Opportunity_Stage__c` or `OpportunityFieldHistory` for the top-level table.

## Step 4 — Apply standard exclusions

Always apply these, regardless of segment:

- Exclude any opp where `Is_Test__c = true`
- Exclude any opp whose account name contains "test" (case-insensitive). In
  SOQL use `Account.Name` with `(NOT Account.Name LIKE '%test%')` — SOQL
  `LIKE` is already case-insensitive, so this catches "Test", "TEST",
  "testing", etc.

A good base query to start from (adjust date range, and pull `IsWon` if lost
opps are in scope):

```sql
SELECT Id, Name, Product_Type__c, Type, CreatedDate, CloseDate, IsWon
FROM Opportunity
WHERE IsClosed = true
  AND CloseDate >= <start> AND CloseDate <= <end>
  AND Is_Test__c = false
  AND (NOT Account.Name LIKE '%test%')
```

This single query is all you need for the top-level table — bucket the
results by product/motion/won-lost per Step 2's choices and move straight to
Step 5.

## Step 5 — Compute average and median

For each output row (a product × motion × won/lost cell, per the user's
merge/separate choices):

- **Average sales cycle** = mean of (`CloseDate` − `CreatedDate`) across opps
  in that segment, expressed in days (round to 1 decimal).
- **Median sales cycle** = median of the same set, in days.
- **# Opps** = count of opps in that segment.

Guard against divide-by-zero / empty segments: if a segment has zero opps,
show "—" rather than an error, and note it's empty.

## Step 6 — Present the table in chat

Output the result **directly in the chat as a rendered Markdown table** — this
is the primary deliverable. Columns:

| Segment | Avg Sales Cycle | Median Sales Cycle | # Opps |
|---|---|---|---|
| GROW – NBEX (Won) | 62.4 days | 54.0 days | 118 |
| On-Demand – Renewal/Rebuy (Won) | … | … | … |

Above or below the table, state the exact time frame (with dates), the
product/motion/won-lost scope, and note that test accounts and `Is_Test__c`
records were excluded. If you had to assume a fiscal year, an open-date
definition, or a merged/separate choice, say so plainly so the user can
correct you.

(There's no opp-exclusion/confidence check at this stage — that only applies
if a stage-by-stage breakdown is requested later, see Step 10.)

## Step 7 — Flag low sample sizes

A cycle-length average/median built on a handful of opps swings wildly on a
single deal and shouldn't be read the same way as one built on hundreds. After
the table, add a short note calling out any segment with a small opp count —
use roughly **fewer than 10 included opps** as the trigger for a caveat, and
treat **fewer than ~30** as "interpret with some caution," using judgment
rather than a hard cutoff. Name the specific segment(s) and their counts, e.g.
"⚠️ GROW – Renewal/Rebuy (Won) is based on only 6 opps, so its cycle length
isn't statistically meaningful." If every segment is well-populated, skip this
note.

## Step 8 — Offer to export

After showing the table, offer to send it somewhere — e.g. Google Drive (as a
Sheet or doc), Notion, or Slack — using whichever of those connectors is
available. Don't push it anywhere without being asked; just make the offer.

## Step 9 — Offer a trend comparison

A single period's sales cycle is a snapshot; the more useful question is
usually "is it speeding up or slowing down?" Once the main table is delivered,
ask if the user would like to compare against another period. Suggest:

1. The **adjacent period before or after** the one requested.
2. **The same period one year earlier or later** (e.g. compare Q1 FY26 with Q1
   FY25), as long as the later comparison period is fully complete.
3. **Any other period they name**, as long as it matches the original
   request's granularity (quarter-to-quarter, half-to-half, year-to-year,
   month-to-month, etc.) — don't compare a quarter against a full year.

**Only ever recommend or accept comparison periods that are entirely in the
past.** Check today's date and skip any period that hasn't fully closed. If
the user picks an incomplete period, point that out and suggest the most
recent complete period of that granularity instead.

If the user agrees, rerun the exact same analysis for the comparison period —
identical product/motion/won-lost scope and identical exclusions — so only the
date range changes. Render those results in a matching Markdown table, clearly
labeled with its period.

Then add a few plain-language comments highlighting interesting trends: which
segments got faster or slower and by how much (in days and %), whether that
tracks with any deal-volume shift, and anything that looks like a meaningful
change versus noise. Keep the low-sample caveat in mind — a swing driven by a
segment with only a few opps is likely noise.

## Step 10 — Offer a stage-by-stage breakdown

After the top-level table (and after any comparison, if requested), offer a
more detailed table showing, per segment, the average and median time opps
spent **in each stage**. This is the only part of the analysis that needs
stage-level history, so only do this work if the user asks for it.

### Stage name prefixes
For the past several years, stage names carry a **numeric prefix** (e.g. "1.
Sales Qualified", "3. Proposal"). Older/legacy opps may have out-of-date stage
names that lack today's wording but still carry the number (e.g. "1.
Discovery"). Use the **prefix number**, not the label text, to map a legacy
stage name onto its current equivalent (so "1. Discovery" → treat as "1. Sales
Qualified"). This applies to both the NBEX/GROW stage set and the separate
Renewal stage set — each has its own prefixed sequence, and you should map
within the correct set for that opp's motion. Before mapping, pull the current
list of stage names (e.g. from the `Opportunity.StageName` picklist metadata,
or by inspecting a sample of recent closed opps) so you know the current label
for each prefix number rather than guessing.

### Where to find stage history
Look first at **`Opportunity_Stage__c`** — it logs a record every time an opp
changes stage (with `Stage__c`, `Entry_Date__c`, `Exit_Date__c`, and a
`Duration__c` rollup), giving unbounded history regardless of how old the opp
is. Use this as your primary source.

**Cross-check against `OpportunityFieldHistory`** (filtered to
`Field = 'StageName'`) whenever an `Opportunity_Stage__c` record looks off —
e.g. a null `Exit_Date__c` paired with a `Duration__c` that doesn't reconcile
with the opp's actual close date, or any other internally inconsistent
timing. `OpportunityFieldHistory` records the literal `StageName` change
events with timestamps, so it can confirm or correct a bad
`Opportunity_Stage__c` value — don't assume a suspicious record means the data
is unrecoverable until you've checked. The catch is that `OpportunityFieldHistory`
only retains a **rolling 18–24 months**, so this cross-check only works for
opps that closed within that window; for older opps you're relying on
`Opportunity_Stage__c` alone.

### Confidence check — when to exclude an opp from the breakdown
For each opp, you need a coherent path of stage transitions from its earliest
recorded stage through to its closed stage, with timestamps. If, even after
the `OpportunityFieldHistory` cross-check, you still can't reconstruct that
path confidently — e.g. the opp is outside `OpportunityFieldHistory`'s
retention window and `Opportunity_Stage__c` has an unresolved gap or
inconsistency — **exclude that opp from the stage-by-stage table only** (it
can remain in the top-level average/median table, since that doesn't depend
on stage data).

Tally excluded opps and note them below the stage-by-stage table, e.g. "⚠️ 2
opps were excluded from the stage-by-stage breakdown because their stage
history couldn't be confidently reconstructed even after cross-referencing
OpportunityFieldHistory." Offer to rerun including them on a best-effort basis
if the user wants.

Once you have a clean per-opp stage timeline, compute the average/median time
spent in each stage (time between entering that stage and entering the next
one) per segment. Present it as one table per segment, or one combined table
with a Segment + Stage column — whichever is more readable given how many
segments/stages are in scope.

**Each stage row has its own opp count — don't assume it matches the
segment's overall count.** Opps can skip stages entirely (e.g. a rebuy that
enters directly at a later stage with no earlier stage records at all, or a
deal that jumps straight from Proposal to Closed Won), so the # of opps
contributing to "1. Sales Qualified" may be smaller than, larger than, or
simply different from the # contributing to "3. Proposal" — none of them need
to equal the segment's total opp count from the top-level table. Always show
(or at least track) the opp count alongside each stage's average/median so
the low-sample-size caveat from Step 7 can be applied per stage, not just per
segment. A stage with only 1–2 opps behind it is not meaningful even if the
segment overall has plenty of opps.

## Notes and gotchas

- **Source of truth is Salesforce.** Never fabricate or estimate; if a query
  fails, say so and retry rather than guessing.
- **Field/object reference:** `Product_Type__c` (product), `Type` (sales
  motion), `IsWon`/`IsClosed` (outcome), `CloseDate`/`CreatedDate` (the
  headline cycle-length dates — sufficient on their own for the top-level
  table), `Is_Test__c` and `Account.Name` (test exclusions). `Opportunity_Stage__c`
  (primary stage-history source) and `OpportunityFieldHistory` (cross-check /
  fallback, 18–24 month retention only) are **only needed for the Step 10
  stage-by-stage breakdown** — don't query them otherwise.
- **Renewal opps have a different stage set** than NBEX/GROW opps, but both
  sets still use numeric prefixes — map within the correct set for each opp's
  motion, don't cross-map between them.
- **Pressure-test surprising results.** If a cycle length looks implausible
  (e.g. near-zero or extremely long relative to the segment's norm), double
  check that opp's `CreatedDate`/`CloseDate` values directly before
  presenting (e.g. a backdated `CreatedDate` from a data migration), and flag
  the anomaly rather than reporting it flatly.
