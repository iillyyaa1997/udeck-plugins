#!/usr/bin/env python3
"""Check a uDeck plugin repository before anything in it is published.

This implements rules 1-17 of the repository format, "What a folder may
contain" and "The official repository" in uDeck's
docs/plugin-repository.md
(https://github.com/iillyyaa1997/udeck/blob/main/docs/plugin-repository.md),
with nothing but the Python standard library.

    python3 .github/scripts/check-repo.py              rules 1-13
    python3 .github/scripts/check-repo.py --official   rules 1-16
    python3 .github/scripts/check-repo.py --official --base <sha> --head <sha>
                                                       and rule 17, for a pull request

It reads the repository through git, at one commit (HEAD unless --ref says
otherwise), and never through the working tree. uDeck sees a repository the
way GitHub lists it -- every path at a commit, with its mode, its blob hash and
its size -- so a script that is executable on disk but committed as 100644, or
a file that exists on disk but was never committed, has to be judged the way
uDeck will judge it. Commit first, then check.

Exit status: 0 when there are no errors (warnings do not fail the check), 1
when there are, and 2 when the repository could not be checked at all -- which
is not the same thing as a repository that fails.

Temporary by design. In stage 2 this script is replaced by
`udeck-plugin check-repo . --strict --official`, built from the Swift library
uDeck itself uses, and deleted rather than kept alongside it: two
implementations of one set of rules drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import posixpath
import re
import struct
import subprocess
import sys
import tempfile

PASSPORT = "udeck-plugins.json"
PLUGINS = "plugins"

# The repository format and the plugin contract this script knows.
FORMAT = 1
API = 1

MODE_FILE = "100644"
MODE_EXECUTABLE = "100755"
MODE_LINK = "120000"
MODE_SUBMODULE = "160000"

# Rule 1: uDeck's PluginIdentifier.pattern.
PLUGIN_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")

# Rule 4: three whole numbers, each 0 or 1-999999999 with no leading zero, and
# nothing after them. [0-9] rather than \d, which also matches other scripts'
# digits.
_PART = r"(?:0|[1-9][0-9]{0,8})"
VERSION = re.compile(_PART + r"\." + _PART + r"\." + _PART)

# Rule 7.
NAME = re.compile(r"[A-Za-z0-9._-]+")
MAX_NAME_BYTES = 255

# Rule 8.
MAX_FILES = 200
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_DEPTH = 8

# Rule 9. An LFS pointer names its spec on its first line; "hawser" is what
# the spec was called before it was Git LFS, and git-lfs still reads it.
LFS_POINTER_PREFIXES = (
    b"version https://git-lfs.github.com/spec/",
    b"version https://hawser.github.com/spec/",
)
ARCHIVE_ATTRIBUTES = ("export-ignore", "export-subst", "filter")

# Rule 14.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICON = "icon.png"
SCREENSHOTS = ("screenshot-1.png", "screenshot-2.png", "screenshot-3.png")
ICON_MAX_SIDE = 512
SCREENSHOT_MAX_BYTES = 1024 * 1024

# Rule 16. The SHA-256 of the Apache License, Version 2.0 -- the text of this
# repository's own LICENSE -- after normalised_licence(). Pinned here rather
# than read from ../../LICENSE, because a pull request can change that file
# too, and a check against a reference the change itself can edit checks
# nothing.
APACHE_2_0_SHA256 = "43070e2d4e532684de521b885f385d0841030efa2b1a20bafb76133a5e1379c1"
COPYRIGHT_LINE = re.compile(r"Copyright ([0-9]{4}) (.+)")

# Rule 17: the trailer `git commit -s` writes.
SIGNED_OFF_BY = re.compile(r"^Signed-off-by: [^<>\n]*[^<>\s] <[^<>\s]+>[ \t]*$", re.MULTILINE)

# What uDeck's PluginManifest.problems() holds a manifest to.
SECONDS_CEILING = 24 * 60 * 60
MINIMUM_INTERVAL = 1
MINIMUM_TIMEOUT = 0.05
GRID_COLUMNS = 12
MAXIMUM_WINDOW_HEIGHT = 24
WINDOW_DEFAULTS = {"defaultWidth": 4, "defaultHeight": 3, "minWidth": 2, "minHeight": 1}
SETTING_KEY = re.compile(r"[a-z0-9][a-z0-9_]*")
SETTING_TYPES = ("bool", "int", "string", "enum")
KINDS = ("poll", "resident")
# Swift's Int, which is what uDeck decodes every integer field into.
INT_MIN, INT_MAX = -(2 ** 63), 2 ** 63 - 1

# Rule 12: every field the plugin contract defines, and nothing else. `restart`
# is absent on purpose: uDeck's decoder knows it, but the contract does not
# describe it, and it could only matter to a resident plugin, which no uDeck
# runs yet.
MANIFEST_FIELDS = frozenset((
    "id", "name", "version", "api", "kind", "description", "author", "homepage",
    "minUDeck", "run", "interval", "timeout", "permissions", "settings", "window",
))
PERMISSION_FIELDS = frozenset(("read", "write", "exec", "network", "screen", "secrets"))
SETTING_FIELDS = frozenset(("key", "type", "label", "help", "default", "min", "max", "options"))
OPTION_FIELDS = frozenset(("value", "label"))
WINDOW_FIELDS = frozenset(WINDOW_DEFAULTS)
TRANSLATION_FIELDS = frozenset(("name", "description", "settings"))
SETTING_TRANSLATION_FIELDS = frozenset(("label", "help", "options"))
PASSPORT_FIELDS = frozenset(("format", "name", "description"))


class CheckFailed(Exception):
    """The repository could not be checked -- git is missing, or a commit is."""


class Finding:
    def __init__(self, level, rule, path, message):
        self.level = level
        self.rule = rule
        self.path = path
        self.message = message

    def __str__(self):
        label = "passport" if self.rule == "passport" else "rule " + self.rule
        where = display(self.path) + ": " if self.path else ""
        return "{}: {}{} [{}]".format(self.level, where, self.message, label)

    def __repr__(self):
        return "Finding({!r}, {!r}, {!r}, {!r})".format(self.level, self.rule, self.path, self.message)


class Report:
    def __init__(self):
        self.findings = []

    def error(self, rule, path, message):
        self.findings.append(Finding("error", str(rule), path, message))

    def warning(self, rule, path, message):
        self.findings.append(Finding("warning", str(rule), path, message))

    @property
    def errors(self):
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self):
        return [f for f in self.findings if f.level == "warning"]


def display(path):
    """A path as it can be printed, even when git stored bytes that are not UTF-8."""
    return path.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")


def run_git(repo, args, stdin=None, env=None):
    try:
        done = subprocess.run(
            ["git", "-C", repo] + list(args),
            input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
    except FileNotFoundError:
        raise CheckFailed("git is not installed, and the repository is read through it")
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip() or "exit status {}".format(done.returncode)
        raise CheckFailed("git {}: {}".format(" ".join(args), detail))
    return done.stdout


def resolve_commit(repo, ref):
    try:
        return run_git(repo, ["rev-parse", "--verify", "--quiet", ref + "^{commit}"]).decode("ascii").strip()
    except CheckFailed:
        raise CheckFailed("{} is not a commit in {} -- a pull request's checkout needs fetch-depth: 0".format(ref, repo))


class Entry:
    """One path of `git ls-tree -r -t -l`: what GitHub's tree listing gives uDeck."""

    __slots__ = ("path", "mode", "kind", "sha", "size")

    def __init__(self, path, mode, kind, sha, size):
        self.path = path
        self.mode = mode
        self.kind = kind      # blob, tree or commit (a submodule)
        self.sha = sha
        self.size = size      # None for a tree or a submodule

    @property
    def name(self):
        return posixpath.basename(self.path)

    @property
    def is_file(self):
        return self.kind == "blob" and self.mode in (MODE_FILE, MODE_EXECUTABLE)


class Snapshot:
    """The repository at one commit."""

    def __init__(self, repo, ref):
        self.repo = repo
        self.commit = resolve_commit(repo, ref)
        listing = run_git(repo, ["ls-tree", "-r", "-t", "-l", "-z", "--full-tree", self.commit])
        self.entries = {}
        for record in listing.split(b"\0"):
            if not record:
                continue
            meta, _, raw_path = record.partition(b"\t")
            mode, kind, sha, size = meta.decode("ascii").split()
            # Names are bytes to git. Rule 7 reports the ones that are not
            # plain ASCII, so they must survive being read rather than stop it.
            path = raw_path.decode("utf-8", "surrogateescape")
            self.entries[path] = Entry(path, mode, kind, sha, None if size == "-" else int(size))
        self._blobs = {}

    def children(self, directory):
        prefix = directory + "/" if directory else ""
        return [
            entry for path, entry in sorted(self.entries.items())
            if path.startswith(prefix) and "/" not in path[len(prefix):]
        ]

    def under(self, directory):
        prefix = directory + "/"
        return [entry for path, entry in sorted(self.entries.items()) if path.startswith(prefix)]

    def load(self, entries):
        """Reads these blobs in one `git cat-file --batch`."""
        wanted = sorted({e.sha for e in entries if e.kind == "blob" and e.sha not in self._blobs})
        if not wanted:
            return
        out = run_git(self.repo, ["cat-file", "--batch"], stdin=("\n".join(wanted) + "\n").encode("ascii"))
        position = 0
        for sha in wanted:
            end = out.index(b"\n", position)
            header = out[position:end].split()
            if len(header) != 3 or header[1] != b"blob":
                raise CheckFailed("git could not read blob {}".format(sha))
            size = int(header[2])
            self._blobs[sha] = out[end + 1:end + 1 + size]
            position = end + 1 + size + 1

    def content(self, entry):
        """The bytes of a blob that load() read, or None for one it did not."""
        return self._blobs.get(entry.sha)

    def attributes(self, paths):
        """What .gitattributes at this commit sets for each path: {path: {attribute: value}}.

        A folder is asked about with a trailing slash, which is how git matches
        a pattern written for folders only (`plugins/uptime/ export-ignore`).
        The attributes are read from the commit through a scratch index,
        never from the working tree, and a personal core.attributesFile is
        switched off: what matters is what the repository says, since that is
        all an archive of it on GitHub or GitLab will apply.
        """
        if not paths:
            return {}
        with tempfile.TemporaryDirectory() as scratch:
            env = dict(os.environ, GIT_INDEX_FILE=os.path.join(scratch, "index"))
            run_git(self.repo, ["read-tree", self.commit], env=env)
            out = run_git(
                self.repo,
                ["-c", "core.attributesFile=" + os.devnull, "check-attr", "--cached", "-z", "--stdin"]
                + list(ARCHIVE_ATTRIBUTES),
                stdin=b"".join(p.encode("utf-8", "surrogateescape") + b"\0" for p in paths),
                env=env,
            )
        fields = out.split(b"\0")
        found = {}
        for i in range(0, len(fields) - 2, 3):
            path, attribute, value = (f.decode("utf-8", "surrogateescape") for f in fields[i:i + 3])
            if value not in ("unspecified", "unset"):
                found.setdefault(path, {})[attribute] = value
        return found


# --- JSON, as uDeck's decoder reads it ---------------------------------------

def parse_json(data):
    """(value, problems) for the bytes of a JSON file.

    Stricter than uDeck in one way, on purpose: a field given twice in one
    object is an error. A reviewer reads one of the two values, and which one
    a decoder keeps is not a promise anybody made.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        return None, ["is not UTF-8 (byte {} cannot be read)".format(error.start)]
    repeated = []

    def pairs(items):
        seen = {}
        for key, value in items:
            if key in seen and key not in repeated:
                repeated.append(key)
            seen[key] = value
        return seen

    def constant(name):
        raise ValueError("{} is not a JSON value".format(name))

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except ValueError as error:
        return None, ["is not valid JSON: {}".format(error)]
    return value, ['gives the field "{}" more than once'.format(key) for key in repeated]


def is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and INT_MIN <= value <= INT_MAX


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_string_list(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def kind_of(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "a list"
    return "an object"


class Fields:
    """Type checks for one JSON object, collecting every mismatch rather than the first."""

    def __init__(self, obj, where, wrong):
        self.obj = obj
        self.where = where
        self.wrong = wrong

    def check(self, field, test, what, required=False):
        name = self.where + field
        value = self.obj.get(field)
        # An optional field given as null is the same as one left out, as it
        # is to Swift's decodeIfPresent; a required one is not.
        if value is None:
            if required:
                self.wrong.append('"{}" is required'.format(name))
            return None
        if not test(value):
            self.wrong.append('"{}" must be {}, not {}'.format(name, what, kind_of(value)))
            return None
        return value


def decode_manifest(manifest):
    """Why uDeck's decoder would refuse this manifest, if it would (PluginManifest.init(from:))."""
    if not isinstance(manifest, dict):
        return ["must be a JSON object"]
    wrong = []
    top = Fields(manifest, "", wrong)
    identifier = top.check("id", lambda v: isinstance(v, str), "a string", required=True)
    if identifier is not None and not PLUGIN_ID.fullmatch(identifier):
        wrong.append('"id" is "{}", which is not a plugin id: lowercase letters, digits and "._-", '
                     "1-64 characters, starting with a letter or digit".format(identifier))
    top.check("name", lambda v: isinstance(v, str), "a string", required=True)
    top.check("version", lambda v: isinstance(v, str), "a string", required=True)
    top.check("api", is_integer, "a whole number", required=True)
    top.check("kind", lambda v: v in KINDS, '"poll" or "resident"', required=True)
    for field in ("description", "author", "homepage", "minUDeck"):
        top.check(field, lambda v: isinstance(v, str), "a string")
    top.check("run", is_string_list, "a list of strings", required=True)
    for field in ("interval", "timeout"):
        top.check(field, is_number, "a number of seconds")

    permissions = top.check("permissions", lambda v: isinstance(v, dict), "an object")
    if permissions is not None:
        inner = Fields(permissions, "permissions.", wrong)
        for field in ("read", "write", "exec", "network", "secrets"):
            inner.check(field, is_string_list, "a list of strings")
        inner.check("screen", lambda v: isinstance(v, bool), "true or false")

    settings = top.check("settings", lambda v: isinstance(v, list), "a list")
    for index, declaration in enumerate(settings or []):
        where = "settings[{}]".format(index)
        if not isinstance(declaration, dict):
            wrong.append('"{}" must be an object, not {}'.format(where, kind_of(declaration)))
            continue
        inner = Fields(declaration, where + ".", wrong)
        inner.check("key", lambda v: isinstance(v, str), "a string", required=True)
        inner.check("type", lambda v: v in SETTING_TYPES, '"bool", "int", "string" or "enum"', required=True)
        inner.check("label", lambda v: isinstance(v, str), "a string", required=True)
        inner.check("help", lambda v: isinstance(v, str), "a string")
        inner.check("default", lambda v: isinstance(v, (bool, str)) or is_integer(v),
                    "true or false, a whole number or a string", required=True)
        inner.check("min", is_integer, "a whole number")
        inner.check("max", is_integer, "a whole number")
        options = inner.check("options", lambda v: isinstance(v, list), "a list")
        for number, option in enumerate(options or []):
            place = "{}.options[{}]".format(where, number)
            if not isinstance(option, dict):
                wrong.append('"{}" must be an object, not {}'.format(place, kind_of(option)))
                continue
            each = Fields(option, place + ".", wrong)
            each.check("value", lambda v: isinstance(v, str), "a string", required=True)
            each.check("label", lambda v: isinstance(v, str), "a string", required=True)

    window = top.check("window", lambda v: isinstance(v, dict), "an object")
    if window is not None:
        inner = Fields(window, "window.", wrong)
        for field in sorted(WINDOW_FIELDS):
            inner.check(field, is_integer, "a whole number")
    return wrong


def setting_problem(declaration):
    """SettingDeclaration.problem in uDeck."""
    key, kind, default = declaration["key"], declaration["type"], declaration["default"]
    if not SETTING_KEY.fullmatch(key):
        return "key must be lowercase letters, digits and underscores"
    if not declaration["label"].strip():
        return "label must not be blank"
    default_kind = "bool" if isinstance(default, bool) else "int" if isinstance(default, int) else "string"
    if kind == "enum" and default_kind == "string":
        options = declaration.get("options") or []
        if not options:
            return "an enum setting must list its options"
        if not any(option["value"] == default for option in options):
            return 'the default "{}" is not one of the declared options'.format(default)
    elif kind != default_kind:
        return "declared type is {} but the default is a {}".format(kind, default_kind)
    minimum, maximum = declaration.get("min"), declaration.get("max")
    if minimum is not None and maximum is not None and minimum > maximum:
        return "min ({}) is greater than max ({})".format(minimum, maximum)
    if default_kind == "int":
        if minimum is not None and default < minimum:
            return "the default {} is below min {}".format(default, minimum)
        if maximum is not None and default > maximum:
            return "the default {} is above max {}".format(default, maximum)
    return None


def manifest_problems(manifest):
    """PluginManifest.problems() in uDeck: why a manifest that decodes is still unusable."""
    found = []
    if manifest["api"] != API:
        found.append("it declares api {}; uDeck speaks api {}".format(manifest["api"], API))
    if not manifest["name"].strip():
        found.append('"name" must not be blank -- it is what the operator sees')
    run = manifest["run"]
    if not run or not run[0].strip():
        found.append('"run" must contain at least the command to execute')

    if manifest["kind"] == "poll":
        for field, floor in (("interval", MINIMUM_INTERVAL), ("timeout", MINIMUM_TIMEOUT)):
            value = manifest.get(field)
            if value is None:
                found.append('a poll plugin must declare "{}" in seconds'.format(field))
            elif value <= 0 or not math.isfinite(value):
                found.append('"{}" must be greater than zero, got {}'.format(field, value))
            elif value > SECONDS_CEILING:
                found.append('"{}" is {} seconds, past the {}-second limit'.format(field, value, SECONDS_CEILING))
            elif value < floor:
                found.append('"{}" is {} seconds, below the {}-second floor'.format(field, value, floor))
        interval, timeout = manifest.get("interval"), manifest.get("timeout")
        if interval is not None and timeout is not None and interval > 0 and timeout > 0 and timeout >= interval:
            found.append('"timeout" ({}s) must be shorter than "interval" ({}s), otherwise a slow run '
                         "always overlaps the next one".format(timeout, interval))
    else:
        found.append('"kind": "resident" is described by the contract but no uDeck runs it yet')

    seen = set()
    for declaration in manifest.get("settings") or []:
        key = declaration["key"]
        if key in seen:
            found.append('two settings share the key "{}"'.format(key))
        seen.add(key)
        problem = setting_problem(declaration)
        if problem:
            found.append('setting "{}": {}'.format(key, problem))

    window = dict(WINDOW_DEFAULTS)
    window.update({k: v for k, v in (manifest.get("window") or {}).items() if k in WINDOW_FIELDS and v is not None})
    if not 1 <= window["defaultWidth"] <= GRID_COLUMNS:
        found.append('"window": defaultWidth must be between 1 and {}'.format(GRID_COLUMNS))
    if not 1 <= window["minWidth"] <= window["defaultWidth"]:
        found.append('"window": minWidth must be between 1 and defaultWidth')
    if not 1 <= window["defaultHeight"] <= MAXIMUM_WINDOW_HEIGHT:
        found.append('"window": defaultHeight must be between 1 and {}'.format(MAXIMUM_WINDOW_HEIGHT))
    if not 1 <= window["minHeight"] <= window["defaultHeight"]:
        found.append('"window": minHeight must be between 1 and defaultHeight')
    return found


def unknown_fields(value, known, where=""):
    if not isinstance(value, dict):
        return []
    return [where + field for field in value if field not in known]


def manifest_unknown_fields(manifest):
    unknown = unknown_fields(manifest, MANIFEST_FIELDS)
    unknown += unknown_fields(manifest.get("permissions"), PERMISSION_FIELDS, "permissions.")
    unknown += unknown_fields(manifest.get("window"), WINDOW_FIELDS, "window.")
    settings = manifest.get("settings")
    for index, declaration in enumerate(settings if isinstance(settings, list) else []):
        where = "settings[{}].".format(index)
        unknown += unknown_fields(declaration, SETTING_FIELDS, where)
        options = declaration.get("options") if isinstance(declaration, dict) else None
        for number, option in enumerate(options if isinstance(options, list) else []):
            unknown += unknown_fields(option, OPTION_FIELDS, "{}options[{}].".format(where, number))
    return unknown


def translation_code(file_name):
    """The language of a manifest.<code>.json, or None -- PluginDiscovery.languageCode in uDeck."""
    if not (file_name.startswith("manifest.") and file_name.endswith(".json")):
        return None
    middle = file_name[len("manifest."):-len(".json")]
    if not middle:
        return None
    parts = middle.split("-")
    if len(parts) > 2:
        return None
    if not (2 <= len(parts[0]) <= 3 and parts[0].isalpha()):
        return None
    if len(parts) == 2 and not (2 <= len(parts[1]) <= 8 and parts[1].isalnum()):
        return None
    return middle.lower()


def translation_problems(translation, manifest):
    """What in a translation the contract does not define (ManifestTranslation in uDeck).

    A setting key or an option value that manifest.json does not declare is
    included: uDeck ignores it, so the label it carries never appears, and the
    reason is nearly always a typo in the key. That comparison needs a
    manifest that decodes, and is skipped -- rather than reported against
    every key -- when `manifest` is None.
    """
    if not isinstance(translation, dict):
        return ["must be a JSON object"]
    wrong = []
    top = Fields(translation, "", wrong)
    top.check("name", lambda v: isinstance(v, str), "a string")
    top.check("description", lambda v: isinstance(v, str), "a string")
    wrong += ['has the field "{}", which a translation does not have'.format(f)
              for f in unknown_fields(translation, TRANSLATION_FIELDS)]
    settings = top.check("settings", lambda v: isinstance(v, dict), "an object keyed by setting key")
    if settings is None:
        return wrong
    declared = None
    if manifest is not None:
        declared = {
            d["key"]: {option["value"] for option in d.get("options") or []}
            for d in manifest.get("settings") or []
        }
    for key, entry in settings.items():
        where = "settings.{}".format(key)
        if declared is not None and key not in declared:
            wrong.append('translates the setting "{}", which manifest.json does not declare'.format(key))
        if not isinstance(entry, dict):
            wrong.append('"{}" must be an object, not {}'.format(where, kind_of(entry)))
            continue
        inner = Fields(entry, where + ".", wrong)
        inner.check("label", lambda v: isinstance(v, str), "a string")
        inner.check("help", lambda v: isinstance(v, str), "a string")
        wrong += ['has the field "{}", which a translation does not have'.format(f)
                  for f in unknown_fields(entry, SETTING_TRANSLATION_FIELDS, where + ".")]
        options = inner.check("options", lambda v: isinstance(v, dict), "an object keyed by option value")
        for value, label in (options or {}).items():
            if not isinstance(label, str):
                wrong.append('"{}.options.{}" must be a string, not {}'.format(where, value, kind_of(label)))
            if declared is not None and key in declared and value not in declared[key]:
                wrong.append('translates the option "{}" of "{}", which manifest.json does not declare'
                             .format(value, key))
    return wrong


# --- the repository ------------------------------------------------------------

def check_passport(snapshot, report):
    entry = snapshot.entries.get(PASSPORT)
    if entry is None:
        report.error("passport", PASSPORT, "is missing, so uDeck refuses the whole repository: it is what "
                     "says a repository was meant to be a plugin repository")
        return
    if not entry.is_file:
        report.error("passport", PASSPORT, "must be a file, not a {}".format(
            "symbolic link" if entry.mode == MODE_LINK else entry.kind))
        return
    snapshot.load([entry])
    passport, problems = parse_json(snapshot.content(entry))
    for problem in problems:
        report.error("passport", PASSPORT, problem)
    if passport is None:
        return
    if not isinstance(passport, dict):
        report.error("passport", PASSPORT, "must be a JSON object")
        return
    wrong = []
    fields = Fields(passport, "", wrong)
    number = fields.check("format", is_integer, "a whole number", required=True)
    name = fields.check("name", lambda v: isinstance(v, str), "a string", required=True)
    description = fields.check("description", lambda v: isinstance(v, str), "a string")
    for problem in wrong:
        report.error("passport", PASSPORT, problem)
    if number is not None and number != FORMAT:
        report.error("passport", PASSPORT, '"format" is {}; this check, like uDeck, reads format {}'
                     .format(number, FORMAT))
    if name is not None and not (1 <= len(name) <= 64 and name.strip()):
        report.error("passport", PASSPORT, '"name" must be 1-64 characters and not blank; it is {} characters'
                     .format(len(name)))
    if description is not None and len(description) > 280:
        report.error("passport", PASSPORT, '"description" is {} characters; at most 280'.format(len(description)))
    for field in unknown_fields(passport, PASSPORT_FIELDS):
        report.error("passport", PASSPORT, 'has the field "{}", which the format does not define -- '
                     "usually a typo".format(field))


def check_plugins_folder(snapshot, report):
    """Rules 1 and 2. Returns the plugin folders to check one by one."""
    top = snapshot.entries.get(PLUGINS)
    if top is None:
        return []
    if top.kind != "tree":
        report.error(1, PLUGINS, "must be a folder holding one folder per plugin")
        return []
    folders = []
    for entry in snapshot.children(PLUGINS):
        if entry.kind != "tree":
            what = {MODE_LINK: "a symbolic link", MODE_SUBMODULE: "a submodule"}.get(entry.mode, "a file")
            report.error(2, entry.path, "is {} directly inside plugins/, which holds nothing but plugin folders"
                         .format(what))
            continue
        if not PLUGIN_ID.fullmatch(entry.name):
            report.error(1, entry.path, 'folder name "{}" is not a plugin id: lowercase letters, digits and '
                         '"._-", 1-64 characters, starting with a letter or digit'.format(display(entry.name)))
            continue
        folders.append(entry.path)
    return folders


def check_plugin(snapshot, folder, attributes, official, report):
    plugin_id = posixpath.basename(folder)
    entries = snapshot.under(folder)
    top = {entry.name: entry for entry in snapshot.children(folder)}

    def relative(entry):
        return entry.path[len(folder) + 1:]

    # Rule 6.
    for entry in entries:
        if entry.mode == MODE_LINK:
            report.error(6, entry.path, "is a symbolic link; a plugin may contain only files and folders")
        elif entry.mode == MODE_SUBMODULE:
            report.error(6, entry.path, "is a submodule -- a pointer to another repository, which uDeck "
                         "would install as nothing")

    # Rule 7.
    by_folder = {}
    for entry in entries:
        name = entry.name
        reasons = []
        if not NAME.fullmatch(name):
            reasons.append('uses a character other than letters, digits, ".", "_" and "-"')
        if name.startswith("."):
            reasons.append('starts with "."')
        if len(name.encode("utf-8", "surrogateescape")) > MAX_NAME_BYTES:
            reasons.append("is longer than {} bytes".format(MAX_NAME_BYTES))
        if reasons:
            report.error(7, entry.path, "the name " + " and ".join(reasons))
        by_folder.setdefault(posixpath.dirname(entry.path), {}).setdefault(name.lower(), []).append(name)
    for parent, names in sorted(by_folder.items()):
        for group in names.values():
            if len(group) > 1:
                report.error(7, parent, "{} differ only in letter case; on a Mac they are one file".format(
                    " and ".join(display(n) for n in sorted(group))))

    # Rule 8.
    files = [entry for entry in entries if entry.kind != "tree"]
    total = sum(entry.size or 0 for entry in files)
    if len(files) > MAX_FILES:
        report.error(8, folder, "holds {} files; at most {}".format(len(files), MAX_FILES))
    if total > MAX_TOTAL_BYTES:
        report.error(8, folder, "is {} bytes in total; at most {} (10 MiB)".format(total, MAX_TOTAL_BYTES))
    for entry in files:
        if (entry.size or 0) > MAX_FILE_BYTES:
            report.error(8, entry.path, "is {} bytes; at most {} (5 MiB) for one file".format(
                entry.size, MAX_FILE_BYTES))
    too_deep = [e for e in entries if e.kind == "tree" and relative(e).count("/") + 1 > MAX_DEPTH]
    if too_deep:
        deepest = too_deep[0]
        report.error(8, deepest.path, "is nested {} folders deep; at most {}".format(
            relative(deepest).count("/") + 1, MAX_DEPTH))

    # Rule 9.
    for entry in entries:
        content = snapshot.content(entry)
        if content is not None and content.startswith(LFS_POINTER_PREFIXES):
            report.error(9, entry.path, "is a Git LFS pointer, not the file; uDeck does not fetch LFS content")
    for entry in [snapshot.entries[folder]] + entries:
        report_archive_attributes(attributes, entry, report)

    # Rules 3, 4, 5, 12.
    manifest_entry = top.get("manifest.json")
    manifest = None
    decoded = None  # the manifest, once it is known to decode as uDeck would decode it
    if manifest_entry is None:
        report.error(3, folder + "/manifest.json", "is missing; every plugin folder has one, directly inside it")
    elif not manifest_entry.is_file:
        report.error(3, manifest_entry.path, "must be a file")
    elif snapshot.content(manifest_entry) is not None:
        where = manifest_entry.path
        manifest, problems = parse_json(snapshot.content(manifest_entry))
        for problem in problems:
            report.error(3, where, problem)
        wrong = decode_manifest(manifest) if manifest is not None else ["could not be read"]
        if manifest is not None:
            for problem in wrong:
                report.error(3, where, problem)
        if not wrong:
            decoded = manifest
            if manifest["id"] != plugin_id:
                report.error(3, where, 'declares id "{}" but sits in the folder "{}"; they must match'.format(
                    manifest["id"], display(plugin_id)))
            for problem in manifest_problems(manifest):
                report.error(3, where, problem)
        if isinstance(manifest, dict):
            check_versions(manifest, where, report)
            check_run(snapshot, folder, manifest, where, report)
            for field in manifest_unknown_fields(manifest):
                report.error(12, where, 'has the field "{}", which the plugin contract does not define -- '
                             "usually a typo, and uDeck ignores it".format(field))

    # Rule 10.
    readme = top.get("README.md")
    if readme is None or not readme.is_file:
        report.error(10, folder + "/README.md", "is missing; it is what a reviewer and an installer read first")

    # Rules 11 and 12, for translations.
    translations = [entry for name, entry in sorted(top.items()) if entry.is_file and translation_code(name)]
    if not translations:
        report.warning(11, folder, "has no manifest.<lang>.json; the plugin will show in English only")
    for entry in translations:
        if snapshot.content(entry) is None:
            continue  # larger than rule 8 allows, and already reported there
        translation, problems = parse_json(snapshot.content(entry))
        for problem in problems:
            report.error(12, entry.path, problem)
        if translation is not None:
            for problem in translation_problems(translation, decoded):
                report.error(12, entry.path, problem)

    # Rule 13.
    for entry in entries:
        content = snapshot.content(entry)
        if entry.mode == MODE_EXECUTABLE and content is not None and b"\r" in content:
            report.error(13, entry.path, "is executable and contains a carriage return; with Windows line "
                         "endings a script fails on a Mac with \"bad interpreter\"")

    if official:
        check_text_only(snapshot, folder, entries, report)
        author = check_author(manifest, folder, report)
        check_licence(snapshot, folder, top.get("LICENSE"), author, report)


def report_archive_attributes(attributes, entry, report):
    """Rule 9, for one path: what .gitattributes sets that changes an archive."""
    if entry is None:
        return
    asked = entry.path + "/" if entry.kind == "tree" else entry.path
    for attribute, value in sorted(attributes.get(asked, {}).items()):
        setting = attribute if value == "set" else "{}={}".format(attribute, value)
        report.error(9, entry.path, "has the attribute {} from .gitattributes; export-ignore, export-subst and "
                     "filter change what an archive of the repository holds".format(setting))


def check_versions(manifest, where, report):
    """Rule 4."""
    version = manifest.get("version")
    if isinstance(version, str) and not VERSION.fullmatch(version):
        report.error(4, where, '"version" is "{}", which is not MAJOR.MINOR.PATCH (like 1.2.0: three whole '
                     "numbers, no leading zeros, no suffix)".format(version))
    minimum = manifest.get("minUDeck")
    if isinstance(minimum, str) and not VERSION.fullmatch(minimum):
        report.error(4, where, '"minUDeck" is "{}", which is not MAJOR.MINOR.PATCH'.format(minimum))


def check_run(snapshot, folder, manifest, where, report):
    """Rule 5: a relative run[0] is a file in the folder, committed as executable."""
    run = manifest.get("run")
    if not (isinstance(run, list) and run and isinstance(run[0], str)):
        return
    command = run[0]
    # A bare name is looked up on uDeck's search path and an absolute path is
    # used as given; only a relative path names something in the folder.
    if command.startswith("/") or "/" not in command:
        return
    target = posixpath.normpath(posixpath.join(folder, command))
    if not target.startswith(folder + "/"):
        report.error(5, where, '"run" starts with "{}", which climbs out of the plugin folder'.format(command))
        return
    entry = snapshot.entries.get(target)
    if entry is None or entry.kind != "blob":
        report.error(5, where, '"run" starts with "{}", but {} is not a file in this commit'.format(
            command, display(target)))
    elif entry.mode != MODE_EXECUTABLE:
        report.error(5, entry.path, "is what \"run\" starts with, but it is committed as {}, not executable "
                     "(100755) -- git update-index --chmod=+x {}".format(entry.mode, display(entry.path)))


def png_dimensions(content):
    """(width, height) from a PNG's IHDR chunk, or None when there is none where it must be."""
    if len(content) < 24 or content[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", content[16:24])


def check_text_only(snapshot, folder, entries, report):
    """Rule 14: every file is UTF-8 text, except an icon and up to three screenshots in PNG."""
    for entry in entries:
        if not entry.is_file:
            continue
        content = snapshot.content(entry)
        if content is None:
            continue  # larger than rule 8 allows, and already reported there
        at_top = posixpath.dirname(entry.path) == folder
        picture_name = at_top and (entry.name == ICON or entry.name in SCREENSHOTS)
        if content.startswith(PNG_SIGNATURE):
            if not picture_name:
                report.error(14, entry.path, "is a PNG; the only pictures a plugin may hold are icon.png and "
                             "screenshot-1.png ... screenshot-3.png, at the top of its folder")
                continue
            if entry.mode == MODE_EXECUTABLE:
                report.error(14, entry.path, "is a picture committed as executable; uDeck only ever shows a "
                             "PNG -- git update-index --chmod=-x {}".format(display(entry.path)))
            size = png_dimensions(content)
            if size is None:
                report.error(14, entry.path, "starts like a PNG but has no IHDR chunk; it is not a picture "
                             "uDeck can show")
            elif entry.name == ICON and (size[0] > ICON_MAX_SIDE or size[1] > ICON_MAX_SIDE):
                report.error(14, entry.path, "is {}x{}; an icon is at most {}x{}".format(
                    size[0], size[1], ICON_MAX_SIDE, ICON_MAX_SIDE))
            if entry.name in SCREENSHOTS and len(content) > SCREENSHOT_MAX_BYTES:
                report.error(14, entry.path, "is {} bytes; a screenshot is at most {} (1 MiB)".format(
                    len(content), SCREENSHOT_MAX_BYTES))
            continue
        if picture_name:
            report.error(14, entry.path, "is named like a picture but is not a PNG; a PNG is recognised by "
                         "its signature, not its name")
            continue
        if b"\0" in content:
            report.error(14, entry.path, "contains a NUL byte, so it is not text; a binary cannot be read by a "
                         "reviewer, so it cannot be verified")
            continue
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as error:
            report.error(14, entry.path, "is not valid UTF-8 (byte {}); every file in the official repository "
                         "is UTF-8 text".format(error.start))


def check_author(manifest, folder, report):
    """Rule 15. Returns the author, or None."""
    if not isinstance(manifest, dict):
        return None  # no manifest to read it from, which rule 3 has said
    author = manifest.get("author")
    if isinstance(author, str) and author.strip():
        return author
    if author is not None and not isinstance(author, str):
        return None  # set, but not to a name, which rule 3 has said
    report.error(15, folder + "/manifest.json", '"author" is not set; it is the name the copyright belongs to')
    return None


def normalised_licence(text):
    """The licence text with trailing spaces and the blank lines around it ignored.

    Word for word and line for line, but not byte for byte: the copy on
    apache.org opens with a blank line and GitHub's does not, and both are the
    unmodified licence.
    """
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def check_licence(snapshot, folder, entry, author, report):
    """Rule 16: `Copyright <year> <author>`, a blank line, then the Apache License 2.0."""
    path = folder + "/LICENSE"
    if entry is None or not entry.is_file:
        report.error(16, path, "is missing; every plugin here is Apache-2.0, and its LICENSE says whose it is")
        return
    content = snapshot.content(entry)
    if content is None:
        return
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return  # rule 14 has said so
    lines = text.split("\n")
    first = lines[0].rstrip()
    matched = COPYRIGHT_LINE.fullmatch(first)
    if not matched:
        report.error(16, path, 'must start with the line "Copyright <year> <author>", not "{}"'.format(first))
    elif author is not None and matched.group(2) != author:
        report.error(16, path, 'says the copyright is "{}", but the manifest\'s author is "{}"; they must be '
                     "the same name".format(matched.group(2), author))
    if len(lines) < 2 or lines[1].strip():
        report.error(16, path, "must have a blank line after the copyright line")
        body = normalised_licence("\n".join(lines[1:]))
    else:
        body = normalised_licence("\n".join(lines[2:]))
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != APACHE_2_0_SHA256:
        report.error(16, path, "after the copyright line and a blank line, must hold the unmodified text of the "
                     "Apache License, Version 2.0 -- copy it from the LICENSE at the top of this repository")


def check_sign_offs(repo, base, head, report):
    """Rule 17: every commit from base (exclusive) to head carries a Signed-off-by line."""
    base_commit = resolve_commit(repo, base)
    head_commit = resolve_commit(repo, head)
    out = run_git(repo, ["log", "-z", "--format=%H%n%B", "{}..{}".format(base_commit, head_commit)])
    count = 0
    for record in out.split(b"\0"):
        if not record.strip():
            continue
        count += 1
        text = record.decode("utf-8", "replace")
        sha, _, message = text.partition("\n")
        if not SIGNED_OFF_BY.search(message):
            subject = message.strip().split("\n")[0] if message.strip() else "(no message)"
            report.error(17, "commit " + sha[:12], '"{}" has no Signed-off-by line. Sign it off with '
                         "git commit --amend -s, or git rebase --signoff {} for several, and push again"
                         .format(subject, base_commit[:12]))
    return count


def check(repo=".", ref="HEAD", official=False, base=None, head=None):
    """Checks the repository at `ref`. Returns (snapshot, report)."""
    if (base is None) != (head is None):
        raise CheckFailed("--base and --head go together")
    snapshot = Snapshot(repo, ref)
    report = Report()
    check_passport(snapshot, report)
    folders = check_plugins_folder(snapshot, report)
    everything = snapshot.under(PLUGINS) if folders else []
    # Content is read for what rule 8 lets through; a larger file is already
    # an error, and reading it would only cost memory.
    snapshot.load([e for e in everything if e.kind == "blob" and (e.size or 0) <= MAX_FILE_BYTES])
    asked = [PLUGINS + "/"] + [e.path + "/" if e.kind == "tree" else e.path for e in everything]
    attributes = snapshot.attributes(asked) if folders else {}
    report_archive_attributes(attributes, snapshot.entries[PLUGINS] if folders else None, report)
    for folder in folders:
        check_plugin(snapshot, folder, attributes, official, report)
    if base is not None:
        check_sign_offs(repo, base, head, report)
    return snapshot, report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check a uDeck plugin repository (docs/plugin-repository.md in uDeck).")
    parser.add_argument("--repo", default=".", help="the repository to check (default: the current directory)")
    parser.add_argument("--ref", default="HEAD", help="the commit to check (default: HEAD)")
    parser.add_argument("--official", action="store_true",
                        help="also the official repository's rules: text only, author, LICENSE (14-16)")
    parser.add_argument("--base", help="a pull request's base commit; with --head, checks sign-offs (rule 17)")
    parser.add_argument("--head", help="a pull request's head commit")
    arguments = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except AttributeError:
        pass
    try:
        snapshot, report = check(arguments.repo, arguments.ref, arguments.official, arguments.base, arguments.head)
    except CheckFailed as failure:
        print("could not check: {}".format(failure))
        return 2
    for finding in report.findings:
        print(finding)
    plugins = len([e for e in snapshot.children(PLUGINS) if e.kind == "tree"]) if PLUGINS in snapshot.entries else 0
    print("checked {} plugin folder{} at {}{}: {} error{}, {} warning{}".format(
        plugins, "" if plugins == 1 else "s", snapshot.commit[:12], " as the official repository"
        if arguments.official else "", len(report.errors), "" if len(report.errors) == 1 else "s",
        len(report.warnings), "" if len(report.warnings) == 1 else "s"))
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
