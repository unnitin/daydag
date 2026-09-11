# The ingestion eval set

> Issue #14, built deliberately **before** the classifier (#15) so precision and
> recall are measured rather than asserted. 24 real meeting notes were read, 22 of
> them labelled, giving 155 hand-labelled items. The fixtures are `tests/fixtures/ingestion/notes.jsonl`
> and `tests/fixtures/ingestion/items.jsonl`; `daydag.evalset` loads and scores them.

## Read this first: nothing here is meeting text

This repository is public. The fixtures hold **structural features and gold
labels** — where the item sat in the note, whether an owner is named and in what
form, tense and modality, whether a date is present, which section it came from,
whether anything in the body supports it, and the label.

Each item also carries a `paraphrase`. **It is not a quotation.** It is a neutral
rewrite that preserves the one linguistic feature the item exists to test —
tense, modality, ownership form, presence of a date — and nothing else. Product
names, partner names, vendor names, project codenames, ticket contents and
numbers are all replaced with generic equivalents ("the platform", "the vendor",
"the warehouse", "a third-party source"). People are role tokens from CLAUDE.md
§2: `Principal`, `VP-Data`, `VP-AI`, `Eng-3`, `DataEng-1`, `Stakeholder-2`. There
is no mapping from a token to a person anywhere in the repository.

`scripts/scan_secrets.py` catches identifier *shapes*. It would pass a real
surname or a codename without comment, so
`test_no_paraphrase_carries_an_unrecognised_proper_noun` checks the other half:
every mid-sentence capitalised token must be in a declared allowlist. It has a
known blind spot — a name in sentence-initial position — recorded in its
docstring, because allow-listing every English word that can open a sentence
would make the check vacuous.

## How it was built

1. `from:gemini-notes@google.com newer_than:30d` — 201 notes in the window,
   every one labelled `meeting notes`, confirming the #2 connector audit.
2. 24 notes read in full via `get_message` with `PLAIN_TEXT`, chosen for spread
   rather than at random; 22 kept, the two dropped being near-duplicates of kinds
   already covered. The kept spread: 3 standups, 3 steering sessions, 4 reviews, 2
   one-to-ones, 2 working sessions, 2 cross-team syncs, a retro, a sprint
   planning, office hours, a programme review, an external partner demo, and one
   note whose entire content is that the meeting was cancelled.
3. Every item in each note labelled by hand into exactly one of the five SPEC
   §3.3 categories, with the four hard cases from #14 marked as such.
4. Emitted as JSONL, with `tests/test_ingestion_evalset.py` asserting the set is
   well-formed before anything is allowed to score against it.

Bounded queries throughout: the audit's finding that a 5-day calendar pull
returns 156k characters applies to Gmail too, so notes were fetched one message
at a time rather than as threads.

## What the notes actually look like

Two body layouts, then always a `Suggested next steps` section:

| Layout | Shape | Seen in |
|---|---|---|
| `quick_notes` | Themed headings, flat bullets of third-person fact | standups, working sessions |
| `summary` | A one-line summary, then 3 themed paragraphs of 2 sentences | everything else |

`Suggested next steps` items have one shape: `[Owner] Verb Object: imperative
sentence.` The owner slot takes a full name, several names, a bare first name, or
the literal string `[The group]`.

## Where SPEC §3.3 is wrong

Four things, all found by reading the notes rather than the spec.

**1. There is no `Decisions` section.** §3.3 step 1 says "Parse Summary /
Decisions / Next steps". Gemini emits `Summary` **or** `Quick Notes`, plus
`Suggested next steps`. That is the whole document. Decisions live only inside
the prose, and are identifiable mostly by verb — `decided`, `agreed`, `reached a
consensus`, `established`, `adopted`, `resolved`, `finalized`, `prioritized`,
`confirmed`. A classifier that routes on sections will find zero decisions.
`test_no_decision_is_labelled_from_the_next_steps_section` freezes this: across
33 gold decisions, not one came from the next-steps section.

*Mostly*, not only: **2 of the 33 carry no marker verb at all** ("priorities
shifted so the rollout targets Q4"). A verb allowlist is the bulk of the signal
and cannot be all of it, which is why the eval set counts the unmarked ones
rather than asserting they do not exist.

**2. The first-person signal does not exist.** §3.3 illustrates his own
commitment as *"I'll intro Daniel to CTO"*. Gemini writes everything in the third
person; `I` and `I'll` appear nowhere in 22 notes. What separates his commitment
from a description of his work is modality, not person — `[Principal] Send the
recordings` against `Principal reworked the flow`. The set contains both.

**3. "owner + what + when" is usually two of three.** `when` is almost always
absent: across the 22 notes, **12 of 149 next steps carry any date at all**. And
`[The group]` is common enough — **20 of those 149**, 14 of which are in the
labelled set and marked — that treating an unnamed owner as "not an ask" would
discard a large fraction of the real work.

**4. The note cannot tell you who assigned an ask.** §3.3 asks for "an ask **he**
assigned". Gemini names an owner and never an assigner, so that distinction is
not recoverable from the note. It has to be inferred from the meeting — his
one-to-ones and his own sessions — and that inference should be surfaced as an
inference, per guardrail 3.

A fifth thing, not a contradiction but the finding that shaped the labelling:
**the entire `Suggested next steps` section is model-written suggestion, not
transcript.** So "a next step the note invented that nobody actually said" is not
a rare edge case. It is a structural property of the section, and it is the
single strongest argument for weighting precision over recall.

## The five categories

| Label | Count | What it is |
|---|---|---|
| `assigned_ask` | 69 | An action item with an owner (named, first-name-only, or `[The group]`) |
| `noise` | 38 | Status, sentiment, framing, logistics, board hygiene, invented next steps |
| `decision` | 33 | A choice settled about existing work |
| `new_workstream` | 9 | Work, a system or a programme that did not exist before the meeting |
| `principal_commitment` | 6 | An action item he owns, alone or jointly |

Three labelling conventions had to be decided, and are decided here rather than
in a prompt:

- **Creating beats settling.** An item with a decision verb that produces
  something to track (`decided to bring the experiments under one programme`,
  `finalized a three-month parallel run`) is `new_workstream`, not `decision`.
  Decisions update a Workstreams block; workstreams open one.
- **Someone else's self-reported commitment is `noise`.** "DataEng-1 is drafting
  the roadmap, targeted for the middle of next week" has an owner, a what, and a
  when, and he did not ask for any of it. There is no slot for it in §3.3, and
  logging it opens a chase loop nobody owns. Marked `third_party_commitment` so
  the classifier's failures on it are visible separately.
- **Board hygiene is `noise`.** "Move the bug fix to done on the board" is real,
  owned, and worth nothing on a chase list, because the board already carries it.

## Two things to know before tuning on these features

The features are structural, so it is tempting to fit a rule to them directly.
Two of them are closer to the label than they look:

- **`grounded_in_body` is not a synonym for `invented_next_step`.** All four
  invented items are ungrounded, but so are two perfectly real owned asks
  (`n05-s01`, `n11-s03`) — a next step can name something the summariser chose
  not to carry into the body. Ungrounded is a reason to look, not a verdict.
- **`owner_form == "principal"` in the next-steps section is near-deterministic**
  for `principal_commitment`, and that is a property of Gemini's output rather
  than an artifact of the sample: if the note puts his name in the owner slot, it
  is his. The hard half of that category is recall, not precision — the joint
  item (`n19-s04`, three owners including him) and the past-tense body line
  (`n04-b01`, his name, already done) are the two shapes a name-matching rule
  gets wrong in each direction.

## The four hard cases

| Case | Count | The trap |
|---|---|---|
| `unowned_ask` | 14 | Owner is `[The group]` (20 of 149 next steps). Dropping it loses real work; inventing an owner is worse |
| `invented_next_step` | 4 | No support anywhere in the body. `grounded_in_body` is false, gold is `noise` |
| `third_party_commitment` | 3 | Named owner, future tense, sometimes dated — and not his |
| `reversed_decision` | 3 | Stated as settled, contradicted later in the same note. `superseded_by` names what displaced it |

Both halves of a reversal are in the set — the decision as first stated *and* the
item that contradicts it — because the read-alone shape of the second half is
completely unremarkable. `n15-b01` drops a vendor purchase; `n15-s02` negotiates
an extension of that vendor's subscription. `n13-b03` defers finalising resource
allocation; `n13-s02` goes and reallocates a person. Neither pair is resolvable
from the note, and per guardrail 4 the agent surfaces the pair and resolves
nothing.

Hard cases are 15% of the set — deliberately over-sampled against their real
frequency, capped so that precision on the set still predicts precision in the
inbox.

## What "precision over recall" means here, and the bar

SPEC's bias, stated plainly: **a wrongly-logged commitment costs more trust than
a missed one, because the missed one still surfaces in Slack.** The asymmetry is
not aesthetic. A false positive puts a fabricated obligation on his weekly note
under a `*(mine)*` tag, or opens a chase loop that nudges a colleague about
something nobody agreed to. A false negative loses a line in a digest for an item
that a human, a thread reply, or the engineering pulse will raise anyway.

`daydag.evalset.score()` makes this arithmetic. Three of its choices exist so a
bar cannot be cleared without being met:

- an item the classifier never emitted counts as a **miss**, not an abstention;
- predicting nothing for a label that has support scores **0.0** precision, not
  1.0 — otherwise emitting nothing clears every precision floor below;
- a prediction for an `item_id` that is not in the set **raises**, so a typo or a
  hallucinated row cannot quietly read as silence.

**The agreed bar for #15**, on this set:

| Category | Precision | Recall |
|---|---|---|
| `principal_commitment` | ≥ 0.95 | ≥ 0.70 |
| `decision` | ≥ 0.90 | ≥ 0.60 |
| `assigned_ask` | ≥ 0.85 | ≥ 0.70 |
| `new_workstream` | ≥ 0.80 | ≥ 0.50 |
| `noise` | — | ≥ 0.80 |

Read the two columns as different kinds of claim. The precision floors are hard:
below them the digest is untrustworthy and the loop is worse than nothing. The
recall floors are targets — missing a third of the decisions is survivable, and
`noise` recall is the one that actually protects the others, because everything
the classifier fails to recognise as noise lands somewhere it does not belong.

Two consequences worth stating before the classifier exists:

- **`principal_commitment` is the category to be conservative in.** Six examples
  is a thin basis for a 0.95 precision claim, so the honest reading of a passing
  score there is "no counter-example yet", not "measured at 95%". Widen the set
  before trusting a number that close to 1.
- **An abstain path is legitimate; a guess is not.** An item the classifier
  cannot place should go into the digest as an unplaced item for him to settle,
  the way `ledger.offer_note()` surfaces an ambiguous note instead of attaching
  it to the better-scoring row.

## Refreshing it

The fixtures are frozen data and the tests never touch the network. Re-derive
them the way they were built — search, read notes with `PLAIN_TEXT`, label by
hand — and keep `test_ingestion_evalset.py` passing, which is what stops the set
quietly losing a category or a hard case. If a category's share moves outside the
3–55% band the distribution test fails on purpose: an eval set that is mostly one
label measures that label and nothing else.
