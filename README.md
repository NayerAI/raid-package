# raid

Python client for the RAID competition system.

```bash
pip install mlsec-raid                                    # or: uv tool install mlsec-raid
export RAID_KEY=<your-api-key>                            # or pass --api-key <your-api-key>

task-fetch <challenge>    # downloads the challenge into ./<challenge>/
task-check <challenge>    # scores it locally with scoring/score.py on data/
task-submit <challenge>   # submits ./<challenge>/ without data/, scoring/ and .raidignore patterns
task-show                 # lists your jobs with their state (pending, running, success, failed) and score
task-log <job>            # prints the log of a finished job, including the errors of your solution
```

Every command also works as `task <command>`, e.g. `task submit <challenge>`.

The same is available in Python: `Raid(api_key).fetch(...)`, `.check(...)`, `.submit(...)`, `.push(...)`, `.wait(id)`, `.status()`, `.log(id)`, `.howto()`.

Challenge authors: `task-check <challenge> --hidden --reference` scores `reference/` on `hidden/`. Admins upload a challenge directory with `task-push <directory>` and their own API key (created in the admin console): the directory's `challenge.yaml` sets the name, version, wave, baseline and badge (keys it leaves out keep their current values), and the upload becomes the active version.

Development: `uv sync` sets up the environment, `uv build` builds the package. Set `RAID_URL` to use another server. Release: bump `version` in `pyproject.toml`, then `uv build && uv publish` (PyPI token in `UV_PUBLISH_TOKEN`).
