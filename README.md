# uDeck plugins

The official plugin repository for [uDeck](https://github.com/iillyyaa1997/udeck),
the panel at the top edge of your Mac's screen. Everything in uDeck's panel is a
plugin, and this is where plugins that somebody has read are published.

**Everything merged here was read by a maintainer first.** That is what the
**Verified** mark in uDeck means — and all it means; see
[what "Verified" does not mean](#what-verified-does-not-mean) before you
install anything.

```
plugins/
  uptime/        how long this Mac has been up, its load, and how often the card ran
```

## Installing a plugin

uDeck reads this repository by itself — you do not clone it or download
anything by hand.

1. Open uDeck's settings and go to **Plugins**. The official catalogue lists
   every plugin merged here, with what each one asks for (*"Asks to run
   sysctl"*) and a link to its folder, where its README is.
2. Press **Install**. uDeck downloads that one plugin's files — nothing else
   from the repository — checks every file against what the repository lists
   for it, and puts the folder in `~/.udeck/plugins/<id>/`.
3. Add it to a tab. A plugin that asks for anything asks you then, in uDeck's
   consent sheet, and asks again whenever a new version arrives.

When a plugin changes here, its row in Settings says *"1.1.0 available"*.
Nothing updates by itself: you press **Update** when you choose, and
**Back to 1.0.0** puts the previous version back.

To read the catalogue, uDeck asks two hosts, `api.github.com` and
`raw.githubusercontent.com`: at launch and once a day, and when you open
**Plugins** on a list more than an hour old. No account, no identifier, no
list of what you have installed.
**Settings → Plugins → Official catalogue** turns it off; installed plugins
keep running.

You can also copy a plugin folder from here into `~/.udeck/plugins/` yourself.
It runs the same, but uDeck calls it **a folder of your own**: it did not put it
there, so it claims nothing about it, and it will not tell you about updates.

How uDeck reads a repository, installs, updates and removes a plugin, and what
each refusal means, is specified in
[docs/plugin-repository.md](https://github.com/iillyyaa1997/udeck/blob/main/docs/plugin-repository.md)
in uDeck's own repository.

## What "Verified" means

A plugin is **Verified** when it was installed from the `main` branch of this
repository and its folder on your Mac is still exactly what was merged here.
Before anything is merged, a maintainer reads every file of it. Merging is
the review, and nothing reaches `main` any other way: a pull request is
required, force pushes are refused, and the check below has to pass.

The mark goes away when it stops being true. Change a file of an installed
plugin and uDeck shows it as **Modified locally** instead. A file whose name
starts with `.` is never part of a plugin, so putting one in its folder — a
`.env`, a `.git` — leaves the mark alone; uDeck still treats such a folder as
yours when it replaces or removes it, and moves it to the Trash rather than
deleting it.

## What "Verified" does not mean

**It does not mean safe.** It means a person read the code and merged it —
the strongest thing that can honestly be said about a plugin, and nothing more.

* **There is no sandbox.** A plugin is an ordinary program that uDeck starts
  as you, with everything you can do: read your files, reach the network, run
  other programs. uDeck cannot confine it, because plugins exist to run
  commands.
* **What a plugin asks for is a declaration, not a limit.** *"Asks to run
  sysctl"* is what the author says the plugin does, and what the reviewer
  checked the code against. Nothing in uDeck stops a running plugin from doing
  something it did not declare.
* **A review is done by a person**, and people miss things.

So install what you would be willing to run from a terminal, and read the
README of anything that asks for more than you expect.

## Offering a plugin

Anyone can. Open a pull request that adds one folder under `plugins/`; your
plugin stays yours — you keep the copyright, and it is published under the
Apache License 2.0 with your name on it. You certify that you have the right to
submit it by signing off your commits (`git commit -s`); there is no
contributor agreement to sign.

[CONTRIBUTING.md](CONTRIBUTING.md) has everything: the folder, the manifest,
the licence file, what the review looks at, and what the automatic check
refuses.

## The check

Every pull request into `main`, and every push to `main`, runs
[`.github/scripts/check-repo.py`](.github/scripts/check-repo.py). It holds
each plugin to the rules uDeck holds a repository to when it installs from
it, and to the stricter rules of this repository: every file readable text,
an author, a licence naming them. Run it yourself before you push:

```sh
python3 .github/scripts/check-repo.py --official
```

A pull request is judged by the check on `main` — the copy of
`.github/scripts/` at the pull request's base, and its tests — not by a copy
the pull request brings. Changing the check therefore takes two pull requests:
one that changes it, reviewed and merged, and the ones it then judges. Only the
very first pull request into `main`, when `main` has no check yet, is checked by
its own copy, and the run's log says so. The workflow that runs the check is
the pull request's own copy, as GitHub runs every `pull_request` workflow — what
guards it is the owner's review of every file, `.github/` included
([CONTRIBUTING.md](CONTRIBUTING.md#what-the-check-refuses)).

## Licence

Each plugin is licensed under the Apache License 2.0, and its copyright belongs
to its author, named in its folder's `LICENSE`. The files of the repository
itself are under the Apache License 2.0 in [LICENSE](LICENSE).
