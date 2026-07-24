#!/usr/bin/env python3
"""
fill_msa.py — Fill Paper's MSA template from raw Salesforce Opportunity +
Account field data, following the rules in generate-msa/SKILL.md.
 
Usage:
    python3 fill_msa.py --template TEMPLATE.docx --output OUTPUT.docx \
        --config config.json [--workdir DIR] [--keep-workdir]
 
config.json:
{
  "opportunity": { ...raw fields from `SELECT FIELDS(ALL) FROM Opportunity...` },
  "account": { ...raw fields from `SELECT FIELDS(ALL) FROM Account...` },
  "decision_maker": { ...raw fields from `SELECT FIELDS(ALL) FROM Contact...`
                       for the Contact pointed to by Opportunity.
                       Decision_Maker__c }, // optional — omit or pass {} if
                       // the opportunity has no Decision_Maker__c; the
                       // Decision_Maker__r.* tags fall back to blank
  "audit_level": "low" | "medium" | "high" | "chat_only",
                                      // chat_only: same comment content as
                                      // "medium" (all Low + Medium level
                                      // notes), but NONE of it is written
                                      // into the docx as Word comments —
                                      // it's returned instead (see
                                      // build_and_apply's second return
                                      // value) for Claude to relay in the
                                      // chat after the docx is generated.
  "od_included_free": true | false,  // optional — only matters for GROW/
                                      // GROW AI opps; controls the
                                      // OD_Only_Or_Free section
  "site_names": ["Site A", "Site B"],  // optional — fills {{site_names}}
  "extra_comments": [                // optional — YOUR judgment-call flags
    {"tag": "Opportunity.Amount", "occurrence": 1, "text": "...",
     "level": "low" | "medium"}      // optional per-entry, defaults to
                                      // "medium" if omitted
  ]
}
 
`extra_comments` is how Claude adds judgment-call flags each run (the
engine can't guess those) — `occurrence` is 1-based per tag literal
(without braces), matching document order. Entries default to Medium
level (dropped whenever audit_level is low) unless they set
"level": "low" (e.g. the Decision Maker Title discrepancy note — see
SKILL.md), in which case they survive even at the default Low audit
level.
 
What this script figures out on its own from opportunity/account fields:
  - Account.Name, Account.BillingAddress (+ flags a mismatch if the
    account's billing country isn't "United States", since the template
    hardcodes ", United States" as static text right after that tag)
  - Subscription_Start_Date__c / Subscription_End_Date__c (passed through
    as-is; Salesforce already returns YYYY-MM-DD)
  - Grade_Levels__c reformatted per the Grades rule ("3;4;5;6;9" -> "3-6, 9")
  - Seats_Sold__c (also compensates for a doubled-prefix Opportunity.
    Opportunity.Seats_Sold__c typo tag, but only if that tag is actually
    found in the template being processed)
  - $9 USD x Seats_Sold__c and Amount + Professional_Services_Amount__c
    formulas, and Amount / Professional_Services_Amount__c on their own,
    all formatted per the Currency rule (only shows cents if non-zero)
  - OD_Only vs GROW_Only, from Product_Type__c
  - OD_Only_Or_Free, from Product_Type__c plus the user-supplied
    `od_included_free` flag (only relevant for GROW/GROW AI opps; defaults
    to deleted if the flag isn't given)
  - Consumption vs Unlimited, from Pricing_Model__c ("Consumption" keeps
    Consumption/deletes Unlimited; anything else, including blank, does the
    reverse). Handled via the same generic paragraph-level mechanism as
    OD_Only/GROW_Only (see Phase 1) -- NOT yet verified against a real doc
    containing these tags, so watch for undiscovered quirks (untagged
    mirror rows, etc.) the way OD_Only/GROW_Only turned out to have.
  - OD_Live vs OD_AI, from ODAI__c
  - Virtual_PS / Onsite_PS, from Professional_Services_Purchased__c
  - {{site_names}}, from the user-supplied `site_names` list, joined with
    an Oxford comma
  - Account.Notification_Email_Address__c, straight from the Account record
  - Opportunity.Decision_Maker__r.{FirstName,LastName,Title,Phone,Email},
    from the user-supplied `decision_maker` Contact record. These same
    tags populate BOTH the Primary Contact and Emergency Contact fields in
    the template (it has no separate emergency-contact field on the
    Opportunity) — that overlap is expected, not a discrepancy to flag.
    NOTE: the script trusts `decision_maker.Title` as given -- it does NOT
    itself compare Title against Job_Function_Drop__c or free-text fields
    like Next_Step_Details__c. That comparison is a judgment call Claude
    makes before writing config.json (see "Decision Maker Title" in
    SKILL.md): a high-confidence corrected title should already be baked
    into `decision_maker.Title` by that point, and a low-confidence
    discrepancy should arrive as a `"level": "low"` extra_comments entry.
  - Two GROW-only blocks that are NOT wrapped in a GROW_Only tag (a known
    template gap, confirmed by rendering the doc): the GROW pricing row in
    the Order and Fees Summary table, and the "GROW Live + AI Schedule"
    Statement of Work appendix at the end of the doc. Removed whenever
    GROW_Only is deleted, via the same mechanism generalized in reverse
    for the OD side (see FEE_SUMMARY_ROW_RULES below).
 
KNOWN GAP: the GROW_Program_Start/End multi-program duplication (rendering
a separate block per program when Opportunity.of_Programs__c > 1) is NOT
automated -- a single aggregated block is rendered instead, flagged with a
Low-level note. GROW_Live vs GROW_AI selection IS automated, driven
directly by Product_Type__c ("GROW" -> Live, "GROW AI" -> AI).
 
Verified end-to-end against both a real On-Demand opportunity and a real
GROW opportunity (run against the actual source Google Doc, not a
plaintext export of it -- see "Known template quirks" in SKILL.md). If
Product_Type__c indicates GROW, still double-check the rendered GROW
section by hand for multi-program cases, and see the "GROW Programs" note
in SKILL.md.
"""
 
import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
 
sys.path.insert(0, str(Path(__file__).parent))
from docx_tag_engine import (  # noqa: E402
    TagEngineError, read_document_xml, write_document_xml, run_merge_runs,
    find_tag_occurrences, find_text_occurrences, paragraph_bounds,
    row_bounds, contained_in, na_paragraph, apply_ops, strip_highlight,
    normalize_bookmarks, CommentAdder,
    paragraph_has_other_content as _paragraph_has_other_content,
    strip_marker_or_line,
)
 
DOCX_SKILL_SCRIPTS = Path("/mnt/skills/public/docx/scripts")
 
# Section Start/End tag pairs known to exist in this template.
SECTION_NAMES = [
    "OD_Only", "GROW_Only", "OD_Only_Or_Free", "OD_Live", "OD_AI",
    "GROW_Live", "GROW_AI", "Virtual_PS", "Onsite_PS",
    "Consumption", "Unlimited",
]
 
# When the *_Only section for a product line is deleted, the matching
# product's SECOND occurrence of its Live/AI pair (the one that lives in
# its own row in the "Order and Fees Summary" table, outside the *_Only
# wrapper) must also be removed as a whole table row. Verified for OD;
# implemented symmetrically for GROW by structural mirroring, not yet
# independently verified against a real GROW deal.
FEE_SUMMARY_ROW_RULES = [
    {"trigger_section": "OD_Only", "row_tags": ["OD_Live", "OD_AI"], "occurrence": 2},
    {"trigger_section": "GROW_Only", "row_tags": ["GROW_Live", "GROW_AI"], "occurrence": 2},
]
 
GROW_SCHEDULE_ANCHOR = "GROW Live + AI Schedule"
GROW_SCHEDULE_END_MARKER = (
    '</w:tbl><w:p w:rsidR="00000000" w:rsidDel="00000000" w:rsidP="00000000" '
    'w:rsidRDefault="00000000" w:rsidRPr="00000000" w14:paraId="000001AC">'
)
 
 
# ---------------------------------------------------------------------------
# Formatters (skill's own formatting rules)
# ---------------------------------------------------------------------------
 
def fmt_currency(amount, currency):
    if amount is None:
        return None
    cents = round(float(amount) * 100)
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    s = f"{sign}${whole:,}"
    if frac:
        s += f".{frac:02d}"
    return f"{s} {currency}"
 
 
def fmt_grades(raw):
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    numeric = sorted({int(p) for p in parts if p.lstrip("-").isdigit()})
    nonnumeric = [p for p in parts if not p.lstrip("-").isdigit()]
    ranges, start, prev = [], None, None
    for n in numeric:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            ranges.append((start, prev))
            start = prev = n
    if start is not None:
        ranges.append((start, prev))
    out = [str(a) if a == b else f"{a}-{b}" for (a, b) in ranges]
    out.extend(nonnumeric)
    return ", ".join(out)
 
 
def fmt_site_names(names):
    names = [n.strip() for n in (names or []) if n and n.strip()]
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"
 
 
def fmt_billing_address(account):
    addr = account.get("BillingAddress") or {}
    street = account.get("BillingStreet") or addr.get("street")
    city = account.get("BillingCity") or addr.get("city")
    state = (account.get("BillingStateCode") or account.get("BillingState")
             or addr.get("stateCode") or addr.get("state"))
    postal = account.get("BillingPostalCode") or addr.get("postalCode")
    country = account.get("BillingCountry") or addr.get("country")
    line2 = ", ".join(x for x in [city, " ".join(x for x in [state, postal] if x)] if x)
    formatted = ", ".join(x for x in [street, line2, country] if x)
    return formatted, country
 
 
# ---------------------------------------------------------------------------
# Business rules: raw Salesforce fields -> {fields, sections, notes}
# ---------------------------------------------------------------------------
 
def derive_config(opportunity: dict, account: dict, od_included_free=None, site_names=None,
                   decision_maker=None):
    currency = opportunity.get("CurrencyIsoCode", "USD")
    notes = []  # (level:'low'|'medium', tag_or_None, text) for auto-comments
 
    fields = {}
 
    fields["Account.Name"] = account.get("Name")
    fields["Account.Notification_Email_Address__c"] = account.get("Notification_Email_Address__c")
 
    # Decision Maker contact fields -- these tags populate BOTH the Primary
    # Contact and Emergency Contact rows in the template (it has no
    # separate emergency-contact field on the Opportunity), so it's normal
    # and expected for both to show the same person. decision_maker may be
    # None/{} if the opportunity has no Decision_Maker__c -- fields.get()
    # on an empty dict just falls back to blank per the usual Low-level rule.
    dm = decision_maker or {}
    fields["Opportunity.Decision_Maker__r.FirstName"] = dm.get("FirstName")
    fields["Opportunity.Decision_Maker__r.LastName"] = dm.get("LastName")
    fields["Opportunity.Decision_Maker__r.Title"] = dm.get("Title")
    fields["Opportunity.Decision_Maker__r.Phone"] = dm.get("Phone")
    fields["Opportunity.Decision_Maker__r.Email"] = dm.get("Email")
 
    addr, country = fmt_billing_address(account)
    fields["Account.BillingAddress"] = addr
    if country and country != "United States":
        notes.append(("medium", "tag", ("Account.BillingAddress", 1),
                      f'The template\'s static text right after this field reads '
                      f'"...{country}, United States" — the account\'s billing '
                      f'country is actually {country}, so that line likely needs '
                      f'manual correction before sending.'))
 
    fields["Opportunity.Subscription_Start_Date__c"] = opportunity.get("Subscription_Start_Date__c")
    fields["Opportunity.Subscription_End_Date__c"] = opportunity.get("Subscription_End_Date__c")
 
    grades_raw = opportunity.get("Grade_Levels__c")
    fields["Opportunity.Grade_Levels__c"] = fmt_grades(grades_raw)
 
    seats = opportunity.get("Seats_Sold__c")
    fields["Opportunity.Seats_Sold__c"] = str(int(seats)) if seats is not None else None
    # NOTE: an earlier version of the template had a doubled-prefix typo tag
    # ({{Opportunity.Opportunity.Seats_Sold__c}}). Whether to compensate for
    # it and flag a note depends on whether *this* template actually still
    # has that tag — this function never sees the template, so that check
    # happens in build_and_apply() instead, once the real document is loaded.
 
    amount = opportunity.get("Amount")
    ps_amount = opportunity.get("Professional_Services_Amount__c") or 0
    fields["Opportunity.Amount"] = fmt_currency(amount, currency)
    fields["Opportunity.Professional_Services_Amount__c"] = fmt_currency(ps_amount, currency)
    if seats is not None:
        fields["$9 USD x Opportunity.Seats_Sold__c"] = fmt_currency(9 * seats, currency)
    if amount is not None:
        # NB: the template's actual tag subtracts PS from Amount (it's
        # backing PS out of the base program fee) -- fields dict keys must
        # match the tag literal exactly, so this has to be "-", not "+".
        fields["Opportunity.Amount - Opportunity.Professional_Services_Amount__c"] = \
            fmt_currency(amount - ps_amount, currency)
 
    fields["site_names"] = fmt_site_names(site_names)
 
    fields["Opportunity.Program_Start_Date__c"] = opportunity.get("Program_Start_Date__c")
    fields["Opportunity.Delivery_Window_End__c"] = (
        opportunity.get("Delivery_Window_End__c") or opportunity.get("Subscription_End_Date__c")
    )
 
    # --- GROW Program Details fields (Order Form GROW block + the
    # Statement of Work appendix) -- these were entirely unmapped before,
    # so every one of these tags fell through to the file unresolved on
    # any real GROW opportunity.
    fields["Opportunity.Program_Grade_Levels__c"] = fmt_grades(opportunity.get("Program_Grade_Levels__c"))
    students_in_program = opportunity.get("Students_in_Program__c")
    fields["Opportunity.Students_in_Program__c"] = (
        str(int(students_in_program)) if students_in_program is not None else None
    )
    of_programs = opportunity.get("of_Programs__c")
    fields["Opportunity.of_Programs__c"] = str(int(of_programs)) if of_programs is not None else None
    subjects_raw = opportunity.get("Subjects__c")
    fields["Opportunity.Subjects__c"] = (
        ", ".join(p.strip() for p in subjects_raw.split(";") if p.strip()) if subjects_raw else None
    )
    sessions_per_week = opportunity.get("Num_Sessions_per_Week__c")
    fields["Opportunity.Num_Sessions_per_Week__c"] = (
        str(int(sessions_per_week)) if sessions_per_week is not None else None
    )
    fields["Opportunity.Session_Length_Minutes__c"] = opportunity.get("Session_Length_Minutes__c")
    fields["Opportunity.Tutor_Student_Ratio__c"] = opportunity.get("Tutor_Student_Ratio__c")
    duration_weeks = opportunity.get("Program_Duration_weeks__c")
    fields["Opportunity.Program_Duration_weeks__c"] = (
        str(int(duration_weeks)) if duration_weeks is not None else None
    )
 
    # --- sections ---
    product_type = (opportunity.get("Product_Type__c") or "").strip()
    is_od = product_type in ("On-Demand", "On-Demand AI", "On-Demand + MC")
    is_grow = product_type in ("GROW", "GROW AI")
    sections = {
        "OD_Only": "keep" if is_od else "delete",
        "GROW_Only": "keep" if is_grow else "delete",
        "OD_Only_Or_Free": "keep" if (is_grow and od_included_free) else "delete",
    }
    if is_grow and od_included_free is None:
        notes.append(("low", "text", "General Service Information",
                      'Product_Type__c indicates a GROW deal but no answer was given '
                      'for whether On-Demand is included for free — defaulted to '
                      'deleting the OD_Only_Or_Free section. Confirm with the user '
                      'and re-run if that\'s wrong.'))
    if not is_od and not is_grow:
        notes.append(("low", "text", "General Service Information",
                      f'Opportunity.Product_Type__c = "{product_type}" did not match '
                      f'a known On-Demand or GROW value — defaulted to removing both '
                      f'the OD_Only and GROW_Only sections. Review the Order Form '
                      f'carefully; it may be entirely blank.'))
    if is_grow:
        notes.append(("low", "text", "General Service Information",
                      'Product_Type__c indicates a GROW deal. Multi-program '
                      'duplication (Opportunity.of_Programs__c > 1) is not automated by '
                      'this script -- it renders one aggregated GROW_Program block '
                      'rather than a separate block per program. Fill in per-program '
                      'detail by hand if the programs actually differ, and double-check '
                      'the rendered Order Form section.'))
 
    pricing_model = (opportunity.get("Pricing_Model__c") or "").strip()
    is_consumption = pricing_model == "Consumption"
    sections["Consumption"] = "keep" if is_consumption else "delete"
    sections["Unlimited"] = "delete" if is_consumption else "keep"
    if not pricing_model:
        notes.append(("low", "text", "General Service Information",
                      'Opportunity.Pricing_Model__c is blank -- defaulted to '
                      'non-Consumption behavior (kept the Unlimited section, '
                      'deleted the Consumption section). Confirm with the user '
                      'and re-run if that\'s wrong.'))
    notes.append(("high", "text", "General Service Information", (
        f'Section logic: Opportunity.Pricing_Model__c = "{pricing_model}" -> '
        f'{"kept" if is_consumption else "removed"} the Consumption section and '
        f'{"kept" if not is_consumption else "removed"} the Unlimited section.'
    )))
 
    od_ai = bool(opportunity.get("ODAI__c"))
    sections["OD_Live"] = "delete" if od_ai else "keep"
    sections["OD_AI"] = "keep" if od_ai else "delete"
 
    is_grow_ai = product_type == "GROW AI"
    sections["GROW_Live"] = "delete" if is_grow_ai else "keep"
    sections["GROW_AI"] = "keep" if is_grow_ai else "delete"
 
    notes.append(("high", "text", "General Service Information", (
        f'Section logic: Opportunity.Product_Type__c = "{product_type}" -> '
        f'{"kept" if is_od else "removed"} the OD_Only section and '
        f'{"kept" if is_grow else "removed"} the GROW_Only section. '
        + (f'Opportunity.ODAI__c = {od_ai} -> '
           f'{"kept OD_AI, removed OD_Live" if od_ai else "kept OD_Live, removed OD_AI"}. '
           if is_od else '')
        + (f'Product_Type__c = "{product_type}" -> '
           f'{"kept GROW_AI, removed GROW_Live" if is_grow_ai else "kept GROW_Live, removed GROW_AI"}. '
           if is_grow else '')
        + f'Opportunity.Professional_Services_Purchased__c = '
          f'"{opportunity.get("Professional_Services_Purchased__c")}" drove the '
          f'Virtual_PS/Onsite_PS decisions below.'
    )))
 
    ps_purchased = opportunity.get("Professional_Services_Purchased__c")
    if ps_purchased == "Asynchronous Support":
        sections["Virtual_PS"] = "keep"
        sections["Onsite_PS"] = "delete"
    elif ps_purchased == "Onsite Support":
        sections["Virtual_PS"] = "delete"
        sections["Onsite_PS"] = "keep"
    elif not ps_purchased:
        sections["Virtual_PS"] = "delete"
        sections["Onsite_PS"] = "delete"
    else:
        sections["Virtual_PS"] = "keep"
        sections["Onsite_PS"] = "keep"
        notes.append(("low", "text", "General Service Information",
                      f'Opportunity.Professional_Services_Purchased__c = '
                      f'"{ps_purchased}" did not match a known value — defaulted to '
                      f'keeping both the Virtual_PS and Onsite_PS sections. Review.'))
 
    return {"fields": fields, "sections": sections, "notes": notes}
 
 
# ---------------------------------------------------------------------------
# Engine driver
# ---------------------------------------------------------------------------
 
def build_and_apply(doc: str, config: dict, comments: CommentAdder):
    """Returns (doc, chat_report).
 
    chat_report is only ever non-empty when audit_level == "chat_only": it's
    a list of comment strings (same content that "medium" would embed as
    Word comments) for the caller to print/relay in the chat instead. In
    every other mode chat_report is [] and all comments go into the docx
    as before.
    """
    # Must happen before any tag-position work: stripping highlight tags
    # changes doc length, which would silently invalidate every position
    # computed afterward (including comment anchors) if done later.
    doc = strip_highlight(doc)
 
    fields = config["fields"]
    sections = config["sections"]
    # True when this is a GROW/GROW AI opp (as opposed to On-Demand)  --
    # used below to special-case the "On Demand Live Help Services" table,
    # which represents OD-bundled-free-with-GROW via GROW_Only's own
    # "$0.00 per student"/"N/A" text rather than the paid OD_Live/OD_AI/
    # Virtual_PS/Onsite_PS placeholders.
    is_grow = sections.get("GROW_Only") == "keep"
    audit_level = config.get("audit_level", "low")
    # chat_only selects exactly the same comment CONTENT as medium (all Low +
    # Medium notes, no High-only per-field log) — it only differs in where
    # that content ends up, which is handled at the very end of this function.
    content_level = "medium" if audit_level == "chat_only" else audit_level
    extra_comments = config.get("extra_comments", [])
    auto_notes = config.get("_notes", []) if content_level != "low" else []
 
    tagidx = find_tag_occurrences(doc)
 
    # Compensate for a known doubled-prefix typo tag
    # ({{Opportunity.Opportunity.Seats_Sold__c}}) from an earlier version of
    # the template, but only if it's actually present here — otherwise this
    # produces a false "typo" note against a template that's already fixed.
    typo_tag = "{{Opportunity.Opportunity.Seats_Sold__c}}"
    if typo_tag in tagidx and fields.get("Opportunity.Seats_Sold__c") is not None:
        fields["Opportunity.Opportunity.Seats_Sold__c"] = fields["Opportunity.Seats_Sold__c"]
        if content_level != "low":
            auto_notes = auto_notes + [
                ("low", "tag", ("Opportunity.Opportunity.Seats_Sold__c", 1),
                 'The template tag here has a typo ("Opportunity.Opportunity.'
                 'Seats_Sold__c") — resolved using Seats_Sold__c.')
            ]
 
    def occ(name_tag_literal, n):
        lst = tagidx.get(name_tag_literal, [])
        if n < 1 or n > len(lst):
            return None
        return lst[n - 1]
 
    def section_span(name, n):
        s = occ(f"{{{{{name}_Start}}}}", n)
        e = occ(f"{{{{{name}_End}}}}", n)
        if s is None or e is None:
            return None
        return (s[0], e[1])
 
    ops = []          # (start, end, replacement, op_id)
    big_ranges = []    # structural deletions, for containment exclusion
    op_counter = [0]
 
    def new_id():
        op_counter[0] += 1
        return op_counter[0]
 
    comment_plan = []  # (op_id, text) to attach after apply_ops, using final_spans
 
    # --- Phase 1: *_Only sections plus Consumption/Unlimited (paragraph-level,
    # unless the paragraph also holds other content -- see
    # _paragraph_has_other_content). Loops over every occurrence: OD_Only/
    # GROW_Only each appear multiple times in the real template (e.g. Grade
    # Levels row, Total Student Accounts row, Platform Fees row) -- not just
    # once. Consumption/Unlimited are handled the same way on the assumption
    # they follow the same mutually-exclusive, possibly-multi-occurrence
    # pattern -- not yet independently verified against a real doc.
    for name in ("OD_Only", "GROW_Only", "Consumption", "Unlimited"):
        n_occ = len(tagidx.get(f"{{{{{name}_Start}}}}", []))
        for n in range(1, n_occ + 1):
            span = section_span(name, n)
            if span is None or contained_in(span, big_ranges):
                continue
            action = sections.get(name, "keep")
            if action == "delete":
                ps, pe = paragraph_bounds(doc, span[0], span[1])
                if _paragraph_has_other_content(doc, ps, pe, span):
                    # A sibling section (e.g. the other of OD_Only/
                    # GROW_Only) shares this paragraph as an alternate
                    # value for the same row -- only remove this
                    # section's own tags+content, not the whole
                    # paragraph, or we'd delete the sibling too.
                    ops.append((span[0], span[1], "", None))
                    big_ranges.append((span[0], span[1]))
                else:
                    ops.append((ps, pe, "", None))
                    big_ranges.append((ps, pe))
            else:
                s = occ(f"{{{{{name}_Start}}}}", n)
                e = occ(f"{{{{{name}_End}}}}", n)
                ss, se = strip_marker_or_line(doc, s)
                es, ee = strip_marker_or_line(doc, e)
                ops.append((ss, se, "", None))
                ops.append((es, ee, "", None))
 
    # --- Phase 1b: OD_Only_Or_Free (multi-paragraph/table span) ---
    # This one was computed in `sections` but never actually applied
    # anywhere -- its tags fell straight through to the output on every
    # run. Unlike Phase 1's *_Only pairs, this wrapper deliberately spans
    # many paragraphs and a whole table, so on delete we remove exactly
    # the marked span (not a paragraph-widened range) rather than trying
    # to guess a bigger boundary.
    name = "OD_Only_Or_Free"
    for n in range(1, len(tagidx.get(f"{{{{{name}_Start}}}}", [])) + 1):
        span = section_span(name, n)
        if span is None or contained_in(span, big_ranges):
            continue
        action = sections.get(name, "keep")
        if action == "delete":
            ops.append((span[0], span[1], "", None))
            big_ranges.append((span[0], span[1]))
        else:
            s = occ(f"{{{{{name}_Start}}}}", n)
            e = occ(f"{{{{{name}_End}}}}", n)
            ss, se = strip_marker_or_line(doc, s)
            es, ee = strip_marker_or_line(doc, e)
            ops.append((ss, se, "", None))
            ops.append((es, ee, "", None))
 
    # --- Phase 1c: static, untagged "Platform Fee" cell text ---
    # Not wrapped in any tag at all -- the template just lists both the
    # paid per-student rate and "N/A" as literal alternatives for a human
    # to pick between. On a GROW deal the Fee Summary always shows $0.00
    # for Platform Fees, so remove the paid-rate wording (and the "or"
    # paragraph right after it) and leave "N/A" standing alone, matching
    # that -- there's no tag here to drive this any other way.
    if is_grow:
        rate_matches = find_text_occurrences(
            doc, "$9.00 USD per student, per year ($_______ USD per annum)")
        if rate_matches:
            rs, re_ = rate_matches[0]
            if not contained_in((rs, re_), big_ranges):
                ps1, pe1 = paragraph_bounds(doc, rs, re_)
                del_end = pe1
                next_p_end_idx = doc.find("</w:p>", pe1)
                if next_p_end_idx != -1:
                    candidate_end = next_p_end_idx + len("</w:p>")
                    visible = re.sub(r"<[^>]+>", "", doc[pe1:candidate_end]).strip()
                    if visible == "or":
                        # The very next paragraph is just the bold "or"
                        # separator -- take it too, or we'd leave a stray
                        # "or" line sitting above "N/A".
                        del_end = candidate_end
                ops.append((ps1, del_end, "", None))
                big_ranges.append((ps1, del_end))
 
    # --- Phase 2: mirrored Fee-Summary product row removal ---
    for rule in FEE_SUMMARY_ROW_RULES:
        if sections.get(rule["trigger_section"]) != "delete":
            continue
        starts, ends = [], []
        for tag in rule["row_tags"]:
            s = occ(f"{{{{{tag}_Start}}}}", rule["occurrence"])
            e = occ(f"{{{{{tag}_End}}}}", rule["occurrence"])
            if s and e:
                starts.append(s[0])
                ends.append(e[1])
        if not starts:
            continue  # template doesn't have a second occurrence; nothing to do
        rs, re_ = row_bounds(doc, min(starts), max(ends))
        ops.append((rs, re_, "", None))
        big_ranges.append((rs, re_))
        # Anchor to "TOTAL Due on signing" — it's the row immediately after
        # both product rows in the Fee Summary table, so it survives either
        # way and sits right next to whichever row just got removed.
        matches = [m for m in find_text_occurrences(doc, "TOTAL Due on signing")
                   if not contained_in(m, big_ranges)]
        if matches and content_level != "low":
            cid = new_id()
            ops.append((matches[0][0], matches[0][0], "", cid))
            comment_plan.append((cid, (
                f'Judgment call: the {rule["trigger_section"].split("_")[0]} pricing '
                f'row that normally appears near here was removed because it is not '
                f'wrapped in a {rule["trigger_section"]} tag, unlike the main Order '
                f'Form section for that product. Removed since '
                f'{rule["trigger_section"]} = delete.'
            )))
 
    # --- Phase 3: GROW schedule appendix (untagged, anchor-text based) ---
    if sections.get("GROW_Only") == "delete":
        matches = find_text_occurrences(doc, GROW_SCHEDULE_ANCHOR)
        if matches:
            start = matches[0][0]
            ps = max(doc.rfind("<w:p ", 0, start), doc.rfind("<w:p>", 0, start))
            end_idx = doc.find(GROW_SCHEDULE_END_MARKER)
            if end_idx != -1:
                pe = end_idx + len("</w:tbl>")
                ops.append((ps, pe, "", None))
                big_ranges.append((ps, pe))
                if content_level != "low":
                    signoff = [m for m in find_text_occurrences(
                        doc, "I have authority to bind the District.")
                        if not contained_in(m, big_ranges)]
                    if signoff:
                        cid = new_id()
                        ops.append((signoff[0][0], signoff[0][0], "", cid))
                        comment_plan.append((cid, (
                            'Judgment call: removed the "GROW Live + AI Schedule" '
                            'Statement of Work appendix that followed this point, '
                            'since it is GROW-only content and GROW_Only = delete. '
                            'It is not wrapped in a GROW_Only tag, so this was a '
                            'judgment call — worth a quick review.'
                        )))
 
    # --- Phase 4: remaining section pairs (OD_Live/OD_AI/GROW_Live/GROW_AI/PS) ---
    ps_pair_done = set()
    for name in ("OD_Live", "OD_AI", "GROW_Live", "GROW_AI"):
        n_occ = len(tagidx.get(f"{{{{{name}_Start}}}}", []))
        for n in range(1, n_occ + 1):
            span = section_span(name, n)
            if span is None or contained_in(span, big_ranges):
                continue
            action = sections.get(name, "keep")
            if name in ("OD_Live", "OD_AI") and n == 1 and is_grow:
                # This occurrence is the "Unlimited Live Help and Review
                # Centre" row inside the OD_Only_Or_Free-wrapped table.
                # On a GROW deal that row's job is to show GROW_Only's own
                # "$0.00 per student" text -- the paid OD_Live/OD_AI
                # placeholder price doesn't apply here regardless of
                # Opportunity.ODAI__c, which only governs the *separate*,
                # actually-paid On-Demand product.
                action = "delete"
            s = occ(f"{{{{{name}_Start}}}}", n)
            e = occ(f"{{{{{name}_End}}}}", n)
            if action == "delete":
                ps, pe = paragraph_bounds(doc, s[0], e[1])
                if _paragraph_has_other_content(doc, ps, pe, span):
                    # Same story as Phase 1: e.g. the GROW Program Details
                    # cell has the Live and AI blocks back-to-back in one
                    # paragraph -- deleting GROW_AI must not take GROW_Live
                    # (or vice versa) down with it.
                    ops.append((span[0], span[1], "", None))
                    big_ranges.append((span[0], span[1]))
                else:
                    ops.append((ps, pe, "", None))
                    big_ranges.append((ps, pe))
            else:
                ss, se = strip_marker_or_line(doc, s)
                es, ee = strip_marker_or_line(doc, e)
                ops.append((ss, se, "", None))
                ops.append((es, ee, "", None))
 
    # --- Phase 4b: GROW_Program (per-program detail block) ---
    # Multi-program duplication itself isn't automated (see the "GROW
    # deal" note emitted below) -- this just strips the Start/End tags so
    # a single aggregated block renders cleanly instead of leaking literal
    # {{GROW_Program_Start}}/{{GROW_Program_End}} tags. Must run after
    # Phase 4 (not alongside Phase 1) so contained_in correctly skips the
    # occurrence inside whichever of GROW_Live/GROW_AI just got deleted.
    for n in range(1, len(tagidx.get("{{GROW_Program_Start}}", [])) + 1):
        span = section_span("GROW_Program", n)
        if span is None or contained_in(span, big_ranges):
            continue
        s = occ("{{GROW_Program_Start}}", n)
        e = occ("{{GROW_Program_End}}", n)
        ss, se = strip_marker_or_line(doc, s)
        es, ee = strip_marker_or_line(doc, e)
        ops.append((ss, se, "", None))
        ops.append((es, ee, "", None))
 
    for n in range(1, len(tagidx.get("{{Virtual_PS_Start}}", [])) + 1):
        key = ("PS", n)
        if key in ps_pair_done:
            continue
        v_span = section_span("Virtual_PS", n)
        o_span = section_span("Onsite_PS", n)
        v_delete = sections.get("Virtual_PS") == "delete"
        o_delete = sections.get("Onsite_PS") == "delete"
        if n == 1 and is_grow:
            # Occurrence 1 is the "Live Help Add-On Professional Services"
            # row inside the OD_Only_Or_Free-wrapped table. On a GROW deal
            # that row's job is to show GROW_Only's own "N/A" text -- force
            # both Virtual_PS and Onsite_PS to delete here regardless of
            # Professional_Services_Purchased__c, which only governs the
            # *separate* GROW Add-On Professional Services row.
            v_delete = True
            o_delete = True
        v_live = v_span is not None and not contained_in(v_span, big_ranges)
        o_live = o_span is not None and not contained_in(o_span, big_ranges)
        if v_live and o_live and v_delete and o_delete:
            combined_span = (v_span[0], o_span[1])
            ps_, pe_ = paragraph_bounds(doc, combined_span[0], combined_span[1])
            if _paragraph_has_other_content(doc, ps_, pe_, combined_span):
                # Something else (e.g. GROW_Only's own "N/A") shares this
                # paragraph -- just remove the PS span, don't synthesize a
                # whole new "N/A" paragraph on top of surviving content.
                ops.append((combined_span[0], combined_span[1], "", None))
                big_ranges.append(combined_span)
            else:
                ops.append((ps_, pe_, na_paragraph(), None))
                big_ranges.append((ps_, pe_))
        else:
            if v_live:
                if v_delete:
                    s, e = occ("{{Virtual_PS_Start}}", n), occ("{{Virtual_PS_End}}", n)
                    ps_, pe_ = paragraph_bounds(doc, s[0], e[1])
                    if _paragraph_has_other_content(doc, ps_, pe_, (s[0], e[1])):
                        ops.append((s[0], e[1], "", None))
                        big_ranges.append((s[0], e[1]))
                    else:
                        ops.append((ps_, pe_, "", None))
                        big_ranges.append((ps_, pe_))
                else:
                    s, e = occ("{{Virtual_PS_Start}}", n), occ("{{Virtual_PS_End}}", n)
                    ss, se = strip_marker_or_line(doc, s)
                    es, ee = strip_marker_or_line(doc, e)
                    ops.append((ss, se, "", None))
                    ops.append((es, ee, "", None))
            if o_live:
                if o_delete:
                    s, e = occ("{{Onsite_PS_Start}}", n), occ("{{Onsite_PS_End}}", n)
                    ps_, pe_ = paragraph_bounds(doc, s[0], e[1])
                    if _paragraph_has_other_content(doc, ps_, pe_, (s[0], e[1])):
                        ops.append((s[0], e[1], "", None))
                        big_ranges.append((s[0], e[1]))
                    else:
                        ops.append((ps_, pe_, "", None))
                        big_ranges.append((ps_, pe_))
                else:
                    s, e = occ("{{Onsite_PS_Start}}", n), occ("{{Onsite_PS_End}}", n)
                    ss, se = strip_marker_or_line(doc, s)
                    es, ee = strip_marker_or_line(doc, e)
                    ops.append((ss, se, "", None))
                    ops.append((es, ee, "", None))
        ps_pair_done.add(key)
 
    # --- Phase 5: field substitutions ---
    # field_op_ids[(tag_literal, occurrence)] = op_id, for every substitution
    # that actually happened (i.e. wasn't swallowed by a bigger deletion) —
    # this is the single source of truth Phases 6/7 anchor comments against.
    field_op_ids = {}
    for tag_literal, value in fields.items():
        wrapped = f"{{{{{tag_literal}}}}}"
        n_occ = len(tagidx.get(wrapped, []))
        for n in range(1, n_occ + 1):
            s, e = occ(wrapped, n)
            if contained_in((s, e), big_ranges):
                continue
            cid = new_id()
            field_op_ids[(tag_literal, n)] = cid
            if value is None or value == "":
                # NB: must be XML-escaped (&lt;/&gt;, not literal </>) since this
                # string is inserted straight into document.xml -- literal angle
                # brackets are parsed as a bogus <blank> element and corrupt the
                # docx. Word still renders the escaped form as the literal text
                # "<blank>" on screen.
                ops.append((s, e, "&lt;blank&gt;", cid))
                comment_plan.append((cid, f'Field: {tag_literal} is blank.'))
            else:
                replacement = str(value)
                ops.append((s, e, replacement, cid))
                if audit_level == "high":
                    comment_plan.append((cid, f'Field: {tag_literal} = "{value}"'))
 
    # --- Phase 6: auto-notes from derive_config (mismatch/product-type flags) ---
    # notes are (level, anchor_mode, anchor_key, text):
    #   anchor_mode == "tag"  -> anchor_key is (tag_literal, occurrence);
    #                            anchors to that field's own substitution.
    #   anchor_mode == "text" -> anchor_key is literal surviving text to
    #                            search for (first non-deleted match used).
    # Falls back to Account.Name occurrence 1 (always present) if the
    # requested anchor can't be found, so a flag is never silently dropped.
    for (level, anchor_mode, anchor_key, text) in auto_notes:
        if content_level == "low" and level != "low":
            continue
        oid = None
        if anchor_mode == "tag" and anchor_key:
            oid = field_op_ids.get(anchor_key)
        elif anchor_mode == "text" and anchor_key:
            matches = [m for m in find_text_occurrences(doc, anchor_key)
                       if not contained_in(m, big_ranges)]
            if matches:
                oid = new_id()
                ops.append((matches[0][0], matches[0][0], "", oid))
        if oid is None:
            oid = field_op_ids.get(("Account.Name", 1))
        if oid is not None:
            comment_plan.append((oid, text))
 
    # --- Phase 7: extra ad hoc comments supplied by the caller this run ---
    for extra in extra_comments:
        extra_level = extra.get("level", "medium")
        if content_level == "low" and extra_level != "low":
            continue
        oid = field_op_ids.get((extra["tag"], extra.get("occurrence", 1)))
        if oid is not None:
            comment_plan.append((oid, extra["text"]))
        else:
            print(f"WARNING: could not anchor extra comment for tag "
                  f"{extra['tag']!r} occurrence {extra.get('occurrence', 1)} "
                  f"(no matching substitution) — comment skipped: {extra['text']!r}",
                  file=sys.stderr)
 
    doc, final_spans = apply_ops(doc, ops)
 
    chat_report = []
    for (oid, text) in comment_plan:
        span = final_spans.get(oid)
        if span is None:
            continue
        if audit_level == "chat_only":
            # Nothing gets written to the docx in this mode — not even the
            # comments.xml plumbing gets touched. Just collect the text for
            # the caller to relay in the chat.
            chat_report.append(text)
        else:
            # Positions in final_spans are already correct post-edit, so
            # it's safe to insert each comment's markers in any order here.
            doc = comments.add(doc, span, text)
 
    if audit_level != "chat_only":
        doc = comments.flush_markers(doc)
    return doc, chat_report
 
 
# ---------------------------------------------------------------------------
# CLI plumbing (unzip, run, rezip, validate)
# ---------------------------------------------------------------------------
 
def unzip_docx(docx_path: Path, unpacked_dir: Path):
    unpacked_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(docx_path) as zf:
        zf.extractall(unpacked_dir)
    for p in unpacked_dir.rglob("*"):
        if p.is_symlink():
            p.unlink()
 
 
def rezip_docx(unpacked_dir: Path, out_path: Path):
    if out_path.exists():
        out_path.unlink()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(unpacked_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(unpacked_dir))
 
 
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--keep-workdir", action="store_true")
    args = ap.parse_args()
 
    config_data = json.loads(Path(args.config).read_text())
    derived = derive_config(
        config_data.get("opportunity", {}),
        config_data.get("account", {}),
        od_included_free=config_data.get("od_included_free"),
        site_names=config_data.get("site_names"),
        decision_maker=config_data.get("decision_maker"),
    )
    config = {
        "fields": derived["fields"],
        "sections": derived["sections"],
        "audit_level": config_data.get("audit_level", "low"),
        "extra_comments": config_data.get("extra_comments", []),
        "_notes": derived["notes"],
    }
 
    workdir = Path(args.workdir) if args.workdir else Path.cwd() / "_fill_msa_work"
    if workdir.exists():
        shutil.rmtree(workdir)
    unpacked = workdir / "unpacked"
    unzip_docx(Path(args.template), unpacked)
    run_merge_runs(unpacked, DOCX_SKILL_SCRIPTS)
 
    doc = read_document_xml(unpacked)
    # Normalize duplicate bookmark ids/names inherited from the Google Doc
    # export (copy-pasted headings keep their anchor ID) so the validator's
    # ID-uniqueness check passes and real regressions stay visible.
    doc = normalize_bookmarks(doc)
    comments = CommentAdder(unpacked, DOCX_SKILL_SCRIPTS)
    doc, chat_report = build_and_apply(doc, config, comments)
    write_document_xml(unpacked, doc)
 
    rezip_docx(unpacked, Path(args.output))
 
    result = subprocess.run(
        ["python3", str(DOCX_SKILL_SCRIPTS / "office" / "validate.py"),
         str(args.output), "--original", args.template],
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
 
    if not args.keep_workdir:
        shutil.rmtree(workdir)
 
    print(f"Wrote {args.output}")
    if derived["notes"]:
        print("\nNotes surfaced during generation:")
        for (level, _anchor_mode, _anchor_key, text) in derived["notes"]:
            print(f"  [{level}] {text}")
 
    if config["audit_level"] == "chat_only":
        print(f"\nchat_only mode: no comments were written to {args.output}.")
        if chat_report:
            print("Relay the following Low/Medium-level comments to the user "
                  "in the chat (do not add them to the docx):")
            for i, text in enumerate(chat_report, 1):
                print(f"  {i}. {text}")
        else:
            print("(No Low/Medium-level comments were generated this run.)")
 
 
if __name__ == "__main__":
    main()
