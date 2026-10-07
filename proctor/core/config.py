"""Загрузка и проверка config.yaml. Ошибка сообщает имя поля, дефолтов нет."""
import os
import yaml

NAME = "config.yaml"


class ConfigError(Exception):
    pass


def _need(cfg, path, types):
    node = cfg
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"{NAME}: отсутствует поле '{'.'.join(path)}'")
        node = node[key]
    if not isinstance(node, types):
        want = getattr(types, "__name__", str(types))
        raise ConfigError(f"{NAME}: поле '{'.'.join(path)}' должно быть {want}")
    return node


def _need_list(cfg, path, item_type):
    items = _need(cfg, path, list)
    for i, v in enumerate(items):
        if not isinstance(v, item_type):
            want = getattr(item_type, "__name__", str(item_type))
            raise ConfigError(f"{NAME}: поле '{'.'.join(path)}[{i}]' должно быть {want}")
    return items


_RULES = ("phone_detected", "phone_raised", "phone_aimed", "gaze_down",
          "gaze_side", "no_face", "multi_face", "camera_covered")
_THRESHOLDS = {
    "gaze_down": (("pitch_thresh", (int, float)),),
    "gaze_side": (("yaw_thresh", (int, float)), ("iris_thresh", (int, float))),
    "camera_covered": (("brightness_thresh", (int, float)), ("variance_thresh", (int, float))),
}


def _check_rule(cfg, name):
    base = ("rules", name)
    enabled = _need(cfg, base + ("enabled",), bool)
    for key in ("hold", "cooldown"):
        v = _need(cfg, base + (key,), (int, float))
        if v < 0:
            raise ConfigError(f"{NAME}: поле '{'.'.join(base + (key,))}' должно быть >= 0")
    for key, types in _THRESHOLDS.get(name, ()):
        _need(cfg, base + (key,), types)
    return enabled


def load_config(path=None, profile=None, require_test_url=True):
    if path is None:
        here = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
        alt = "proctor/config.yaml"
        path = alt if os.path.exists(alt) else here
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"{NAME}: файл не найден: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"{NAME}: ошибка разбора YAML: {e}")
    if not isinstance(cfg, dict):
        raise ConfigError(f"{NAME}: корневой элемент должен быть словарем")

    prof = profile or cfg.get("profile", "dev")
    if prof not in ("dev", "exam"):
        raise ConfigError(f"{NAME}: поле 'profile' должно быть dev или exam")

    test = _need(cfg, ("test",), dict)
    url = _need(cfg, ("test", "test_url"), str)
    domains = _need_list(cfg, ("test", "allowed_domains"), str)
    if prof == "exam" and require_test_url and not url.strip():
        raise ConfigError(f"{NAME}: в профиле exam поле 'test.test_url' не должно быть пустым")
    if url.strip() and not os.path.isfile(url):
        from urllib.parse import urlparse as _up
        parts = _up(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ConfigError(f"{NAME}: поле 'test.test_url' должно быть путем к файлу или http(s) URL")

    cam = _need(cfg, ("camera",), dict)
    _need(cfg, ("camera", "index"), int)
    for key in ("width", "height", "fps"):
        v = _need(cfg, ("camera", key), (int, float))
        if v <= 0:
            raise ConfigError(f"{NAME}: поле 'camera.{key}' должно быть > 0")
    _need(cfg, ("camera", "mirror_preview"), bool)

    _need(cfg, ("yolo", "model"), str)
    for key in ("imgsz", "vote_window", "vote_threshold", "phone_class", "person_class"):
        _need(cfg, ("yolo", key), int)
    for key in ("conf_phone", "conf_person", "target_fps"):
        _need(cfg, ("yolo", key), (int, float))

    _need(cfg, ("face", "max_faces"), int)
    _need(cfg, ("calibration", "duration_sec"), (int, float))

    for name in _RULES:
        _check_rule(cfg, name)
    _need(cfg, ("rules", "gap_tolerance"), (int, float))

    mode = _need(cfg, ("guard", "process_mode"), str)
    if mode not in ("close", "log"):
        raise ConfigError(f"{NAME}: поле 'guard.process_mode' должно быть close или log")
    _need_list(cfg, ("guard", "forbidden_processes"), str)
    _need_list(cfg, ("guard", "hotkeys"), str)
    _need(cfg, ("guard", "exit_combo"), str)
    _need(cfg, ("guard", "examiner", "salt_hex"), str)
    _need(cfg, ("guard", "examiner", "hash_hex"), str)
    _need(cfg, ("guard", "examiner", "iterations"), int)
    if prof == "exam" and not (cfg["guard"]["exit_combo"].strip()
                               and cfg["guard"]["examiner"]["salt_hex"]
                               and cfg["guard"]["examiner"]["hash_hex"]):
        raise ConfigError(f"{NAME}: в профиле exam задайте guard.exit_combo и guard.examiner.salt_hex/hash_hex")

    _need(cfg, ("store", "db"), str)
    _need(cfg, ("store", "screenshots"), str)
    _need(cfg, ("store", "save_screenshots"), bool)

    host = _need(cfg, ("examiner", "host"), str)
    if host not in ("127.0.0.1", "localhost"):
        raise ConfigError(f"{NAME}: поле 'examiner.host' разрешено только 127.0.0.1 или localhost")
    port = _need(cfg, ("examiner", "port"), int)
    if not 1024 <= port <= 65535:
        raise ConfigError(f"{NAME}: поле 'examiner.port' должно быть 1024-65535")

    _need(cfg, ("report", "out"), str)

    _need(cfg, ("hash_chain", "enabled"), bool)
    _need(cfg, ("hash_chain", "salt_hex"), str)

    _need(cfg, ("preflight", "virtual_check_enabled"), bool)
    _need_list(cfg, ("preflight", "virtual_keywords"), str)

    weights = _need(cfg, ("trust_weights",), dict)
    for k, v in weights.items():
        if not isinstance(v, (int, float)) or v < 0:
            raise ConfigError(f"{NAME}: поле 'trust_weights.{k}' должно быть числом >= 0")

    # Runtime budgets must be valid: no division by zero or impossible voting windows.
    for section, key in (("yolo", "imgsz"), ("yolo", "target_fps"), ("yolo", "vote_window"),
                         ("face", "max_faces"), ("calibration", "duration_sec")):
        if cfg[section][key] <= 0:
            raise ConfigError(f"{NAME}: {section}.{key} должно быть > 0")
    if cfg["face"]["max_faces"] < 2:
        raise ConfigError(f"{NAME}: face.max_faces >= 2 для Кейс №3")
    if cfg["yolo"]["vote_threshold"] < 1 or cfg["yolo"]["vote_threshold"] > cfg["yolo"]["vote_window"]:
        raise ConfigError(f"{NAME}: неверные параметры vote_threshold/vote_window")
    for key in ("conf_phone", "conf_person"):
        if not 0 < cfg["yolo"][key] <= 1:
            raise ConfigError(f"{NAME}: yolo.{key} должно быть в (0, 1]")
    perf = cfg.get("performance", {})
    if perf.get("mode", "auto") not in ("auto", "weak", "balanced"):
        raise ConfigError(f"{NAME}: performance.mode должно быть auto/weak/balanced")
    for section,key,default in (("face","target_fps",15),("face","hands_fps",5),
                                ("face","max_width",640),("performance","threads",2)):
        value = cfg.get(section,{}).get(key,default)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or value <= 0:
            raise ConfigError(f"{NAME}: {section}.{key} должно быть > 0")
    if cfg["calibration"]["duration_sec"] != 30:
        raise ConfigError(f"{NAME}: calibration.duration_sec = 30: отдельно 15 с головы + 15 с глаз")
    pc=cfg.get('policy',{})
    yellow,red=pc.get('yellow_sec',3.),pc.get('red_sec',5.)
    if not isinstance(yellow,(int,float)) or not isinstance(red,(int,float)) or not .5<=yellow<red<=30:
        raise ConfigError(f"{NAME}: policy: 0.5 <= yellow_sec < red_sec <= 30")
    immediate=pc.get('immediate_phone_conf',.55)
    if not isinstance(immediate,(int,float)) or not .3<=immediate<=1:
        raise ConfigError(f"{NAME}: policy.immediate_phone_conf в [0.3,1]")
    ec=cfg.get('evidence',{})
    for key,default,low,high in (('fps',5,1,10),('pre_sec',2.,0,5),('post_sec',2.,0,5),('segment_sec',15.,5,60)):
        value=ec.get(key,default)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not low<=value<=high:
            raise ConfigError(f"{NAME}: evidence.{key} в [{low},{high}]")

    cfg["profile"] = prof
    cfg["_domains"] = domains
    cfg["_test_url"] = url
    cfg["_path"] = os.path.abspath(path)
    return cfg


def ensure_hash_salt(cfg):
    """Генерирует salt_hex для hash_chain при первом старте. Правит только свою строку."""
    import binascii
    salt = binascii.hexlify(os.urandom(16)).decode()
    path = cfg.get("_path", "")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        raise ConfigError(f"{NAME}: нет доступа к файлу для записи соли: {e}")
    start = next((i for i, l in enumerate(lines) if l.strip() == "hash_chain:"), None)
    if start is None:
        raise ConfigError(f"{NAME}: нет секции 'hash_chain', задайте salt_hex вручную")
    target = None
    for i in range(start + 1, len(lines)):
        s = lines[i]
        if s.strip() and not s[0].isspace():
            break
        if s.strip().startswith("salt_hex:"):
            target = i
            break
    if target is None:
        raise ConfigError(f"{NAME}: нет поля 'hash_chain.salt_hex', задайте вручную")
    indent = lines[target][:len(lines[target]) - len(lines[target].lstrip())]
    lines[target] = f'{indent}salt_hex: "{salt}"\n'
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    cfg["hash_chain"]["salt_hex"] = salt
    return salt
