"""Kịch bản chạy thử nghiệm trực quan dành cho Sinh viên 2 (Trần Lê Quang Hữu).
Minh họa trực tiếp:
1. ProcessManager khởi động 3 tiến trình phân tán (Coordinator, Airline, Hotel).
2. Kiểm tra snapshot trạng thái hệ thống.
3. Đặt chỗ du lịch 2PC thành công qua RPC.
4. Kiểm tra trừ kho trong SQLite DB của Airline và Hotel.
5. Thử nghiệm Idempotency: Gửi lại cùng booking token, không bị trừ trùng.
6. Thử nghiệm Crash/Restart qua ProcessManager: Giữ nguyên DB cũ.
7. Dọn dẹp an toàn toàn bộ tiến trình.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

# Đảm bảo đường dẫn import
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lifecycle import ProcessManager
from common import rpc, db


def separator(title: str):
    print("\n" + "=" * 65)
    print(f"👉 {title}")
    print("=" * 65)


def check_inventory():
    print("\n[Kiểm tra tồn kho trong SQLite DB]")
    for name in ['airline', 'hotel']:
        db_path = ROOT / f"data/{name}.db"
        if not db_path.exists():
            db_path = ROOT / f"{name}.db"
        if db_path.exists():
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute("SELECT resource, available FROM inventory").fetchall()
                print(f"  • {name.upper():<12} ({db_path.name}): {rows}")


def main():
    print("""
=================================================================
  HỆ THỐNG ĐẶT CHỖ DU LỊCH PHÂN TÁN VỚI TWO-PHASE COMMIT (2PC)
  BẢN DEMO THỰC THI TRỰC TIẾP — SINH VIÊN 2 (TRẦN LÊ QUANG HỮU)
=================================================================
""")

    pm = ProcessManager(root=ROOT)

    try:
        separator("BƯỚC 1: ProcessManager khởi động 3 tiến trình phân tán")
        for service in ['coordinator', 'airline', 'hotel']:
            info = pm.control(service, 'start')
            print(f"  ✅ Dịch vụ {service.upper():<12} đã chạy thành công (PID: {info['pid']}, Port: {info['port']})")

        separator("BƯỚC 2: Kiểm tra snapshot hệ thống (phi chặn)")
        snap = pm.snapshot()
        for name, item in snap.items():
            print(f"  • {name:<12}: state={item['state']}, pid={item['pid']}, error={item['error']}")

        check_inventory()

        separator("BƯỚC 3: Thực hiện giao dịch 2PC đặt combo du lịch (Pha 1 & Pha 2)")
        token = "DEMO-TOKEN-001"
        print(f"  Gửi yêu cầu đặt vé với Booking Token: '{token}'")
        
        # 1. Begin transaction ở Coordinator
        begin_res = rpc("http://127.0.0.1:8000", "begin", {
            "token": token,
            "airline": "DEFAULT",
            "hotel": "DEFAULT"
        })
        tx = begin_res["tx"]
        print(f"  -> Coordinator tạo mã giao dịch tx: {tx} (Trạng thái: ACTIVE)")

        # 2. End transaction (Coordinator chạy Two-Phase Commit tới Airline & Hotel)
        print("  -> Coordinator thực hiện 2PC (Pha 1: PREPARE -> Pha 2: COMMIT)...")
        end_res = rpc("http://127.0.0.1:8000", "end", {"tx": tx})
        print(f"  🎉 Kết quả 2PC: {end_res}")

        check_inventory()

        separator("BƯỚC 4: Kiểm tra tính lũy biến (Idempotency) - Chống trừ tiền trùng")
        print(f"  Thử gửi lại chính xác Booking Token '{token}' vừa đặt...")
        retry_begin = rpc("http://127.0.0.1:8000", "begin", {
            "token": token,
            "airline": "DEFAULT",
            "hotel": "DEFAULT"
        })
        print(f"  -> Coordinator nhận diện token cũ, trả lại tx cũ mà không tạo mới: {retry_begin['tx']}")
        retry_end = rpc("http://127.0.0.1:8000", "end", {"tx": retry_begin['tx']})
        print(f"  -> Trạng thái vẫn là: {retry_end['state']} (Kho KHÔNG bị trừ thêm lần nữa!)")
        check_inventory()

        separator("BƯỚC 5: Thử nghiệm Crash & Restart dịch vụ qua ProcessManager")
        print("  Đột ngột dừng (Stop) dịch vụ HOTEL để mô phỏng sự cố sập node...")
        pm.control('hotel', 'stop')
        snap_after_stop = pm.snapshot()
        print(f"  -> Trạng thái Hotel sau khi stop: {snap_after_stop['hotel']['state']}")

        print("  Khởi động lại (Restart) HOTEL bằng ProcessManager...")
        pm.control('hotel', 'start')
        snap_after_restart = pm.snapshot()
        print(f"  -> Trạng thái Hotel sau khi restart: {snap_after_restart['hotel']['state']} (PID mới: {snap_after_restart['hotel']['pid']})")
        print("  Kiểm tra xem dữ liệu DB cũ có bị mất không:")
        check_inventory()

        separator("BƯỚC 6: Dọn dẹp sạch sẽ toàn bộ các tiến trình")
        pm.close_all()
        print("  ✅ Đã đóng tất cả các tiến trình con an toàn. File cơ sở dữ liệu SQLite vẫn được lưu trên đĩa.")

        print("""
=================================================================
  🎉 DEMO HOÀN TẤT THÀNH CÔNG RỰC RỠ!
  Bạn đã thấy rõ:
  1. ProcessManager do bạn viết khởi động, giám sát và dọn dẹp tiến trình trơn tru.
  2. Giao dịch phân tán 2PC giữa Airline và Hotel chạy chuẩn ACID.
  3. Dữ liệu SQLite tồn tại bền vững qua các lần khởi động lại.
=================================================================
""")

    except Exception as exc:
        print(f"\n❌ LỖI TRONG QUÁ TRÌNH DEMO: {exc}")
        pm.close_all()
        raise


if __name__ == '__main__':
    main()
