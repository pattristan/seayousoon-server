# Deploying to a VPS (IONOS or similar)

The shared IONOS *webhosting* can't run a persistent Python process — but an
IONOS **VPS** (or Hetzner etc.) can. Smallest tier is plenty: this backend is
one small process and one SQLite file.

The recipe below assumes **Ubuntu 24.04 LTS** on the VPS and the subdomain
**crew.oconnell-connect.de** (change to taste).

---

## 1. Order the VPS & point DNS at it

1. In the IONOS panel: order the smallest VPS with **Ubuntu 24.04**.
   Note its public IP address.
2. Still in IONOS (where your domain DNS lives): add an **A record**
   `crew` → `<the VPS IP>`. (Propagation is usually minutes.)

## 2. First login & basic setup

```bash
ssh root@<VPS-IP>

# a dedicated unprivileged user that will run the service
adduser --disabled-password --gecos "" seayousoon
mkdir -p /home/seayousoon/data
chown -R seayousoon:seayousoon /home/seayousoon

apt update && apt install -y python3-venv rsync
```

## 3. Install Caddy (automatic HTTPS)

Caddy fetches and renews the Let's Encrypt certificate by itself — no
certbot, no renewal cron.

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
```

## 4. Copy the code up (run on your Mac)

```bash
cd /Users/patrickoconnell/Developer
rsync -av --exclude .venv --exclude data.db seayousoon-server/ \
  root@<VPS-IP>:/home/seayousoon/seayousoon-server/
```

## 5. Python environment (on the VPS)

```bash
chown -R seayousoon:seayousoon /home/seayousoon/seayousoon-server
su - seayousoon -c "
  cd ~/seayousoon-server &&
  python3 -m venv .venv &&
  .venv/bin/pip install -r requirements.txt
"
```

## 6. Wire up the two config files

```bash
# systemd service (keeps it running, restarts on reboot/crash)
cp /home/seayousoon/seayousoon-server/deploy/seayousoon.service /etc/systemd/system/
# IMPORTANT: put a real secret in the unit file first
sed -i "s/CHANGE-ME/$(openssl rand -hex 32)/" /etc/systemd/system/seayousoon.service
systemctl daemon-reload
systemctl enable --now seayousoon

# Caddy: HTTPS reverse proxy
cp /home/seayousoon/seayousoon-server/deploy/Caddyfile /etc/caddy/Caddyfile
systemctl reload caddy
```

## 7. Check it works

```bash
systemctl status seayousoon        # should say "active (running)"
curl -s https://crew.oconnell-connect.de/ | head -5   # login page HTML
```

Then open https://crew.oconnell-connect.de in a browser, register an account
and generate a code.

## 8. Point the iOS app at it

In `PairingService.swift`, change one line:

```swift
enum Pairing {
    static let service: any PairingService =
        RemotePairingService(baseURL: URL(string: "https://crew.oconnell-connect.de")!)
}
```

No ATS exception needed — it's real HTTPS.

## 9. Backups (one cron line)

The entire state is one SQLite file. On the VPS:

```bash
crontab -e   # as root, add:
15 3 * * * sqlite3 /home/seayousoon/data/data.db ".backup /home/seayousoon/data/backup-$(date +\%a).db"
```

That keeps seven rotating daily backups (Mon…Sun). Copy them off the box
occasionally (`rsync` them to your Mac) for real safety.

## Updating the server later

```bash
# on the Mac: push new code
rsync -av --exclude .venv --exclude data.db seayousoon-server/ \
  root@<VPS-IP>:/home/seayousoon/seayousoon-server/
# on the VPS: restart
systemctl restart seayousoon
```

---

## Before real users (hardening checklist)

- [ ] Rate-limit `/login` and `/pairing-codes/redeem` (5 tries → cooldown)
- [ ] Swap salted SHA-256 for bcrypt/argon2 PIN hashing
- [ ] `ufw allow 22,80,443/tcp && ufw enable` (firewall)
- [ ] SSH key login only (`PasswordAuthentication no` in sshd_config)
- [ ] Unattended security updates: `apt install unattended-upgrades`
