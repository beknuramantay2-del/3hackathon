"""Bounded JPEG pre-roll + segmented raw-camera footage. Encoding never blocks Qt.
Actual capture timestamps are saved; this is sampled evidence, not a full exam video.
"""
from collections import deque
from pathlib import Path
import hashlib, json, threading, time
import cv2
import numpy as np
from .pipeline import LatestSlot

class EvidenceRecorder:
    def __init__(self, store, directory, fps=5, pre=2., post=2., segment=15., source='camera'):
        if not 1<=fps<=10 or not 0<=pre<=5 or not 0<=post<=5 or not 5<=segment<=60:
            raise ValueError('unbounded evidence settings')
        self.store=store; self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True)
        self.fps,self.pre,self.post,self.segment=fps,pre,post,segment
        self.source=source; self.wall_offset=time.time()-time.monotonic()
        self.frames=LatestSlot(); self.lock=threading.Lock(); self.pending={}
        self.ring=deque(maxlen=int(fps*pre)+2); self.active=set(); self.tags=set()
        self.writer=None; self.clip_tags=set(); self.capture_times=[]; self.clip_start=0.
        self.stop_event=threading.Event(); self.error=''; self.failures=0; self.clips=0
        self.retry_at=0.; self.post_until=0.; self.last_encoded=-1e9; self.path=None
        self.thread=threading.Thread(target=self._run,name='evidence-recorder',daemon=True); self.thread.start()

    def push(self, packet):
        self.frames.put(packet)  # reference to immutable captured frame, no copy/encoding here

    def submit(self, rows):
        with self.lock:
            for e in rows:
                if len(self.pending)>=128 and e['ident'] not in self.pending:
                    self.error='Очередь эпизодов заполнена: запись неполная'; self.failures+=1; continue
                self.pending[e['ident']]=dict(e)

    def _changes(self):
        with self.lock:
            rows=list(self.pending.values()); self.pending.clear()
        for e in rows:
            self.store.save_episode(e,self.wall_offset,self.source)
            self.tags.add(e['ident'])
            if e['closed']:
                self.active.discard(e['ident']); self.post_until=time.monotonic()+self.post
            else:
                self.active.add(e['ident'])
            if self.writer is not None: self.clip_tags.add(e['ident'])

    def _write(self, stamp, jpg):
        if self.capture_times and stamp<=self.capture_times[-1]: return
        frame=cv2.imdecode(jpg,cv2.IMREAD_COLOR)
        if frame is None: raise OSError('JPEG decode failed')
        self.writer.write(frame); self.capture_times.append(stamp)

    def _open(self, now):
        self.path=self.directory/(str(time.time_ns())+'.avi')
        self.writer=cv2.VideoWriter(str(self.path),cv2.VideoWriter_fourcc(*'MJPG'),self.fps,(480,360))
        if not self.writer.isOpened():
            self.writer.release(); self.writer=None; raise OSError('MJPG video encoder unavailable')
        self.clip_start=now; self.capture_times=[]; self.clip_tags=set(self.active)|self.tags
        for stamp,jpg in self.ring:
            if now-stamp<=self.pre+.25: self._write(stamp,jpg)

    def _finish(self, truncated=False):
        if self.writer is None: return
        self.writer.release(); self.writer=None
        if not self.capture_times:
            self.path.unlink(missing_ok=True); raise OSError('Нет свежих кадров для футажа')
        metadata=dict(source=self.source, fps=self.fps,size=[480,360],capture_times=self.capture_times,
            wall_offset=self.wall_offset,frames=len(self.capture_times),truncated=truncated,
            sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),review='Signal, not proof of misconduct')
        self.path.with_suffix('.json').write_text(json.dumps(metadata,indent=2))
        self.store.save_clip(self.path.resolve(),metadata,self.clip_tags); self.clips+=1
        self.capture_times=[]; self.clip_tags=set()

    def _step(self, now, version):
        self._changes()
        if self.writer is not None and (now-self.clip_start>=self.segment or not self.active and now>=self.post_until):
            self._finish()
        if self.writer is None and now>=self.retry_at and (self.active or self.tags and now<self.post_until): self._open(now)
        self.tags.clear()
        version,p=self.frames.take_after(version)
        if p is None or now-p.captured_at>.7 or p.captured_at-self.last_encoded<1/self.fps:
            return version
        # Aspect-fit: preserve desk area, do not crop phone evidence.
        h,w=p.frame.shape[:2]; scale=min(480/w,360/h)
        resized=cv2.resize(p.frame,(max(1,round(w*scale)),max(1,round(h*scale))))
        canvas=np.zeros((360,480,3),np.uint8); rh,rw=resized.shape[:2]
        canvas[(360-rh)//2:(360-rh)//2+rh,(480-rw)//2:(480-rw)//2+rw]=resized
        ok,jpg=cv2.imencode('.jpg',canvas,[cv2.IMWRITE_JPEG_QUALITY,75])
        if not ok: raise OSError('Evidence JPEG encoder failed')
        self.last_encoded=p.captured_at; self.ring.append((p.captured_at,jpg))
        if self.writer is not None: self._write(p.captured_at,jpg)
        return version

    def _run(self):
        version=0
        while not self.stop_event.wait(.015):
            try: version=self._step(time.monotonic(),version)
            except Exception as exc:
                self.error=str(exc); self.failures+=1; self.retry_at=time.monotonic()+1.
                if self.writer is not None: self.writer.release(); self.writer=None
        try:
            self._changes(); self._finish(truncated=bool(self.active))
        except Exception as exc: self.error=str(exc); self.failures+=1

    def stop(self):
        self.stop_event.set(); self.thread.join(timeout=10.)
        if self.thread.is_alive(): raise RuntimeError('Evidence recorder did not stop; database must stay open')
