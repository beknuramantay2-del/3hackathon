"""Count actual warnings on an honest-behavior video; no synthetic zero-FPR claims."""
import argparse
import json
import sys
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from proctor.tools.replay_evaluator import run
from proctor.core.config import load_config

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video",required=True,help="Размеченное видео честного поведения")
    ap.add_argument("--output",required=True)
    ap.add_argument("--config")
    ap.add_argument("--calibration",choices=("five","center"),default="five")
    a = ap.parse_args()
    events = run(a.video,load_config(a.config),a.calibration)
    counts = dict(Counter(e["type"] for e in events))
    result = {"source":a.video,"events":events,"warning_counts":counts,
              "note":"Это подсчёт предупреждений; ложность проверяется человеком по видео, не автоматически"}
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(counts,ensure_ascii=False))

if __name__ == "__main__":
    main()
