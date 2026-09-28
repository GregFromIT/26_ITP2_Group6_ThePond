# Using Pond Sec

How the platform works from the outside, for students and for the staff running
a class. Nothing in here needs you to have read the code.

---

## For students

### Getting an account

Register with your name, year of study and a username, and pick a password of at
least 12 characters. There's no email address involved anywhere.

**Your account has to be approved before you can use it.** Once you register you
can sign in, but you'll land on a page saying you're waiting and won't be able to
reach the challenges. An administrator approves new accounts.

If your account isn't approved yet, signing in tells you so and says your
password was correct — so you know not to keep trying different passwords. Sign
in again later and you'll go straight through.

That last part matters more than it sounds: **there is no "forgot password"
link**. If you can't get in, you have to ask a moderator or your course
administrator, and they'll give you a temporary password in person. So pick
something you'll actually remember.

Your username is what shows on the leaderboards, so choose accordingly.

### Signing in

Three wrong passwords locks the account for 15 minutes. The page tells you how
many attempts you have left before that happens.

If you're locked out and don't want to wait, ask staff. They can unlock it, or
issue you a temporary password if you've genuinely forgotten it. When they do,
you'll be sent straight to a change-password page at your next sign-in and you
can't go anywhere else until you set your own.

You'll also be signed out after an hour of doing nothing.

### The dashboard

Your score, your rank, how many challenges you've finished, how many flags
you've captured and how many you've attempted. Underneath that, the overall
leaderboard and a smaller board for each theme, each with a button through to
that theme.

Recent sessions are at the bottom, showing how long you spent and whether it
went down as complete or abandoned.

### Themes and challenges

A theme is a subject area. Each one holds six challenges that get harder as you
go up. Pick a theme and you'll see the six challenge tiles plus the theme
scoreboard, which breaks down everyone's points per challenge.

Click **Launch challenge** on a tile and the platform builds a virtual machine
for you on Proxmox. That takes a moment. Once it's up, that tile turns into your
working area.

You can only have one challenge running at a time. If you try to launch a second
one, you'll be sent back to the one you already have open.

### Working a challenge

The tile becomes a panel with:

- a **timer**, which starts the moment the machine is built. The server keeps
  the time, so reloading the page or opening a second tab won't change it.
- an **Open console** button, which opens the machine's console in a new tab.
- a **flag box** for submitting what you find.
- a **flag list** showing what's in this challenge and what you've got.
- a **progress bar** and your recent attempts.

Flags look like `flag{something_here}`. Capitalisation and stray spaces don't
matter. You get the points the first time you submit a correct flag, and
submitting it again just tells you that you already have them.

There's a limit of 20 guesses per challenge per five minutes, so you can't
brute-force your way to a flag.

### Finishing

Capture every flag in a challenge and it closes itself and goes down as
**complete**.

Leave early using the close button and it goes down as **abandoned**. You keep
every flag you'd already captured, so abandoning doesn't cost you points, but it
does show on your record.

Either way the machine is destroyed when the challenge closes. **Anything you
left on that VM is gone.** Notes, files, half-finished work, all of it. If you
want to keep something, get it off the machine before you close.

### Scoring

Each flag is worth points, and later challenges are worth more. Your score is
the total of everything you've captured.

Each theme also has a weighting multiplier, applied to that theme's board. It
doesn't change your overall total.

Ties on the leaderboard are broken by who got there first, which is what the
"last solved" column is for.

---

## For staff

Moderators and administrators get a **Staff** link in the top bar, leading to
the console at `/admin`.

### What each role can do

Moderators can see accounts and sessions, unlock or lock an account, issue
temporary passwords, force-close a session and read the audit log.

Administrators can do all of that, and are the only ones who can change
somebody's role.

Two limits apply to everyone. You can't act on an account at your own level or
above, so one moderator can't unlock or reset another, and neither can touch an
administrator. And nobody can edit scores or delete flag awards, from any role.
There's no button for it, deliberately.

Administrators don't appear on any leaderboard. Moderators do.

### The console

The front page gives you a count of live sessions, machines up, accounts,
moderators, admins, anyone locked out and anyone waiting on approval, plus a list
of locked accounts, a list of what's running right now, and the latest audit
events.

### Approving new accounts

Administrators only, at **Approvals**. New registrations queue up there, oldest
first, and until somebody approves them those people can sign in but reach
nothing. Worth checking at the start of a class — a student sitting on the
waiting page has no way to tell you except by asking.

Rejecting keeps the account rather than deleting it, so nobody can re-register
that username, and you can approve it later if the decision changes. You can't
reject an account that already holds moderator or admin powers; use the role
controls for that.

### Uploading VM images

Administrators only, at **VM uploads**. Pick a VM file from your machine, give it
a name and any notes, and upload it. Large files take a while and the page sits
waiting until it finishes, so don't close the tab.

Each image gets a status label you can set: *new*, *checked* or *rejected*, with
an optional note. Your name and the time are recorded against whatever you set.

**These are notes between staff, not a control.** Nothing in the platform
behaves differently based on them. All uploads require appropriate quarantine
and sanitisation checks as this system does not have any in place currently.
Pasting a link also makes this server fetch that address itself, which is a
server-side request forgery risk — only paste links you trust.

You can also paste a URL instead of choosing a file. The server downloads the
image and stores it the same way, with the same formats and size limit, and
records where it came from. Large images take a while and the page waits until
the download finishes.

If a URL is refused for being a private or reserved address, that's the SSRF
guard doing its job. Images on an internal server need
`UPLOAD_FETCH_ALLOW_PRIVATE=1` set by whoever runs the platform.

Disk images only: `.vmdk`, `.vhd`, `.vhdx`, `.vdi`, `.qcow`, `.qcow2`, `.raw`
and `.img`. Anything else is refused.

The first bytes of the file are also read and compared against the extension. If
they disagree the row gets a **contents differ** marker — the upload still goes
through, because the file may be fine and only the name wrong, but it's worth
checking before anyone builds a template from it.

You can also register an image by URL instead of uploading it. That records the
link only; **the server does not download the file**, so somebody still has to
fetch it, check it and upload it here. That's deliberate — see the decisions
document if you want the reasoning.

Files are stored on the server outside the web root and are never served back
over the web, and every upload is recorded in the audit log with your name on
it.

### Helping someone who can't sign in

Open **Accounts**, find them, and open their account.

If they're locked out from failed attempts, hit **Unlock this account** and they
can try again straight away.

If they've forgotten their password, hit **Issue a temporary password**. One
appears on your screen, something like `lantern-ribbon-velvet-78`.

Read it to them, or hand it over on paper. **You will not see it again** — it
isn't stored anywhere readable and there's no way to look it up. If it gets
lost, just issue another one. They'll be forced to set their own password at
their next sign-in, and issuing one also clears any lockout.

Don't send this over anything you wouldn't send a password over. It is one, for
the few minutes it exists.

### Locking someone out

There's a lock button with an hours field if you need to stop somebody using the
platform. It takes effect immediately, including on a session they already have
open, so they'll be kicked out on their next click rather than at their next
sign-in.

### Changing roles

Administrators only, on the account page.

An administrator can step down to moderator, but not if they're the only
administrator left. The console will refuse that, because recovering from it
would need somebody with shell access to the server.

The first administrator is created from the command line. If every admin account
is ever lost, that's the way back in:

```bash
flask --app wsgi set-role <username> admin
```

### Sessions and machines

**Sessions** shows everything running: who, which challenge, when it started and
which VM. Close anything that's been left open, which frees the machine for the
next class. It goes down as abandoned, and the student keeps every flag they'd
already banked.

Underneath is a list of machines with no live session attached. Those are
usually clones that failed to tear down and need clearing in Proxmox by hand.

### The audit log

Every sign-in, failed attempt, lockout, role change, temporary password, and
session launch or close. Filterable by event type.

It's read-only. Nothing in the platform can edit or delete a row, which is what
makes it worth having.

Note what's recorded when you act on a student: the audit entry names *you*.
That's deliberate, because staff can effectively take over any account below
their rank, and the log is what keeps that accountable.

---

## Known limits

Worth knowing before you run a class on this:

- **Machines aren't isolated from each other yet.** Every VM currently sits on
  the same network bridge, so students can reach each other's machines. This is
  being worked on and is the biggest outstanding item.
- **The console doesn't log you in to Proxmox.** The link checks the session is
  yours, but students still have no Proxmox credential of their own, so this
  isn't fully wired up yet.
- **Nothing reaps idle machines.** A session left open holds its VM until
  somebody closes it, by hand or from the staff console.
- **Challenge content is in the code.** Adding or editing themes, challenges and
  flags means editing `app/seed.py`. There's no web interface for it.
- **All the seeded challenges currently share the same flags.** Fine for a demo,
  no good for assessment.
- **All uploads require appropriate quarantine and sanitisation checks, as this
  system does not have any in place currently.** The extension and first bytes
  are checked, which catches mistakes rather than a determined administrator.
- **Pasting a link makes the server fetch that address itself**, which is a
  server-side request forgery risk. Private, loopback, link-local and reserved
  addresses are refused and every redirect is re-checked, but anyone who can
  reach the uploads page can still make the platform request any public address
  and store the reply.
- **Downloaded images are no more checked than uploaded ones.** The URL is
  guarded against pointing at internal addresses, but the file that comes back
  is not scanned.
- **Nothing cleans up uploaded images.** They stay on the server until an
  administrator deletes them from the uploads page.
