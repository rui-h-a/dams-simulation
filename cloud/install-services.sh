#!/bin/bash
set -euo pipefail
umask 077
cd /opt/dams
test -f /var/lib/dams/guest-runtime.json
cat > /etc/systemd/system/dams-pipeline.service <<'UNIT'
[Unit]
Description=DAMS fixed-source research pipeline
After=network-online.target
Wants=network-online.target dams-upload.timer dams-watchdog.service

[Service]
Type=simple
WorkingDirectory=/opt/dams
ExecStart=/usr/bin/python3 /opt/dams/research_tools/cloud_worker.py run
ExecStopPost=/usr/bin/python3 /opt/dams/research_tools/cloud_worker.py upload
Restart=no
TimeoutStopSec=45
KillMode=mixed
UMask=0077
NoNewPrivileges=true
Environment=OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/dams-upload.service <<'UNIT'
[Unit]
Description=DAMS verified periodic persistence
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/dams/research_tools/cloud_worker.py upload
# HTTP/chunk checks and server terminationTime impose the absolute bound.
# A fixed 50-second service timeout would discard a legitimate large snapshot.
TimeoutStartSec=infinity
UMask=0077
NoNewPrivileges=true
UNIT
# The timer interval is validated and materialized from the runtime, not user text.
interval=$(python3 -c 'import json; n=json.load(open("/var/lib/dams/guest-runtime.json"))["upload_interval_seconds"]; assert isinstance(n,int) and 15<=n<=300; print(n)')
cat > /etc/systemd/system/dams-upload.timer <<UNIT
[Unit]
Description=DAMS periodic immutable snapshot upload
[Timer]
OnBootSec=30
OnUnitInactiveSec=${interval}
AccuracySec=1
Unit=dams-upload.service
[Install]
WantedBy=timers.target
UNIT
cat > /etc/systemd/system/dams-watchdog.service <<'UNIT'
[Unit]
Description=DAMS absolute-deadline watchdog
After=network-online.target
[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/dams/research_tools/cloud_worker.py watchdog
Restart=on-failure
RestartSec=2
UMask=0077
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now dams-upload.timer dams-watchdog.service
systemctl enable --now dams-pipeline.service
