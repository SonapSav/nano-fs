import gymnasium as gym

from flightsim.envs.altitude_heading import ACTION_NAMES, OBS_NAMES, AltitudeHeadingHoldEnv, controls_to_action
from flightsim.envs.config import EnvConfig, load_env_config

ENV_ID = "flightsim/AltitudeHeadingHold-v0"
DEFAULT_ENV_CONFIG = "configs/envs/altitude_heading_hold.yaml"


def _make(config_path: str = DEFAULT_ENV_CONFIG, record: bool = False) -> AltitudeHeadingHoldEnv:
    return AltitudeHeadingHoldEnv(load_env_config(config_path), record=record)


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
]
