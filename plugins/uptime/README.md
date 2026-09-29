# Uptime

How long this Mac has been up, its load averages, and how many times the card
has run since the plugin was installed.

```
up                    3d 4h 12m
load (1, 5, 15 min)   1.52 · 1.38 · 1.20
runs since install    1
```

It is the first plugin in this repository, and its job is to exercise the
system rather than to be useful: it proves the whole path from a repository to
a card — listed, installed, updated, removed — and it leaves a trace that can be
checked at every step.

## What the card shows

* **up** — the time since the Mac last started, from `sysctl -n kern.boottime`.
* **load** — the load averages over 1, 5 and 15 minutes, from
  `sysctl -n vm.loadavg`: roughly how many processes were running or waiting
  to run.
* **runs since install** — how many times uDeck has run the plugin since it
  was installed. It is counted in a file in the plugin's cache directory,
  `UDECK_CACHE_DIR`, which uDeck deletes when the plugin is removed. So a plugin
  removed and installed again starts counting from 1, and one that does not
  was removed without its cache.

The card follows the panel's language — English or Russian, from `UDECK_LANG`.
If a value cannot be read, the card says so, names what failed, and turns
`unknown` rather than showing a number it does not have.

It runs every minute, and the card stays trustworthy for two (`ttl` 120), so a
plugin that stopped running shows as stale rather than as current.

## What it needs and what it runs

Nothing to install. It is POSIX `sh` using only what every Mac has:
`/usr/sbin/sysctl`, `date`, `sed`, `cat`, `mkdir` and `mv`.

It writes one file, `runs`, in `UDECK_CACHE_DIR`, and nothing anywhere else —
not even in its own folder, which has to stay exactly what this repository
holds, or uDeck marks the plugin as modified.

## Permissions

```json
"permissions": { "exec": ["sysctl"] }
```

It runs `sysctl`, so it says so. That means installing it walks through the
consent sheet, and every new version asks again. As with every plugin, uDeck
does not sandbox it: the declaration is what you read and agree to, not a wall
the plugin cannot climb.

## Settings

None.

## Licence

Apache-2.0, copyright Ilya Volkov — see [LICENSE](LICENSE).
