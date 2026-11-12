WITH connections AS MATERIALIZED (
  SELECT pid, state, wait_event_type, wait_event, xact_start, query_start, query, query_id
  FROM pg_stat_activity
  WHERE datname=current_database() AND usename='portal_runtime' AND backend_type='client backend'
), waiters AS MATERIALIZED (
  SELECT c.*, pg_blocking_pids(c.pid) AS blockers
  FROM connections c WHERE c.wait_event_type='Lock'
), waits AS (
  SELECT w.pid, b.pid AS blocker_pid, w.wait_event,
         sdu_diagnostics.step(w.query) AS waiting_step, sdu_diagnostics.step(b.query) AS blocker_last_step,
         w.query_id::text AS waiting_query_id, b.query_id::text AS blocker_query_id, b.state AS blocker_state,
         l.locktype, l.mode, l.relation::regclass::text AS relation, l.transactionid::text AS lock_xid,
         round((extract(epoch FROM clock_timestamp()-l.waitstart)*1000)::numeric,2) AS wait_ms,
         round((extract(epoch FROM clock_timestamp()-w.xact_start)*1000)::numeric,2) AS waiting_transaction_ms,
         round((extract(epoch FROM clock_timestamp()-b.xact_start)*1000)::numeric,2) AS blocker_transaction_ms
  FROM waiters w
  LEFT JOIN LATERAL (SELECT * FROM pg_locks WHERE pid=w.pid AND NOT granted ORDER BY waitstart LIMIT 1) l ON true
  LEFT JOIN LATERAL unnest(w.blockers) p(pid) ON true
  LEFT JOIN connections b ON b.pid=p.pid
  LIMIT 120
)
SELECT jsonb_build_object(
  'connections',(SELECT count(*) FROM connections),
  'connection_budget', (SELECT jsonb_build_object(
      'business', count(*) FILTER (WHERE usename='portal_runtime' AND application_name='portal-benchmark'),
      'security', count(*) FILTER (WHERE usename='portal_runtime' AND application_name='sdu-security'),
      'operational', count(*) FILTER (WHERE usename IS DISTINCT FROM 'portal_runtime'
          OR application_name NOT IN ('portal-benchmark', 'sdu-security')))
    FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend'),
  'active',(SELECT count(*) FROM connections WHERE state='active'),
  'waits',coalesce((SELECT json_object_agg(wait_event_type || ':' || wait_event,n)
    FROM (SELECT wait_event_type,wait_event,count(*) AS n FROM connections WHERE state='active' AND wait_event IS NOT NULL GROUP BY 1,2) x),'{}'),
  'wal', (SELECT jsonb_build_object('records',wal_records,'full_page_images',wal_fpi,'bytes',wal_bytes,
                                   'buffers_full',wal_buffers_full,'reset',stats_reset) FROM pg_stat_wal),
  'wal_io', (SELECT jsonb_build_object('writes',sum(writes),'write_ms',sum(write_time),
                         'syncs',sum(fsyncs),'sync_ms',sum(fsync_time),'reset',max(stats_reset))
             FROM pg_stat_io WHERE object='wal'),
  'lock_edges',coalesce((SELECT jsonb_agg(waits) FROM waits),'[]'));
