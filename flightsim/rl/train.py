"""PPO training on a flightsim task, with provenance and periodic evaluation.

A run directory <out_dir>/<run_id>/ gets:
  config.json        resolved training config (including the full env config) and versions
  progress.csv       SB3 training log
  evaluation.csv     periodic deterministic evaluation on fixed tuning seeds
  best/              model.zip + obs_rms.npz (+ residual.json for a residual agent) with
                     the best evaluation return so far
  final/             the same at the end of training
  checkpoints/       periodic snapshots
  summary.json       timesteps, wall time, stop reason, best evaluation

The run id is a hash of the resolved config and code, so the same training setup always
maps to the same directory. Training stops at total_timesteps or the wall-clock limit,
whichever comes first; the summary records which. Reproducing a wall-clock-limited run
exactly needs total_timesteps set to the recorded timesteps.
"""

import csv
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

from flightsim.config import canonical_json, load_raw
from flightsim.envs import AltitudeHeadingHoldEnv
from flightsim.envs.config import env_config_from_raw
from flightsim.envs.evaluate import run_episode
from flightsim.provenance import code_version
from flightsim.rl.policy import OBS_RMS_FILE, RESIDUAL_FILE, RLPolicy, save_obs_rms


def load_training_config(path: str | Path, overrides: dict | None = None) -> dict:
    """Training config with its env config (and a residual agent's LQR config) resolved
    inline, so the hash covers them. Overrides starting with "env." apply to the env config."""
    path = Path(path)
    overrides = overrides or {}
    env_overrides = {k[len("env."):]: v for k, v in overrides.items() if k.startswith("env.")}
    raw = load_raw(path, {k: v for k, v in overrides.items() if not k.startswith("env.")})
    raw["env"] = load_raw(path.parent / raw.pop("env_config"), env_overrides)
    if "residual" in raw:
        raw["residual"]["lqr"] = load_raw(path.parent / raw["residual"].pop("lqr_config"))
    return raw


def _make_env(env_raw: dict, reward_scale: float, residual: dict | None = None):
    def factory():
        from gymnasium.wrappers import TransformReward
        from stable_baselines3.common.monitor import Monitor

        cfg = env_config_from_raw(env_raw)
        env = AltitudeHeadingHoldEnv(cfg)
        if residual is not None:
            from flightsim.rl.residual import ResidualEnv, make_lqr_policy

            env = ResidualEnv(env, make_lqr_policy(cfg, residual["lqr"]), residual["scale"])
        # Monitor before scaling, so logged episode returns are the task's true (unscaled) returns.
        env = Monitor(env)
        return TransformReward(env, lambda r: r * reward_scale) if reward_scale != 1.0 else env

    return factory


def _save(model, vec_normalize, directory: Path, residual: dict | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    model.save(directory / "model.zip")
    rms = vec_normalize.obs_rms
    save_obs_rms(directory / OBS_RMS_FILE, rms.mean, rms.var, vec_normalize.clip_obs, vec_normalize.epsilon)
    if residual is not None:
        (directory / RESIDUAL_FILE).write_text(json.dumps(residual, indent=2, sort_keys=True) + "\n")


def evaluate(policy, env_raw: dict, seeds: range) -> dict:
    env = AltitudeHeadingHoldEnv(env_config_from_raw(env_raw))
    ms = [run_episode(env, policy, s) for s in seeds]
    returns = np.array([m.episode_return for m in ms])
    return {
        "mean_return": float(returns.mean()),
        "worst_return": float(returns.min()),
        "terminated": sum(1 for m in ms if m.termination_reason),
        "never_settled": sum(1 for m in ms if math.isinf(m.alt_settle_s) or math.isinf(m.heading_settle_s)),
        "alt_rms_m": float(np.mean([m.alt_rms_m for m in ms])),
        "heading_rms_deg": float(np.mean([m.heading_rms_deg for m in ms])),
        "comfort_cost": float(np.mean([m.comfort_cost for m in ms])),
    }


def train(config_path: str | Path, out_dir: str | Path = "data/rl", overrides: dict | None = None, force: bool = False) -> Path:
    import stable_baselines3
    import torch
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import BaseCallback
    from stable_baselines3.common.logger import configure
    from stable_baselines3.common.vec_env import SubprocVecEnv, VecNormalize

    cfg = load_training_config(config_path, overrides)
    versions = {"code": code_version(), "torch": torch.__version__, "stable_baselines3": stable_baselines3.__version__}
    identity = {"config": cfg, "code_source_sha256": versions["code"]["source_sha256"],
                "torch": versions["torch"], "stable_baselines3": versions["stable_baselines3"]}  # fmt: skip
    run_id = hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:12]
    run_dir = Path(out_dir) / run_id
    if run_dir.exists() and not force:
        raise FileExistsError(f"{run_dir} exists; pass force=True to overwrite")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps({"run_id": run_id, **identity, "versions": versions}, indent=2, sort_keys=True) + "\n")

    torch.set_num_threads(1)  # environments run in their own processes; keep the learner off their cores
    ppo, norm, ev = cfg["ppo"], cfg["normalize"], cfg["evaluation"]
    # Fixed reward scaling (instead of, or as well as, normalization) keeps the ratio between
    # step costs and the termination charge exactly as the task defines it.
    reward_scale = float(norm.get("reward_scale", 1.0))
    residual = cfg.get("residual")
    base_policy = None
    if residual is not None:
        from flightsim.rl.residual import make_lqr_policy

        # Design (or load) the LQR schedule here once, so the environment workers find it cached.
        base_policy = make_lqr_policy(env_config_from_raw(cfg["env"]), residual["lqr"])
    venv = SubprocVecEnv([_make_env(cfg["env"], reward_scale, residual) for _ in range(cfg["n_envs"])], start_method="forkserver")
    venv = VecNormalize(
        venv, norm_obs=norm["observations"], norm_reward=norm["rewards"],
        clip_obs=norm["clip_observations"], clip_reward=norm["clip_rewards"], gamma=ppo["gamma"],
    )  # fmt: skip
    model = PPO(
        "MlpPolicy", venv,
        learning_rate=ppo["learning_rate"], n_steps=ppo["n_steps"], batch_size=ppo["batch_size"],
        n_epochs=ppo["n_epochs"], gamma=ppo["gamma"], gae_lambda=ppo["gae_lambda"], clip_range=ppo["clip_range"],
        ent_coef=ppo["ent_coef"], max_grad_norm=ppo["max_grad_norm"],
        policy_kwargs={"net_arch": {"pi": ppo["net_arch"], "vf": ppo["net_arch"]}, "log_std_init": ppo["log_std_init"]},
        seed=cfg["seed"], device="cpu", verbose=0,
    )  # fmt: skip
    model.set_logger(configure(str(run_dir), ["csv"]))

    eval_seeds = range(*ev["seeds"])
    deadline = time.monotonic() + 60.0 * cfg["wall_clock_limit_min"]
    state = {"best": -math.inf, "next_eval": 0, "next_checkpoint": cfg["checkpoint_every_timesteps"], "stop": "total_timesteps"}
    eval_file = run_dir / "evaluation.csv"
    t_start = time.monotonic()

    def run_evaluation(timesteps: int) -> None:
        rms = venv.obs_rms
        policy = RLPolicy(model, rms.mean.copy(), rms.var.copy(), venv.clip_obs, venv.epsilon)
        if residual is not None:
            from flightsim.rl.residual import ResidualPolicy

            policy = ResidualPolicy(policy, base_policy, residual["scale"])
        result = {"timesteps": timesteps, "wall_s": round(time.monotonic() - t_start, 1), **evaluate(policy, cfg["env"], eval_seeds)}
        new = not eval_file.exists()
        with eval_file.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(result))
            if new:
                w.writeheader()
            w.writerow(result)
        if result["mean_return"] > state["best"]:
            state["best"] = result["mean_return"]
            state["best_timesteps"] = timesteps
            _save(model, venv, run_dir / "best", residual)
        print(f"[{result['wall_s']:7.0f} s] {timesteps:>10,} steps: eval return {result['mean_return']:9.1f} "
              f"(worst {result['worst_return']:9.1f}), ended {result['terminated']}, unsettled {result['never_settled']}", flush=True)  # fmt: skip

    class Control(BaseCallback):
        def _on_step(self) -> bool:
            if self.num_timesteps >= state["next_eval"]:
                run_evaluation(self.num_timesteps)
                state["next_eval"] = self.num_timesteps + ev["every_timesteps"]
            if self.num_timesteps >= state["next_checkpoint"]:
                _save(model, venv, run_dir / "checkpoints" / f"{self.num_timesteps}", residual)
                state["next_checkpoint"] += cfg["checkpoint_every_timesteps"]
            if time.monotonic() > deadline:
                state["stop"] = "wall_clock_limit"
                return False
            return True

    try:
        model.learn(total_timesteps=cfg["total_timesteps"], callback=Control())
        run_evaluation(model.num_timesteps)
        _save(model, venv, run_dir / "final", residual)
    finally:
        venv.close()
    summary = {
        "run_id": run_id,
        "timesteps": int(model.num_timesteps),
        "wall_s": round(time.monotonic() - t_start, 1),
        "stop_reason": state["stop"],
        "best_eval_mean_return": state["best"],
        "best_eval_timesteps": state.get("best_timesteps"),
        "eval_seeds": [eval_seeds.start, eval_seeds.stop],
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return run_dir
