"""Dashboard web đầy đủ: giao diện (web/) + API trên cổng 8080.

Chạy:  python run_dashboard.py        rồi mở http://127.0.0.1:8080
Nếu có lifecycle.py (bộ quản lý tiến trình của SV2), dashboard tự khởi chạy Airline,
Hotel, Coordinator và điều khiển Chạy/Dừng/Khởi động lại từ trình duyệt. Dữ liệu SQLite
luôn được giữ khi dừng hoặc restart. Không có lifecycle.py thì dashboard chạy ở chế độ
"chưa quản lý tiến trình": vẫn xem/đặt/gây lỗi/tải được với ba dịch vụ chạy thủ công.
Chỉ dùng thư viện chuẩn; chỉ bind 127.0.0.1.
"""
import argparse
import signal
import sys
import threading
from pathlib import Path

from dashboard_api import NAMES, DashboardAPI, create_http_server

ROOT = Path(__file__).resolve().parent
START_ORDER = ('airline', 'hotel', 'coordinator')


def build_manager(args):
    try:
        from lifecycle import ProcessManager
    except ImportError:
        return None
    ports = {name: args.base_port + i for i, name in enumerate(NAMES)}
    kwargs = {'ports': ports, 'root': ROOT}
    if args.data_dir:
        kwargs['data_dir'] = args.data_dir
    if args.log_dir:
        kwargs['log_dir'] = args.log_dir
    return ProcessManager(**kwargs)


def autostart(manager) -> None:
    for name in START_ORDER:
        try:
            manager.control(name, 'start')
            print(f'[dashboard] {name}: đã chạy', flush=True)
        except Exception as exc:  # lỗi (ví dụ cổng bận) hiện trên giao diện, không dừng web
            print(f'[dashboard] {name}: không chạy được — {exc}', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', type=int, default=8080, help='cổng dashboard (mặc định 8080)')
    parser.add_argument('--base-port', type=int, default=8000,
                        help='cổng Coordinator; Airline = +1, Hotel = +2 (mặc định 8000)')
    parser.add_argument('--data-dir', help='thư mục chứa ba SQLite DB (mặc định: data/)')
    parser.add_argument('--log-dir', help='thư mục log từng dịch vụ (mặc định: logs/)')
    parser.add_argument('--no-autostart', action='store_true', help='không tự chạy ba dịch vụ khi khởi động')
    args = parser.parse_args()

    urls = {name: f'http://127.0.0.1:{args.base_port + i}' for i, name in enumerate(NAMES)}
    manager = build_manager(args)
    api = DashboardAPI(urls, manager=manager)
    server = create_http_server(api, args.port, static_dir=ROOT / 'web')
    # SIGTERM/Ctrl+Break cũng đi qua khối finally để dọn đúng các child do dashboard tạo.
    def stop(*_):
        raise KeyboardInterrupt

    for sig_name in ('SIGTERM', 'SIGBREAK'):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            signal.signal(sig, stop)
    print(f'Dashboard: http://127.0.0.1:{server.server_port}', flush=True)
    if manager is None:
        print('[dashboard] Không tìm thấy lifecycle.py: chế độ chưa quản lý tiến trình '
              '(chạy ba dịch vụ thủ công theo README).', flush=True)
    elif not args.no_autostart:
        threading.Thread(target=autostart, args=(manager,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if manager is not None:
            print('[dashboard] Đang dừng các dịch vụ do dashboard tạo (giữ nguyên DB)…', flush=True)
            manager.close_all()


if __name__ == '__main__':
    sys.exit(main())
