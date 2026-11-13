"""Demo trực quan phần việc của:
- SV1: Nguyễn Đăng Khoa (Coordinator 2PC, Dashboard HTTP API, Background Load)
- SV3: Dương Quốc Khánh (Transactional Client, Chaos Injector, Multi-worker load)
kết hợp với ProcessManager của SV2 (Trần Lê Quang Hữu).
"""
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lifecycle import ProcessManager
from dashboard_api import DashboardAPI, create_http_server


def separator(title: str):
    print("\n" + "=" * 65)
    print(f"👉 {title}")
    print("=" * 65)


def http_get(url: str, token: str = None) -> dict:
    req = Request(url)
    if token:
        req.add_header("X-Session-Token", token)
    with urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(url: str, data: dict, token: str = None) -> dict:
    body = json.dumps(data).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Session-Token", token)
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print("""
=================================================================
  DEMO TOÀN DIỆN PHẦN VIỆC CỦA KHOA (SV1) & KHÁNH (SV3)
  KẾT HỢP HỆ THỐNG PHÂN TÁN 2PC & PROCESS MANAGER CỦA HỮU (SV2)
=================================================================
""")

    pm = ProcessManager(root=ROOT)

    try:
        separator("BƯỚC 1: SV2 khởi động 3 Service (Coordinator, Airline, Hotel)")
        for s in ['coordinator', 'airline', 'hotel']:
            pm.control(s, 'start')
        print("  ✅ Cả 3 node đã sẵn sàng phục vụ!")

        separator("BƯỚC 2: [SV1 - KHOA] Khởi động Dashboard HTTP API (Cổng 8080)")
        api = DashboardAPI(manager=pm)
        http_server = create_http_server(api, port=8080)
        import threading
        t = threading.Thread(target=http_server.serve_forever, daemon=True)
        t.start()
        print("  ✅ Dashboard API của Khoa đang lắng nghe tại: http://127.0.0.1:8080")

        # Lấy Session Token HTTP
        session_res = http_get("http://127.0.0.1:8080/api/session")
        session_token = session_res["session_token"]
        print(f"  🔑 Cấp Session Token phiên an toàn: {session_token[:15]}...")

        # Gọi GET /api/overview của Khoa
        print("\n  [SV1 - Khoa] Gọi API GET /api/overview để xem toàn cảnh hệ thống:")
        overview = http_get("http://127.0.0.1:8080/api/overview")
        nodes = overview['nodes']
        print(f"  • Trạng thái 3 Services:")
        for name in ['coordinator', 'airline', 'hotel']:
            node = nodes[name]
            life = node.get('lifecycle', {})
            print(f"    - {name.upper():<12}: state={life.get('state')}, pid={life.get('pid')}, stale={node.get('stale')}")
        airline_inv = nodes['airline']['snapshot']['inventory']
        hotel_inv = nodes['hotel']['snapshot']['inventory']
        print(f"  • Tồn kho nhận diện từ RPC: Airline={airline_inv}, Hotel={hotel_inv}")

        separator("BƯỚC 3: [SV1 - KHOA] Đặt vé thông qua Dashboard API của Khoa")
        print("  Gửi yêu cầu POST /api/bookings qua API của Khoa:")
        booking_data = {"token": "KHOA-API-BOOKING-01", "airline": "DEFAULT", "hotel": "DEFAULT"}
        booking_res = http_post("http://127.0.0.1:8080/api/bookings", booking_data, token=session_token)
        print(f"  🎉 Kết quả đặt vé qua API của Khoa: tx={booking_res['tx']}, state={booking_res['state']}, complete={booking_res['complete']}")

        separator("BƯỚC 4: [SV3 - KHÁNH] Dùng Client CLI đặt vé trực tiếp (client.py)")
        print("  Khánh xây dựng client.py. Chạy lệnh: python client.py book")
        cmd_book = [sys.executable, "client.py", "book", "--token", "KHANH-CLI-BOOK-02"]
        res = subprocess.run(cmd_book, capture_output=True, text=True, cwd=ROOT)
        print("  Client của Khánh trả về kết quả:")
        for line in res.stdout.strip().splitlines()[:6]:
            print(f"    {line}")

        separator("BƯỚC 5: [SV3 - KHÁNH] Bơm lỗi / Chaos Injector bằng client.py")
        print("  Khánh xây dựng công cụ bơm lỗi: Bơm lỗi 'crash sau khi prepare' vào Hotel...")
        cmd_fault = [sys.executable, "client.py", "fault", "--url", "http://127.0.0.1:8002", "--point", "after_prepare", "--mode", "crash"]
        res_fault = subprocess.run(cmd_fault, capture_output=True, text=True, cwd=ROOT)
        print(f"  -> Kết quả bơm lỗi của Khánh: {res_fault.stdout.strip()}")

        separator("BƯỚC 6: [SV1 - KHOA] Thử nghiệm tính năng Bắn tải nền (POST /api/load)")
        print("  Khoa xây dựng background load: Bắn 3 booking đồng thời bằng worker pool...")
        load_res = http_post("http://127.0.0.1:8080/api/load", {"count": 3, "workers": 2}, token=session_token)
        print(f"  -> Trạng thái bắn tải của Khoa: run_id={load_res['run_id']} (Đang chạy ngầm)")

        print("\n  Chờ 1 giây để worker hoàn tất và kiểm tra kết quả tồn kho cuối cùng:")
        time.sleep(1.2)
        final_overview = http_get("http://127.0.0.1:8080/api/overview")
        f_nodes = final_overview['nodes']
        f_air = f_nodes['airline']['snapshot']['inventory']
        f_hot = f_nodes['hotel']['snapshot']['inventory']
        f_txs = f_nodes['coordinator']['snapshot']['transactions']
        print(f"  📊 Tồn kho cuối cùng: Airline={f_air}, Hotel={f_hot}")
        print(f"  📊 Tổng số giao dịch đã ghi nhận ở Coordinator: {len(f_txs)} giao dịch")

        http_server.shutdown()
        pm.close_all()

        print("""
=================================================================
  🎉 DEMO HOÀN TẤT THÀNH CÔNG!
  Bạn đã thấy rõ vai trò của từng người:
  - Khoa (SV1): Dashboard HTTP API (overview, bookings, load nền) và 2PC Coordinator.
  - Hữu (SV2 - Bạn): ProcessManager khởi động/giám sát 3 server & SQLite RM.
  - Khánh (SV3): Client đặt chỗ (client.py), Bơm lỗi (fault/chaos injection).
=================================================================
""")

    except Exception as exc:
        print(f"Lỗi demo: {exc}")
        pm.close_all()
        raise


if __name__ == '__main__':
    main()
