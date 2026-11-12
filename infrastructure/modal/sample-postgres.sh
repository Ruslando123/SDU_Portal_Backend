#!/bin/bash
set -u
export PGOPTIONS='-c statement_timeout=1800'
ticks=$(getconf CLK_TCK)
page_size=$(getconf PAGESIZE)
while true; do
  printf 'T\t%s\n' "$(date +%s%3N)"
  if pg=$(psql -X -qAt --no-password -v ON_ERROR_STOP=1 -f /opt/sdu/pg-lock-sample.sql 2>/dev/null); then
    printf 'P\t%s\n' "$pg"
  fi
  printf 'M\tsdu_pg_ticks_per_second %s\nM\tsdu_pg_page_size %s\n' "$ticks" "$page_size"
  # Each PID has independent counters. Process RSS includes shared pages.
  for stat in /proc/[0-9]*/stat; do
    [[ -r "$stat" ]] && read -r process < "$stat" || continue
    [[ "$process" == *' (postgres) '* ]] || continue
    read -r -a fields <<< "${process##*) }"
    pid=${process%% *}
    printf 'O\t%s %s %s\n' "$pid" "$(( fields[11]+fields[12] ))" "${fields[19]}"
  done
  sleep 1
done
