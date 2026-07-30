---
name: slip-rate
description: "Calculates count-based and dollar-based slip rates from Salesforce for Paper's opportunities. Use this whenever the user asks for a slip-rate analysis, slip rate, or push rate, or types the /slip-rate command — even if they don't specify a time frame, product, or sales motion (the skill will ask). Trigger this for any request involving how often deals are pushed out of quarter, slip rates by product (GROW, On-Demand) or by sales motion (NBEX, Renewal/Rebuy), or comparisons of slip rates across quarters, halves, or fiscal years. Also trigger for per-stage or funnel slip-rate questions, e.g. \"slip rate by stage\" — this skill supports that breakdown too. Prefer this skill over ad-hoc SOQL whenever slip rate is the metric."
---

# Slip-Rate Analysis

This skill produces a clean slip-rate table for Paper's Salesforce
opportunities. It answers one specific question: **of the opps that were open
on Day 1 of the quarter with a close date expected inside that quarter, how
many ended up pushed past the quarter's end?**

- **Cohort (denominator)** = opps that existed and were still open as of Day 1
  of the quarter, *and* whose `CloseDate` — as it stood on Day 1, not its
  current value — fell within the quarter.
- **Slipped** = an opp in that cohort whose close date ended up outside the
  quarter: it closed (won or lost) with a final `CloseDate` after quarter end,
  or it's still open with a current `CloseDate` after quarter end.
- **Held** = an opp in that cohort that closed on time (any day within the
  quarter counts, even if the exact date moved around inside it).

**"Held" means the date held, not that the deal was won.** An opp closed **lost**
inside the quarter is Held — it resolved when the forecast said it would, it just
resolved badly. This metric measures close-date reliability, not deal health, and
the two are easy to conflate. Held can be overwhelmingly composed of on-time
losses.

Because of that, **whenever you present a slip rate you must also state the
held-won / held-lost split and carry a one-line note that in-quarter losses count
as Held.** Don't leave the reader to infer that a high hold rate is good news. If
Held is a large share of the cohort and almost all of it is losses, say so
plainly — that's a materially different story from a segment that held and won.

A related trap: do **not** contrast "slipped deals that were later lost" against
the held count to argue that slipping causes losses without first computing the
segment's base win rate. If the segment's held deals were mostly losses too, then
a slipped subset with zero wins is the expected result, not a finding. Compute the
held win rate, apply it to the number of resolved slipped deals, and only call the
difference meaningful if the observed shortfall is larger than that expectation
can account for. With slipped subsets typically in the low tens, it usually
isn't.
**Exclude `Closed Cleanup` opps entirely.** This stage marks opps that were
closed lost for administrative reasons — not a real sales outcome, just
record-keeping — and they must never enter the cohort, the slipped count, or
the held count. Filter them out before anything else, same tier as the
test-data exclusions below.

**Count slip rate** = Slipped count ÷ Cohort count
**Dollar slip rate** = Σ `Amount` of slipped opps ÷ Σ `Amount` of the cohort

A related but distinct question is the **per-stage (funnel) slip rate**: of the
opps that slipped, what stage were they sitting in when they slipped? See Step
6 — it covers both the methodology and when to run it.

## Step 0 — Confirm the model and effort level

This analysis leans on multi-pass set reconstruction and judgment calls
(outlier detection, low-sample caveats, reopened-opp handling) that a lighter
model or lower effort setting is more likely to get subtly wrong without
erroring. If the active model is anything other than an Opus-tier model, tell
the user this analysis is more reliable on Opus and recommend they switch
before you proceed. Separately, if you can tell the current effort level is
below xhigh, recommend the user raise it to xhigh (via `/effort xhigh` in
Claude Code, or the effort control in Claude.ai) before proceeding. Don't run
the analysis until the user confirms they want to proceed as-is or has made the
switch.

## Step 1 — Confirm Salesforce is connected

This analysis is impossible without Salesforce. If the Salesforce
connector/tools aren't available, stop and tell the user you need them to
connect Salesforce before you can continue. Don't try to approximate the
numbers from anywhere else — Salesforce is Paper's source of truth and a wrong
slip rate is worse than none.

## Step 2 — Gather the parameters

The analysis needs a time frame, a product, and a sales motion. If the user
already supplied any of these in their request, use them and don't re-ask. Only
ask about what's missing. Ask concisely — ideally batch the missing questions
together rather than interrogating one at a time.

These same parameters drive the per-stage breakdown too, so gather them once
regardless of which analysis was requested. If the user explicitly asked for a
per-stage/funnel slip rate rather than the standard rate, go straight to Step 6
once you have the parameters — Step 3 still runs (it builds the cohort), but
Steps 4–5 are skipped.

### Time frame

Paper's fiscal year matches the calendar year, so quarters and halves map to
calendar months. Accept any of:

- A fiscal **quarter**: Q1 (Jan 1 – Mar 31), Q2 (Apr 1 – Jun 30), Q3 (Jul 1 –
  Sep 30), Q4 (Oct 1 – Dec 31)
- A fiscal **half**: H1 (Jan 1 – Jun 30), H2 (Jul 1 – Dec 31)
- A fiscal **year**: e.g. FY26 = Jan 1 – Dec 31, 2026
- A **custom** range the user gives you
If the user names a quarter/half without a year (e.g. "Q2"), ask which year, or
assume the current fiscal year if context makes it obvious — but say which year
you used. The chosen range plays two roles here: its **start date is "Day 1"**
(the cohort snapshot point) and its **end date is the slip threshold**
(anything closing after this date has slipped).

#### The Day 1 datetime boundary — fixed convention

`CreatedDate` on `Opportunity` and on the history objects is a **datetime**, so
"Day 1" needs a precise instant, and the choice shifts a handful of edge records.
Use this convention every run so results stay comparable across quarters:

> **Day 1 = 00:00:00 Eastern time (`America/New_York`) on the quarter's first
> day**, converted to the equivalent UTC literal in the SOQL. Eastern is Paper's
> operating timezone, so this makes the snapshot land at the true start of the
> business day rather than mid-evening the day before.

Eastern is UTC−5 in winter and UTC−4 during daylight time, so the offset differs
by quarter. Worked examples:

| Quarter | Day 1 (Eastern) | SOQL literal to use |
|---|---|---|
| Q1 | Jan 1, 00:00 EST | `<YYYY>-01-01T05:00:00Z` |
| Q2 | Apr 1, 00:00 EDT | `<YYYY>-04-01T04:00:00Z` |
| Q3 | Jul 1, 00:00 EDT | `<YYYY>-07-01T04:00:00Z` |
| Q4 | Oct 1, 00:00 EDT | `<YYYY>-10-01T04:00:00Z` |

Apply the same literal everywhere Day 1 appears — Pass 1's `CreatedDate <=`,
Pass 2's `CreatedDate <`, and Pass 3's `CreatedDate >` — or the passes will
disagree about which opps are candidates. `CloseDate` is a plain **date** field
and needs no timezone handling; keep using the bare `<YYYY>-01-01` form for it.

**Note on prior runs:** the Q1 2026 and Q2 2026 figures recorded at the bottom of
this skill were computed with a UTC-midnight Day 1 (`T00:00:00Z`), five hours
earlier than this convention. The difference only affects opps created during
those five hours, so the recorded rates are still usable as reference points —
but a re-run under this convention may differ by an opp or two. Don't treat a
small discrepancy against those numbers as a bug.

### Completeness check — required before running

Check today's date against the time frame's end date. **If the
quarter/half/year hasn't fully ended yet, refuse to run the analysis and say
why**: a slip rate needs every cohort opp's outcome resolved (either closed, or
still open past the end date) to mean anything. An in-progress period would
understate slips, since deals still on track haven't had the chance to slip
yet. Suggest the most recent *completed* period of the same granularity instead
(e.g. if they ask for the current quarter, offer the prior one).

### Product (`Opportunity.Product_Type__c`)
Ask whether they want **GROW**, **On-Demand**, or **both**. Map the raw field
values into buckets:

- **GROW** bucket ← `GROW`, `GROW AI`
- **On-Demand** bucket ← `On-Demand`, `On-Demand + MC`, `On-Demand AI`
- **Ignore** every other value (notably standalone `MC`, which is a divested
  product, and anything unrecognized).
- Merge `GROW`+`GROW AI` together, and merge the three On-Demand variants
  together, *unless* the user explicitly asks to see a variant separately or on
  its own (e.g. "show GROW AI separately", "just On-Demand + MC"). Honor that
  request when made, but the default is the merged buckets above.
If the user asks for **both** products, ask whether they want GROW and
On-Demand **merged into one number or shown as separate rows**. Default to
**separate** if they don't care.

### Sales motion (`Opportunity.Type`)
Ask whether they want **NBEX**, **Renewal/Rebuy**, or **both**. Map `Type`
into:

- **NBEX** ← `New Business`, `Expansion`, `New Logo`, `New Logo Rebuy`,
  `Cross-Sell`, `Pilot`
  - `Expansion` is **not an active `Type` picklist value in this org** — as of
    July 2026 the live values are `New Business`, `New Logo`, `New Logo Rebuy`,
    `Cross-sell`, `Pilot` (plus the Renewal-side values). Keep it in the `IN`
    list anyway: it costs nothing, SOQL won't error on it, and it still catches
    any historical record holding a since-retired value. Just don't be surprised
    when it matches zero rows.
- **Renewal/Rebuy** ← everything else (e.g. `Renewal`, `Rebuy`, `Short-Term
  Renewal`)
Match `Type` values case-insensitively and tolerate small spelling variations —
Salesforce stores `Cross-sell` (lowercase s), so a strict match would silently
drop those deals. When in doubt, treat any type you can't confidently place in
NBEX as Renewal, since NBEX is the explicitly enumerated set.

If the user asks for **both** motions, ask whether they want them **merged or
shown as separate rows**. Default to **separate**.

## Step 3 — Query Salesforce

Unlike a straightforward closed-opp query, this needs a **historical
reconstruction**: what did each opp's `CloseDate` look like on Day 1, and was
the opp even still open at that point? Build it in three passes.

### Pass 1 — Candidate opps

Pull opps that existed as of Day 1 **and** whose current `CloseDate` is on or
after Day 1, excluding test data and `Closed Cleanup` opps. Filter directly to
the requested product(s) and motion(s) in the query itself — don't pull full
org history and bucket afterward:

```sql
SELECT Id, Product_Type__c, Type, CreatedDate, CloseDate, IsClosed, IsWon, Amount, StageName
FROM Opportunity
WHERE CreatedDate <= <Day1>
  AND CloseDate >= <Day1>
  AND Is_Test__c = false
  AND (NOT Account.Name LIKE '%test%')
  AND (NOT StageName LIKE '%Closed Cleanup%')
  AND Product_Type__c IN (<raw values for the requested product bucket(s)>)
  AND Type IN (<raw values for the requested NBEX motion, omit this line if both motions or Renewal/Rebuy requested — see below>)
```

**`CloseDate >= <Day1>` is the key narrowing filter** — it cuts the candidate
set to a small fraction of full org history without losing valid cohort members
in the vast majority of cases. A Held opp's current CloseDate falls inside the
quarter (≥ Day1); a Slipped-and-closed opp's current CloseDate falls after
quarter end (≥ Day1); a Slipped-and-still-open opp's CloseDate field, left
untouched or nudged forward, is also ≥ Day1 under normal data hygiene. **Known
edge case:** an opp still open past quarter-end whose `CloseDate` was manually
or programmatically reset to a date *before* Day1 (e.g. a stale-deal cleanup
automation backdating the field) will be missed, undercounting Slipped. This is
rare — flag it as a caveat if a result looks suspiciously low, but don't reach
for a `CreatedDate` floor instead; that reintroduces the full-history pull this
filter exists to avoid.

**Push product/motion filtering into this query** using the raw values from
Step 2's mapping, not a post-query bucketing pass:
- GROW only → `Product_Type__c IN (<the 2 GROW values>)`; On-Demand only → `IN
  (<the 3 On-Demand values>)`
- Both products → all 5 values in one `IN` list (still worth doing — it
  excludes standalone `MC` and unrecognized values)
- NBEX only → `Type IN (<the 6 NBEX values>)`
- Renewal/Rebuy only → `Type NOT IN (<the 6 NBEX values>)` — Renewal/Rebuy is
  defined as the complement of NBEX, so exclude the NBEX list rather than
  trying to enumerate every renewal-type value
- Both motions → omit the `Type` filter entirely and bucket in code afterward
`Closed Cleanup` is filtered here rather than later because it's a
current-state field — cheapest to drop before any Pass 2/3 history
reconstruction. Use `LIKE` rather than an exact match, in case the stage
carries a numeric prefix (e.g. `6. Closed Cleanup`) or the label shifts
slightly over time.

### Pass 2 — Was it still open on Day 1?

Don't pull the full stage history and search it for the earliest terminal
transition. **Query the exclusions directly**: the only opps you need to
identify are those that had already entered a terminal stage (any stage
containing "Closed", e.g. `Closed Won`, `Closed Lost`, `Closed Lost – No
Decision`) *before* Day 1. Everything else was open.

Filter on the parent `Opportunity` fields rather than passing an Id list — see
"Query shape" below:

```sql
SELECT OpportunityId, StageName, CreatedDate
FROM OpportunityHistory
WHERE StageName LIKE '%Closed%'
  AND CreatedDate < <Day1>
  AND Opportunity.CreatedDate <= <Day1>
  AND Opportunity.CloseDate >= <Day1>
  AND Opportunity.Is_Test__c = false
  AND (NOT Opportunity.Account.Name LIKE '%test%')
  AND (NOT Opportunity.StageName LIKE '%Closed Cleanup%')
  AND Opportunity.Product_Type__c IN (<same raw values as Pass 1>)
ORDER BY OpportunityId, CreatedDate ASC
```

**Pass-2 survivors = Pass 1 candidates minus the `OpportunityId`s this
returns.** This is logically identical to finding each opp's earliest terminal
transition and comparing it to Day 1, but the result set is tiny — typically a
handful of rows rather than thousands — because the two date filters do the
work. In a representative run this returned 3 rows against 327 candidates.

**Decision point — reopened opps.** This rule excludes an opp that closed
before Day 1 and was later reopened, even though it genuinely *was* open on Day
1. Example: an opp that went `Closed Won` in a prior year, was reopened, and
sits in `1. Pre-Renewal` today. The current rule drops it. If Paper would
rather count such opps as cohort members, change the test from "has any
terminal transition before Day 1" to "its *latest* stage transition before Day
1 was a terminal one" — which requires the full ordered history for the
affected Ids, so only make this change if reopened opps are common enough to
matter. **Pick one behavior and state it here explicitly** so future runs are
consistent.

### Pass 3 — What was CloseDate on Day 1?

Only opps that **survived Pass 2** can be cohort members, so ignore the Pass-2
exclusions when interpreting these results.

**You only need history rows created *after* Day 1.** Field history chains
contiguously — the `NewValue` of one row is the `OldValue` of the next — so the
reconstruction collapses into a single rule:

> **If an opp has at least one `CloseDate` history row with `CreatedDate >
  Day1`, its Day-1 value is the `OldValue` of the *earliest* such row. If it
  has none, its Day-1 value is its current `CloseDate`.**

That holds whether or not the opp also has rows at or before Day 1, which means
you can filter them out of the query entirely and roughly halve the payload:

```sql
SELECT OpportunityId, OldValue, NewValue, CreatedDate
FROM OpportunityFieldHistory
WHERE Field = 'CloseDate'
  AND CreatedDate > <Day1>
  AND Opportunity.CreatedDate <= <Day1>
  AND Opportunity.CloseDate >= <Day1>
  AND Opportunity.Is_Test__c = false
  AND (NOT Opportunity.Account.Name LIKE '%test%')
  AND (NOT Opportunity.StageName LIKE '%Closed Cleanup%')
  AND Opportunity.Product_Type__c IN (<same raw values as Pass 1>)
ORDER BY OpportunityId, CreatedDate ASC
```

Because the result is ordered by `OpportunityId` then `CreatedDate ASC`, the
**first row for each `OpportunityId`** is the one you want — take its
`OldValue`. Ignore that opp's remaining rows.

Sanity-check the contiguity assumption once per run: pick two or three opps
with multiple rows and confirm each row's `NewValue` equals the next row's
`OldValue`. If a gap appears (which would suggest untracked writes), pull the
pre-Day-1 rows too and reconstruct from the full chain.

### Query shape — prefer parent filtering over Id batching

**Default: filter the history objects on their parent `Opportunity` fields**,
repeating Pass 1's `WHERE` clause with an `Opportunity.` prefix, as shown in
Passes 2 and 3 above. Both `OpportunityHistory` and `OpportunityFieldHistory`
support this traversal (confirmed working three levels deep, e.g.
`Opportunity.Account.Name`). This turns each pass into **one query call instead
of four to eight**, and removes the truncation risk described below.

**Fallback: Id batching**, if parent traversal is ever blocked in this org.
Batch at roughly 75–100 Ids per call — both history objects can be high-volume
(a single opp can carry dozens of rows), so larger batches risk exceeding the
~2,000-row page size. If a batch comes back truncated, split it in half and
re-run rather than proceeding with a partial result.

**Check the `done` flag on every query**, parent-filtered or batched. A `done:
false` means the page was truncated and rows are silently missing — there's no
way to page through `nextRecordsUrl` with this tool — so a truncated Pass 2 or
Pass 3 silently understates slips and invalidates the run.

**Minimize hand-transcription of query results.** Results arrive inline in the
conversation with no pipe to a file, so getting them into a computation means
retyping them — the main silent-error risk in this skill. Use the aggregation
path in "Assemble the cohort and outcomes" below to keep the transcribed set as
small as possible. Transcribe only the rows that genuinely require row-level
reconstruction, and when you do, assert the expected row count in code (e.g.
`assert len(CAND) == <totalSize from the query>`) so a dropped or duplicated
line fails loudly instead of silently skewing a rate. The audit CSVs in Step 8
are where that transcribed set gets written down and checked against the
query's `totalSize` — if the user wants them, they're also your own record of
what was transcribed when a rate later looks off.

### Assemble the cohort and outcomes

The **cohort** = candidate opps that were open on Day 1 (Pass 2) *and* whose
Day-1 `CloseDate` (Pass 3) falls within the quarter.

#### Non-cohort candidates — report every exclusion reason

Pass 1 deliberately casts wider than the cohort (it filters on *current*
`CloseDate >= Day1`, not the Day-1 value), so a large share of candidates
legitimately drop out. Tag every dropped candidate with its exclusion reason and
**report the count for each reason present**. Don't assume how many reasons will
appear in a given run — a bucket being empty this quarter says nothing about next
quarter. There are three paths out:

- **`not_open_on_day1`** — excluded by Pass 2: the opp had a terminal stage
  transition before Day 1, so it wasn't open at the snapshot point. Often zero, but
  never assume it; if Pass 2 returns no rows, validate that the query works before
  believing the zero (see Pass 2 above).
- **`day1_close_after_quarter`** — Day-1 `CloseDate` was already past quarter end.
  These were never in-quarter deals; they were forecast for a later period and only
  surfaced in Pass 1 because their current date is in range. Expect this bucket to
  be large and unremarkable.
- **`day1_close_before_quarter`** — Day-1 `CloseDate` was *already in the past* on
  Day 1. These deals entered the quarter carrying a stale, overdue close date.
  They're correctly excluded (they weren't forecast to close in the quarter, they
  were forecast to have closed already) — but the bucket's **size is a signal
  about forecast hygiene**, not a neutral filtering artifact. A rep who never
  re-dates a dying deal produces these instead of producing slips, which means a
  clean-looking slip rate can coexist with bad pipeline discipline.

Call out that last bucket explicitly whenever it's non-trivial, and offer to list
the opps in it — they're usually worth a separate cleanup conversation. Reporting
the cohort count without the exclusion breakdown hides the fact that the cohort
can be a minority of the candidate pool.

For each opp in that cohort, determine the outcome from its **current**
Opportunity fields:
- **Held**: `IsClosed = true` and current `CloseDate` falls within the quarter.
- **Slipped**: `IsClosed = true` and current `CloseDate` falls after the
  quarter end, **or** `IsClosed = false` (since the completeness check already
  confirmed the quarter is over, any opp still open at this point necessarily
  failed to close in time — this holds even if its current `CloseDate` field is
  stale and still shows a date inside the quarter).
### Splitting the work: two groups

Pass 3 partitions the candidates into two groups, and only one of them needs
row-level handling:

- **Group A — no post-Day-1 `CloseDate` change** (absent from Pass 3's
  results). Their Day-1 value *is* their current `CloseDate`, so no
  reconstruction is needed: they're in the cohort iff their current `CloseDate`
  falls within the quarter, and being closed-and-inside-the-quarter makes them
  Held.
- **Group B — has a post-Day-1 `CloseDate` change** (present in Pass 3's
  results). These need the per-opp Day-1 value and the Held/Slipped test
  applied individually.
**Group A's share of the candidate pool varies a lot by quarter — don't assume
it.** One run saw roughly two-thirds of candidates in Group A; another (Q2
2026) saw barely a fifth, with most candidates carrying at least one
`CloseDate` edit. Since Group B is the expensive part (no SOQL window-function
equivalent exists for "earliest row per Id," so it needs row-level
transcription), **run the Group A aggregate query first** — it's cheap — to see
how large Group B actually is before committing to a full row-level pass. That
tells you up front whether you're looking at a quick finish or a multi-batch
transcription job.

**Aggregate Group A with a semi-join subquery — never enumerate Group B's Ids
by hand.** SOQL supports `NOT IN (SELECT ... FROM ChildObject WHERE ...)`
semi-joins directly, which sidesteps building an `Id NOT IN (<hundreds of
ids>)` list entirely (a real cost at scale — one run had 253 Group B ids, a
~5,000-character list that would otherwise need retyping into every query):

```sql
SELECT Product_Type__c, Type, IsClosed, COUNT(Id), SUM(Amount)
FROM Opportunity
WHERE <Pass 1 filters>
  AND CloseDate <= <QuarterEnd>
  AND Id NOT IN (
    SELECT OpportunityId FROM OpportunityFieldHistory
    WHERE Field = 'CloseDate' AND CreatedDate > <Day1>
  )
  AND Id NOT IN (
    SELECT OpportunityId FROM OpportunityHistory
    WHERE StageName LIKE '%Closed%' AND CreatedDate < <Day1>
  )
GROUP BY Product_Type__c, Type, IsClosed
```

The subqueries aren't scoped to this run's product/motion filters — that's
fine, since the exclusion is a per-Id membership test (does *this* Id appear in
the history at all), not an aggregate, so extra org-wide noise in the subquery
doesn't affect correctness. No Id list needs building or transcribing.

Bucket the returned `Product_Type__c`/`Type` values into segments and add the
counts and dollars to Group B's row-level results. Only Group B gets
transcribed. Cross-check the aggregate dollar totals against `COUNT` for
null-`Amount` records here (see Notes).

**Transcribing a large Group B: work in verified batches, not one long pass.**
If Group A turns out small and Group B runs into the hundreds, don't try to
reconstruct Day-1 values for all of them in a single mental pass — that's
exactly where a transcription slip hides. Instead: pull Group B's distinct Ids
(`SELECT OpportunityId, COUNT(Id) FROM OpportunityFieldHistory WHERE ... GROUP
BY OpportunityId` gives the distinct list plus a free row-count check), then
reconstruct Day-1 values in batches of roughly 50–75 Ids at a time, asserting
the batch's row count in code after each one before moving on.

## Step 4 — Compute the slip rates

For each output row (a product × motion cell, or a merged group per the user's
choices):

- Count slip rate = slipped_count / cohort_count
- Dollar slip rate = slipped_amount / cohort_amount
Guard against divide-by-zero: if a cell has no cohort, show "—" rather than 0%
or an error, and note the empty segment.

## Step 5 — Present the table in chat

Output the result **directly in the chat as a rendered Markdown table** — this
is the primary deliverable, so lead with it rather than burying it under a
file. Include the counts and dollars behind each rate, because a rate without
its denominator is easy to misread (a 100% slip rate on 1 deal is not the same
story as 20% on 200 deals). Don't include a separate "Held" column — the cohort
count already implies it (Held = Cohort − Slipped), so it would just be
redundant.

Use a layout like this (columns/rows depend on what the user asked for):

| Segment | Cohort | Slipped | Count Slip Rate | Slipped $ | Cohort $ | Dollar Slip Rate |
|---|---|---|---|---|---|---|
| GROW – NBEX | 100 | 18 | 18.0% | $0.6M | $3.0M | 20.0% |
| On-Demand – Renewal/Rebuy | … | … | … | … | … | … |

Above or below the table, state the exact time frame (with the Day-1 and
quarter-end dates), the product and motion scope, and note that test accounts,
`Is_Test__c` records, and `Closed Cleanup` opps were excluded. If you had to
assume a fiscal year or default a merged/separate choice, say so plainly so the
user can correct you.

**Check whether any segment's dollar rate is really one deal.** Before
presenting, sort each segment's cohort by `Amount` and look at the top record's
share of the segment total. If one opp is a large fraction of a segment's
dollars, its Held/Slipped outcome single-handedly sets that segment's dollar
slip rate, and a wide gap between the count rate and the dollar rate is the
tell. Say so explicitly and give the rate with the outlier removed — e.g. "GROW
– Renewal/Rebuy's 18.1% dollar slip rate rests on one $4.5M held deal at 72% of
segment dollars; excluding it, the rate is ~63%."

As a concrete trigger, **flag any single opp at or above ~15% of its segment's
cohort dollars** and report the dollar rate both with and without it. Note that
this cuts both ways: a large **Held** deal *depresses* the dollar rate, so the
headline can understate slippage just as easily as overstate it. When a big held
deal is doing that, the outlier-excluded figure is usually the more
representative read and should be given alongside the headline, not instead of
it. Don't only look for outliers that inflate the number.

Watch specifically for **bulk-created record blocks**: runs of opps sharing a
`CreatedDate` to the second and a repeated identical `Amount` (e.g. seventeen
records created in one second, nearly all at exactly $40,000, one at $4.5M).
These are usually a migration or automation artifact, and an anomalous amount
inside such a block is more likely a data-quality problem than a real deal.
Flag it for hygiene review rather than reporting the rate flatly.

**Flag low-sample segments below the table.** A slip rate computed on a handful
of cohort opps swings wildly on a single deal and shouldn't be read the same
way as one built on hundreds. After the table, add a short note calling out any
segment with a small cohort so the reader takes it with a grain of salt. Use
roughly **fewer than 10 cohort opps** as the trigger for a caveat, and treat
**fewer than ~30** as "interpret with some caution" — but use judgment rather
than a hard cutoff, and name the specific segment(s) and their counts, e.g. "⚠️
GROW – NBEX is based on only 6 cohort opps, so its slip rate is not
statistically meaningful." If every segment is well-populated, no note is
needed.

## Step 6 — Offer (or run) the per-stage slip-rate breakdown

This step answers a different question from the main table: **of the opps that
slipped, what stage were they sitting in when they slipped**? Run it either
because the user explicitly asked for a per-stage/funnel breakdown — in which
case it replaces Steps 4–5, though Step 3 still runs to build the cohort — or
as a follow-up offer after presenting the standard table. If offering, ask a
single concise question, e.g. "Want me to also break this down by stage — which
stage were the slipped deals sitting in?" Don't run it unless the user asked
for it or agreed to the offer.

### Definitions

- **Stage** here means the opp's `StageName` **as of the moment its close date was
  pushed out of the quarter** — a point-in-time reconstruction, not a read of the
  current field. This requires a `StageName` field-history query (see Query
  approach below).

  **Do not use the current `StageName`.** It answers a different and much less
  useful question. Most slipped deals eventually close, so their current stage is
  terminal, and a table built on the current field collapses onto `Closed Lost`
  while telling you nothing about where slippage originates.

  *Worked example (one run — GROW–NBEX, Q1 2026; illustrative only, don't expect
  these shapes to repeat).* Same 25 slipped deals, scored both ways:

  | Stage | Current-field (wrong) | At push-out (correct) |
  |---|---|---|
  | 0. SDR Holding | 0 | 1 |
  | 1. Sales Qualified | 2 | 6 |
  | 2. Demo | 3 | 8 |
  | 3. Proposal | 1 | 9 |
  | 5. Negotiation | 0 | 1 |
  | Closed Lost | 19 | 0 |

  The left column says "76% of slips are closed-lost deals," which is a tautology.
  The right column locates slippage in the funnel, which is actionable. That
  divergence is the whole reason this step spends an extra query — if a future run
  is tempted to shortcut back to the current field, this is why not.

- **Defining the push-out event.** Using the Pass 3 `CloseDate` history rows, find
  the edits where `OldValue` is on or before quarter end and `NewValue` is after
  it — the transitions that carried the deal out of the quarter. **Use the last
  such crossing**, not the first: a deal pushed out, pulled back in, then pushed
  out again slipped on the final push, and the earlier one didn't stick.
- **The push event can fall after quarter end.** A deal still dated for the last
  day of the quarter that gets re-dated a few days later has a push timestamp
  outside the period. That's legitimate — don't filter these out, and don't assume
  a late push means a late-stage deal.
- **Terminal stages should be rare here, and are worth investigating.** Unlike the
  current-field version, a deal in a closed stage at the moment of push-out is
  anomalous — it means someone re-dated an already-closed opp. If terminal rows
  appear, surface them as a data-hygiene flag rather than a normal row. **`Closed
  Cleanup` should never appear at all** — those opps were filtered in Pass 1; if
  you see one, the Pass 1 filter wasn't applied, so go back and exclude them.
- **Expect retired stage values.** Historical reconstruction surfaces stages that
  no longer exist in the current picklist — `1. Discovery` is one known retired
  NBEX value that appears in older history. Report such values as their own rows
  under their historical name; don't silently remap them onto a current stage, and
  don't drop them.
- **Fallback for slips with no push event.** A deal can slip by staleness: its
  close date never moves, it simply sits open past quarter end. It's Slipped under
  the Step 3 assembly rule but has no push timestamp to measure. Report these as a
  separate **"slipped without re-dating"** row rather than forcing them into a
  stage bucket — they're the same forecast-hygiene pathology as the
  `day1_close_before_quarter` bucket in Step 3, and lumping them into a stage
  hides that. Don't assume this bucket is empty; check it every run.
- **Denominator** = the same cohort from the main analysis, restricted to the
  **slipped** subset (Step 3's assembly). Held opps don't have a "stage when
  they slipped," so they're out of scope for this view entirely.
- **The Renewal pipeline has its own stage names — don't mix them with NBEX.**
  Paper's `StageName` picklist holds two parallel sequences that share some
  numbers: an NBEX path (0. SDR Holding → 1. Sales Qualified → 2. Demo → 3.
  Proposal → 4. Planning → 5. Negotiation) and a Renewal path (1. Pre-Renewal →
  2. Renewal Planning → 3. Negotiation → 4. Purchase Approval → 5.
  Contracting). If the slipped cohort is NBEX-only or Renewal/Rebuy-only,
  that's not an issue. If it mixes both motions, build **two separate per-stage
  tables** (one per pipeline) rather than one — a shared leading digit doesn't
  mean the same stage. Terminal stages (`Closed Won`/`Closed Lost`) are shared
  across both pipelines and can appear in either table.
### Query approach

1. Start from the slipped-opp `Id` set Step 3 produced — run Step 3 in full first
   if you haven't. This step **does need one new query**; Pass 1's `StageName` is
   the current value and is the wrong field for this view.
2. Derive each slipped opp's push-out timestamp from the Pass 3 rows you already
   have (last `OldValue <= QuarterEnd` → `NewValue > QuarterEnd` crossing). No
   query needed — it's already in hand.
3. Query `StageName` field history for the slipped set only. This is usually a
   small set — a few dozen opps at most — so an explicit `Id IN (...)` list is fine
   and more precise than a semi-join:

   ```sql
   SELECT OpportunityId, OldValue, NewValue, CreatedDate
   FROM OpportunityFieldHistory
   WHERE Field = 'StageName'
     AND OpportunityId IN (<slipped opp ids>)
   ORDER BY OpportunityId ASC, CreatedDate ASC
   ```

4. Reconstruct the stage in effect at each push timestamp `T`:
   - **Latest edit at or before `T` exists** → the stage is that edit's `NewValue`.
   - **Edits exist but all are after `T`** → the stage is the *earliest* edit's
     `OldValue` (the opp's stage before any recorded change).
   - **No `StageName` history rows at all** → the stage never changed, so the
     current `StageName` is correct for every point in the opp's life. Expect a
     handful of these in any run.

   Record which of the three branches produced each answer, so the audit file can
   show the derivation rather than just the result.

5. Group by the reconstructed stage (splitting into NBEX/Renewal tables if the
   scope mixes both motions per above), and compute counts and dollars per stage.

6. **Check the push timestamps for clustering.** Sort the push events
   chronologically and look for runs within a few minutes of each other — that's
   one person re-dating a batch of deals in a single sitting, usually a pipeline
   review, rather than that many independent judgments. Report the clustered share
   alongside the stage table when it's material: it reframes the slip rate from a
   sum of individual deal problems into a handful of bulk forecast resets, which is
   a different management conversation. Use roughly a 10-minute window as the
   default test, and name the dates the clusters fall on — they usually correspond
   to a recurring forecast meeting.
### Segment scope

If the user's main analysis has multiple segments (e.g. GROW and On-Demand
shown separately), default to **one combined per-stage table** across the
requested scope rather than a per-stage table per segment — that's usually what
"break it down by stage" means. Note this choice, and offer to split the
per-stage view by segment too if they want that level of detail. This is
independent of the NBEX/Renewal pipeline split above — if the scope spans both
motions, produce two pipeline tables *and* apply this same segment-combining
default within each.

### Output format

Render in a table with stage as the row:

| Stage at Push-Out | Slipped Count | % of Slipped | Slipped $ | % of Slipped $ |
|---|---|---|---|---|
| 1. Sales Qualified | … | … | … | … |
| 2. Demo | … | … | … | … |
| 3. Proposal | … | … | … | … |
| *slipped without re-dating* | … | … | … | … |

Order rows by the pipeline sequence, not by size — the shape of the funnel is the
point. Include the "slipped without re-dating" row only when non-empty, and any
retired stage values in their correct historical position.

This view doesn't have a "rate" per row the way the main table does — it's a
distribution of **where deals were standing when they were pushed out**, not a
rate against a denominator — so show counts and dollars plus each row's share of
the slipped totals. Say explicitly in the surrounding prose that the denominator
is the slipped subset only, since held deals have no push-out event.

Also read the **absences** aloud, not just the populated rows. A stage with no
slips is a real finding: if nothing slipped from the late stages, then deals that
reached them resolved inside the quarter and the problem sits at the top of the
funnel rather than in closing — and the reverse pattern points somewhere entirely
different. Check the empty rows against the pipeline sequence before writing the
summary; that conclusion is invisible in a table read only for its biggest row.

## Step 7 — Offer a trend comparison

A single period's slip rate is a snapshot; the more useful question is usually
"which way is it moving?" So once the main table is delivered, ask the user
whether they'd like to compare it against another period to see how the slip
rate is changing over time. Suggest two options:

1. The **adjacent period before or after** the one they requested (the quickest
   apples-to-apples comparison).
2. **Any other period they name**, as long as it's the same granularity as the
   original request (quarter-to-quarter, half-to-half, year-to-year,
   month-to-month, etc.) and the period is complete. Comparing a quarter
   against a full year, for instance, isn't meaningful, so keep the granularity
   matched.
**Only ever recommend or accept comparison periods that are entirely in the
past**, per the completeness check in Step 2. For example, if the current
fiscal quarter is Q3 and the user asked about Q2, the period *after* (Q3) is
still in progress, so offer only the period *before* (Q1) among the adjacent
options. If the user picks a period of their own that isn't yet complete, point
that out and suggest the most recent complete period of that granularity
instead. If both adjacent periods are fully in the past, you may offer either
or both.

**Set the expectation when you offer.** A comparison period is a **full second
three-pass reconstruction**, not a cheap filter on what you already have: Day 1
moves, so both the candidate set and every opp's Day-1 `CloseDate` change, and
nothing from the first run is reusable. Say that in the offer ("that's a second
full pass over history — worth it?") so the user can decide whether they want
it now or later.

If the user agrees, rerun the exact same analysis for the comparison period —
**identical product bucket(s) and sales motion(s), identical exclusions and
math** — so the only thing that changes is the date range. Render those results
in a Markdown table formatted just like the first one (same columns), and label
each table clearly with its period.

Then, underneath, add a few plain-language comments highlighting any
interesting trends: which segments' slip rates rose or fell and by how many
points, whether count and dollar rates moved in the same direction or diverged
(e.g. slipping more deals but smaller ones), shifts in cohort size, and
anything that looks like a meaningful change versus noise. Keep the low-sample
caveat in mind here too — a swing driven by a segment with only a few cohort
opps is likely noise, so say so rather than over-interpreting it.

## Step 8 — Offer an audit CSV set

Once the analysis is complete, offer to package the underlying data as
downloadable CSVs. This matters more here than in any other metric skill: the
cohort is a *reconstruction*, not a filter, so "why is this deal in the
cohort?" and "why did you call this one slipped?" are questions the chat table
can't answer on its own. The same files are also the fastest way to
troubleshoot this skill when a rate looks wrong, and they're where the
row-level transcription work from Step 3 gets written down and verified instead
of living only in the conversation. Ask once, concisely — e.g. "Want an audit
CSV set: every candidate opp with its Day-1 close date and Held/Slipped call,
plus every SOQL query I ran, so this can be rebuilt in Sheets?" Don't create
anything unless the user asks or accepts.

### Always include these two files

- **`queries.csv`** — every SOQL statement sent to the Salesforce connector
  this run, in the order you ran them. Columns: `query_number`, `pass` (Pass 1
  / Pass 2 / Pass 3 / Group A aggregate / Group B batch), `sobject`, `soql`,
  `rows_returned`, `total_size`, `done_flag`. Collapse each statement to a
  single line with single spaces so it lands in one cell, and write the query
  **exactly as sent**, with the real dates and value lists substituted in — not
  the placeholder version from this skill. With three passes plus batches, this
  file is the only complete record of what was actually asked of Salesforce.
- **`run-metadata.csv`** — the methodology record, as `key`,`value` rows: skill
  name, run date, the **Day-1 date** and the **quarter-end slip threshold** as
  separate keys, the product buckets *and* the raw `Product_Type__c` values
  behind them, the motion buckets and their raw `Type` values, every exclusion
  applied, any default or assumption you made, which reopened-opp rule was in
  force (per the decision point in Pass 2), the Group A and Group B sizes, the
  outcome of the Pass 3 contiguity sanity check, the `done` flag on every pass,
  and each data file's row count.

### Data files

- **`candidates.csv`** — the important one. One row per Pass 1 candidate, raw
  fields first (`Id`, `Product_Type__c`, `Type`, `CreatedDate`, `CloseDate`,
  `IsClosed`, `IsWon`, `Amount`, `StageName`), then the entire reconstruction
  as columns: `open_on_day1`, `day1_close_date`, `day1_source` (`current_field`
  for Group A or `history_oldvalue` for Group B), `group` (A or B),
  `in_cohort`, `outcome` (`Held`, `Slipped`, or `not_in_cohort`), `slip_reason`
  (`closed_late` or `still_open`), `segment`, `amount_used`, and
  `excluded_reason`. Every judgment call Step 3 makes is a column here, which
  is what lets someone disagree with a specific deal rather than with the rate
  in the abstract.
- **`closedate-history.csv`** — the Pass 3 rows: `OpportunityId`, `OldValue`,
  `NewValue`, `CreatedDate`, plus `is_earliest_post_day1` marking the row whose
  `OldValue` you actually took. That flag makes the Day-1 rule visibly applied
  rather than asserted, and it's what you check first when a Day-1 value looks
  wrong.
- **`stage-history-exclusions.csv`** — the Pass 2 rows: the candidates dropped
  for having a terminal stage transition before Day 1, with their `StageName`
  and `CreatedDate`. Usually a handful of rows, and worth including precisely
  because it's a small, easily-challenged exclusion.
- **`group-a-aggregate.csv`** — only if the Group A semi-join aggregate was
  used. Write the returned aggregate rows exactly as they came back and note in
  `run-metadata.csv` that Group A has no per-deal detail. **Don't re-query to
  manufacture row-level rows** and don't reconstruct per-deal rows from the
  aggregates — say plainly which part of the cohort is aggregate-only, and
  offer a fresh row-level run if the user wants per-deal detail for it. When
  Group A is aggregate-only, `candidates.csv` covers Group B; say so in
  `run-metadata.csv` rather than leaving the reader to notice the count
  mismatch.
- **`stage-at-push-out.csv`** — required **only if Step 6 was run**. Step 6 now
  reconstructs the stage as of each slipped opp's push-out event, which is *not*
  derivable from `candidates.csv` (that file's `StageName` is the current value,
  which Step 6 deliberately doesn't use). One row per slipped opp:
  `OpportunityId`, `push_event_at`, `pushed_from`, `pushed_to`,
  `stage_at_push_out`, `derivation` (which of the three reconstruction branches
  produced it), `current_stagename`, `amount`. Include a companion
  `stagename-history.csv` holding the raw `StageName` field-history rows the
  reconstruction was built from, so the derivation is checkable rather than
  asserted. If Step 6 wasn't run, omit both files.

### Keep the rows honest

- Include the candidates that didn't make the cohort rather than dropping them
  — that's what `in_cohort`, `excluded_reason`, and `outcome = not_in_cohort`
  are for. A cohort file that contains only cohort members can't show anyone
  *why* a deal was left out, which is the question that actually gets asked.
- **Only rows a query actually returned this run.** Never infer, reconstruct,
  or backfill a row to make a file look complete — record the gap in
  `run-metadata.csv` instead. That includes the known Pass 1 edge case (an opp
  still open past quarter-end whose `CloseDate` was backdated before Day 1 is
  never in the candidate set, so it can't be in the file either — note the
  caveat, don't invent the row).
- **Assert the row counts.** Each data file's row count must equal that query's
  `totalSize`, and `candidates.csv`'s cohort/slipped counts must match the
  table you already presented. If they don't, the table is wrong — stop and say
  so rather than shipping a CSV that disagrees with it. If any pass came back
  `done: false`, say so in `run-metadata.csv` *and* in chat, since a truncated
  Pass 2 or 3 silently understates slips and invalidates the whole run.

### CSV formatting

- Header row, UTF-8, one row per record.
- Quote any field containing a comma, double quote, or newline, and double any
  embedded double quotes. The `soql` column needs this.
- Dates as ISO `YYYY-MM-DD` and datetimes as ISO 8601, so Sheets parses them as
  dates rather than text. This applies to `OldValue`/`NewValue` in the history
  file too — normalize them to ISO rather than passing through whatever format
  the API returned.
- Numbers raw: `4540000`, never `$4.5M`. No currency symbols, thousands
  separators, or `%` signs — the chat table abbreviates, the CSV must not.
  Write rates as decimals if you include them at all.
- Null `Amount` → an empty cell in the raw column and `0` in `amount_used`, so
  the null-vs-zero distinction survives into Sheets (see the null-`Amount` note
  below).
- Booleans as `TRUE`/`FALSE`.

### Delivery

Create each file with `create_file` inside a run-scoped directory named for the
metric and period (e.g. `slip-rate-audit-Q2-2026/`), then hand them over with
`present_files` so the user can download them. Don't use a fixed path —
`create_file` fails outright on an existing path, and a prior run's files may
still be present. **Don't paste CSV contents inline as a code block**; the
point is a downloadable file. If a trend comparison from Step 7 was also run,
produce a separate labelled folder per period rather than mixing periods in one
file — Day 1 differs between periods, so a shared file would have two
incompatible `day1_close_date` columns.

These files carry account-level deal data and amounts, so they're internal-only
— mention that when you hand them over, and don't upload them anywhere unless
the user asks.

## Notes and gotchas

- **Source of truth is Salesforce.** Never fabricate or estimate; if a query
  fails, say so and retry rather than guessing.
- **Field reference:** `Product_Type__c` (product), `Type` (sales motion),
  `CreatedDate` (existence check), `CloseDate`/`IsClosed`/`IsWon`
  (current-state fields, used only for the final-outcome check in Step 3's last
  part), `Amount` (dollar weighting), `Is_Test__c` and `Account.Name` (test
  exclusions),
  `OpportunityFieldHistory.Field`/`OldValue`/`NewValue`/`CreatedDate` (Day-1
  `CloseDate` reconstruction), `OpportunityHistory.StageName`/`CreatedDate`
  (open-on-Day-1 check), `Opportunity.StageName` (Step 6's current-stage read,
  no history needed). These are the confirmed API names on Paper's Opportunity
  object.
- **`Amount` is the dollar field** for the dollar-based rate — not a custom
  ARR/TCV field — unless the user specifies otherwise.
- **`Amount` can be null, not just zero.** At least one live record has a null
  `Amount`. Coerce null to 0 explicitly in any dollar math, or sums will throw.
  Note that SOQL `SUM()` silently skips nulls while `COUNT()` includes those
  rows, so a segment's dollar figure can be based on fewer opps than its count
  implies.
- **Watch for one specific recurring distortion:** a bulk-created block of ~14
  GROW Rebuy opps (all stamped `CreatedDate` 2026-01-16T12:54:28, almost all at
  exactly $40,000) contains one $4.54M outlier that single-handedly drove
  GROW–Renewal/Rebuy's Q2 2026 dollar rate down to 18.1% (vs. ~64% with it
  excluded, and vs. a 60% count rate). If this same bulk-block reappears in a
  future quarter's cohort (check for opps sharing that `CreatedDate` and
  product/type), flag it the same way rather than trusting the dollar rate at
  face value.
- **Close dates move backward as well as forward.** Some opps have a Day-1
  `CloseDate` later than their final one (pulled in, not pushed out). The
  Held/Slipped test already handles this correctly — Held only requires closing
  *somewhere* inside the quarter — but don't assume a `CloseDate` change means
  a push when reading the history.
- **Same-day `CloseDate` churn is common.** Individual opps have had the field
  flipped back and forth several times within minutes (e.g. toggling between
  two dates four times in six minutes, likely an integration or bulk-edit
  loop). This is harmless for Day-1 reconstruction, since only the earliest
  post-Day-1 row matters. It would badly distort any future "number of pushes
  per deal" metric, so don't build one on raw row counts without deduplicating.
- **Don't assume a clean filesystem.** A prior run's files may already be
  present, and `create_file` fails outright on an existing path. Write into a
  run-scoped directory (e.g. a timestamped or per-run subfolder) rather than a
  fixed filename, and never overwrite files you didn't create in this session.
  This applies concretely to the Step 8 audit CSVs, and to any scratch file you
  write mid-run.
- **Pressure-test surprising results.** If a rate looks implausible (e.g. 0% or
  100%, or a segment with far fewer deals than expected), double-check the
  filters before presenting, and flag the anomaly to the user rather than
  reporting it flatly. A useful concrete check: among a segment's slipped opps,
  split *still open* from *closed late*. A segment dominated by still-open
  deals with close dates pushed into later quarters points to optimistic close
  dates at creation, whereas one dominated by closed-late deals points to
  genuine end-of-quarter slippage — a materially different diagnosis, and worth
  stating.
## Open items to fill in

Two things this skill can't decide on its own. Resolving them makes future runs
both faster and more useful.

- **A plausible range for the headline rate.** Two data points so far, and **they
  disagree by roughly 2x** — which is itself the most important thing recorded
  here.

  **Q2 2026** (GROW + On-Demand, NBEX + Renewal/Rebuy combined): 62.3% count /
  39.1% dollar, cohort 146. By segment: GROW–NBEX 69.7% count / 72.4% dollar
  (n=89, well-populated); GROW–Renewal/Rebuy 60.0% count / 18.1% dollar (n=25,
  dollar figure misleading — see the bulk-block note above); On-Demand–NBEX
  0.0%/0.0% (n=6, too small to mean anything); On-Demand–Renewal/Rebuy 53.8% count
  / 35.2% dollar (n=26).

  **Q1 2026** (GROW–NBEX only, run 2026-07-29): **36.2% count / 31.5% dollar**,
  cohort 69 of 123 candidates. Slipped 25 — 19 closed late, 6 still open. Held 44,
  of which only 4 were won. The dollar figure was depressed by one $1M held opp at
  22.5% of cohort dollars; excluding it, 40.6% dollar against 36.8% count, which
  was the more representative read. Three of the 19 closed-late slips moved by only
  a single day, so the defensible count range was ~32–36% depending on whether
  one-day boundary slips count. Stage-at-push-out concentrated at Demo (8) and
  Proposal (9), with nothing slipping from Planning. Other segments were not run
  for this quarter.

  **So: GROW–NBEX has come in at 69.7% and 36.2% in consecutive quarters.** Do not
  present either number, or their midpoint, as a benchmark or target — a band this
  wide on two observations means the metric's quarter-to-quarter variance is
  currently unknown, and a target set from it would be arbitrary. If someone asks
  "is our slip rate good," the honest answer is that we don't yet have enough
  history to say, and the useful move is to compare within a quarter (across
  segments, stages, or owners) rather than against a cross-quarter number. Record
  future quarters here so a real band can eventually emerge, and note the scope
  and any outlier adjustments alongside each figure — the bare percentage is not
  interpretable without them.
- **An optional third breakdown dimension.** Product × motion answers *what* is
  slipping but not *who* or *where*, which is what makes a slip rate
  actionable. If Paper has an owner, team, region, or segment field on
  `Opportunity` worth grouping by, name the exact API field here and add it as
  an optional breakdown the user can request. Leaving this blank is fine — but
  don't guess at a field name at runtime.