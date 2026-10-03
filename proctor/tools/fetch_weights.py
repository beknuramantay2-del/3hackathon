"""Скачать веса YOLO в репо для офлайн-демо (интернета на площадке может не быть)."""
import os

def main():
    os.makedirs("proctor/weights", exist_ok=True)
    try:
        from ultralytics import YOLO
        m = YOLO("yolov8n.pt")  # скачает при первом запуске
        dst = "proctor/weights/yolov8n.pt"
        import shutil
        src = m.ckpt_path if hasattr(m, "ckpt_path") else "yolov8n.pt"
        if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy(src, dst)
            print(f"Веса скопированы: {dst}")
        else:
            print(f"Веса на месте: {src} -> проверьте {dst}")
        print("В config.yaml укажите model: proctor/weights/yolov8n.pt для офлайна.")
    except Exception as e:
        print(f"[weights] не скачалось: {e}")
        print("Скачайте вручную: https://github.com/ultralytics/assets/releases и положите в proctor/weights/")

if __name__ == "__main__":
    main()
