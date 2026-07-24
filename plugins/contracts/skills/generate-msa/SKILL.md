---
name: generate-msa
description: "Generate a Master Service Agreement using Paper's MSA template. Data is pulled from Salesforce."
---
 
# Trigger
Use this skill whenever someone mentions generating a contract, MSA, or something similar. This skill should also be run when the user types /generate-msa.
 
# Context
Sales reps use a template for a Master Service Agreement and "fill in the blanks" in order to customize the MSA before sending it to a customer for signing. This is time consuming and can lead to human error.
 
# Objective
The objective of this skill is to use AI to find the right information from the account and opportunity in Salesforce, make a copy of the MSA template, and populate the required information in the document.
 
# Process
Follow this process to create the customized MSA.
 
## 1. Copy the template
Use the Google Drive connector to search for and download the real Google Doc named "Paper MSA [Hackathon 2026]" (exporting it as `application/vnd.openxmlformats-officedocument.wordprocessingml.document`), and use that as your local Word doc copy. See "Known template quirks" below.
Use this naming convention for the copied file: "{{Account.Name}} - MSA".
The local Word doc file is what you will be updating throughout this skill.
 
## 2. Get the Salesforce record
If the user did not provide the salesforce ID for an opportunity, ask them for it. They can also provide the URL for the opportunity. Either way, use the Salesforce connector to get all of the opportunity's fields and their values.
 
Also get the Account record's fields and values (needed for Account.Name, Account.BillingAddress, and Account.Notification_Email_Address__c among others — use `SELECT FIELDS(ALL) FROM Account WHERE Id = '...'`, don't hand-pick a subset of fields, or tags referencing fields you skipped will be left unresolved).
 
The template also references the Opportunity's Decision Maker as a relationship (`{{Opportunity.Decision_Maker__r.FirstName}}`, `.LastName`, `.Title`, `.Phone`, `.Email`) for both the Primary Contact and Emergency Contact fields. The Salesforce connector doesn't traverse relationships automatically, so if `Opportunity.Decision_Maker__c` is populated, separately query the Contact record it points to (`SELECT FIELDS(ALL) FROM Contact WHERE Id = '...'`) to get those fields.
 
## 2b. Ask for site names
Regardless of product type, ask the user for the names of the schools included in the deal. This isn't in Salesforce, so it has to come from the user directly — ask alongside (or right after) the audit-requirements question in step 3. See the Site Names rule under Formatting for how to apply this.
 
## 2a. Use the automated script for steps 3–4 and highlighting
 
Steps 3 (replace tags) and 4 (add comments), plus the yellow-highlight removal in Formatting below, are automated in scripts/fill_msa.py (and its dependency scripts/docx_tag_engine.py) — this has been built and verified end-to-end against both a real On-Demand opportunity and a real GROW opportunity (run against the actual source Google Doc — see step 1), so prefer it over doing this by hand. GROW_Live vs GROW_AI selection is automated (driven by Product_Type__c); multi-program duplication for Opportunity.of_Programs__c > 1 is not (see the GROW Programs note below).
 
To use it:
 
1. Query the Opportunity with SELECT FIELDS(ALL) FROM Opportunity WHERE Id = '...' and the Account similarly, and get the raw JSON results. If Opportunity.Decision_Maker__c is populated, also query that Contact with SELECT FIELDS(ALL) FROM Contact WHERE Id = '...'.
2. Ask the user for their audit-comment granularity (see step 3 below), whether On-Demand is included for free (only matters for GROW/GROW AI opps — see the Products rule below), and the site names (see step 2b).
3. Write a config.json:
```json
   {
     "opportunity": { ...raw Opportunity fields... },
     "account": { ...raw Account fields... },
     "decision_maker": { ...raw Contact fields, from Opportunity.Decision_Maker__c... },
     "audit_level": "low" | "medium" | "high" | "chat_only",
     "od_included_free": true | false,
     "site_names": ["Site A", "Site B"],
     "extra_comments": [
       {"tag": "Opportunity.Amount", "occurrence": 1, "text": "your own judgment-call flag"},
       {"tag": "Opportunity.Decision_Maker__r.Title", "occurrence": 1, "text": "low-confidence title discrepancy note", "level": "low"}
     ]
   }
```
   `od_included_free` only affects the OD_Only_Or_Free section on GROW/GROW AI opps — the script ignores it otherwise. `site_names` populates the {{site_names}} tag (Oxford comma, see Formatting below). `decision_maker` populates the `{{Opportunity.Decision_Maker__r.*}}` tags used for both Primary Contact and Emergency Contact (see the Contacts note under Formatting below) — omit it (or pass `{}`) if the opportunity has no Decision_Maker__c, and the script will treat those tags as blank per the usual Low-level rule. If the Decision Maker Title rule under Formatting gives you a high-confidence corrected title, put that corrected value in `decision_maker.Title` directly — the script has no way to know the Contact record's Title was overridden, so this substitution has to happen before you write config.json.
 
   extra_comments is how you add judgment-call flags each run — the script only auto-generates the mechanical ones (blanks, formula errors, known template quirks, the field-substitution log at High). Each entry defaults to Medium-level ("this value looks off" style flags) and is dropped when audit_level is low. Set `"level": "low"` on an entry (as with the Decision Maker Title discrepancy above) to have it survive even at the default Low audit level — use this only for comment types the skill actually designates as Low (see step 3). Medium-level entries still require audit_level to be medium, high, or chat_only. (At chat_only, both levels are relayed in chat rather than written to the docx, same as every other non-High comment.)
4. Run:
 
   `python3 scripts/fill_msa.py --template TEMPLATE.docx --output OUTPUT.docx --config config.json`
 
Read the script's own stdout — it prints any notes it surfaced (blank fields, product-type mismatches, the template typo it resolved, etc.) and whether the validator found any new structural issues. The validator should report "All validations PASSED!" — duplicate bookmark IDs from the Google Doc export are auto-normalized at load, so any validation failure is a real regression worth investigating (see "Known template quirks" below).
 
Still render and visually spot-check the output before sending, especially for GROW deals (see the GROW note below) — the script handles the mechanics reliably, but it can't judge whether the resulting document reads correctly.
 
If the script errors out or a field/section rule doesn't apply cleanly to a given opportunity, fall back to doing that part by hand per the manual process below — don't force the script past a real error.
 
## 3. Ask about audit requirements
Tell the user that you can add comments to the output Word doc. There are four levels of granularity they can choose from: High, Medium, Low, Chat-only. The default is low.
 
Below is a list of types of comments you can make, and the minimum level of granularity required in order for you to add that type of comment.
 
- (High) Add a comment wherever you replaced a tag showing the exact field API name you used in the substitution.
- (Medium) If you don't think the Salesforce value makes sense (e.g. the subscription end date is earlier than the subscription start date), add a comment indicating this and what you recommend doing, if anything.
- (Low) If the field is blank, replace the tag with the text "<blank>" and add a comment saying the field API name and that the value is blank.
- (Low) If you couldn't find the data you needed in the Salesforce account or opportunity records, use other sources and your best judgement of what the value should be. Add a comment indicating how you came to that conclusion and what source you used, if any.
- (Low) If you can't calculate a tag formula, replace the tag with the text "<error>" and add a comment indicating why you can't calculate it.
- (Low) If the Decision Maker's Title looks inconsistent with Job_Function_Drop__c or a text field like Next_Step_Details__c, and you have low confidence in the correct title, add a comment stating the discrepancy and the title you'd recommend, with your reasoning. (If you have high confidence instead, use the corrected title directly per the Decision Maker Title rule under Formatting — no comment needed for that case.)
**Chat-only** is a special case: it includes exactly the same comments as Medium (every Low- and Medium-level comment, but none of the High-only per-field substitution log), but none of them are written into the docx. Instead, after the docx is generated, report all of those comments directly in the chat to the user, and leave the docx completely clean (no Word comments at all — don't add comment infrastructure to the file just to leave it empty). If using scripts/fill_msa.py, pass `"audit_level": "chat_only"` in config.json — the script itself collects this content and prints it under a "chat_only mode" heading at the end of its output for you to relay; don't add any of it to the file yourself.
 
## 3. Replace the tags
In the MSA, anything that appears in double braces ({{ or }}) is called a "tag". Tags are instructions or guidance for you to read. These tags should not appear in the final version of the file.
 
### Fields
If you see a tag containing a field name (e.g. {{Account.Name}}), replace the tag with the value of that field.
 
### Sections
A section begins with a tag ending with the word "Start" (e.g. {{NAME_Start}}). It should have a matching end tag: {{NAME_End}}. If you are told to delete the NAME section, delete everything in between the section's start and end tags, including the tags.
 
If you are keeping a section (or otherwise just removing the Start/End marker tags themselves, e.g. GROW_Program), and a Start or End tag is the only text on its line, delete that entire line rather than just the tag — otherwise you'll leave a blank line behind where the tag used to be. If the tag shares a line with other text (e.g. inline content or another tag), only remove the tag itself and leave the rest of the line alone. This is automated in scripts/fill_msa.py via `strip_marker_or_line()` in docx_tag_engine.py; apply the same rule if doing this step by hand.
 
### Formulas
A tag may contain a formula (e.g. {{9 x Opportunity.Seats_Sold__c}}). Replace the tag with the result of the formula.
 
## 4. Add comments
Add your comments to the file, per the user's audit requirements — unless the user chose Chat-only, in which case add none to the file and hold onto them to report in chat instead (see step 5).
 
## 5. Deliver the file and summarize the results
Present the finished docx directly to the user (e.g. via present_files) — do not upload it anywhere; the user can put it in Google Drive or wherever else themselves if they want to.
 
In your chat, provide a high-level summary of what happened: Was it successful? Was there data you couldn't find or that didn't make sense? Don't restate all of your comments, just provide a high-level overview.
 
- If audit level was High, Medium, or Low: encourage the user to look at your comments in the docx for more details, rather than restating them here.
- If audit level was Chat-only: this step is where those comments actually surface, since none of them are in the file. List every Low/Medium-level comment here (fill_msa.py prints them under a "chat_only mode" heading for exactly this purpose).
# Formatting
Do not change any formatting in the document, unless explicitly instructed to do so.
 
Remove all yellow highlighting.
 
## Dates
Write all dates using "YYYY-MM-DD" format.
 
## Products
If the opp's Product_Type__c field is On-Demand, On-Demand AI, or On-Demand + MC, delete the GROW_Only section.
 
If the opp's Product_Type__c field is GROW or GROW AI, delete the OD_Only section. Also ask the user whether On-Demand will be included for free as part of this deal. If they say yes, leave the OD_Only_Or_Free section in; if they say no (or it doesn't apply, e.g. the opp isn't GROW/GROW AI), delete the OD_Only_Or_Free section.
 
## Pricing Model
If the opp's Pricing_Model__c field is Consumption, delete the Unlimited section (keep the Consumption section).
 
If Pricing_Model__c is anything other than Consumption (including blank), delete the Consumption section (keep the Unlimited section).
 
## Grades
When using the Grade_Levels__c field, express it in a human-readable format. For example, if the value is "3;4;5;6;9", write it as "3-6, 9".
 
## Contacts
The template's Primary Contact and Emergency Contact fields both pull from the same `{{Opportunity.Decision_Maker__r.*}}` tags, since Salesforce doesn't have a separate emergency-contact field on the Opportunity. It's expected and fine for Primary Contact and Emergency Contact to show the same person — don't flag this as a Medium-level "value looks off" discrepancy.
 
## Decision Maker Title
Before filling in {{Opportunity.Decision_Maker__r.Title}}, sanity-check the Decision Maker Contact's Title field against:
- Job_Function_Drop__c (check whichever record it actually lives on — Opportunity or the Decision Maker's Contact)
- Any mention of the Decision Maker's role in free-text fields, such as Opportunity.Next_Step_Details__c
If these agree with (or are at least consistent with) the Title field, use the Title field as-is and move on.
 
If they suggest the Title field is wrong:
- **High confidence in the true title:** use that corrected title as the value instead of the Title field's value. No special comment is needed for this beyond the normal High-level substitution log, if that's the audit level in effect.
- **Low confidence in the true title:** keep the Title field's original value, but add a Low-level comment on the {{Opportunity.Decision_Maker__r.Title}} tag stating the discrepancy, the title you'd recommend instead, and your reasoning. This comment should be added even at the default Low audit level (see step 3 and the extra_comments `level` key in step 2a).
## Site Names
Whenever you see the tag {{site_names}}, replace it with a comma-separated list of the school names the user gave you in step 2b, using the Oxford comma. For example, three sites become "Site A, Site B, and Site C"; two sites become "Site A and Site B"; one site is just "Site A".
 
## Currency
Be sure to write the currency code after all dollar amounts. For example if CurrencyIsoCode is "USD", write "USD" after all dollar amounts (e.g. $100 USD). Put a dollar sign ($) in front of all dollar amounts. Use commas to separate thousands (e.g. $1,000 USD). Only display cents if the number of cents is not zero.
 
## Professional Services
If Professional_Services_Purchased__c is "Asynchronous Support", delete the Onsite_PS section. Note that this section appears multiple times in the document.
If Professional_Services_Purchased__c is "Onsite Support", delete the Virtual_PS section. Note that this section appears multiple times in the document.
If Professional_Services_Purchased__c is blank, delete the Onsite_PS and Virtual_PS sections and write "N/A".
 
## GROW Programs
A single GROW opp can include details for multiple GROW programs that will be run with different students at different schools for different subjects, etc. 
 
Use the fields on the opp as well as any related Program_Design__c records to determine if there are multiple programs being run, and if so, what their details are. Then, copy the GROW_Program section as many times as you need to and fill in each copy of the section with the relevant details. 
 
Please note that the Program_Design__c object won't exist until July 27, 2026, so don't worry if you can't find it before then.
 
## Delivery Window End
The Opportunity.Delivery_Window_End__c won't exist until July 27, 2026. Before then, whenever a tag uses that field, use Opportunity.Subscription_End_Date instead.
 
# Known template quirks
Confirmed by running scripts/fill_msa.py against the real source Google Doc ("Paper MSA [Hackathon 2026]" in Drive):
 
- **Duplicate bookmark ids/names from Google Docs export — now auto-normalized.** The source doc has copy-pasted headings (two "General Service Information" headings, one per Order Form variant); Google Docs keeps the same heading anchor ID on pasted copies, so the export contains bookmarks with identical names and ids. `normalize_bookmarks()` in docx_tag_engine.py (called at template load in fill_msa.py) renumbers/renames the duplicates, so the validator's ID-uniqueness check now passes cleanly. **A duplicate-ID warning from the validator is therefore a real regression, not expected noise.** The root fix in the source doc — deleting a duplicated heading and retyping it fresh — is still worthwhile but no longer required.
- **Missing `pgMar` `gutter` attribute.** Pre-existing in the pristine template; expected on every run; not a regression.
- **Untagged "$" cost blanks in the GROW Program Details block.** "Total costs, per GROW Live Educator Program: $" and "Total costs of all GROW Live Educator Services Programs: $" carry no tags, so the script leaves them blank. Fill by best judgment (single-program deals: both equal Total_GROW_Amount__c; sanity-check against Program_Price_Per_Student__c × Students_in_Program__c) and flag per the audit level, or add tags to the template.
- **GROW + On-Demand-not-included (`od_included_free: false`) deletes the OD_Only_Or_Free block with kept-section tags inside it.** apply_ops now prunes those redundant nested deletion ops automatically (they're semantic no-ops — the outer deletion removes them anyway), so this combination runs cleanly.