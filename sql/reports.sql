-- Отчёты для руководителя продаж (FR-10). Запуск: leasing report.
-- Каждый запрос начинается со строки «-- name: <имя>»; :now — момент построения отчёта.

-- name: approval_by_asset
-- Доля одобрений по типу предмета лизинга среди заявок, по которым есть решение.
SELECT asset_type,
       COUNT(*)                                                AS decided,
       SUM(status = 'APPROVED')                                AS approved,
       ROUND(100.0 * SUM(status = 'APPROVED') / COUNT(*), 1)   AS approval_pct
FROM applications
WHERE status IN ('APPROVED', 'DECLINED')
GROUP BY asset_type
ORDER BY asset_type;

-- name: decision_time
-- Время от последней отправки в банк до решения, часы.
WITH sent AS (
    SELECT application_id, MAX(at) AS sent_at
    FROM status_history
    WHERE to_status = 'SENT'
    GROUP BY application_id
),
decided AS (
    SELECT application_id, at AS decided_at
    FROM status_history
    WHERE to_status IN ('APPROVED', 'DECLINED')
)
SELECT COUNT(*)                                                                AS decided,
       ROUND(AVG((julianday(d.decided_at) - julianday(s.sent_at)) * 24), 1)   AS avg_hours,
       ROUND(MAX((julianday(d.decided_at) - julianday(s.sent_at)) * 24), 1)   AS max_hours
FROM sent s
JOIN decided d USING (application_id);

-- name: stuck_in_review
-- Заявки, которые ждут решения банка дольше суток: по ним нужно звонить в банк.
SELECT a.id,
       a.company_name,
       ROUND((julianday(:now) - julianday(MAX(h.at))) * 24, 1) AS hours_waiting
FROM applications a
JOIN status_history h ON h.application_id = a.id AND h.to_status = 'SENT'
WHERE a.status = 'SENT'
GROUP BY a.id, a.company_name
HAVING julianday(:now) - julianday(MAX(h.at)) > 1
ORDER BY hours_waiting DESC;

-- name: decline_reasons
-- Причины отказов банка.
SELECT reason_code,
       COUNT(*) AS declined
FROM applications
WHERE status = 'DECLINED'
GROUP BY reason_code
ORDER BY declined DESC, reason_code;
