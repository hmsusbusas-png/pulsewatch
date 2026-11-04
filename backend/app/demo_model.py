import math
import random
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL_ZONE = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True)
class Shape:
    base: float
    daily: float
    weekend: float
    noise: float
    spike_rate: float
    spike_scale: float
    peak_hour: float
    tls: bool = True


SHAPES = {
    "api": Shape(115, 0.18, 0.08, 0.07, 0.002, 6, 14),
    "shop": Shape(80, 4.0, 0.35, 0.12, 0.004, 4, 20.5),
    "payments": Shape(190, 0.7, 0.15, 0.10, 0.003, 5, 13),
    "database": Shape(2.4, 0.5, 0.2, 0.15, 0.002, 8, 14, tls=False),
}


def latency(shape: Shape, moment: datetime, rng: random.Random, factor: float = 1.0) -> int:
    local = moment.astimezone(LOCAL_ZONE)
    hour = local.hour + local.minute / 60
    wave = 0.5 * (1 + math.cos(2 * math.pi * (hour - shape.peak_hour) / 24))
    weekend = 1 - shape.weekend if local.weekday() >= 5 else 1.0
    value = shape.base * (1 + shape.daily * wave * weekend) * math.exp(rng.gauss(0, shape.noise))
    if rng.random() < shape.spike_rate:
        value *= rng.uniform(2.5, shape.spike_scale)
    return max(1, round(value * factor))


def phases(total: int, rng: random.Random, tls: bool) -> tuple[int, int, int | None, int, int]:
    dns = rng.randint(0, 3)
    connect = max(1, round(total * 0.03) + rng.randint(0, 2))
    handshake = round(total * 0.1) + rng.randint(4, 10) if tls else None
    transfer = max(1, round(total * 0.05) + rng.randint(0, 3))
    ttfb = max(1, total - dns - connect - (handshake or 0) - transfer)
    return dns, connect, handshake, ttfb, transfer
