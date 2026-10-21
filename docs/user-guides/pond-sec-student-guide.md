# Using Pond Sec: student guide

How the platform works for students. Nothing in here needs you to have read the
code.

---

## Getting an account

Register with your name, year of study and a username, and pick a password of at
least 12 characters. There's no email address involved anywhere.

<!-- TODO(team): year of study is asked for at registration but never stored.
     Decide whether to keep the field, then update the line above. -->

That last part matters more than it sounds: **there is no "forgot password"
link**. If you can't get in, you have to ask a moderator, and they'll give you a
temporary password in person. So pick something you'll actually remember.

Your username is what shows on the leaderboards, so choose accordingly.

### Waiting for approval

Once you register, an admin needs to approve your account. Until they do,
signing in takes you to a **Waiting for approval** page. You don't need to keep
this tab open. Once you've been approved, sign in again and you can get started.

---

## Signing in

Three wrong passwords locks the account for 15 minutes. The page tells you how
many attempts you have left before that happens.

If you're locked out and don't want to wait, or you've genuinely forgotten your
password, ask a moderator. If they give you a temporary password, you'll be sent
straight to a change-password page at your next sign-in, and you can't go
anywhere else until you set your own.

You'll also be signed out after an hour of doing nothing.

---

## The dashboard

Your score, your rank, how many challenges you've finished, how many flags
you've captured and how many you've attempted. Underneath that, the overall
leaderboard and a smaller board for each theme, each with a button through to
that theme.

Recent sessions are at the bottom, showing how long you spent and whether it
went down as complete or abandoned.

---

## Themes and challenges

Themes group challenges by subject, and a theme can hold any number of
challenges. Pick a theme and you'll see its challenge tiles plus its
scoreboard (headed **Theme scoreboard**), which breaks down everyone's points
per challenge.

### Read the instructions first

Each tile shows the challenge's difficulty, a short description and an
**Instructions** section. Click **Instructions** to expand it, and read it
before you launch. It tells you how to reach the challenge, where to find any
files, and what format the flag takes.

### Your virtual machine

Every challenge gives you a virtual machine (VM) to work in. It works like the
Skillable labs in your degree: the machine runs on a server, and you use it
through your browser. Do all your work inside this VM, not in the terminal on
the computer in front of you.

There are two kinds of challenge:

- **VM challenges.** The platform builds the challenge machine for you, and you
  work on it directly.
- **Red Duck challenges.** You get Red Duck, a Kali Linux workstation, and the
  challenge itself runs on a separate server that you reach from Red Duck. Any
  files for the challenge are already on Red Duck's desktop, in
  `~/Desktop/<challenge name>`. Red Duck can reach the challenge server and
  nothing else: not other students' machines, and not the internet.

### Launching

Click **Launch challenge** on a tile and the platform builds your VM. That takes
a moment. Once it's up, that tile turns into your working area.

You can only have one challenge running at a time. If you try to launch a second
one, you'll be sent back to the one you already have open.

---

## Working a challenge

The tile becomes a panel with:

- a **timer**, which starts when you launch the challenge. The server keeps
  the time, so reloading the page or opening a second tab won't change it.
- an **Open console** button, which opens your VM's console in a new tab. This
  is the machine you work in.
- a **flag box** for submitting what you find.
- a **flag list** showing what's in this challenge and what you've got.
- a **progress bar** and your recent attempts.

Each challenge's flag follows the format given in its Instructions, whatever
the hint in the flag box says. Capitalisation and stray spaces don't matter. You get
the points the first time you submit a correct flag, and submitting it again
just tells you that you already have them.

There's a limit of 20 guesses per challenge per five minutes, so you can't
brute-force your way to a flag.

---

## Finishing

Capture every flag in a challenge and it closes itself and goes down as
**complete**.

Leave early using the close button and it goes down as **abandoned**. You keep
every flag you'd already captured, so abandoning doesn't cost you points, but it
does show on your record.

Either way the machine is destroyed when the challenge closes. **Anything you
left on that VM is gone.** Notes, files, half-finished work, all of it. If you
want to keep something, get it off the machine before you close.

### Replaying a challenge

A challenge you've completed shows **Replay** instead of **Launch challenge**.
You can play it again, but flags you've already captured won't score a second
time.

---

## Scoring

Each challenge is worth a set number of points. Capture its flag to get all of
them; there are no partial points. Your score is the total of everything you've
captured.

Ties on the leaderboard are broken by who got there first, which is what the
"last solved" column is for.
