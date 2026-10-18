---
name: expenses
description: "File Nitin's receipts into Ramp (Rippling until 2026-10-18). Finds unfiled receipts in Gmail (Uber parsed against real mail; DoorDash and others by a generic Total-line rule), drops Uber's charge summaries and tip duplicates, lists them with permalinks, forwards each to Rippling's receipts inbox ONLY after he says go, labels and logs it so nothing is offered twice, and reconciles a trip against an Amex activity export. Use for \"file my receipts\", \"expense my uber rides\", \"what's not in rippling yet\", \"reconcile my amex\", \"expenses for the LA trip\". Do NOT use for approving other people's Spend requests (a Rippling task in his inbox, not a loop) or for anything that writes to the vault."
daydag:
  writes:
    # The marker on a forwarded mail, and its row in the event log. Nothing in
    # the vault: a receipt carries his home address and card last-4, and the
    # log row is marked private for the same reason.
    - gmail:label-expensed
    - local:event-log/expenses
  reads: [gmail]
  emits: []
  sensitivity: private
---

# expenses - receipts out of Gmail, into Ramp (Rippling until 2026-10-18), after his go

> **Since 2026-10-18 the sink is Ramp**: forwards go to `reimbursements@ramp.com`
> (`RECEIPTS_INBOX`), Ramp replies from `do-not-reply@ramp.com` within seconds
> with "Reimbursement created for this receipt", and `check` reads that reply.
> Everything below that says Rippling describes the same loop against the old
> sink; the mechanics did not change. `reference/rippling-expenses.md` has the
> 2026-10-18 section with what was verified on Ramp.

You are filing receipts for Nitin. He pays with a personal Amex, files one
Rippling Spend reimbursement report per trip ("Trip to LA", "Steerco in LA")
and is repaid through payroll. Rippling's intake that needs no admin is its
receipts inbox: a mail forwarded there from his address lands under Spend >
Receipts, where he completes category, memo and report. So the work is:
find what is not filed, drop the duplicates, show him the list, forward what
he names, and never offer it again.

**Read `reference/rippling-expenses.md` before the first run.** It holds what
was verified about Rippling on 2026-10-15, what was not, and the shape of his
mail. Do not re-derive it here. The mechanism is `src/daydag/expenses.py`;
run the loop through it, not by hand.

## The route is verified - and how to tell if it breaks

The forward path was verified end to end on 2026-10-15: nine receipts became
nine draft reimbursements, each answered by `receipts@rippling.com` in the
forward's own thread within a minute (`reference/rippling-expenses.md`). So
batches are not held to one receipt. The tell that it has broken is step 8:
a forward with no reply after a minute. Then stop sending, say so, and leave
the cause to him - a From-address mismatch or a domain auth failure, both
silent drops on Rippling's side.

## The run

0. **The trip first.** Receipts are picked against the trip, never the other
   way round. Pull the calendar **one day per call** across the dates he
   named (`mcp__claude_ai_Google_Calendar__list_events` with `startTime` and
   `endTime` on the same day, `pageSize` 50; a week in one call overflows).
   Save the answers as a json list in `calendar.json`, then:

   ```sh
   python -m daydag.expenses trip calendar.json --name "Trip to LA" --out trip.json
   ```

   It reads the `Flight to <City> (<AS 1388>)` events Gmail puts on his
   calendar (origin airport in the location), the hotel block the travel desk
   adds (name, address, confirmation, check-in and check-out from its
   description), and every timed event with a street address - the dinner
   that explains an evening ride. Show him the trip render and ask one thing:
   right? A wrong window is the one mistake every later step inherits. Flights
   and the stay the travel desk booked are not his to claim; the render says
   so. He names the report; the trip name is a proposal.

1. **Window.** From the trip, plus two days: Uber mails the receipt hours
   after the ride, so a last-day ride's receipt arrives the next morning.
   Placement still bounds by purchase date. Without a trip, the dates he
   named, else since the last forward in the log, else 30 days. Never wider
   than the cap; the plan refuses it.

   ```sh
   python -m daydag.expenses plan --trip trip.json
   ```

2. **Fetch.** One `mcp__claude_ai_Gmail__search_threads` per source with the
   query exactly as printed (`pageSize` 50, follow `nextPageToken`). Save each
   raw answer under its source key in `payloads.json`:
   `{"uber": <answer>, "doordash": <answer>}`. **With a trip, fetch every
   receipt's body** (`mcp__claude_ai_Gmail__get_message`, `PLAIN_TEXT`) and
   put it on the message as `plaintextBody`: the snippet has the time and the
   Total but no address, and a receipt with no place on it cannot be placed.
   Without a trip the snippet is enough unless he wants the route in the memo.

3. **Review.** Then paste the render **verbatim** into his DM or the terminal.

   ```sh
   python -m daydag.expenses review payloads.json --trip trip.json \
     --log ~/.local/state/daydag/events.db --report "Trip to LA" \
     --out <scratch dir>/batch.json
   ```

   `--out` is required and must point outside the repo (the scratchpad):
   the batch carries his home street and the card's last four, and the repo
   is public.

   Every row says where the trip put it: *at the destination*, *at the
   hotel*, *airport leg (SEA)*, *in CA*, or *where? ...* for a receipt in the
   window the trip cannot place, and a separate line for anything dated
   outside it. A stop that matches a calendar event that day names the event
   in the memo (`uber N Wilton Pl -> Mateo St (Dinner in LA)`). The report
   name is his: the one he said, else the trip's. Never invent a city.

4. **His go.** `go 1 2 3` or `go all`. Nothing else counts - not "looks good",
   not silence. `go all` is the rows placed inside the trip only. A row marked
   *where?* (in the window, nowhere the trip explains - a ride at home on a
   trip day, a DoorDash order with no address) and one *outside the trip*
   need their own number: that is him saying it belongs. A row marked
   *changed since filing* (a tip arrived after the forward) needs its own
   number too, because a second forward puts a second receipt in Rippling.

5. **Forward.** Run each call exactly as printed - `messageId`, `to`,
   `forwardText` - with `mcp__claude_ai_Gmail__forward`. Add nothing: no cc,
   no bcc, no other `to`. The From header is what Rippling files it under.

   ```sh
   python -m daydag.expenses go batch.json 1 2 3
   ```

6. **Label, when the connector can.** `mcp__claude_ai_Gmail__list_labels`;
   if `expensed` exists, `mcp__claude_ai_Gmail__label_message` on every
   forwarded message id AND every id in its `supersedes` list, so the next
   plan's `-label:expensed` hides the whole trip. **On 2026-10-15 the Gmail
   connector had no label-write scope** (`create_label` and `label_message`
   fail with "Insufficient scope"), so until that changes the log in step 7
   is the only guard and this step is one line: "couldn't label". He can make
   the `expensed` label by hand in Gmail; the plan already excludes it.

7. **Mark.** The idempotency check outside Gmail, and today the only one.
   It takes the numbers that were actually sent, never `all`: a row marked
   that was not sent hides itself from every later run.

   ```sh
   python -m daydag.expenses mark batch.json 1 2 3 --log ~/.local/state/daydag/events.db
   ```

8. **Confirm, from Rippling's own reply.** Within a minute of each forward
   `receipts@rippling.com` answers in the same Gmail thread: *"Draft
   reimbursement created - Rippling scanned your receipt and created a draft
   reimbursement for you"*, with the amount, vendor, purchase date, category
   and a *View draft reimbursement* link. Search
   `from:receipts@rippling.com newer_than:1d`, save the answer, and:

   ```sh
   python -m daydag.expenses check batch.json confirmations.json \
     --log ~/.local/state/daydag/events.db
   ```

   `--log` is what tells `check` which rows were forwarded; the batch file
   still says NEW for a row that was sent after it was written.

   Paste the render. A forwarded row with no reply is **unconfirmed**, said
   as such, never assumed to be in Rippling; wait a minute and search again
   before calling it a drop.

9. **Tell him** in one line: how many drafts Rippling staged, with the links.
   Each draft already carries vendor, amount, date and category from the
   receipt; what is left for him in Rippling is the memo (the forward's note
   has it), the expense report to group under, purchaser and entity, and
   Submit. Nothing here submits, and nothing here claims it did.
   `reference/rippling-expenses.md` has the field list and what Rippling
   accepts as proof.

## Ramp, through the connector (since 2026-10-18)

The "claude.ai Ramp" connector is installed in his workspace; `/mcp` ->
"claude.ai Ramp" -> authenticate, once per machine. It is Ramp's own hosted
server with write scopes: reimbursements, receipts, trips. Mechanics are in
`src/daydag/expenses_ramp.py`; what was verified is in
`reference/rippling-expenses.md`, 2026-10-18 section. Three things it changes:

**1. Files can be filed from here.** Photographed slips, a hotel folio, the
statement-line PDFs: `ramp_request_receipt_upload_link` gives a single-use URL
(30 minutes), `curl -F file=@<path>` from the session puts the bytes in Ramp
and answers 201 with a `receipt_uuid`, `ramp_create_reimbursement_from_receipt`
makes the draft. The bytes never pass through the model, which is why email
could not do this and this can. Write `files.json` from what he said each file
is (the README beside the files), then:

```sh
python -m daydag.expenses ramp-stage files.json --trip-uuid <from ramp_get_user_trips>
```

prints the four steps per file. Run them **only after his numbered go**, in
order, filling each placeholder from the previous answer. **Always pass
amount, merchant and date on create**: they are draft-only fields and Ramp's
OCR read a $1,203.77 folio total where $1,177.95 was paid, and named the hotel
"Hotel Tonnelle". Mail receipts keep the forward route (step 5 above): Ramp
answers from `do-not-reply@ramp.com` within seconds, and `check` reads it.

**2. The check reads Ramp, not mail.** Call
`ramp_get_user_recent_reimbursements` with `include_drafts: true`,
`limit: null`, `up_to_days` covering the trip; save the answer; then

```sh
python -m daydag.expenses ramp-check batch.json ramp.json --expected expected.json
```

where `expected.json` lists the lines filed from files (date, merchant,
amount, category, kind). It prints the pending total against the expected
total, every match with status, trip and GL, and both leftovers. Amount is the
join, so a line Ramp read with a different amount shows on both lists with the
gap in the header - that is the shape of the hotel on 2026-10-18.

**3. Fix in place, within what Ramp allows.** `ramp_edit_reimbursement` on a
PENDING reimbursement accepts trip, memo and coding and **rejects amount and
merchant**; those need a draft. Attach every trip line to the Ramp trip
(`ramp_get_user_trips`; he names it, e.g. "Burbank - Oct 2026"). Coding for a
Data & AI trip line is the three fixed selections plus the travel GL for the
category, all in `expenses_ramp.CODING` / `GL_BY_CATEGORY`, read from his own
rows - never guess a UUID.

**Submit is its own go.** Nothing prints a submit call. `ramp_submit_reimbursement`
runs only on "submit 1 2 3" over a list he has read; a submitted reimbursement
reaches an approver and stops being editable in the ways that matter. Never
delete or edit a reimbursement that is not his own, and never delete a PENDING
one without his word - it is in someone's queue.

## The report, by day

When he wants to see the expense report before submitting it, or asks what a
trip cost day by day:

```sh
python -m daydag.expenses report batch.json --out trip-by-day.md
```

Every row of the batch under its purchase day with a time, a subtotal per day,
the total, the split by category, and a ✓ on what Rippling already has. Paste
it as printed; the `--out` file is his to keep outside the repo. For the memo
to carry the route, the review needs the message bodies (step 2).

## How a reimbursement is staged

A forward **is** the staging. Rippling's receipts inbox scans the mail and
creates a **draft reimbursement** with vendor, amount, purchase date and
category filled from the receipt (seen 2026-10-15: an Uber mail became a
$11.98 draft under Rideshare & Taxis 17 seconds after the forward). `go all`
on a trip therefore leaves one draft per receipt waiting under Spend >
Reimbursement; he opens each, adds memo and report, and submits. There is no
other write path into Rippling for him - no API without an admin token - so
"stage my reimbursements" means: build the trip, review, go, check.

## How a receipt is placed

In the window, and then one of: a stop whose zip prefix is the hotel's (at
the destination); a stop on the hotel's street; an airport the trip flies
through, from a `(BUR)` in an address or a `BUR Airport Surcharge` /
`SeaTac Airport Pickup Fee` line (the home-side legs); a fee line naming the
trip's state (`CA Driver Benefits`). Anything else in the window is *where?*.
DoorDash and the generic sources carry whatever addresses the mail has; an
order delivered to the hotel places itself, one with no address does not.
Nothing here guesses from the amount or the merchant.

## Reconcile a trip against Amex

Amex sends nothing to his work mailbox (checked over 180 days, 2026-10-15). What
Amex gives him is the activity export: Statements & Activity > Download > CSV.
He drops the file somewhere outside the repo; then:

```sh
python -m daydag.expenses reconcile batch.json --amex /path/to/activity.csv
```

Three lists: matched, receipts with no charge yet, charges with no receipt.
The third is the one he acts on - a hotel or a lunch he still has to
photograph into Rippling. **A statement line is never forwarded**: Rippling
needs the receipt, and the CSV is not one.

## What never happens

- **No send without a per-batch go** naming the rows (house rule 2). The
  Python prints calls; you make them, after he says go, and only those.
- **One recipient.** `Forward` has no field for another address; do not add
  one at the connector either.
- **A charge summary is never forwarded, nor a statement line.** Rippling
  rejects "bank transaction history, screenshots, credit card slips" as
  proof; Uber mails the summary first under the same subject, and the code
  skips it and says how many. Zero receipts and N summaries means the
  receipts have not arrived yet - say so.
- **Nothing is re-sent on its own.** Already filed is set aside; changed is
  flagged. Both need him. Run `mark` the moment a forward returns: with no
  label scope, a forward that is not in the log is one the next run offers
  again.
- **Nothing the trip cannot place goes with `go all`.** The trip comes from
  his calendar, never from the receipts; a receipt never widens the window.
- **Nothing in the vault, nothing to a channel.** Receipts carry his home
  address and card last-4. This skill is `sensitivity: private`: the DM and
  the terminal are the only surfaces, and the log row is marked private.
- **Every row carries its permalink.** A receipt with none is refused.

## Degrade

Calendar unreachable, or no flight and no stay in the window: say so, and run
without a trip - every row is then offered, with the dates he gave as the only
frame. Gmail unreachable: one line, "couldn't check gmail", stop. Log unreachable:
run with the label exclusion only, and say the log was not checked so a
re-offer is possible. No CSV: skip reconcile, say so. An unverified source
(anything but Uber) prints a heads-up line; read those two fields before go.

## Not this skill

- **Approving Spend requests** ("You have 1 Spend request to approve"). A
  Rippling task for him; the brief may surface it, nothing here acts on it.
- **Rippling.** Superseded 2026-10-18; CMG expenses live in Ramp now. The Rippling API note below is history.
- **The Rippling API.** No expense or receipt endpoint is in the public docs,
  and a token needs a company admin. `expenses.py` is shaped so an API sink is
  a second `Forward`-like class behind `go`; build it when both exist, not
  before. Details and sources in the reference file.
- **A new source** (Uber Eats, Lyft, a hotel). Add a `Source` row in
  `expenses.py` with the sender and subject, `verified=False` until a real
  mail has been parsed, and a test on that mail's real shape. The generic rule
  covers it meanwhile and the render says so.
