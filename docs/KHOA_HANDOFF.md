# Bàn giao phần của Khoa — API và kiểm soát tích hợp

Ngày cập nhật: 09/11/2026, Asia/Saigon. Tổng baseline của Khoa 42h, nhóm 126h;
đây là effort kế hoạch, không phải giờ đã tiêu. D1 = 07/11, D14 = 20/11.

## Kiến trúc và trách nhiệm

```mermaid
flowchart LR
    UI[UI của Khánh] --> HTTP[HTTP cùng nguồn / token phiên]
    HTTP --> API[DashboardAPI của Khoa]
    API --> RPC[RPC health / snapshot / begin / end / fault]
    API --> LM[LifecycleManager của Hữu]
    RPC --> C[Coordinator và DB riêng]
    RPC --> A[Airline và DB riêng]
    RPC --> H[Hotel và DB riêng]
    LM -. sở hữu child / log / DB cũ .-> C
    LM -.-> A
    LM -.-> H
```

API dùng thư viện Python chuẩn, chạy ngoài Coordinator. HTTP server chỉ bind loopback;
POST kiểm tra Host/Origin/token phiên. Backend hiện có vẫn chịu quyết định 2PC, persistence,
idempotency và replay; API không tự ghi DB hoặc tự quyết định abort khi mất kết nối.

Snapshot Coordinator có payload/ack dạng JSON string: API chuẩn hóa object/list và tính
complete từ ACK của airline/hotel. Node được đọc song song, health/snapshot dùng chung
deadline 2 giây/node. Cache theo node có freshness; dữ liệu cũ không chứng minh trạng thái
hiện tại. Participant PREPARED giữ khóa qua restart; COMMIT thiếu ACK vẫn là quyết định
bền vững, phải replay đến đủ ACK. Timestamp chỉ để quan sát, events có thứ tự riêng mỗi node.

Booking begin/end dùng chung deadline 30 giây. Khi đã gửi nhưng không có kết quả xác định,
lỗi API giữ token/payload và tx đã biết, outcome_unknown=true; retry dùng đúng yêu cầu cũ.
Không có token mới tự sinh cho retry. Token phiên chỉ bảo vệ thao tác HTTP cùng nguồn.

Load là công việc nền trong một dashboard process: một đợt, 1–50 request, 1–8 worker,
token riêng mỗi request, kết quả theo request. Giữ tối đa 20 đợt; không có lưu bền qua restart.
Lỗi mạng trong một request vẫn có thể chưa xác định, không tính tự động là abort.

Interface, JSON và lỗi chi tiết nằm trong [đặc tả, mục 8](DASHBOARD_SPEC.md).

## Chạy và nối các phần

1. Chạy ba RPC server riêng theo README, giữ nguyên DB khi crash/recovery.
2. Chạy `python run_dashboard_api.py`, mặc định cổng 8080.
3. Đọc `/api/session` rồi gửi X-Session-Token trên mọi POST JSON.
4. Khánh nối UI với overview/booking/transactions/faults/load cùng nguồn. HTML entry tương lai
   do UI/dashboard entry phục vụ; API entry hiện tại không có UI và không phải `run_dashboard.py`.
5. Hữu cung cấp `snapshot()`/`control()` rồi truyền `manager=` vào DashboardAPI; entry sở hữu
   manager phải xử lý khởi chạy/cleanup child. HTTP server tạo bằng `create_http_server(api, port)`.

Manager chưa có: overview ghi unmanaged; services trả 503. Không chạy lệnh shell hoặc
kill tiến trình ngoài để thay thế W06. `run_demo.py` dừng cả cụm khi một node chết nên không
dùng nó để nghiệm thu web sống qua crash.

## Trạng thái task và đầu vào còn thiếu

| Task | Phần bàn giao của Khoa | Điều kiện còn lại trước Done toàn phần |
|---|---|---|
| W01 #4 | Contract, ví dụ, baseline, ranh giới module | Hữu/Khánh xác nhận; quỹ giờ, máy demo và lịch diễn tập chưa được nhóm xác nhận |
| W02a #5 | Overview, booking, transaction, HTTP boundary | PR/tests/CI là bằng chứng ghi trong issue |
| W02b #6 | Fault/unknown, kiểm chứng crash/replay/ACK qua API | PR/tests/CI là bằng chứng ghi trong issue |
| W02c #7 | Load nền, adapter lifecycle và test hợp đồng | Nối implementation manager thật của Hữu và kiểm thử lifecycle tích hợp |
| W03a #8 | Có bằng chứng API với RPC child thật | Ghép UI/manager thật, review UI, nghiệm thu M1 trên web |
| W03b #9 | Có hồi quy backend/API | Lifecycle/UI/browser và M2/G10 thực tế, khóa scope theo kết quả thật |
| W04a #10 | Bản kiến trúc/bàn giao Markdown này | Ghép W08/W11a, MSSV/lớp/GV; đồng bộ Word/Drive; bàn giao thử trên máy demo |
| W04b #11 | Checklist nghiệm thu/phát hành bên dưới | Diễn tập cả nhóm, sửa lỗi, test cuối và duyệt bản nộp |

Hạn gốc W01 giữ 08/11; dự báo bàn giao toàn phần 10/11. Overview thật đã kiểm chứng
qua API ngày 09/11 (sớm hơn dự báo bù ban đầu 10/11).
Mục tiêu 13/11 M1, 15/11 M2, 16/11 G10, 18/11 M3, 20/11 M4 giữ nguyên, chưa ghi đạt.
Không tính mock manager hoặc RPC child do test harness chạy thành nghiệm thu W06/W07/browser.
Markdown này là phần báo cáo kỹ thuật đã chuẩn bị; Word/Drive hiện còn mô tả kế hoạch,
chưa được coi là báo cáo triển khai đồng bộ hoàn chỉnh.

## Bằng chứng kiểm thử và nghiệm thu

Lệnh chuẩn: `python -m unittest discover -s tests -v`. Dùng subprocess/cổng riêng/DB tạm.
Trước thay đổi API, Windows/Python 3.11.15 đã chạy 14/14 backend test, 84.914s.
Các PR API ghi riêng commit, local result và CI Ubuntu/Windows Python 3.10/3.13.
Đầu ra test cuối của lượt triển khai được lưu trong `docs/khoa_test_results.txt`.

Lượt triển khai 09/11: **34/34 test đạt**, Windows/Python 3.11.15, 50.383s
(14 backend + 20 API). Các PR đã merge vào develop sau khi đủ bốn job CI:
[contract #27](https://github.com/KhoaTryHard/travel-reservation-2pc/pull/27),
[API nền #28](https://github.com/KhoaTryHard/travel-reservation-2pc/pull/28),
[fault/recovery #29](https://github.com/KhoaTryHard/travel-reservation-2pc/pull/29),
[load/adapter #30](https://github.com/KhoaTryHard/travel-reservation-2pc/pull/30).
[CI 34 test trên bốn môi trường](https://github.com/KhoaTryHard/travel-reservation-2pc/actions/runs/42576693232).
Tiến độ nghiệm thu: W02a + W02b = 8h kế hoạch, tức 8/42h của Khoa và 8/126h của nhóm.
W01/W02c còn pending, W03/W04 chưa nghiệm thu nên chưa cộng. Số liệu là trạng thái
09/11; các cập nhật tiếp theo lấy từ issue điều phối, không xem bảng này là số liệu realtime.

| Nhóm hành vi | Bằng chứng tự động | Phần nhóm phải kiểm tra thật |
|---|---|---|
| Booking/abort/retry/conflict | API với RPC thật và DB tạm | Double-click, nhiều tab, reload/localStorage trên UI |
| Freshness/unknown/ACK | Envelope/cache, lỗi trước/sau begin, COMMIT chờ ACK | Nhãn UI khi polling, giữ focus/bàn phím |
| Crash/recovery | Coordinator trước/sau quyết định, Hotel mất ACK, tồn chỉ giảm một lần | Thao tác crash/restart qua UI dùng manager thật |
| Fault/load | Role whitelist, finite delay, giới hạn, nền, một đợt, tồn khớp commit | Điều khiển demo, tiến độ và kết quả trên browser |
| Lifecycle | Adapter double, lỗi 503/409, tách khỏi RPC health | Cổng bận, đúng child, cleanup, giữ DB, không ảnh hưởng tiến trình ngoài |
| HTTP | Host/Origin/session/JSON/giới hạn request | UI cùng nguồn khi tích hợp entry cuối |

## Checklist chốt các mốc

- [ ] M1 13/11: web success/abort/retry, start/stop/restart đúng child, DB giữ nguyên; ghi commit/log/ảnh.
- [ ] M2 15/11: crash trước/sau quyết định, replay/ACK, load không oversell; giữ DB giữa crash/recovery.
- [ ] G10 16/11: các bộ test phù hợp đạt, CI bốn job trên head cần merge, không còn lỗi chặn demo; khóa tính năng.
- [ ] M3 18/11: W08/W11a đã ghép; MSSV/lớp/GV xác nhận; Word/Markdown/Drive/source khớp; Chrome/Edge máy demo có bằng chứng.
- [ ] D13 19/11: diễn tập 12–15 phút; Khoa giải thích quyết định/ACK, Hữu khóa/persistence, Khánh thao tác web.
- [ ] M4 20/11: test lại bản sau sửa lỗi, gói sạch không DB thử/secret, ghi commit/Python/browser, Khoa duyệt bản nộp.
- [ ] Mở PR develop → main, đủ CI/thảo luận/nhánh cập nhật, dùng merge commit; không bypass bảo vệ nhánh.

## Nhịp điều phối

Trước 20:00 mỗi người cập nhật issue: bằng chứng, effort còn lại, dự báo bàn giao,
blocker bắt đầu lúc nào/cần ai/hạn gỡ. Khoa chốt lúc 20:15. Tối đa một issue Doing/người.
Thiếu đầu vào phải ghi rõ, không tự tuyên bố thành viên đã đồng thuận. Blocker quá 4 giờ
làm việc hoặc dự báo hụt gate cần phương án trong 2 giờ làm việc; chưa biết lịch làm việc
thì không suy số giờ blocker từ thời gian lịch. Giữ hạn gốc và dự báo riêng.

Tiến độ nghiệm thu tính tổng effort kế hoạch của task Done có bằng chứng /126h;
không tính issue điều phối, giờ đã dùng, task not planned hay phần contract còn pending.
Không đóng W03/W04 chỉ vì đã có test API hoặc checklist này.
