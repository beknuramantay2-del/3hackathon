"""Фаза 0 spike: камера + YOLO + FaceMesh в одном цикле, реальные FPS. Только измеренные цифры."""
import argparse, time, cv2

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=None)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--imgsz", type=int, default=384)
    ap.add_argument("--shots", default="data/bench")
    a = ap.parse_args()
    import os
    os.makedirs(a.shots, exist_ok=True)

    cap = cv2.VideoCapture(a.video if a.video else 0)
    try:
        from ultralytics import YOLO
        yolo = YOLO("yolov8n.pt")
    except Exception as e:
        print(f"[bench] YOLO недоступна: {e}")
        yolo = None
    try:
        import mediapipe as mp
        mesh = mp.solutions.face_mesh.FaceMesh(max_num_faces=2, refine_landmarks=True)
    except Exception as e:
        print(f"[bench] FaceMesh недоступна: {e}")
        mesh = None

    n = ny = nf = 0
    ty = tf = 0.0
    t0 = time.time()
    i = 0
    while time.time() - t0 < a.seconds:
        ok, fr = cap.read()
        if not ok:
            break
        n += 1
        if yolo is not None:
            s = time.time()
            try:
                r = yolo.predict(fr, imgsz=a.imgsz, verbose=False)[0]
                found = any(int(b.cls[0]) == 67 for b in r.boxes) if r.boxes is not None else False
                if found:
                    ny += 1
            except Exception as e:
                print("[bench] yolo:", e)
            ty += time.time() - s
        if mesh is not None:
            s = time.time()
            try:
                res = mesh.process(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
                if res.multi_face_landmarks:
                    nf += 1
            except Exception as e:
                print("[bench] mesh:", e)
            tf += time.time() - s
        if i % 30 == 0:
            cv2.imwrite(f"{a.shots}/frame_{i}.jpg", fr)
        i += 1
    dur = time.time() - t0
    print(f"кадров: {n} за {dur:.1f}с => общий FPS: {n / max(dur, 1e-6):.1f}")
    if yolo is not None:
        print(f"YOLO: кадров с телефоном {ny}/{n}, среднее время инференса: {ty / max(n, 1) * 1000:.0f}мс")
    if mesh is not None:
        print(f"FaceMesh: кадров с лицом {nf}/{n}, среднее время: {tf / max(n, 1) * 1000:.0f}мс")
    print("Условия замера запишите в презентацию: железо, свет, imgsz, дистанция до камеры.")
    cap.release()

if __name__ == "__main__":
    main()
