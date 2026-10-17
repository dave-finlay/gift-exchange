# Gift exchange: design

A small, invite-only web app for running gift exchanges: everyone in a
group is secretly assigned one other person to buy a gift for. It's the
successor to an earlier command-line script, and the vehicle for an
experiment in automated security (dependency updates, CVE scanning,
rebuilds and redeploys).

It's meant to be self-hosted. A technically minded person can fork the
repository and run a private exchange site for their family, friends or
community, quickly and cheaply, and change it to suit their group. That's
what sets it apart from the established free services.

Status: design, as of 2026-10-16. No code yet.

## Goals

- **Double-blind.** Each person learns only who they're buying for. The
  organiser doesn't learn the assignments, and doesn't have to run the
  draw or send the emails.
- **Rules.** People in the same household never get each other. The
  organiser can add other exclusions, force a pairing ("Bob wants
  Carol"), and avoid repeating recent years' pairings.
- **Test mode.** Check the rules ahead of time without making the real
  draw.
- **Friendly emails** with the organiser's note.
- **Free, no ads, no unrelated email.**
- **Invite-only.** The operator invites people they know; they create a
  login and run their own exchanges. Participants need no account.
- **Easy to self-host and fork.** One small server, low running costs,
  clear setup instructions, and nothing specific to one family or group
  in the code.

## Architecture (decided)

| Area | Decision |
|---|---|
| Hosting | One Hetzner cloud server (ARM, Germany or Finland) |
| Runtime | Docker Compose: the official Caddy image plus the app image |
| HTTPS | Caddy, with Let's Encrypt certificates renewed automatically |
| App | Django 5.2 LTS under gunicorn (2–3 sync workers), dependencies managed with `uv` (`uv.lock`), base image `python:3.13-slim` |
| Database | SQLite in WAL mode, with a busy timeout and Django's `"transaction_mode": "IMMEDIATE"` |
| Backups | Backblaze B2 (Amsterdam). Layer 1: Litestream continuous replication, in its own container, with a one-off restore container before the app starts. Layer 2: a nightly `sqlite3 .backup`, encrypted with `age` (the server holds only the public key), in a separate bucket with 30-day Object Lock and a write-only key. 30-day retention. A weekly automated restore test reports to healthchecks.io. |
| Secrets | Master copies in KeePassXC. A script on the Mac reads them with `keepassxc-cli` and installs them over SSH into `/etc/gift-exchanger/secrets/` (root-owned, readable only by the container's user). Mounted per service with Compose `secrets`, never as environment variables. CI never sees production secrets. |
| Email | Brevo over its HTTPS API, via `django-anymail`. Emails carry a private link, not the assignment. Link and open tracking are off. The API key is limited by Brevo's Authorized IPs setting. |
| Domain | The operator's own domain, with DNS at Cloudflare |
| Sign-in | Organisers: password (Argon2id), lockout after repeated failures (`django-axes`), optional two-factor codes. Invitation links are single use, expire after about 7 days, and are stored hashed. |
| Admin | Django admin, reachable only through an SSH tunnel |
| CI | GitHub Actions |
| Local development | OrbStack |

Still open: Cloudflare proxy in front (optional), `django-allauth` or
plain Django with `django-otp`.

## Data model

```
User (organiser) ──< Invitation
   │
   └──< Circle ──< Household
           ├──< Person ─── household (optional)
           ├──< Rule   (giver Person → receiver Person, EXCLUDE | REQUIRE,
           │            standing or for one exchange)
           └──< Exchange ──< Participant ──── Assignment (receiver encrypted)
                                 └──< EmailLog
AuditEvent
```

A **circle** is the lasting group ("the Smith family"): people, households and
standing rules. An **exchange** is one event ("Christmas 2026"): who takes
part this time, one-off rules, the draw and the emails. The model is
`Circle` rather than `Group` to avoid clashing with Django's
`auth.Group`.

| Model | Main fields | Notes |
|---|---|---|
| User | email (sign-in name), display_name, password (Argon2id), is_staff | Custom user model from the start. 2FA devices come from `django-otp`. |
| Invitation | email, token_hash, invited_by, created_at, expires_at, accepted_at, revoked_at | Single use; only the hash of the token is stored |
| Circle | owner (User), name, created_at | One owner for now; co-organisers maybe later |
| Household | circle, name | Members never give to each other, in either direction |
| Person | circle, name, email, household (nullable), active | Name unique within the circle. Email not unique, so a child can share a parent's address. |
| Rule | circle, exchange (null means standing), giver, receiver, kind (EXCLUDE or REQUIRE), note | One direction each; the UI's "both ways" makes two rules |
| Exchange | circle, name, gift_date, draw_date (optional, shown to participants), budget, note, status, confirm_first (default true), avoid_repeats, allow_mutual (default false), single_loop (default false), drawn_at, sent_at, archived_at | `avoid_repeats` is the number of previous exchanges in the circle whose pairings to avoid. `allow_mutual` allows A→B together with B→A. `single_loop` makes the draw one loop through everyone (A→B→C→…→A); when it's on, `allow_mutual` has no effect. |
| Participant | exchange, person, link_token_hash, link_rotated_at, response (pending, in, declined), responded_at, gift_status (not_yet, bought, sent), gift_status_at, revealed_at, withdraw_requested_at, withdrawn_at, created_at | Who takes part in this exchange; the private link points here |
| GiftIdea | participant, text (up to 280 characters), created_at, updated_at | Ideas for yourself, seen only by your giver. Up to 10 per participant; deleted at archive. |
| Assignment | giver (Participant, unique), receiver_ciphertext, created_at, updated_at | See "Assignment secrecy" |
| EmailLog | participant, kind (join, assignment, reassignment, removed, reminder), sent_at, provider_message_id, status (sent, delivered, bounced, complaint) | Never stores email bodies. Statuses come from Brevo webhooks. |
| AuditEvent | actor, action, target, created_at, ip | Sign-ins, invitations, draws, sends, rule changes, withdrawals, admin actions |

### Rules

- **Allowed pairing.** Giver G may get receiver R only if all of these
  hold:
  - G ≠ R;
  - they're not in the same household;
  - no EXCLUDE rule applies to G→R, either standing or for this exchange;
  - G→R didn't happen in the circle's last `avoid_repeats` exchanges.
- **Forced pairings.** A REQUIRE rule fixes G→R. It's rejected if it
  conflicts with any of the rules above, including same household, or with
  another REQUIRE rule that has the same giver or the same receiver. With
  mutual pairs off, REQUIRE rules A→B and B→A are rejected together.
- **Structure.** By default no mutual pairs are allowed (if A gives to B,
  B doesn't give to A). Optionally the draw must be a single loop.

### Exchange lifecycle

`Draft` → `Joining` → `Sent` → `Archived`

- **Draft:** edit participants and rules, run test mode.
- **Joining** (only if `confirm_first`): "you're in" emails have gone out.
  Participants confirm or decline, and add gift ideas. These emails also
  confirm that addresses work. Before drawing, the organiser decides
  whether to include or leave out anyone who hasn't responded. Without
  `confirm_first`, the exchange goes straight from Draft to Sent.
- **Draw and send** is one action, started manually by the organiser
  after a confirmation page. The organiser can't see the draw, so a
  separate "drawn but not sent" stage would add nothing. Once the draw
  is made, the rules are locked.
- **Sent:** assignment emails have gone out. People can still withdraw,
  and late joiners can be added (see below).
- **Archived:** about 60 days after the gift date. See "Retention".

### Assignment secrecy

- Only the receiver is encrypted. It's stored as
  `MultiFernet.encrypt("exchange_id:giver_id:receiver_id")`. The IDs are
  checked when decrypting, so an encrypted value can't be copied to a
  different row.
- Exactly one code path decrypts: showing a participant their own
  assignment, reached through their private link. Organiser pages and the
  admin site never decrypt. Tests check this.
- The database can't enforce "each person receives exactly once" because
  receivers are encrypted. The draw checks that the whole result is a
  valid assignment before encrypting, and saves it in one transaction.
- The draw uses `secrets.SystemRandom`. Seeds are never logged or stored.
- Private links are 32-byte random tokens, stored hashed. "Re-send my
  link" issues a new token, so old forwarded links stop working.

### Matching algorithm

The allowed pairings form a directed graph with an arrow G→R for every
allowed pairing. A valid draw is a perfect matching from givers to
receivers using only allowed arrows. On top of that, there must be no
mutual pairs (unless `allow_mutual`), or the draw must be a single loop
(if `single_loop`).

The code is a pure Python module (`matching.py`) with no Django and no
dependencies. Up to 50 participants.

1. **Feasibility.** Run maximum bipartite matching (augmenting paths).
   - If there's no perfect matching, use Hall's theorem to explain why:
     find the set of k givers who between them can only give to fewer than
     k people, e.g. "Alice, Bob, Erin and Grace can only give to Carol,
     Dan and Frank between them".
   - Re-check with each type of rule relaxed to suggest fixes, e.g. "it
     would work if repeats were avoided for 1 year instead of 2".
   - If the structure settings add constraints, a perfect matching is
     necessary but not sufficient. A randomised backtracking search with
     pruning and a time limit then finds a valid draw, or explains the
     failure, e.g. "every possible draw has Carol and Dan giving to each
     other". For a single loop (a Hamiltonian cycle, NP-complete in
     general), a timeout gives "couldn't find a single loop with these
     rules".
2. **Fair draw.** Every valid draw should be equally likely.
   - Rejection sampling: fix the forced pairings, shuffle the remaining
     receivers with `secrets.SystemRandom` (Fisher–Yates), and accept if
     every rule and structure setting holds. For a single loop, shuffle a
     cyclic order of the participants instead. This is exactly uniform.
   - Fallback after about 2 seconds: start from a valid draw found by the
     search, then randomise it with a long sequence of random swaps that
     keep it valid (a Markov chain whose steady state is uniform). This is
     approximately uniform; record which method was used, and warn in
     test mode.
   - Validate the complete result before encrypting and saving it.
3. **Test mode.** Run about 1,000 fair practice draws and count how often
   each giver gets each receiver. This works for every setting. Nothing is
   saved.
4. **Repairs** (withdrawals and late joiners). Search for the smallest
   change that keeps every rule and structure setting: change 1 existing
   giver, then 2, then 3, otherwise offer a full redraw. Among equally
   small changes, choose at random. (Without the structure settings this is the
   classic assignment problem, solvable exactly by the Hungarian algorithm,
   but a small bounded search handles every setting with one method.)

Tests use Hypothesis, a property-based testing library (needed for tests
only), to generate random groups and rules. They check that:

- every draw is valid;
- "impossible" agrees with a brute-force check for small groups;
- repairs are minimal;
- a statistical test on a tiny group shows every valid draw coming up
  equally often.

### Test mode

- It reports whether a valid draw exists, and explains why if not. It
  saves nothing.
- From the practice draws, it shows how predictable each person's
  assignment is. It warns when the rules come close to revealing the
  result, e.g. "with these rules, Alice gets Dan in 85% of draws", or
  "Alice can only ever get Dan".
- It warns when the draw would only be approximately uniform.

### Organiser screens

Responsive. Setup is probably done on a laptop and checking progress on
a phone, so both have to work. The organiser's own assignment arrives
by email like everyone else's, and never appears on organiser screens.

1. **Home.** Circles, each with its current exchange, its status and the
   next step, e.g. "Christmas 2026 · Joining · 9 of 12 in · draw on 1
   Nov". There's also "New circle".
2. **Circle page.**
   - People are grouped by household: add, edit, deactivate.
   - **Paste a list:** lines of `Name, email`, like the old script's
     file.
   - Standing rules, offered as "both ways" or one way.
   - Past exchanges, and "New exchange".
3. **Exchange setup.** One page with sections, not a wizard:
   - basics (name, gift date, draw date, budget, note with an email
     preview);
   - who's in (all active people ticked by default);
   - rules for this exchange (with an optional private note, e.g. the
     reason for a forced pairing);
   - settings (avoid repeats, allow mutual pairs, single loop, confirm
     first);
   - **Check the rules** (test mode). By default it shows only counts,
     e.g. "1 person's assignment is very predictable". Names are behind
     "Show details", with a warning that this may reveal likely
     assignments, including the organiser's own if they take part.
   - The main button: "Send 'you're in' emails", or "Draw and send" when
     `confirm_first` is off.
4. **Exchange dashboard.**
   - A banner for withdrawal requests.
   - One row per participant: response, email status (with bounces
     flagged, plus "fix email", which re-sends to that person only),
     revealed or not, and gift status.
   - Reminders to a filtered group (no response yet, or not bought yet),
     with a cap on how often they can be sent.
   - "Add a late joiner", "Draw and send" (during Joining), and "End
     exchange now".
5. **Withdrawal review.** "Remove Erin? 1 other person will get a new
   assignment email." It shows only the number of people affected. The
   organiser can also remove someone directly.

Later: scheduled automatic draw on `draw_date`; co-organisers; a message
to everyone.

### Participant page

Mobile-first and one column, because most people open it from an email on
a phone. Plain wording. Works without JavaScript; no third-party scripts or
fonts; strict Content-Security-Policy.

**Getting in.** The private link is the participant's only identity.

- Opening the link changes nothing. Email security scanners open links
  automatically, so a visit doesn't count as "viewed", and nothing is
  single-use.
- On the first visit, the app swaps the token for a session cookie
  limited to that exchange, and redirects to a clean URL.
- The page sets `Referrer-Policy: no-referrer` and `noindex`. Caddy's
  access logs are set up not to record the token.
- When people share an email address, each still gets their own email and
  link, and the subject names them ("For Frank: …").

**What it shows**, by exchange status:

| Status | Page |
|---|---|
| Joining | "Bob has added you to *Christmas 2026*." Buttons: "I'm in" and "Not this time". Add gift ideas for yourself. Shows the draw date. |
| Sent | The main page (below) |
| After gift_date | "Hope you enjoyed it!" Ideas and status are still visible. |
| Archived | Only "This exchange has ended". |

**Main page**, top to bottom:

1. The exchange name, the circle, the organiser, the gift date with a
   countdown, and the budget.
2. "You're buying for…" with a **Tap to reveal** button. Revealing is a
   button press (a form POST), never just opening the page. It also
   protects against someone glancing at the screen. Once revealed, it
   shows the receiver's gift ideas.
3. Your gift status: not yet, bought, sent. Only the organiser sees it,
   for each person, which reveals nothing about who they're buying for.
   Receivers see nothing about their incoming gift.
4. Your own gift ideas (only your giver sees them): add, edit, delete.
   Text is escaped. Only `https://` links are made clickable, with
   `rel="noopener nofollow"`.
5. The organiser's note.
6. Who's taking part (names only).
7. "Can't take part any more?" This asks the organiser, who confirms
   before the withdrawal repair runs. It never happens instantly, because
   of forwarded links and accidental taps. Also "Lost this email? Get a new
   link", which issues a new token.

**Later:** anonymous questions from giver to receiver ("What size are
you?"). **Not planned:** postal addresses, because they're the most
sensitive data the app could hold.

### Someone withdraws after the emails go out

When participant P withdraws, let G be P's giver and R be P's receiver.
The repair search (see "Matching algorithm") finds the smallest valid
change:

1. **Simple fix.** If G→R is allowed, G now gives to R. Only G gets a new
   email, and P's assignment is deleted. With a single loop, this keeps the
   loop intact.
2. **Swaps.** Otherwise, change one or two more givers, e.g. G gives to Y
   and X gives to R. This includes the case where G and P were giving to
   each other, so that R = G. Only the changed givers get new emails.
3. **Full redraw.** If nothing smaller works, offer a full redraw, which
   emails everyone.

The organiser is told only how many people will be re-notified, never who.
P gets a short "you've been removed" email. Each withdrawal is recorded as
an AuditEvent.

### Someone joins after the emails go out

This is the reverse of a withdrawal. The organiser adds new participant N
(from the circle, or a new person). The repair search picks an existing
pairing X→Y such that X→N and N→Y are both allowed. Then X gives to N,
and N gives to Y. Only X gets a new email; N gets a normal assignment
email. With a single loop, this keeps the loop intact. If no such
pairing exists, search for larger changes as for withdrawals.

### Retention

- **At archive time** (about 60 days after the gift date): private links
  stop working, and gift ideas, statuses and email logs are deleted.
- **Encrypted assignments** are kept only as long as needed for
  `avoid_repeats`, then deleted.
- **Circles and people** are kept until the organiser deletes them. A
  circle unused for 2 years is deleted after a warning email.
- **Deleting an account** deletes everything it owns.
- **Backups** expire within 30 days, so deleted data is gone everywhere
  within 30 days.

### Size limits

These are settings, checked in forms and in the draw:

- up to 5 circles per user;
- up to 50 people per circle;
- up to 4 exchanges per circle per year;
- a daily cap on emails per exchange.

## Next

- Email templates.
- Repository setup: git, project layout, `uv`, a first version of CI.
