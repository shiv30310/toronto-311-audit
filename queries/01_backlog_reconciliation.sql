WITH reference_date AS (
    -- Use the max date in the dataset as "today" so the query is reproducible
    SELECT MAX("Creation Date"::TIMESTAMP) AS max_date
    FROM service_requests_2026
),
base AS (
    SELECT
        "Service Request Type",
        "Status",
        "Creation Date"::TIMESTAMP AS created_at,
        r.max_date
    FROM service_requests_2026
    CROSS JOIN reference_date r
)
SELECT
    "Service Request Type",

    -- 1. How many are currently active (New or In Progress)?
    COUNT(*) FILTER (WHERE "Status" IN ('New', 'In Progress')) AS active_requests,

    -- 2. How many of those have been active for >90 days? (The True Backlog)
    COUNT(*) FILTER (
        WHERE "Status" IN ('New', 'In Progress')
          AND date_diff('day', created_at, max_date) > 90
    ) AS aged_backlog_90d,

    -- 3. What % of the active requests are actually aged backlog?
    ROUND(
        100.0 * COUNT(*) FILTER (
            WHERE "Status" IN ('New', 'In Progress')
              AND date_diff('day', created_at, max_date) > 90
        ) / NULLIF(COUNT(*) FILTER (WHERE "Status" IN ('New', 'In Progress')), 0),
        2
    ) AS pct_active_aged,

    -- 4. Data Quality: How many are "Unknown"?
    COUNT(*) FILTER (WHERE "Status" = 'Unknown') AS unknown_status_count

FROM base
GROUP BY 1
-- Only show categories that actually have active requests
HAVING COUNT(*) FILTER (WHERE "Status" IN ('New', 'In Progress')) > 0
ORDER BY aged_backlog_90d DESC;
