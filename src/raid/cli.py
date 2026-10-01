"""Command line tools: task-fetch, task-check, task-submit and task-push."""
import argparse
import sys

from . import Raid, RaidError


def _run(description, action, key=True, options=(), target=("challenge", "challenge name, e.g. self-assessment")):
    parser = argparse.ArgumentParser(description=description)
    if key:
        parser.add_argument("--api-key", help="your API key (default: $RAID_KEY)")
    for flag, text in options:
        parser.add_argument(flag, action="store_true", help=text)
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
    _run("Score ./<challenge>/ locally on data/.", lambda raid, a: raid.check(a.challenge, a.hidden, a.reference),
         key=False, options=[("--hidden", "challenge authors: score on hidden/ instead of data/"),
                             ("--reference", "challenge authors: score reference/ instead of the student files")])


def submit():
    def action(raid, args):
        sid = raid.submit(args.challenge)
        try:
            raid.wait(sid)
        except KeyboardInterrupt:
            sys.exit("\nStopped waiting. Your submission is still being scored.")
    _run("Submit ./<challenge>/ (without data/, scoring/ and .raidignore patterns), wait for the score and print the log.", action)


def push():
    _run("Admins: upload a challenge directory (without reference/) as its new active version. "
         "challenge.yaml supplies the name, the version and the metadata of a new challenge.",
         lambda raid, a: raid.push(a.directory), target=("directory", "challenge directory, e.g. self-assessment"))
