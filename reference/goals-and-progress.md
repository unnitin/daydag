# Goals & Progress — the goal-progress document

> **Pointer, not a copy.** Live Google Doc `${GDOC_GOALS_PRIORITIES}` ("Goals & Progress", owner ${EMAIL_PRINCIPAL}, last modified **2026-08-15**). It is the doc `weekly-progress-reporting` updates — that skill references the same id at its lines 20 and 118.
>
> This file is a **snapshot taken 2026-09-05** so the agent knows what to watch. The doc is authoritative; re-read it rather than trusting the ledger below. Copying it wholesale would repeat the Workstreams drift mistake.

## Structure

Two tabs' worth of content in one doc:

- **Priorities** — `v021726` (original prose goals) superseded by **`v072926`**, the SMART/Lattice refresh: 5 Objectives, each with a Key Result table (measurement · baseline · target · due).
- **Progress** — dated snapshots `v053026`, `v060526`, `v081426`. The Aug 14 one is the first structured against the five SMART objectives rather than the older 0217 dimensions.

## KR ledger as of 2026-09-05

Derived from `v072926` targets against the `v081426` progress narrative. **Nothing here has been verified against live systems** — that is the agent's job, and the ledger is exactly what it should be verifying.

### Past due

| Objective | Key result | Due | Evidence as of Aug 14 |
|---|---|---|---|
| 3 — A&R discovery (VP-AI) | R1.5 **shipped to production** | **Aug 11** | *"effectively met at staging"* — the KR says production; beta-user provisioning was still "the next step". **Staging ≠ production; 25 days past due** |
| 5 — Team (Nitin) | Individual goals cascaded, 100% of team | **Aug 31** | *"individual cascade is the open piece"* |
| 1 — Data foundations (VP-Data) | Estate-integration roadmap published | **Aug 31** | Phase 1 described as "moving in practice"; publication of the roadmap itself unconfirmed |

### Due Sep 30 — 25 days out

| Objective | Key result | Standing blocker |
|---|---|---|
| 1 (VP-Data) | Gold Label-Engine join cutover complete, parallel pipelines retired | — |
| 1 (VP-Data) | Artist/contributor model shipped; gold opened for Analytics self-serve | Named as *the* remaining dependency in both the doc and `Workstreams#Data platform` |
| 1 (VP-Data) | Top-10 DSPs on one performance model | 6 of 10 at baseline |
| 4 (VP-AI/Declan) | Internal-AI security/review path formalized (intake + risk register) | `Workstreams#Security` still reads "running manually" |

### Due Oct 31 / Dec 31

Obj 1 revenue data domain · Obj 3 artist-evaluation + deal-discovery agents live in production · Obj 4 eval loops enforced as a release gate · Obj 5 career framework extended, DPM hired (Oct 31). Obj 2's two automation counts, Obj 3 external-partner count, Obj 4 registry, Obj 5 feedback cadence (Dec 31).

Targets marked *(proposed — confirm)* in the doc — Obj 2 both KRs, Obj 3 partner count, Obj 5 DPM date — were never confirmed. That is itself an open loop.

## Why this belongs to the agent

**The progress doc is three weeks stale and the KRs are not.** Last update Aug 14; three KRs have since passed their due date with no recorded outcome. This is the same failure the agent exists to prevent, in the highest-visibility artifact Nitin owns — the one mapped to Lattice and read by the sponsor.

Hooks into the loops:

- **§3.4 chase** — every KR is an ask with a named owner and a hard date. The clock is the due date, not 2 business days.
- **§3.7 pulse** — several KRs are directly observable: R1.5 to production is a deploy, the Label-Engine cutover and DSP count are pipeline state. The pulse can answer them without asking a human.
- **§3.5 / §3.6** — a KR crossing its due date with no evidence is a lead item, not a footnote.
- **`weekly-progress-reporting`** — already owns the write-back into this doc. The agent feeds it evidence; it does not write the doc itself.

**Do not write to this doc.** It goes to the sponsor and into Lattice. Same rule as Jira: read it, draft for it, let the human ship it.
