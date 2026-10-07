import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proctor.core.hash_chain import EventHashChain
from proctor.core.store import Store
from proctor.core.models import Violation


def make_store(salt):
    tmp = tempfile.mkdtemp()
    return Store(os.path.join(tmp, "s.db"), os.path.join(tmp, "shots"), salt=salt), tmp


def test_roundtrip_ok():
    store, _ = make_store("salt1")
    store.add(Violation("GAZE_DOWN", 1, 1000.0, 3.0, "", {}))
    store.add(Violation("PHONE_DETECTED", 2, 1010.0, 0.5, "", {}))
    assert EventHashChain("salt1").verify(store.all_with_hash())


def test_tamper_breaks():
    store, _ = make_store("salt2")
    store.add(Violation("GAZE_DOWN", 1, 1000.0, 3.0, "", {}))
    con = sqlite3.connect(store.db)
    con.execute("UPDATE events SET severity=3 WHERE type='GAZE_DOWN'")
    con.commit()
    assert not EventHashChain("salt2").verify(store.all_with_hash())


def test_resume_from_db():
    store, _ = make_store("salt3")
    store.add(Violation("NO_FACE", 2, 1000.0, 2.0, "", {}))
    again = Store(store.db, store.shots, salt="salt3")
    again.add(Violation("NO_FACE", 2, 1020.0, 2.0, "", {}))
    assert EventHashChain("salt3").verify(again.all_with_hash())


if __name__ == "__main__":
    for fn in (test_roundtrip_ok, test_tamper_breaks, test_resume_from_db):
        fn()
        print(f"OK {fn.__name__}")
    print("Все тесты прошли (синтетика, без камеры).")
