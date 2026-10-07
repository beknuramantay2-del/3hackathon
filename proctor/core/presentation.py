LABELS = {
    "PHONE_DETECTED": "Телефон в кадре",
    "PHONE_IN_HAND": "Телефон в руке",
    "PHONE_LIFTED": "Телефон поднят",
    "PHONE_RAISED": "Телефон у лица / экрана",
    "PHONE_AIMED": "Возможное наведение телефона",
    "NO_FACE": "Ученик не виден",
    "MULTI_FACE": "Второе лицо",
    "CAMERA_COVERED": "Камера закрыта / тёмный кадр",
    "CAMERA_LOST": "Камера не передаёт кадры",
    "HOTKEY_BLOCKED": "Запрещённая клавиша",
    "FOCUS_LOST": "Потерян фокус окна",
    "GUARD_LOST": "Защита экзамена недоступна",
    "FORBIDDEN_PROCESS": "Посторонняя программа",
    "COPY_ATTEMPT": "Копирование / вставка",
    "TAB_SWITCH": "Смена вкладки",
}
for code, text in (
    ("LEFT", "влево"),
    ("RIGHT", "вправо"),
    ("UP", "вверх"),
    ("DOWN", "вниз"),
):
    LABELS["GAZE_" + code] = "Длительный взгляд " + text
    LABELS["HEAD_" + code] = "Голова " + text


def event_label(code):
    return LABELS.get(code, code)
