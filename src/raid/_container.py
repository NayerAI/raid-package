"""The local container raid-<challenge> in which task-check runs scoring/score.py, built like the backend's sandbox.

With Docker it is an image on the backend's base (Debian trixie slim with python3), the challenge's Debian packages
and the challenge's setup.sh, run as root during the build. With only udocker it is a udocker container of
debian:trixie in which the same is installed. Either is built again only when setup.sh or the packages change: the
image's label raid.setup (Docker) or the container's /etc/raid-setup.sha256 (udocker) holds their hash.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DOCKER_BASE = "debian:trixie-slim"  # the base of the backend's sandbox (raid-scoring-service runner/)
UDOCKER_BASE = "debian:trixie"
RECIPE = "1"  # part of the hash: a new recipe rebuilds every container
# Trusted directories first, as on the backend: setup.sh's commands in /usr/local come last.
PATH = "/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin"
PASSED_ENV = re.compile(r"^(RAID_LLM_\w+|https?_proxy|no_proxy|HTTPS?_PROXY|NO_PROXY)$")  # handed into the container
RUNNER = Path(__file__).with_name("_runner.py")


class ContainerError(Exception):
    pass


def packages(target):
    """The challenge's Debian packages: scoring/packages.txt (from the server) or, for challenge authors,
    requirements in challenge.yaml."""
    listed = target / "scoring" / "packages.txt"
    if listed.is_file():
        return listed.read_text().split()
    meta = target / "challenge.yaml"
    return _requirements(meta.read_text()) if meta.is_file() else []


def _requirements(text):
    """The requirements list of challenge.yaml, as a block (- gcc) or a flow sequence ([gcc, make])."""
    lines = [line.split("#", 1)[0].rstrip() for line in text.splitlines()]
    for i, line in enumerate(lines):
        m = re.match(r"^requirements:\s*(.*)$", line)
        if not m:
            continue
        if m.group(1).startswith("["):
            flow = " ".join([m.group(1)] + lines[i + 1:])
            return [p.strip().strip("'\"") for p in flow[1:flow.index("]")].split(",") if p.strip()]
        found = []
        for item in lines[i + 1:]:
            if item and not item[0].isspace():
                break
            if item.strip().startswith("-"):
                found.append(item.strip()[1:].strip().strip("'\""))
        return found
    return []


def engine():
    """docker when it is installed and its daemon answers, else udocker when it is installed, else None."""
    if shutil.which("docker"):
        try:
            if subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0:
                return "docker"
        except subprocess.TimeoutExpired:
            pass
    return "udocker" if shutil.which("udocker") else None


class Container:
    def __init__(self, challenge, target, kind, output=None):
        """output: called with every line the build and score.py print (None: quiet, only errors)."""
        self.name, self.target, self.kind, self.output = f"raid-{challenge}", target, kind, output
        self.packages = packages(target)
        setup = target / "setup.sh"
        self.setup = setup.read_bytes() if setup.is_file() else b""
        base = DOCKER_BASE if kind == "docker" else UDOCKER_BASE
        self.hash = hashlib.sha256(f"{RECIPE}\0{base}\0{' '.join(self.packages)}\0".encode() + self.setup).hexdigest()

    # ---------- building ----------

    def ensure(self, rebuild=False, status=print):
        """Build the container unless it exists with the current setup.sh and packages."""
        if not rebuild and self._built_hash() == self.hash:
            return
        status(f"Building {self.name} with {'setup.sh' if self.setup.strip() else 'no setup.sh'}"
               f"{' and ' + ' '.join(self.packages) if self.packages else ''} (once, and again when they change) ...")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "setup.sh").write_bytes(self.setup)
            if self.kind == "docker":
                self._build_docker(Path(tmp))
            else:
                self._build_udocker(Path(tmp))

    def _install(self):
        return ("apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
                f"python3 ca-certificates {' '.join(self.packages)}")

    def _built_hash(self):
        if self.kind == "docker":
            found = subprocess.run(["docker", "image", "inspect", "-f", '{{index .Config.Labels "raid.setup"}}',
                                    self.name], capture_output=True, text=True)
            return found.stdout.strip() if found.returncode == 0 else None
        found = subprocess.run(self._udocker("inspect", "-p", self.name), capture_output=True, text=True)
        if found.returncode:
            return None
        stamp = Path(found.stdout.strip().splitlines()[-1]) / "etc" / "raid-setup.sha256"
        return stamp.read_text().strip() if stamp.is_file() else ""

    def _build_docker(self, context):
        dockerfile = (f"FROM {DOCKER_BASE} AS trusted\n"
                      f"RUN {self._install()} && rm -rf /var/lib/apt/lists/*\n")
        if self.setup.strip():  # as on the backend: afterwards the trusted image is copied back over setup.sh's changes
            dockerfile += ("FROM trusted\n"
                           "COPY setup.sh /tmp/raid-setup/setup.sh\n"
                           "RUN cd /tmp/raid-setup && DEBIAN_FRONTEND=noninteractive bash setup.sh\n"
                           "COPY --from=trusted / /\n"
                           "RUN rm -rf /tmp/raid-setup\n")
        (context / "Dockerfile").write_text(dockerfile)
        self._step(["docker", "build", "-t", self.name, "--label", f"raid.setup={self.hash}", str(context)], setup=True)

    def _build_udocker(self, context):
        if self._built_hash() is not None:
            self._step(self._udocker("rm", self.name))
        if subprocess.run(self._udocker("inspect", UDOCKER_BASE), capture_output=True).returncode:
            self._step(self._udocker("pull", UDOCKER_BASE))
        self._step(self._udocker("create", f"--name={self.name}", UDOCKER_BASE))
        script = (f"set -e; {self._install()}; "
                  "cd /raid-setup && DEBIAN_FRONTEND=noninteractive bash setup.sh; "
                  f"echo {self.hash} > /etc/raid-setup.sha256")
        try:
            self._step(self._udocker("run", "--user=root", f"-v={context}:/raid-setup", self.name,
                                     "/bin/bash", "-c", script), setup=True)
        except ContainerError:
            subprocess.run(self._udocker("rm", self.name), capture_output=True)  # never reused half-built
            raise

    def _step(self, cmd, setup=False):
        """Run a build command; on failure raise ContainerError with the end of its output. setup: the command
        installs the packages and runs setup.sh."""
        lines = []
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL) as proc:
            for line in iter(proc.stdout.readline, b""):
                lines.append(line.decode(errors="replace"))
                if self.output:
                    self.output(lines[-1])
        if proc.returncode:
            tail = "".join(lines[-40:]).rstrip() if not self.output else "(the output is above)"
            step = " ".join(c for c in cmd[:4] if c not in ("-q", "--allow-root"))
            raise ContainerError(f"Building {self.name} failed in `{step} ...`:\n{tail}"
                                 + ("\nCheck setup.sh: it runs as root in the image build, with internet access." if setup else ""))

    def _udocker(self, *args):
        return ["udocker", "-q", *(["--allow-root"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []), *args]

    # ---------- running ----------

    def call(self, function, dataset, solution, write=None):
        """Run function (score, method or artifact) of scoring/score.py on dataset in the container, with the
        challenge directory at /job. Output goes to write line by line; score.py prints the output of everything it
        runs unless the container is quiet. Returns the runner's result dict."""
        write = write or sys.stdout.write
        inside = lambda p: "/job/" + p.relative_to(self.target).as_posix() if p != self.target else "/job"  # noqa: E731
        env = {"HOME": "/tmp", "PATH": PATH, "RAID_SOLUTION": inside(solution), "PYTHONDONTWRITEBYTECODE": "1",
               **({"RAID_VERBOSE": "1"} if self.output else {}),
               **{k: v for k, v in os.environ.items() if PASSED_ENV.match(k)}}
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(RUNNER, Path(tmp, "runner.py"))
            command = ["python3", "-IS", "/raid-check/runner.py", function, inside(dataset), "/raid-check/result.json"]
            if self.kind == "docker":
                user = ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
                cmd = ["docker", "run", "--rm", "--init", *user, "-v", f"{self.target}:/job", "-v", f"{tmp}:/raid-check",
                       "-w", "/job", *[f"--env={k}={v}" for k, v in env.items()], self.name, *command]
            else:
                cmd = self._udocker("run", f"-v={self.target}:/job", f"-v={tmp}:/raid-check", "--workdir=/job",
                                    *[f"--env={k}={v}" for k, v in env.items()], self.name, *command)
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL) as proc:
                for line in iter(proc.stdout.readline, b""):
                    write(line.decode(errors="replace"))
            result = Path(tmp, "result.json")
            if not result.is_file():
                return {"exit": f"the container {self.name} exited with code {proc.returncode} before score.py finished"}
            return json.loads(result.read_text())
