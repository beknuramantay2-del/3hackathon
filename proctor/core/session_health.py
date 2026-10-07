def cv_health(camera, workers, packet, now):
    errors = []
    if (
        camera.status != "ready"
        or packet is None
        or not 0 <= now - packet.captured_at <= 1.0
    ):
        errors.append("Нет свежего кадра камеры")
    for name, worker in workers:
        result = worker.output.peek()
        if (
            worker.status != "ready"
            or result is None
            or result.seq < 0
            or result.error
            or not 0 <= now - result.captured_at <= 0.7
        ):
            errors.append(name + ": нет свежего результата")
    return not errors, errors
