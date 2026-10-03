"""Тексты интерфейса из strings.yaml."""
import os
import yaml

NAME = "strings.yaml"


def load_strings(path=None):
    if path is None:
        here = os.path.join(os.path.dirname(__file__), "..", "strings.yaml")
        alt = "proctor/strings.yaml"
        path = alt if os.path.exists(alt) else here
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise RuntimeError(f"{NAME}: файл не найден: {path}")
    if not isinstance(data, dict):
        raise RuntimeError(f"{NAME}: корневой элемент должен быть словарем")
    return data
