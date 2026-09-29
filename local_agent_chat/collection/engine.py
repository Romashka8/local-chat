"""Deterministic collection assignment engine adapted from the colleague demo."""

import html
import math
from collections import Counter, defaultdict
from datetime import timedelta


def clamp(value):
    return round(min(1.0, max(0.0, value)), 4)


def scores(base, day_no, client_id):
    """Воспроизводимые учебные ежедневные скоры, не результат ML-модели."""
    return (
        clamp(float(base['self_cure_score']) + 0.06 * math.sin(day_no / 3 + client_id)),
        clamp(float(base['repayment_score']) + 0.08 * math.sin(day_no / 4 + client_id * 0.7)),
    )


def route(client, loans, self_cure, repayment, policy):
    if client['no_contact']:
        return 'NO_CONTACT'
    if client['early_late_collection']:
        return 'EARLY_LATE'
    if not loans:
        return 'NO_OVERDUE'
    if self_cure >= float(policy['self_cure_no_touch_min']):
        return 'SELF_CURE'
    if repayment <= float(policy['intensive_repayment_max']):
        return 'INTENSIVE'
    if repayment >= float(policy['light_repayment_min']):
        return 'LIGHT'
    return 'STANDARD'


def parse_intensity(items, defaults, enabled):
    schemes = {k: Counter(v) for k, v in defaults.items()}
    for item in items:
        try:
            route_name, specification = item.split('=', 1)
            counts = Counter()
            specification = specification.strip()
            if specification:
                for part in specification.split(','):
                    channel, count = part.split(':', 1)
                    channel = channel.strip().upper()
                    if channel not in enabled or int(count) < 0:
                        raise ValueError()
                    counts[channel] += int(count)
            route_name = route_name.strip().upper()
            if route_name not in schemes or sum(counts.values()) > 5:
                raise ValueError()
            schemes[route_name] = counts
        except (ValueError, AttributeError) as exc:
            raise ValueError('Формат: INTENSIVE=ROBOT:2,SMS:2,PUSH:1; максимум 5 контактов') from exc
    return schemes


def distribute(scheme, priorities):
    """Расставляет недельные контакты по будним дням, приоритет задаёт порядок."""
    channels = sorted(scheme.elements(), key=lambda c: (priorities.get(c, 999), c))
    n = len(channels)
    return {((2 * i + 1) * 5 // (2 * n)): channel for i, channel in enumerate(channels)} if n else {}


def business_days_since(start, current, holidays):
    return sum((start + timedelta(days=i)).weekday() < 5 and
               (start + timedelta(days=i)) not in holidays
               for i in range((current - start).days + 1)) - 1


def simulate(data, start, days, intensity, text_only=False):
    if len(data['policy']) != 1:
        raise ValueError('Ожидается одна политика маршрутизации с policy_id=1')
    policy = data['policy'][0]
    base_scores = {r['client_id']: r for r in data['scores'] if r['as_of_date'] == start}
    if len(base_scores) != len(data['clients']):
        raise ValueError(f'Для даты {start} нужны исходные скоры каждого клиента')
    loans_by_client = defaultdict(list)
    for loan in data['loans']:
        if loan['as_of_date'] == start:
            loans_by_client[loan['client_id']].append(loan)
    names = {r['strategy_id']: r['strategy_code'] for r in data['strategies']}
    schemes = defaultdict(list)
    for slot in sorted(data['slots'], key=lambda r: (r['day_of_week'], r['slot_no'])):
        if slot['strategy_id'] in names:
            schemes[names[slot['strategy_id']]].append(slot['channel_code'])
    enabled = {r['channel_code']: r['legal_bucket'] for r in data['channels']}
    schemes = parse_intensity(intensity, schemes, enabled)
    if text_only:
        message_channels = {'SMS', 'PUSH', 'EMAIL'}
        for name, scheme in list(schemes.items()):
            schemes[name] = Counter({channel: count for channel, count in scheme.items()
                                     if channel in message_channels and enabled.get(channel) == 'MESSAGE'})
    priority = defaultdict(dict)
    for row in data['priorities']:
        if row['is_allowed']:
            priority[row['client_id']][row['channel_code']] = row['priority_no']
    limits = {r['legal_bucket']: r for r in data['limits'] if r['valid_from'] <= start
              and (r['valid_to'] is None or r['valid_to'] >= start + timedelta(days=days - 1))}
    if set(enabled.values()) - limits.keys():
        raise ValueError('Для канала нет действующего лимита на весь период')
    holidays = {r['holiday_date'] for r in data['holidays']}
    events = defaultdict(list)
    enrollment = {}
    rows = []
    # Один вымышленный пример: клиент 1 обещал оплатить на 7-й день симуляции.
    promise_client, promise_until = 1, start + timedelta(days=6)
    for day_no in range(days):
        current = start + timedelta(days=day_no)
        for client in sorted(data['clients'], key=lambda r: r['client_id']):
            cid = client['client_id']
            sc, rp = scores(base_scores[cid], day_no, cid)
            eligible = [loan for loan in loans_by_client[cid]
                        if loan['dpd'] > 0 and loan['overdue_rub'] > 0
                        and loan['dpd'] + day_no >= 3]
            selected = max(eligible, key=lambda l: (l['overdue_rub'], l['dpd'], -l['loan_id']), default=None)
            rt = route(client, eligible, sc, rp, policy)
            detail = {'date': current, 'client': cid, 'loan': selected['loan_id'] if selected else '',
                      'route': rt, 'self_cure': sc, 'repayment': rp, 'channel': '',
                      'status': 'SKIPPED', 'reason': '', 'primary': True}
            if rt not in schemes:
                detail['reason'] = rt
            elif current.weekday() >= 5 or current in holidays:
                detail['reason'] = 'Выходной/праздник'
            elif cid == promise_client and start + timedelta(days=3) <= current <= promise_until:
                detail['reason'] = 'Обещание платежа до ' + str(promise_until)
            elif not selected:
                detail['reason'] = 'Нет договора с DPD ≥ 3'
            else:
                state = (rt, selected['loan_id'])
                if cid not in enrollment or enrollment[cid][0] != state:
                    enrollment[cid] = (state, current)
                anchor = enrollment[cid][1]
                index = business_days_since(anchor, current, holidays) % 5
                channel = distribute(schemes[rt], priority[cid]).get(index)
                if channel is None:
                    detail['reason'] = 'Нет слота в схеме'
                elif channel not in priority[cid]:
                    detail['reason'] = 'Канал запрещён клиенту'
                else:
                    detail['channel'] = channel
                    bucket = enabled[channel]
                    rule = limits[bucket]
                    past = events[cid]
                    week = current.isocalendar()[:2]
                    counts = (
                        sum(d == current and b == bucket for d, b in past),
                        sum(d.isocalendar()[:2] == week and b == bucket for d, b in past),
                        sum(d.year == current.year and d.month == current.month and b == bucket for d, b in past),
                    )
                    caps = (rule['max_per_day'], rule['max_per_calendar_week'], rule['max_per_calendar_month'])
                    exceeded = [label for count, cap, label in zip(counts, caps, ('сутки', 'неделя', 'месяц')) if count >= cap]
                    if exceeded:
                        detail['status'] = 'BLOCKED'
                        detail['reason'] = f"Лимит {bucket}: {', '.join(exceeded)}"
                    elif any(d == current for d, _ in past):
                        detail['status'] = 'BLOCKED'
                        detail['reason'] = 'У клиента уже есть контакт сегодня по другому договору'
                    else:
                        detail['status'] = 'ASSIGNED'
                        detail['reason'] = 'Состоявшийся контакт (допущение демо)'
                        events[cid].append((current, bucket))
            rows.append(detail)
            # Планируем на уровне договора; остальные договоры клиента в этот день
            # видны в отчёте как уступившие договору с большим просроченным долгом.
            for other in eligible:
                if selected and other['loan_id'] != selected['loan_id']:
                    rows.append({**detail, 'loan': other['loan_id'], 'channel': '',
                                 'status': 'SKIPPED', 'primary': False,
                                 'reason': f"Приоритет договору {selected['loan_id']} по сумме просрочки"})
    return rows, schemes, (promise_client, promise_until)


def render_report(rows, schemes, promise, start, days):
    def e(value):
        return html.escape(str(value))

    primary = [r for r in rows if r['primary']]
    assigned = [r for r in primary if r['status'] == 'ASSIGNED']
    blocked = [r for r in primary if r['status'] == 'BLOCKED']
    by_day = Counter(str(r['date']) for r in assigned)
    by_channel = Counter(r['channel'] for r in assigned)
    reasons = Counter(r['reason'] for r in primary if r['status'] != 'ASSIGNED')
    route_status = Counter((r['route'], r['status']) for r in primary)
    route_reason = Counter((r['route'], r['reason']) for r in primary if r['status'] != 'ASSIGNED')
    client_status = Counter((r['client'], r['status']) for r in primary)
    other_loans = Counter(r['client'] for r in rows if not r['primary'])
    route_changes = Counter()
    prev = {}
    for r in primary:
        if r['client'] in prev and prev[r['client']] != r['route']:
            route_changes[(prev[r['client']], r['route'])] += 1
        prev[r['client']] = r['route']
    def table(headers, items):
        head = ''.join(f'<th>{e(h)}</th>' for h in headers)
        body = ''.join('<tr>' + ''.join(f'<td>{e(v)}</td>' for v in item) + '</tr>' for item in items)
        return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    def daily_chart():
        width, height, left, top, plot_w, plot_h = 900, 265, 48, 25, 825, 180
        values = [(start + timedelta(days=i), by_day[str(start + timedelta(days=i))])
                  for i in range(days)]
        upper = max(1, max((count for _, count in values), default=0))
        parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
                 'aria-label="Количество назначений по дням">']
        for fraction in (0, 0.5, 1):
            y = top + plot_h * (1 - fraction)
            parts.append(f'<line x1="{left}" y1="{y}" x2="{left+plot_w}" y2="{y}" '
                         'stroke="#d9e2e2"/><text x="{left-8}" y="{y+4}" text-anchor="end" '
                         f'fill="#60717a" font-size="12">{round(upper*fraction)}</text>')
        step = plot_w / max(1, days)
        for i, (day, count) in enumerate(values):
            bar_h = count / upper * plot_h
            bar_w = max(2, step * .68)
            x = left + i * step + (step - bar_w) / 2
            y = top + plot_h - bar_h
            color = '#a8bdc2' if day.weekday() >= 5 else '#2c7a86'
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                         f'height="{bar_h:.1f}" rx="3" fill="{color}">'
                         f'<title>{e(day)}: {count} назначений</title></rect>')
            if days <= 14 or i % max(1, math.ceil(days / 14)) == 0:
                parts.append(f'<text x="{x+bar_w/2:.1f}" y="{top+plot_h+22}" '
                             f'text-anchor="middle" fill="#60717a" font-size="11">'
                             f'{day:%d.%m}</text>')
        parts.append('</svg>')
        return ''.join(parts)

    def channel_chart():
        items = sorted(by_channel.items(), key=lambda item: (-item[1], item[0]))
        if not items:
            return '<p>Назначений нет.</p>'
        maximum = max(by_channel.values())
        colors = {'ROBOT': '#2c7a86', 'CALL': '#315f7e', 'SMS': '#efa45d',
                  'PUSH': '#a579b4', 'EMAIL': '#79a979', 'VOICE': '#c87b78'}
        lines = ['<div class="bars">']
        for channel, count in items:
            lines.append(f'<div class="bar-row"><span>{e(channel)}</span>'
                         f'<div class="bar-track"><div class="bar-fill" '
                         f'style="width:{100*count/maximum:.1f}%;background:{colors.get(channel, "#2c7a86")}"></div>'
                         f'</div><strong>{count}</strong></div>')
        return ''.join(lines) + '</div>'

    def route_chart():
        colors = {'ASSIGNED': '#2c7a86', 'BLOCKED': '#dc8060', 'SKIPPED': '#bdcdd0'}
        lines = ['<div class="legend"><span>● Назначено</span><span>● Заблокировано</span>'
                 '<span>● Без контакта</span></div><div class="bars">']
        for rt in sorted({r['route'] for r in primary}):
            total = sum(route_status[rt, status] for status in colors)
            pieces = ''.join(
                f'<div style="width:{100*route_status[rt,status]/total:.1f}%;'
                f'background:{color}" title="{e(rt)} · {e(status)}: '
                f'{route_status[rt,status]}"></div>'
                for status, color in colors.items() if route_status[rt,status]
            )
            lines.append(f'<div class="bar-row route-row"><span>{e(rt)}</span>'
                         f'<div class="bar-track stacked">{pieces}</div><strong>{total}</strong></div>')
        return ''.join(lines) + '</div>'

    detail = [(r['date'], r['client'], r['loan'], r['route'], r['self_cure'], r['repayment'],
               r['channel'], r['status'], 'Основной' if r['primary'] else 'Другой договор',
               r['reason']) for r in rows]
    page = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Симуляция коммуникаций</title>
<style>body{{font:15px system-ui;margin:28px;background:#FAF7E8;color:#273039}}h1,h2{{color:#22505b}}
.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{background:white;padding:18px;border-radius:12px;min-width:150px}}
.scroll{{overflow:auto;max-height:520px;background:white;border-radius:10px}}table{{border-collapse:collapse;width:100%}}
td,th{{padding:8px 12px;border-bottom:1px solid #ddd;text-align:left;white-space:nowrap}}th{{position:sticky;top:0;background:#e5efef}}
.note{{background:#fff5d8;padding:12px;border-radius:8px}}.chart{{background:white;border-radius:12px;padding:18px;margin:12px 0;max-width:950px}}
.chart svg{{width:100%;height:auto}}.bars{{display:grid;gap:11px}}.bar-row{{display:grid;grid-template-columns:95px minmax(80px,1fr) 42px;align-items:center;gap:12px}}
.route-row{{grid-template-columns:125px minmax(80px,1fr) 42px}}.bar-track{{height:22px;background:#edf1f1;border-radius:5px;overflow:hidden}}
.bar-fill{{height:100%;border-radius:5px}}.stacked{{display:flex}}.stacked div{{height:100%}}.legend{{display:flex;gap:20px;margin-bottom:16px;flex-wrap:wrap}}
.legend span:nth-child(1){{color:#2c7a86}}.legend span:nth-child(2){{color:#dc8060}}.legend span:nth-child(3){{color:#8ca3a7}}
@media(max-width:600px){{body{{margin:12px}}.route-row{{grid-template-columns:95px minmax(80px,1fr) 28px;font-size:12px}}}}</style>
<h1>Симуляция коммуникаций</h1><p>{e(start)} · {days} календарных дней · {len(set(r['client'] for r in rows))} клиентов</p>
<div class="cards"><div class="card">Назначено<br><strong>{len(assigned)}</strong></div>
<div class="card">Заблокировано лимитами/правилами<br><strong>{len(blocked)}</strong></div>
<div class="card">Нарушений лимитов в назначенном плане<br><strong>0</strong></div>
<div class="card">Смен маршрута<br><strong>{sum(route_changes.values())}</strong></div></div>
<p class="note">Учебный сценарий: ежедневные скоры вычисляются по воспроизводимой формуле от исходного снимка; реальной ML-модели здесь нет. Каждый разрешённый контакт считается состоявшимся. Клиент {promise[0]} дал вымышленное обещание платежа до {promise[1]} включительно (пауза с четвёртого дня). План не отправляет сообщения. Время контактов не моделируется, поэтому соблюдение ограничений по времени здесь не проверяется. Классификация PUSH/EMAIL/VOICE как сообщений требует проверки для конкретной реализации.</p>
<h2>Недельные схемы</h2>{table(['Маршрут','Каналы и количество'], [(k, ', '.join(f'{c} × {n}' for c,n in sorted(v.items()))) for k,v in schemes.items()])}
<h2>Динамика назначений</h2><div class="chart">{daily_chart()}<p>Наведите на столбец для даты и количества. Выходные выделены серым.</p></div>
<h2>Распределение по каналам</h2><div class="chart">{channel_chart()}</div>
<h2>Назначения и пропуски по маршрутам</h2><div class="chart">{route_chart()}<p>Каждая полоса — клиентские дни в этом маршруте; число справа — их общее количество.</p></div>
<h2>Назначения по дням</h2>{table(['Дата','Контактов'], sorted(by_day.items()))}
<h2>По каналам</h2>{table(['Канал','Контактов'], sorted(by_channel.items()))}
<h2>Итог по маршрутам</h2>{table(['Маршрут','Назначено','Заблокировано','Без контакта'],
 [(route, route_status[route,'ASSIGNED'], route_status[route,'BLOCKED'], route_status[route,'SKIPPED'])
  for route in sorted({r['route'] for r in primary})])}
<h2>Почему нет контакта в маршруте</h2>{table(['Маршрут','Причина','Клиенто-дней'],
 [(route, reason, count) for (route, reason), count in sorted(route_reason.items())])}
<h2>Итог по клиентам</h2>{table(['Клиент','Назначено','Заблокировано','Без контакта','Другие договоры уступили приоритет'],
 [(cid, client_status[cid,'ASSIGNED'], client_status[cid,'BLOCKED'],
   client_status[cid,'SKIPPED'], other_loans[cid]) for cid in sorted({r['client'] for r in primary})])}
<h2>Причины пропуска или блокировки</h2>{table(['Причина','Клиенто-дней'], reasons.most_common())}
<h2>Смена маршрута</h2>{table(['Было','Стало','Событий'], [(a,b,n) for (a,b),n in route_changes.items()])}
<h2>Все решения по клиентам и дням</h2>{table(['Дата','Клиент','Договор','Маршрут','Self-cure','Repayment','Канал','Статус','Учёт','Объяснение'],detail)}
</html>'''
    return page
