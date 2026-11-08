"""Runs update-sites against local git repos standing in for GitHub."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'update-sites'


def git(*args, cwd=None):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


class UpdateSites(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.github = self.tmp / 'github'
        self.root = self.tmp / 'srv'
        self.root.mkdir()

    def publish(self, repo, files):
        """Force-pushes a one-commit deploy branch, the way the shared build does."""
        bare = self.github / f'{repo}.git'
        if not bare.exists():
            bare.parent.mkdir(parents=True, exist_ok=True)
            git('init', '-q', '--bare', str(bare))
        work = Path(tempfile.mkdtemp(dir=self.tmp))
        for name, text in files.items():
            (work / name).parent.mkdir(parents=True, exist_ok=True)
            (work / name).write_text(text)
        git('init', '-q', '-b', 'deploy', cwd=work)
        git('add', '-A', cwd=work)
        git('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '-m', 'build', cwd=work)
        git('push', '-q', '-f', str(bare), 'deploy', cwd=work)

    def update(self, *lines):
        sites = self.tmp / 'sites'
        sites.write_text('\n'.join(lines) + '\n')
        env = dict(os.environ, SITES=str(sites), ROOT=str(self.root), GITHUB=f'file://{self.github}')
        return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)

    def live(self, name):
        return (self.root / name / 'current' / 'index.html').read_text()

    def releases(self, name):
        return sorted(p.name for p in (self.root / name / 'releases').iterdir())

    def test_puts_new_builds_live_and_keeps_two_older(self):
        self.publish('LambdaIITH/aero-website', {'index.html': 'aero 1'})
        self.publish('LambdaIITH/glitch-website', {'index.html': 'glitch 1', 'events/index.html': 'e'})
        sites = ('# comment', '', 'aero  LambdaIITH/aero-website', 'glitch LambdaIITH/glitch-website')
        run = self.update(*sites)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(self.live('aero'), 'aero 1')
        self.assertEqual((self.root / 'glitch/current/events/index.html').read_text(), 'e')
        self.assertTrue((self.root / 'aero/current').is_symlink())

        run = self.update(*sites)  # nothing new: nothing printed, nothing unpacked
        self.assertEqual((run.returncode, run.stdout), (0, ''))
        self.assertEqual(len(self.releases('aero')), 1)

        for n in (2, 3, 4):
            self.publish('LambdaIITH/aero-website', {'index.html': f'aero {n}'})
            self.assertEqual(self.update(*sites).returncode, 0)
            self.assertEqual(self.live('aero'), f'aero {n}')
        self.assertEqual(len(self.releases('aero')), 3)
        self.assertEqual(self.live('glitch'), 'glitch 1')

    def test_keeps_the_live_build_when_a_new_one_has_no_index(self):
        self.publish('LambdaIITH/aero-website', {'index.html': 'good'})
        self.assertEqual(self.update('aero LambdaIITH/aero-website').returncode, 0)
        self.publish('LambdaIITH/aero-website', {'other.html': 'broken build'})
        run = self.update('aero LambdaIITH/aero-website')
        self.assertEqual(run.returncode, 1)
        self.assertIn('no index.html', run.stdout)
        self.assertEqual(self.live('aero'), 'good')
        self.assertEqual(len(self.releases('aero')), 1)  # no half-unpacked leftovers

    def test_a_failing_site_never_stops_the_others(self):
        self.publish('LambdaIITH/torque-website', {'index.html': 'torque'})
        run = self.update('missing LambdaIITH/missing-website', 'torque LambdaIITH/torque-website')
        self.assertEqual(run.returncode, 1)
        self.assertIn('missing: not updated', run.stdout)
        self.assertEqual(self.live('torque'), 'torque')

    def test_skips_lines_that_are_not_a_plain_name_and_repo(self):
        self.publish('LambdaIITH/aero-website', {'index.html': 'aero'})
        run = self.update('../escape LambdaIITH/aero-website', 'Aero LambdaIITH/aero-website',
                          'aero https://example.com/x', 'aero2 LambdaIITH/../aero-website')
        self.assertEqual(run.returncode, 1)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['.update-sites.lock'])
        self.assertFalse((self.tmp / 'escape').exists())


if __name__ == '__main__':
    unittest.main()
