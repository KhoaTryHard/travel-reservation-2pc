# HTPT — Đặt chuyến du lịch với Two Phase Commit

Bản triển khai học phần Hệ thống phân tán cho nhóm 3 sinh viên năm 4. Mỗi giao dịch đặt đúng 1 chỗ máy bay và 1 phòng. Hai Resource Manager là hai tiến trình với hai SQLite DB riêng. Coordinator có DB nhật ký riêng. Python 3.10 trở lên, chỉ dùng thư viện chuẩn, không cần pip hoặc Docker.

## Chạy nhanh trên Windows

Mở PowerShell tại thư mục đã giải nén (thư mục chứa server.py):

```powershell
python --version
python run_demo.py
```

Nếu máy dùng Python Launcher, thay `python` bằng `py -3`. Mở PowerShell thứ hai tại cùng thư mục:

```powershell
python client.py book --token booking-001
python client.py book --token booking-001
python client.py snapshot
python client.py snapshot --url http://127.0.0.1:8001
python client.py load --count 10 --workers 4
python -m unittest discover -s tests -v
```

Hai lần book cùng token chỉ đặt một lần. Mỗi DB mới có 5 đơn vị tài nguyên DEFAULT. Load có thể abort khi hết chỗ. `run_demo.py` dừng cả cụm khi một tiến trình chết; dùng 3 terminal bên dưới để demo crash và khởi động lại từng dịch vụ. Ctrl+C kết thúc, dữ liệu vẫn còn. Chỉ để bắt đầu demo sạch: dừng cả ba dịch vụ, đổi tên thư mục data thành data_backup; lần chạy sau tự tạo DB mới. Không xóa DB giữa giao dịch.

## Chạy từng server và phục hồi

Terminal 1:
```powershell
python server.py participant --port 8001 --db data/airline.db --enable-chaos
```
Terminal 2:
```powershell
python server.py participant --port 8002 --db data/hotel.db --enable-chaos
```
Terminal 3:
```powershell
python server.py coordinator --port 8000 --db data/coordinator.db --enable-chaos
```
Terminal 4 dùng client. Nếu cổng đã bị chiếm, dừng run_demo trước. `--capacity N` chỉ áp dụng khi khởi tạo DB mới.

### Demo 1 — Thành công và retry
`python client.py book --token demo-success` rồi chạy lại. Đối chiếu cùng tx và lượng tồn chỉ giảm 1 ở mỗi server.

### Demo 2 — Thiếu phòng
`python client.py book --token demo-abort --hotel MISSING`. Cả hai ABORTED, tồn máy bay không giảm.

### Demo 3 — Coordinator chết trước quyết định
```powershell
python client.py fault --point after_votes
python client.py book --token demo-before
python client.py snapshot --url http://127.0.0.1:8001
python client.py snapshot --url http://127.0.0.1:8002
```
Coordinator thoát mã 86. Hai participant giữ PREPARED và locks. Khởi động lại terminal 3 bằng đúng lệnh cũ, giữ nguyên DB. Coordinator đọc PREPARING không có quyết định, ghi ABORTED rồi phát lại. Chạy lại book cùng token để kiểm tra kết quả ABORTED.

### Demo 4 — Coordinator chết sau quyết định commit
```powershell
python client.py fault --point after_decision
python client.py book --token demo-after
```
Khởi động lại coordinator, retry cùng token: COMMITTED, complete=true khi đủ ACK. Không được đổi quyết định thành abort.

### Demo 5 — Hotel chết sau ghi prepare
```powershell
python client.py fault --url http://127.0.0.1:8002 --point after_prepare
python client.py book --token demo-rm
```
Khởi động lại hotel bằng đúng DB; coordinator phát lại abort. `after_commit` thay cho `after_prepare` mô phỏng mất ACK sau khi đã commit: replay không được trừ tồn hai lần.

### Demo 6 — Timeout và thông điệp đến muộn
```powershell
python client.py fault --url http://127.0.0.1:8002 --point before_prepare --mode delay --seconds 3
python client.py book --token demo-timeout
```
RPC nội bộ timeout sau 2 giây; coordinator abort. Tombstone ABORTED ngăn PREPARE đến muộn tạo khóa trở lại. Đợi ít nhất 3 giây rồi kiểm tra snapshot.

## RPC contract

Mọi RPC: POST /action với JSON object; HTTP 200 chứa kết quả hoặc `{"error":"..."}`. Client phải kiểm tra error; đây là quy ước RPC tối giản, không phải thiết kế REST production.

| Nơi nhận | action | JSON đầu vào | Kết quả |
|---|---|---|---|
| Coordinator | begin | token, airline, hotel | tx, state, complete, ack |
| Coordinator | end | tx | Chạy 2PC hoặc trả quyết định cũ |
| Coordinator | abort | tx | Hủy trước quyết định; đã commit thì trả COMMITTED |
| Coordinator | status | tx | Quyết định và ACK hiện tại |
| Participant | prepare | tx, resource | vote, state |
| Participant | commit / abort | tx | state |
| Participant | status | tx | PREPARED / COMMITTED / ABORTED / UNKNOWN |
| Cả hai | snapshot / health | {} | Dữ liệu kiểm tra / trạng thái tiến trình |
| Cả hai | fault | point, mode, seconds | Chỉ khi bật --enable-chaos |

`begin`, `end`, `abort` tương ứng BEGIN_TRANSACTION, END_TRANSACTION, ABORT_TRANSACTION. `prepare` là VOTE_REQUEST; vote=true là VOTE_COMMIT; commit/abort là GLOBAL_COMMIT/GLOBAL_ABORT. Client ghi nhớ token trước khi gửi. Mất phản hồi là kết quả chưa biết, không đồng nghĩa thất bại. Không dùng token mới để retry cùng yêu cầu; cùng token khác payload bị từ chối. Một yêu cầu đã ABORTED muốn thử đặt mới phải dùng token mới.

## Bất biến và lưu bền vững

1. Coordinator ghi quyết định vào SQLite transaction có synchronous=FULL trước khi gửi pha 2.
2. Participant ghi PREPARED, tentative reservation và khóa trong cùng local transaction trước khi vote YES.
3. COMMIT cập nhật inventory, trạng thái, xóa khóa và ghi events trong cùng local transaction; lặp lại không trừ thêm.
4. ABORT tạo tombstone kể cả chưa thấy PREPARE, tránh thông điệp đến muộn hồi sinh giao dịch.
5. PREPARED không hết hạn tự động. Coordinator chạy recovery mỗi 0,5 giây, phát lại quyết định đến khi đủ ACK. Participant khởi động lại giữ nguyên khóa trên đĩa và chờ phát lại.
6. `state=COMMITTED` là quyết định bền vững; `complete=true` xác nhận đủ ACK. Tại thời điểm chuyển tiếp có thể một bên COMMITTED, bên kia PREPARED. Không có giao dịch kết thúc với hai quyết định đối nghịch. Snapshot là công cụ debug, không cung cấp distributed snapshot isolation.

SQLite WAL là cơ chế nhật ký vật lý; bảng events là nhật ký logic phục vụ đọc và demo. Không tự cài storage engine hoặc XA. Quyết định, ACK, events cùng được ghi bằng local atomic transaction.

## Cấu trúc và người phụ trách

- SV1 / leader: coordinator.py, thiết kế giao thức, quản lý tích hợp và báo cáo.
- SV2: participant.py, common.py phần persistence, dữ liệu và khóa.
- SV3: client.py, server.py phần chaos, run_demo.py, tests/test_system.py.
- docs/plan.md: kế hoạch 14 ngày và cách cập nhật tiến độ.
- docs/test_results.txt: đầu ra kiểm thử thực tế của bản bàn giao.

## Phạm vi và giới hạn

Bản học thuật dùng 1 tài nguyên DEFAULT mỗi RM và 1 đơn vị mỗi giao dịch; chưa có lịch bay, ngày nhận phòng, thanh toán thật hay web UI. “Không tính phí trùng” được minh họa bằng không tạo đặt chỗ/trừ tồn trùng, chưa tích hợp ngân hàng.

Coordinator tiếp nhận nhiều request đồng thời nhưng tuần tự hóa end/abort/recovery bằng mutex để dễ kiểm chứng. Participant có khóa độc quyền bền vững theo resource và từ chối ngay khi xung đột; chưa có hàng đợi công bằng hoặc phát hiện deadlock đa tài nguyên. Một coordinator duy nhất, không có HA/Raft hay peer termination. Giả định lỗi crash/recovery, đĩa bền vững không mất/hỏng và cuối cùng kết nối được phục hồi. Nếu coordinator/DB mất vĩnh viễn thì không bảo đảm tiến triển.

Chỉ lắng nghe loopback, endpoint không có xác thực/TLS; chaos phục vụ phòng lab. Không mở Internet. Có thể dùng các tiến trình trên cùng máy để minh họa failure độc lập và RPC; chưa kiểm thử triển khai nhiều máy hoặc Windows thực tế. Đã chạy kiểm thử subprocess trên Linux trong môi trường tạo sản phẩm. Cần nhóm chạy lại trên máy thuyết trình.

## Nguồn môn học

Đề Track B do người học cung cấp; Lect05 trang 5, 10–11, 29–33; Tanenbaum & Van Steen, Distributed Systems Principles and Paradigms, 2nd ed., §1.3.2 và §8.5.1. Không đóng gói lại sách/slide. Đề nhắc Lecture 11 nhưng tài liệu này chưa được cung cấp.
