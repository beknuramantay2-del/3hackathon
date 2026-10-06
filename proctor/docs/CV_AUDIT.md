# Технический аудит CV / Кейс №3

## Итог и статус

Проблемный pipeline переработан без тяжёлого ReID, сервисной архитектуры или дополнительных GPU-моделей. Исправления интегрированы в приложение, а не оставлены в отдельно работающем demo-скрипте.

**Статус: программные проверки пройдены; приёмка на реальной камере и Windows не пройдена в этой среде.** Нельзя честно заявить, что все пункты кейса закрыты по качеству и защите: здесь нет пользовательской камеры, телефонов, размеченного набора 19 сценариев, Windows или CUDA GPU.

Основание требований: предоставленный PDF «Кейс №3 КРУ и Qostanai Hub рус.pdf», разделы 2.1–2.3, и расширенный список пользователя. Не добавлена скрытая передача кадров в облако.

## Найденные дефекты

| Область | Причина / риск | Исправление |
|---|---|---|
| Старт | main обращался к `store.shots` и `guard.emergency_exit`, которых нет в YAML | точные `screenshots` / `exit_combo`, отдельная БД сессии |
| Транспорт | frame-сигналы Qt могли накапливаться; worker повторно брал тот же numpy frame | latest slots + seq + monotonic timestamp + свежесть; GUI polling |
| Захват | sleep поверх read снижал live FPS; EOF закручивал пустое видео | live delivery limit, точная pacing replay, EOF/ошибки отдельно, reconnect |
| YOLO | все классы, жёсткие 8Hz, отсутствие tracker, silent mock при ошибке | классы 0/67, nano, explicit error, bounded tracker, measured budgets |
| Tracking | нет ID/confirmation/восстановления слабых боксов | два этапа association, motion gating, TTL, короткий прогноз только для оверлея |
| PnP | chin/eyes/mouth сопоставлены с чужими 3D-точками; оси yaw/pitch/roll перепутаны | канонический порядок, SQPnP + LM, положительная глубина, reprojection gate, корректные оси |
| PnP runtime | ITERATIVE выбирал заднее решение даже на публичном фото | воспроизведено; SQPnP выбирает переднее решение; native smoke проверяет valid pose |
| Iris | один общий midpoint двух радужек использовался для обоих глаз | iris 468/473, собственная система координат каждого глаза, раздельное сглаживание |
| Blink | ошибка измерения представлялась идеальным CENTER | UNKNOWN + quality gate, один валидный глаз допускается, два несовместимых отклоняются |
| HEAD/GAZE | условия OR смешивали поворот головы с движением глаз | независимые state machines и отдельные HEAD_* / GAZE_* события |
| Temporal | start=0 считался отсутствием таймера; длинный gap забывался при возвращении True | явная None-проверка, reset также на возврате после gap, cooldown |
| Phone aimed | удержание предварительно проверялось и потом отсчитывалось ещё раз | ровно один hold; описание как эвристики, отдельная фиксация подъёма по скорости track |
| Presence | YOLO person>=2 выдавал MULTI_FACE; без face любой phone считался raised | лица считаются только FaceMesh; без лица зона подъёма неизвестна |
| Calibration | пустые данные давали успешные нули; всё считалось одной центральной позой | 5 целей, trimmed median/MAD, переходы исключены, противоположность целей проверяется |
| UI | debug/hold/log были no-op; preview обновлялся только при FaceMesh результате | отдельный video clock, тайлы, feed<=100, hold/countdown, актуальные боксы, читаемый mirror |
| SQLite | один thread-bound connection + синхронный JPEG в UI, цепочка вне транзакции | WAL/RLock/BEGIN IMMEDIATE, bounded event writer, atomic hash и insertion order |
| Clipboard | Qt clipboard вызывался из QThread; clear вызывал новый clear и событие | QObject/QTimer GUI thread, recursion guard, empty check, throttle |
| Hotkeys | bare F12 конфликтовал с Ctrl+Shift+F12, hook failure маскировался | терминальная клавиша выхода не подавляется; status/errors явно; exam не начинается при ошибке hooks |
| WebEngine | поздний import без shared contexts; отсутствующая QWebChannel JS библиотека | ранняя настройка Qt, загрузка qrc bridge, проверка finish callback, lifecycle pages/profile |
| Web security | навигация проверялась после загрузки; локальный mode разрешал любой file:// | acceptNavigationRequest + request interceptor, один файл или allowlisted hosts, no popup/download |
| Reports / HTTP | незакрытая table, HTML-инъекция ФИО, cross-site finish | закрытая таблица, escaping, CSRF + exam password gate |
| Replay | своя ошибочная геометрия, на пропущенных YOLO кадрах терялись телефоны, неожиданные типы игнорировались | production processors и retained snapshot; union GT/detection types |
| FPR tool | «честное поведение» было синтетическими идеальными значениями | реальное входное видео обязательно; никакого нулевого FPR без измерения |

## Лёгкая архитектура

- Один процесс; capture QThread, YOLO/FaceMesh/Hands QThreads, GUI thread, небольшой SQLite writer.
- Кадры не идут через queued Qt signals. Каждый input хранит один FramePacket; старые и уже обработанные seq пропускаются.
- Результаты содержат исходный seq/capture time. Gaze/phone evidence истекает; ошибка камеры/модели не интерпретируется как намеренное отсутствие ученика.
- `person` нужен для оверлея, не для второго лица. Основной face выбирается по overlap с предыдущим; это стабилизация ROI, **не биометрическая идентификация**.
- Full-frame FaceMesh необходим для второго лица; его внутренний tracking и ограниченный rate дешевле постоянных дополнительных face-моделей. Сглаживаются нужные выходы (pose/eyes/box), не все 478 landmarks.
- Hands — model_complexity=0, до двух рук, около phone ROI, низкая частота. Нет телефона — нет hands inference.
- Консервативный CPU startup, ограниченные torch/OpenCV pools; адаптация target rate/imgsz по EWMA. Число CPU cores — стартовая подсказка, не замена измерению.
- Короткий box prediction компенсирует отображение между YOLO кадрами; прогноз не участвует в предупреждениях.
- SQLite/JPEG не блокируют активный video loop. Очередь writer ограничена 64; переполнение/ошибка показываются явно, а не замалчиваются.

## Проверки

Финальный локальный прогон: **102 passed** с opt-in model/WebEngine smoke; обычный набор: **98 passed, 4 skipped**. Native smoke — техническая проверка компонентов, не оценка точности прокторинга.

- Unit/regression: tracking ID/weak association/TTL/motion, 3D-проекции осей включая roll, отдельные глаза и blink, calibration trim/empty/opposite labels, HOLD gap/zero origin/cooldown, отдельные HEAD/GAZE, отсутствие/второе лицо, freshness, independent phone-in-hand/lift rules, кадры без дублей, camera EOF/pacing, многопоточная SQLite/hash-chain, clipboard storm, navigation policy, CSRF/HTML.
- Нативные проверки: локальная YOLOv8n, FaceMesh на пустом кадре и публичном статическом portrait, gated Hands ROI на пустом изображении, QtWebEngine bridge/finish.
- Проверен запуск main с реальными workers на video source и штатное завершение. Визуально проверен дашборд с тестовыми значениями: без обрезки, отдельные тайлы, hold/feed/debug.
- GitHub Actions добавлен для обычных regression tests. Opt-in нативные smoke требуют заранее установленных моделей/локального portrait; CI по умолчанию их пропускает.
- **Не выполнялись** живые попытки cheating, измерение precision/recall на размеченных сценах, GPU bench, Windows hotkey suppression, лэптоп-специфические проверки света/камеры.

## Измерения: не рекламные цифры

Сырые результаты: [weak](benchmarks/synthetic-static-face-weak.json), [balanced](benchmarks/synthetic-static-face-balanced.json).

Оба прогона — **одна Linux-среда с 2 CPU, CPU torch, без GPU**, по 15 секунд после готовности моделей. Источник — публичное статическое фото OpenCV, искусственно закодированное в 640×480 / 30fps. Это проверка работоспособности и стоимости стадий, **не реальная камера и не датасет качества**. Повторение одного фото не проверяет движения/телефон/свет. Python 3.11, MediaPipe 0.10.21, OpenCV headless 4.11.0.86, torch CPU 2.14.1, Ultralytics 8.4.173, Qt 6.11.

Warmup исключён из throughput; capture pacing исправлен без усечения миллисекунд. `camera_fps = produced_frames / measured_seconds`. Превью и CV имеют разные частоты. CPU% относится к основному процессу и может превышать 100% на многопроцессорной системе; RSS не включает отдельные WebEngine процессы. GPU и нагрузка реальной экзаменационной страницы не измерены.

Профиль `weak`: capture 29.93 FPS; YOLO 5.93 Hz; FaceMesh 11.86 Hz; CPU process 34.3%; RSS peak 622.9 MB.

| Стадия (weak smoke) | N | Mean, ms | P95, ms |
|---|---:|---:|---:|
| camera_read | 448 | 0.399 | 0.547 |
| yolo | 89 | 29.932 | 34.965 |
| postprocess | 89 | 0.062 | 0.072 |
| tracker | 89 | 0.012 | 0.015 |
| mediapipe | 178 | 6.756 | 7.969 |
| head_pose | 178 | 0.452 | 0.627 |
| eye_gaze | 178 | 0.165 | 0.208 |
| logic | 2829 | 0.043 | 0.059 |
| ui_render | 448 | 0.870 | 1.049 |
| preview_latency | 448 | 3.287 | 5.657 |
| decision_data_age | 2794 | 125.338 | 196.984 |

Наблюдаемый bottleneck в этом ограниченном smoke — YOLO, не tracker/logic/UI. **Нет обещания общей CV latency 35ms**: данные решения обновляются дискретно, возраст самого медленного актуального результата больше времени одного inference. Превью latency отсчитывается после camera read и не включает задержку сенсора/драйвера. Значения параллельных стадий нельзя просто сложить и назвать TOTAL. Hold warning добавляет намеренную задержку согласно конфигу.

Повторить на целевом устройстве:

```bash
python -m proctor.tools.benchmark --seconds 60 --perf weak --preview --output data/weak-real.json
python -m proctor.tools.benchmark --seconds 60 --perf balanced --preview --output data/balanced-real.json
```

Для воспроизведения только статического smoke: взять публичный portrait локально, `python -m proctor.tools.make_smoke_video --image portrait.jpg --output synthetic_static_face.mp4`, затем benchmark с `--video`. Это не приёмка.

## Соответствие функциональным требованиям PDF

| Требование | Реализация | Статус приёмки |
|---|---|---|
| Смартфон в кадре/перед экраном | nano-YOLO + confirmed tracks + hold | есть код и smoke без телефона; нужны реальные положительные/отрицательные сцены |
| Телефон в руке | gated Hands ROI + phone overlap + temporal hold | логика/ROI smoke проверены; нужна сцена с настоящей рукой/телефоном |
| Поднятие для фото монитора | PHONE_LIFTED по движению track; PHONE_RAISED по face-relative зоне | логика проверена; намерение фотографировать не доказывается |
| Наведение камеры телефона | PHONE_AIMED = zone + hold | только эвристика; фактическая ориентация объектива не проверяется |
| Направление глаз | независимые per-eye normalized iris + personalised targets + hysteresis | геометрия/состояния/native mesh проверены; точность всех направлений требует разметки |
| Положение головы | корректные PnP axes, smoothing, quality gates, separate state | synthetic rotations + native face smoke проверены; реальные движения не приняты |
| Долгий отвод вниз/в стороны | HEAD_* и GAZE_* таймеры отдельно | детерминированные tests пройдены; live FPR/recall не измерены |
| Присутствие / второе лицо | свежие FaceMesh results + holds | logic tests; реальные люди/ракурсы/свет не приняты |
| Alt+Tab, Win, Ctrl+C/V, PrtScn | Windows keyboard hooks, focus watch, clipboard GUI guard | clipboard проверен; глобальные hooks не проверены на Windows |
| Вкладки/чужие страницы | no popup, pre-navigation gate, request allowlist, focus events | policy и bridge tests; внешний экзаменационный сайт требует проверки CSP/domains |
| Сторонние окна/браузеры | focus return + watch новых запрещённых процессов | не является OS kiosk; существующие программы и привилегированные обходы остаются ограничением |

COCO phone модель не обучена на лица. Лица из FaceMesh удовлетворяют функции отображения/подсчёта, но буквальное требование YOLO-face не реализовано отдельными весами: нет face dataset/weights и необходимости добавлять ещё одну модель для MVP.

## Матрица 19 живых сценариев

В этой среде **все 19 требуют очного прогона**. Колонка тестов описывает только покрытие логики; она не означает, что сценарий принят физически.

| № | Сценарий | Программное покрытие / критерий живой приёмки |
|---|---|---|
| 1 | Прямо | neutral/hysteresis; 10 мин честного поведения, считать предупреждения |
| 2 | Влево | независимый gaze LEFT; после калибровки глаз без головы |
| 3 | Вправо | независимый gaze RIGHT; не инвертировать при mirror |
| 4 | Вверх | iris vertical / state UP; blink не считать взглядом |
| 5 | Вниз | DOWN + hold; краткий/долгий отвод раздельно |
| 6 | Только голова | projected PnP axes / independent state; iris не подменять yaw |
| 7 | Только глаза | per-eye geometry/state; HEAD остаётся CENTER |
| 8 | Голова и глаза | separate state tests; проверить разнонаправленные комбинации |
| 9 | Быстрое движение головы | EMA reset/quality; измерить loss/reacquire и latency на видео |
| 10 | Короткий отвод | single-frame/gap/dwell tests; без предупреждения до hold |
| 11 | Долгий отвод | hold/cooldown/reset; duration и event в пределах допусков |
| 12 | Поднести телефон | track confirmation/TTL; recall на нескольких телефонах/чехлах |
| 13 | Поднять к экрану | motion + zone/aimed tests; измерить задержку и отдельно обозначить эвристику |
| 14 | Убрать телефон | stale/TTL/prediction-only; warning не продлевается по прогнозу |
| 15 | Второй человек | FaceMesh count+hold; person!=face, краткий FP без event |
| 16 | Ученик выходит | NO_FACE hold; camera error не обвиняет в отсутствии |
| 17 | Плохой свет | brightness/quality; нужны реальные lux/occlusion/очки, не synthetic pixels |
| 18 | Слабый ноутбук | adaptive budget и CPU smoke; 60с живой камеры на реальном устройстве |
| 19 | Мощный ноутбук | balanced настройки; GPU/CPU проверяются отдельно на реальном устройстве |

Для каждого живого прогона фиксировать разрешение, модель/imgsz, inference Hz, свет/дистанцию, железо, число размеченных попыток, TP/FP/FN по типам, p50/p95 data age, restart/lost frames и RSS. Не публиковать один FPS без условий и accuracy/FPR.

## Оставшиеся риски / критерий готовности

1. Нужен размеченный набор позитивных/негативных phone/eyes/head/second-face сцен. Синтетические tests не доказывают precision/recall.
2. Не обеспечены биометрическая идентификация, anti-spoofing или гарантированная ориентация камеры смартфона. Это не добавляется «для галочки» в UI.
3. Webcam-драйвер может навечно блокировать native read. UI заметит устаревшие кадры, но гарантированное принудительное восстановление/выход потребовало бы изоляции capture в процессе; для лёгкого MVP это оставлено как явно заявленный риск.
4. Windows hooks/фокус/новые процессы требуют очной проверки. Привилегированные OS shortcuts, screenshot tools, уже открытые приложения и подмена локального кода выходят за гарантию application-level MVP.
5. Ошибка writer/переполнение показывается и сохраняемый отчёт требует проверки; нельзя считать потерянные записи «нулём нарушений».
6. После живой приёмки подобрать thresholds и adaptive budgets по данным. Без этих данных нельзя объявить систему «точной на любом слабом ноутбуке».

**Готовность к Demo Day подтверждать только после заполнения реальными результатами этой матрицы, replay/FPR и Windows check.**
