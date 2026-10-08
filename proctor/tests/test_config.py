import pytest
import yaml
from proctor.core.config import load_config, ConfigError


def test_clean_config_starts():
    cfg = load_config()
    assert cfg["store"]["screenshots"] == "data/shots"
    assert cfg["guard"]["exit_combo"] == "ctrl+shift+f12"
    assert cfg["calibration"]["duration_sec"] == 30
    assert "test" not in cfg and "_test_url" not in cfg


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("yolo", "target_fps", 0),
        ("yolo", "detail_imgsz", 321),
        ("yolo", "detail_imgsz", 256),
        ("yolo", "detail_fps", 0),
        ("yolo", "detail_model", 42),
        ("face", "max_faces", 1),
        ("face", "target_fps", 0),
        ("face", "hands_fps", -1),
        ("yolo", "conf_phone", 1.2),
        ("yolo", "vote_threshold", 50),
        ("performance", "mode", "fast"),
        ("calibration", "duration_sec", 0),
    ],
)
def test_bad_runtime_config(tmp_path, section, key, value):
    with open("proctor/config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg[section][key] = value
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(path))


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("policy", "yellow_sec", 5),
        ("policy", "red_sec", 2),
        ("policy", "immediate_phone_conf", 0),
        ("evidence", "fps", 100),
        ("evidence", "pre_sec", 60),
        ("evidence", "segment_sec", 600),
        ("calibration", "duration_sec", 20),
    ],
)
def test_invalid_review_policy_or_unbounded_recording(tmp_path, section, key, value):
    cfg = yaml.safe_load(open("proctor/config.yaml", encoding="utf8"))
    cfg[section][key] = value
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ConfigError):
        load_config(str(path))
