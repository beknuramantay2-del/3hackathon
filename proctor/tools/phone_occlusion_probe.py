"""Native static-image probe. Not a webcam/occlusion dataset accuracy benchmark.
Provide an already downloaded COCO128 directory; images never leave this computer.
"""
import argparse,json,time
from pathlib import Path
import cv2
from proctor.core.detector_yolo import DetectorYolo
from proctor.core.hands import HandsThread
from proctor.core.pipeline import FramePacket
from proctor.core.tracking import coords,iou

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--coco-root',type=Path,required=True)
    ap.add_argument('--image',default='000000000395')
    ap.add_argument('--model',default='proctor/weights/yolov8n.pt')
    ap.add_argument('--output',type=Path,default=Path('data/phone-occlusion-probe.json'))
    args=ap.parse_args()
    image=next(args.coco_root.rglob(args.image+'.jpg'))
    label=next(args.coco_root.rglob(args.image+'.txt'))
    phones=[list(map(float,l.split()[1:])) for l in label.read_text().splitlines() if l.startswith('67 ')]
    if not phones:raise SystemExit('Нет разметки cell phone')
    base=cv2.imread(str(image));height,width=base.shape[:2]
    cx,cy,w,h=max(phones,key=lambda b:b[2]*b[3])
    x1,y1,x2,y2=map(int,((cx-w/2)*width,(cy-h/2)*height,(cx+w/2)*width,(cy+h/2)*height))
    rows=[]
    for mode in ('full','half_left','half_bottom'):
        frame=base.copy()
        if mode=='half_left':frame[y1:y2,x1:(x1+x2)//2]=110
        if mode=='half_bottom':frame[(y1+y2)//2:y2,x1:x2]=110
        detector=DetectorYolo(args.model,640,adaptive=False);detector.setup()
        hands=HandsThread(detector.output.peek);hands.setup();detector.hand_provider=hands.output.peek
        try:
            for n in range(12):
                packet=FramePacket(n,time.monotonic(),frame)
                result=detector.process(packet);detector.output.put(result)
                hand_result=hands.process(packet);hands.output.put(hand_result)
                target=[b for b in result.phones if iou(coords(b),(x1,y1,x2,y2))>=.1]
                rows.append(dict(scene=mode,seq=n,confirmed=any(b.confirmed and b.observed for b in target),
                    candidates=[dict(conf=b.conf,bbox=coords(b)) for b in result.candidates],
                    hands=hand_result.boxes,tracks=[dict(conf=b.conf,bbox=coords(b),observed=b.observed,
                    confirmed=b.confirmed,support=b.support_kind) for b in target]))
                time.sleep(.12)
        finally:hands.teardown()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(source='COCO128 repeated static photograph; artificial grey occluder',
        image=args.image,model=args.model,target_bbox=[x1,y1,x2,y2],rows=rows),ensure_ascii=False,indent=2))
    print('Saved static probe:',args.output)

if __name__=='__main__':main()
