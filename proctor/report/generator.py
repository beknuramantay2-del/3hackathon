import os
from html import escape
from types import SimpleNamespace
from proctor.core.strings import load_strings

T = load_strings()

CSS = (
    "body{font-family:'Segoe UI',Arial,sans-serif;font-size:14px;background:#F5F5F5;color:#1A1A1A;margin:0;padding:16px}"
    ".panel{background:#FFFFFF;border:1px solid #CCCCCC;border-radius:2px;padding:16px;margin-bottom:16px}"
    "h1{font-size:18px;margin:0 0 8px 0}h2{font-size:16px;margin:0 0 8px 0}p{margin:4px 0}"
    "table{width:100%;border-collapse:collapse;background:#FFFFFF}"
    "th,td{border:1px solid #CCCCCC;padding:8px;text-align:left;font-size:14px}th{font-weight:bold}"
    "tr.new td{background:#FDECEA}tr.total td{font-weight:bold}"
    ".bad{color:#B00020;font-weight:bold}"
    "img.thumb{width:160px;height:120px;object-fit:cover;border:1px solid #CCCCCC;display:block}"
)


def _mmss(ts, t0):
    s = max(0, int(ts - t0))
    return f"{s // 60:02d}:{s % 60:02d}"


def _rows(store, out_path):
    out = []
    for typ, sev, ts, dur, shot, det in store.all():
        typ = escape(typ)
        relative = (
            escape(os.path.relpath(shot, os.path.dirname(out_path) or "."), quote=True)
            if shot
            else ""
        )
        thumb = (
            f'<img class="thumb" src="{relative}" alt="Кадр">'
            if shot and os.path.exists(shot)
            else T["no_frame"]
        )
        out.append((_mmss(ts, store.t0), typ, f"{dur:.0f} с", thumb))
    return out


def _table(rows, mark_latest=False):
    items = ""
    for i, (t, typ, dur, thumb) in enumerate(rows):
        cls = ' class="new"' if (mark_latest and i == len(rows) - 1 and rows) else ""
        items += (
            f"<tr{cls}><td>{t}</td><td>{typ}</td><td>{dur}</td><td>{thumb}</td></tr>"
        )
    if not items:
        items = f'<tr><td colspan="4">{T["no_violations"]}</td></tr>'
    return (
        f"<table><tr><th>{T['th_time']}</th><th>{T['th_type']}</th><th>{T['th_duration']}</th>"
        f"<th>{T['th_shot']}</th></tr>{items}</table>"
    )


def _write(path, body):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)


def generate(store, cfg, out_path, fps=0, duration=0, fio="", trust=None):
    from proctor.core.trust_score import TrustCalculator

    rows = _rows(store, out_path)
    if trust is None:
        calc = TrustCalculator(cfg.get("trust_weights", {}))
        for typ, sev, ts, dur, shot, det in store.all():
            v = SimpleNamespace(type=typ, severity=sev, t_start=ts)
            calc.apply_violation(v)
        trust = calc.get_score((T.get("trust_labels") or {}))
    fio = escape(fio)
    score = trust["trust_score"]
    breakdown = f"<p>{trust['breakdown_text']}</p>" if trust["breakdown_text"] else ""
    chain_line = ""
    hc = cfg.get("hash_chain", {}) or {}
    if hc.get("enabled") and hc.get("salt_hex"):
        from proctor.core.hash_chain import EventHashChain

        ok = EventHashChain(hc["salt_hex"]).verify(store.all_with_hash())
        chain_line = f"<p>{T['integrity']}: {T['integrity_ok'] if ok else T['integrity_failed']}</p>"
    total_dur = sum(float(r[2].split()[0]) for r in rows) if rows else 0
    head = (
        f"<div class=\"panel\"><h1>{T['report_title']}: {fio or T['student']}</h1>"
        f"<p>{T['violations']}: {len(rows)}. {T['trust_score']}: {score}/100.</p>"
        f"{breakdown}{chain_line}</div>"
    )
    table = _table(rows)
    table = table.replace(
        "</table>",
        f"<tr class=\"total\"><td colspan=\"2\">{T['total']}</td>"
        f"<td>{total_dur:.0f} с</td><td>{len(rows)} {T['events']}</td></tr></table>",
    )
    body = (
        f'<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
        f"<title>{T['report_title']}</title><style>{CSS}</style></head>"
        f'<body>{head}<div class="panel">{table}</div></body></html>'
    )
    _write(out_path, body)
    return out_path, score, trust
