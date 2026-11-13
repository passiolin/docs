#!/bin/bash
# External heartbeat writer for the MGR scenario.
# Keeps working across MGR primary switches, unlike a MySQL EVENT created on
# the old primary (which becomes REPLICA_SIDE_DISABLED after promotion).
set -u
: "${APP_PASSWORD:?APP_PASSWORD is required}"
while true; do
  MYSQL_PWD="$APP_PASSWORD" mysql --protocol=tcp -h10.10.12.125 -P6446 -uapp -N -B \
    -e "UPDATE app_mgr.sync_heartbeat SET ts=NOW(3), seq=seq+1 WHERE id=1;" \
    >/dev/null 2>&1
  sleep 1
done
