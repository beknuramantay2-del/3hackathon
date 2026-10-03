"""Запись пароля экзаменатора в config.yaml (только соль и хеш PBKDF2-HMAC-SHA256)."""
import binascii
import getpass
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def patch(path, salt, digest, iterations):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == "examiner:"), None)
    if start is None:
        raise SystemExit("config.yaml: нет секции 'examiner'")
    targets = {}
    for i in range(start + 1, len(lines)):
        s = lines[i]
        if s.strip() and not s[0].isspace():
            break
        for key in ("salt_hex", "hash_hex", "iterations"):
            if s.strip().startswith(key + ":"):
                targets[key] = i
    if not all(k in targets for k in ("salt_hex", "hash_hex", "iterations")):
        raise SystemExit("config.yaml: нет полей salt_hex/hash_hex/iterations в 'examiner'")
    for key, value in (("salt_hex", salt), ("hash_hex", digest), ("iterations", str(iterations))):
        i = targets[key]
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        quote = "" if key == "iterations" else '"'
        lines[i] = f"{indent}{key}: {quote}{value}{quote}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--iterations", type=int, default=100000)
    a = ap.parse_args()
    path = a.config or ("proctor/config.yaml" if os.path.exists("proctor/config.yaml") else "config.yaml")
    first = getpass.getpass("Новый пароль экзаменатора: ")
    second = getpass.getpass("Повторите пароль: ")
    if not first:
        raise SystemExit("пустой пароль запрещен")
    if first != second:
        raise SystemExit("пароли не совпадают")
    salt = binascii.hexlify(os.urandom(16)).decode()
    digest = hashlib.pbkdf2_hmac("sha256", first.encode("utf-8"), bytes.fromhex(salt), a.iterations).hex()
    patch(path, salt, digest, a.iterations)
    print(f"записано: {path}")


if __name__ == "__main__":
    main()
