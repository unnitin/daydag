# GitHub access: the minimum DayDAG actually needs

Measured 2026-09-13 against the code, not guessed from the loop descriptions.

**Status:** the org enforces SAML SSO. The existing broad token was authorized
for `CreateMusicGroup` on 2026-09-13 and the API works again, so this document
is now about *narrowing* that grant rather than restoring access. Before the
authorization every `gh api` call returned 403 while `git` kept working over
both SSH and keychain HTTPS — which is the failure mode to recognise, because
it reads as "the repo is quiet" rather than as an auth error.

## The answer

**One permission: `Pull requests: Read`.** Plus `Metadata: Read`, which every
fine-grained token carries automatically and cannot be switched off.

Nothing else. Not Contents, not Actions, not Projects, not Administration.

## Why it is that small

The pulse is split by what the data physically is (`pulse.py` module docstring:
*"git mirrors for history, the API for review state"*). Each half sources
differently, and only one half needs a token at all.

| need | source | token? |
|---|---|---|
| merges, commits, history, branch state | local git mirror | no - SSH or keychain |
| open PR title / number / state | API | yes, `Pull requests: Read` |
| GitHub Projects v2 board columns | not read at all | no |
| CI verdicts | not consumed today | no |

Three specifics worth not re-deriving:

1. **`observe_pr` takes exactly three fields** - `title`, `number`, `state`
   (`pulse.py`). That is the entire API surface DayDAG consumes. Everything
   richer in the docstring is aspiration, not a call site.
2. **Projects v2 is deliberately flagged, not read** (the retired `board.py`, at tag `pre-simplification`: *"GitHub
   Projects v2 is flagged, not read - the v2 API returns an empty page rather
   than an error, which is why a watched org project renders as one [note]"*).
   So the `project` / `read:project` scope buys nothing.
3. **DayDAG never reads a GitHub token itself.** No `GH_TOKEN` or
   `GITHUB_TOKEN` anywhere in `src/`. Mirrors shell out to `git`; the token
   exists only for `gh api` calls made by the agent.

Optional, only if you later want issue state surfaced: `Issues: Read`. The pods
run their boards on GitHub issues with wave parent issues, but no code path
reads them today.

## What is wrong with the current token

Scopes as of 2026-09-13: `gist`, `read:org`, `repo`, `workflow`.

`repo` is the problem. It is full read **and write** on every private repo the
account can see - push to any branch, change collaborators, edit deployment
statuses. `workflow` adds the ability to rewrite GitHub Actions workflow files.

One correction to the usual worry: `repo` does **not** grant repository
deletion. That is a separate scope, `delete_repo`, and it is not on this token.
The ask is still far too broad - it is write access to everything - but nothing
here can delete a repo.

There is no read-only classic scope for private repo contents. Classic tokens
are all-or-nothing, so the fix has to be a fine-grained token.

## Minting the replacement

1. github.com → Settings → Developer settings → **Fine-grained tokens** → Generate new.
2. Resource owner: **CreateMusicGroup** (not the personal account, or org repos stay invisible).
3. Repository access: **Only select repositories** → the nine in `DayDAG/Watchlist.md`.
4. Repository permissions: **Pull requests: Read-only**. Leave every other row at "No access".
5. Generate, then **Configure SSO** on the token and authorize CreateMusicGroup.

Then hand it to `gh` without replacing the keychain login:

```sh
export GH_TOKEN="github_pat_..."      # or put it in .env and source it
gh api repos/CreateMusicGroup/createos-discovery-services/pulls --paginate \
  --jq '.[] | {number, title, state}'
```

Expect `gh auth status` to say it cannot determine scopes for a fine-grained
token. That is normal. `gh api` is the only surface that matters here; some
higher-level `gh` subcommands assume classic scopes and will complain.

**Likely snag:** orgs with SAML enforced usually require an owner to approve
fine-grained tokens before they work. If step 5 leaves it pending, that
approval is the blocker, not the permission set.

## Git stays on SSH

Unrelated to the token and already working - verified with a bare clone on
2026-09-13. `git ls-remote` over both SSH and HTTPS-via-keychain succeeded on
every watched repo *while* `gh api` was still returning 403. Do not add
`Contents: Read` to fix a mirror problem; mirrors were never broken.

**The trap this creates.** Git working while the API is dead means a
default-branch check still answers, so a repo looks reachable and merely idle.
That is how a live repo gets read as stalled. Two rules follow:

1. Check branch tips, not just `main`. Several repos here work on `dev` or a
   pod branch, so the default branch can be weeks behind the real work.
2. Before calling a repo dormant, confirm the API is actually answering, and
   confirm the repo is the one still in use - a predecessor repo keeps its
   whole branch history and looks authoritative long after the work moved.
