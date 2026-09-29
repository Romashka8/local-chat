-- Локальный учебный стенд для Greenplum 7.1.0.
-- Запускать один раз в пустой базе demo. Все имена и суммы вымышлены.
-- Сценарий НЕ отправляет коммуникации и НЕ утверждает правовую допустимость плана.

CREATE SCHEMA collection_sim;

CREATE TABLE collection_sim.client (
    client_id integer NOT NULL,
    client_label text NOT NULL,
    timezone_name text NOT NULL DEFAULT 'Europe/Moscow',
    early_late_collection boolean NOT NULL DEFAULT false,
    no_contact boolean NOT NULL DEFAULT false,
    no_contact_reason text,
    active boolean NOT NULL DEFAULT true
) DISTRIBUTED BY (client_id);

CREATE TABLE collection_sim.loan (
    loan_id integer NOT NULL,
    client_id integer NOT NULL,
    product_code text NOT NULL CHECK (product_code IN ('MORTGAGE','CARD','CONSUMER','AUTO')),
    balance_rub numeric(16,2) NOT NULL CHECK (balance_rub >= 0),
    overdue_rub numeric(16,2) NOT NULL CHECK (overdue_rub >= 0),
    dpd integer NOT NULL CHECK (dpd >= 0),
    is_active boolean NOT NULL,
    as_of_date date NOT NULL
) DISTRIBUTED BY (client_id);

CREATE TABLE collection_sim.client_score (
    client_id integer NOT NULL,
    as_of_date date NOT NULL,
    self_cure_score numeric(5,4) NOT NULL CHECK (self_cure_score BETWEEN 0 AND 1),
    repayment_score numeric(5,4) NOT NULL CHECK (repayment_score BETWEEN 0 AND 1)
) DISTRIBUTED BY (client_id);

-- DIRECT: переговоры с оператором и интерактивным роботом вместе.
-- MESSAGE: СМС, пуш, e-mail, запись голоса; классификация последних трех
-- для конкретной реализации должна быть согласована перед реальным использованием.
CREATE TABLE collection_sim.channel (
    channel_code text NOT NULL,
    channel_label text NOT NULL,
    legal_bucket text NOT NULL CHECK (legal_bucket IN ('DIRECT','MESSAGE')),
    is_enabled boolean NOT NULL DEFAULT true
) DISTRIBUTED RANDOMLY;

CREATE TABLE collection_sim.client_channel_priority (
    client_id integer NOT NULL,
    channel_code text NOT NULL,
    priority_no integer NOT NULL CHECK (priority_no > 0),
    is_allowed boolean NOT NULL DEFAULT true
) DISTRIBUTED BY (client_id);

-- Дневные/недельные/месячные лимиты считаются суммарно на клиента,
-- по календарной неделе и месяцу, а не отдельно по каждому договору.
CREATE TABLE collection_sim.contact_limit (
    legal_bucket text NOT NULL,
    max_per_day integer NOT NULL,
    max_per_calendar_week integer NOT NULL,
    max_per_calendar_month integer NOT NULL,
    workday_start time NOT NULL,
    workday_end time NOT NULL,
    holiday_start time NOT NULL,
    holiday_end time NOT NULL,
    valid_from date NOT NULL,
    valid_to date
) DISTRIBUTED RANDOMLY;

CREATE TABLE collection_sim.holiday_calendar (
    holiday_date date NOT NULL,
    description text NOT NULL
) DISTRIBUTED RANDOMLY;

CREATE TABLE collection_sim.strategy (
    strategy_id integer NOT NULL,
    strategy_code text NOT NULL,
    description text NOT NULL,
    is_enabled boolean NOT NULL DEFAULT true
) DISTRIBUTED RANDOMLY;

-- day_of_week: 1=понедельник, 7=воскресенье. Несколько слотов за день допустимы.
CREATE TABLE collection_sim.strategy_slot (
    strategy_id integer NOT NULL,
    day_of_week integer NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    slot_no integer NOT NULL CHECK (slot_no > 0),
    channel_code text NOT NULL,
    desired_local_time time NOT NULL
) DISTRIBUTED RANDOMLY;

CREATE TABLE collection_sim.routing_policy (
    policy_id integer NOT NULL,
    self_cure_no_touch_min numeric(5,4) NOT NULL,
    intensive_repayment_max numeric(5,4) NOT NULL,
    light_repayment_min numeric(5,4) NOT NULL
) DISTRIBUTED RANDOMLY;

CREATE TABLE collection_sim.simulation_run (
    run_id integer NOT NULL,
    start_date date NOT NULL,
    days_count integer NOT NULL CHECK (days_count BETWEEN 1 AND 365),
    snapshot_date date NOT NULL,
    run_status text NOT NULL CHECK (run_status IN ('DRAFT','READY','FINISHED')),
    created_at timestamp NOT NULL DEFAULT current_timestamp,
    note text
) DISTRIBUTED RANDOMLY;

-- Каждая строка: одно назначение на конкретный день. Сюда позже пишет симулятор.
CREATE TABLE collection_sim.planned_contact (
    run_id integer NOT NULL,
    client_id integer NOT NULL,
    plan_date date NOT NULL,
    slot_no integer NOT NULL,
    channel_code text NOT NULL,
    strategy_id integer NOT NULL,
    legal_bucket text NOT NULL,
    plan_status text NOT NULL CHECK (plan_status IN ('ASSIGNED','BLOCKED','SKIPPED')),
    decision_reason text NOT NULL,
    planned_local_time time
) DISTRIBUTED BY (client_id);

-- Реальные контакты/исходы: отдельны от планов и входят в расчёт доступных лимитов.
CREATE TABLE collection_sim.contact_event (
    event_id bigint NOT NULL,
    client_id integer NOT NULL,
    loan_id integer,
    channel_code text NOT NULL,
    occurred_at timestamp NOT NULL,
    local_date date NOT NULL,
    legal_bucket text NOT NULL,
    outcome text NOT NULL,
    counts_toward_limit boolean NOT NULL,
    run_id integer
) DISTRIBUTED BY (client_id);

INSERT INTO collection_sim.channel VALUES
 ('CALL','Звонок оператора','DIRECT',true),
 ('ROBOT','Интерактивный робот','DIRECT',true),
 ('SMS','СМС','MESSAGE',true),
 ('PUSH','Пуш','MESSAGE',true),
 ('EMAIL','Электронная почта','MESSAGE',true),
 ('VOICE','Одностороннее голосовое сообщение','MESSAGE',true);

-- Учебные пределы для сценарного контроля, применимость уточняется по типу контакта.
INSERT INTO collection_sim.contact_limit VALUES
 ('DIRECT',1,2,8,'08:00','22:00','09:00','20:00','2026-01-01',NULL),
 ('MESSAGE',2,4,16,'08:00','22:00','09:00','20:00','2026-01-01',NULL);

INSERT INTO collection_sim.strategy VALUES
 (1,'INTENSIVE','В неделю: два робота, две СМС, один пуш',true),
 (2,'STANDARD','В неделю: один робот, одна СМС, один e-mail',true),
 (3,'LIGHT','В неделю: одна СМС и один пуш',true);

INSERT INTO collection_sim.strategy_slot VALUES
 (1,1,1,'ROBOT','11:00'), (1,2,1,'SMS','12:00'),
 (1,3,1,'ROBOT','11:00'), (1,4,1,'SMS','12:00'), (1,5,1,'PUSH','12:00'),
 (2,1,1,'SMS','12:00'), (2,3,1,'ROBOT','11:00'), (2,5,1,'EMAIL','12:00'),
 (3,2,1,'SMS','12:00'), (3,5,1,'PUSH','12:00');

-- Меняя эту строку, можно настраивать маршрутизацию без изменения SQL-представления.
-- В таблице должна оставаться ровно одна активная политика для учебного сценария.
INSERT INTO collection_sim.routing_policy VALUES (1,0.8000,0.3500,0.7000);

INSERT INTO collection_sim.simulation_run
    (run_id,start_date,days_count,snapshot_date,run_status,note)
VALUES (1,'2026-09-28',14,'2026-09-28','DRAFT',
        'Учебный сценарий на 14 дней; назначения пока не рассчитаны');

INSERT INTO collection_sim.client VALUES
 (1,'Клиент 01','Europe/Moscow',false,false,NULL,true),
 (2,'Клиент 02','Europe/Moscow',false,false,NULL,true),
 (3,'Клиент 03','Europe/Moscow',true,false,NULL,true),
 (4,'Клиент 04','Europe/Moscow',false,false,NULL,true),
 (5,'Клиент 05','Europe/Moscow',false,false,NULL,true),
 (6,'Клиент 06','Europe/Moscow',false,true,'Учебный отказ от контактов',true),
 (7,'Клиент 07','Europe/Moscow',false,false,NULL,true),
 (8,'Клиент 08','Europe/Moscow',false,false,NULL,true),
 (9,'Клиент 09','Europe/Moscow',false,false,NULL,true),
 (10,'Клиент 10','Europe/Moscow',false,false,NULL,true),
 (11,'Клиент 11','Europe/Moscow',false,false,NULL,true),
 (12,'Клиент 12','Europe/Moscow',true,false,NULL,true);

INSERT INTO collection_sim.loan VALUES
 (101,1,'MORTGAGE',4200000,78000,12,true,'2026-09-28'),
 (102,1,'CARD',90000,12000,5,true,'2026-09-28'),
 (103,2,'CONSUMER',350000,24000,8,true,'2026-09-28'),
 (104,3,'AUTO',900000,93000,35,true,'2026-09-28'),
 (105,4,'CARD',140000,14000,3,true,'2026-09-28'),
 (106,4,'CONSUMER',500000,0,0,true,'2026-09-28'),
 (107,5,'MORTGAGE',2800000,41000,6,true,'2026-09-28'),
 (108,6,'CARD',85000,10000,18,true,'2026-09-28'),
 (109,7,'AUTO',1100000,53000,9,true,'2026-09-28'),
 (110,7,'CARD',45000,5000,2,true,'2026-09-28'),
 (111,8,'CONSUMER',210000,12000,4,true,'2026-09-28'),
 (112,9,'MORTGAGE',3600000,82000,21,true,'2026-09-28'),
 (113,9,'AUTO',600000,0,0,true,'2026-09-28'),
 (114,10,'CARD',70000,16000,11,true,'2026-09-28'),
 (115,11,'CONSUMER',470000,37000,14,true,'2026-09-28'),
 (116,12,'AUTO',780000,69000,29,true,'2026-09-28');

INSERT INTO collection_sim.client_score VALUES
 (1,'2026-09-28',0.2100,0.1800), (2,'2026-09-28',0.8800,0.8600),
 (3,'2026-09-28',0.1200,0.1300), (4,'2026-09-28',0.4400,0.6200),
 (5,'2026-09-28',0.8300,0.7700), (6,'2026-09-28',0.2000,0.3300),
 (7,'2026-09-28',0.3000,0.2700), (8,'2026-09-28',0.5700,0.5100),
 (9,'2026-09-28',0.1700,0.3100), (10,'2026-09-28',0.7900,0.7200),
 (11,'2026-09-28',0.3800,0.4000), (12,'2026-09-28',0.1100,0.1200);

-- Приоритет канала у каждого клиента: меньший номер важнее.
INSERT INTO collection_sim.client_channel_priority
SELECT c.client_id, x.channel_code,
       ((x.priority_no + c.client_id - 2) % 6) + 1, true
FROM collection_sim.client c
CROSS JOIN (
 SELECT 'CALL' AS channel_code, 1 AS priority_no UNION ALL
 SELECT 'ROBOT',2 UNION ALL SELECT 'SMS',3 UNION ALL
 SELECT 'PUSH',4 UNION ALL SELECT 'EMAIL',5 UNION ALL SELECT 'VOICE',6
) x;

CREATE VIEW collection_sim.v_portfolio AS
SELECT c.client_id, c.client_label, c.timezone_name, c.early_late_collection,
       c.no_contact, s.as_of_date, s.self_cure_score, s.repayment_score,
       count(l.loan_id) FILTER (WHERE l.is_active) AS active_loans,
       count(l.loan_id) FILTER (WHERE l.is_active AND l.dpd > 0) AS overdue_loans,
       coalesce(sum(l.overdue_rub) FILTER (WHERE l.is_active),0) AS total_overdue_rub,
       coalesce(max(l.dpd) FILTER (WHERE l.is_active),0) AS max_dpd
FROM collection_sim.client c
JOIN collection_sim.client_score s ON s.client_id = c.client_id
LEFT JOIN collection_sim.loan l ON l.client_id = c.client_id AND l.as_of_date = s.as_of_date
WHERE c.active
GROUP BY c.client_id,c.client_label,c.timezone_name,c.early_late_collection,
         c.no_contact,s.as_of_date,s.self_cure_score,s.repayment_score;

CREATE VIEW collection_sim.v_client_route AS
SELECT p.*,
       CASE WHEN p.no_contact THEN 'NO_CONTACT'
            WHEN p.early_late_collection THEN 'EARLY_LATE'
            WHEN p.overdue_loans = 0 THEN 'NO_OVERDUE'
            WHEN p.self_cure_score >= pol.self_cure_no_touch_min THEN 'SELF_CURE'
            WHEN p.repayment_score <= pol.intensive_repayment_max THEN 'INTENSIVE'
            WHEN p.repayment_score >= pol.light_repayment_min THEN 'LIGHT'
            ELSE 'STANDARD' END AS route_code,
       CASE WHEN p.no_contact OR p.early_late_collection OR p.overdue_loans = 0
                      OR p.self_cure_score >= pol.self_cure_no_touch_min THEN NULL
            WHEN p.repayment_score <= pol.intensive_repayment_max THEN 1
            WHEN p.repayment_score >= pol.light_repayment_min THEN 3
            ELSE 2 END AS strategy_id
FROM collection_sim.v_portfolio p
CROSS JOIN collection_sim.routing_policy pol
WHERE pol.policy_id = 1;

-- Контроль после загрузки:
-- SELECT * FROM collection_sim.v_client_route ORDER BY client_id;
-- SELECT product_code, count(*) FROM collection_sim.loan GROUP BY 1 ORDER BY 1;
