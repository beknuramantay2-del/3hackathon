"""Encode a supplied local image as static video. NOT a labelled CV scenario dataset."""
import argparse
import cv2

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image",required=True)
    ap.add_argument("--output",required=True)
    ap.add_argument("--seconds",type=int,default=35)
    a = ap.parse_args()
    image = cv2.imread(a.image)
    if image is None or a.seconds <= 0:
        ap.error("provide a readable image and positive duration")
    image = cv2.resize(image,(640,480))
    writer = cv2.VideoWriter(a.output,cv2.VideoWriter_fourcc(*"mp4v"),30,(640,480))
    if not writer.isOpened():
        raise SystemExit("Video encoder unavailable")
    try:
        for _ in range(a.seconds*30):
            writer.write(image)
    finally:
        writer.release()

if __name__ == "__main__":
    main()
