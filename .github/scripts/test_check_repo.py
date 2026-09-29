"""Tests for check-repo.py: for every rule, a repository that passes and one that breaks it.

Every test commits a real git repository in a temporary folder and checks it
at HEAD, which is how the script reads this repository. The commits are made
with git's plumbing rather than from files on disk, so that a mode, a symbolic
link, a submodule or two names differing only in case can be committed on any
filesystem -- including the case-insensitive one a Mac starts with.

    python3 -I -B -m unittest discover -s .github/scripts -p 'test_*.py' -v
"""

import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("check_repo", os.path.join(HERE, "check-repo.py"))
check_repo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_repo)

with open(os.path.join(HERE, "..", "..", "LICENSE"), encoding="utf-8") as _licence:
    APACHE = _licence.read()

FILE = "100644"
EXECUTABLE = "100755"
LINK = "120000"
SUBMODULE = "160000"
MiB = 1024 * 1024

SIGNED = "Add a plugin\n\nSigned-off-by: Ada Lovelace <ada@example.com>\n"
UNSIGNED = "Add a plugin\n"
REMOVE = object()

# The tests' git must not read the machine's configuration -- a global
# attributes file, commit signing, a hook -- or what passes here would depend
# on whose machine it ran on.
GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Ada Lovelace",
    "GIT_AUTHOR_EMAIL": "ada@example.com",
    "GIT_AUTHOR_DATE": "2026-09-28T12:00:00Z",
    "GIT_COMMITTER_NAME": "Ada Lovelace",
    "GIT_COMMITTER_EMAIL": "ada@example.com",
    "GIT_COMMITTER_DATE": "2026-09-28T12:00:00Z",
}
_saved_environment = {}


def setUpModule():
    for key, value in GIT_ENVIRONMENT.items():
        _saved_environment[key] = os.environ.get(key)
        os.environ[key] = value


def tearDownModule():
    for key, value in _saved_environment.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# --- the fixture: one good repository, changed one thing at a time ------------

def manifest(**changes):
    value = {
        "id": "sample",
        "name": "Sample",
        "version": "1.0.0",
        "api": 1,
        "kind": "poll",
        "description": "A plugin that exists to be checked.",
        "author": "Ada Lovelace",
        "run": ["./run.sh"],
        "interval": 60,
        "timeout": 2,
        "permissions": {"exec": ["sysctl"]},
        "settings": [
            {"key": "mode", "type": "enum", "default": "a", "label": "Mode",
             "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
            {"key": "rows", "type": "int", "default": 3, "min": 0, "max": 10, "label": "Rows"},
        ],
        "window": {"defaultWidth": 4, "defaultHeight": 3, "minWidth": 2, "minHeight": 1},
    }
    for key, change in changes.items():
        if change is REMOVE:
            value.pop(key, None)
        else:
            value[key] = change
    return json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")


def translation(**changes):
    value = {
        "name": "Образец",
        "description": "Плагин, который существует, чтобы его проверяли.",
        "settings": {"mode": {"label": "Режим", "options": {"a": "А", "b": "Б"}}, "rows": {"label": "Строки"}},
    }
    for key, change in changes.items():
        if change is REMOVE:
            value.pop(key, None)
        else:
            value[key] = change
    return json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")


def passport(**changes):
    value = {"format": 1, "name": "Test plugins", "description": "Plugins for the tests."}
    for key, change in changes.items():
        if change is REMOVE:
            value.pop(key, None)
        else:
            value[key] = change
    return json.dumps(value, indent=2).encode("utf-8")


def licence(first="Copyright 2026 Ada Lovelace", separator="\n\n", text=None):
    return (first + separator + (APACHE if text is None else text)).encode("utf-8")


RUN_SH = b"#!/bin/sh\necho '{ \"rows\": [ { \"text\": \"hello\" } ], \"ttl\": 120 }'\n"


def plugin(folder="plugins/sample", **overrides):
    files = {
        folder + "/manifest.json": manifest(),
        folder + "/manifest.ru.json": translation(),
        folder + "/README.md": b"# Sample\n\nA plugin that exists to be checked.\n",
        folder + "/LICENSE": licence(),
        folder + "/run.sh": (EXECUTABLE, RUN_SH),
    }
    for name, content in overrides.items():
        path = folder + "/" + name
        if content is REMOVE:
            files.pop(path, None)
        else:
            files[path] = content
    return files


def repository(**overrides):
    """The good repository; overrides are {file name in the plugin: content}, REMOVE to drop one."""
    files = {
        "udeck-plugins.json": passport(),
        "README.md": b"# Test plugins\n",
        "LICENSE": APACHE.encode("utf-8"),
    }
    files.update(plugin(**overrides))
    return files


def png(width, height, padding=0):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    pixels = b"".join(b"\0" + b"\0" * (width * 3) for _ in range(height))
    body = check_repo.PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    body += chunk(b"IDAT", zlib.compress(pixels))
    if padding:
        body += chunk(b"tEXt", b"x\0" + b"y" * padding)
    return body + chunk(b"IEND", b"")


def png_of_size(total):
    """A 1x1 PNG padded to exactly `total` bytes."""
    padding = total - len(png(1, 1)) - 12 - 2
    made = png(1, 1, padding)
    assert len(made) == total
    return made


class Repository:
    """A git repository in a temporary folder, whose HEAD is exactly the files it was given."""

    def __init__(self, test):
        self.path = tempfile.mkdtemp(prefix="check-repo-test-")
        test.addCleanup(shutil.rmtree, self.path, True)
        self.git("init", "-q", "-b", "main", "--template=")
        # A Mac's git sets this, and fast-import then folds Run.sh and run.sh
        # into one path -- the very collision rule 7 exists to catch.
        self.git("config", "core.ignorecase", "false")

    def git(self, *args, stdin=None):
        done = subprocess.run(["git", "-C", self.path] + list(args), input=stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if done.returncode != 0:
            raise AssertionError("git {} failed: {}".format(" ".join(args[:1]), done.stderr.decode()))
        return done.stdout.decode("utf-8")

    def commit(self, files, message=SIGNED, parent=None):
        """Commits exactly these files, {path: bytes or (mode, bytes)}, and makes it HEAD."""
        stream = io.BytesIO()

        def data(content):
            stream.write(b"data " + str(len(content)).encode() + b"\n" + content + b"\n")

        stream.write(b"commit refs/heads/main\n")
        stream.write(b"committer Ada Lovelace <ada@example.com> 1790596800 +0000\n")
        data(message.encode("utf-8"))
        if parent:
            stream.write(b"from " + parent.encode() + b"\n")
        stream.write(b"deleteall\n")
        for path, content in sorted(files.items()):
            mode, content = content if isinstance(content, tuple) else (FILE, content)
            if mode == SUBMODULE:
                stream.write("M {} {} {}\n".format(mode, content, path).encode("utf-8"))
            else:
                stream.write("M {} inline {}\n".format(mode, path).encode("utf-8"))
                data(content)
        stream.write(b"done\n")
        self.git("fast-import", "--quiet", "--done", "--force", stdin=stream.getvalue())
        return self.git("rev-parse", "HEAD").strip()


class CheckTestCase(unittest.TestCase):
    def check(self, files, official=True):
        repo = Repository(self)
        repo.commit(files)
        _, report = check_repo.check(repo.path, official=official)
        return report.findings

    def assertClean(self, files, official=True):
        """Neither an error nor a warning."""
        self.assertEqual([], [str(f) for f in self.check(files, official)])

    def assertBreaks(self, rule, files, official=True, also=()):
        """Errors for this rule, and none for any other but those named in `also`."""
        errors = [f for f in self.check(files, official) if f.level == "error"]
        rules = {f.rule for f in errors}
        self.assertIn(str(rule), rules, "expected an error for rule {}, got: {}".format(
            rule, [str(f) for f in errors]))
        self.assertLessEqual(rules, {str(rule)} | {str(r) for r in also}, [str(f) for f in errors])
        return errors


class TheGoodRepository(CheckTestCase):
    def test_passes_as_the_official_repository(self):
        self.assertClean(repository())

    def test_passes_as_any_repository(self):
        self.assertClean(repository(), official=False)

    def test_the_pinned_licence_is_the_licence_at_the_top_of_this_repository(self):
        import hashlib
        self.assertEqual(check_repo.APACHE_2_0_SHA256,
                         hashlib.sha256(check_repo.normalised_licence(APACHE).encode()).hexdigest())

    def test_the_official_rules_apply_only_with_official(self):
        files = repository(**{"data.bin": b"\0\1\2", "LICENSE": REMOVE,
                              "manifest.json": manifest(author=REMOVE)})
        self.assertClean(files, official=False)
        self.assertBreaks(14, files, also=(15, 16))

    def test_it_reads_the_commit_not_the_working_tree(self):
        repo = Repository(self)
        repo.commit(repository())
        with open(os.path.join(repo.path, "stray.txt"), "w") as f:
            f.write("never committed")
        with open(os.path.join(repo.path, ".gitattributes"), "w") as f:
            f.write("plugins/** export-ignore\n")
        os.makedirs(os.path.join(repo.path, "plugins", "Not An Id"))
        with open(os.path.join(repo.path, "plugins", "Not An Id", "x"), "w") as f:
            f.write("x")
        _, report = check_repo.check(repo.path, official=True)
        self.assertEqual([], [str(f) for f in report.findings])


class Passport(CheckTestCase):
    def test_missing(self):
        files = repository()
        del files["udeck-plugins.json"]
        self.assertBreaks("passport", files)

    def test_not_json(self):
        self.assertBreaks("passport", dict(repository(), **{"udeck-plugins.json": b"{ format: 1 }"}))

    def test_not_an_object(self):
        self.assertBreaks("passport", dict(repository(), **{"udeck-plugins.json": b"[1]"}))

    def test_format(self):
        for value in (2, 0, "1", REMOVE, True):
            with self.subTest(format=value):
                self.assertBreaks("passport", dict(repository(), **{"udeck-plugins.json": passport(format=value)}))

    def test_name(self):
        for value in ("", " ", "n" * 65, 5, REMOVE):
            with self.subTest(name=value):
                self.assertBreaks("passport", dict(repository(), **{"udeck-plugins.json": passport(name=value)}))
        self.assertClean(dict(repository(), **{"udeck-plugins.json": passport(name="n" * 64)}))
        self.assertClean(dict(repository(), **{"udeck-plugins.json": passport(name="ю" * 64)}))

    def test_description(self):
        self.assertBreaks("passport", dict(repository(), **{"udeck-plugins.json": passport(description="d" * 281)}))
        self.assertBreaks("passport", dict(repository(), **{"udeck-plugins.json": passport(description=[])}))
        self.assertClean(dict(repository(), **{"udeck-plugins.json": passport(description="d" * 280)}))
        self.assertClean(dict(repository(), **{"udeck-plugins.json": passport(description=REMOVE)}))

    def test_an_unknown_field(self):
        self.assertBreaks("passport", dict(repository(), **{"udeck-plugins.json": passport(nmae="typo")}))

    def test_a_field_given_twice(self):
        twice = b'{ "format": 1, "name": "One", "name": "Two" }'
        self.assertBreaks("passport", dict(repository(), **{"udeck-plugins.json": twice}))

    def test_a_link_is_not_a_passport(self):
        # A link's blob is its target path; this one's target happens to be a
        # valid passport, so only the rule against links can refuse it.
        self.assertBreaks("passport", dict(repository(), **{"udeck-plugins.json": (LINK, passport())}))


class Rule1PluginFolders(CheckTestCase):
    def test_a_folder_name_that_is_not_an_id(self):
        for name in ("Sample", "my plugin", "-sample", "_sample", ".sample", "sam+ple", "a" * 65):
            with self.subTest(name=name):
                files = repository()
                files = {k: v for k, v in files.items() if not k.startswith("plugins/")}
                files.update(plugin("plugins/" + name, **{"manifest.json": manifest(id=name)}))
                self.assertBreaks(1, files)

    def test_the_longest_id(self):
        name = "a" * 64
        files = {k: v for k, v in repository().items() if not k.startswith("plugins/")}
        files.update(plugin("plugins/" + name, **{"manifest.json": manifest(id=name)}))
        self.assertClean(files)

    def test_ids_use_dots_dashes_and_underscores(self):
        name = "0my.plug-in_2"
        files = {k: v for k, v in repository().items() if not k.startswith("plugins/")}
        files.update(plugin("plugins/" + name, **{"manifest.json": manifest(id=name)}))
        self.assertClean(files)

    def test_plugins_must_be_a_folder(self):
        files = {k: v for k, v in repository().items() if not k.startswith("plugins/")}
        files["plugins"] = b"not a folder\n"
        self.assertBreaks(1, files)

    def test_a_repository_with_no_plugins_is_still_a_repository(self):
        self.assertClean({k: v for k, v in repository().items() if not k.startswith("plugins/")})


class Rule2OnlyFoldersInPlugins(CheckTestCase):
    def test_a_file(self):
        self.assertBreaks(2, dict(repository(), **{"plugins/README.md": b"# All the plugins\n"}))

    def test_a_link(self):
        self.assertBreaks(2, dict(repository(), **{"plugins/other": (LINK, b"sample")}))

    def test_a_submodule(self):
        self.assertBreaks(2, dict(repository(), **{"plugins/vendored": (SUBMODULE, "1" * 40)}))


class Rule3Manifest(CheckTestCase):
    def test_missing(self):
        self.assertBreaks(3, repository(**{"manifest.json": REMOVE}))

    def test_nested_a_level_too_deep(self):
        files = {k: v for k, v in repository().items() if not k.startswith("plugins/")}
        files.update(plugin("plugins/group/sample"))
        self.assertBreaks(3, files, also=(10, 16))

    def test_not_json(self):
        self.assertBreaks(3, repository(**{"manifest.json": b'{ "id": "sample", }'}))

    def test_not_an_object(self):
        self.assertBreaks(3, repository(**{"manifest.json": b'["sample"]'}))

    def test_nan_is_not_json(self):
        errors = self.assertBreaks(3, repository(**{"manifest.json": manifest().replace(b'"interval": 60',
                                                                                        b'"interval": NaN')}))
        self.assertIn("is not valid JSON", " ".join(f.message for f in errors))

    def test_a_field_given_twice(self):
        twice = manifest().replace(b'"interval": 60', b'"interval": 60, "interval": 5')
        self.assertBreaks(3, repository(**{"manifest.json": twice}))

    def test_the_id_is_the_folder_name(self):
        self.assertBreaks(3, repository(**{"manifest.json": manifest(id="other")}))

    def test_what_the_decoder_refuses(self):
        cases = {
            "id missing": {"id": REMOVE},
            "id not an id": {"id": "Sample"},
            "name not a string": {"name": 5},
            "version a number": {"version": 1},
            "api a string": {"api": "1"},
            "api true": {"api": True},
            "api a fraction": {"api": 1.5},
            "kind unknown": {"kind": "daemon"},
            "kind missing": {"kind": REMOVE},
            "run a string": {"run": "./run.sh"},
            "run with a number": {"run": ["./run.sh", 5]},
            "interval a string": {"interval": "60"},
            "author a number": {"author": 5},
            "minUDeck a number": {"minUDeck": 1},
            "permissions a list": {"permissions": ["sysctl"]},
            "exec a string": {"permissions": {"exec": "sysctl"}},
            "screen a string": {"permissions": {"screen": "yes"}},
            "settings an object": {"settings": {"key": "mode"}},
            "setting without a default": {"settings": [{"key": "x", "type": "bool", "label": "X"}]},
            "setting default a fraction": {"settings": [{"key": "x", "type": "int", "default": 1.5, "label": "X"}]},
            "string setting default a fraction": {"settings": [{"key": "x", "type": "string", "default": 1.5,
                                                               "label": "X"}]},
            "setting max past Int": {"settings": [{"key": "x", "type": "int", "default": 1, "max": 2 ** 63,
                                                   "label": "X"}]},
            "setting type unknown": {"settings": [{"key": "x", "type": "float", "default": 1, "label": "X"}]},
            "option without a label": {"settings": [{"key": "x", "type": "enum", "default": "a", "label": "X",
                                                     "options": [{"value": "a"}]}]},
            "window width a string": {"window": {"defaultWidth": "4"}},
            "an integer past Int": {"window": {"defaultWidth": 2 ** 63}},
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.assertBreaks(3, repository(**{"manifest.json": manifest(**change)}))

    def test_null_is_the_same_as_leaving_an_optional_field_out(self):
        self.assertClean(repository(**{"manifest.json": manifest(homepage=None, window=None, settings=None),
                                       "manifest.ru.json": translation(settings=REMOVE)}))

    def test_what_makes_a_manifest_unusable(self):
        cases = {
            "api from the future": {"api": 2},
            "blank name": {"name": "  "},
            "empty run": {"run": []},
            "blank command": {"run": [" "]},
            "no interval": {"interval": REMOVE},
            "no timeout": {"timeout": REMOVE},
            "interval zero": {"interval": 0},
            "interval past a day": {"interval": 86401},
            "interval below a second": {"interval": 0.5, "timeout": 0.1},
            "timeout below the floor": {"timeout": 0.01},
            "timeout as long as interval": {"timeout": 60},
            "resident": {"kind": "resident"},
            "two settings with one key": {"settings": [
                {"key": "x", "type": "bool", "default": True, "label": "X"},
                {"key": "x", "type": "bool", "default": False, "label": "Y"}]},
            "setting key": {"settings": [{"key": "X", "type": "bool", "default": True, "label": "X"}]},
            "setting blank label": {"settings": [{"key": "x", "type": "bool", "default": True, "label": " "}]},
            "setting default of another type": {"settings": [{"key": "x", "type": "bool", "default": 1, "label": "X"}]},
            "enum without options": {"settings": [{"key": "x", "type": "enum", "default": "a", "label": "X"}]},
            "enum default not an option": {"settings": [{"key": "x", "type": "enum", "default": "c", "label": "X",
                                                         "options": [{"value": "a", "label": "A"}]}]},
            "min above max": {"settings": [{"key": "x", "type": "int", "default": 5, "min": 9, "max": 1,
                                            "label": "X"}]},
            "min above max on a string": {"settings": [{"key": "x", "type": "string", "default": "a", "min": 9,
                                                        "max": 1, "label": "X"}]},
            "default below min": {"settings": [{"key": "x", "type": "int", "default": 0, "min": 1, "label": "X"}]},
            "default above max": {"settings": [{"key": "x", "type": "int", "default": 11, "max": 10,
                                                "label": "X"}]},
            "window too wide": {"window": {"defaultWidth": 13}},
            "window min wider than default": {"window": {"defaultWidth": 3, "minWidth": 4}},
            "window too tall": {"window": {"defaultHeight": 25}},
            "window min taller than default": {"window": {"defaultHeight": 2, "minHeight": 3}},
            "window min below one": {"window": {"minHeight": 0}},
        }
        for name, change in cases.items():
            with self.subTest(name):
                files = repository(**{"manifest.json": manifest(**change)})
                if "settings" in change:
                    files["plugins/sample/manifest.ru.json"] = translation(settings=REMOVE)
                self.assertBreaks(3, files)

    def test_a_duration_of_zero_is_named_as_such(self):
        errors = self.assertBreaks(3, repository(**{"manifest.json": manifest(interval=0)}))
        self.assertIn('"interval" must be greater than zero', " ".join(f.message for f in errors))

    def test_the_limits_themselves_are_allowed(self):
        cases = {
            "interval of a day": {"interval": 86400, "timeout": 2},
            "interval of a second": {"interval": 1, "timeout": 0.05},
            "the widest window": {"window": {"defaultWidth": 12, "minWidth": 12, "defaultHeight": 24,
                                             "minHeight": 24}},
            "the smallest window": {"window": {"defaultWidth": 1, "minWidth": 1, "defaultHeight": 1,
                                               "minHeight": 1}},
            "a default on the bounds": {"settings": [{"key": "x", "type": "int", "default": 1, "min": 1, "max": 1,
                                                      "label": "X"}]},
        }
        for name, change in cases.items():
            with self.subTest(name):
                files = repository(**{"manifest.json": manifest(**change)})
                if "settings" in change:
                    files["plugins/sample/manifest.ru.json"] = translation(settings=REMOVE)
                self.assertClean(files)


class Rule4Versions(CheckTestCase):
    def test_versions_that_are_not_major_minor_patch(self):
        for version in ("1.2", "v1.2.0", "1.2.0-beta", "01.2.0", "1.02.0", "1.2.00", "1.2.0.0", "",
                        "1000000000.0.0", "1.2.0\n", "١.2.0", "1١.2.0"):
            with self.subTest(version=version):
                self.assertBreaks(4, repository(**{"manifest.json": manifest(version=version)}))

    def test_versions_that_are(self):
        for version in ("0.1.0", "1.2.0", "10.0.3", "0.0.0", "999999999.999999999.999999999"):
            with self.subTest(version=version):
                self.assertClean(repository(**{"manifest.json": manifest(version=version)}))

    def test_min_udeck(self):
        self.assertBreaks(4, repository(**{"manifest.json": manifest(minUDeck="0.6")}))
        self.assertBreaks(4, repository(**{"manifest.json": manifest(minUDeck="v0.6.0")}))
        self.assertClean(repository(**{"manifest.json": manifest(minUDeck="0.6.0")}))


class Rule5TheProducer(CheckTestCase):
    def test_committed_without_the_executable_bit(self):
        self.assertBreaks(5, repository(**{"run.sh": (FILE, RUN_SH)}))

    def test_not_in_the_folder(self):
        self.assertBreaks(5, repository(**{"manifest.json": manifest(run=["./missing.sh"])}))

    def test_a_folder_is_not_a_producer(self):
        files = repository(**{"manifest.json": manifest(run=["./bin"]), "bin/tool": (EXECUTABLE, RUN_SH)})
        self.assertBreaks(5, files)

    def test_climbing_out_of_the_folder(self):
        files = repository(**{"manifest.json": manifest(run=["../other/run.sh"])})
        files.update(plugin("plugins/other", **{"manifest.json": manifest(id="other")}))
        self.assertBreaks(5, files)

    def test_a_link_to_the_producer(self):
        files = repository(**{"manifest.json": manifest(run=["./start"]), "start": (LINK, b"run.sh")})
        self.assertBreaks(5, files, also=(6,))

    def test_a_producer_in_a_subfolder(self):
        self.assertClean(repository(**{"manifest.json": manifest(run=["bin/tool"]), "run.sh": REMOVE,
                                       "bin/tool": (EXECUTABLE, RUN_SH)}))

    def test_a_bare_name_or_an_absolute_path_is_not_in_the_folder(self):
        for command in ("sh", "/bin/sh"):
            with self.subTest(command=command):
                self.assertClean(repository(**{"manifest.json": manifest(run=[command, "run.sh"]),
                                               "run.sh": (FILE, RUN_SH)}))


class Rule6FilesAndFoldersOnly(CheckTestCase):
    def test_a_symbolic_link(self):
        self.assertBreaks(6, repository(**{"lib": (LINK, b"../other")}))

    def test_a_submodule(self):
        self.assertBreaks(6, repository(**{"vendor": (SUBMODULE, "2" * 40)}))


class Rule7Names(CheckTestCase):
    def test_names_that_are_refused(self):
        for name in ("Run Me.sh", ".DS_Store", ".hidden/x.txt", "café.txt", "a:b", "notes (1).txt",
                     "n" * 256, "dir with space/x.txt"):
            with self.subTest(name=name):
                self.assertBreaks(7, repository(**{name: b"text\n"}))

    def test_names_that_are_allowed(self):
        self.assertClean(repository(**{"n" * 255: b"text\n", "A-z_0.9": b"text\n", "lib/x.sh": b"text\n"}))

    def test_two_names_that_differ_only_in_case(self):
        self.assertBreaks(7, repository(**{"Run.sh": b"one\n", "run.sh": (EXECUTABLE, RUN_SH)}))

    def test_two_folders_that_differ_only_in_case(self):
        self.assertBreaks(7, repository(**{"Lib/a.txt": b"one\n", "lib/b.txt": b"two\n"}))

    def test_the_same_name_in_two_folders_is_fine(self):
        self.assertClean(repository(**{"a/run.sh": b"one\n", "b/RUN.sh": b"two\n"}))


class Rule8Limits(CheckTestCase):
    def test_files(self):
        base = len(repository(**{})) - 3  # the plugin's own files
        allowed = {"f{:03d}.txt".format(i): b"x\n" for i in range(check_repo.MAX_FILES - base)}
        self.assertClean(repository(**allowed))
        allowed["one-more.txt"] = b"x\n"
        self.assertBreaks(8, repository(**allowed))

    def test_one_file(self):
        self.assertClean(repository(**{"data.txt": b"a" * (5 * MiB)}))
        self.assertBreaks(8, repository(**{"data.txt": b"a" * (5 * MiB + 1)}))

    def test_in_total(self):
        base = sum(len(v[1] if isinstance(v, tuple) else v)
                   for k, v in repository().items() if k.startswith("plugins/"))
        filler = {"a.txt": b"a" * (5 * MiB), "b.txt": b"b" * (5 * MiB - base)}
        self.assertClean(repository(**filler))
        filler["c.txt"] = b"c"
        self.assertBreaks(8, repository(**filler))

    def test_depth(self):
        self.assertClean(repository(**{"/".join("d" * 8) + "/x.txt": b"x\n"}))
        self.assertBreaks(8, repository(**{"/".join("d" * 9) + "/x.txt": b"x\n"}))


class Rule9WhatAnArchiveWouldChange(CheckTestCase):
    def test_an_lfs_pointer(self):
        pointer = (b"version https://git-lfs.github.com/spec/v1\n"
                   b"oid sha256:4d7a214614ab2935c943f9e0ff69d22eadbb8f32b1258daaa5e2ca24d17e2393\nsize 12345\n")
        self.assertBreaks(9, repository(**{"data.txt": pointer}))

    def test_attributes_that_change_an_archive(self):
        for attributes in ("plugins/** filter=lfs", "plugins/sample/ export-ignore",
                           "plugins/sample/README.md export-subst", "plugins/ export-ignore",
                           "*.sh filter=crlf", "[attr]hidden export-ignore\nplugins/sample/run.sh hidden"):
            with self.subTest(attributes=attributes):
                files = dict(repository(), **{".gitattributes": (attributes + "\n").encode()})
                self.assertBreaks(9, files)

    def test_attributes_that_do_not(self):
        attributes = b"*.sh text eol=lf\ndocs/** export-ignore\nplugins/sample/data.txt -export-ignore\n"
        self.assertClean(dict(repository(**{"data.txt": b"x\n"}), **{".gitattributes": attributes}))


    def test_a_personal_attributes_file_is_not_the_repository(self):
        home = tempfile.mkdtemp(prefix="check-repo-test-")
        self.addCleanup(shutil.rmtree, home, True)
        attributes = os.path.join(home, "attributes")
        with open(attributes, "w") as f:
            f.write("* export-ignore\n")
        config = os.path.join(home, "gitconfig")
        with open(config, "w") as f:
            f.write("[core]\n\tattributesFile = {}\n".format(attributes))
        repo = Repository(self)
        repo.commit(repository())
        os.environ["GIT_CONFIG_GLOBAL"] = config
        try:
            _, report = check_repo.check(repo.path, official=True)
        finally:
            os.environ["GIT_CONFIG_GLOBAL"] = GIT_ENVIRONMENT["GIT_CONFIG_GLOBAL"]
        self.assertEqual([], [str(f) for f in report.findings])


class Rule10Readme(CheckTestCase):
    def test_missing(self):
        self.assertBreaks(10, repository(**{"README.md": REMOVE}))

    def test_only_in_a_subfolder(self):
        self.assertBreaks(10, repository(**{"README.md": REMOVE, "docs/README.md": b"# Sample\n"}))

    def test_a_link_to_one(self):
        self.assertBreaks(10, repository(**{"README.md": (LINK, b"docs/README.md"),
                                            "docs/README.md": b"# Sample\n"}), also=(6,))


class Rule11Translations(CheckTestCase):
    def test_none_is_a_warning_not_an_error(self):
        findings = self.check(repository(**{"manifest.ru.json": REMOVE}))
        self.assertEqual([("warning", "11")], [(f.level, f.rule) for f in findings])

    def test_a_file_that_is_not_a_language_does_not_count(self):
        for name in ("manifest.backup.json", "manifest.e.json", "manifest.pt-B.json", "manifest.en-US-x.json",
                     "manifest.r1.json", "manifest..json", "manifest.pt-B_R.json", "manifest.ru.json.txt"):
            with self.subTest(name=name):
                findings = self.check(repository(**{"manifest.ru.json": REMOVE, name: translation()}))
                self.assertEqual([("warning", "11")], [(f.level, f.rule) for f in findings])

    def test_a_language_with_a_region_counts(self):
        self.assertClean(repository(**{"manifest.ru.json": REMOVE, "manifest.pt-BR.json": translation()}))


class Rule12OnlyWhatTheContractDefines(CheckTestCase):
    def test_in_the_manifest(self):
        cases = {
            "a misspelt field": {"permisions": {"exec": ["sysctl"]}},
            "restart, which the contract does not describe": {"restart": {"mode": "never"}},
            "in permissions": {"permissions": {"exce": ["sysctl"]}},
            "in window": {"window": {"width": 4}},
            "in a setting": {"settings": [{"key": "mode", "type": "enum", "default": "a", "lable": "Mode",
                                           "label": "Mode", "options": [{"value": "a", "label": "A"},
                                                                        {"value": "b", "label": "B"}]}]},
            "in an option": {"settings": [{"key": "mode", "type": "enum", "default": "a", "label": "Mode",
                                           "options": [{"value": "a", "label": "A", "hint": "?"},
                                                       {"value": "b", "label": "B"}]}]},
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.assertBreaks(12, repository(**{"manifest.json": manifest(**change),
                                                    "manifest.ru.json": translation(settings=REMOVE)}))

    def test_every_field_the_contract_defines_is_known(self):
        everything = manifest(
            homepage="https://example.com", minUDeck="0.6.0",
            permissions={"read": ["~/x"], "write": ["~/y"], "exec": ["df"], "network": ["example.com"],
                         "screen": False, "secrets": ["token"]},
            settings=[{"key": "mode", "type": "enum", "default": "a", "label": "Mode", "help": "Which one.",
                       "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
                      {"key": "rows", "type": "int", "default": 3, "min": 0, "max": 10, "label": "Rows"}])
        self.assertClean(repository(**{"manifest.json": everything}))

    def test_in_a_translation(self):
        cases = {
            "a misspelt field": translation(nmae="Образец"),
            "a field only manifest.json has": translation(run=["./other.sh"]),
            "in a setting": translation(settings={"mode": {"lable": "Режим"}}),
            "a setting manifest.json does not declare": translation(settings={"mdoe": {"label": "Режим"}}),
            "an option manifest.json does not declare": translation(settings={"mode": {"options": {"c": "В"}}}),
            "a label that is not a string": translation(settings={"mode": {"label": 5}}),
            "an option label that is not a string": translation(settings={"mode": {"options": {"a": 5}}}),
            "a name that is not a string": translation(name=["Образец"]),
            "not an object": '"Образец"'.encode(),
            "not JSON": '{ "name": "Образец", }'.encode(),
        }
        for name, content in cases.items():
            with self.subTest(name):
                self.assertBreaks(12, repository(**{"manifest.ru.json": content}))


class Rule13CarriageReturns(CheckTestCase):
    def test_in_an_executable(self):
        self.assertBreaks(13, repository(**{"run.sh": (EXECUTABLE, RUN_SH.replace(b"\n", b"\r\n"))}))

    def test_in_a_file_that_is_not_executable(self):
        self.assertClean(repository(**{"README.md": b"# Sample\r\n\r\nWritten on Windows.\r\n"}))


class Rule14TextOnly(CheckTestCase):
    def test_a_binary(self):
        self.assertBreaks(14, repository(**{"data.bin": b"\x00\x01\x02\x03"}))

    def test_text_that_is_not_utf8(self):
        for content in (b"caf\xe9\n", b"\xff\xfe", b"\xed\xa0\x80", b"\xc0\xaf"):
            with self.subTest(content=content):
                self.assertBreaks(14, repository(**{"notes.txt": content}))

    def test_text_that_is(self):
        self.assertClean(repository(**{"notes.txt": "Время работы — ✓\n".encode(), "empty.txt": b""}))

    def test_an_icon(self):
        self.assertClean(repository(**{"icon.png": png(512, 512)}))
        self.assertBreaks(14, repository(**{"icon.png": png(513, 1)}))
        self.assertBreaks(14, repository(**{"icon.png": png(1, 513)}))

    def test_a_picture_under_another_name_or_place(self):
        for name in ("picture.png", "icon.PNG", "screenshot-4.png", "screenshot-0.png", "img/icon.png",
                     "screenshot.png", "run.dat"):
            with self.subTest(name=name):
                self.assertBreaks(14, repository(**{name: png(16, 16)}))

    def test_a_picture_is_never_executable(self):
        # A PNG's signature holds a carriage return, so rule 13 has its say too.
        self.assertBreaks(14, repository(**{"icon.png": (EXECUTABLE, png(16, 16))}), also=(13,))

    def test_a_file_named_like_a_picture_that_is_not_one(self):
        self.assertBreaks(14, repository(**{"icon.png": b"a picture, honestly\n"}))

    def test_a_png_without_its_header(self):
        self.assertBreaks(14, repository(**{"icon.png": check_repo.PNG_SIGNATURE + b"\0" * 32}))

    def test_screenshots(self):
        self.assertClean(repository(**{"screenshot-1.png": png_of_size(MiB), "screenshot-2.png": png(640, 400),
                                       "screenshot-3.png": png(640, 400)}))
        self.assertBreaks(14, repository(**{"screenshot-1.png": png_of_size(MiB + 1)}))


class Rule15Author(CheckTestCase):
    def test_missing_or_blank(self):
        for author in (REMOVE, "", "  ", None):
            with self.subTest(author=author):
                self.assertBreaks(15, repository(**{"manifest.json": manifest(author=author)}))


class Rule16Licence(CheckTestCase):
    def test_missing(self):
        self.assertBreaks(16, repository(**{"LICENSE": REMOVE}))

    def test_the_copyright_line(self):
        for first in ("Copyright 2026 Someone Else", "Copyright Ada Lovelace", "Copyright (c) 2026 Ada Lovelace",
                      "Copyright 26 Ada Lovelace", "© 2026 Ada Lovelace", ""):
            with self.subTest(first=first):
                self.assertBreaks(16, repository(**{"LICENSE": licence(first=first)}))

    def test_the_blank_line(self):
        # Exactly one finding, and about the blank line: the licence that
        # follows is intact and must not be reported as modified.
        errors = self.assertBreaks(16, repository(**{"LICENSE": licence(separator="\n")}))
        self.assertEqual(1, len(errors), [str(f) for f in errors])
        self.assertIn("must have a blank line after the copyright line", errors[0].message)

    def test_a_licence_that_is_not_apache(self):
        self.assertBreaks(16, repository(**{"LICENSE": licence(text="MIT License\n\nPermission is hereby granted")}))

    def test_one_word_changed(self):
        changed = APACHE.replace("perpetual,", "temporary,", 1)
        self.assertNotEqual(APACHE, changed)
        self.assertBreaks(16, repository(**{"LICENSE": licence(text=changed)}))

    def test_one_line_joined(self):
        changed = APACHE.replace("\n", " ", 1)
        self.assertBreaks(16, repository(**{"LICENSE": licence(text=changed)}))

    def test_the_licence_as_apache_org_has_it_and_with_windows_line_endings(self):
        self.assertClean(repository(**{"LICENSE": licence(text="\n" + APACHE)}))
        self.assertClean(repository(**{"LICENSE": licence().replace(b"\n", b"\r\n")}))


class Rule17SignOff(CheckTestCase):
    def history(self, *messages):
        repo = Repository(self)
        base = repo.commit(repository(), message=UNSIGNED)
        head = base
        for message in messages:
            head = repo.commit(repository(), message=message, parent=head)
        return repo, base, head

    def sign_off_errors(self, *messages):
        repo, base, head = self.history(*messages)
        _, report = check_repo.check(repo.path, official=True, base=base, head=head)
        return [f for f in report.findings if f.level == "error"]

    def test_every_commit_signed_off(self):
        self.assertEqual([], self.sign_off_errors(SIGNED, "Fix\n\nSigned-off-by: B <b@example.com>\n"))

    def test_the_base_is_not_part_of_the_pull_request(self):
        self.assertEqual([], self.sign_off_errors())

    def test_one_commit_not_signed_off(self):
        errors = self.sign_off_errors(SIGNED, UNSIGNED, SIGNED)
        self.assertEqual(["17"], [f.rule for f in errors])

    def test_sign_offs_that_are_not(self):
        for message in ("Fix\n\nSigned-off-by: Ada Lovelace\n", "Fix\n\nSigned-off-by: <ada@example.com>\n",
                        "Fix\n\nsigned-off-by: Ada <ada@example.com>\n", "Fix\n\nSigned-off-by: Ada <>\n",
                        "Fix\n\n Signed-off-by: Ada <ada@example.com>\n",
                        "Fix\n\nReviewed-by: Ada <ada@example.com>\n"):
            with self.subTest(message=message):
                self.assertEqual(["17"], [f.rule for f in self.sign_off_errors(message)])

    def test_a_commit_that_is_not_there(self):
        repo, base, _ = self.history(SIGNED)
        with self.assertRaises(check_repo.CheckFailed):
            check_repo.check(repo.path, official=True, base=base, head="f" * 40)


class CommandLine(CheckTestCase):
    def run_main(self, files, *arguments):
        repo = Repository(self)
        repo.commit(files)
        out = io.StringIO()
        with redirect_stdout(out):
            status = check_repo.main(["--repo", repo.path] + list(arguments))
        return status, out.getvalue()

    def test_clean(self):
        status, out = self.run_main(repository(), "--official")
        self.assertEqual(0, status)
        self.assertIn("0 errors, 0 warnings", out)

    def test_an_error_fails(self):
        status, out = self.run_main(repository(**{"README.md": REMOVE}), "--official")
        self.assertEqual(1, status)
        self.assertIn("error: plugins/sample/README.md: ", out)
        self.assertIn("[rule 10]", out)

    def test_a_warning_does_not_fail(self):
        status, out = self.run_main(repository(**{"manifest.ru.json": REMOVE}), "--official")
        self.assertEqual(0, status)
        self.assertIn("warning: plugins/sample: ", out)

    def test_not_a_repository_is_not_a_failure_of_the_repository(self):
        folder = tempfile.mkdtemp(prefix="check-repo-test-")
        self.addCleanup(shutil.rmtree, folder, True)
        out = io.StringIO()
        with redirect_stdout(out):
            status = check_repo.main(["--repo", folder])
        self.assertEqual(2, status)
        self.assertIn("could not check", out.getvalue())

    def test_the_script_runs_as_a_program(self):
        repo = Repository(self)
        repo.commit(repository(**{"README.md": REMOVE}))
        done = subprocess.run([sys.executable, "-B", os.path.join(HERE, "check-repo.py"), "--official"],
                              cwd=repo.path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(1, done.returncode, done.stderr.decode())
        self.assertIn(b"[rule 10]", done.stdout)


if __name__ == "__main__":
    unittest.main()
