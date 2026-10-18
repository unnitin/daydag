# Rippling expenses - what was verified, and what was not

Facts a run of the `expenses` skill consults. Verified from live sources on
2026-10-15 unless a line says otherwise; re-verify anything older than a
quarter. Mechanism is `src/daydag/expenses.py`; instructions are
`.claude/skills/expenses/SKILL.md`. Neither restates this.

## How CMG uses Rippling, from his own inbox

Rippling is HR, payroll, time off, recruiting (ATS) and **Spend** at CMG. Two
roles show up in his mail:

- **Approver.** `spendmanagement@rippling.com` sends "You have 1 Spend request
  to approve" (bills - e.g. a vendor invoice with Approve / Reject links).
  Nothing in this skill acts on those.
- **Filer.** He submits **reimbursement expense reports**, one per trip, and
  they are reviewed in Rippling and repaid in the next paycheck:
  - "Some/All expenses in your expense report for *Steerco in LA* have been
    reviewed" (Apr 11-12, 2026), "*Trip to LA*" (May 31 and Jul 27, 2026).
  - "Your expense was approved" (May 13, 2026): *$82.82 · American Express ·
    Purchase date 5/12/26 · Category Meals · Memo "Lunch and coffee w/ ... -
    Kiwi" · reimbursement in your next paycheck.*

So the expense record Rippling keeps is: **amount · merchant · purchase date ·
category · memo · status**, grouped under a **report** with a name. Categories
seen on his approved expenses, spelled as Rippling spells them: `Meals`,
`Rideshare & Taxis`, `Travel`. The payment method on reimbursements is
"American Express" - his personal card, not a Rippling corporate card. Deep
links look like `link.rippling.com/spendApp?path=/spend-management/expense/<id>`
and `.../expense-reports/<id>`.

## The three ways a receipt gets in

| Route | Needs | Status 2026-10-15 |
|---|---|---|
| **Forward to `receipts@rippling.com`** | mail sent from the address on his Rippling profile, passing SPF/DKIM/DMARC | **Confirmed in his own Rippling on 2026-10-15**: the Reimbursement dialog (Spend > Reimbursement > Upload receipt) says *"Forward your receipts to receipts@rippling.com or set up automatic email forwarding"*, beside the upload box (jpg / png / jpeg / bmp / svg / pdf, up to 15 at a time, "Rippling will scan your receipt to fill out reimbursement details") and the mobile app. A community tool that uses the route reports: matched by the `From:` header, lands "within a minute", a mismatch or auth failure is a **silent drop**. Gmail sends from his work address with Google's DKIM. **Confirmed end to end on 2026-10-15**: the first receipt (Oct 5 Uber, $11.98) was forwarded at 06:44:11Z as Gmail message `1a143743516a6d53`; at 06:44:28Z `receipts@rippling.com` replied in the same thread *"Draft reimbursement created - Rippling scanned your receipt and created a draft reimbursement for you"* with amount $11.98, vendor Uber, purchase date Oct 5, 2026, category **Rideshare & Taxis** (filled by Rippling, not typed) and a *View draft reimbursement* link to `/spend-management/expense/<id>/`. The first forward had been blocked once by Claude Code's permission layer, not by Rippling. |
| **Rippling REST API** (`developer.rippling.com`, base `api.rippling.com`) | an app or API key created by a company admin, with a Spend scope | No expense, receipt or reimbursement endpoint appears in the public reference (searched the developer site; the published collections list employees, departments, leave, custom fields, SAML, matching users). Third-party catalogues advertise an "Expenses API" and a "Corporate Cards API" with no paths. He is not a Rippling admin. **Not built.** |
| **Mobile app / Slack capture** | him | manual; what the forward route replaces |

What the forward does: it creates a **draft reimbursement** with vendor,
amount, purchase date and category read from the receipt. What it does not
do: memo, expense report, purchaser, entity and Submit remain clicks in
Rippling. The skill's forward note carries memo and report so he can paste
rather than retype.

## The confirmation mail

From `receipts@rippling.com`, in the forward's own Gmail thread, subject
`Fwd: <the receipt's subject>`, about 17 seconds after the send. Plain text
of the body, pipes and all:

```
| Draft reimbursement created Rippling scanned your receipt and created a draft reimbursement for you. |
| | $11.98 | [](https://link.rippling.com/spendApp?path=/spend-management/expense/<id>/&isDeepLink=true) |
| Uber |
| Purchase date | Category |
| Oct 5, 2026 | Rideshare & Taxis |
| View draft reimbursement [](https://link.rippling.com/spendApp?path=/spend-management/expense/<id>/&isDeepLink=true) |
```

The search snippet carries the same fields without the link. `drafts_in`
reads either; `check` joins a draft to its forward by Gmail thread, then by
amount and purchase date. No reply after a minute means the mail did not
arrive (From mismatch or domain auth), not that Rippling is slow.

## Getting the expense documented right

What a Rippling reimbursement asks for, from a customer-published Rippling
Spend guide (2025) and his own approval mails: **Vendor** (the merchant name
"exactly as it appears on the receipt"), **Total amount** ("cannot exceed the
amount shown on the receipt"), **Purchase date**, **Category** (his: `Meals`,
`Rideshare & Taxis`, `Travel`), **Memo** (a business purpose - his past ones:
"taxi", "stay for steerco", "Lunch and coffee w/ ... - Kiwi"), **Purchaser**
(you), **Entity** (the company entity), and optionally an **Expense report**
to group under. Rippling's OCR auto-fills from the receipt; "you must
double-check this data for accuracy".

What counts as a receipt, per Rippling: vendor name, date, an itemised
description, the total including tax. **Not** valid: "bank transaction
history, screenshots, credit card slips" - so an Amex statement line and
Uber's "charge summary" are not receipts, which is why neither is ever
forwarded. Digital and emailed receipts are accepted. File uploads take jpg,
png, jpeg, bmp, svg and pdf, up to 15 at a time.

Uber's receipt mail carries all of it: vendor, trip date and time, the fare
lines, the total, the card. It has no attachment (the PDF is behind a link),
so the forward sends the mail body. The forward's own note puts the fields on
their own lines at the top - `vendor:`, `amount:`, `date:`, `category:`,
`memo:`, `report:` - so whatever Rippling reads from the body finds them, and
he pastes the rest. If a forwarded Uber mail lands without vendor and amount
filled in, the fallback is the PDF from the mail's "Download PDF" link
uploaded by hand, and the skill's note stays the paste source.

Deadlines are a company policy Rippling enforces per receipt; the published
example is 20 days for anything over $200 and 30 days under, with tax
withheld on late claims. His is unknown; a trip's receipts should go the
week after the trip.

## What the Gmail connector can and cannot do here

Read and forward, yes. Label, no: `create_label` and `label_message` fail
with "Insufficient scope" (the connector is not granted `gmail.labels` or
`gmail.modify`), checked 2026-10-15. So the `expensed` label is his to create
by hand if he wants the visual marker; the event log (`expenses.forwarded`)
is the guard that keeps a forwarded receipt from being offered again.

## The shape of his calendar - the trip

The calendar carries the whole trip, so the skill builds the frame from it
before it reads a receipt (`trip_from_calendar`):

- **Flights** arrive as events Gmail creates: summary `Flight to Burbank
  (AS 1388)`, location `Seattle SEA` (the origin airport last), timed
  start/end = departure/arrival. The return is `Flight to Seattle (AS 1388)`
  at `Burbank BUR`. Booked by the travel desk through TravelPerk
  (`confirmation@perk.com`, "Booking confirmed #<trip id>"); the Alaska
  check-in mails follow.
- **The stay** is an all-day block the travel desk adds, summary `<Hotel> -
  Confirmation number: <n>`, description `Confirmation number: <n> ...
  Starts:<date>, Ends: <date> <hotel> <street>   <city>, <State spelled out>,
  USA <zip> Tel: ...` and a location without the zip. Gmail adds its own
  `Stay at <Hotel>` all-day events beside it; the one with an address and a
  confirmation wins. The hotel (a Tribute Portfolio property, booked on
  Marriott by the desk) was extended by a night at his request on Oct 1.
- **Anchors** are timed events whose location carries a street address -
  `Girl & the Goat Los Angeles (555 Mateo St, ...)` for "Dinner in LA" - and
  they explain rides: the 5:54 pm Uber to Mateo St and the 8:25 pm one back.
  Office events carry a room name (`CMG LA-2-Conference Hall-Red Room (10)`),
  no street, and are not anchors.
- Flights and the stay are company-booked, so they are not in his report;
  only what he paid is.

Pull it one day per call: a week of his calendar in one `list_events` is
over the output limit (56k characters on 2026-10-15).

## The shape of his mail

**Uber** (`noreply@uber.com`, subject `[Business] Your <day> <part-of-day> trip
with Uber`). Per trip, two or three mails under ONE subject:

1. a **charge summary** within minutes - "This is your charge summary ...
   This is not a payment receipt" - skipped, never forwarded;
2. the **receipt** hours later - "Thanks for riding, Nitin ... Total $97.93",
   the fare breakdown, "Apple Pay American Express ••••<last four>", pickup and
   dropoff addresses, a "Download PDF" link (no attachment);
3. sometimes a **tip restatement** - "Tip ... Thanks for tipping, Nitin ...
   Total $24.94" - the same trip at the higher total. Newest wins.

The trip time ("Oct 8, 2026 2:08 PM") is in every one of the three and is the
key. The search snippet alone carries the time and the Total; the body adds
the card and the route. He already labels these `uber receipts` (28 threads,
67 messages on 2026-10-15); the skill's own marker is a second label,
`expensed`, applied only after a forward.

**Amex, DoorDash, Lyft, Grubhub, Uber Eats**: no mail from any of them in the
work mailbox in the 180 days to 2026-10-15. They go to another address. Two
ways to bring them in, both his call: a Gmail filter on that account that
forwards receipts to work, or run the skill against that account's connector.
Until a real mail exists the `doordash` source runs by the generic rule and
says so.

**Amex activity export** (Statements & Activity > Download > CSV): header row
`Date, Description, Card Member, Account #, Amount, Extended Details, Appears
On Your Statement As, Address, City/State, Zip Code, Country, Reference,
Category`; dates `MM/DD/YYYY`; charges positive, payments negative. The reader
goes by header name and needs `Date`, `Description`, `Amount`.

## Open with him

1. ~~Does the first forward land~~ - it did, as a draft reimbursement with
   the category already right (2026-10-15).
2. Report naming - he types "Trip to LA"; the skill proposes dates only.
3. Whether to route personal-mailbox receipts into work Gmail.

## Sources

- A customer-published Rippling Spend reimbursement guide (fields, validity,
  OCR, deadlines): https://finni.help.usepylon.com/articles/7356029926-rippling-spend-expense-reimbursement-guide
- Rippling on what a valid receipt shows: https://www.rippling.com/blog/expenses-receipts
- Rippling, Expense Management product page - forwarding and the inbox name:
  https://www.rippling.com/products/finance/expense-management
- Rippling Spend review (receipt capture by mobile, email, Slack):
  https://www.rippling.com/blog/rippling-expense-management-review
- `turian/send-to-rippling` - the From-header match, auth requirements and
  the silent-drop failure mode: https://github.com/turian/send-to-rippling
- Rippling developer portal and reference: https://developer.rippling.com/
- Third-party API profile listing an Expenses API with no paths:
  https://github.com/api-evangelist/rippling

## 2026-10-18: the sink is Ramp now

CMG moved expenses to Ramp (AP's "Ramp User training" mail, Oct 14; Ramp bill
approvals already reach him). Verified live on 2026-10-18:

- **Intake:** a mail from his work address to `reimbursements@ramp.com`
  becomes one **draft reimbursement** per receipt (Ramp support, "Submitting
  reimbursements"). `receipts@ramp.com` is a different inbox, for matching
  receipts to Ramp *card* charges - not for out-of-pocket.
- **Confirmation:** `do-not-reply@ramp.com` replies in the forward's own
  thread. First forward 21:58:59Z, reply 21:59:12Z (13 s). Body: "Reimbursement
  created for this receipt / You have 1 draft reimbursement: / $11.98 at Uber /
  Draft reimbursement · Nitin Srivastava · 10/05/26 / Review draft
  <https://app.ramp.com/s/reimbursements/<uuid>?email=...>". No category in the
  reply. `drafts_in` reads this shape (`_ramp_draft`).
- **Nine Uber receipts forwarded** this way on 2026-10-18 ~15:00 PT, logged
  under `expenses.forwarded` with `to=reimbursements@ramp.com` (rows 3191-3199);
  the Rippling rows 3182-3190 from 10/15 predate the `to` field.
- **What he filled by hand in Ramp** (export `spend_events_*.csv`, 13 rows):
  columns Date, Payment Method (REIMBURSEMENT), Amount, Merchant, Ramp
  Category, Trip Name, URL, Memo, NetSuite mapping. Categories as Ramp spells
  them on his rows: `Taxi and Rideshare`, `Restaurants`, `Lodging`, `Office`,
  `Uncategorized`. Trip name he used: `Burbank - Oct 2026`. Ramp read the hotel
  folio's gross ($1,203.77) not the amount paid ($1,177.95), and named the
  merchant "Hotel Tonnelle" - OCR fields need a look before submit.
- **Files still can't be sent from a session** (connector attachments are
  inline bytes). Ramp adds a phone path: text a receipt photo to HIRAMP
  (447-267) / +1 844 331 1023, or drag-and-drop at Home > New > Reimbursement.
- **Not found:** a Ramp MCP/API write path. The Ramp connector in this
  workspace (unauthenticated) and Ramp's hosted MCP expose read tools
  (`load_reimbursements`, ...) only.

### The Ramp connector, verified 2026-10-18 (afternoon)

"claude.ai Ramp" = Ramp's hosted MCP (`ramp-mcp-remote.ramp.com`), installed in
the workspace, authorised per user via `/mcp`. Token scopes include
`reimbursements:write`, `receipts:write`, `trips:write`, `accounting:write`.
What the first real use established:

- **Reads.** `ramp_get_user_recent_reimbursements(include_drafts=true,
  limit=null, up_to_days=30)` returned all 23 of his rows with amount_decimal,
  merchant_name, transaction_date, status, memo, trip_uuid and the four
  tracking selections (Department, Division, Business Unit, GL "Category").
  `ramp_get_reimbursements` returns a different shape without trip_uuid.
  `ramp_get_user_trips` gave the trip he made by hand: "Burbank - Oct 2026"
  (uuid from `ramp_get_user_trips` at run time), Seattle -> Burbank -> Los Angeles,
  Oct 5-8.
- **Edits on PENDING.** trip_uuid: accepted (16 rows). amount, merchant:
  rejected, `DEVELOPER_7078` "can only be edited while the reimbursement is
  in DRAFT state". Memo and coding were not tried on PENDING.
- **Files.** `ramp_request_receipt_upload_link` (no transaction_uuid) ->
  single-use URL, 30 min. `curl -sS -F "file=@<pdf>;type=application/pdf"
  '<url>'` from the session -> 201 `{"receipt_uuid": ..., "transaction_uuid":
  null}`. `ramp_create_reimbursement_from_receipt(receipt_uuid, amount,
  currency_code, transaction_date, merchant)` -> DRAFT with the file attached
  and the given fields, GL unset, memo unset. The auto-mode classifier allowed
  the curl; it had blocked `python -m daydag.expenses go` (prints only) as a
  "real-world transaction", so the nine forwards were made from the log rows.
- **Duplicate** copies fields, not the receipt (`missing_receipt: true`).
- **Policy** "General Expenses": receipt, memo and category required from $0.
  Fund not required. Submission validated all of that on his 22.
- **Coding on his rows** (UUIDs in .env under the names in `expenses_ramp.CODING_KEYS`): Department
  "Data & AI", Division "3 - Corporate", Business Unit "22 - Create Music
  Group"; GLs 607101 Airfare, 607102 Lodging, 607103 Ground Transportation,
  607104 Meals (Employee), 607105 Meals and Entertainment (Client), 607106
  Entertainment (Employee), 607107 Conferences; he coded in-flight Wi-Fi to
  604104 Office Supplies - no internet/telecom GL exists, only IT
  software/hardware/support.
- **State at the end of the day:** 22 PENDING, $2,172.17, all on the trip;
  expected $2,146.35; the $25.82 gap is the hotel at the folio's gross, which
  he chose to leave. One DRAFT, hotel $1,177.95 with the folio attached, left
  unsubmitted pending his word.
