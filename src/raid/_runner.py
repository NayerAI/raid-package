"""Runs inside the raid-<challenge> container: python3 -IS _runner.py <function> <dataset> <result.json>

Calls <function> (score, method or artifact) of scoring/score.py in the working directory (the challenge directory)
with the dataset path and writes {"value": ...}, {"exit": ...} or {"error": ..., "traceback": ...} to result.json.
Like the backend, score.py sees only Debian's Python packages.
"""
import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path

DEBIAN_PACKAGES = "/usr/lib/python3/dist-packages"


def main(function, dataset, out):
    job = Path.cwd()
    script = job / "scoring" / "score.py"
    solution = os.environ.get("RAID_SOLUTION", str(job))
    sys.dont_write_bytecode = True  # no __pycache__/ in the challenge directory
    sys.path.append(DEBIAN_PACKAGES)
    sys.path[:0] = [str(script.parent), solution]  # score.py imports the solution, e.g. `from solution import md5`
    try:
        spec = importlib.util.spec_from_file_location("score", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        value = getattr(module, function)(Path(dataset))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            result = {"error": "TypeError", "traceback": f"{function}() must return a number, not {value!r}"}
        else:
            result = {"value": float(value)}
    except SystemExit as e:  # score.py stops with sys.exit("why"), e.g. when the solution does not compile
        result = {"exit": e.code if isinstance(e.code, str) else f"exit code {e.code}"}
    except BaseException as e:
        frames = [f for f in traceback.extract_tb(e.__traceback__) if job in Path(f.filename).resolve().parents]
        result = {"error": type(e).__name__,
                  "traceback": "".join(traceback.format_list(frames) + traceback.format_exception_only(e)).rstrip()}
    sys.stdout.flush()
    sys.stderr.flush()
    with open(out, "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main(*sys.argv[1:4])
