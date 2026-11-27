"""Kiểm thử trình duyệt thật cho dashboard (W10) — KHÔNG nằm trong `unittest discover`.

Tự khởi chạy run_dashboard.py trên cổng riêng + DB tạm (không đụng data/ của buổi demo),
điều khiển trình duyệt bằng Playwright theo kịch bản demo (docs/DASHBOARD_SPEC.md mục 6/7),
chụp ảnh từng bước và ghi biên bản Markdown. Cần lifecycle.py (bộ quản lý tiến trình của SV2).

Cài một lần:   pip install playwright
Chạy bằng Chrome/Edge có sẵn trên máy (không cần tải trình duyệt):
    python tests/browser_check.py --browser msedge
    python tests/browser_check.py --browser chrome
Chromium của Playwright:   python -m playwright install chromium  rồi  --browser chromium
Thêm --headed để xem trình duyệt chạy. Kết quả: browser_evidence/<thời gian>/report.md + *.png.
Mỗi ca chỉ ghi PASS khi có kiểm tra thật; ca lỗi ghi FAIL kèm lý do, không bỏ qua.
"""
import argparse
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def free_port(start: int) -> int:
    for port in range(start, start + 400):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise RuntimeError('Không tìm được cổng trống')


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def fetch_json(url: str):
    import json
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


def stop_dashboard(proc) -> None:
    """Dừng nhẹ để run_dashboard dọn các child; chỉ kill cứng khi quá hạn."""
    if proc.poll() is not None:
        return
    try:
        if os.name == 'nt':
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
        proc.wait(timeout=30)
    except Exception:
        proc.kill()


class Report:
    def __init__(self, out: Path):
        self.out, self.rows, self.page = out, [], None

    def shot(self, name: str) -> str:
        path = self.out / f'{name}.png'
        if self.page is not None:
            self.page.screenshot(path=str(path), full_page=True)
        return path.name

    def step(self, case: str, title: str, fn) -> bool:
        started = time.time()
        try:
            detail = fn() or ''
            result = 'PASS'
        except Exception as exc:  # AssertionError hoặc Timeout của Playwright
            detail, result = f'{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ""}', 'FAIL'
            try:
                self.shot(f'FAIL_{case}')
            except Exception:
                pass
        self.rows.append((case, title, result, detail, round(time.time() - started, 1)))
        print(f'[{result}] {case} {title}' + (f' — {detail}' if detail else ''), flush=True)
        return result == 'PASS'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--browser', default='chromium', choices=['chromium', 'chrome', 'msedge'])
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--out', default=None, help='thư mục kết quả (mặc định browser_evidence/<thời gian>)')
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('Thiếu Playwright: pip install playwright'); return 2
    if not (ROOT / 'lifecycle.py').exists():
        print('Thiếu lifecycle.py (bộ quản lý tiến trình của SV2): chưa thể kiểm thử lifecycle qua web.'); return 2

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    out = Path(args.out) if args.out else ROOT / 'browser_evidence' / stamp
    out.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.TemporaryDirectory(prefix='htpt2pc-', ignore_cleanup_errors=True)
    data_dir, log_dir = Path(tmp.name) / 'data', Path(tmp.name) / 'logs'
    dash_port = free_port(18080)
    base = free_port(18200)
    while not all(not port_open(base + i) for i in range(3)):
        base = free_port(base + 3)
    url = f'http://127.0.0.1:{dash_port}/'
    ports = {'coordinator': base, 'airline': base + 1, 'hotel': base + 2}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
    dash = subprocess.Popen([sys.executable, 'run_dashboard.py', '--port', str(dash_port), '--base-port', str(base),
                             '--data-dir', str(data_dir), '--log-dir', str(log_dir)], cwd=ROOT,
                            stdout=open(out / 'dashboard_stdout.log', 'wb'), stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=flags,
                            env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    rep = Report(out)
    page_errors, max_inflight = [], {'now': 0, 'max': 0}
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                nodes = fetch_json(url + 'api/overview')['nodes']
                if all(n['lifecycle'].get('state') == 'running' and not n['stale'] for n in nodes.values()):
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            print('Cụm dịch vụ không lên đủ trong 40 giây. Nội dung dashboard_stdout.log:')
            stop_dashboard(dash)
            print((out / 'dashboard_stdout.log').read_text(encoding='utf-8', errors='replace')[-2000:])
            return 3

        with sync_playwright() as pw:
            kind = pw.chromium
            browser = kind.launch(headless=not args.headed, **({} if args.browser == 'chromium' else {'channel': args.browser}))
            ctx = browser.new_context(viewport={'width': 1280, 'height': 800}, locale='vi-VN')
            page = ctx.new_page()
            rep.page = page
            page.set_default_timeout(25000)
            page.on('dialog', lambda d: d.accept())
            page.on('pageerror', lambda e: page_errors.append(str(e)))

            def on_req(r):
                if r.url.endswith('/api/overview'):
                    max_inflight['now'] += 1; max_inflight['max'] = max(max_inflight['max'], max_inflight['now'])
            def on_done(r):
                if r.url.endswith('/api/overview'):
                    max_inflight['now'] -= 1
            page.on('request', on_req); page.on('requestfinished', on_done); page.on('requestfailed', on_done)

            # ---------- tiện ích ----------
            def wait_js(expr, arg=None, timeout=25000):
                page.wait_for_function(expr, arg=arg, timeout=timeout)

            def wait_text(sel, text, timeout=25000):
                wait_js('([s,t]) => { const e = document.querySelector(s); return !!e && e.textContent.includes(t); }', [sel, text], timeout)

            def wait_svc(name, text, timeout=30000):
                wait_text(f'.service[data-name={name}] [data-role=life]', text, timeout)

            def avail(name):
                return page.evaluate("(n) => { const e = document.querySelector(`.inventory[data-name=${n}] .bignum`); return e ? Number(e.textContent) : null; }", name)

            def wait_avail(name, value, timeout=20000):
                wait_js("([n, v]) => { const e = document.querySelector(`.inventory[data-name=${n}] .bignum`); return !!e && Number(e.textContent) === v; }", [name, value], timeout)

            def body(name):
                return page.inner_text(f'.inventory[data-name={name}] [data-role=body]')

            def rows_for(token):
                return page.evaluate("(t) => [...document.querySelectorAll('#tx-body tr')].filter(tr => tr.children[1].textContent === t).length", token)

            def cur_token():
                return page.input_value('#bk-token')

            def book(hotel='DEFAULT', token=None, page_=None):
                pg = page_ or page
                pg.select_option('#bk-hotel', hotel)
                if token:
                    pg.fill('#bk-token', token)
                tok = pg.input_value('#bk-token')
                pg.click('#bk-submit')
                return tok

            def wait_result(text, timeout=30000):
                wait_text('#bk-result', text, timeout)

            def arm(service, point, mode='crash'):
                page.select_option('#ft-service', service); page.select_option('#ft-point', point); page.select_option('#ft-mode', mode)
                page.click('#ft-submit'); wait_text('#ft-status', 'Đã arm')

            def svc_click(name, action):
                page.click(f'.service[data-name={name}] button[data-action={action}]')

            page.goto(url)
            page.wait_for_selector('#poll-state:has-text("Đang cập nhật")')

            # ---------- các ca ----------
            def a00():
                for n in ('coordinator', 'airline', 'hotel'):
                    wait_svc(n, 'Đang chạy')
                wait_avail('airline', 5); wait_avail('hotel', 5)
                for n, pt in ports.items():
                    wait_text(f'.service[data-name={n}] h3', f'127.0.0.1:{pt}')
                width = page.evaluate('[document.documentElement.scrollWidth, window.innerWidth]')
                assert width[0] <= width[1] + 1, f'tràn ngang ở 1280px: {width}'
                rep.shot('00_khoi_dong')
                return 'ba node Đang chạy; tiêu đề hiện đúng cổng thật; mỗi RM 5 DEFAULT; không tràn ngang ở 1280px'
            rep.step('A00', 'Khởi động: ba node chạy, tồn 5/5', a00)

            state = {}
            def a01():
                state['t1'] = book()
                wait_result('COMMITTED — đủ ACK')
                wait_avail('airline', 4); wait_avail('hotel', 4)
                wait_js("() => !!document.querySelector('#tx-body tr')")
                rep.shot('01_dat_thanh_cong')
                return f'token {state["t1"]}: COMMITTED, ACK 2/2, tồn 5→4 hai bên'
            rep.step('A01', 'Đặt combo mới → COMMITTED, tồn 5→4', a01)

            def a03_reload():
                page.reload(); page.wait_for_selector('#poll-state:has-text("Đang cập nhật")')
                wait_result(state['t1'])
                tx_before = page.evaluate("() => document.querySelector('#bk-result button.linklike').textContent")
                page.click('#bk-retry'); wait_result('COMMITTED — đủ ACK')
                time.sleep(2)
                tx_after = page.evaluate("() => document.querySelector('#bk-result button.linklike').textContent")
                assert tx_before == tx_after, 'tx đổi sau khi gửi lại'
                assert rows_for(state['t1']) == 1, 'có nhiều hơn một giao dịch cho cùng token'
                assert avail('airline') == 4 and avail('hotel') == 4, 'tồn thay đổi sau retry'
                return f'reload rồi Gửi lại: cùng tx {tx_before[:8]}, tồn vẫn 4/4'
            rep.step('A03a', 'Reload + Gửi lại cùng token → cùng tx, tồn vẫn 4', a03_reload)

            def a04_conflict():
                tx_rows = page.locator('#tx-body tr').count()
                page.fill('#bk-token', state['t1']); page.select_option('#bk-hotel', 'MISSING')
                page.click('#bk-submit')
                wait_text('#bk-status', 'Không dùng lại token với nội dung khác')
                time.sleep(1.5)
                assert page.locator('#tx-body tr').count() == tx_rows, 'đã tạo giao dịch mới'
                page.select_option('#bk-hotel', 'DEFAULT')
                return 'UI chặn dùng lại token với payload khác; không có giao dịch mới'
            rep.step('A04', 'Cùng token khác payload bị chặn, không tạo giao dịch', a04_conflict)

            def a02_abort_and_dblclick():
                page.click('#bk-newtoken')
                state['t2'] = cur_token()
                page.select_option('#bk-hotel', 'MISSING')
                page.dblclick('#bk-submit')               # double-click khi đặt yêu cầu mới
                wait_result('ABORTED')
                time.sleep(2.5)
                assert rows_for(state['t2']) == 1, 'double-click tạo nhiều giao dịch'
                assert avail('airline') == 4 and avail('hotel') == 4
                wait_text('.inventory[data-name=airline] [data-role=body]', 'Không có khóa')
                wait_text('.inventory[data-name=hotel] [data-role=body]', 'Không có khóa')
                rep.shot('02_abort_missing')
                return 'hotel MISSING → ABORTED; double-click chỉ một giao dịch; tồn giữ 4/4; không còn khóa'
            rep.step('A02', 'Hotel MISSING → ABORTED; double-click không đặt trùng', a02_abort_and_dblclick)

            def a03_offline_unknown():
                before = avail('airline')
                page.click('#bk-newtoken'); state['t3'] = cur_token()
                ctx.set_offline(True)
                wait_text('#banner', 'Không đọc được dashboard')
                assert avail('airline') == before, 'tồn bị đổi khi mất kết nối'
                assert 'Dữ liệu cũ' in page.inner_text('.inventory[data-name=airline] [data-role=fresh]')
                book('MISSING', state['t3'])
                wait_text('#bk-status', 'Chưa xác định kết quả')
                assert 'ABORT' not in page.inner_text('#bk-status') + page.inner_text('#bk-result'), 'UI tự kết luận ABORT khi mất phản hồi'
                rep.shot('03_mat_ket_noi')
                ctx.set_offline(False)
                wait_js("() => document.querySelector('#banner').hidden")
                page.click('#bk-check')
                wait_text('#bk-status', 'chưa có giao dịch với mã này')
                page.click('#bk-retry'); wait_result('ABORTED')
                wait_js("(t) => [...document.querySelectorAll('#tx-body tr')].filter(tr => tr.children[1].textContent === t).length === 1", state['t3'])
                return 'mất kết nối → “Chưa xác định”, dữ liệu gắn nhãn cũ; Kiểm tra không kết luận bừa; Gửi lại cùng token → ABORTED, một giao dịch'
            rep.step('A05/A06', 'Mất kết nối: unknown, dữ liệu cũ, không suy ra abort', a03_offline_unknown)

            def a03_two_tabs():
                page2 = ctx.new_page(); page2.on('dialog', lambda d: d.accept())
                page2.goto(url); page2.wait_for_selector('#poll-state:has-text("Đang cập nhật")')
                page2.wait_for_function("(t) => document.querySelector('#bk-result').textContent.includes(t)", arg=state['t3'])
                page2.click('#bk-retry'); page.click('#bk-retry')
                time.sleep(3)
                assert rows_for(state['t3']) == 1, 'hai tab + retry tạo nhiều giao dịch'
                page2.close()
                return 'hai tab cùng gửi lại một token: vẫn một giao dịch'
            rep.step('A03b', 'Hai tab + retry cùng token không đặt trùng', a03_two_tabs)

            def a08_before_decision():
                arm('coordinator', 'after_votes')
                state['t4'] = book('DEFAULT', None)
                wait_text('#bk-status', 'Chưa xác định kết quả')
                wait_svc('coordinator', 'Bị lỗi')
                wait_text('.inventory[data-name=airline] [data-role=body]', 'Đang giữ (PREPARED): 1')
                wait_text('.inventory[data-name=hotel] [data-role=body]', 'Tạm khóa')
                assert 'Đang chạy' in page.inner_text('.service[data-name=airline] [data-role=life]')
                assert 'Đang chạy' in page.inner_text('.service[data-name=hotel] [data-role=life]')
                assert page.locator('#bk-lock').is_visible()
                wait_text('#tx-fresh', 'Dữ liệu cũ')
                assert avail('airline') == 4, 'tồn hiển thị sai khi Coordinator chết'
                rep.shot('04_coordinator_crash_truoc_quyet_dinh')
                svc_click('coordinator', 'restart'); wait_svc('coordinator', 'Đang chạy')
                page.click('#bk-retry'); wait_result('ABORT')
                wait_text('.inventory[data-name=airline] [data-role=body]', 'Không có khóa', 30000)
                wait_text('.inventory[data-name=hotel] [data-role=body]', 'Không có khóa', 30000)
                assert avail('airline') == 4 and avail('hotel') == 4
                rep.shot('05_sau_restart_abort')
                return 'web và hai RM sống khi Coordinator chết; hai RM PREPARED giữ khóa; restart cùng DB → ABORTED, khóa được giải phóng, tồn 4/4'
            rep.step('A08', 'Coordinator crash trước quyết định → restart → ABORTED', a08_before_decision)

            def a09_after_decision():
                arm('coordinator', 'after_decision')
                state['t5'] = book('DEFAULT', None)
                wait_text('#bk-status', 'Chưa xác định kết quả')
                wait_svc('coordinator', 'Bị lỗi')
                svc_click('coordinator', 'restart'); wait_svc('coordinator', 'Đang chạy')
                page.click('#bk-retry'); wait_result('COMMITTED')
                wait_avail('airline', 3); wait_avail('hotel', 3)
                wait_result('COMMITTED — đủ ACK')
                time.sleep(2.5)
                assert avail('airline') == 3 and avail('hotel') == 3, 'tồn giảm sai số lần'
                assert rows_for(state['t5']) == 1
                rep.shot('06_replay_commit')
                return 'giữ COMMITTED sau restart, replay đủ ACK; tồn 4→3 đúng một lần'
            rep.step('A09', 'Coordinator crash sau quyết định → restart → COMMIT replay một lần', a09_after_decision)

            def a05_hotel_stop():
                svc_click('hotel', 'stop'); wait_svc('hotel', 'Đã dừng')
                wait_text('.inventory[data-name=hotel] [data-role=fresh]', 'Dữ liệu cũ')
                assert avail('hotel') == 3, 'tồn Hotel bị thay bằng giá trị khác khi node dừng'
                assert 'Dữ liệu mới' in page.inner_text('.inventory[data-name=airline] [data-role=fresh]')
                rep.shot('07_hotel_dung')
                return 'Hotel dừng: tồn giữ 3 và gắn “Dữ liệu cũ” (không thành 0); Airline vẫn mới'
            rep.step('A05b', 'Hotel dừng: dữ liệu cũ có nhãn, Airline không ảnh hưởng', a05_hotel_stop)

            def a12_port_busy():
                svc_click('airline', 'stop'); wait_svc('airline', 'Đã dừng')
                blocker = socket.socket(); blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                blocker.bind(('127.0.0.1', ports['airline'])); blocker.listen(5)
                try:
                    svc_click('airline', 'start')
                    wait_text('.service[data-name=airline] [data-role=msg]', 'Cổng đang bị tiến trình khác chiếm')
                    assert port_open(ports['airline']), 'tiến trình ngoài đã bị tác động'
                    assert 'Đã dừng' in svc_state('airline')
                    rep.shot('08_cong_ban')
                finally:
                    blocker.close()
                time.sleep(0.3)
                svc_click('airline', 'start'); wait_svc('airline', 'Đang chạy')
                svc_click('hotel', 'start'); wait_svc('hotel', 'Đang chạy')
                wait_avail('airline', 3); wait_avail('hotel', 3)
                return 'cổng bận → báo lỗi, không đụng tiến trình ngoài; sau khi trả cổng chạy lại được; DB cũ giữ tồn 3/3'
            def svc_state(name):
                return page.inner_text(f'.service[data-name={name}] [data-role=life]')
            rep.step('A11/A12', 'Cổng bận, start/restart giữ DB', a12_port_busy)

            def a17_poll_focus():
                page.focus('#tx-body tr button'); page.keyboard.press('Enter')
                wait_text('#tx-detail', 'Giao dịch')
                key = page.evaluate("() => document.activeElement.closest('tr').dataset.key")
                max_inflight['max'] = 0
                time.sleep(3.6)
                same = page.evaluate("(k) => { const a = document.activeElement; return !!a && !!a.closest('tr') && a.closest('tr').dataset.key === k && a.getAttribute('aria-current') === 'true'; }", key)
                assert same, 'polling làm mất focus/lựa chọn giao dịch'
                assert max_inflight['max'] <= 1, f'có {max_inflight["max"]} request overview chạy chồng'
                assert key in page.inner_text('#tx-detail')
                return 'bàn phím chọn giao dịch; sau 3 lần polling focus + lựa chọn còn nguyên; không có request chồng'
            rep.step('A17', 'Polling giữ focus/lựa chọn, không chồng request', a17_poll_focus)

            def a15_validation():
                for count, workers in (('0', '4'), ('51', '4'), ('10', '9')):
                    page.fill('#ld-count', count); page.fill('#ld-workers', workers); page.click('#ld-submit')
                    wait_text('#ld-status', 'số nguyên')
                page.fill('#ld-count', '10'); page.fill('#ld-workers', '4')
                return 'count 0/51 và workers 9 bị từ chối ngay trên UI'
            rep.step('A15', 'Kiểm tra đầu vào tải', a15_validation)

            def a14_load():
                before = (avail('airline'), avail('hotel'))
                page.click('#ld-submit')
                wait_text('#ld-status', 'Hoàn tất 10/10', 60000)
                summary = page.inner_text('#ld-summary')
                import re
                committed = int(re.search(r'COMMITTED (\d+)', summary).group(1))
                assert committed <= before[0], f'commit {committed} vượt tồn {before[0]}'
                wait_text('#ld-summary', 'Khớp', 40000)
                a, h = avail('airline'), avail('hotel')
                assert a == before[0] - committed and h == before[1] - committed and a >= 0 and h >= 0, 'tồn không khớp số commit'
                rep.shot('09_tai_dong_thoi')
                return f'10 booking/4 worker: {committed} COMMITTED trên {before[0]} chỗ; tồn {before[0]}→{a}, đối chiếu “Khớp”, không bán vượt'
            rep.step('A14', 'Tải 10/4 chạy nền, không bán vượt, tồn khớp commit', a14_load)

            def a17_layout_narrow():
                page.set_viewport_size({'width': 800, 'height': 900})
                time.sleep(0.5)
                width = page.evaluate('[document.documentElement.scrollWidth, window.innerWidth]')
                rep.shot('10_man_hinh_hep')
                page.set_viewport_size({'width': 1280, 'height': 800})
                assert width[0] <= width[1] + 1, f'tràn ngang ở 800px: {width}'
                return 'một cột, không tràn ngang ở 800px'
            rep.step('A17b', 'Bố cục co một cột ở màn hình nhỏ', a17_layout_narrow)

            def js_errors():
                assert not page_errors, f'lỗi JS: {page_errors[:2]}'
                return 'không có lỗi JavaScript chưa bắt'
            rep.step('A17c', 'Không có lỗi JavaScript trong toàn bộ kịch bản', js_errors)

            browser_name = f'{browser.browser_type.name} {browser.version}' + ('' if args.browser == 'chromium' else f' (channel {args.browser})')
            ctx.close(); browser.close()

        # ---------- dọn dẹp: A13 ----------
        def a13():
            stop_dashboard(dash)
            assert dash.poll() is not None, 'dashboard chưa thoát'
            time.sleep(0.5)
            alive = [n for n, p in ports.items() if port_open(p)]
            assert not alive, f'child còn sống sau khi dừng dashboard: {alive}'
            files = sorted(p.name for p in data_dir.glob('*.db'))
            assert files == ['airline.db', 'coordinator.db', 'hotel.db'], f'DB không còn đủ: {files}'
            return 'dừng dashboard → ba child đã dừng; ba tệp DB còn nguyên'
        rep.page = None
        rep.step('A13', 'Dừng dashboard dọn đúng child, giữ DB', a13)
    finally:
        stop_dashboard(dash)
        for f in log_dir.glob('*.log') if log_dir.exists() else []:
            (out / f'service_{f.name}').write_bytes(f.read_bytes())
        tmp.cleanup()

    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip() or 'không rõ'
    dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--', 'web', 'dashboard_api.py', 'run_dashboard.py'], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip())
    lines = ['# Biên bản kiểm thử trình duyệt dashboard', '',
             f'- Thời điểm: {datetime.now().isoformat(timespec="seconds")}',
             f'- Commit: {commit}' + (' (có thay đổi chưa commit ở web/, dashboard_api.py, run_dashboard.py)' if dirty else ''),
             f'- OS / Python: {platform.platform()} / {platform.python_version()}',
             f'- Trình duyệt: {browser_name}', f'- Cổng: dashboard {dash_port}, dịch vụ {base}–{base + 2}; DB tạm, không dùng data/ của demo',
             '', '| Ca | Nội dung | Kết quả | Quan sát | Giây |', '|---|---|---|---|---|']
    lines += [f'| {c} | {t} | {r} | {d.replace("|", "/")} | {s} |' for c, t, r, d, s in rep.rows]
    failed = [row for row in rep.rows if row[2] != 'PASS']
    lines += ['', f'Tổng: {len(rep.rows) - len(failed)}/{len(rep.rows)} PASS.',
              '', 'Giới hạn: kịch bản chạy với một trình duyệt trên một máy; không thay thế diễn tập bằng tay, '
              'chạy trên máy thuyết trình, hoặc đánh giá hiển thị bằng mắt từ các ảnh đính kèm.']
    (out / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'\nBiên bản: {out / "report.md"}  ({len(rep.rows) - len(failed)}/{len(rep.rows)} PASS)')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
