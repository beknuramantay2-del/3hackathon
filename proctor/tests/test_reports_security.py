import urllib.request
import urllib.error
from urllib.parse import urlencode
from proctor.core.store import Store
from proctor.core.examiner_server import ExaminerServer
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


def test_examiner_finish_csrf_and_escaped_name(tmp_path):
    s = Store(str(tmp_path / "events.db"), str(tmp_path / "shots"))
    server = ExaminerServer(
        "127.0.0.1",
        0,
        s.db,
        s.shots,
        {},
        lambda: "<script>x</script>",
        lambda: "Active",
    )
    server.start()
    url = "http://127.0.0.1:" + str(server._server.server_port)
    try:
        with urllib.request.urlopen(url) as response:
            page = response.read().decode()
        assert "<script>x</script>" not in page and "&lt;script&gt;" in page
        try:
            urllib.request.urlopen(url + "/finish", data=b"csrf=wrong")
        except urllib.error.HTTPError as e:
            assert e.code == 403
        else:
            assert False, "must reject unknown token"
        assert not server.finish_requested.is_set()
        with urllib.request.urlopen(
            url + "/finish", data=urlencode({"csrf": server.csrf}).encode()
        ) as response:
            assert response.status == 200
        assert server.finish_requested.is_set()
    finally:
        server.stop()
        s.close()
