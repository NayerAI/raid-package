# raid

Python client for the RAID competition system.

```bash
pip install git+https://github.com/NayerAI/raid-package   # or: uv add git+https://github.com/NayerAI/raid-package
```

```python
from raid import Raid

raid = Raid(api_key="<your-api-key>")
print(raid.howto())
```

Development: `uv sync` sets up the environment, `uv build` builds the package. Set `RAID_URL` to use another server.
