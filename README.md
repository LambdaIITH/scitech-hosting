# SciTech hosting

How the SciTech club sites are hosted on Lambda's server. Each site's repo builds itself with the shared workflow
here, the server pulls the finished files, and nginx serves every site behind the Cloudflare tunnel.

```
site repo, main  ──GitHub Actions (build.yml)──►  deploy branch
deploy branch    ──update-sites, every 5 min──►   /srv/sites/<name>/current
/srv/sites       ──nginx, 127.0.0.1:8100──►        Cloudflare tunnel  ──►  https://<name>.iith.dev
```

The server needs no build tools, no GitHub credentials and no open ports: it only pulls public `deploy` branches,
and the tunnel connects out to Cloudflare. GitHub holds no key to the server. Nothing in this repo is secret.

| File | What it does |
| --- | --- |
| `sites` | The sites: a name (which is also the address `<name>.iith.dev` and the folder `/srv/sites/<name>`) and a repo. |
| `update-sites` | Runs every 5 minutes on the server. Fetches each site's `deploy` branch and switches to a new build in one atomic step, keeping the two before it. A build without `index.html` is refused, and one failing site never stops the others. |
| `nginx/scitech.conf` | One nginx server for every site. Shared headers and compression; per-site rules (Glitch's stricter headers, old-link redirects) are the maps at the top. |
| `install.sh` | The step that needs sudo: creates `/srv/sites`, installs the nginx config, adds the cron job. |
| `.github/workflows/build.yml` | The build every site uses: Hugo or Node, then a push to the site's `deploy` branch. |
| `tests/` | Checks for `update-sites` and the nginx config, run by GitHub Actions on every push. |

## A site's workflow

Each site repo has `.github/workflows/deploy.yml` along these lines (use the repo's main branch name):

```yaml
name: Build and publish
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
permissions: {}
jobs:
  build:
    uses: LambdaIITH/scitech-hosting/.github/workflows/build.yml@main
    permissions:
      contents: write # to publish the build to the deploy branch
    with:
      url: https://aero.iith.dev/
      hugo: 0.87.0
```

| Input | Meaning |
| --- | --- |
| `url` | The site's address. Hugo builds get it as `--baseURL`. |
| `hugo` | Hugo version for a Hugo site (the extended edition, checked against Hugo's published checksums). |
| `node` | Node version for a site with npm packages; runs `npm ci` first. |
| `command` | Build command. Default `hugo --minify --gc`. |
| `output` | The folder the build writes. Default `public`. |

Pushes to the main branch publish; pull requests only build, as a check. A failed build never touches `deploy`,
so the live site stays on its last good build.

## Setting up the server

Once, as the account that will own the sites (`lambda`):

```sh
git clone https://github.com/LambdaIITH/scitech-hosting.git ~/scitech-hosting
sudo ~/scitech-hosting/install.sh
```

It creates `/srv/sites`, installs `nginx/scitech.conf` as `/etc/nginx/conf.d/scitech.conf` (if `nginx -t` rejects
it, the previous config stays), reloads nginx, adds the cron job and runs the first update. The server's other
nginx sites keep their own ports and aren't touched.

## Moving a site from Netlify

1. Merge the site's pull request that adds the workflow. Its first run on the main branch creates `deploy`.
2. Within 5 minutes the site is on the server. Check it there:
   `curl -s -H 'Host: aero.iith.dev' http://127.0.0.1:8100/ | head`, or read `journalctl -t update-sites`.
3. In Cloudflare, delete the DNS record for the site (it points at Netlify), then open the tunnel that serves
   glitch.iith.dev (Zero Trust → Networks → Tunnels) and add a public hostname: subdomain `aero`, domain
   `iith.dev`, service `HTTP` `127.0.0.1:8100`. Cloudflare creates the new DNS record itself.
4. Open the site and click around. To go back, remove the public hostname and restore the Netlify record.
5. After a week without problems, delete the site on Netlify.

Glitch's hostname already goes through the tunnel, to the old Next.js app on port 3000. To switch it, edit that
public hostname's service to `127.0.0.1:8100`, check the site, then run `pm2 stop glitch-frontend && pm2 save`.

## Adding or removing a site

To add one: give its repo the workflow above and merge it; add a line to `sites` here and merge; on the server run
`git -C ~/scitech-hosting pull`; then add its public hostname in the tunnel. nginx needs no change: any
`<name>.iith.dev` is served from `/srv/sites/<name>/current`.

To remove one: delete its line, pull on the server, remove its public hostname, then `rm -rf /srv/sites/<name>`.

## Rolling back

The clean way is to revert the bad commit in the site's repo: the fixed build is live within minutes. To roll back
at once, on the server:

```sh
cd /srv/sites/aero && ls -t releases/   # the live build first, then the two before it
ln -sfn releases/<older build> current.tmp && mv -T current.tmp current
```

That stays until the site's next build arrives.

## Changing the nginx rules

Edit `nginx/scitech.conf` and merge, then on the server:
`git -C ~/scitech-hosting pull && sudo ~/scitech-hosting/install.sh`. If nginx rejects the change, `install.sh`
puts the previous config back.

Updates are logged to the journal (`journalctl -t update-sites`, one line per site that changed or failed);
nginx logs to `/var/log/nginx/`.

## Security

- Everyone who can merge here changes what every site's build runs (`build.yml` is used from `main`) and, after a
  pull, what runs on the server. Keep write access to Lambda maintainers and protect `main`.
- `install.sh` runs as root: read changes to it before running it.
- Every site sends `X-Content-Type-Options: nosniff` and `Referrer-Policy: strict-origin-when-cross-origin`, and
  only the site itself may show it in a frame. `/api/` is readable from any site, for the SciTech feeds.
- Files are served with `Cache-Control: max-age=0, must-revalidate`, as on Netlify, so a new build shows at once.
  Only Astro's `/_astro/` files, named after their content, are cached for a year.

## Checks

```sh
python3 -m unittest discover -s tests
```

The nginx test starts its own nginx on a free localhost port (set `NGINX` and `MIME_TYPES` if it isn't installed
in the usual place) and is skipped when there is no nginx.
