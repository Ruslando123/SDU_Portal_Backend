COPY (
  SELECT sdu_diagnostics.step(query) AS business_step, queryid, toplevel, calls,
         round(total_exec_time::numeric,2) AS total_ms, round(mean_exec_time::numeric,3) AS mean_ms,
         round(max_exec_time::numeric,2) AS max_ms,
         rows, shared_blks_hit, shared_blks_read, wal_records
  FROM sdu_diagnostics.pg_stat_statements
  WHERE userid=(SELECT oid FROM pg_roles WHERE rolname='portal_runtime') AND calls>0
  ORDER BY total_exec_time DESC LIMIT 120
) TO STDOUT WITH (FORMAT csv, HEADER);
