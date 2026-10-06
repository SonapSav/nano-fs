import gymnasium as gym

from flightsim.envs.altitude_heading import ACTION_NAMES, OBS_NAMES, AltitudeHeadingHoldEnv, controls_to_action
from flightsim.envs.config import EnvConfig, load_env_config


def make_env(cfg: EnvConfig, record: bool = False) -> AltitudeHeadingHoldEnv:
    """The task a config describes: approach and landing if it has an `approach` section,
    takeoff if it has a `takeoff` section, otherwise altitude and heading hold."""
    if cfg.approach is not None:
        from flightsim.envs.approach import ApproachLandingEnv

        return ApproachLandingEnv(cfg, record=record)
    if cfg.takeoff is not None:
        from flightsim.envs.takeoff import TakeoffEnv

        return TakeoffEnv(cfg, record=record)
    return AltitudeHeadingHoldEnv(cfg, record=record)

ENV_ID = "flightsim/AltitudeHeadingHold-v0"
DEFAULT_ENV_CONFIG = "configs/envs/altitude_heading_hold.yaml"


def _make(config_path: str = DEFAULT_ENV_CONFIG, record: bool = False) -> AltitudeHeadingHoldEnv:
    return make_env(load_env_config(config_path), record=record)


if ENV_ID not in gym.registry:
    gym.register(id=ENV_ID, entry_point=_make)

__all__ = [
    "ACTION_NAMES",
    "DEFAULT_ENV_CONFIG",
    "ENV_ID",
    "OBS_NAMES",
    "AltitudeHeadingHoldEnv",
    "EnvConfig",
    "controls_to_action",
    "load_env_config",
    "make_env",
]
