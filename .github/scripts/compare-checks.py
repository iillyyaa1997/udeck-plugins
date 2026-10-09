#!/usr/bin/env python3
"""Hold check-repo.py and `udeck-plugin check-repo` to one answer.

The official repository is moving from check-repo.py to `udeck-plugin
check-repo --strict --official`, built from the Swift library uDeck itself
runs (uDeck's docs/plugin-repository.md, "The official repository"). Until
the two have agreed long enough for check-repo.py to go, validate runs both on
the same commit and this script compares what they printed. They agree when:

* both checked the repository, or neither could (exit status 2 from both);
* both read the same commit, found as many plugin folders, and checked it as
  the same layer (as the official repository, or not);
* they report the same findings for the passport and rules 1-17, compared as
  the corpus that froze check-repo.py's answers in uDeck compares them: level,
  rule and path, as a set, the words aside;
* and so the same exit status, udeck-plugin's counted over those rules alone.

Rules 18, 19 and 20 -- a changed plugin's version goes up, `minUDeck`, a name
and a description of one line each -- are udeck-plugin's alone: check-repo.py
never had them. Its findings of them are listed, never compared. Any other
rule udeck-plugin reports is compared, and so disagrees: a rule check-repo.py
does not have is a rule nobody has said may be left out.

Each output is read whole and held to itself before anything is compared:
every line a finding, a note (udeck-plugin's) or, last, the summary, whose
counts are the findings' and agree with the exit status. An output that does
not read that way is not compared at all.

    compare-checks.py --check-repo-py <output> <exit status>
                      --udeck-plugin <output> <exit status>

Exit status: 0 they agree, 1 they do not, 2 an output could not be read.
Whether each check passed is not this script's question: validate fails on
either check's own exit status as well.

Temporary by design: it goes with check-repo.py.
"""

from __future__ import annotations

import argparse
import re
import sys

# The rules udeck-plugin has and check-repo.py never had (uDeck's
# CorpusReplay.newRules: rule 18 needs history, 19 and 20 came with it).
UDECK_PLUGIN_ONLY = frozenset(("18", "19", "20"))

# `error: plugins/uptime/README.md: is missing; ... [rule 10]` -- the shape
# both checks print, word for word (check-repo.py's Finding.__str__ and
# udeck-plugin's CheckFinding.description).
FINDING = re.compile(r"(error|warning): (.*) \[(passport|rule [1-9][0-9]*)\]")
NOTE = "note: "
SUMMARY = re.compile(
    r"checked ([0-9]+) plugin folders? at ([0-9a-f]{0,12})( as the official repository| strictly)?: "
    r"([0-9]+) errors?, ([0-9]+) warnings?")
COMMIT_PLACE = re.compile(r"commit [0-9a-f]{12}")


class Unreadable(Exception):
    """An output that is not what the check prints."""


class Output:
    """What one check printed, and the exit status it gave."""

    def __init__(self, who, status, could_not_check=None, findings=(), notes=(), commit=None, folders=None,
                 layer=None):
        self.who = who
        self.status = status
        self.could_not_check = could_not_check
        self.findings = list(findings)
        self.notes = list(notes)
        self.commit = commit
        self.folders = folders
        self.layer = layer

    def keys(self, rules=None):
        """The findings as the corpus compares them: `level rule path`, a set."""
        return {key(f) for f in self.findings if rules is None or rules(f[1])}


def key(finding):
    level, rule, path = finding
    return "{} {} {}".format(level, rule, path)


def place(text):
    """The path a finding names, from what follows its level.

    Both checks print `<path>: <message>`, or the message alone when there is
    no path, and the message can hold `: ` as well. Every path a check names
    is the passport, `plugins`, a path under it, or `commit <sha>` -- so the
    text before the first `: ` is the path when it is one of those, and there
    is no path when it is not. A path that holds `: ` itself is cut at it, the
    same way in both outputs.
    """
    head, separator, _ = text.partition(": ")
    if separator and (head in ("udeck-plugins.json", "plugins") or head.startswith("plugins/")
                      or COMMIT_PLACE.fullmatch(head)):
        return head
    return ""


def read(who, text, status):
    """An Output from what `who` printed, or Unreadable saying why not."""
    if status not in (0, 1, 2):
        raise Unreadable("{} exited {}; a check exits 0, 1 or 2".format(who, status))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if status == 2:
        # Nothing was checked, and what it says about why -- git's own words,
        # perhaps over several lines -- is said, not compared.
        first = lines[0] if lines else "(nothing on standard output)"
        return Output(who, status, could_not_check=first)
    if not lines:
        raise Unreadable("{} printed nothing and exited {}".format(who, status))
    findings, notes = [], []
    for number, line in enumerate(lines[:-1], start=1):
        match = FINDING.fullmatch(line)
        if match:
            level, rest, label = match.groups()
            rule = "passport" if label == "passport" else label[len("rule "):]
            findings.append((level, rule, place(rest)))
        elif line.startswith(NOTE):
            notes.append(line[len(NOTE):])
        else:
            raise Unreadable("{}, line {}: neither a finding, a note nor the summary: {!r}".format(who, number, line))
    summary = SUMMARY.fullmatch(lines[-1])
    if not summary:
        raise Unreadable("{}: the last line is not the summary: {!r}".format(who, lines[-1]))
    folders, commit, layer, errors, warnings = summary.groups()
    counted = (sum(1 for f in findings if f[0] == "error"), sum(1 for f in findings if f[0] == "warning"))
    if counted != (int(errors), int(warnings)):
        raise Unreadable("{}: the summary says {} errors and {} warnings, and it printed {} and {}".format(
            who, errors, warnings, counted[0], counted[1]))
    if status != (1 if counted[0] else 0):
        raise Unreadable("{}: {} errors, and exit status {}".format(who, counted[0], status))
    return Output(who, status, findings=findings, notes=notes, commit=commit, folders=int(folders),
                  layer=(layer or "").strip() or "with neither --strict nor --official")


def compared(rule):
    return rule not in UDECK_PLUGIN_ONLY


def compare(python, swift):
    """(agree, lines to print) for the two checks' Outputs."""
    said = []
    for output in (python, swift):
        if output.could_not_check is not None:
            said.append("{}: exit 2, could not check -- {}".format(output.who, output.could_not_check))
            continue
        said.append("{}: exit {}, {} at {}, checked {}, {} finding{}".format(
            output.who, output.status, plural(output.folders, "plugin folder"), output.commit, output.layer,
            len(output.findings), "" if len(output.findings) == 1 else "s"))
        for note in output.notes:
            said.append("  note: " + note)
        for finding in sorted(output.keys()):
            said.append("  " + finding)

    if python.could_not_check is not None or swift.could_not_check is not None:
        if python.status == swift.status:
            said.append("They agree: neither could check the repository.")
            return True, said
        said.append("They disagree: {} could not check the repository, and {} could.".format(
            *((python.who, swift.who) if python.status == 2 else (swift.who, python.who))))
        return False, said

    disagreements = []
    for what, one, other in (("commit", python.commit, swift.commit), ("plugin folders", python.folders,
                             swift.folders), ("layer", python.layer, swift.layer)):
        if one != other:
            disagreements.append("{}: {} says {}, {} says {}".format(what, python.who, one, swift.who, other))
    theirs = python.keys()
    ours = swift.keys(compared)
    for finding in sorted(theirs - ours):
        disagreements.append("only {}: {}".format(python.who, finding))
    for finding in sorted(ours - theirs):
        disagreements.append("only {}: {}".format(swift.who, finding))
    counted_status = 1 if any(f.startswith("error ") for f in ours) else 0
    if python.status != counted_status:
        disagreements.append("exit status: {} {}, {} {} over the passport and rules 1-17".format(
            python.who, python.status, swift.who, counted_status))
    own = sorted(swift.keys(lambda rule: not compared(rule)))
    said.append("Rules 18-20, {}'s alone and not compared: {}".format(swift.who, "; ".join(own) if own else "none"))
    if disagreements:
        said.append("They disagree:")
        said.extend("  " + line for line in disagreements)
        return False, said
    said.append("They agree: the same commit, plugin folders and layer, the same findings for the passport "
                "and rules 1-17 ({}), and the same exit status ({}).".format(len(theirs), python.status))
    return True, said


def plural(count, noun):
    return "{} {}{}".format(count, noun, "" if count == 1 else "s")


def status_of(text):
    try:
        return int(text)
    except ValueError:
        raise Unreadable("{!r} is not an exit status".format(text))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare what check-repo.py and udeck-plugin check-repo said.")
    parser.add_argument("--check-repo-py", nargs=2, metavar=("OUTPUT", "STATUS"), required=True,
                        help="check-repo.py's standard output, in a file, and its exit status")
    parser.add_argument("--udeck-plugin", nargs=2, metavar=("OUTPUT", "STATUS"), required=True,
                        help="udeck-plugin check-repo's standard output, in a file, and its exit status")
    arguments = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except AttributeError:
        pass
    try:
        outputs = []
        for who, (path, status) in (("check-repo.py", arguments.check_repo_py),
                                    ("udeck-plugin", arguments.udeck_plugin)):
            with open(path, "rb") as file:
                text = file.read().decode("utf-8", "surrogateescape")
            outputs.append(read(who, text, status_of(status)))
    except (Unreadable, OSError) as failure:
        print("could not compare: {}".format(failure))
        return 2
    agree, said = compare(*outputs)
    for line in said:
        print(line)
    return 0 if agree else 1


if __name__ == "__main__":
    sys.exit(main())
