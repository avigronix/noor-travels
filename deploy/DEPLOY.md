# Deploy – Noor Travels Services on the shared VPS

Server: Ubuntu, **system Python 3.10 only** (do not install another Python), PostgreSQL 16, nginx without SSL
(HTTPS at Cloudflare). Port **8020 is used by another app** – this app uses **8030**.
Everything here is separate: own Linux user `noor`, own DB role, own systemd service, own nginx file.

## 1. First deploy

```bash
# user + folders
sudo adduser --system --group --home /var/www/noor-travels noor
sudo mkdir -p /var/www/noor-travels /var/www/noor-travels-backups && sudo chown noor:noor /var/www/noor-travels /var/www/noor-travels-backups
sudo apt install -y python3.10-venv postgresql-client     # venv module for the system Python (if missing)

# code
sudo -u noor git clone <REPO_URL> /var/www/noor-travels
cd /var/www/noor-travels
sudo -u noor mkdir -p uploads logs

# Python venv with the system python3.10
sudo -u noor python3.10 -m venv .venv
sudo -u noor .venv/bin/pip install --upgrade pip
sudo -u noor .venv/bin/pip install -r requirements.txt

# database – own role and database, nothing else touched
sudo -u postgres psql -c "CREATE ROLE noor LOGIN PASSWORD '<STRONG_PASSWORD>';"
sudo -u postgres psql -c "CREATE DATABASE noor_travels OWNER noor;"
sudo -u postgres psql -d noor_travels -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

# settings
sudo -u noor cp .env.example .env && sudo chmod 600 .env
sudo -u noor nano .env
#   APP_ENV=prod   (secure cookie, no /docs)
#   PORT=8030      HOST=127.0.0.1    WORKERS=2
#   SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
#   DATABASE_URL=postgresql://noor:<STRONG_PASSWORD>@127.0.0.1:5432/noor_travels
#   SITE_URL=https://<domain>   (used in receipt links and the portal link in WhatsApp)
#   OFFICE_EMAIL / OFFICE_HOURS / OFFICE_GSTIN / OFFICE_RA_LICENCE when the office confirms them
#   No quotes and no comments after a value – systemd reads this file too.

sudo -u noor .venv/bin/python migrate.py
sudo -u noor .venv/bin/python migrate.py create-owner

# CSS: static/css/app.css is committed. To rebuild on the server (optional):
#   curl -sSLo tailwindcss https://github.com/tailwindlabs/tailwindcss/releases/download/v4.1.18/tailwindcss-linux-x64
#   chmod +x tailwindcss && ./tailwindcss -i static/css/input.css -o static/css/app.css --minify

# service
sudo cp deploy/noor-travels.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now noor-travels
sudo systemctl status noor-travels          # should be active (running)
curl -s http://127.0.0.1:8030/health        # {"ok":true}

# nginx – its own file, straight into sites-enabled
sudo cp deploy/nginx-noor-travels.conf /etc/nginx/sites-enabled/noortrip.in      # server_name is noortrip.in
sudo nginx -t && sudo systemctl reload nginx

# cron: reminders every 30 min + nightly backup
sudo chmod +x deploy/backup.sh
(crontab -l; grep '^[*0-9]' deploy/crontab.txt) | crontab -     # append – never replace other apps' jobs
sudo timedatectl set-timezone Asia/Kolkata     # only if the other app is fine with it; the app works in IST anyway
```

## 2. Cloudflare

1. Add the domain to Cloudflare; at the registrar set the two Cloudflare name servers.
2. DNS: `A  @  <server IP>` and `A  www  <server IP>`, both **Proxied** (orange cloud).
3. SSL/TLS mode: **Flexible** (nginx has no certificate). If you later add a Cloudflare Origin Certificate to nginx, switch to **Full (strict)**.
4. SSL/TLS → Edge Certificates: **Always Use HTTPS = On**.
5. Caching: leave `/app/*`, `/my/*`, `/r/*`, `/login` uncached (Cloudflare does not cache HTML by default – do not add a "cache everything" rule).

## 3. Update routine

```bash
cd /var/www/noor-travels
sudo -u noor git pull
sudo -u noor .venv/bin/pip install -r requirements.txt
sudo -u noor .venv/bin/python migrate.py          # applies only new migrations/00N_*.sql
sudo systemctl restart noor-travels
sudo systemctl status noor-travels --no-pager
```

Never edit a migration that has already run – add a new numbered file.

## 4. Checks after deploy

- `https://<domain>/` website, `/login` staff login, `/my` customer portal, `/health`.
- Log in as owner → Settings: GSTIN, RA licence, SAC code, next file / ticket number, bank accounts, staff users.
- Messages → Rules / Wording: check the texts and the language.
- `ls /var/www/noor-travels-backups` the morning after – a `db_*.dump` file should be there.
- Restore test once: `pg_restore --list /var/www/noor-travels-backups/db_<date>.dump | head`.

## 5. Logs

```bash
journalctl -u noor-travels -f                 # app
tail -f /var/www/noor-travels/logs/jobs.log       # reminders
tail -f /var/log/nginx/noor-travels.error.log
```
