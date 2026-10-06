# Локальный прокторинг — лёгкий MVP, Кейс №3

Camera → latest-only workers (nano-YOLO / FaceMesh / gated Hands) → отдельные HEAD и GAZE → temporal rules → Qt dashboard → локальный SQLite/HTML.

**Это прототип, не сертифицированный kiosk/anti-cheat.** Реальная точность на телефонах, 19 сценариев камеры и Windows-блокировки требуют очного прогона. Технические исправления, результаты и границы: [CV_AUDIT.md](docs/CV_AUDIT.md).

## Быстрый запуск

Python **3.10–3.11**. MediaPipe закреплён на версиях с `mp.solutions.face_mesh`; новые версии с другим API не подходят.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate

# Только CPU: сначала поставить CPU-вариант torch, не CUDA-пакеты.
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r proctor/requirements.txt
python -m proctor.tools.fetch_weights
python -m proctor.main --profile dev --no-guard --debug
```

После установки зависимостей и весов CV работает офлайн. По умолчанию `yolo.offline: true`: отсутствующие веса — видимая ошибка, а не загрузка из сети или mock. `test.test_url` по умолчанию указывает на локальную заглушку; удалённый экзаменационный сайт сам по себе офлайн не становится.

```bash
python -m proctor.main --perf weak --no-guard --debug
python -m proctor.main --perf balanced --no-guard --debug
python -m proctor.main --video demo.mp4 --no-guard  # dev, EOF не зацикливается
python -m proctor.tools.set_password
python -m proctor.main --profile exam
```

`exam`: Windows, права администратора, живая камера, включённая защита, пароль и URL теста. `--demo`, `--no-guard`, `--video` в exam запрещены. Аварийный выход: **Ctrl+Shift+F12**, в exam — с паролем экзаменатора. `dev` можно закрыть обычным способом; `--demo` не завершает сторонние процессы.

## Что изменено

- Захват и три worker-потока не создают очередей Qt-сигналов с кадрами. Каждый worker берёт только самый свежий кадр и обрабатывает его один раз. GUI опрашивает результаты примерно в 30 Hz.
- Nano-YOLO видит только `person` / `cell phone`; небольшой двухэтапный IoU/motion tracker стабилизирует ID. Слабые боксы восстанавливают трек, но не создают новый. Прогноз используется только в оверлее, не как доказательство нарушения.
- FaceMesh считает лица отдельно от людей. Исправлены 3D-пары, оси head pose и отрицательная глубина PnP. Левый/правый глаз: собственные iris, локальные оси, раздельное EMA, blink → UNKNOWN.
- 5 поз взгляда за 20 секунд: CENTER/LEFT/RIGHT/UP/DOWN, звук перехода, пропуск переходных кадров, MAD-тримминг, персональные центры и пороги. Голова во время калибровки остаётся прямо. Пустая/неразличимая калибровка не проходит.
- HEAD и GAZE независимы; есть hysteresis, dwell, hold/cooldown, допуск коротких разрывов и отсчёт удержания. Устаревшие результаты и падение модели не превращаются в обвинение ученика.
- Hands запускается редко и только в ROI около подтверждённого телефона. PHONE_IN_HAND — пересечение руки с телефоном; PHONE_LIFTED — устойчивое движение трека вверх; PHONE_RAISED — зона у лица; PHONE_AIMED — положение + удержание, **не доказательство направления объектива**.
- Тёмная панель: превью с подсветкой, четыре тайла, таймер, progress удержания, ограниченная лента событий и debug.
- CPU fallback, ограниченные native thread pools, адаптация imgsz/частоты по измеренному времени worker. Архитектура и типы событий одинаковы для всех профилей.
- Splash, single-instance lock, reconnect при ошибках чтения камеры, детекция зависшего потока кадров. Драйвер, навечно блокирующий `read()`, всё ещё требует отдельного аппаратного теста.
- SQLite WAL + lock + атомарная hash-chain; JPEG/запись событий вынесены из video loop. Буфер обмена работает в GUI-потоке, самопорождаемые изменения игнорируются.
- Навигация блокируется **до загрузки**, новые окна/скачивание запрещены. Исправлен JS bridge; локальная страница не даёт навигацию на произвольный файл. HTTP finish защищён CSRF-токеном; в exam требуется ещё и пароль в приложении.

## Настройки

`proctor/config.yaml`: разрешение камеры, confidence, частоты YOLO/FaceMesh/Hands, таймеры, `performance.mode` (`auto/weak/balanced`), `performance.adaptive`, `performance.threads`.

Auto стартует консервативно по доступным CPU cores, затем worker корректирует частоту по EWMA своего времени. Это **не автоматическая оценка точности** и не гарантия FPS на любом ноутбуке. GPU выбирается только если доступен CUDA; иначе CPU. Число worker-потоков фиксировано. Full-frame FaceMesh сохраняется для второго лица; сокращение до одной face ROI сломало бы это требование.

Старые `vote_window/vote_threshold` оставлены для совместимости конфигурации; теперь доказательства основываются на подтверждении треков и времени, не на FPS-зависимом окне голосования.

## Тесты и измерения

```bash
pip install -r proctor/requirements-dev.txt
python -m pytest -q proctor/tests
python -m proctor.tools.benchmark --seconds 30 --perf weak --output data/benchmark.json
python -m proctor.tools.benchmark --video demo.mp4 --seconds 30 --preview
python -m proctor.tools.replay_evaluator --video labelled.mp4 --gt ground_truth.json --output data/metrics.json --calibration five
python -m proctor.tools.false_positive_run --video honest.mp4 --output data/honest.json --calibration five
```

Replay использует те же processors, что приложение, а не вторую реализацию геометрии. Видео для `five` начинается с тех же 5 поз (20с); `center` — 5с прямо с частичной калибровкой, `none` — без калибровки. Не считать эти два режима проверкой персональных порогов.

Ground truth: `[ {"type":"PHONE_DETECTED", "t_start":25, "t_end":30} ]`. Проверка учитывает также неожиданные типы событий, даже если их нет в GT. Скрипт честного поведения считает реальные предупреждения из видео, а не генерирует «FPR=0» на искусственном нулевом потоке.

Опциональные model smoke: `PROCTOR_MODEL_SMOKE=1`, локальные веса и `PROCTOR_FACE_FIXTURE=/path/to/face.jpg`. Опциональный WebEngine smoke: `PROCTOR_WEB_SMOKE=1`. На Linux без дисплея: `QT_QPA_PLATFORM=offscreen`; sandbox-флаги Chromium из технического CI не переносить в экзаменационный запуск.

Каждый запуск получает отдельную БД в `data/sessions/<run>/`, JPEG и timings JSON; предыдущие экзамены не смешиваются. Последний HTML-отчёт: `data/report.html`. Скриншоты чувствительны: хранить локально, не коммитить и удалять по согласованному сроку.

## Честные границы

- COCO nano-YOLO **не имеет класса face**. Лица берутся из FaceMesh, `person` не заменяет второе лицо.
- «Наведение камеры телефона» — эвристика. Проверка объектива требует других данных/модели; скрытый телефон вне кадра не обнаруживается.
- Eye gaze — грубое направление, не точка на экране. Очки, закрытые глаза, сильный yaw, плохой свет и индивидуальная геометрия требуют проверки.
- На Linux/macOS глобальная защита клавиш/окон не заявляется. В Windows приложение не блокирует Ctrl+Alt+Del/Win+L и не гарантирует все способы screenshot. ProcWatch работает с новыми запрещёнными процессами; уже открытые программы не завершает.
- Проверка виртуальной камеры по имени не доказывает отсутствие подмены; модель не проверяет живость/личность ученика. Имя неизвестно — результат проверки не является доказательством.
- Метрики latency начинаются после `read()`, не после экспозиции сенсора. Hold warnings намеренно задерживаются на время правила. FPS превью ≠ FPS YOLO.
- Ultralytics имеет лицензионные условия; перед публичным распространением проверить подходящую лицензию.
