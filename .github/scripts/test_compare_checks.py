"""Tests for compare-checks.py: what agreeing means, and that a disagreement fails.

Two kinds. Outputs written here, line by line, for each thing the comparison
looks at. And check-repo.py's real output -- the copy beside this file, run on
a repository these tests commit with fixed dates, so that its commits are the
same everywhere -- held against what udeck-plugin 0.6.1 printed for those very
commits, recorded below: they agree where they should, and where udeck-plugin
is right and check-repo.py is not, the comparison fails.

    python3 -I -B -m unittest discover -s .github/scripts -p 'test_*.py' -v
"""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("compare_checks", os.path.join(HERE, "compare-checks.py"))
compare_checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare_checks)

with open(os.path.join(HERE, "..", "..", "LICENSE"), encoding="utf-8") as _licence:
    APACHE = _licence.read()

COMMIT = "0123456789ab"
CLEAN = "checked 1 plugin folder at {} as the official repository: 0 errors, 0 warnings\n".format(COMMIT)


def summary(errors=0, warnings=0, folders=1, commit=COMMIT, layer=" as the official repository"):
    return "checked {} plugin folder{} at {}{}: {} error{}, {} warning{}\n".format(
        folders, "" if folders == 1 else "s", commit, layer, errors, "" if errors == 1 else "s",
        warnings, "" if warnings == 1 else "s")


def run(python_text, python_status, swift_text, swift_status):
    """compare-checks.py's exit status and what it printed, for two outputs."""
    with tempfile.TemporaryDirectory() as scratch:
        python = os.path.join(scratch, "python.txt")
        swift = os.path.join(scratch, "udeck-plugin.txt")
        for path, text in ((python, python_text), (swift, swift_text)):
            with open(path, "wb") as file:
                file.write(text.encode("utf-8", "surrogateescape"))
        printed = io.StringIO()
        with redirect_stdout(printed):
            status = compare_checks.main(["--check-repo-py", python, str(python_status),
                                          "--udeck-plugin", swift, str(swift_status)])
    return status, printed.getvalue()


class Agreeing(unittest.TestCase):
    def assertAgree(self, python_text, python_status, swift_text, swift_status):
        status, printed = run(python_text, python_status, swift_text, swift_status)
        self.assertEqual(status, 0, printed)
        self.assertIn("They agree", printed)
        return printed

    def test_two_clean_outputs(self):
        self.assertAgree(CLEAN, 0, CLEAN, 0)

    def test_the_same_findings_in_other_words(self):
        python = ("error: plugins/uptime/README.md: is missing; it is what a reviewer reads first [rule 10]\n"
                  "warning: plugins/uptime: has no manifest.<lang>.json [rule 11]\n" + summary(1, 1))
        swift = ("warning: plugins/uptime: says it another way: and with a colon [rule 11]\n"
                 "error: plugins/uptime/README.md: is missing [rule 10]\n" + summary(1, 1))
        self.assertAgree(python, 1, swift, 1)

    def test_rules_18_to_20_are_listed_and_not_compared(self):
        swift = ('error: plugins/uptime/manifest.json: the folder changed, and "version" is 1.0.0 [rule 18]\n'
                 'warning: plugins/uptime/manifest.json: "minUDeck" is 0.6.0, which does nothing [rule 19]\n'
                 'error: plugins/uptime/manifest.json: "name" holds U+000A, a line break [rule 20]\n'
                 + summary(2, 1))
        printed = self.assertAgree(CLEAN, 0, swift, 1)
        self.assertIn("not compared: error 18 plugins/uptime/manifest.json; error 20 plugins/uptime/manifest.json; "
                      "warning 19 plugins/uptime/manifest.json", printed)
        self.assertIn("the same exit status (0)", printed)

    def test_rule_18_with_no_path(self):
        # What a clone without history gets: a finding with no path at all.
        swift = ("error: rule 18 not checked: HEAD has no parent here, and a strict check does not pass what it "
                 "could not check — fetch full history (fetch-depth: 0 on GitHub, GIT_DEPTH: 0 on GitLab) [rule 18]\n"
                 + summary(1, 0))
        printed = self.assertAgree(CLEAN, 0, swift, 1)
        self.assertIn("not compared: error 18 \n", printed + "\n")

    def test_findings_compared_as_a_set(self):
        # The corpus compares sets: two findings of one rule at one path are
        # the same finding however many messages they come in.
        python = ("error: plugins/uptime/manifest.json: \"interval\" is 0; at least 1 [rule 3]\n"
                  "error: plugins/uptime/manifest.json: \"timeout\" is 0; at least 0.05 [rule 3]\n" + summary(2))
        swift = "error: plugins/uptime/manifest.json: \"interval\" and \"timeout\" [rule 3]\n" + summary(1)
        self.assertAgree(python, 1, swift, 1)

    def test_notes_are_said_and_not_compared(self):
        swift = "note: the working copy holds changes the check did not see\n" + CLEAN
        printed = self.assertAgree(CLEAN, 0, swift, 0)
        self.assertIn("note: the working copy holds changes", printed)

    def test_neither_could_check(self):
        printed = self.assertAgree("could not check: git log: fatal: bad revision\nand a second line\n", 2,
                                   "could not check: not a commit\n", 2)
        self.assertIn("neither could check", printed)

    def test_the_passport_and_a_commit_are_places(self):
        python = ('error: udeck-plugins.json: "format" is 2; this check, like uDeck, reads format 1 [passport]\n'
                  "error: commit 0123456789ab: \"Add\" has no Signed-off-by line [rule 17]\n"
                  "error: plugins: must be a folder holding one folder per plugin [rule 1]\n"
                  + summary(3, 0, folders=0))
        swift = ('error: udeck-plugins.json: "format" is 2 [passport]\n'
                 "error: plugins: must be a folder [rule 1]\n"
                 "error: commit 0123456789ab: \"Add\" has no Signed-off-by line: sign it [rule 17]\n"
                 + summary(3, 0, folders=0))
        self.assertAgree(python, 1, swift, 1)

    def test_a_path_holding_a_colon_is_cut_the_same_way(self):
        line = 'error: plugins/uptime/a: b.txt: the name uses a character other than letters [rule 7]\n'
        self.assertAgree(line + summary(1), 1, line + summary(1), 1)


class Disagreeing(unittest.TestCase):
    def assertDisagree(self, python_text, python_status, swift_text, swift_status, *said):
        status, printed = run(python_text, python_status, swift_text, swift_status)
        self.assertEqual(status, 1, printed)
        self.assertIn("They disagree", printed)
        for words in said:
            self.assertIn(words, printed)
        return printed

    def test_a_finding_only_udeck_plugin_has(self):
        swift = ('error: plugins/uptime/manifest.json: "run" starts with "sub/../../uptime/uptime.sh", which leaves '
                 'the plugin folder on its way [rule 5]\n' + summary(1))
        self.assertDisagree(CLEAN, 0, swift, 1, "only udeck-plugin: error 5 plugins/uptime/manifest.json",
                            "exit status: check-repo.py 0, udeck-plugin 1 over the passport and rules 1-17")

    def test_a_finding_only_check_repo_py_has(self):
        python = "warning: plugins/uptime: has no manifest.<lang>.json [rule 11]\n" + summary(0, 1)
        self.assertDisagree(python, 0, CLEAN, 0, "only check-repo.py: warning 11 plugins/uptime")

    def test_the_same_rule_at_another_level(self):
        python = "warning: plugins/uptime: has no manifest.<lang>.json [rule 11]\n" + summary(0, 1)
        swift = "error: plugins/uptime: has no manifest.<lang>.json [rule 11]\n" + summary(1, 0)
        self.assertDisagree(python, 0, swift, 1, "only check-repo.py: warning 11 plugins/uptime",
                            "only udeck-plugin: error 11 plugins/uptime")

    def test_the_same_path_under_another_rule(self):
        python = "error: plugins/uptime/manifest.json: has the field \"restart\" [rule 12]\n" + summary(1)
        swift = ("error: plugins/uptime/manifest.json: is not a manifest uDeck can read [rule 3]\n"
                 "error: plugins/uptime/manifest.json: has the field \"restart\" [rule 12]\n" + summary(2))
        self.assertDisagree(python, 1, swift, 1, "only udeck-plugin: error 3 plugins/uptime/manifest.json")

    def test_the_same_rule_at_another_path(self):
        python = "error: plugins/uptime/README.md: is missing [rule 10]\n" + summary(1)
        swift = "error: plugins/other/README.md: is missing [rule 10]\n" + summary(1)
        self.assertDisagree(python, 1, swift, 1, "only check-repo.py: error 10 plugins/uptime/README.md",
                            "only udeck-plugin: error 10 plugins/other/README.md")

    def test_a_rule_beyond_20_is_compared(self):
        # Only 18-20 are known to be udeck-plugin's alone; a rule a later
        # release adds is a disagreement until someone says otherwise.
        swift = "error: plugins/uptime/manifest.json: something new [rule 21]\n" + summary(1)
        self.assertDisagree(CLEAN, 0, swift, 1, "only udeck-plugin: error 21 plugins/uptime/manifest.json")

    def test_another_commit(self):
        self.assertDisagree(CLEAN, 0, summary(commit="ba9876543210"), 0,
                            "commit: check-repo.py says 0123456789ab, udeck-plugin says ba9876543210")

    def test_another_number_of_plugin_folders(self):
        self.assertDisagree(CLEAN, 0, summary(folders=2), 0, "plugin folders: check-repo.py says 1, udeck-plugin says 2")

    def test_another_layer(self):
        self.assertDisagree(summary(layer=""), 0, summary(layer=" strictly"), 0,
                            "layer: check-repo.py says with neither --strict nor --official, udeck-plugin says strictly")

    def test_only_one_could_check(self):
        self.assertDisagree("could not check: 0123 is not a commit\n", 2, CLEAN, 0,
                            "check-repo.py could not check the repository, and udeck-plugin could")
        self.assertDisagree(CLEAN, 0, "could not check: no history\n", 2,
                            "udeck-plugin could not check the repository, and check-repo.py could")

    def test_a_name_that_is_not_utf8_is_said_two_ways(self):
        # check-repo.py says the byte as \xe9, udeck-plugin as U+FFFD: both
        # report rule 7, and the comparison says they disagree rather than
        # guess that two different texts name one path.
        python = "error: plugins/uptime/caf\\xe9.txt: the name uses a character other than letters [rule 7]\n"
        swift = "error: plugins/uptime/caf�.txt: the name uses a character other than letters [rule 7]\n"
        self.assertDisagree(python + summary(1), 1, swift + summary(1), 1,
                            "only check-repo.py: error 7 plugins/uptime/caf\\xe9.txt")


class Unreadable(unittest.TestCase):
    def assertUnreadable(self, python_text, python_status, swift_text, swift_status, words):
        status, printed = run(python_text, python_status, swift_text, swift_status)
        self.assertEqual(status, 2, printed)
        self.assertIn("could not compare: ", printed)
        self.assertIn(words, printed)

    def test_a_line_that_is_none_of_the_three(self):
        self.assertUnreadable(CLEAN, 0, "Segmentation fault\n" + CLEAN, 0,
                              "udeck-plugin, line 1: neither a finding, a note nor the summary")

    def test_a_message_over_two_lines(self):
        swift = "error: plugins/uptime/manifest.json: one line\nand the next [rule 3]\n" + summary(1)
        self.assertUnreadable(CLEAN, 0, swift, 1, "udeck-plugin, line 1: neither")

    def test_no_summary(self):
        self.assertUnreadable("error: plugins/uptime/README.md: is missing [rule 10]\n", 1, CLEAN, 0,
                              "check-repo.py: the last line is not the summary")

    def test_nothing_at_all(self):
        self.assertUnreadable(CLEAN, 0, "", 0, "udeck-plugin printed nothing and exited 0")

    def test_a_line_after_the_summary(self):
        self.assertUnreadable(CLEAN + "error: plugins: late [rule 1]\n", 1, CLEAN, 0,
                              "check-repo.py, line 1: neither")

    def test_a_summary_that_does_not_count_the_findings(self):
        self.assertUnreadable("error: plugins/uptime/README.md: is missing [rule 10]\n" + summary(2), 1, CLEAN, 0,
                              "the summary says 2 errors and 0 warnings, and it printed 1 and 0")

    def test_an_exit_status_its_findings_do_not_make(self):
        self.assertUnreadable(CLEAN, 1, CLEAN, 0, "check-repo.py: 0 errors, and exit status 1")
        self.assertUnreadable(CLEAN, 0, "error: plugins/uptime/README.md: is missing [rule 10]\n" + summary(1), 0,
                              "udeck-plugin: 1 errors, and exit status 0")

    def test_an_exit_status_no_check_gives(self):
        self.assertUnreadable(CLEAN, 0, "", 127, "udeck-plugin exited 127; a check exits 0, 1 or 2")
        self.assertUnreadable(CLEAN, 0, CLEAN, "zero", "'zero' is not an exit status")

    def test_an_output_that_is_not_there(self):
        printed = io.StringIO()
        with redirect_stdout(printed):
            status = compare_checks.main(["--check-repo-py", os.path.join(HERE, "no such file"), "0",
                                          "--udeck-plugin", os.path.join(HERE, "no such file"), "0"])
        self.assertEqual(status, 2)
        self.assertIn("could not compare: ", printed.getvalue())


class Places(unittest.TestCase):
    def test_places(self):
        place = compare_checks.place
        self.assertEqual(place("udeck-plugins.json: must be a JSON object"), "udeck-plugins.json")
        self.assertEqual(place("plugins: must be a folder"), "plugins")
        self.assertEqual(place("plugins/uptime: has no manifest.<lang>.json"), "plugins/uptime")
        self.assertEqual(place("commit 0123456789ab: \"Add\" has no Signed-off-by line"), "commit 0123456789ab")
        self.assertEqual(place("rule 18 not checked: HEAD has no parent here"), "")
        self.assertEqual(place("cannot compare with origin/main: no common history here"), "")
        self.assertEqual(place("plugins/uptime/manifest.json"), "")  # a message with no `: ` names no path
        self.assertEqual(place("commit 0123: too short to be a commit's place"), "")


# --- check-repo.py's real output, against udeck-plugin 0.6.1's ------------------

GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Ada Lovelace",
    "GIT_AUTHOR_EMAIL": "ada@example.com",
    "GIT_AUTHOR_DATE": "2026-10-09T12:00:00Z",
    "GIT_COMMITTER_NAME": "Ada Lovelace",
    "GIT_COMMITTER_EMAIL": "ada@example.com",
    "GIT_COMMITTER_DATE": "2026-10-09T12:00:00Z",
}
SIGNED_OFF = "\n\nSigned-off-by: Ada Lovelace <ada@example.com>\n"
RUN_SH = "#!/bin/sh\nprintf '{\"title\": \"Sample\"}\\n'\n"


def manifest(**changes):
    value = {
        "id": "sample", "name": "Sample", "version": "1.0.0", "api": 1, "kind": "poll",
        "description": "A sample.", "author": "Ada Lovelace", "run": ["./run.sh"], "interval": 60, "timeout": 2,
    }
    value.update(changes)
    return json.dumps(value, indent=2) + "\n"


BASE_FILES = {
    "udeck-plugins.json": json.dumps({"format": 1, "name": "Samples"}, indent=2) + "\n",
    "plugins/sample/manifest.json": manifest(),
    "plugins/sample/manifest.ru.json": json.dumps({"name": "Образец"}, ensure_ascii=False, indent=2) + "\n",
    "plugins/sample/README.md": "# Sample\n",
    "plugins/sample/LICENSE": "Copyright 2026 Ada Lovelace\n\n" + APACHE,
    "plugins/sample/run.sh": RUN_SH,
}

# What udeck-plugin 0.6.1 (the release .github/udeck-plugin.lock names when
# this was written) printed for each head below, checked as validate checks a
# pull request: check-repo --strict --official --base <base> --head <head>.
# The base is 5ba07d19fb38 in all three.
UDECK_PLUGIN_0_6_1 = {
    "readme-missing": (1, (
        "error: plugins/sample/README.md: is missing; it is what a reviewer and an installer read first [rule 10]\n"
        "checked 1 plugin folder at e1d10817b67a as the official repository: 1 error, 0 warnings\n")),
    "unchanged-version": (1, (
        'error: plugins/sample/manifest.json: the folder changed, and "version" is 1.0.0 where 5ba07d19fb38 has '
        "1.0.0; any change goes out as a new version \u2014 1.0.1 or later [rule 18]\n"
        "checked 1 plugin folder at d80a9a0a655e as the official repository: 1 error, 0 warnings\n")),
    "run-leaves-the-folder": (1, (
        'error: plugins/sample/manifest.json: "run" starts with "sub/../../sample/run.sh", which leaves the plugin '
        "folder on its way [rule 5]\n"
        "checked 1 plugin folder at 8771a2bdaf49 as the official repository: 1 error, 0 warnings\n")),
}


def commit(repo, files, message):
    """Commits exactly `files` (path: text), with run.sh executable, and answers the commit."""
    for name in os.listdir(repo):
        if name != ".git":
            full = os.path.join(repo, name)
            shutil.rmtree(full) if os.path.isdir(full) else os.remove(full)
    for path, text in sorted(files.items()):
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
        os.chmod(full, 0o755 if path.endswith(".sh") else 0o644)
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "--no-verify", "--no-gpg-sign", "-F", "-"],
                   input=message.encode("utf-8"), check=True)
    return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                          check=True).stdout.decode("ascii").strip()


HEADS = {
    "readme-missing": (dict(BASE_FILES, **{"plugins/sample/manifest.json": manifest(version="1.0.1")}),
                       "plugins/sample/README.md"),
    "unchanged-version": (dict(BASE_FILES, **{"plugins/sample/run.sh": RUN_SH + "# changed\n"}), None),
    "run-leaves-the-folder": (dict(BASE_FILES, **{"plugins/sample/manifest.json": manifest(
        version="1.0.1", run=["sub/../../sample/run.sh"])}), None),
}


class AgainstTheRealOutputs(unittest.TestCase):
    """check-repo.py run here, udeck-plugin 0.6.1 as it printed for the same commits."""

    @classmethod
    def setUpClass(cls):
        cls._saved = {key: os.environ.get(key) for key in GIT_ENVIRONMENT}
        os.environ.update(GIT_ENVIRONMENT)
        cls._scratch = tempfile.TemporaryDirectory()
        cls.heads = {}
        for name, (files, removed) in HEADS.items():
            repo = os.path.join(cls._scratch.name, name)
            subprocess.run(["git", "init", "-q", "--template=", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "core.autocrlf", "false"], check=True)
            cls.base = commit(repo, BASE_FILES, "Add sample" + SIGNED_OFF)
            files = {path: text for path, text in files.items() if path != removed}
            cls.heads[name] = (repo, commit(repo, files, "Change sample" + SIGNED_OFF))

    @classmethod
    def tearDownClass(cls):
        cls._scratch.cleanup()
        for key, value in cls._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def check_repo_py(self, name):
        repo, head = self.heads[name]
        done = subprocess.run([sys.executable, "-I", "-B", os.path.join(HERE, "check-repo.py"), "--repo", repo,
                               "--ref", head, "--official", "--base", self.base, "--head", head],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=HERE)
        return done.stdout.decode("utf-8", "surrogateescape"), done.returncode

    def test_the_commits_are_the_recorded_ones(self):
        # udeck-plugin's outputs below name these commits; a fixture that
        # built other ones would compare outputs of two different things.
        self.assertEqual(self.base[:12], "5ba07d19fb38")
        for name, (_, head) in self.heads.items():
            status, text = UDECK_PLUGIN_0_6_1[name]
            self.assertIn(" at {} as the official repository".format(head[:12]), text, name)

    def test_readme_missing_agrees(self):
        text, status = self.check_repo_py("readme-missing")
        self.assertEqual(status, 1, text)
        swift_status, swift = UDECK_PLUGIN_0_6_1["readme-missing"]
        code, printed = run(text, status, swift, swift_status)
        self.assertEqual(code, 0, printed)
        self.assertIn("error 10 plugins/sample/README.md", printed)

    def test_a_version_left_alone_agrees_over_rules_1_to_17(self):
        text, status = self.check_repo_py("unchanged-version")
        self.assertEqual(status, 0, text)
        swift_status, swift = UDECK_PLUGIN_0_6_1["unchanged-version"]
        code, printed = run(text, status, swift, swift_status)
        self.assertEqual(code, 0, printed)
        self.assertIn("not compared: error 18 plugins/sample/manifest.json", printed)

    def test_a_run_path_that_leaves_the_folder_disagrees(self):
        # P05 of uDeck's corpus: check-repo.py normalises the path and passes
        # it, udeck-plugin refuses it as uDeck does -- the comparison fails.
        text, status = self.check_repo_py("run-leaves-the-folder")
        self.assertEqual(status, 0, text)
        swift_status, swift = UDECK_PLUGIN_0_6_1["run-leaves-the-folder"]
        code, printed = run(text, status, swift, swift_status)
        self.assertEqual(code, 1, printed)
        self.assertIn("only udeck-plugin: error 5 plugins/sample/manifest.json", printed)


if __name__ == "__main__":
    unittest.main()
