# Handover checklist

What the outgoing team (Group 6) has to finish before handing The Pond over.
Nobody from the group stays on, so anything only one of us knows or can do is
lost when we leave. That's the test for every item here: **if it isn't done
before we go, can the next team still do it?** Section 1 is the "no" list. Do it
first.

Written against `main` at `04998a1` with `fix/seeder-theme-flagformat`
(`3dc6afc`) merged. Every item links to the document that has the detail.

Fill in **Owner** and **Done** as you go. "Done" means a date and the evidence:
a commit, a command's output, or a signed record.

> **DRAFT.** Owners are blank on purpose. Assign them at the next team meeting.

---

## 1. Only we can do these

These depend on access, knowledge or machines that leave with us.

| # | Task | Why it can't wait | Detail | Owner | Done |
|---|---|---|---|---|---|
| 1.1 | Transfer ownership of the GitHub repository from the personal account `GregFromIT` to an account or organisation the client controls | Only the current owner can transfer or delete it. If that account is closed, the repo goes with it | [Access inventory](access-and-credentials.md) A1; [GitHub: transferring a repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository) | | |
| 1.2 | Hand over the Proxmox `root` password, the server BMC login and the controller and Docker host logins, in person or through the client's password manager. Never in the repo or a chat | Without `root` on `pve` nobody can run the playbook, rotate the token or recover a broken host | [Access inventory](access-and-credentials.md) B1–B4 | | |
| 1.3 | Copy the Docker host's configuration off VM 9997 into `deployment/docker-host/` | It exists only on that VM. A new team can't rebuild the Docker host without it (known issue C5) | [Docker host README](../../deployment/docker-host/README.md), "Files to copy in" | | |
| 1.4 | Commit the work done live on the controller on 20 Oct (`POND_DIRECT_ENTRY`, the Docker host moving to `10.1.21.206`, the CD drive fix), or decide to drop it | It's on no branch, and no document describes it | [Known issues](../known-issues.md) §7, "Also before handover". On the controller: `git status`, `git diff` in the checkout | | |
| 1.5 | Run the host checks on `pve` and save the output into the repo, with secrets removed | They settle every **VERIFY** in the network reference: the real bridge layout, whether `pondnc` has a subnet, which bridge each template uses. Nobody else will know what the live host looked like | [Network reference](../networking/pond-proxmox-vmid-network-reference.md) §10.3 | | |
| 1.6 | Record how each template was built: the source image, the VMID, what was changed inside it, and the Kioptrix template's VMID (still "VERIFY") | Templates can't be rebuilt from the repo. A new team inherits them as black boxes | [Build from scratch](../setup/build-from-scratch.md) phase 5 | | |
| 1.7 | Fill in every `<FILL IN>` and **TO UPDATE** in the launch guide: server location, controller login, repo path, CA file path, how "Machines without a session" rows are cleared | They're facts about our site that only we have | [Launch guide](../user-guides/launch-guide.md) | | |
| 1.8 | Create a `sysadmin` Pond Sec account for the incoming team and give them its temporary password | Every account in `db/seed_accounts.py` belongs to one of us | [Admin guide](../user-guides/pond-sec-admin-guide.md), "Accounts from the command line" | | |
| 1.9 | Do one full backup **and a restore** of the database, and record the result | A backup nobody has restored is unproven | [Maintenance and restore](../operations/maintenance-and-restore.md) §3 | | |

## 2. Security clean-up

The repository is **public**. The team charter says it should be private
(Charter §3.1, risk R10). Several items below depend on which it will be.

| # | Task | Why | Detail | Owner | Done |
|---|---|---|---|---|---|
| 2.1 | **DECISION, with the client:** make the repository private, or keep it public | Decides 2.2, 2.6 and 2.7. Charter §3.4 makes it a group decision, and the client owns the challenge content | [GitHub: repository visibility](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility) | | |
| 2.2 | Remove `the_pond.db.bak` and add `*.bak` to `.gitignore`. Then either purge it from history (`git filter-repo`) or accept that it's out | It holds every early account's password hash and every flag hash. Removing the file doesn't remove it from history | [Known issues](../known-issues.md) §7; [git-filter-repo](https://github.com/newren/git-filter-repo) | | |
| 2.3 | Reset the password of every account that was in `the_pond.db.bak` | Treat those hashes as exposed. Any account still using one of those passwords is at risk | Admin guide, "Helping someone who can't sign in" (temporary password) | | |
| 2.4 | Confirm the old `root@pam!root` API token is gone: `pveum user token list root@pam` on `pve` | It had full rights and was in `.env` files on team machines | [`pond-sec/docs/README.md`](../../pond-sec/docs/README.md), token runbook step 5 | | |
| 2.5 | Rotate everything an outgoing member might hold a copy of: the `pond@pve!launcher` token, the Proxmox `root` password, the controller's root SSH key to `pve`, `FLASK_SECRET_KEY` | After handover our copies must stop working. Rotating the secret key signs everyone out once | [Access inventory](access-and-credentials.md), "Rotating" column | | |
| 2.6 | Replace our names in `db/seed_accounts.py` | It lists five team members' real names, in a public repository | `ACCOUNTS` list | | |
| 2.7 | Decide what to do about plaintext flags in `vars/challenges/*.yml` | Anyone can read the answers while the repo is public (known issue C6) | [Authoring guide](../challenges/authoring-guide.md) §2, "Flags and points" | | |
| 2.8 | Remove the other files that shouldn't be tracked: `.provisioner.py.swp`, `odn`, `db/__pycache__/*.pyc` (`git rm --cached`) | Clutter, and the `.pyc` files show as modified after every run | [Known issues](../known-issues.md) §7 | | |

## 3. Code that should land before handover

| # | Task | Detail | Owner | Done |
|---|---|---|---|---|
| 3.1 | Merge `fix/seeder-theme-flagformat` into `main`. If this documentation is already on `main`, keep its `docs/user-guides/launch-guide.md` when git reports the conflict (`git checkout --ours docs/user-guides/launch-guide.md`) | Every doc assumes it's merged. Charter A.3: a second member reviews it first | | |
| 3.2 | Delete the duplicate flag rows in the live database | [Known issues](../known-issues.md) B5 (SQL given; back up first) | | |
| 3.3 | Fix `kioptrixlevel1.yml` (both VMs on `10.10.10.10`, and a `target → target` rule): give the target `10.10.10.20` and delete that rule. Re-seeding won't change the live database (known issue C1), so also run the SQL below it, after a backup | Known issue B6 | | |

SQL for 3.3, tested against a fresh seed. It changes the target's address and
removes the self rule. Run it with `sqlite3 the_pond.db` or Python's `sqlite3`
module, from the repo root:

```sql
UPDATE challenge_templates SET static_ip = '10.10.10.20'
 WHERE vm_role = 'target'
   AND challenge_id = (SELECT challenge_id FROM challenges WHERE title = 'kioptrixlevel1');
DELETE FROM network_rules
 WHERE from_role = 'target' AND to_role = 'target'
   AND challenge_id = (SELECT challenge_id FROM challenges WHERE title = 'kioptrixlevel1');
```

> **VERIFY:** the app sets a static IP by writing a Debian `ifupdown` file for
> `eth0` (build guide phase 5.1). Kioptrix Level 1 is, as far as we know, an old
> Red Hat-based image, which doesn't read that file. Launch it and check from
> its console which address it actually gets.

| # | Task | Detail | Owner | Done |
|---|---|---|---|---|
| 3.4 | List the attacker last in `dc1.yml`, or add a `boot_order` key to the seeder | Known issue C8 | | |
| 3.5 | Delete `vars/challenges/lockedshields.yml`, and hide the challenge if it's seeded | Known issue C3 (test challenge, flag `pond{test}`) | | |
| 3.6 | Add `python-dotenv` and `pytest` to a requirements file | Known issues B3, B4 | | |
| 3.7 | Catch errors after boot in `launch()` (a refused network rule, a full VMID range) | Known issues L14, L16: today they leave a stuck session | | |

## 4. Decisions to make or hand over

Each one is flagged **DECISION** where it appears. Either decide it and update
that document, or write down that the next team inherits it.

| # | Decision | Where it's flagged |
|---|---|---|
| 4.1 | Single-VM targets (Metasploitable 2) can only be reached through the console. Pair them with a Kali attacker, or accept console-only | [Known issues](../known-issues.md) N8; network reference §10.1 L2; build guide phase 5 |
| 4.2 | Keep the token secret in `/root/pond-launcher.token` and read it at each start, or copy it into the controller's settings and `shred` the file | Build guide phase 7; admin guide, "The Proxmox token" |
| 4.3 | Move from `flask run` to gunicorn behind a reverse proxy, and with what worker and thread counts | Launch guide step 4 |
| 4.4 | The shutdown order | Admin guide, "Starting and stopping" |
| 4.5 | Backups: how often, kept where, by whom | Admin guide, "Backups"; [maintenance and restore](../operations/maintenance-and-restore.md) |
| 4.6 | Which Proxmox identity `import_challenge.py` uses (a second scoped token is needed) | Build guide phase 5; known issue C4 |
| 4.7 | Can an admin step down? The code disagrees with itself | Admin guide, "Acting on other accounts"; known issue W5 |
| 4.8 | Whether to schedule the submission workers at all | [Submission pipeline](../challenges/submission-pipeline.md) |
| 4.9 | Whether targets may open connections back to the attacker (reverse shells) | Known issue N4 |
| 4.10 | Keep or drop the year-of-study field at registration (it's never stored) | Known issue W1; student guide TODO |
| 4.11 | Branch and review rules for the next team | [Contributing](../../CONTRIBUTING.md) |

## 5. Documents to finish

| # | Task | Owner | Done |
|---|---|---|---|
| 5.1 | Review every document in this suite against the code once 3.1 is merged. Each says at the top which commit it was written against | | |
| 5.2 | Resolve or hand over every **FIX** flag (list: `grep -rn '\*\*FIX' docs`) | | |
| 5.3 | Fill in the [access inventory](access-and-credentials.md) "Held by" column, and keep it outside the repo if names and roles shouldn't be public | | |
| 5.4 | Reconcile the participant documents kept in the Teams/Claude Project (participant FAQ, quickstart, scoring explainer, challenge instructions template) with the student and authoring guides, or retire them | | |
| 5.5 | Redraw or label the diagrams in `docs/diagrams/` (the scoring diagram still shows a points total on the user table) | | |
| 5.6 | Record the version history (`Version Plans.xlsx`) somewhere a new team will find it, such as `CHANGELOG.md` | | |

## 6. The handover itself

| # | Task | Detail | Owner | Done |
|---|---|---|---|---|
| 6.1 | Run the network acceptance tests N1–N15 against the merged code and record the results, the date and who ran each | [Network reference](../networking/pond-proxmox-vmid-network-reference.md) §9. None has been run against the merged code | | |
| 6.2 | Run one challenge of each shape end to end | [Testing](../testing/testing.md), "Testing the live system" | | |
| 6.3 | Complete the customer acceptance record: accepted functions, known limitations, recommended future work | Charter risk R13. [Known issues](../known-issues.md) is the limitations list | | |
| 6.4 | Handover briefing with the incoming team: walk through the launch guide on the real hardware, together | Charter §4.6, phase 5 | | |
| 6.5 | Confirm the incoming team can, without us: start the system, launch a challenge, add a challenge, restore a backup, rotate the token | The real test of this checklist | | |
