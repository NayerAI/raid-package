"""Client for the RAID competition system: fetch challenges, check and submit solutions."""
import importlib.util
import json
import os
import posixpath
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

__all__ = ["Raid", "RaidError"]
__version__ = "0.2.0"

DEFAULT_URL = "https://raid.mlsec.tu-berlin.de"
PARTS = ("source", "data", "scoring")  # source/ is unpacked into the challenge directory itself
OWN = {"data", "scoring"}  # fetched with the challenge, never submitted
SKIP = {"__pycache__", ".git", ".ipynb_checkpoints", ".venv", "venv", ".DS_Store"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class RaidError(Exception):
    pass


class Raid:
    """raid = Raid(api_key); print(raid.howto())"""

    def __init__(self, api_key=None, url=None, path="."):
        self.api_key = (api_key or os.environ.get("RAID_KEY") or "").strip()
        self.url = (url or os.environ.get("RAID_URL") or DEFAULT_URL).rstrip("/")
        self.path = Path(path)

    def howto(self):
        """Explain the workflow."""
        return self._json("/api/howto")["text"]

    def fetch(self, challenge, force=False):
        """Download a challenge into ./<challenge>/: its files, data/ and scoring/.

        An existing directory is left untouched unless force=True, which overwrites the challenge files.
        """
        target = self.path / _check(challenge)
        if target.exists() and not force:
            print(f"{target}/ already exists. Fetch with force=True (task-fetch --force) to download it again "
                  "(this overwrites the challenge files).")
            return target
        with tempfile.TemporaryDirectory() as tmp:
            for part in PARTS:
                with self._request("GET", f"/api/challenges/{challenge}/{part}.tar.gz", timeout=300) as r, \
                        open(Path(tmp) / f"{part}.tar.gz", "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
            target.mkdir(parents=True, exist_ok=True)
            for part in PARTS:
                _extract(Path(tmp) / f"{part}.tar.gz", target, part)
        print(f"Fetched {challenge} into {target}/. Start with {target / 'README.txt'}.")
        return target

    def check(self, challenge):
        """Score ./<challenge>/ locally: scoring/score.py's score() on data/. Returns the score."""
        target = (self.path / _check(challenge)).resolve()
        script = target / "scoring" / "score.py"
        if not script.is_file():
            raise RaidError(f"{script} not found. Fetch the challenge first.")
        paths = [str(script.parent), str(target)]  # score.py imports the solution, e.g. `from main import md5`
        sys.path[:0] = paths
        try:
            spec = importlib.util.spec_from_file_location("score", script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            value = module.score(target / "data")
        finally:
            for p in paths:
                sys.path.remove(p)
            for name, mod in list(sys.modules.items()):  # a later check sees the current solution
                if target in Path(getattr(mod, "__file__", None) or "/").resolve().parents:
                    del sys.modules[name]
        print(f"Score: {value}")
        return value

    def submit(self, challenge):
        """Pack ./<challenge>/ without data/ and scoring/ and submit it for scoring. Returns the submission id."""
        target = self.path / _check(challenge)
        if not target.is_dir():
            raise RaidError(f"{target}/ not found. Fetch the challenge first.")
        required = self._json(f"/api/challenges/{challenge}")["required_files"]
        missing = [f for f in required if not (target / f).is_file()]
        if missing:
            raise RaidError(f"Missing required files in {target}/: " + ", ".join(missing))
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "submission.tar.gz"
            _pack(target, archive)
            size = archive.stat().st_size
            with open(archive, "rb") as f, self._request(
                    "POST", f"/api/submit/{challenge}", data=f, timeout=900,
                    headers={"Content-Type": "application/gzip", "Content-Length": str(size)}) as r:
                result = json.load(r)
        print(f"Submitted {challenge} ({size / 1e6:.2f} MB) as submission {result['id']}.")
        return result["id"]

    def wait(self, submission_id, poll=5):
        """Wait until a submission is scored, print its log and return its details."""
        print("Waiting for the scoring (Ctrl-C stops waiting, the scoring continues) ...", flush=True)
        while True:
            sub = self._json(f"/api/submissions/{int(submission_id)}")
            if sub["status"] in ("success", "failed"):
                break
            time.sleep(poll)
        print(sub["log"] or "No log output.")
        print(f"Submission {sub['id']}: {sub['status']}" + ("" if sub["score"] is None else f", score {sub['score']:.4f}"))
        return sub

    def status(self, challenge=None):
        """Print your submissions (newest first) and return them as a list of dicts."""
        query = f"?challenge={_check(challenge)}" if challenge else ""
        subs = self._json(f"/api/submissions{query}")["submissions"]
        print(f"{'ID':>6}  {'CHALLENGE':<20} {'STATUS':<8} {'SCORE':>10}  SUBMITTED")
        for s in subs:
            score = "" if s["score"] is None else f"{s['score']:.4f}"
            print(f"{s['id']:>6}  {s['challenge']:<20} {s['status']:<8} {score:>10}  {s['submitted_at'][:19]}")
        if not subs:
            print("No submissions yet.")
        return subs

    def log(self, submission_id):
        """Return the scoring log of one of your submissions."""
        return self._json(f"/api/submissions/{int(submission_id)}")["log"]

    def _request(self, method, endpoint, data=None, headers=None, timeout=60):
        if not self.api_key:
            raise RaidError("No API key. Pass --api-key or set RAID_KEY.")
        req = urllib.request.Request(self.url + endpoint, data=data, method=method, headers={
            "Authorization": f"Bearer {self.api_key}", "User-Agent": f"raid/{__version__}", **(headers or {})})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            try:
                message = json.load(e).get("error")
            except (ValueError, AttributeError):
                message = None
            raise RaidError(message or f"{e.code} {e.reason}") from None
        except urllib.error.URLError as e:
            raise RaidError(f"Cannot reach {self.url}: {e.reason}") from None

    def _json(self, endpoint):
        with self._request("GET", endpoint) as r:
            return json.load(r)


def _check(challenge):
    if not isinstance(challenge, str) or not NAME_RE.match(challenge):
        raise RaidError(f"Invalid challenge name {challenge!r}.")
    return challenge


def _extract(archive, target, part):
    """Extract only regular files and directories below part/, never outside target. source/ maps to target."""
    root = target.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            name = posixpath.normpath(member.name)
            if name != part and not name.startswith(part + "/"):
                raise RaidError(f"Unexpected entry {member.name!r} in the {part} archive.")
            if part == "source":
                name = name[len("source/"):] or "."
            dest = (root / name).resolve()
            if dest != root and root not in dest.parents:
                raise RaidError(f"Unsafe entry {member.name!r} in the {part} archive.")
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                if member.mode & 0o111:
                    dest.chmod(0o755)


def _normalize(info):
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o755 if info.isdir() or info.mode & 0o111 else 0o644
    return info


def _pack(target, archive):
    """tar.gz of the challenge directory without data/ and scoring/. Regular files only;
    caches, VCS data and symlinks are left out."""
    with tarfile.open(archive, "w:gz") as tar:
        for dirpath, dirnames, filenames in os.walk(target):
            rel = Path(dirpath).relative_to(target)
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP and not (rel == Path(".") and d in OWN))
            for name in dirnames + sorted(f for f in filenames if f not in SKIP):
                path = Path(dirpath) / name
                if path.is_symlink():
                    print(f"Skipping symlink {path}")
                    continue
                if path.is_dir() or path.is_file():
                    tar.add(path, arcname=(rel / name).as_posix(), recursive=False, filter=_normalize)
