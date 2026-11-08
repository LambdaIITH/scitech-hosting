"""Serves stand-in sites through nginx/scitech.conf and checks routing, headers and redirects.

Needs an nginx binary: NGINX (default: nginx on PATH) and its mime.types (MIME_TYPES, default
/etc/nginx/mime.types). The test is skipped without one, unless REQUIRE_NGINX is set (as in CI).
"""
import gzip
import http.client
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

CONF = Path(__file__).resolve().parent.parent / 'nginx' / 'scitech.conf'
NGINX = os.environ.get('NGINX') or shutil.which('nginx') or (os.path.exists('/usr/sbin/nginx') and '/usr/sbin/nginx')
MIME = os.environ.get('MIME_TYPES', '/etc/nginx/mime.types')

SITES = {
    'aero/index.html': '<h1>aero home</h1>',
    'aero/404.html': '<h1>aero not found</h1>',
    'aero/about/index.html': 'about aero',
    'aero/css/site.css': 'body { color: black; }\n' * 100,
    'aero/api/events.json': '{"club": "Aero", "version": 1, "events": []}',
    'glitch/index.html': '<h1>glitch home</h1>',
    'glitch/404.html': '<h1>glitch not found</h1>',
    'glitch/scoreboard/index.html': 'scoreboard',
    'glitch/_astro/page.abc123.js': 'console.log(1)',
}


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@unittest.skipUnless(NGINX or os.environ.get('REQUIRE_NGINX'), 'nginx is not installed')
class Nginx(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        srv = cls.tmp / 'srv'
        for path, text in SITES.items():
            name, rest = path.split('/', 1)
            f = srv / name / 'releases' / 'r1' / rest
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        for name in {p.split('/')[0] for p in SITES}:
            (srv / name / 'current').symlink_to('releases/r1')  # as update-sites leaves it

        cls.port = free_port()
        conf = CONF.read_text().replace('/srv/sites', str(srv)).replace('127.0.0.1:8100', f'127.0.0.1:{cls.port}')
        (cls.tmp / 'scitech.conf').write_text(conf)
        temps = ' '.join(f'{d}_temp_path {cls.tmp}/{d};' for d in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'))
        (cls.tmp / 'nginx.conf').write_text(f"""
            worker_processes 1;
            pid {cls.tmp}/nginx.pid;
            error_log {cls.tmp}/error.log;
            events {{ worker_connections 64; }}
            http {{
                include {MIME};
                default_type application/octet-stream;
                access_log off;
                {temps}
                include {cls.tmp}/scitech.conf;
            }}""")
        cls.nginx = subprocess.Popen([NGINX, '-p', f'{cls.tmp}/', '-c', f'{cls.tmp}/nginx.conf', '-g', 'daemon off;'],
                                     stderr=subprocess.PIPE, text=True)
        for _ in range(100):
            try:
                socket.create_connection(('127.0.0.1', cls.port), timeout=0.1).close()
                return
            except OSError:
                if cls.nginx.poll() is not None:
                    break
                time.sleep(0.05)
        cls.tearDownClass()
        raise RuntimeError(f'nginx did not start: {cls.nginx.stderr.read()}')

    @classmethod
    def tearDownClass(cls):
        if cls.nginx.poll() is None:
            cls.nginx.terminate()
            cls.nginx.wait(timeout=10)
        shutil.rmtree(cls.tmp)

    def get(self, host, path, **headers):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        c.request('GET', path, headers={'Host': host, **headers})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def test_each_name_gets_its_own_site(self):
        r, body = self.get('aero.iith.dev', '/')
        self.assertEqual((r.status, body), (200, b'<h1>aero home</h1>'))
        r, body = self.get('glitch.iith.dev', '/')
        self.assertEqual(body, b'<h1>glitch home</h1>')

    def test_unknown_names_get_nothing(self):
        for host in ('example.com', 'nosuchclub.iith.dev', 'iith.dev', 'a.b.iith.dev'):
            r, body = self.get(host, '/')
            self.assertEqual(r.status, 404, host)
            self.assertNotIn(b'home', body, host)

    def test_security_and_cache_headers(self):
        r, _ = self.get('aero.iith.dev', '/')
        self.assertEqual(r.getheader('Content-Security-Policy'), "frame-ancestors 'self'")
        self.assertEqual(r.getheader('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(r.getheader('Referrer-Policy'), 'strict-origin-when-cross-origin')
        self.assertEqual(r.getheader('Cache-Control'), 'public, max-age=0, must-revalidate')
        self.assertIsNone(r.getheader('Access-Control-Allow-Origin'))
        self.assertIsNone(r.getheader('Server').partition('/')[2] or None)  # no version number
        r, _ = self.get('glitch.iith.dev', '/')
        self.assertIn("frame-ancestors 'none'", r.getheader('Content-Security-Policy'))
        self.assertIn("form-action 'self'", r.getheader('Content-Security-Policy'))
        r, _ = self.get('glitch.iith.dev', '/_astro/page.abc123.js')
        self.assertEqual(r.getheader('Cache-Control'), 'public, max-age=31536000, immutable')

    def test_feeds_are_open_to_other_sites(self):
        r, body = self.get('aero.iith.dev', '/api/events.json')
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader('Access-Control-Allow-Origin'), '*')
        self.assertEqual(r.getheader('Content-Type'), 'application/json')

    def test_not_found_uses_the_sites_own_page_and_headers(self):
        for path in ('/nope', '/_redirects', '/_headers'):
            r, body = self.get('aero.iith.dev', path)
            self.assertEqual((r.status, body), (404, b'<h1>aero not found</h1>'), path)
            self.assertEqual(r.getheader('X-Content-Type-Options'), 'nosniff')

    def test_redirects(self):
        for path in ('/leaderboard', '/leaderboard/'):
            r, _ = self.get('glitch.iith.dev', path)
            self.assertEqual((r.status, r.getheader('Location')), (301, '/scoreboard/'), path)
        r, _ = self.get('aero.iith.dev', '/leaderboard')  # only Glitch had that page
        self.assertEqual(r.status, 404)
        r, _ = self.get('aero.iith.dev', '/about')  # folder without slash: a relative redirect, not 127.0.0.1
        self.assertEqual((r.status, r.getheader('Location')), (301, '/about/'))

    def test_text_is_compressed(self):
        r, body = self.get('aero.iith.dev', '/css/site.css', **{'Accept-Encoding': 'gzip'})
        self.assertEqual(r.getheader('Content-Encoding'), 'gzip')
        self.assertIn('Accept-Encoding', r.getheader('Vary'))
        self.assertEqual(gzip.decompress(body), SITES['aero/css/site.css'].encode())


if __name__ == '__main__':
    unittest.main()
