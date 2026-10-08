import urllib.request
import urllib.error
from urllib.parse import urlencode
from proctor.core.store import Store
from proctor.report.generator import generate
from proctor.core.config import load_config


def test_report_valid_table_and_escaped_name(tmp_path):
    s = Store(str(tmp_path / "events.db"), str(tmp_path / "shots"))
    try:
        cfg = load_config()
        out = tmp_path / "report.html"
        generate(s, cfg, str(out), fio="<script>alert(1)</script>")
        text = out.read_text()
        assert "</table>" in text and '<tr class="total">' in text
        assert "<script>alert(1)</script>" not in text
        assert "&lt;script&gt;" in text
    finally:
        s.close()
