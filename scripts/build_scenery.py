"""Build a real-world scenery region from open data into data/scenery/<name>/ (downloads
what is missing, then writes the tiles and manifest.json). Needs the scenery group:

    uv run --group scenery python scripts/build_scenery.py configs/scenery/abu_dhabi.yaml [--pin]

--pin writes the downloaded files' sha256 into the region file, so a later build stops if
a source changed.
"""

import argparse
import re
import time
from pathlib import Path

from flightsim.world.scenery import manifest_hash
from flightsim.world.scenery_build import SCENERY_DIR, build, build_hires, download, load_spec, write_manifest


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
    parser.add_argument("--hires-only", action="store_true", help="rebuild only the high-resolution imagery of a built region")
    args = parser.parse_args()

    spec = load_spec(args.region)
    t0 = time.perf_counter()
    if args.download_only:
        sources = download(spec)
    elif args.hires_only:
        sources = download(spec) + [build_hires(spec, SCENERY_DIR / spec.name)]
        manifest = write_manifest(spec, sources, SCENERY_DIR / spec.name)
        print(f"rebuilt {spec.name}'s high-resolution imagery in {time.perf_counter() - t0:.0f} s, scenery hash {manifest_hash(manifest)[:16]}")
    else:
        manifest = build(spec)
        sources = manifest["sources"]
        print(f"built {spec.name}: {len(manifest['files'])} files, scenery hash {manifest_hash(manifest)[:16]} "
              f"in {time.perf_counter() - t0:.0f} s")  # fmt: skip
    if args.pin:
        pin(Path(args.region), sources)
        print(f"pinned {len(sources)} source files in {args.region}")


if __name__ == "__main__":
    main()
