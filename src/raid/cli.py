"""Command line tools: task-fetch, task-check, task-submit, task-show, task-log and task-push (also as `task <command>`)."""
import argparse
import sys

from . import Raid, RaidError


def _run(description, action, key=True, options=(), target=("challenge", "challenge name, e.g. self-assessment"),
         parser=None):
    parser = parser or argparse.ArgumentParser(description=description)
    if key:
        parser.add_argument("--api-key", help="your API key (default: $RAID_KEY)")
    for flag, text in options:
        parser.add_argument(flag, action="store_true", help=text)
    if target:
        parser.add_argument(target[0], help=target[1])
    args = parser.parse_args()
    try:
        action(Raid(getattr(args, "api_key", None)), args)
    except RaidError as e:
        sys.exit(f"Error: {e}")


def fetch():
    _run("Download a challenge into ./<challenge>/.", lambda raid, a: raid.fetch(a.challenge, a.force),
         options=[("--force", "download again and overwrite the challenge files")])


def check():
    """task check [method|artifact] <challenge>: the method, the artifact or both (first the method)."""
    parser = argparse.ArgumentParser(
        description="Check ./<challenge>/ locally on its data in the container raid-<challenge>, built like our "
                    "backend's with the challenge's setup.sh. `task check method <challenge>` runs your method, "
                    "`task check artifact <challenge>` scores your artifact, `task check <challenge>` does both.",
        usage="%(prog)s [-h] [options] [method|artifact] challenge")
    parser.add_argument("names", nargs="+", metavar="[method|artifact] challenge", help=argparse.SUPPRESS)

    def action(raid, a):
        names = a.names
        if len(names) == 2 and names[1] in ("method", "artifact") and names[0] not in ("method", "artifact"):
            names = names[::-1]  # task check dawn method
        if len(names) > 2 or len(names) == 2 and names[0] not in ("method", "artifact"):
            parser.error("expected [method|artifact] <challenge>")
        part, challenge = (None, names[0]) if len(names) == 1 else names
        raid.check(challenge, part, a.hidden, a.reference, container=not a.no_container, rebuild=a.rebuild,
                   quiet=a.quiet)

    _run(None, action, key=False, target=None, parser=parser, options=[
        ("--quiet", "hide the output of the container build and of what scoring runs (method/run, make, tests)"),
        ("--rebuild", "build the container raid-<challenge> again even if setup.sh did not change"),
        ("--no-container", "check on this machine instead of in the container (needs the challenge's packages)"),
        ("--hidden", "challenge authors: score on hidden/ instead of the data"),
        ("--reference", "challenge authors: score reference/ instead of the student files")])


def submit():
    _run("Submit ./<challenge>/ for scoring (without its data, scoring/ and .raidignore patterns).",
         lambda raid, a: raid.submit(a.challenge))


def show():
    _run("List your jobs (submissions) with their state and score.", lambda raid, a: raid.status(), target=None)


def log():
    _run("Print the log of a finished job: the output and errors of its scoring run.",
         lambda raid, a: print(raid.log(a.job)), target=("job", "job id, see task-show"))


def push():
    _run("Admins: upload a challenge directory (without reference/) as its new active version. "
         "challenge.yaml supplies the name, the version and the metadata.",
         lambda raid, a: raid.push(a.directory), target=("directory", "challenge directory, e.g. self-assessment"))


def main():
    """`task submit <challenge>` is the same as `task-submit <challenge>`."""
    commands = {"fetch": fetch, "check": check, "submit": submit, "show": show, "log": log, "push": push}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        sys.exit("Usage: task {" + ",".join(commands) + "} ...")
    command = sys.argv.pop(1)
    sys.argv[0] = f"task {command}"
    commands[command]()
