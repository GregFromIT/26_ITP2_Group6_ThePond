# Design decisions

Why things are the way they are, and what we turned down. Mostly written so we
don't re-argue the same points, and so there's something to point at in the
report when someone asks why.

Each one is roughly: what we decided, what else was on the table, and what it
costs us.

---

## Flask and SQLite rather than Django

**Decided:** Flask with plain SQL against SQLite.

**Also considered:** Django, which would have given us migrations, an admin
site, and auth, sessions and CSRF as framework features rather than code we
write.

**Why not:** two reasons. It's a rewrite of everything mid-semester on a project
with a client contract. And a fair chunk of what Django would hand us for free
is the exact material the unit assesses. Hand-writing the CSRF check and the
lockout logic is easier to defend in a report than "the framework did it".

**What it costs:** no migrations, which is the real one. See below.

---

## No migration system (yet)

**Decided:** `init-db` drops and rebuilds the schema from `schema.sql`.

**Also considered:** Alembic from the start.

**Why not:** while the only data is seeded demo content, dropping everything is
genuinely convenient and nothing is lost.

**What it costs:** the moment there are real student scores, a schema change
destroys them. This has to be solved before the first real class, not after.
It's on the to-do list in the README and flagged at the top of `app/db.py`.

---

## No email anywhere

**Decided:** no email addresses stored, no reset links, no SMTP. Password
recovery is a staff-issued temporary password handed over in person.

**Also considered:** the original design had emailed reset links with a 24-hour
expiry, and it was built and working before we took it out.

**Why:** it removes a whole class of problems in one go — reset-link
interception, mail spoofing, address enumeration through the reset form,
mail-bombing a student's inbox — and it means we're not holding personal data
with no purpose. It also sidesteps needing anything configured against the
university mail relay.

**What it costs:** two things, and both belong in the report.

Recovery now depends on staff being reachable. A student locked out at 11pm the
night before an assessment has no way in until somebody answers them.

And staff can effectively take over any account below their rank, because
issuing a temporary password is a full credential reset. That's inherent to
staff-held recovery rather than a flaw in how we built it. The mitigations are
that the action is audited by name, and that nobody can use it sideways or
upwards.

---

## Flags stored as hashes

**Decided:** `challenge_points` stores SHA-256 of the flag, never the plaintext.
Submissions are stripped and lower-cased before hashing.

**Also considered:** storing them in the clear, which would let staff read
flags out of the database when setting up a challenge.

**Why not:** the whole point of a flag is that possessing it proves something.
A database dump handing over every answer defeats that, and this platform is
specifically used by people learning to dump databases.

**What it costs:** staff can't look a flag up. They have to keep the plaintext
wherever the challenge is authored. Right now that's `app/seed.py`.

---

## Double-award prevention is a database constraint

**Decided:** `UNIQUE (user_id, flag_id)` on the award ledger.

**Also considered:** a boolean on the user or the award table, checked in
Python before paying out. That's what the original outline sketched.

**Why not:** an application-level check has a gap between reading and writing.
Two tabs submitting the same flag at the same instant can both pass the check
and both get paid. The constraint can't be raced — the second insert simply
fails.

**What it costs:** nothing we've found. This is the change we're happiest with.

---

## Points denormalised onto `user`

**Decided:** `user.points` is a running total, incremented in the same
transaction as the award.

**Also considered:** calculating it from the ledger every time, which is
strictly correct.

**Why not:** the outline explicitly wanted the leaderboard to be
`SELECT points FROM user ORDER BY points DESC`, and that's genuinely simpler and
faster than aggregating every award on every dashboard load.

**What it costs:** the total can in principle drift from the ledger. The
recalculation query is in `docs/DATA_MODEL.md` if it ever needs fixing.

---

## Theme weighting applied on read

**Decided:** weighting multiplies the score when a leaderboard is read, and is
never baked into a stored score.

**Why:** changing a theme's weighting updates every board on the next page load
and nobody needs rescoring. If it were stored, changing it would mean a bulk
recalculation and a decision about whether past results move.

**What it costs:** it's repeated in four places — the two views plus
`theme_challenge_matrix()` and `user_stats()` — because SQLite views can't share
a predicate. They all have to agree.

---

## Administrators excluded from the leaderboards

**Decided:** admins don't appear on any board and have no rank. Moderators do
compete.

**Why:** an admin sitting mid-table looks odd when that same person is marking
you. Moderators are different — they're usually students helping run a class, so
hiding them would cost them their own results.

**What it costs:** the exclusion is in four places, same as the weighting. Grep
for `role != 'admin'` before changing eligibility.

---

## No score editing, from any role

**Decided:** nothing in the platform can edit a score or delete a flag award.

**Also considered:** an admin-only override for cheating cases or mistakes.

**Why not:** the scores are the record of what a student actually did, and the
award ledger is what makes double-claiming impossible. A cheating case should be
a deliberate database action with a reason written down, not a button a tired
moderator can hit at 4pm. If the client wants one, it should be added
consciously with its own permission and its own audit event.

---

## Proxmox behind an adapter

**Decided:** `app/proxmox.py` has two backends, `simulate` and `api`, behind one
interface. Nothing else in the codebase imports `proxmoxer`.

**Why:** the whole platform demos and tests without a cluster, which mattered
while the hypervisor wasn't available. Switching to the real thing is a config
change.

**What it costs:** a thin layer of indirection, and the `simulate` backend has
to be kept honest as the real one changes.

---

## Linked clones by default

**Decided:** `PROXMOX_FULL_CLONE=0`, so clones share the template's disk.

**Also considered:** full clones, which is why `PROXMOX_STORAGE` exists.

**Why:** linked clones are near-instant and small, which suits sessions that
last an hour. Full clones would make every launch slow enough to be annoying.

**What it costs:** a linked clone breaks if the template changes underneath it,
so templates shouldn't be edited while sessions are running. Note also that
Proxmox rejects a `storage` argument on a linked clone, which is why we only
send it when `PROXMOX_FULL_CLONE=1`.

---

## Rate limit counters in SQLite

**Decided:** `throttle_event` rows rather than an in-memory dictionary.

**Why:** with several gunicorn workers, an in-memory counter gives each worker
its own full allowance, so the real limit is N times what you configured. It
also resets on every deploy.

**What it costs:** a write per throttled attempt, and a table that grows until
something prunes it. Nothing prunes it yet.

---

## Per-source throttle in front of the lockout

**Decided:** sign-in attempts are rate limited per IP *and* per account, in
front of the three-strikes lockout.

**Why:** the lockout on its own is a denial of service. Usernames are public on
the leaderboards, so anyone could walk the board and lock out the entire cohort
with three guesses each. The client asked for three-strikes, so we kept it and
put a throttle in front.

---

## No separate session page

**Decided:** a running challenge works inline on its own tile on the theme page.

**Also considered:** the standalone page we originally built, which had the
console link, timer, flag form and progress on a full-width layout.

**Why:** the client asked for it removed. Everything moved onto the tile rather
than being deleted.

**What it costs:** the working area is narrower now, being a tile in a grid.
Fine for a flag box and a console link, cramped if we ever want an embedded
terminal or bigger challenge briefs. That'd be a CSS change to
`.tile-challenge.is-live` rather than a structural one.

---

## Hand-written CSRF instead of Flask-WTF

**Decided:** `app/csrf.py`, about sixty lines.

**Also considered:** Flask-WTF, which does this and form handling properly.

**Why:** one fewer dependency, and the mechanism is visible in our own code,
which is worth more in an assessed project than an import statement.

**What it costs:** every form needs the hidden field added by hand, and
forgetting it is a runtime 400 rather than something the editor catches. The
test suite checks for it.

---

## Still open

These aren't decided yet, and they're the ones to bring to the next group
meeting:

- [ ] **Final schema.** Still being agreed.
- [ ] **Network isolation per session.** Every clone lands on the same bridge.
      Options are a VLAN per session or a Proxmox SDN zone. Nobody has costed
      either yet, and this is the biggest security gap we have.
- [ ] **Console authentication.** A ticket-issuing proxy, or Proxmox's
      `/access/ticket` with a short-lived per-session user. Needs a decision on
      which.
- [ ] **The Proxmox API token.** Currently `root@pam!root`, which is far more
      access than the app needs. A dedicated user with five specific privileges
      would be better, and it's about two minutes of work in the Proxmox UI.
- [ ] **Flag generation.** Shared static flags now. Per-challenge is the
      minimum; per-VM generated flags would be better and are more work.
