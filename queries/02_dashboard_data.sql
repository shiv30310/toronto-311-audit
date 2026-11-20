WITH reference_date AS (
    SELECT MAX("Creation Date"::TIMESTAMP) AS max_date FROM service_requests_2026
),
base AS (
    SELECT
        "Service Request Type",
        "Status",
        "Creation Date"::TIMESTAMP AS created_at,
        date_diff('day', "Creation Date"::TIMESTAMP, r.max_date) AS age_days
    FROM service_requests_2026
    CROSS JOIN reference_date r
    WHERE "Status" IN ('New', 'In Progress') -- Only look at active requests
)
SELECT
    "Service Request Type",
    -- Group into visual buckets for Power BI
    CASE
        WHEN age_days <= 30 THEN '1. Fresh (0-30d)'
        WHEN age_days <= 90 THEN '2. Aging (31-90d)'
        ELSE '3. Backlog (>90d)'
    END AS age_bucket,
    COUNT(*) AS request_count
FROM base
GROUP BY 1, 2
ORDER BY 1, 2;
