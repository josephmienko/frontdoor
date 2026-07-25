#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

readonly PROGRAM="${0##*/}"
DRY_RUN=false
MAINTENANCE_UPGRADE=false
HOSTNAME_VALUE=""
WHEEL_PATH=""
WHEEL_SHA256=""
CONFIG_PATH=""
AUTHORIZATION_PATH=""
SCHEMA_PATH=""
INSTALL_SERVICE=false
HARDWARE_SERVICE=false

usage() {
  cat <<'EOF'
Usage:
  sudo bash scripts/bootstrap-rp4-bench.sh [options]

Required:
  --hostname NAME             Bench hostname to configure
  --wheel PATH               Locally staged Bridgewire wheel
  --wheel-sha256 HEX         Expected SHA-256 of the wheel
  --config PATH              Sanitized simulation TOML
  --authorization PATH       Sanitized KEY,NAME,ALLOW CSV
  --schema PATH              Authorization JSON schema

Optional:
  --maintenance-upgrade      Run apt-get update and full-upgrade first
  --dry-run                  Print mutating commands without running them
  --install-service          Install and enable the safe simulated service
  --install-hardware-service Install the supervised physical bench service
  -h, --help                 Show this help

This script is safe for a standalone bench Pi. It does not configure GPIO,
serial hardware, credentials, SSH keys, disks, or a production service.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

run() {
  if "$DRY_RUN"; then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

require_value() {
  [[ $# -ge 2 && -n "$2" ]] || die "$1 requires a value"
}

while (($#)); do
  case "$1" in
    --hostname)
      require_value "$@"; HOSTNAME_VALUE="$2"; shift 2 ;;
    --wheel)
      require_value "$@"; WHEEL_PATH="$2"; shift 2 ;;
    --wheel-sha256)
      require_value "$@"; WHEEL_SHA256="${2,,}"; shift 2 ;;
    --config)
      require_value "$@"; CONFIG_PATH="$2"; shift 2 ;;
    --authorization)
      require_value "$@"; AUTHORIZATION_PATH="$2"; shift 2 ;;
    --schema)
      require_value "$@"; SCHEMA_PATH="$2"; shift 2 ;;
    --maintenance-upgrade)
      MAINTENANCE_UPGRADE=true; shift ;;
    --dry-run)
      DRY_RUN=true; shift ;;
    --install-service)
      INSTALL_SERVICE=true; shift ;;
    --install-hardware-service)
      INSTALL_SERVICE=true; HARDWARE_SERVICE=true; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      die "unknown option: $1" ;;
  esac
done

[[ -n "$HOSTNAME_VALUE" ]] || die "--hostname is required"
[[ "$HOSTNAME_VALUE" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$ ]] ||
  die "hostname is invalid"
[[ -n "$WHEEL_PATH" ]] || die "--wheel is required"
[[ -n "$CONFIG_PATH" ]] || die "--config is required"
[[ -n "$AUTHORIZATION_PATH" ]] || die "--authorization is required"
[[ -n "$SCHEMA_PATH" ]] || die "--schema is required"
[[ "$WHEEL_SHA256" =~ ^[0-9a-f]{64}$ ]] || die "--wheel-sha256 must be 64 hexadecimal characters"
[[ "$WHEEL_PATH" == *.whl ]] || die "--wheel must name a .whl file"
[[ -f "$WHEEL_PATH" ]] || die "wheel does not exist"
[[ -f "$CONFIG_PATH" ]] || die "configuration does not exist"
[[ -f "$AUTHORIZATION_PATH" ]] || die "authorization file does not exist"
[[ -f "$SCHEMA_PATH" ]] || die "schema does not exist"

[[ -r /etc/os-release ]] || die "cannot identify operating system"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${VERSION_CODENAME:-}" == "trixie" ]] || die "Raspberry Pi OS/Debian trixie is required"
[[ "$(dpkg --print-architecture)" == "arm64" ]] || die "arm64 is required"
[[ "$(python3.13 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" == "3.13" ]] ||
  die "Python 3.13 is required"
if ! "$DRY_RUN" && [[ "$EUID" -ne 0 ]]; then
  die "run as root (for example, with sudo)"
fi

actual_sha256="$(sha256sum "$WHEEL_PATH" | awk '{print $1}')"
[[ "$actual_sha256" == "$WHEEL_SHA256" ]] || die "wheel checksum does not match"

wheel_name="${WHEEL_PATH##*/}"
version="${wheel_name#bridgewire_access_control-}"
version="${version%%-*}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.][a-zA-Z0-9]+)?$ ]] ||
  die "cannot derive a safe version from the wheel filename"

readonly base_dir="/opt/bridgewire"
readonly release_dir="$base_dir/releases/$version"
readonly venv_dir="$base_dir/venvs/$version"
readonly shared_dir="$base_dir/shared"
readonly state_dir="/var/lib/bridgewire"

if "$MAINTENANCE_UPGRADE"; then
  run apt-get update
  run env DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y
fi

if [[ "$(hostname)" != "$HOSTNAME_VALUE" ]]; then
  run hostnamectl set-hostname "$HOSTNAME_VALUE"
fi

if ! getent group bridgewire >/dev/null; then
  run groupadd --system bridgewire
fi
if ! id bridgewire >/dev/null 2>&1; then
  run useradd --system --gid bridgewire --home-dir "$state_dir" \
    --create-home --shell /usr/sbin/nologin bridgewire
fi
if "$HARDWARE_SERVICE"; then
  run usermod -a -G dialout,gpio bridgewire
fi

run install -d -o root -g root -m 0755 "$base_dir" "$base_dir/releases" "$base_dir/venvs"
run install -d -o root -g bridgewire -m 0750 "$release_dir" "$shared_dir"
run install -d -o bridgewire -g bridgewire -m 0750 "$state_dir"
run install -o root -g bridgewire -m 0640 "$CONFIG_PATH" "$shared_dir/config.toml"
run install -o root -g bridgewire -m 0640 "$AUTHORIZATION_PATH" "$shared_dir/authorization.csv"
run install -o root -g bridgewire -m 0640 "$SCHEMA_PATH" "$shared_dir/schema.json"
run install -o root -g root -m 0644 "$WHEEL_PATH" "$release_dir/$wheel_name"

if [[ ! -x "$venv_dir/bin/python" ]]; then
  run python3.13 -m venv "$venv_dir"
fi
run "$venv_dir/bin/python" -m pip install --disable-pip-version-check \
  "$release_dir/$wheel_name"
run "$venv_dir/bin/bridgewire" version
run chgrp -R bridgewire "$venv_dir"
run chmod -R g+rX,o-rwx "$venv_dir"
run ln -sfn "$release_dir" "$base_dir/current.new"
run mv -Tf "$base_dir/current.new" "$base_dir/current"
run ln -sfn "$venv_dir" "$base_dir/current-venv.new"
run mv -Tf "$base_dir/current-venv.new" "$base_dir/current-venv"

printf 'Bridgewire bench release %s is staged at %s.\n' "$version" "$release_dir"
if "${INSTALL_SERVICE:-false}"; then
  unit="$(mktemp)"
  trap 'rm -f "$unit"' EXIT
  if "$HARDWARE_SERVICE"; then
    cat >"$unit" <<EOF
[Unit]
Description=Bridgewire supervised hardware access-control service
After=local-fs.target
Conflicts=bridgewire-simulated.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=bridgewire
Group=bridgewire
SupplementaryGroups=dialout gpio
ExecStart=/opt/bridgewire/current-venv/bin/bridgewire serve-hardware --config /opt/bridgewire/shared/config.toml --authorization /opt/bridgewire/shared/authorization.csv --schema /opt/bridgewire/shared/schema.json --audit /var/lib/bridgewire/audit.sqlite3 --notifications /var/lib/bridgewire/notifications.jsonl --health /run/bridgewire/health.json
Restart=on-failure
RestartSec=5
TimeoutStopSec=10
RuntimeDirectory=bridgewire
StateDirectory=bridgewire
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/bridgewire /run/bridgewire

[Install]
WantedBy=multi-user.target
EOF
  else
    cat >"$unit" <<EOF
[Unit]
Description=Bridgewire safe simulated access-control service
After=network.target

[Service]
Type=simple
User=bridgewire
Group=bridgewire
ExecStart=/opt/bridgewire/current-venv/bin/bridgewire serve-simulated --config /opt/bridgewire/shared/config.toml --authorization /opt/bridgewire/shared/authorization.csv --schema /opt/bridgewire/shared/schema.json
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/bridgewire

[Install]
WantedBy=multi-user.target
EOF
  fi
  run install -o root -g root -m 0644 "$unit" /etc/systemd/system/bridgewire.service
  run systemctl daemon-reload
  run systemctl enable bridgewire.service
  run systemctl restart bridgewire.service
fi
