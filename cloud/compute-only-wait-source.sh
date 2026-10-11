#!/bin/bash
# No credentials/download/science; root uploads an approved package via IAP next.
set -euo pipefail
umask 077
install -d -m 700 /opt/dams /var/lib/dams-compute
