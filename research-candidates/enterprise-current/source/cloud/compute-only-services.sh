#!/bin/bash
# Install only compute supervision and an independent fixed-deadline watchdog.
set -euo pipefail
umask 077
cd /opt/dams
test -f /var/lib/dams-compute/compute-runtime.json
stop_timeout=$(PYTHONPATH=/opt/dams:/opt/dams/research_tools /usr/bin/python3 \
  /opt/dams/research_tools/compute_only_worker.py service-timeout)
[[ "$stop_timeout" =~ ^[0-9]+$ ]] || exit 2
cat > /etc/systemd/system/dams-compute-pipeline.service <<UNIT
[Unit]
Description=DAMS fixed-source compute-only pipeline
After=network-online.target dams-compute-watchdog.service
Wants=network-online.target
Requires=dams-compute-watchdog.service

[Service]
Type=simple
WorkingDirectory=/opt/dams
ExecStart=/usr/bin/python3 /opt/dams/research_tools/compute_only_worker.py run
Restart=no
TimeoutStopSec=${stop_timeout}
KillMode=mixed
UMask=0077
NoNewPrivileges=true
Environment=PYTHONPATH=/opt/dams:/opt/dams/research_tools
Environment=OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/dams-compute-watchdog.service <<'UNIT'
[Unit]
Description=DAMS compute-only absolute-deadline watchdog
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/dams/research_tools/compute_only_worker.py watchdog
Restart=on-failure
RestartSec=1
UMask=0077
NoNewPrivileges=true
Environment=PYTHONPATH=/opt/dams:/opt/dams/research_tools

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now dams-compute-watchdog.service
systemctl enable --now dams-compute-pipeline.service
