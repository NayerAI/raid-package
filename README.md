# raid

Python client for the RAID competition system.

```bash
pip install mlsec-raid                                    # or: uv tool install mlsec-raid
export RAID_KEY=<your-api-key>                            # or pass --api-key <your-api-key>

task-fetch <challenge>            # downloads the challenge into ./<challenge>/
task-check <challenge>            # scores it locally with scoring/score.py on its data, in a container
task-check method <challenge>     # only the method: runs method/run like our backend's sanity check
task-check artifact <challenge>   # only the artifact: scores artifact/result/
task-submit <challenge>           # submits ./<challenge>/ without its data, scoring/ and .raidignore patterns
task-show                         # lists your jobs with their state (pending, running, success, failed) and
                                  # score, and the submissions left today on challenges with a daily limit
task-log <job>                    # prints the log of a finished job, including the errors of your solution
```

Every command also works as `task <command>`, e.g. `task submit <challenge>` or `task check method <challenge>`.

Challenges like dawn separate the method from the artifact:

```
<challenge>/
  artifact/data/     the data, never submitted
  artifact/result/   the artifact you submit, e.g. dawn's re-implementations
  method/run         your method, which the backend runs once as a sanity check (dawn: on the simple target)
  scoring/score.py   method() (verification score), artifact() (artifact score) and score(), the backend's scoring
  setup.sh           your installs
```

`task-check` runs `scoring/score.py` in the container `raid-<challenge>`, built like the backend's sandbox: with Docker an image on Debian trixie (slim) with python3 and the challenge's Debian packages (`scoring/packages.txt`), in which the challenge's `setup.sh` runs as root during the build; with only udocker (`pip install udocker`) a udocker container of `debian:trixie` set up the same way. It is built again only when `setup.sh` or the packages change (their hash is the image's `raid.setup` label, or `/etc/raid-setup.sha256` in the udocker container), or with `--rebuild`. Like on the backend, `score.py` sees only Debian's Python packages; unlike there, the method may use the internet. `task-check method` calls `score.py`'s `method()` and prints the verification score, `task-check artifact` its `artifact()` and prints the artifact score and `task-check` its `score()`, which checks the method first and then the artifact. Challenges without that split (e.g. self-assessment) have only `task-check <challenge>`. `task-check` shows everything the container build and the scoring run print, including the output of `method/run`, `make` and each test on your data (never on `hidden/`, and never on the backend); `--quiet` hides it and leaves the summary. `--no-container` checks on this machine instead.

The same is available in Python: `Raid(api_key).fetch(...)`, `.check(...)`, `.submit(...)`, `.push(...)`, `.wait(id)`, `.status()`, `.log(id)`, `.howto()`.

Challenge authors: `task-check <challenge> --hidden --reference` scores `reference/` on `hidden/` (the packages come from `challenge.yaml`). Admins upload a challenge directory with `task-push <directory>` and their own API key (created in the admin console): the directory's `challenge.yaml` sets the name, version, wave, baseline, badge and `submissions_per_day` (keys it leaves out keep their current values), and the upload becomes the active version.

Development: `uv sync` sets up the environment, `uv build` builds the package. Set `RAID_URL` to use another server. Release: bump `version` in `pyproject.toml`, then `uv build && uv publish` (PyPI token in `UV_PUBLISH_TOKEN`).
