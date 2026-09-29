from __future__ import annotations

from datetime import date, time
from decimal import Decimal


def build_demo_data() -> dict[str, list[dict]]:
    """Small deterministic dataset equivalent to the colleague's SQL demo fixture."""
    as_of = date(2026, 9, 28)
    clients = [
        {"client_id": i, "client_label": f"Клиент {i:02d}", "timezone_name": "Europe/Moscow",
         "early_late_collection": i in {3, 12}, "no_contact": i == 6, "active": True}
        for i in range(1, 13)
    ]
    loans_raw = [
        (101,1,"MORTGAGE",4200000,78000,12),(102,1,"CARD",90000,12000,5),
        (103,2,"CONSUMER",350000,24000,8),(104,3,"AUTO",900000,93000,35),
        (105,4,"CARD",140000,14000,3),(106,4,"CONSUMER",500000,0,0),
        (107,5,"MORTGAGE",2800000,41000,6),(108,6,"CARD",85000,10000,18),
        (109,7,"AUTO",1100000,53000,9),(110,7,"CARD",45000,5000,2),
        (111,8,"CONSUMER",210000,12000,4),(112,9,"MORTGAGE",3600000,82000,21),
        (113,9,"AUTO",600000,0,0),(114,10,"CARD",70000,16000,11),
        (115,11,"CONSUMER",470000,37000,14),(116,12,"AUTO",780000,69000,29),
    ]
    loans = [
        {"loan_id": loan_id, "client_id": client_id, "product_code": product,
         "balance_rub": balance, "overdue_rub": overdue, "dpd": dpd,
         "is_active": True, "as_of_date": as_of}
        for loan_id, client_id, product, balance, overdue, dpd in loans_raw
    ]
    score_pairs = [
        (1,.21,.18),(2,.88,.86),(3,.12,.13),(4,.44,.62),(5,.83,.77),(6,.20,.33),
        (7,.30,.27),(8,.57,.51),(9,.17,.31),(10,.79,.72),(11,.38,.40),(12,.11,.12),
    ]
    scores = [
        {"client_id": cid, "as_of_date": as_of,
         "self_cure_score": Decimal(str(sc)), "repayment_score": Decimal(str(rp))}
        for cid, sc, rp in score_pairs
    ]
    channels = [
        ("CALL", "DIRECT"), ("ROBOT", "DIRECT"), ("SMS", "MESSAGE"),
        ("PUSH", "MESSAGE"), ("EMAIL", "MESSAGE"), ("VOICE", "MESSAGE"),
    ]
    channel_rows = [
        {"channel_code": code, "channel_label": code, "legal_bucket": bucket, "is_enabled": True}
        for code, bucket in channels
    ]
    priorities = []
    order = ["CALL", "ROBOT", "SMS", "PUSH", "EMAIL", "VOICE"]
    for cid in range(1, 13):
        for base_priority, channel in enumerate(order, start=1):
            priorities.append({
                "client_id": cid,
                "channel_code": channel,
                "priority_no": ((base_priority + cid - 2) % 6) + 1,
                "is_allowed": True,
            })
    strategies = [
        {"strategy_id": 1, "strategy_code": "INTENSIVE", "is_enabled": True},
        {"strategy_id": 2, "strategy_code": "STANDARD", "is_enabled": True},
        {"strategy_id": 3, "strategy_code": "LIGHT", "is_enabled": True},
    ]
    slots_raw = [
        (1,1,1,"ROBOT"),(1,2,1,"SMS"),(1,3,1,"ROBOT"),(1,4,1,"SMS"),(1,5,1,"PUSH"),
        (2,1,1,"SMS"),(2,3,1,"ROBOT"),(2,5,1,"EMAIL"),
        (3,2,1,"SMS"),(3,5,1,"PUSH"),
    ]
    slots = [
        {"strategy_id": sid, "day_of_week": dow, "slot_no": slot_no,
         "channel_code": channel, "desired_local_time": time(12, 0)}
        for sid, dow, slot_no, channel in slots_raw
    ]
    policy = [{
        "policy_id": 1,
        "self_cure_no_touch_min": Decimal("0.8000"),
        "intensive_repayment_max": Decimal("0.3500"),
        "light_repayment_min": Decimal("0.7000"),
    }]
    limits = [
        {"legal_bucket": "DIRECT", "max_per_day": 1, "max_per_calendar_week": 2,
         "max_per_calendar_month": 8, "workday_start": time(8), "workday_end": time(22),
         "holiday_start": time(9), "holiday_end": time(20), "valid_from": date(2026,1,1), "valid_to": None},
        {"legal_bucket": "MESSAGE", "max_per_day": 2, "max_per_calendar_week": 4,
         "max_per_calendar_month": 16, "workday_start": time(8), "workday_end": time(22),
         "holiday_start": time(9), "holiday_end": time(20), "valid_from": date(2026,1,1), "valid_to": None},
    ]
    return {
        "clients": clients,
        "loans": loans,
        "scores": scores,
        "priorities": priorities,
        "channels": channel_rows,
        "slots": slots,
        "strategies": strategies,
        "policy": policy,
        "limits": limits,
        "holidays": [],
    }
