"""HTTP-панель экзаменатора на 127.0.0.1: события, статус, индекс, кнопка завершения."""
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

from proctor.core.strings import load_strings
from proctor.core.trust_score import TrustCalculator

T = load_strings()

CSS = ("body{font-family:'Segoe UI',Arial,sans-serif;font-size:14px;background:#F5F5F5;color:#1A1A1A;margin:0;padding:16px}"
       ".panel{background:#FFFFFF;border:1px solid #CCCCCC;border-radius:2px;padding:16px;margin-bottom:16px}"
       "h1{font-size:18px;margin:0 0 8px 0}p{margin:4px 0}"
       "table{width:100%;border-collapse:collapse;background:#FFFFFF}"
       "th,td{border:1px solid #CCCCCC;padding:8px;text-align:left;font-size:14px}th{font-weight:bold}"
       "tr.new td{background:#FDECEA}"
       "button{background:#2B579A;color:#FFFFFF;border:1px solid #2B579A;border-radius:2px;padding:8px 16px;font-size:14px}"
       "img.thumb{width:160px;height:120px;object-fit:cover;border:1px solid #CCCCCC;display:block}")


class ExaminerServer:
    def __init__(self, host, port, db_path, shots_dir, weights, get_fio, get_status):
        self.host = host
        self.port = port
        self.db_path = db_path
        self.shots_dir = shots_dir
        self.weights = dict(weights)
        self.get_fio = get_fio
        self.get_status = get_status
        self.finish_requested = threading.Event()
        self._server = None
        self._thread = None

    def _rows(self):
        try:
            con = sqlite3.connect(self.db_path)
            rows = con.execute(
                "SELECT type,severity,t_start,duration,screenshot FROM events ORDER BY t_start").fetchall()
            con.close()
            return rows
        except Exception:
            return []

    def _trust(self, rows, t0):
        from types import SimpleNamespace
        calc = TrustCalculator(self.weights)
        for typ, sev, ts, dur, shot in rows:
            calc.apply_violation(SimpleNamespace(type=typ, severity=sev, t_start=ts))
        return calc.get_score(T.get("trust_labels") or {})

    def _page(self):
        rows = self._rows()
        t0 = rows[0][2] if rows else 0
        trust = self._trust(rows, t0)
        items = ""
        for i, (typ, sev, ts, dur, shot) in enumerate(rows):
            s = max(0, int(ts - t0))
            mmss = f"{s // 60:02d}:{s % 60:02d}"
            base = os.path.basename(shot or "")
            full = os.path.join(self.shots_dir, base)
            thumb = (f'<img class="thumb" src="/shots/{base}" alt="Кадр">'
                     if base and os.path.exists(full) else T["no_frame"])
            cls = ' class="new"' if i == len(rows) - 1 else ""
            items += f"<tr{cls}><td>{mmss}</td><td>{typ}</td><td>{dur:.0f} с</td><td>{thumb}</td></tr>"
        if not items:
            items = f'<tr><td colspan="4">{T["no_violations"]}</td></tr>'
        table = (f"<table><tr><th>{T['th_time']}</th><th>{T['th_type']}</th>"
                 f"<th>{T['th_duration']}</th><th>{T['th_shot']}</th></tr>{items}</table>")
        head = (f'<div class="panel"><h1>{T["examiner_title"]}: {self.get_fio() or T["student"]}</h1>'
                f"<p>{T['violations']}: <b>{len(rows)}</b></p>"
                f"<p>{T['session_status']}: {self.get_status()}</p>"
                f"<p>{T['trust_score']}: {trust['trust_score']}/100.</p>"
                f'<form method="post" action="/finish"><button type="submit">{T["examiner_finish"]}</button></form></div>')
        return (f'<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
                f'<meta http-equiv="refresh" content="1">'
                f"<title>{T['examiner_title']}</title><style>{CSS}</style></head>"
                f"<body>{head}<div class=\"panel\">{table}</div></body></html>").encode("utf-8")

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path.startswith("/shots/"):
                    name = os.path.basename(unquote(parsed.path[len("/shots/"):]))
                    full = os.path.join(server.shots_dir, name)
                    if name and os.path.exists(full):
                        self.send_response(200)
                        self.send_header("Content-Type", "image/jpeg")
                        self.end_headers()
                        with open(full, "rb") as f:
                            self.wfile.write(f.read())
                    else:
                        self.send_response(404)
                        self.end_headers()
                elif parsed.path == "/":
                    body = server._page()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if urlparse(self.path).path == "/finish":
                    server.finish_requested.set()
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://{self.host}:{self.port}"

    def stop(self):
        try:
            if self._server:
                self._server.shutdown()
        except Exception:
            pass
