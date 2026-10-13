"""Build a real-world scenery region from open data into data/scenery/<name>/ (downloads
what is missing, then writes the tiles and manifest.json). Needs the scenery group:

    uv run --group scenery python scripts/build_scenery.py configs/scenery/abu_dhabi.yaml [--pin]

A build reruns only the steps whose inputs (code, region file, sources) changed since the
last one, and a build that stopped part way goes on from the step that stopped; --force
reruns every step. --pin writes the downloaded files' sha256 into the region file, so a
later build stops if a source changed.
"""

import argparse
import re
import sys
import time
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from flightsim.world.scenery import manifest_hash
from flightsim.world.scenery_build import build, check_platform, download, load_spec


def pin(region_file: Path, sources: list[dict]) -> None:
    text = region_file.read_text()
    block = "pinned:\n" + "".join(f"  {f['name']}: {f['sha256']}\n" for f in sorted(sources, key=lambda f: f["name"]))
    text = re.sub(r"^pinned:.*?(?=^\S|\Z)", block, text, flags=re.S | re.M)
    region_file.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("region", help="region file, e.g. configs/scenery/abu_dhabi.yaml")
    parser.add_argument("--pin", action="store_true", help="record the sources' sha256 in the region file")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--workers", type=int, default=None, help="worker processes for the parallel steps (default: one per CPU core, as many as the free memory holds)")
    parser.add_argument("--force", action="store_true", help="rebuild every step (by default only the steps whose inputs changed since the last build)")
    args = parser.parse_args()
    if not args.download_only:
        check_platform()

    spec = load_spec(args.region)
    t0 = time.perf_counter()
    if args.download_only:
        sources = download(spec)
    else:
        manifest = build(spec, workers=args.workers, force=args.force)
        sources = manifest["sources"]
        print(f"built {spec.name}: {len(manifest['files'])} files, scenery hash {manifest_hash(manifest)[:16]} "
              f"in {time.perf_counter() - t0:.0f} s")  # fmt: skip
    if args.pin:
        pin(Path(args.region), sources)
        print(f"pinned {len(sources)} source files in {args.region}")


if __name__ == "__main__":
    try:
        main()
    except BrokenProcessPool:
        # A worker died without a Python error: on Linux the out-of-memory killer.
        sys.exit("\nscenery build: a worker process was killed, most likely for want of memory "
                 "(check with: journalctl -k | grep -i 'out of memory').\nRun the build again with fewer "
                 "workers, e.g. --workers 4: the steps that had finished are kept.")
