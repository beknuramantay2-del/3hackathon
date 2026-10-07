import cv2


def render_overlay(packet, face, yolo, hands, mirror=True, max_age=0.35, states=None):
    vis = cv2.flip(packet.frame, 1) if mirror else packet.frame.copy()
    h, w = vis.shape[:2]

    def draw(rect, label, color):
        x1, y1, x2, y2 = map(int, rect)
        if mirror:
            x1, x2 = w - x2, w - x1
        x1, x2 = max(0, x1), min(w - 1, x2)
        y1, y2 = max(0, y1), min(h - 1, y2)
        if x2 <= x1 or y2 <= y1:
            return
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            vis, label, (x1, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1
        )

    def fresh(result):
        return (
            result is not None
            and result.captured_at > 0
            and 0 <= packet.captured_at - result.captured_at <= max_age
            and not result.error
        )

    if fresh(face):
        for eye in face.eye_boxes:
            draw(eye, "eye", (230, 160, 85))
        for b in face.face_boxes:
            draw(b, "face", (80, 210, 100) if b == face.face_box else (60, 190, 240))
        for point in face.eye_points:
            x, y = point
            if mirror:
                x = w - x
            cv2.circle(vis, (x, y), 3, (240, 220, 40), -1)
            if (
                states
                and (states.get("calibrated") or states.get("preview_ready"))
                and (face.gaze_valid or face.gaze_preview_valid)
            ):
                gaze = states.get("gaze", "UNKNOWN")
                sign = 1 if mirror else -1
                vectors = {
                    "LEFT": (-18 * sign, 0),
                    "RIGHT": (18 * sign, 0),
                    "UP": (0, -18),
                    "DOWN": (0, 18),
                }
                if gaze in vectors:
                    dx, dy = vectors[gaze]
                    color = (
                        (50, 180, 240)
                        if "GAZE_" + gaze in states.get("warnings_active", [])
                        else (240, 180, 75)
                    )
                    cv2.arrowedLine(
                        vis,
                        (x, y),
                        (max(1, min(w - 2, x + dx)), max(1, min(h - 2, y + dy))),
                        color,
                        2,
                        tipLength=0.4,
                    )
        if (
            states
            and (states.get("calibrated") or states.get("preview_ready"))
            and face.pose_valid
            and face.face_box
        ):
            direction = states.get("head", "UNKNOWN")
            if direction != "UNKNOWN":
                x1, y1, x2, y2 = face.face_box
                x = int((x1 + x2) / 2)
                y = max(24, int(y1) - 18)
                if mirror:
                    x = w - x
                text = (
                    "HEAD "
                    + direction
                    + (" preview" if not states.get("calibrated") else "")
                )
                cv2.putText(
                    vis,
                    text,
                    (max(0, min(w - 150, x - 60)), y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (240, 180, 75),
                    1,
                )
                sign = 1 if mirror else -1
                vector = {
                    "LEFT": (-24 * sign, 0),
                    "RIGHT": (24 * sign, 0),
                    "UP": (0, -20),
                    "DOWN": (0, 20),
                }.get(direction)
                if vector:
                    dx, dy = vector
                    cv2.arrowedLine(
                        vis,
                        (x, min(h - 2, y + 5)),
                        (max(1, min(w - 2, x + dx)), max(1, min(h - 2, y + 5 + dy))),
                        (240, 180, 75),
                        2,
                        tipLength=0.4,
                    )
    if fresh(yolo):
        for b in yolo.persons:
            draw((b.x1, b.y1, b.x2, b.y2), "person", (240, 140, 70))
        for b in yolo.candidates:
            if not any(abs(b.x1 - p.x1) + abs(b.y1 - p.y1) < 12 for p in yolo.phones):
                strong = b.observed and b.conf >= (states or {}).get(
                    "immediate_phone_conf", 1.0
                )
                draw(
                    (b.x1, b.y1, b.x2, b.y2),
                    f"{'phone signal' if strong else 'candidate'} {b.conf:.2f}",
                    (80, 80, 240) if strong else (40, 180, 240),
                )
        for b in yolo.phones:
            label = (
                ("phone" if b.confirmed else "candidate")
                + f" #{b.track_id} {b.conf:.2f}"
                + (
                    " predicted"
                    if not b.observed
                    else " " + b.support_kind if b.supported else ""
                )
            )
            color = (
                (80, 80, 240)
                if b.observed
                and (
                    b.confirmed
                    or b.conf >= (states or {}).get("immediate_phone_conf", 1.0)
                )
                else (40, 180, 240) if b.observed else (150, 150, 150)
            )

            dt = min(0.15, max(0.0, packet.captured_at - yolo.captured_at))
            predicted = tuple(
                x + v * dt for x, v in zip((b.x1, b.y1, b.x2, b.y2), b.velocity)
            )
            draw(predicted, label, color)
    if fresh(hands):
        for b in hands.boxes:
            draw(b, "hand", (220, 210, 60))
    return vis
