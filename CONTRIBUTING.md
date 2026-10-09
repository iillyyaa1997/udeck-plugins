# Adding a plugin

Anyone can offer a plugin: open a pull request that adds one folder under
`plugins/`. A maintainer reads every file of it, and merging it is what
publishes it — once it lands on `main`, every uDeck lists it at its next
refresh, marked **Verified**. Everything below exists so that the reading can be done,
and so that everybody's rights in the result are clear.

Write and try the plugin first. uDeck's
[walkthrough](https://github.com/iillyyaa1997/udeck/blob/main/docs/writing-a-plugin.md)
goes from an empty folder to a card on the panel, and
[the plugin contract](https://github.com/iillyyaa1997/udeck/blob/main/docs/plugin-api.md)
is the reference for every field. A plugin works from `~/.udeck/plugins/<id>/`
exactly as it will once installed from here, so that is where to test it.

## The folder

```
plugins/
  your-plugin/
    manifest.json        required
    manifest.ru.json     recommended: one manifest.<lang>.json per language
    README.md            required
    LICENSE              required
    your-plugin.sh       the producer, committed as executable
    icon.png             optional
    screenshot-1.png     optional, up to three
```

* **Directly under `plugins/`**, and the folder's name is the plugin's `id`:
  lowercase letters, digits and `. _ -`, up to 64 characters, starting with a
  letter or a digit. Not `plugins/<group>/<id>/`: on a Mac every plugin is one
  folder in `~/.udeck/plugins/`, keyed by that id.
* **Self-contained.** Only the plugin's own folder is installed. A script that
  sources `../common/lib.sh` works here and fails on every machine — copy what
  you need instead.
* **The folder on a Mac is exactly this folder.** Nothing is added to it on the
  way, and a plugin must not write into it either: a file it writes makes
  uDeck call it *modified*. Anything a plugin keeps goes in `UDECK_CACHE_DIR`,
  which is uDeck's to create and to remove with the plugin.

## The manifest

`manifest.json` is the plugin contract's manifest, with these held firmly:

```json
{
  "id": "your-plugin",
  "name": "Your plugin",
  "version": "1.0.0",
  "api": 1,
  "kind": "poll",
  "description": "What the card shows, in one sentence.",
  "author": "Your Name",
  "run": ["./your-plugin.sh"],
  "interval": 60,
  "timeout": 2,
  "permissions": { "exec": ["sysctl"] }
}
```

* **`version`** is `MAJOR.MINOR.PATCH`: three whole numbers, no leading zeros,
  no `v`, no `-beta`. `1.2.0` and `10.0.3` are versions; `1.2`, `v1.2.0` and
  `1.2.0-beta` are not. uDeck compares versions to offer an update and to go
  back to an earlier one, so a plugin here must have one it can compare.
  Raise it whenever you change the plugin — PATCH for a fix, MINOR for
  something new (a setting, a row, a permission), MAJOR when something people
  relied on changes shape (a setting's `key` renamed or removed). A new version
  asks the operator's permission again.
* **`api`** is `1`, the contract this uDeck speaks.
* **`minUDeck`** is optional: the lowest uDeck release that has everything
  your plugin uses, in the same `MAJOR.MINOR.PATCH` form. Leave it out unless
  you use something recent.
* **`author`** is your name, exactly as it appears in `LICENSE`. It is the name
  the copyright belongs to.
* **`permissions`** says honestly what the plugin does: a plugin that runs
  `sysctl` declares `"exec": ["sysctl"]`, one that reads a file declares
  `read`. It is what the operator agrees to, and what review checks the code
  against.
* **Only the fields the contract defines.** A misspelt `permisions` is ignored
  by uDeck — and the plugin then runs without asking for anything — so the
  check refuses a field it does not know, in the manifest and in translations.

## LICENSE

Every plugin here is licensed under the Apache License 2.0, and **its author
keeps the copyright**. The `LICENSE` file says whose it is: the line
`Copyright <year> <author>`, with the same name as the manifest's `author`, a
blank line, then the unmodified text of the Apache License 2.0 — the same text
as the [LICENSE](LICENSE) at the top of this repository. From the top of your
fork:

```sh
{ printf 'Copyright 2026 Your Name\n\n'; cat LICENSE; } > plugins/your-plugin/LICENSE
```

The file travels with the folder onto every machine that installs it.

## What the files may be

**Text, UTF-8.** A binary cannot be read by a reviewer, so it cannot be
verified: no compiled programs, libraries or archives. The one exception is
pictures, in PNG and nothing else:

* `icon.png`, at most 512×512;
* `screenshot-1.png`, `screenshot-2.png`, `screenshot-3.png`, at most 1 MiB each.

All of them at the top of the plugin's folder, never executable. A PNG is recognised by
its content, so a PNG under another name is refused, and so is another file
under one of these names. **No SVG**: it can carry script.

And for every plugin, here or in any repository uDeck reads:

* **The producer is committed as executable** (git mode `100755`). If git has
  it as `100644`, run `git update-index --chmod=+x plugins/your-plugin/your-plugin.sh`
  and commit again.
* **Names** use only `A–Z a–z 0–9 . _ -`, do not start with `.`, and no two
  names in one folder differ only in letter case — a Mac's disk would see them
  as one file.
* **No symbolic links, no submodules, no Git LFS**, and no `.gitattributes`
  setting `export-ignore`, `export-subst` or `filter` on anything in `plugins/`.
* **Unix line endings in executables**: a script saved with Windows line
  endings fails on a Mac with *bad interpreter: /bin/sh^M*.
* **At most** 200 files, 10 MiB in all, 5 MiB for one file, folders nested 8
  deep.

## Signing off

Every commit in your pull request carries a `Signed-off-by:` line, which
`git commit -s` adds:

```
Signed-off-by: Your Name <you@example.com>
```

With it you certify the [Developer Certificate of Origin](https://developercertificate.org)
— in short, that you wrote the change or otherwise have the right to submit it
under the plugin's licence. That is all: no rights move to the repository's
owner, and there is no contributor agreement to sign.

Forgot it? For the last commit:

```sh
git commit --amend -s --no-edit
git push --force-with-lease
```

For every commit of the branch, where `main` is the branch you started from:

```sh
git rebase --signoff main
git push --force-with-lease
```

**A merge commit is a commit of your pull request too**, and the check reads it
like any other. GitHub's **Update branch** button, in its *Update with merge
commit* form, adds one, and unless that commit carries a `Signed-off-by:` line
the check turns red.
Bring your branch up to date with **Update with rebase** instead (the arrow
next to the button), or on your machine:

```sh
git fetch origin
git rebase --signoff origin/main
git push --force-with-lease
```

## What the check refuses

Every pull request runs two checks of the same rules:
[`.github/scripts/check-repo.py`](.github/scripts/check-repo.py), and
`udeck-plugin check-repo --strict --official`, from the uDeck release that
[`.github/udeck-plugin.lock`](.github/udeck-plugin.lock) names. Both must pass,
and they must say the same of everything they both check —
[`.github/scripts/compare-checks.py`](.github/scripts/compare-checks.py)
compares them — before anything is merged. `udeck-plugin` is the check that
stays: it is built from the code uDeck itself runs, and it has three rules
`check-repo.py` does not (18–20 below); `check-repo.py` goes once the two have
agreed long enough. The copies that run — the scripts with their tests, and the
release the lock file names — are the ones on `main` as GitHub merges your
pull request into it, the check already there, and not the ones in your
branch: a pull request that changes the check is judged by the check it
changes, and its own version applies from the pull request after it is merged.
When your pull request changes `.github/scripts/`, `LICENSE` or the lock file,
the copies merging leaves are tried too, last, once the check already there
has given its verdict: their tests, beside the lock file merging leaves,
and the two scripts themselves, beside the release that lock file names, on the
repository as merging leaves it and on your branch's head with its commits.
They can turn the pull request red, never green: copies or a release that fail
there would fail `main`'s next run, or pull requests like yours after it, and
are red before they are merged rather than `main` after it. What a pull
request changes is what merging it changes — the merge GitHub makes, against
`main` — so a branch that only lags behind `main` is not taken to change or
remove what `main` has since changed. A pull request that changes the lock
file is also checked by the release it names, which has to pass and to agree
with `check-repo.py` as well, and its lock file has to be what that release
has (`udeck-plugin pin --check`). A release with a rule the comparison does
not know waits for the comparison to learn it first, in a pull request of its
own. A pull request that would take `check-repo.py`, `compare-checks.py`,
either of their test files, the lock file or `LICENSE` off `main`, or leave
one of them a file of zero bytes, a folder or a symbolic link, is refused: the
next pull request takes them from there. What those files hold, down to a
single blank line, is left to the review. A name in `.github/scripts/` that
uses anything but `A–Z a–z 0–9 . _ -` or starts with `.` is refused too.
(Only the first pull request into `main`, when `main` had no check yet, was
checked by its own copy, and the one that brought the lock file and the
comparison by its own of those; the log said so.) The checks run apart from
your branch's files — isolated Python, started outside the checkout, and a
command whose archive is held to the sum in the lock file before it runs — so a
file in your branch cannot stand in for part of them. What they cannot guard is
the workflow itself: GitHub runs `.github/workflows/validate.yml` as its merge
of your pull request has it — your own version, if you change the file — so a
pull request could rewrite it. What stands in the way is the owner's review:
`CODEOWNERS` puts every file, `.github/` included, in front of him, and branch
protection on `main` lets only the maintainers merge — merging is the review. A
pull request that touches `.github/` is read with that in mind.

They read the repository as it is committed, so commit first, then run them
yourself:

```sh
python3 .github/scripts/check-repo.py --official
udeck-plugin check-repo --strict --official
```

`udeck-plugin` comes inside uDeck.app (**Install command** under Settings →
Plugins), and as an archive for macOS and Linux with every
[uDeck release](https://github.com/iillyyaa1997/udeck/releases). Before you
push, `--base origin/main --head HEAD` checks your branch as the pull request
will be: rule 18 against `main`, and the sign-off of every commit on it.

| # | Refused |
|---|---|
| — | `udeck-plugins.json` missing, not `format` 1, or with a field the format does not have |
| 1 | a plugin folder whose name is not a plugin id |
| 2 | anything but folders directly inside `plugins/` |
| 3 | a `manifest.json` that is missing, is not valid JSON, has an `id` other than the folder's name, or that uDeck would refuse to run |
| 4 | a `version` or `minUDeck` that is not `MAJOR.MINOR.PATCH` |
| 5 | a relative `run` command that is not a file in the folder committed as executable |
| 6 | a symbolic link or a submodule |
| 7 | a name outside `A–Z a–z 0–9 . _ -`, starting with `.`, longer than 255 bytes, or differing from another only in case |
| 8 | more than 200 files, 10 MiB in all, 5 MiB in one, or folders nested more than 8 deep |
| 9 | a Git LFS pointer, or an `export-ignore`, `export-subst` or `filter` attribute on anything in `plugins/` |
| 10 | no `README.md` |
| 11 | no `manifest.<lang>.json` — a warning, not a refusal |
| 12 | a field in `manifest.json` or a translation that the contract does not define, or a translation that is not valid |
| 13 | a carriage return in an executable file |
| 14 | a file that is not UTF-8 text, other than the icon and screenshots above |
| 15 | no `author` in the manifest |
| 16 | a `LICENSE` that is not the copyright line, a blank line and the Apache License 2.0 |
| 17 | a commit without `Signed-off-by:` |
| 18 | a plugin whose folder changed and whose `version` did not go up (`udeck-plugin` only) |
| 19 | a `minUDeck` below the uDeck release that has everything the plugin uses (`udeck-plugin` only; one that does nothing is a warning) |
| 20 | a `name` or `description`, in the manifest or a translation, that is more than one line or holds a control character (`udeck-plugin` only) |

The rules and the reasons for each are in uDeck's
[docs/plugin-repository.md](https://github.com/iillyyaa1997/udeck/blob/main/docs/plugin-repository.md).

## What the review looks at

The check only knows the shape of a plugin. The review is a person reading
every file, and asking:

* **Does the code do what the README and the manifest say, and nothing else?**
  Every command it runs, every file it reads, every host it talks to is either
  declared in `permissions` or the plugin does not do it. A plugin that
  understates what it does is not merged.
* **Can it be read?** No obfuscated or minified code, nothing downloaded and
  then run, nothing decoded from a blob at run time. If the reviewer cannot
  follow it, it cannot be verified.
* **Does it behave?** One JSON card on standard output, diagnostics on standard
  error, no background processes left behind, nothing that can block forever,
  an `interval` and a `timeout` that suit what it does, and nothing written
  outside `UDECK_CACHE_DIR` — as
  [the contract](https://github.com/iillyyaa1997/udeck/blob/main/docs/plugin-api.md#writing-a-producer-that-behaves)
  describes.
* **Does it need only what a Mac has?** If it needs something installed — a
  language runtime, a command-line tool — its README says so, and it says so on
  the card too when the thing is missing, rather than failing blank.
* **Is its README enough to decide whether to install it?** What the card
  shows, what it needs, what it runs.

Changes to a plugin that is already here are reviewed the same way, with its
`version` raised.

## After the merge

Merges are squashed, so each pull request is one commit on `main` and a
plugin's history reads one entry per change. There is no release step: the
catalogue in every uDeck picks the change up at its next refresh.
