"""Train a PPO agent on a flightsim task.

Example:
  uv run python scripts/train_rl.py configs/rl/ppo_comfort.yaml
  uv run python scripts/train_rl.py configs/rl/ppo_comfort.yaml --set wall_clock_limit_min=5   # smoke test
Evaluate the result like any controller:
  uv run python scripts/batch_run.py --env-config configs/envs/altitude_heading_hold_comfort.yaml \\
      --policy rl --rl-model data/rl/<run_id>/best --seeds 0:1000
"""

import argparse
import json

import yaml

from flightsim.rl.train import train


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", nargs="?", default="configs/rl/ppo_comfort.yaml")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override a training config value")
    parser.add_argument("--out-dir", default="data/rl")
    parser.add_argument("--force", action="store_true", help="overwrite an existing run directory")
    args = parser.parse_args()
    overrides = {}
    for item in args.set:
        key, _, value = item.partition("=")
        overrides[key] = yaml.safe_load(value)
    run_dir = train(args.config, args.out_dir, overrides, args.force)
    print(f"run directory: {run_dir}")
    print(json.dumps(json.loads((run_dir / "summary.json").read_text()), indent=2))


if __name__ == "__main__":
    main()
