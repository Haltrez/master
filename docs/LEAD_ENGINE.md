# How the lead engine works

What we sell: a sheet of UK companies that keep re-advertising a role they can't fill, in the
agency's industry and area. Every row has to be something a specialist agency would happily
call. One wrong row costs more trust than ten missing ones, so every stage is strict.

## 1. Order

Agency + industry + town + number of leads (+ once/weekly, sheet/outreach). Industry + town is
exclusive to one agency. Each industry has a sharp definition in `engine/app/industries.py`:

- **search**: the job titles searched on every board
- **in / out**: what an agency in that industry places, and what it doesn't (for example Sales:
  B2B/B2C sales and marketing roles are in; shop-floor retail, store managers, optical assistants,
  customer service and care are out)

## 2. Collect (every morning, and when an order is made)

Every search term on Reed, Adzuna, Indeed (Apify), Careerjet and Jooble, within the radius. Agency
ads are separated (Reed's own flag, agency names, "our client" wording, known agency brands). The
ad text is kept, so the ad can be read later.

## 3. Find reposts

Ads are grouped by company + role + town. Then the engine counts **separate postings**, not ads:

- ads that opened within 7 days of each other are **one posting** (the same job copied to several
  towns or boards on the same day: AECOM's 10 copies count once)
- a later ad is a **new posting** when it opened 7+ days after the last posting started, or after
  every earlier ad had been gone for 2+ days

A role qualifies with **2+ separate postings**, or when Indeed itself marks it as a repost, or when
older copies are in the web archive (Totaljobs on the Wayback Machine). Its newest ad must have
been seen in the last 10 days, so the company is still hiring.

## 4. Claude Haiku reads every candidate ad

Haiku (the cheapest model) gets the full ad text and the industry's in/out definition, 8 ads per call
with strict JSON output, and answers yes/no to four questions:

| signal | yes means |
|---|---|
| industry_fit | the job itself is in the industry (not just mentioned) |
| direct_employer | the company advertising is the employer, not an agency, job board or outsourced team |
| real_vacancy | a real job: not a talent pool, franchise, commission-only, self-employed or course ad |
| agency_would_fill | an agency could get paid to fill it: not an apprenticeship, in-house grad scheme or mass seasonal hiring |

**Four yeses or it's out.** Haiku also returns the clean role name and a short reason. Each verdict
is cached per industry + ad, so an ad is read once (roughly £0.01-0.03 per 100 ads). For Reed, where
search results only carry a snippet, the full ad is fetched first. If Claude is unreachable, those
ads simply wait for the next run: nothing unchecked goes out.

## 5. Companies House

Only for leads that passed Haiku: an active UK company, matched by name, and not a recruitment
business (SIC 78xxx / 74500, which also marks it as an agency everywhere).

## 6. One row per company, best first

If a company passed with several roles, the row shows the best one (most postings) and lists the
others under "Also hiring". Companies this agency already had are never sent again.

## 7. Decision maker

Directors from Companies House (free), then a hiring/HR person or director email from Hunter, only
for rows going out, at most 2 Hunter calls each, within the monthly cap.

## 8. The row

Date added, Company, Role, Also hiring, Salary (per year / day / hour), Times posted, Posted on
(the dates of each posting), Location (postcodes shown as towns), Ad link, Previous ad (from an
earlier posting, never a same-day copy), **Why it's a lead** (the evidence + Haiku's reason),
Decision maker, Their role, Email (verified, Ltd/LLP/PLC only), Suggested email, Status.

The exact row is stored with the delivery, so the CSV always matches the sheet.

## Volume

Day one only has the evidence the boards and archive give (Indeed's repost flag, posting dates far
apart, archived ads). Every daily run adds history, so more reposts become visible each morning: an
order that starts at 6/20 fills over the following days. That is deliberate: no padding with weak rows.
