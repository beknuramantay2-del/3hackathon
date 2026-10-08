import os
import shutil
from pathlib import Path


def fetch_models(models=("yolov8n.pt", "yolov8s.pt"), factory=None):
    if factory is None:
        from ultralytics import YOLO

        factory = YOLO
    destination = Path("proctor/weights")
    destination.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in models:
        dst = destination / name
        model = factory(str(dst) if dst.is_file() else name)
        src = Path(getattr(model, "ckpt_path", None) or name)
        if not src.is_file():
            raise FileNotFoundError(f"Нет скачанного файла весов: {src}")
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        print(f"Веса готовы: {dst}")
        paths.append(dst)
    return paths


def main():
    os.chdir(Path(__file__).resolve().parents[2])
    try:
        fetch_models()
    except Exception as e:
        print(f"[weights] не скачалось: {e}")
        print(
            "Скачайте yolov8n.pt и yolov8s.pt из https://github.com/ultralytics/assets/releases и положите в proctor/weights/"
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
