"""Phục vụ file tĩnh của dashboard và hợp đồng id giữa index.html và app.js (không cần trình duyệt)."""
import re
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from test_dashboard_api import FakeRPC, URLS
from dashboard_api import DashboardAPI, create_http_server

WEB = Path(__file__).resolve().parent.parent / 'web'


class StaticServingTests(unittest.TestCase):
    def setUp(self):
        self.server = create_http_server(DashboardAPI(URLS, transport=FakeRPC()), port=0, static_dir=WEB)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), thread.join(3)))
        self.host = f'127.0.0.1:{self.server.server_port}'

    def get(self, path, host=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        conn.putrequest('GET', path, skip_host=True)
        conn.putheader('Host', host or self.host)
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status, response.getheader('Content-Type'), response.getheader('Content-Security-Policy'), body

    def test_index_and_assets(self):
        status, ctype, csp, body = self.get('/')
        self.assertEqual((status, ctype), (200, 'text/html; charset=utf-8'))
        self.assertIn("default-src 'self'", csp)
        self.assertIn('lang="vi"', body.decode())
        self.assertEqual(self.get('/app.js')[1], 'text/javascript; charset=utf-8')
        self.assertEqual(self.get('/style.css')[1], 'text/css; charset=utf-8')

    def test_no_traversal_dotfiles_or_other_types(self):
        for path in ('/../dashboard_api.py', '/%2e%2e/dashboard_api.py', '/.git/config', '/..%2fREADME.md',
                     '/nope.html', '/app.js/..', '/%5c..%5cdashboard_api.py', '/docs/plan.md'):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_host_check_still_applies_to_static_files(self):
        self.assertEqual(self.get('/', host='evil.example:80')[0], 403)

    def test_api_routes_unchanged(self):
        status, ctype, _, body = self.get('/api/session')
        self.assertEqual((status, ctype), (200, 'application/json; charset=utf-8'))
        self.assertEqual(self.get('/api/unknown')[0], 404)


class HtmlContractTests(unittest.TestCase):
    def test_every_id_used_by_app_js_exists_in_index(self):
        html = (WEB / 'index.html').read_text(encoding='utf-8')
        js = (WEB / 'app.js').read_text(encoding='utf-8')
        ids = set(re.findall(r"\$\('#([\w-]+)'\)", js))
        self.assertTrue(ids)
        missing = sorted(i for i in ids if f'id="{i}"' not in html)
        self.assertEqual(missing, [])

    def test_no_inline_style_or_script_that_csp_would_block(self):
        html = (WEB / 'index.html').read_text(encoding='utf-8')
        js = (WEB / 'app.js').read_text(encoding='utf-8')
        self.assertNotIn(' style=', html)
        self.assertNotIn('style:', js.replace('// ', ''))
        self.assertIsNone(re.search(r'<script(?![^>]*\bsrc=)', html))


if __name__ == '__main__':
    unittest.main()
