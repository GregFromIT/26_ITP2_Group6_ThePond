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
recalculation query is in `../database/pond-sec-data-model.md` if it ever needs fixing.

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

<<<<<<< ours
||||||| base
## Registration needs approval

**Decided:** a new account lands on `pending` and reaches nothing until an
administrator approves it. It can still sign in, and sees a page saying it is
waiting.

**Also considered:** refusing the sign-in outright until approved.

**Why not:** somebody waiting on approval can't tell a refusal from a wrong
password, so they'll keep retrying and hit the three-strikes lockout. Then
they're locked out *and* unapproved, and staff have two things to fix. Letting
them in as far as a page that says "waiting" costs nothing, because the gate
runs before every other view.

**What it costs:** there's now a queue somebody has to watch. If nobody checks
it, students sit unable to start and have no way of telling you except by
asking. The console shows a count for that reason.

**Why admin-only rather than moderators too:** approving is deciding who gets
onto the platform at all, which felt like the same weight as granting a role.
Easy to loosen later by moving `approve_accounts` in `app/roles.py`; harder to
tighten once moderators are used to having it.

---

## Challenge tiles no longer name the VM

**Decided:** the challenge list shows the name, the brief and your own progress.
The backing VM template name is gone.

**Why:** it was a spoiler — `forensics-w10` tells you what the challenge is
before you open it — and it told students more about our infrastructure naming
than they need. The view no longer even fetches the column, so it can't leak
back in by accident.

**What it costs:** nothing for students. Staff who need the VM details find them
in the console.

---

## VM uploads use SQLAlchemy while the rest of the app doesn't

**Decided:** `app/uploads.py` and `app/orm.py` are SQLAlchemy against a separate
`the_pond.db`. Everything else still goes through `app/db.py` and raw sqlite3.

**Why:** the group's newer database work is already SQLAlchemy with a `pond`
bind key, and uploads are new, so there was no reason to add to the old layer.
`app/orm.py` mirrors the group's `db/orm.py` exactly so those model files work
here unchanged.

**What it costs:** two database layers, two SQLite files, and no way to join
across them. Temporary, and on the to-do list. Anyone new to the code has to be
told which layer owns what, which is why both module docstrings say so.

---

## VM uploads accept disk images only

**Decided:** `.vmdk`, `.vhd`, `.vhdx`, `.vdi`, `.qcow`, `.qcow2`, `.raw` and
`.img`. Anything else is refused.

**Why those:** the formats the group expects to move between VMware,
VirtualBox, Hyper-V and Proxmox. `.img` is in because it is the same thing as
`.raw` under another name and someone will inevitably have one.

**A mismatch is flagged, not refused.** The extension proves nothing on its own,
because the client picks the filename, so the first bytes are read and compared
against it. If they disagree the row is marked and the uploader told, rather than
the upload being rejected: the file may be perfectly good and only the name
wrong, and an administrator is better placed to judge that than a header check
is. Raw images have no signature at all, so "no signature" is a normal answer
rather than a failure.

**What it still costs:** a determined administrator could rename anything to
`.qcow2` and upload it. That is accepted, because the people with this permission
are the ones running the platform. The controls that matter do not depend on the
format: files land outside the web root and are never served back, so nothing
uploaded can be requested by URL; the stored name is generated rather than taken
from the browser; there is a size cap enforced while streaming; and every upload
is audited by name with a SHA-256 of the contents.

---

## Pasted URLs are downloaded, with the SSRF guards written down

**Decided:** pasting a URL downloads the image and stores it exactly as a chosen
file would. The client asked for the two buttons to behave the same way, and
they now do.

**The risk this creates, and what handles it:** making a server fetch a URL a
user supplied is server-side request forgery. This server is an awkward place
for it — same network as the Proxmox cluster, and the process holds an API
token — so an unguarded version would let anyone with the uploads page make the
platform request `https://10.1.21.151:8006/api2/json/...` or a metadata endpoint
and store the reply.

`app/fetcher.py` therefore keeps all of it in one reviewable place: http and
https only; the hostname resolved and **every** address it resolves to checked
against private, loopback, link-local, multicast and reserved ranges before
connecting; redirects followed by hand with the same check on each hop, because
a public host redirecting to 127.0.0.1 would otherwise defeat the first check;
a hop limit; connect and read timeouts; and a size cap enforced while streaming.

**Why private addresses are refused by default:** a teaching network may well
host images internally, so `UPLOAD_FETCH_ALLOW_PRIVATE=1` exists — but as a
conscious switch with its own setting and its own comment, rather than a default
that quietly opens the platform up. Loopback and link-local stay refused even
then: those are never a file server, and they are exactly the two that turn this
into a way to read the server's own secrets.

**What it still costs:** with the setting on, anyone who can reach the uploads
page can make the platform request any internal address and store the reply.
That is administrator-only and audited, but it is a real capability and should
be understood before the switch is flipped.

---

## Unapproved accounts are told their password was right

**Decided:** signing in to a pending account says so explicitly, rather than
just bouncing to the waiting page.

**Why:** without it the person can't tell "waiting for approval" from "typed my
password wrong", so they retry, and three retries locks the account. Then
they're locked out *and* unapproved and staff have two things to fix.

**Is this an information leak?** Slightly — it confirms the password for an
account that can't be used. It was judged worth it because the account is inert
until an administrator approves it, and the alternative pushed real students
into lockouts. A wrong password on a pending account still reads as a wrong
password, which the tests check.

---

## Quarantine and sanitisation happen in Proxmox, not here

**Decided:** the platform accepts an image, catalogues it, and does nothing
else to it. No quarantine, no scanning, no sanitisation. That work happens in
Proxmox.

**Why:** Proxmox is where the image actually becomes a running machine, and it
is where the isolation exists. Building a second, weaker version of the same
check in the web app would mean two places to maintain and a real risk that
somebody trusts the weaker one.

**What it costs, stated plainly because it should not be a surprise later:**

- An uploaded image is not scanned. The extension is checked and the first few
  bytes are read, which catches mistakes, not a determined administrator.
- A registered URL is not fetched or checked at all. It is a link in a table.
- `status` is a label staff set for each other. Nothing behaves differently
  based on it — an image marked `checked` is not treated differently by any code
  path.

So anything reaching Proxmox from here is untrusted. That is fine as long as
somebody knows it, which is why it is written in the module docstring, on the
uploads page, and in the README to-do list rather than left implicit.

**Still to settle:** who does the checking, and what counts as done. Worth
agreeing before the handover, not after.

**Why the server still does not fetch a registered URL:** that is a separate
question from quarantine and Proxmox does not help with it. Downloading an
arbitrary URL happens on the web server, inside the trusted network, from a
process holding a Proxmox API token — see the decision below.

---

=======
## Registration needs approval

**Decided:** a new account lands on `pending` and reaches nothing until an
administrator approves it. It can still sign in, and sees a page saying it is
waiting.

**Also considered:** refusing the sign-in outright until approved.

**Why not:** somebody waiting on approval can't tell a refusal from a wrong
password, so they'll keep retrying and hit the three-strikes lockout. Then
they're locked out *and* unapproved, and staff have two things to fix. Letting
them in as far as a page that says "waiting" costs nothing, because the gate
runs before every other view.

**What it costs:** there's now a queue somebody has to watch. If nobody checks
it, students sit unable to start and have no way of telling you except by
asking. The console shows a count for that reason.

**Why admin-only rather than moderators too:** approving is deciding who gets
onto the platform at all, which felt like the same weight as granting a role.
Easy to loosen later by moving `approve_accounts` in `app/roles.py`; harder to
tighten once moderators are used to having it.

---

## Challenge tiles no longer name the VM

**Decided:** the challenge list shows the name, the brief and your own progress.
The backing VM template name is gone.

**Why:** it was a spoiler — `forensics-w10` tells you what the challenge is
before you open it — and it told students more about our infrastructure naming
than they need. The view no longer even fetches the column, so it can't leak
back in by accident.

**What it costs:** nothing for students. Staff who need the VM details find them
in the console.

---

## VM uploads use SQLAlchemy while the rest of the app doesn't

**Decided:** `app/uploads.py` and `app/orm.py` are SQLAlchemy against a separate
`the_pond.db`. Everything else still goes through `app/db.py` and raw sqlite3.

**Why:** the group's newer database work is already SQLAlchemy with a `pond`
bind key, and uploads are new, so there was no reason to add to the old layer.
`app/orm.py` mirrors the group's `db/orm.py` exactly so those model files work
here unchanged.

**What it costs:** two database layers, two SQLite files, and no way to join
across them. Temporary, and on the to-do list. Anyone new to the code has to be
told which layer owns what, which is why both module docstrings say so.

---

## VM uploads accept disk images only

**Decided:** `.vmdk`, `.vhd`, `.vhdx`, `.vdi`, `.qcow`, `.qcow2`, `.raw` and
`.img`. Anything else is refused.

**Why those:** the formats the group expects to move between VMware,
VirtualBox, Hyper-V and Proxmox. `.img` is in because it is the same thing as
`.raw` under another name and someone will inevitably have one.

**A mismatch is flagged, not refused.** The extension proves nothing on its own,
because the client picks the filename, so the first bytes are read and compared
against it. If they disagree the row is marked and the uploader told, rather than
the upload being rejected: the file may be perfectly good and only the name
wrong, and an administrator is better placed to judge that than a header check
is. Raw images have no signature at all, so "no signature" is a normal answer
rather than a failure.

**What it still costs:** a determined administrator could rename anything to
`.qcow2` and upload it. That is accepted, because the people with this permission
are the ones running the platform. The controls that matter do not depend on the
format: files land outside the web root and are never served back, so nothing
uploaded can be requested by URL; the stored name is generated rather than taken
from the browser; there is a size cap enforced while streaming; and every upload
is audited by name with a SHA-256 of the contents.

---

## Pasted URLs are downloaded, with the SSRF guards written down

**Decided:** pasting a URL downloads the image and stores it exactly as a chosen
file would. The client asked for the two buttons to behave the same way, and
they now do.

**The risk this creates, and what handles it:** making a server fetch a URL a
user supplied is server-side request forgery. This server is an awkward place
for it — same network as the Proxmox cluster, and the process holds an API
token — so an unguarded version would let anyone with the uploads page make the
platform request `https://10.1.21.151:8006/api2/json/...` or a metadata endpoint
and store the reply.

`app/fetcher.py` therefore keeps all of it in one reviewable place: http and
https only; the hostname resolved and **every** address it resolves to checked
against private, loopback, link-local, multicast and reserved ranges before
connecting; redirects followed by hand with the same check on each hop, because
a public host redirecting to 127.0.0.1 would otherwise defeat the first check;
a hop limit; connect and read timeouts; and a size cap enforced while streaming.

**Why private addresses are refused by default:** a teaching network may well
host images internally, so `UPLOAD_FETCH_ALLOW_PRIVATE=1` exists — but as a
conscious switch with its own setting and its own comment, rather than a default
that quietly opens the platform up. Loopback and link-local stay refused even
then: those are never a file server, and they are exactly the two that turn this
into a way to read the server's own secrets.

**What it still costs:** with the setting on, anyone who can reach the uploads
page can make the platform request any internal address and store the reply.
That is administrator-only and audited, but it is a real capability and should
be understood before the switch is flipped.

---

## Unapproved accounts are told their password was right

**Decided:** signing in to a pending account says so explicitly, rather than
just bouncing to the waiting page.

**Why:** without it the person can't tell "waiting for approval" from "typed my
password wrong", so they retry, and three retries locks the account. Then
they're locked out *and* unapproved and staff have two things to fix.

**Is this an information leak?** Slightly — it confirms the password for an
account that can't be used. It was judged worth it because the account is inert
until an administrator approves it, and the alternative pushed real students
into lockouts. A wrong password on a pending account still reads as a wrong
password, which the tests check.

---

## Quarantine and sanitisation happen in Proxmox, not here

**Decided:** the platform accepts an image, catalogues it, and does nothing
else to it. No quarantine, no scanning, no sanitisation. That work happens in
Proxmox.

**Why:** Proxmox is where the image actually becomes a running machine, and it
is where the isolation exists. Building a second, weaker version of the same
check in the web app would mean two places to maintain and a real risk that
somebody trusts the weaker one.

**What it costs, stated plainly because it should not be a surprise later:**

- An uploaded image is not scanned. The extension is checked and the first few
  bytes are read, which catches mistakes, not a determined administrator.
- A registered URL is not fetched or checked at all. It is a link in a table.
- `status` is a label staff set for each other. Nothing behaves differently
  based on it — an image marked `checked` is not treated differently by any code
  path.

So anything reaching Proxmox from here is untrusted. That is fine as long as
somebody knows it, which is why it is written in the module docstring, on the
uploads page, and in the README to-do list rather than left implicit.

**Still to settle:** who does the checking, and what counts as done. Worth
agreeing before the handover, not after.

**Why the server still does not fetch a registered URL:** that is a separate
question from quarantine and Proxmox does not help with it. Downloading an
arbitrary URL happens on the web server, inside the trusted network, from a
process holding a Proxmox API token — see the decision below.

---

## The app refuses root and over-privileged Proxmox tokens

**Decided:** The Pond runs as its own user, `pond@pve`, through a
privilege-separated token whose ACLs are created by
`playbooks/pond_least_privilege.yml`. The adapter (`app/proxmox.py`) refuses to
connect with a `@pam` token, a malformed ID, a missing secret or an empty pool.
It verifies TLS (mandatory in production, against `PROXMOX_CA_BUNDLE` or the
system store), puts every clone in one pool, never touches the protected VMs
300-303, and before each launch reads `GET /access/permissions` and refuses if the
token holds anything beyond an allowlist. Production refuses to start with
settings the adapter would refuse anyway.

**Also considered:**

- *Documenting least privilege only.* That is what we had, and the root token
  stayed because nothing stopped it. A rule nobody enforces is a suggestion.
- *A start-up check only.* It misses a token that is widened after the app
  started, and it says nothing when an operator swaps the token in a running
  environment. The per-launch check (cached) covers both.
- *A hard allowlist in Proxmox only.* Proxmox is the real boundary and the
  playbook sets it, but an app that also checks fails loudly when someone widens
  the token by hand, instead of silently holding the extra power.

**Why:** a root token turns any bug in the app, or any read of its environment,
into control of every VM on the cluster including other projects' machines. Both
layers are cheap; either alone leaves a gap.

**What it costs:**

- One extra API call per launch, once per five minutes per worker.
- A new privilege need means editing both the playbook and `allowed_privileges()`
  in `app/proxmox.py`. `tests/test_credentials.py` feeds the playbook's grants
  through the app's own check, so the two cannot drift unnoticed.
- Development cannot use a root token; it needs the pond token like production
  does (or the simulate backend).
- A token that is too wide blocks launches until it is fixed. Teardown is not
  blocked, so running VMs are never orphaned.

---

>>>>>>> theirs
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
<<<<<<< ours
- [ ] **The Proxmox API token.** Currently `root@pam!root`, which is far more
      access than the app needs. A dedicated user with five specific privileges
      would be better, and it's about two minutes of work in the Proxmox UI.
||||||| base
- [ ] **The Proxmox API token.** Currently `root@pam!root`, which is far more
      access than the app needs. A dedicated user with five specific privileges
      would be better, and it's about two minutes of work in the Proxmox UI.
- [ ] **Fetching images by URL safely.** Registered links have to be downloaded
      by hand for now. See the decision above for what the fetch would need.
- [ ] **Merging the two database layers.** Which way, and when.
=======
- [x] **The Proxmox API token.** Done in code: the app refuses root and
      over-privileged tokens, see "The app refuses root and over-privileged
      Proxmox tokens" above. Running the playbook on the node and revoking the
      old token are still to do.
- [ ] **Fetching images by URL safely.** Registered links have to be downloaded
      by hand for now. See the decision above for what the fetch would need.
- [ ] **Merging the two database layers.** Which way, and when.
>>>>>>> theirs
- [ ] **Flag generation.** Shared static flags now. Per-challenge is the
      minimum; per-VM generated flags would be better and are more work.
