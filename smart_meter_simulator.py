"""AUCA Assignment I common data generator, version 3.0.

Import this module from your own Python program. It has no command-line interface.
Running the file directly produces no output.

Public interface
  dataset_info()       Return the fixed period, scale, fields and expected counts.
  meter_metadata()     Return 4,000 meter metadata dictionaries.
  iter_readings()      Yield reading dictionaries one at a time, including duplicates.
  anomaly_events()     Return the injected abnormal-event intervals for evaluation.

Every new call to iter_readings() restarts the same complete dataset. Consume the
iterator progressively. Do not collect millions of records into a single list.
For the pipeline pilot, consume only its first 2,000 records in your own program.

This module only creates data in memory. Your program must capture records, count
them, report progress, serialize them, write files, publish MQTT messages and/or
insert into a database. This module performs none of those operations.

Fields
  meter_id       Ten-digit integer, starting at 1000000000.
  event_time     Start of the 15-minute measurement interval, as a CAT ISO string.
  received_time  Simulated arrival, as a CAT ISO string, not the actual load time.
  power_kw       Average power over the interval.
  voltage_v      Voltage in volts.
  current_a      Current in amperes, using a power factor of 0.95.
  frequency_hz   Frequency in hertz.
  energy_kwh     Energy used during the interval, equal to power_kw * 0.25
                 within rounding tolerance. This is not a cumulative register.

All groups use this file unchanged. No seed or data settings are required.
The generator uses only the Python standard library.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Iterator

VERSION = "3.0"
CAT = timezone(timedelta(hours=2), "CAT")
_START = datetime(2026, 9, 1, tzinfo=CAT)
_METERS = 4000
_DAYS = 28
_SLOTS_PER_DAY = 96
_FIXED_STATE = 20261001
_FIELDS = ("meter_id", "event_time", "received_time", "power_kw", "voltage_v",
           "current_a", "frequency_hz", "energy_kwh")
_EXPECTED_COUNTS = {
    "emitted_readings": 10696848,
    "unique_readings": 10643735,
    "duplicate_readings": 53113,
    "delayed_unique_readings": 213090,
    "missing_readings": 108265,
}


def _cat_text(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def dataset_info() -> dict:
    """Return the fixed data contract and counts for the complete stream."""
    return {
        "generator_version": VERSION,
        "meters": _METERS,
        "days": _DAYS,
        "minutes_between_readings": 15,
        "timezone": "CAT",
        "start_inclusive": _cat_text(_START),
        "end_exclusive": _cat_text(_START + timedelta(days=_DAYS)),
        "planned_readings": _METERS * _DAYS * _SLOTS_PER_DAY,
        "reading_fields": list(_FIELDS),
        "expected_counts": dict(_EXPECTED_COUNTS),
    }


def meter_metadata() -> list[dict]:
    """Return fresh metadata objects for the same 4,000 meters on every call."""
    rng = random.Random(_FIXED_STATE)
    result = []
    for index in range(_METERS):
        draw = rng.random()
        if draw < 0.65:
            kind, low, high = "residential", 0.3, 1.4
        elif draw < 0.90:
            kind, low, high = "commercial", 1.5, 5.0
        else:
            kind, low, high = "industrial", 5.0, 15.0
        result.append({
            "meter_id": 1000000000 + index,
            "region": f"Region {index % 10 + 1}",
            "customer_type": kind,
            "base_power_kw": round(low + (high - low) * rng.random(), 6),
            "power_factor": 0.95,
        })
    return result


def _anomaly_specs() -> list[tuple]:
    rng = random.Random(_FIXED_STATE + 1)
    selected = set()
    specs = []
    while len(selected) < 40:
        index = int(rng.random() * _METERS)
        if index in selected:
            continue
        selected.add(index)
        day = int(rng.random() * _DAYS)
        hour = 6 + int(rng.random() * 13)
        first = day * _SLOTS_PER_DAY + hour * 4
        spike = len(specs) % 2 == 0
        specs.append((index, first, first + 12,
                      "demand_spike" if spike else "unusual_drop",
                      2.8 if spike else 0.03))
    return specs


def anomaly_events() -> list[dict]:
    """Return the 40 labeled intervals for evaluating anomaly detection only."""
    return [{
        "meter_id": 1000000000 + index,
        "start_time": _cat_text(_START + timedelta(minutes=15 * first)),
        "end_time": _cat_text(_START + timedelta(minutes=15 * last)),
        "event_type": kind,
    } for index, first, last, kind, _ in _anomaly_specs()]


def iter_readings() -> Iterator[dict]:
    """Yield the common historical stream without file, terminal or network I/O.

    Normal arrivals occur at the interval end. About 1% of planned readings are
    omitted, 0.5% extra duplicate copies are added among transmitted readings,
    and 2% of unique transmitted readings arrive 30 to 180 minutes after event_time.
    Records are yielded by event time, not by arrival time. For a delay experiment,
    your consumer must replay a bounded sample in received_time order.
    """
    meters = meter_metadata()
    quality = random.Random(_FIXED_STATE + 2)
    values = random.Random(_FIXED_STATE + 3)
    anomalies = {index: (first, last, factor)
                 for index, first, last, _, factor in _anomaly_specs()}

    for slot in range(_DAYS * _SLOTS_PER_DAY):
        stamp = _START + timedelta(minutes=15 * slot)
        stamp_text = _cat_text(stamp)
        normal_arrival = _cat_text(stamp + timedelta(minutes=15))
        hour = stamp.hour + stamp.minute / 60
        morning = math.exp(-0.5 * ((hour - 7.5) / 1.6) ** 2)
        evening = math.exp(-0.5 * ((hour - 19.0) / 2.0) ** 2)
        business = math.exp(-0.5 * ((hour - 13.0) / 3.0) ** 2)
        shapes = {
            "residential": 0.22 + 0.65 * morning + 0.95 * evening,
            "commercial": (0.12 + 0.95 * business) * (0.55 if stamp.weekday() >= 5 else 1.0),
            "industrial": 0.85 if stamp.weekday() < 5 else 0.68,
        }
        trend = 1.0 + 0.003 * (slot // _SLOTS_PER_DAY)

        for index, meter in enumerate(meters):
            if quality.random() < 0.01:
                continue
            delayed = quality.random() < 0.02
            duplicate = quality.random() < 0.005
            arrival = normal_arrival
            if delayed:
                delay = 30 + int(quality.random() * 151)
                arrival = _cat_text(stamp + timedelta(minutes=delay))

            power = meter["base_power_kw"] * shapes[meter["customer_type"]]
            power *= trend * (0.85 + 0.30 * values.random())
            event = anomalies.get(index)
            if event and event[0] <= slot < event[1]:
                power *= event[2]
            voltage = 226.0 + 8.0 * values.random()
            frequency = 49.95 + 0.10 * values.random()
            record = {
                "meter_id": meter["meter_id"],
                "event_time": stamp_text,
                "received_time": arrival,
                "power_kw": round(power, 6),
                "voltage_v": round(voltage, 3),
                "current_a": round(power * 1000 / (voltage * 0.95), 6),
                "frequency_hz": round(frequency, 4),
                "energy_kwh": round(power * 0.25, 8),
            }
            yield record.copy()
            if duplicate:
                yield record.copy()
