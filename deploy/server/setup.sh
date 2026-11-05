#!/usr/bin/env bash
# Prepares a fresh Ubuntu 24.04 or 26.04 server (Hetzner CAX11, arm64) for engleks. Run as root:
#
#   setup.sh <owner> '<CI public deploy key>' <storage box host, uNNNNNN.your-storagebox.de>
#
# Every step checks before it acts, so it is safe to run again. Running it again is also
# how a change to engleks-deploy, engleks-backup or their systemd units reaches the server:
# it installs them from the checkout in /opt/engleks. See docs/deployment.md.
set -euo pipefail

REPO_URL=https://github.com/topol-3000/engleks.git
ENGLEKS_DIR=/opt/engleks
BACKUP_DIR=/var/backups/engleks
MIN_COMPOSE=2.24.0

step() {
  echo "==> $*"
}

die() {
  echo "error: $*" >&2
  exit 1
}

if [[ $# -ne 3 ]]; then
  echo "usage: $0 <owner> '<CI public deploy key>' <storage box host>" >&2
  exit 2
fi
if [[ $EUID -ne 0 ]]; then
  die "run as root"
fi
owner=$1 ci_key=$2 box_host=$3
[[ $owner =~ ^[a-z_][a-z0-9_-]*$ && $owner != deploy ]] || die "bad owner name: $owner"
# One line only: a second line would be a key without the forced command.
[[ $ci_key != *$'\n'* && $ci_key =~ ^ssh-[a-z0-9-]+\ [A-Za-z0-9+/=]+(\ [^\"]*)?$ ]] ||
  die "the CI key must be one public key line (ssh-ed25519 AAAA... comment)"
[[ $box_host =~ ^[a-z0-9-]+(\.[a-z0-9-]+)+$ ]] || die "bad storage box host: $box_host"
box_user=${box_host%%.*}

# Writes stdin to a file only when the content differs. Returns 0 if it wrote.
write_if_changed() {
  local path=$1 mode=$2 tmp
  tmp=$(mktemp)
  cat >"$tmp"
  if [[ -f $path ]] && cmp -s "$tmp" "$path"; then
    rm -f "$tmp"
    return 1
  fi
  install -m "$mode" "$tmp" "$path"
  rm -f "$tmp"
}

step "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ca-certificates curl git make openssl restic unattended-upgrades

step "docker"
install -m 0755 -d /etc/apt/keyrings
if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi
arch=$(dpkg --print-architecture)
# Docker's suite for this release (noble, resolute); apt-get update fails if Docker has none.
codename=$(sed -n 's/^VERSION_CODENAME=//p' /etc/os-release)
[[ -n $codename ]] || die "no VERSION_CODENAME in /etc/os-release"
write_if_changed /etc/apt/sources.list.d/docker.list 0644 <<EOF || true
deb [arch=$arch signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $codename stable
EOF
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
  docker-compose-plugin
compose_version=$(docker compose version --short)
compose_version=${compose_version#v}
if [[ $(printf '%s\n' "$MIN_COMPOSE" "$compose_version" | sort -V | head -n 1) != "$MIN_COMPOSE" ]]; then
  die "Docker Compose $compose_version is older than $MIN_COMPOSE (compose.prod.yaml uses !reset)"
fi
# The local driver rotates logs by default.
if write_if_changed /etc/docker/daemon.json 0644 <<'EOF'; then
{
  "log-driver": "local"
}
EOF
  systemctl restart docker
fi

step "swap (2 GB)"
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  if [[ ! -f /swapfile ]]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab

step "timezone and automatic security updates"
timedatectl set-timezone UTC
write_if_changed /etc/apt/apt.conf.d/52engleks 0644 <<'EOF' || true
// engleks: install security updates daily and reboot at 04:00 UTC when one needs it.
// The stack comes back through restart: unless-stopped.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
EOF

step "owner account: $owner"
if ! id "$owner" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$owner"
fi
usermod -aG sudo "$owner"
owner_home=$(getent passwd "$owner" | cut -d: -f6)
install -d -m 0700 -o "$owner" -g "$owner" "$owner_home/.ssh"
if [[ ! -s $owner_home/.ssh/authorized_keys && -s /root/.ssh/authorized_keys ]]; then
  install -m 0600 -o "$owner" -g "$owner" /root/.ssh/authorized_keys \
    "$owner_home/.ssh/authorized_keys"
fi
[[ -s $owner_home/.ssh/authorized_keys ]] ||
  die "$owner has no SSH key; add one to $owner_home/.ssh/authorized_keys and run again"
# sudo asks for this password, so a leaked SSH key alone does not give root.
if [[ $(passwd -S "$owner" | awk '{print $2}') != P ]]; then
  echo "Set a password for $owner (sudo asks for it):"
  passwd "$owner"
fi

step "deploy account"
if ! id deploy >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" --shell /bin/bash deploy
fi
usermod -aG docker deploy
deploy_home=$(getent passwd deploy | cut -d: -f6)
install -d -m 0700 -o deploy -g deploy "$deploy_home/.ssh"
# CI's key can only run engleks-deploy: no shell, no forwarding, no pty.
printf 'command="/usr/local/bin/engleks-deploy",restrict %s\n' "$ci_key" |
  write_if_changed "$deploy_home/.ssh/authorized_keys" 0600 || true
chown deploy:deploy "$deploy_home/.ssh/authorized_keys"

step "ssh: keys only, no root login"
# 10- sorts before Ubuntu's 50-cloud-init.conf, and sshd keeps the first value it reads.
if write_if_changed /etc/ssh/sshd_config.d/10-engleks.conf 0644 <<'EOF'; then
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
  sshd -t
  # Ubuntu 24.04+ starts ssh.service from ssh.socket; an inactive service reads the file on start.
  systemctl try-reload-or-restart ssh
fi

step "checkout in $ENGLEKS_DIR"
if [[ ! -d $ENGLEKS_DIR/.git ]]; then
  install -d -m 0755 -o deploy -g deploy "$ENGLEKS_DIR"
  runuser -u deploy -- git clone "$REPO_URL" "$ENGLEKS_DIR"
fi

step "deploy and backup scripts"
install -m 0755 -o root -g root "$ENGLEKS_DIR/deploy/server/engleks-deploy" \
  /usr/local/bin/engleks-deploy
install -m 0755 -o root -g root "$ENGLEKS_DIR/deploy/server/engleks-backup" \
  /usr/local/bin/engleks-backup
install -m 0644 -o root -g root "$ENGLEKS_DIR/deploy/server/engleks-backup.service" \
  "$ENGLEKS_DIR/deploy/server/engleks-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now engleks-backup.timer
install -d -m 0700 -o deploy -g deploy "$BACKUP_DIR"

step "storage box access for the backups"
if [[ ! -f $deploy_home/.ssh/storagebox ]]; then
  runuser -u deploy -- ssh-keygen -q -t ed25519 -N "" -C "engleks-backup@$(hostname)" \
    -f "$deploy_home/.ssh/storagebox"
fi
write_if_changed "$deploy_home/.ssh/config" 0600 <<EOF || true
Host storagebox
  HostName $box_host
  User $box_user
  Port 23
  IdentityFile ~/.ssh/storagebox
  IdentitiesOnly yes
EOF
chown deploy:deploy "$deploy_home/.ssh/config"
known_hosts=$deploy_home/.ssh/known_hosts
if ! ssh-keygen -F "[$box_host]:23" -f "$known_hosts" >/dev/null 2>&1; then
  scanned=$(mktemp)
  ssh-keyscan -p 23 "$box_host" >"$scanned" 2>/dev/null
  [[ -s $scanned ]] || die "could not read the host key of $box_host:23"
  echo "Storage box host key fingerprints; compare them with Hetzner's published ones:"
  ssh-keygen -lf "$scanned"
  cat "$scanned" >>"$known_hosts"
  rm -f "$scanned"
  chown deploy:deploy "$known_hosts"
  chmod 0600 "$known_hosts"
fi

cat <<EOF

Setup done.

1. Before closing this session, check in a second one that you can log in as $owner
   and that sudo works (ssh $owner@<server>, then sudo -v). Root login is now off.
2. Install the backup key on the storage box (it asks for the box's password once):
     sudo -iu deploy sh -c 'ssh -p 23 $box_user@$box_host install-ssh-key < ~/.ssh/storagebox.pub'
   The key:
$(sed 's/^/     /' "$deploy_home/.ssh/storagebox.pub")
3. Create .env:
     sudo -iu deploy $ENGLEKS_DIR/deploy/server/bootstrap-env.sh
EOF
