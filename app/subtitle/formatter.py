from datetime import timedelta


def seconds_to_srt_timestamp(seconds: float) -> str:
    td = timedelta(seconds=max(0.0, seconds))
    total_sec = int(td.total_seconds())
    ms = int(td.microseconds / 1000)
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    return f"{h:02}:{m:02}:{s:02},{ms:03}"
