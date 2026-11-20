#!/usr/bin/env bash
# Install asc-ctl (run ONCE per server by an administrator, with sudo).
#
#   sudo bash /home/asc/ai-stream-clipper/infrastructure/admin/install-asc-ctl.sh 210 <expected-sha256>
#
# What it does (and nothing else):
#   * copies asc-ctl to /usr/local/sbin/asc-ctl (root:root 0755) after checking its SHA-256
#   * writes /etc/asc/asc-ctl.conf (server id, LAN IP, render gid, asc user, repo path)
#   * creates /opt/asc/{models,test-media,bench} (uid 10001 = container user) and /etc/asc/tls
#   * allows ONLY `asc` to run ONLY /usr/local/sbin/asc-ctl as root: /etc/sudoers.d/asc-ctl
#     (validated with visudo before it is put in place)
# It does not touch Docker, other containers, groups, firewall, Caddy or any other service.
#
# Uninstall:  sudo rm /etc/sudoers.d/asc-ctl /usr/local/sbin/asc-ctl
#             (data in /opt/asc and /etc/asc is left in place; remove manually if wanted)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
SERVER="${1:?usage: install-asc-ctl.sh 201|206|210|212 EXPECTED_SHA256}"
EXPECTED="${2:?missing expected sha256 of asc-ctl}"
SRC="$(cd "$(dirname "$0")" && pwd)/asc-ctl"
case "$SERVER" in
  201) LAN_IP=192.168.1.201 ;;
  206) LAN_IP=192.168.1.206 ;;
  210) LAN_IP=192.168.1.210 ;;
  212) LAN_IP=192.168.1.212 ;;  # dedicated database host (asc PostgreSQL only, port 55432)
  *) echo "server must be 201, 206, 210 or 212"; exit 1 ;;
esac
id asc >/dev/null 2>&1 || { echo "user asc does not exist"; exit 1; }
command -v docker >/dev/null || { echo "docker not found"; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }

ACTUAL="$(sha256sum "$SRC" | cut -d' ' -f1)"
if [ "$ACTUAL" != "$EXPECTED" ]; then
  echo "SHA-256 mismatch: got $ACTUAL, expected $EXPECTED - aborting"; exit 1
fi

RENDER_GID="$(getent group render | cut -d: -f3 || true)"
install -o root -g root -m 0755 "$SRC" /usr/local/sbin/asc-ctl
install -d -o root -g root -m 0755 /etc/asc /etc/asc/tls
install -d -o root -g root -m 0700 /etc/asc/ca
cat > /etc/asc/asc-ctl.conf <<EOF
SERVER=$SERVER
LAN_IP=$LAN_IP
RENDER_GID=$RENDER_GID
ASC_USER=asc
REPO=/home/asc/ai-stream-clipper
OPENVINO_IMAGE=openvino/ubuntu24_runtime:2026.2.0
EOF
chmod 0644 /etc/asc/asc-ctl.conf
install -d -o 10001 -g 10001 -m 0755 /opt/asc /opt/asc/models /opt/asc/test-media /opt/asc/bench
chown root:root /opt/asc
touch /var/log/asc-ctl.log && chmod 0640 /var/log/asc-ctl.log

TMP="$(mktemp)"
echo "asc ALL=(root) NOPASSWD: /usr/local/sbin/asc-ctl" > "$TMP"
visudo -cf "$TMP"
install -o root -g root -m 0440 "$TMP" /etc/sudoers.d/asc-ctl
rm -f "$TMP"

echo "asc-ctl $(/usr/local/sbin/asc-ctl version) installed on server $SERVER (render gid: ${RENDER_GID:-none})"
echo "test as asc:  sudo -n /usr/local/sbin/asc-ctl status"
