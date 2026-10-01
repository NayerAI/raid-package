# raid

Python client for the RAID competition system.

```bash
pip install git+https://github.com/NayerAI/raid-package   # or: uv tool install git+https://github.com/NayerAI/raid-package
export RAID_KEY=<your-api-key>                            # or pass --api-key <your-api-key>

task-fetch <challenge>    # downloads the challenge into ./<challenge>/
task-check <challenge>    # scores it locally with scoring/score.py on data/
task-submit <challenge>   # submits ./<challenge>/ without data/, scoring/ and .raidignore patterns, prints score and log
```

The same is available in Python: `Raid(api_key).fetch(...)`, `.check(...)`, `.submit(...)`, `.push(...)`, `.wait(id)`, `.status()`, `.log(id)`, `.howto()`.

Challenge authors: `task-check <challenge> --hidden --reference` scores `reference/` on `hidden/`. Admins upload a challenge directory with `task-push <directory>` and their own API key (created in the admin console): the directory's `challenge.yaml` sets the name and version (and the wave, baseline and badge of a new challenge), and the upload becomes the active version.

Development: `uv sync` sets up the environment, `uv build` builds the package. Set `RAID_URL` to use another server.
