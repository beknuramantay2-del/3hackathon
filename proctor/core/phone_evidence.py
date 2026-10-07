"""Weak phone support from observations, never a hand/prediction alone.
Two lanes: hand+time; near-strong multi-scale (global/ROI) agreement+longer time.
Scores remain the model's raw scores; this is not a probability of correctness.
"""
from .tracking import iou,coords

class PartialPhoneEvidence:
    def __init__(self,minimum=.15,duration=.35,observations=3):
        self.minimum,self.duration,self.observations=minimum,duration,observations
        self.history=[]

    def update(self,candidates,hands,now,strong):
        old=self.history;self.history=[];used=set()
        hand_fresh=hands is not None and not hands.error and 0<=now-hands.captured_at<=.55
        for box in candidates:
            if box.conf < max(self.minimum,strong*.65) or box.conf >= strong:continue
            matching=[(iou(coords(box),item[0]),i,item) for i,item in enumerate(old)
                      if i not in used and 0<=now-item[2]<=.55]
            score,index,item=max(matching,default=(0,-1,None))
            if score>=.2:
                used.add(index);first,count=item[1],item[3]+1;dual,near=item[4],item[5]
            else:first,count,dual,near=now,1,None,None
            if box.detail_agreement:dual=now
            if box.conf>=max(.22,strong*.9):near=now
            self.history.append((coords(box),first,now,count,dual,near))
            overlap=False
            for h in hands.boxes if hand_fresh else ():
                ix=max(0,min(box.x2,h[2])-max(box.x1,h[0]));iy=max(0,min(box.y2,h[3])-max(box.y1,h[1]))
                if ix*iy/max(1,(box.x2-box.x1)*(box.y2-box.y1))>=.06:overlap=True;break
            hand_supported=overlap and count>=self.observations and now-first>=self.duration
            multi_supported=count>=4 and now-first>=.7 and dual is not None and near is not None and 0<=now-dual<=.55 and 0<=now-near<=.55
            box.supported=bool(hand_supported or multi_supported)
            box.support_kind='hand+time' if hand_supported else 'ROI+time' if multi_supported else ''
        return candidates
