"""Validate the resolved LAN port binding using synthetic configuration only."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('docker'), 'Docker CLI unavailable')
class LanComposeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lan-compose-')
        self.root = Path(self.temp.name)
        shutil.copy2(Path(__file__).resolve().parents[1] / 'docker-compose.yml', self.root)
        (self.root / 'backend.env').write_text('', encoding='utf-8')
        self.env = dict(os.environ)
        self.env.pop('LAN_BIND_IP', None)
        self.env.pop('LAN_PORT', None)

    def tearDown(self):
        self.temp.cleanup()

    def config(self):
        return subprocess.run(['docker', 'compose', 'config', '--format', 'json'],
                              cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30)

    def test_unset_bind_address_rejects_configuration(self):
        result = self.config()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LAN_BIND_IP', result.stderr)

    def test_explicit_private_ipv4_has_no_wildcard_binding(self):
        self.env['LAN_BIND_IP'] = '192.168.1.100'
        result = self.config()
        self.assertEqual(result.returncode, 0, result.stderr)
        ports = json.loads(result.stdout)['services']['caddy']['ports']
        lan = [port for port in ports if port['target'] == 8090]
        self.assertEqual(len(lan), 1)
        self.assertEqual(lan[0]['host_ip'], '192.168.1.100')
        self.assertEqual(str(lan[0]['published']), '8090')

    def test_loopback_development_address_and_custom_lan_port(self):
        self.env.update(LAN_BIND_IP='127.0.0.1', LAN_PORT='18090')
        result = self.config()
        self.assertEqual(result.returncode, 0, result.stderr)
        lan = next(port for port in json.loads(result.stdout)['services']['caddy']['ports']
                   if port['target'] == 8090)
        self.assertEqual(lan['host_ip'], '127.0.0.1')
        self.assertEqual(str(lan['published']), '18090')
