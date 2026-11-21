# Biên bản kiểm thử trình duyệt dashboard

- Thời điểm: 2026-11-14T20:29:10
- Commit: 546c998680f851b29da773a26e504c1f27d5912a (có thay đổi chưa commit ở web/, dashboard_api.py, run_dashboard.py)
- OS / Python: Linux-6.18.44-fc-v470-x86_64-with-glibc2.39 / 3.12.3
- Trình duyệt: chromium 153.0.8010.12
- Cổng: dashboard 18081, dịch vụ 18203–18205; DB tạm, không dùng data/ của demo

| Ca | Nội dung | Kết quả | Quan sát | Giây |
|---|---|---|---|---|
| A00 | Khởi động: ba node chạy, tồn 5/5 | PASS | ba node Đang chạy; mỗi RM 5 DEFAULT; không tràn ngang ở 1280px | 0.2 |
| A01 | Đặt combo mới → COMMITTED, tồn 5→4 | PASS | token booking-20261114-202839-i5t0: COMMITTED, ACK 2/2, tồn 5→4 hai bên | 1.1 |
| A03a | Reload + Gửi lại cùng token → cùng tx, tồn vẫn 4 | PASS | reload rồi Gửi lại: cùng tx abecb7a3, tồn vẫn 4/4 | 2.2 |
| A04 | Cùng token khác payload bị chặn, không tạo giao dịch | PASS | UI chặn dùng lại token với payload khác; không có giao dịch mới | 1.6 |
| A02 | Hotel MISSING → ABORTED; double-click không đặt trùng | PASS | hotel MISSING → ABORTED; double-click chỉ một giao dịch; tồn giữ 4/4; không còn khóa | 2.8 |
| A05/A06 | Mất kết nối: unknown, dữ liệu cũ, không suy ra abort | PASS | mất kết nối → “Chưa xác định”, dữ liệu gắn nhãn cũ; Kiểm tra không kết luận bừa; Gửi lại cùng token → ABORTED, một giao dịch | 2.6 |
| A03b | Hai tab + retry cùng token không đặt trùng | PASS | hai tab cùng gửi lại một token: vẫn một giao dịch | 3.4 |
| A08 | Coordinator crash trước quyết định → restart → ABORTED | PASS | web và hai RM sống khi Coordinator chết; hai RM PREPARED giữ khóa; restart cùng DB → ABORTED, khóa được giải phóng, tồn 4/4 | 2.0 |
| A09 | Coordinator crash sau quyết định → restart → COMMIT replay một lần | PASS | giữ COMMITTED sau restart, replay đủ ACK; tồn 4→3 đúng một lần | 4.6 |
| A05b | Hotel dừng: dữ liệu cũ có nhãn, Airline không ảnh hưởng | PASS | Hotel dừng: tồn giữ 3 và gắn “Dữ liệu cũ” (không thành 0); Airline vẫn mới | 0.5 |
| A11/A12 | Cổng bận, start/restart giữ DB | PASS | cổng bận → báo lỗi, không đụng tiến trình ngoài; sau khi trả cổng chạy lại được; DB cũ giữ tồn 3/3 | 2.9 |
| A17 | Polling giữ focus/lựa chọn, không chồng request | PASS | bàn phím chọn giao dịch; sau 3 lần polling focus + lựa chọn còn nguyên; không có request chồng | 3.7 |
| A15 | Kiểm tra đầu vào tải | PASS | count 0/51 và workers 9 bị từ chối ngay trên UI | 0.2 |
| A14 | Tải 10/4 chạy nền, không bán vượt, tồn khớp commit | PASS | 10 booking/4 worker: 3 COMMITTED trên 3 chỗ; tồn 3→0, đối chiếu “Khớp”, không bán vượt | 1.6 |
| A17b | Bố cục co một cột ở màn hình nhỏ | PASS | một cột, không tràn ngang ở 800px | 0.9 |
| A17c | Không có lỗi JavaScript trong toàn bộ kịch bản | PASS | không có lỗi JavaScript chưa bắt | 0.0 |
| A13 | Dừng dashboard dọn đúng child, giữ DB | PASS | dừng dashboard → ba child đã dừng; ba tệp DB còn nguyên | 0.5 |

Tổng: 17/17 PASS.

Giới hạn: kịch bản chạy với một trình duyệt trên một máy; không thay thế diễn tập bằng tay, chạy trên máy thuyết trình, hoặc đánh giá hiển thị bằng mắt từ các ảnh đính kèm.
