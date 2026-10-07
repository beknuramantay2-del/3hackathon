# Локальный прокторинг

[Установка, подготовка аккаунтов и запуск панели/ученика](../README.md).

Основные команды из корня проекта:

```powershell
python -m proctor.school --monitor
python -m proctor.school --student --preview --perf weak
```

Вторую команду выполняйте в отдельном терминале на том же компьютере. Оба окна используют одну локальную SQLite. `--preview` — проверка без защиты; защищённый школьный режим требует Windows/admin и запуска без этого флага.

Для отдельной проверки CV без вопросов:

```powershell
python -m proctor.main --perf weak
```

Это операторский режим, не защищённый экзамен. Голова и глаза показываются отдельно; полная настройка нужна для направленных предупреждений.

- [Панель наблюдения и данные](docs/SCHOOL_SYSTEM.md)
- [Проверка требований](docs/CASE3_ACCEPTANCE.md)
- [Исправление отображения направлений](docs/DIRECTION_REGRESSION.md)
- [Исходные replay-измерения](docs/benchmarks/direction-regression-native.json)

Ранние документы CV_AUDIT, CV_LIVE, SCHOOL_MVP и CV_CALIBRATION_EPISODES описывают предыдущие этапы, а не актуальный порядок запуска.
