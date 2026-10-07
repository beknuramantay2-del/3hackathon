import argparse
import getpass
import json
from pathlib import Path
from .database import SchoolDB, SchoolError


def load_test(path):
    source = Path(path)
    if source.stat().st_size > 1_000_000:
        raise SchoolError("Файл вопросов слишком большой")
    data = json.loads(source.read_text(encoding="utf8"))
    if not isinstance(data, dict) or not isinstance(data.get("title"), str):
        raise SchoolError("Нужны title, minutes и questions")
    minutes = data.get("minutes")
    questions = data.get("questions")
    if type(minutes) is not int or not 1 <= minutes <= 180:
        raise SchoolError("minutes: целое число от 1 до 180")
    if not isinstance(questions, list) or not all(
        isinstance(q, dict) and isinstance(q.get("text"), str) for q in questions
    ):
        raise SchoolError("questions: список вопросов с text, options, correct")
    return data["title"], minutes * 60, questions


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Подготовка локальных аккаунтов и вопросов, отдельно от панели наблюдения"
    )
    parser.add_argument("--db", default="data/school.db", help="Локальный файл SQLite")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Создать первого администратора")
    init.add_argument("--login", required=True)
    init.add_argument("--name", required=True)
    account = commands.add_parser("account", help="Создать учётную запись")
    account.add_argument(
        "--login", required=True, help="Логин действующего администратора"
    )
    account.add_argument("--new-login", required=True)
    account.add_argument("--name", required=True)
    account.add_argument(
        "--role", choices=("student", "examiner", "admin"), default="student"
    )
    quiz = commands.add_parser(
        "import-test", help="Импортировать подготовленный преподавателем JSON"
    )
    quiz.add_argument(
        "--login", required=True, help="Логин администратора или экзаменатора"
    )
    quiz.add_argument("file")
    args = parser.parse_args(argv)
    db = SchoolDB(args.db)
    token = None
    try:
        if args.command == "init":
            password = getpass.getpass("Новый пароль администратора: ")
            if password != getpass.getpass("Повторите пароль: "):
                raise SchoolError("Пароли не совпадают")
            db.setup_owner(args.login, args.name, password)
            print("Администратор создан")
            return 0
        token = db.login(
            args.login, getpass.getpass("Пароль действующего пользователя: ")
        )
        if args.command == "account":
            db.actor(token, ("admin",))
            password = getpass.getpass("Пароль новой учётной записи: ")
            if password != getpass.getpass("Повторите пароль: "):
                raise SchoolError("Пароли не совпадают")
            ident = db.create_user(
                token, args.new_login, args.name, password, args.role
            )
            print("Учётная запись создана: " + ident)
        else:
            db.actor(token, ("admin", "examiner"))
            ident = db.create_test(token, *load_test(args.file))
            print("Вопросы импортированы: " + ident)
        return 0
    except (SchoolError, OSError, ValueError) as exc:
        parser.exit(2, str(exc) + "\n")
    finally:
        if token:
            db.logout(token)
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
