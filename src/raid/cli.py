"""Command line tools: task-fetch, task-check and task-submit."""
import argparse
import sys

from . import Raid, RaidError


def _run(description, action, key=True, force=False):
    parser = argparse.ArgumentParser(description=description)
    if key:
        parser.add_argument("--api-key", help="your API key (default: $RAID_KEY)")
    if force:
        parser.add_argument("--force", action="store_true", help="download again and overwrite the challenge files")
    parser.add_argument("challenge", help="challenge name, e.g. md5")
    args = parser.parse_args()
    try:
        action(Raid(getattr(args, "api_key", None)), args)
    except RaidError as e:
        sys.exit(f"Error: {e}")


def fetch():
    _run("Download a challenge into ./<challenge>/.", lambda raid, a: raid.fetch(a.challenge, a.force), force=True)


def check():
    _run("Score ./<challenge>/ locally on data/.", lambda raid, a: raid.check(a.challenge), key=False)


def submit():
    def action(raid, args):
        sid = raid.submit(args.challenge)
        try:
            raid.wait(sid)
        except KeyboardInterrupt:
            sys.exit("\nStopped waiting. Your submission is still being scored.")
    _run("Submit ./<challenge>/ (without data/ and scoring/), wait for the score and print the log.", action)
