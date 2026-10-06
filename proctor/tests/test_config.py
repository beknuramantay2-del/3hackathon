import pytest
import yaml
from proctor.core.config import load_config,ConfigError


def test_clean_config_starts():
    cfg = load_config()
    assert cfg["store"]["screenshots"] == "data/shots"
    assert cfg["guard"]["exit_combo"] == "ctrl+shift+f12"
    assert cfg["calibration"]["duration_sec"] == 20
    assert cfg["test"]["test_url"] == ""

@pytest.mark.parametrize("section,key,value",[("yolo","target_fps",0),("face","max_faces",1),
    ("face","target_fps",0),("face","hands_fps",-1),("yolo","conf_phone",1.2),
    ("yolo","vote_threshold",50),("performance","mode","fast"),("calibration","duration_sec",0)])
def test_bad_runtime_config(tmp_path,section,key,value):
    with open("proctor/config.yaml",encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg[section][key] = value
    path = tmp_path/"bad.yaml"
    path.write_text(yaml.safe_dump(cfg),encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(path))
