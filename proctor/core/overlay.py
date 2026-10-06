"""Overlay fresh detections on the latest preview; mirror pixels, never text/directions."""
import cv2

def render_overlay(packet,face,yolo,hands,mirror=True,max_age=.35):
    vis = cv2.flip(packet.frame,1) if mirror else packet.frame.copy()
    h,w = vis.shape[:2]
    def draw(rect,label,color):
        x1,y1,x2,y2 = map(int,rect)
        if mirror:
            x1,x2 = w-x2,w-x1
        x1,x2 = max(0,x1),min(w-1,x2)
        y1,y2 = max(0,y1),min(h-1,y2)
        if x2 <= x1 or y2 <= y1:
            return
        cv2.rectangle(vis,(x1,y1),(x2,y2),color,2)
        cv2.putText(vis,label,(x1,max(12,y1-4)),cv2.FONT_HERSHEY_SIMPLEX,.45,color,1)
    def fresh(result):
        return result is not None and result.captured_at > 0 and 0 <= packet.captured_at-result.captured_at <= max_age and not result.error
    if fresh(face):
        for b in face.face_boxes:
            draw(b,"face",(80,210,100) if b == face.face_box else (60,190,240))
        for point in face.eye_points:
            x,y=point
            if mirror:
                x=w-x
            cv2.circle(vis,(x,y),3,(240,220,40),-1)
    if fresh(yolo):
        for b in yolo.persons:
            draw((b.x1,b.y1,b.x2,b.y2),"person",(240,140,70))
        for b in yolo.candidates:
            if not any(abs(b.x1-p.x1)+abs(b.y1-p.y1)<12 for p in yolo.phones):
                draw((b.x1,b.y1,b.x2,b.y2),f"candidate {b.conf:.2f}",(40,180,240))
        for b in yolo.phones:
            label = f"phone #{b.track_id} {b.conf:.2f}"+(" predicted" if not b.observed else "")
            color = (80,80,240) if b.confirmed and b.observed else (150,150,150)
            # Extrapolate only the overlay to the displayed frame; never feed this back into rules.
            dt = min(.15,max(0.,packet.captured_at-yolo.captured_at))
            predicted = tuple(x+v*dt for x,v in zip((b.x1,b.y1,b.x2,b.y2),b.velocity))
            draw(predicted,label,color)
    if fresh(hands):
        for b in hands.boxes:
            draw(b,"hand",(220,210,60))
    return vis
