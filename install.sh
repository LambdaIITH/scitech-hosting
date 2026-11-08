#!/bin/bash
# Server setup for the SciTech sites. Run it from this checkout with sudo, once at the start and again whenever
# nginx/scitech.conf changes:   sudo ./install.sh
# It creates /srv/sites for the account that runs it, installs the nginx config (putting the old one back if nginx
# rejects the new one), and adds the cron line that runs update-sites every 5 minutes as that account.
set -euo pipefail
cd "$(dirname "$0")"
here=$PWD
owner=${SUDO_USER:?run this with sudo, from the account that will own the sites}

install -d -o "$owner" -g "$owner" -m 755 /srv/sites

conf=/etc/nginx/conf.d/scitech.conf
[ -f "$conf" ] && cp -p "$conf" "$conf.previous"
install -o root -g root -m 644 nginx/scitech.conf "$conf"
if ! nginx -t; then
    echo "nginx rejected nginx/scitech.conf, so the previous config is back in place." >&2
    if [ -f "$conf.previous" ]; then mv "$conf.previous" "$conf"; else rm -f "$conf"; fi
    exit 1
fi
rm -f "$conf.previous"
systemctl reload nginx

job="*/5 * * * * $here/update-sites 2>&1 | logger -t update-sites"
{ crontab -u "$owner" -l 2>/dev/null | grep -vF "$here/update-sites" || true; echo "$job"; } | crontab -u "$owner" -

echo "First run (a site whose repo has no deploy branch yet shows up as not updated):"
sudo -u "$owner" "$here/update-sites" || true
echo "Done: nginx serves /srv/sites on 127.0.0.1:8100, and update-sites runs every 5 minutes as $owner."
