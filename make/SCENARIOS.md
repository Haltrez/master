# Make.com scenarios

Five scenarios. Build them by hand in Make.com, following these steps. You don't
need any Make.com experience. Each one takes 10 to 20 minutes.

**Before you start**

1. Sign in at <https://www.make.com>. In **Organization → Settings**, set the
   **time zone to Europe/London**. Scenario 3 is the one exception, set below.
2. You need two values from the server's `.env`:
   - `ENGINE_URL`: `https://<DOMAIN>/engine`, for example `https://v2202608395914497498.luckysrv.de/engine`
   - `ENGINE_API_SECRET`: the long random secret
3. In Make, create a **Data store** or a scenario variable for the secret. You
   can also paste it directly into the header field of each HTTP module. It's
   private to your Make account.
4. **Every HTTP module in every scenario** uses these settings:
   - Module: **HTTP → Make a request**
   - Headers: one item, name `X-API-Key`, value = your `ENGINE_API_SECRET`
   - **Parse response: Yes**
   - Timeout: 40 seconds

   The engine answers long jobs straight away (HTTP 202) and keeps working in
   the background, so Make never waits.
5. **Error handling, the same for every scenario.** Right-click the first HTTP
   module, then **Add error handler → Break**. After the Break, add **Gmail →
   Send an email** (or **Telegram Bot → Send a text message**) to yourself,
   with the subject `Repost Radar: <scenario name> failed` and the body
   `{{error.message}}`. Under scenario settings, turn on **Allow storing of
   incomplete executions**. You'll then get an email or Telegram message
   whenever a step fails.

---

## Client Google Sheet template (make once)

Create a Google Sheet called **Repost Radar – client template**. Give it two
tabs.

**Tab 1: `Hot leads`.** It's laid out like the owner's Excel. Row 1 headers, columns A–O:

| A | B | C | D | E | F | G | H | I | J | K | L | M | N | O |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Date added | Company | Role | Max salary | Times posted | First seen | Location | Ad link | Previous ad | Decision maker | Their role | Email | Suggested email | Status | Notes |

- Columns A–M are filled by Make. The client fills N and O.
- Select column N, then **Data → Data validation → Dropdown**, with the
  options New, Contacted, Meeting, Placement, Not relevant.
- **Email** is only filled when Hunter verified it *and* Companies House
  confirms a Ltd, LLP or PLC. Otherwise there's a name only, so the client
  phones or uses LinkedIn.
- **Suggested email** is ready to send from the client agency to the hiring
  company. It greets the decision maker by first name, or says "Hello," when
  there isn't one.
- Make column M wide, and turn on **Format → Wrapping → Wrap**.
- Freeze row 1 and make it bold.

**Tab 2: `Candidate matches`.** Row 1 headers, columns A–I:

| A | B | C | D | E | F | G | H | I |
|---|---|---|---|---|---|---|---|---|
| Candidate role | Company | Their open role | Location | Distance (miles) | Open since | Signal | Link | Status |

- Column I is a dropdown with the options New, Sent, Contacted, Interview,
  Placed, Not relevant.

Copy the template's ID from its URL, the long part between `/d/` and `/edit`.
Scenario 4 needs it.

---

## Scenario 1: Daily collect (every day, 06:00 UK)

Runs every saved search on Reed (and Adzuna if it's set up), then rebuilds
hot leads and agencies.

1. **Create a new scenario.** Click the clock on the first module, choose
   **Every day** at **06:00**.
2. Add **HTTP → Make a request**:
   - URL `{{ENGINE_URL}}/run-all`
   - Method **POST**
   - The `X-API-Key` header
3. Add the error handler (step 5 above).
4. **Save** and switch the scenario **ON**.

The reply looks like `{"run_ids":[12,13],"status":"running"}`. Collection
takes a few minutes in the background. Each run's result is at
`GET /runs/{id}` and on the CRM's Searches page.

> No Make.com? `docker compose --profile scheduler up -d` runs the same daily
> collection from the server itself.

---

## Scenario 2: Client delivery (weekdays, 08:00 UK)

Puts each paying or pilot client's new hot leads into their Google Sheet,
then emails them. A lead never goes to the same client twice.

1. **Schedule**: **Days of the week**, Monday to Friday, at **08:00**.
   Collection starts at 06:00 and takes minutes, so it's done by then.
2. **HTTP → Make a request**:
   - `GET {{ENGINE_URL}}/clients/active`, with the header
   - Parse response: Yes
3. **Flow Control → Iterator**. Array = the body of module 2 (the list of
   clients).
4. **HTTP → Make a request**:
   - `GET {{ENGINE_URL}}/clients/{{3.id}}/daily-leads?mark=true`, with the header
   - `mark=true` records the delivery, so tomorrow these leads won't come
     back.
5. **Flow Control → Iterator**. Array = `{{4.data.leads}}`.
6. **Google Sheets → Add a Row**. Connect your Google account the first time.
   - Spreadsheet ID: **Map** → `{{4.data.sheet_id}}`
   - Sheet name: `Hot leads`
   - Column range A–O. Fill:
     A `{{5.date_added}}`, B `{{5.company}}`, C `{{5.role}}`,
     D `{{5.max_salary}}`, E `{{5.times_posted}}`, F `{{5.first_seen}}`,
     G `{{5.location}}`, H `{{5.ad_link}}`, I `{{5.previous_ad_link}}`,
     J `{{5.decision_maker}}`, K `{{5.decision_maker_role}}`,
     L `{{5.email}}`, M `{{5.suggested_email}}`, N `New`, O empty.
7. **Tools → Numeric aggregator**:
   - Source module: the iterator in step 5
   - Function: COUNT
   - Group by `{{3.id}}`
8. Add a **Filter** on the link after the aggregator: `{{7.result}}`
   *Numeric: greater than* `0`. With no new leads, nothing is sent.
9. **Gmail → Send an email**:
   - To `{{3.delivery_email}}`
   - Subject `{{7.result}} new hot leads in your sheet today`
   - Body: `Morning! {{7.result}} companies have been reposting roles in
     your niche. They're in your sheet: {{3.sheet_url}}`
10. Add the error handler on the HTTP modules, then save and switch it ON.

**Check:** run it once by hand (**Run once**). Then run it again
straight away: the second run adds 0 rows. Leave it on for three days and
there are still no duplicates.

---

## Scenario 3: Owner's morning digest (weekdays 07:30 **Riga** time)

1. **Scenario settings** (the gear icon): set the **time zone** for this
   scenario to **Europe/Riga**. If your Make plan has no per-scenario time
   zone, schedule it at **05:30 Europe/London**, which is 07:30 in Riga
   (Riga is two hours ahead of London all year).
2. **Schedule**: Monday to Friday, 07:30.
3. **HTTP → Make a request**: `GET {{ENGINE_URL}}/digest/today`, with the
   header.
4. **Gmail → Send an email** to yourself, or **Telegram Bot → Send a text
   message** to your own chat:
   - Subject `Repost Radar – today`
   - Body `{{3.data.text}}`. The engine writes this text already formatted:
     yesterday's outreach against 100, new hot leads, the top 10 agencies to
     contact, and follow-ups due.
5. Error handler, save, ON.

---

## Scenario 4: New client onboarding (triggered by the CRM)

When you create a client in the CRM (for example by dragging an agency to
**Pilot**), the engine calls this webhook. Make copies the sheet template for
the client and saves the link back.

1. **Webhooks → Custom webhook → Add**. Name it `repost-radar new client`.
   **Copy the URL**.
2. In the CRM go to **Settings → Make.com webhook: New client onboarding**,
   paste the URL and save. Create a test client so Make learns the data
   shape: `client_id`, `agency`, `delivery_email`.
3. **Google Drive → Copy a File**:
   - File: your **client template** (pick it, or map its ID)
   - New name `{{1.agency}} – Hot Leads`
   - Folder: wherever you keep client sheets
4. **Sharing.** At first, open the new sheet and share it with the client's
   email yourself. To automate it later, add **Google Drive → Share a File /
   Folder**:
   - File ID `{{3.id}}`
   - Role: Writer
   - Email `{{1.delivery_email}}`
5. **HTTP → Make a request**:
   - `PATCH {{ENGINE_URL}}/clients/{{1.client_id}}/sheet`, with the header
   - Body type **Raw**, content type **JSON**
   - Request content
     `{"sheet_url": "https://docs.google.com/spreadsheets/d/{{3.id}}/edit"}`
6. Error handler, save, ON.

---

## Scenario 5: Candidate matches to Google Sheet (on demand, from the CRM)

The **Send to Google Sheet** button on an agency's Candidates tab triggers
this.

1. **Webhooks → Custom webhook**, named `repost-radar matches to sheet`.
   Copy the URL into **CRM → Settings → Make.com webhook: Candidate matches
   to Sheet**. Click the button once in the CRM so Make learns the fields:
   `agency_id`, `sheet_url`.
2. **Tools → Set variable**:
   - Name `sheet_id`
   - Value `{{get(split(1.sheet_url; "/"); 6)}}`. This is the ID part of the
     sheet link.
3. **HTTP → Make a request**:
   - `GET {{ENGINE_URL}}/agencies/{{1.agency_id}}/matches?status=new&min_score=4`,
     with the header
4. **Iterator** over the body of module 3.
5. **Google Sheets → Add a Row**:
   - Spreadsheet `{{2.sheet_id}}`
   - Sheet `Candidate matches`
   - Fill: A `{{4.brief_label}}`, B `{{4.company}}`, C `{{4.their_role}}`,
     D `{{4.location}}`, E `{{round(4.distance_miles)}}`,
     F `{{4.open_since_label}}`, G `{{4.signal}}`, H `{{4.link}}`,
     I `New`
6. **Array aggregator**: source = the iterator, field `id`.
7. **HTTP → Make a request**:
   - `POST {{ENGINE_URL}}/matches/status`, with the header
   - Raw JSON body `{"ids": {{map(6.array; "id")}}, "status": "sent"}`
   - This marks the matches as sent, so a second click doesn't add them again.
8. Error handler, save, ON.

---

## Checklist

- [ ] Organization time zone is Europe/London. Scenario 3 is set to Riga.
- [ ] Every HTTP module sends `X-API-Key`.
- [ ] Every scenario has a Break error handler that emails or Telegrams you.
- [ ] Scenario 2 run twice in a row adds no duplicate rows.
- [ ] Both webhook URLs are pasted into CRM → Settings.
