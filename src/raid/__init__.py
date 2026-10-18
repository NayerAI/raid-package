"""Client for the RAID competition system: fetch challenges, check and submit solutions."""
import ast
import fnmatch
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
__version__ = "0.6.1"

DEFAULT_URL = "https://raid.mlsec.tu-berlin.de"
PARTS = ("source", "data", "scoring")  # source/ is unpacked into the challenge directory itself
# Parts of the challenge directory that are never submitted: these top-level entries and artifact/data/
OWN = {"data", "scoring", "hidden", "reference", "challenge.yaml", "entrypoint.sh"}
ARTIFACT_DATA = Path("artifact", "data")  # the data/ part of challenges split into artifact/ and method/
# The part checked: score.py's function and what it prints. Older challenges define only score(path).
CHECKS = {"method": ("score_method", "Verification score"), "artifact": ("score_artifact", "Artifact score"),
          None: ("create_scoring", "Score")}
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
        """Download a challenge into ./<challenge>/: its files, data/ (artifact/data/) and scoring/.

        An existing directory is left untouched unless force=True, which overwrites the challenge files.
        """
        target = self.path / _check(challenge)
        if target.exists() and not force:
            print(f"{target}/ already exists. Fetch with force=True (task-fetch --force) to download it again "
                  "(this overwrites the challenge files).")
            return target
        info = self._json(f"/api/challenges/{challenge}")
        with tempfile.TemporaryDirectory() as tmp:
            for part in PARTS:
                with self._request("GET", f"/api/challenges/{challenge}/{part}.tar.gz", timeout=300) as r, \
                        open(Path(tmp) / f"{part}.tar.gz", "wb") as f, \
                        _Progress(f"Downloading {part}", r.headers.get("Content-Length")) as bar:
                    while chunk := r.read(1 << 16):
                        f.write(chunk)
                        bar.update(len(chunk))
            target.mkdir(parents=True, exist_ok=True)
            for part in PARTS:
                _extract(Path(tmp) / f"{part}.tar.gz", target, part)
        print(f"Fetched {challenge} (version {info['version']}) into {target}/. Start with {target / 'README.txt'}.")
        return target

    def check(self, challenge, part=None, hidden=False, reference=False, container=True, rebuild=False, quiet=False):
        """Check ./<challenge>/ locally with scoring/score.py on its data (artifact/data/ or data/). Returns the score.

        part="method" calls score.py's score_method() (the verification score), part="artifact" its score_artifact()
        (the artifact score) and part=None its create_scoring(), which runs both, with the mode "local" (or "hidden"). It runs in the container raid-<challenge>, built like the
        backend's sandbox with the challenge's setup.sh (Docker, else udocker) and rebuilt when setup.sh changes or
        rebuild=True; container=False runs it on this machine instead. The output of the build and of everything
        score.py runs (method/run, make, tests) is shown unless quiet=True; score.py reads $RAID_VERBOSE for it.

        For challenge authors: hidden=True scores on hidden/, reference=True scores the files
        in reference/ in place of the student files. Raises RaidError when scoring stops without a score.
        """
        if part not in CHECKS:
            raise RaidError(f"Invalid check {part!r}, use method or artifact.")
        target = (self.path / _check(challenge)).resolve()
        script = target / "scoring" / "score.py"
        dataset = target / "hidden" if hidden else _data(target)
        solution = target / "reference" if reference else target
        for path in (script, dataset, solution):
            if not path.exists():
                raise RaidError(f"{path} not found." + (" Fetch the challenge first." if path == script else ""))
        function, label = CHECKS[part]
        argument = "hidden" if hidden else "local"  # the mode of create_scoring(), score_method(), score_artifact()
        defined = _functions(script)
        if defined is not None and "create_scoring" not in defined and "score" in defined:  # older challenges
            function, argument = "score", dataset
        if defined is not None and function not in defined:
            raise RaidError(f"{challenge} has no separate {part} check (scoring/score.py has no {CHECKS[part][0]}()). "
                            f"Run task check {challenge}." if part else f"scoring/score.py has no {function}().")
        files = "reference/" if reference else "your files"
        what = f"{challenge} {part}" if part else challenge
        here = str(Path.cwd().resolve()) + os.sep  # shown paths are relative, e.g. self-assessment/solution.c
        shown = f"{dataset.relative_to(target).as_posix()}/"
        if container:
            from . import _container
            kind = _container.engine()
            if kind is None:
                raise RaidError("task check needs Docker or udocker (pip install udocker) to build the container "
                                f"raid-{challenge}. Pass --no-container to check on this machine instead.")
            box = _container.Container(challenge, target, kind,
                                       None if quiet else lambda line: _write("    " + line.replace(here, "")))
            print(f"Checking {what}: {files} on {shown} in {box.name} ({kind})")
            try:
                box.ensure(rebuild, status=lambda text: print(_indent(text, here)))
            except _container.ContainerError as e:
                raise RaidError(str(e)) from None
            inside = (str(target) + os.sep).replace(here, "")  # /job/ in the container is the challenge directory
            with _Indented(here, {"/job/": inside}) as out:
                result = box.call(function, argument, solution, out.write)
            for key in ("traceback", "exit"):  # paths in the container
                if isinstance(result.get(key), str):
                    result[key] = result[key].replace("/job/", inside)
        else:
            print(f"Checking {what}: {files} on {shown}")
            result = _call_here(function, script, argument, solution, target, here, quiet)
        if "exit" in result:
            raise RaidError("No score, scoring stopped:\n" + _indent(str(result["exit"]).strip(), here))
        if "error" in result:
            raise RaidError(f"No score, scoring/score.py raised {result['error']}:\n" + _indent(result["traceback"], here)
                            + ("\nInstall the packages the challenge needs, see its setup.sh."
                               if result["error"] == "ModuleNotFoundError" and not container else ""))
        value = result["value"]
        print(f"\n{_bold(f'{label}: {value:.4f}')}")
        return value

    def submit(self, challenge):
        """Pack ./<challenge>/ (see _pack) and submit it for scoring. Returns the submission id."""
        target = self.path / _check(challenge)
        if not target.is_dir():
            raise RaidError(f"{target}/ not found. Fetch the challenge first.")
        required = self._json(f"/api/challenges/{challenge}")["required_files"]
        missing = [f for f in required if not (target / f).is_file()]
        if missing:
            raise RaidError(f"Missing required files in {target}/: " + ", ".join(missing))
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "submission.tar.gz"
            _status(f"Packing {target}/ ...")
            _pack(target, archive)
            size = archive.stat().st_size
            with open(archive, "rb") as f, _Progress("Uploading", size) as bar, self._request(
                    "POST", f"/api/submit/{challenge}", data=bar.reader(f), timeout=900,
                    headers={"Content-Type": "application/gzip", "Content-Length": str(size)}) as r:
                result = json.load(r)
        print("Job submitted!\nRun task-show to see the status of your jobs.")
        return result["id"]

    def push(self, directory):
        """Admins: upload a challenge directory (everything but reference/) as a new, active version.

        Its challenge.yaml supplies the name, the version and the metadata (title, wave, baseline, badge_score,
        badge_points); keys it leaves out keep their current values. Returns the server's summary."""
        target = Path(directory)
        missing = [p for p in ("challenge.yaml", "scoring", "hidden") if not (target / p).exists()]
        if not (target / "data").is_dir() and not (target / ARTIFACT_DATA).is_dir():
            missing.append(f"{ARTIFACT_DATA.as_posix()} (or data)")
        if missing:
            raise RaidError(f"Missing in {target}/: " + ", ".join(missing))
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "challenge.tar.gz"
            _status(f"Packing {target}/ ...")
            _pack(target, archive, own={"reference"})
            size = archive.stat().st_size
            with open(archive, "rb") as f, _Progress("Uploading", size) as bar, self._request(
                    "POST", "/api/admin/push", data=bar.reader(f), timeout=3600,
                    headers={"Content-Type": "application/gzip", "Content-Length": str(size)}) as r:
                result = json.load(r)
        wave = f"wave {result['wave']}" if result["wave"] else "the self-assessment"
        print(f"Pushed {result['name']} version {result['version']} ({size / 1e6:.2f} MB) to {wave}"
              + (", now active." if result["active"] else "."))
        return result

    def wait(self, submission_id, poll=5):
        """Wait until a submission is scored, print its log and return its details."""
        print("Waiting for the scoring (Ctrl-C stops waiting, the scoring continues) ...", flush=True)
        while True:
            sub = self._json(f"/api/submissions/{int(submission_id)}")
            if sub["status"] in ("success", "failed"):
                break
            time.sleep(poll)
        print(sub["log"] or "No log output.")
        print(f"Submission {sub['id']} (version {sub['version']}): {sub['status']}"
              + ("" if sub["score"] is None else f", score {sub['score']:.4f}"))
        return sub

    def status(self, challenge=None):
        """Print your jobs (submissions, newest first) and return them as a list of dicts."""
        query = f"?challenge={_check(challenge)}" if challenge else ""
        result = self._json(f"/api/submissions{query}")
        subs = result["submissions"]
        print(f"{'JOB':>6}  {'CHALLENGE':<20} {'VERSION':<8} {'STATE':<8} {'SCORE':>10}  SUBMITTED")
        for s in subs:
            score = "" if s["score"] is None else f"{s['score']:.4f}"
            print(f"{s['id']:>6}  {s['challenge']:<20} {s['version']:<8} {s['status']:<8} {score:>10}  {s['submitted_at'][:19]}")
        if not subs:
            print("No jobs yet.")
        else:
            print("Run task-log <job> to see the log of a finished job.")
        limits = result.get("daily_limits") or []
        if limits:
            print("\nDaily submission limits (in competition mode per team, reset at 00:00 UTC):")
            for d in limits:
                left = max(d["submissions_per_day"] - d["submitted_today"], 0)
                print(f"  {d['challenge']:<20} {left} of {d['submissions_per_day']} left today")
        return subs

    def log(self, submission_id):
        """Return the scoring log of one of your finished submissions: the output and errors of the scoring run."""
        if not str(submission_id).isdigit():
            raise RaidError(f"Invalid job id {submission_id!r}. task-show lists your jobs.")
        sub = self._json(f"/api/submissions/{submission_id}")
        if sub["status"] in ("pending", "running"):
            raise RaidError(f"Job {sub['id']} is {sub['status']}. Its log is available once it has finished.")
        return sub["log"] or "No log output."

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


def _status(text):
    """A transient line on stderr, only when it is a terminal."""
    if sys.stderr.isatty():
        sys.stderr.write(f"\r\033[K{text}")
        sys.stderr.flush()


class _Indented:
    """Indent everything printed to stdout and stderr inside the with block by two spaces; paths under here become
    relative, and the keys of paths (e.g. /job/ in the container) become their values."""

    class _Stream:
        def __init__(self, stream, paths):
            self.stream, self.paths, self.line_start = stream, paths, True

        def write(self, text):
            for line in text.splitlines(keepends=True):
                if self.line_start and line.strip():
                    self.stream.write("  ")
                for old, new in self.paths.items():
                    line = line.replace(old, new)
                self.stream.write(line)
                self.line_start = line.endswith(("\n", "\r"))
            self.stream.flush()
            return len(text)

        def __getattr__(self, name):
            return getattr(self.stream, name)

    def __init__(self, here=None, paths=None):
        self.paths = {here: ""} if here else {}
        self.paths.update(paths or {})

    def __enter__(self):
        self.saved = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = self._Stream(sys.stdout, self.paths), self._Stream(sys.stderr, self.paths)
        return sys.stdout

    def __exit__(self, *exc):
        for stream in (sys.stdout, sys.stderr):
            stream.flush()
        sys.stdout, sys.stderr = self.saved


def _write(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def _indent(text, here):
    return "\n".join("  " + line.replace(here, "") if line.strip() else line for line in text.splitlines())


def _traceback(error, target):
    """The traceback of an error in score.py, only with the frames of the challenge directory (score.py, solution)."""
    import traceback
    frames = [f for f in traceback.extract_tb(error.__traceback__)
              if target in Path(f.filename).resolve().parents]
    lines = traceback.format_list(frames) + traceback.format_exception_only(error)
    return "".join(lines).rstrip()


def _bold(text):
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() and not os.environ.get("NO_COLOR") else text


class _Progress:
    """A one-line progress bar on stderr, only when it is a terminal. Without a known total it counts bytes."""

    def __init__(self, label, total=None):
        self.label, self.total, self.done, self.shown = label, int(total) if total else None, 0, 0.0
        self.tty = sys.stderr.isatty()

    def __enter__(self):
        self._draw(force=True)
        return self

    def __exit__(self, *exc):
        if self.tty:
            self._draw(force=True)
            sys.stderr.write("\n" if exc[0] is None else "\r\033[K")
            sys.stderr.flush()

    def update(self, n):
        self.done += n
        self._draw()

    def reader(self, f):
        """f with a read() that advances the bar, for uploads."""
        bar = self

        class Reader:
            def read(self, n=-1):
                chunk = f.read(n)
                bar.update(len(chunk))
                return chunk
        return Reader()

    def _draw(self, force=False):
        now = time.monotonic()
        if not self.tty or (not force and now - self.shown < 0.1):
            return
        self.shown = now
        if self.total:
            frac = min(self.done / self.total, 1)
            line = f"{self.label} [{'#' * int(frac * 30):<30}] {frac:4.0%}  {_size(self.done)} of {_size(self.total)}"
        else:
            line = f"{self.label} {_size(self.done)}"
        sys.stderr.write(f"\r\033[K{line}")
        sys.stderr.flush()


def _size(n):
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"


def _data(target):
    """The challenge's data: artifact/data/ in challenges split into artifact/ and method/, else data/."""
    return target / ARTIFACT_DATA if (target / ARTIFACT_DATA).is_dir() else target / "data"


def _functions(script):
    """The names of the top-level functions of score.py, None if it cannot be parsed (running it shows why)."""
    try:
        tree = ast.parse(script.read_bytes())
    except (SyntaxError, ValueError):
        return None
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _call_here(function, script, argument, solution, target, here, quiet=False):
    """Call function of score.py with argument (the mode, or the dataset of an older score()) in this process, as the
    container's runner does. Returns its result dict."""
    paths = [str(script.parent), str(solution)]  # score.py imports the solution, e.g. `from solution import md5`
    sys.path[:0] = paths
    os.environ["RAID_SOLUTION"] = str(solution)  # where score.py finds files that are not imported, e.g. solution.c
    if not quiet:
        os.environ["RAID_VERBOSE"] = "1"  # score.py prints the output of everything it runs
    try:
        with _Indented(here):  # score.py's own output, set off from the result
            spec = importlib.util.spec_from_file_location("score", script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            value = getattr(module, function)(argument)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return {"error": "TypeError", "traceback": f"{function}() must return a number, not {value!r}"}
        return {"value": float(value)}
    except SystemExit as e:  # score.py stops with sys.exit("why"), e.g. when the solution does not compile
        return {"exit": e.code if isinstance(e.code, str) else f"exit code {e.code}"}
    except Exception as e:
        return {"error": type(e).__name__, "traceback": _traceback(e, target)}
    finally:
        del os.environ["RAID_SOLUTION"]
        os.environ.pop("RAID_VERBOSE", None)
        for p in paths:
            sys.path.remove(p)
        for name, mod in list(sys.modules.items()):  # a later check sees the current solution
            if target in Path(getattr(mod, "__file__", None) or "/").resolve().parents:
                del sys.modules[name]


def _check(challenge):
    if not isinstance(challenge, str) or not NAME_RE.match(challenge):
        raise RaidError(f"Invalid challenge name {challenge!r}.")
    return challenge


def _extract(archive, target, part):
    """Extract only regular files and directories below part/, never outside target. source/ maps to target, the
    data archive of a challenge split into artifact/ and method/ holds artifact/data/."""
    root = target.resolve()
    allowed = [part, ARTIFACT_DATA.as_posix()] if part == "data" else [part]
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            name = posixpath.normpath(member.name)
            if not any(name == a or name.startswith(a + "/") for a in allowed) and not (part == "data" and name == "artifact"):
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


def _ignored(target):
    """Patterns of <challenge>/.raidignore (tar exclude patterns, matched against any path suffix)."""
    path = target / ".raidignore"
    lines = path.read_text().splitlines() if path.is_file() else []
    patterns = [line.strip().rstrip("/") for line in lines if line.strip() and not line.startswith("#")]
    return lambda rel: any(fnmatch.fnmatchcase("/".join(rel.parts[i:]), p) for p in patterns for i in range(len(rel.parts)))


def _pack(target, archive, own=OWN):
    """tar.gz of the challenge directory without the top-level entries in own (by default data/, scoring/,
    hidden/, reference/, challenge.yaml and entrypoint.sh, plus artifact/data/) and the .raidignore patterns.
    Regular files only; caches, VCS data and symlinks are left out."""
    nested = {ARTIFACT_DATA} if own is OWN else set()
    ignored = _ignored(target)
    with tarfile.open(archive, "w:gz") as tar:
        for dirpath, dirnames, filenames in os.walk(target):
            rel = Path(dirpath).relative_to(target)
            top = rel == Path(".")
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP and not (top and d in own) and rel / d not in nested
                                 and not ignored(rel / d))
            files = sorted(f for f in filenames if f not in SKIP and not (top and f in own) and not ignored(rel / f))
            for name in dirnames + files:
                path = Path(dirpath) / name
                if path.is_symlink():
                    print(f"Skipping symlink {path}")
                    continue
                if path.is_dir() or path.is_file():
                    tar.add(path, arcname=(rel / name).as_posix(), recursive=False, filter=_normalize)
