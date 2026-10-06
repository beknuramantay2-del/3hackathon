import sqlite3
from concurrent.futures import ThreadPoolExecutor
from proctor.core.store import Store,EventWriter
from proctor.core.models import Violation
from proctor.core.hash_chain import EventHashChain


def test_sqlite_parallel_connections_and_hash_order(tmp_path):
    path = str(tmp_path/"events.db")
    stores = [Store(path,str(tmp_path/"shots"),salt="test-salt") for _ in range(2)]
    def write(i):
        stores[i%2].add(Violation("TEST",1,1000-i,.1,"",{"i":i}))
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write,range(100)))
        assert len(stores[0].all()) == 100
        # Insertion order, not wall-clock sorting, must verify across multiple writers.
        assert EventHashChain("test-salt").verify(stores[0].all_with_hash())
    finally:
        for s in stores:
            s.close()


def test_writer_drain(tmp_path):
    store = Store(str(tmp_path/"events.db"),str(tmp_path/"shots"),salt="salt")
    writer = EventWriter(store)
    try:
        for i in range(20):
            assert writer.submit(Violation("TEST",1,i,0,"",{}))
        writer.flush()
        assert len(store.all()) == 20 and writer.failures == 0
        assert EventHashChain("salt").verify(store.all_with_hash())
    finally:
        writer.stop(); store.close()


def test_transaction_failure_no_phantom_hash(tmp_path):
    s = Store(str(tmp_path/"events.db"),str(tmp_path/"shots"),salt="salt")
    try:
        s.con.execute("CREATE TRIGGER reject_bad BEFORE INSERT ON events WHEN NEW.type='BAD' BEGIN SELECT RAISE(ABORT,'reject'); END")
        s.con.commit()
        try:
            s.add(Violation("BAD",1,0,0,"",{}))
        except sqlite3.IntegrityError:
            pass
        else:
            assert False,"expected trigger rejection"
        s.add(Violation("GOOD",1,1,0,"",{}))
        assert EventHashChain("salt").verify(s.all_with_hash())
    finally:
        s.close()


def test_screenshot_failure_still_persists_event(tmp_path):
    s = Store(str(tmp_path/"events.db"),str(tmp_path/"shots"))
    def broken(*args):
        raise OSError("disk full")
    s.save_frame = broken
    writer = EventWriter(s)
    try:
        assert writer.submit(Violation("TEST",1,0,0,"",{}),object())
        writer.flush()
        rows = s.all()
        assert len(rows) == 1 and "screenshot_error" in rows[0][5]
        assert writer.failures == 1 and "disk full" in writer.error
    finally:
        writer.stop(); s.close()
