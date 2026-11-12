# Disposable benchmark settings. No durability or transaction timeout changes.
case "${SDU_DB_COMMIT_DELAY_US:-0}" in
  0|200) ;;
  *) exit 2 ;;
esac
sdu_pg_diagnostic_options="-c track_commit_timestamp=on -c track_wal_io_timing=on -c commit_delay=${SDU_DB_COMMIT_DELAY_US:-0} -c shared_preload_libraries=pg_stat_statements -c pg_stat_statements.track=all -c log_lock_waits=on -c deadlock_timeout=100ms -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0 -c log_min_duration_statement=150"
