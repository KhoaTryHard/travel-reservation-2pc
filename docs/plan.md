# Kế hoạch 14 ngày xây dựng dashboard trình diễn 2PC

## Mục tiêu và trạng thái

Sản phẩm mục tiêu là ứng dụng web cục bộ đặt combo một chỗ máy bay và một phòng khách sạn, quan sát giao dịch 2PC và điều khiển Coordinator, Airline, Hotel trên trình duyệt. CLI tiếp tục phục vụ kiểm thử và đối chiếu. Xem [đặc tả dashboard](DASHBOARD_SPEC.md) để triển khai UI, API, quản lý tiến trình và demo.

**Cập nhật 09/11:** API độc lập của Khoa được triển khai qua các PR W02; UI và bộ quản lý tiến trình thật chưa tích hợp. Backend ba dịch vụ, CLI và 14 bài kiểm thử tiếp tục là nền tảng. Tại commit `1fc1b274de111f009a3746a894038a8fe72501fa`, [CI](https://github.com/KhoaTryHard/travel-reservation-2pc/actions/runs/42186762833) đã chạy đủ 14 bài và đạt trên Ubuntu/Windows với Python 3.10/3.13. Bằng chứng API mới ghi trong PR và bản bàn giao, không thay cho nghiệm thu dashboard hoặc diễn tập trên máy thuyết trình.

Giữ 14 ngày, tổng **126 giờ công dự kiến, mỗi người 42 giờ**, với điều kiện tận dụng backend hiện có. Nhóm đã xác nhận D1 = **07/11/2026**, D14 = **20/11/2026**. Giữ hạn gốc W01 08/11; dự báo bàn giao đầy đủ mới 10/11 và overview thật 10/11. Theo dõi [issue điều phối #3](https://github.com/KhoaTryHard/travel-reservation-2pc/issues/3); không coi effort kế hoạch là giờ đã tiêu. Đăng nhập khách hàng, thanh toán, lịch bay/ngày nhận phòng và triển khai Internet ngoài phạm vi bản này.

## Phân công và WBS

| Mã | Công việc | Chủ trì | Giờ | Hạn | Điều kiện nghiệm thu |
|---|---|---|---:|---|---|
| W01 | Rà soát Coordinator và contract | Nguyễn Đăng Khoa — SV1 | 8 | D2 | Giải thích được token, quyết định bền vững, ACK/replay; chốt contract web |
| W02 | API dashboard, tổng hợp và tải | SV1 | 14 | D7 | Booking/retry, overview, chi tiết, load dùng dữ liệu thật |
| W03 | Tích hợp và review | SV1 | 8 | D10 | UI/API/lifecycle tích hợp, xử lý lỗi từng node |
| W04 | Kiến trúc và báo cáo | SV1 | 12 | D14 | Báo cáo/source thống nhất, review và bản nộp |
| W05 | Rà soát RM và khóa | Trần Lê Quang Hữu — SV2 | 8 | D2 | Chốt cách hiển thị tồn, PREPARED và khóa |
| W06 | Bộ quản lý tiến trình | SV2 | 18 | D7 | Chạy/dừng/restart đúng child; giữ DB; xử lý cổng bận |
| W07 | Kiểm thử lifecycle/recovery | SV2 | 10 | D10 | Web sống khi node chết; recovery dùng DB cũ; không tác động tiến trình ngoài |
| W08 | Tài liệu dữ liệu/vận hành | SV2 | 6 | D12 | Giải thích lifecycle, lưu bền vững và giới hạn |
| W09 | Dashboard tiếng Việt | Dương Quốc Khánh — SV3 | 20 | D9 | Đặt/retry, tồn, giao dịch, chi tiết, điều khiển demo |
| W10 | Kiểm thử browser/demo | SV3 | 12 | D10 | Double-click/reload, stale/unknown, fault/load, demo chính |
| W11 | Hướng dẫn và đóng gói | SV3 | 10 | D14 | Máy demo Chrome/Edge, bằng chứng thật, gói chạy sạch |

SV1 sở hữu Coordinator/API web; SV2 sở hữu RM/process manager; SV3 sở hữu frontend/demo. Khuyến khích review chéo: SV2 review SV1, SV3 review SV2, SV1 review SV3; không bắt buộc người khác Approve để merge. Bổ sung kiểm thử API/lifecycle/browser bên cạnh 14 kiểm thử backend. Không đổi ý nghĩa RPC hoặc 2PC để phục vụ animation.

## Lịch thực hiện

| Ngày | SV1 — Nguyễn Đăng Khoa | SV2 — Trần Lê Quang Hữu | SV3 — Dương Quốc Khánh | Cổng kiểm tra |
|---|---|---|---|---|
| D1 | Xác minh baseline, backlog | Rà RM/DB/khóa | Rà CLI, luồng màn hình | Nhận quyền sở hữu |
| D2 | Chốt API/trạng thái | Chốt lifecycle/cổng/DB | Chốt bố cục và test plan | Đồng thuận contract/demo |
| D3 | Overview và health | Start tiến trình sở hữu | Khung UI, thẻ dịch vụ/tồn | Đọc dữ liệu thật |
| D4 | Booking và tra cứu | Stop/restart giữ DB | Form đặt/bảng giao dịch | Booking end-to-end |
| D5 | Unknown/retry | Cổng bận/chống lặp | Lưu token/payload, reload | Không đặt trùng |
| D6 | Chi tiết, fault, load | Giám sát crash/log | Chi tiết ACK/events, lifecycle | Web sống khi node chết |
| D7 | Tích hợp/review | Ownership/cleanup | Chạy success/abort trên web | M1 — happy path và lifecycle |
| D8 | Replay/ACK | PREPARED qua restart | Fault và mất kết nối | Crash trước/sau quyết định |
| D9 | Load/timeout | Tombstone/recovery | Hoàn thiện fault/load/retry | M2 — đủ demo chính |
| D10 | Hồi quy/tích hợp | Test lifecycle/cổng/recovery | Browser test, khóa scope | Backend/API/lifecycle/UI đạt |
| D11 | Kiến trúc/báo cáo | Dữ liệu/vận hành | Hướng dẫn/checklist | Tài liệu đúng thực tế |
| D12 | Ghép báo cáo | Kiểm tra nguồn/gói | Chrome/Edge trên máy demo | M3 — bàn giao thử |
| D13 | Điều phối diễn tập | Demo khóa/recovery | Thao tác web, lưu bằng chứng | Sửa lỗi, giải thích module |
| D14 | Duyệt bản nộp | Chốt dữ liệu/log | Test cuối/đóng gói | M4 — nghiệm thu |

Sau D10 chỉ sửa lỗi, tài liệu và hoàn thiện demo đã chốt. Nếu trễ, giảm trang trí/animation trước; giữ đủ lifecycle trên web, logging, recovery và idempotency. Báo leader khi chậm hơn một ngày hoặc blocker quá bốn giờ làm việc.

## Kiến trúc và demo mục tiêu

Lệnh **dự kiến sau triển khai**: `python run_dashboard.py`; mở `http://127.0.0.1:8080`. Dashboard/process manager chạy ngoài Coordinator; một node crash không dừng web hoặc các node còn lại. Ba dịch vụ cổng 8000–8002 giữ ba SQLite DB riêng. HTML/CSS/JavaScript thuần và Python chuẩn. CLI/`run_demo.py` hiện có tiếp tục dùng theo README trong lúc dashboard chưa hoàn thành.

Polling mỗi giây, có timeout và dữ liệu cũ riêng từng node. Lỗi RPC không biến thành tồn 0 hoặc ABORTED. Token/payload lưu trước gửi; retry dùng lại đúng yêu cầu. Phân biệt COMMIT chờ ACK với hoàn tất; PREPARED giữ khóa. Log theo từng node, không có thứ tự thời gian toàn cục.

Demo 12–15 phút: giới thiệu → đặt thành công → retry cùng token → phòng không tồn tại → Coordinator crash trước quyết định, restart để abort → crash sau quyết định, restart để replay commit → tải đồng thời và đối chiếu tồn. Hotel crash/timeout là phần bổ sung. Chuẩn bị DB sạch trước demo; không reset giữa crash và recovery. Xem tiêu chí quan sát trong đặc tả.

## Quản lý tiến độ và GitHub

Bảng công việc: Backlog → Ready → Doing → Review → Done; Blocked là nhãn. Mỗi thẻ có mã WBS, người làm/review, hạn, effort còn lại, điều kiện nghiệm thu và bằng chứng; mỗi người tối đa một thẻ Doing. Đề xuất cập nhật trước 20:00 và họp 15 phút lúc 20:00 giờ Việt Nam; chưa tạo automation.

Tiến độ = tổng effort kế hoạch của task Done / 126 × 100%. Chỉ Done khi tác giả đã tự kiểm tra, xử lý góp ý nếu có và kiểm thử phù hợp đã đạt; review chéo được khuyến khích. Không suy ra tiến độ nhóm từ số test backend; chưa xác nhận giờ công và ngày bắt đầu thật.

Tuân theo [quy trình làm việc trên GitHub](GITHUB_WORKFLOW.md): nhánh công việc từ `develop`, tên dạng `<type>/<issue>-<mo-ta>`; công việc nhỏ có thể bỏ số issue, nhánh Codex dùng `codex/`. PR công việc vào `develop` dùng squash; phát hành `develop → main` và đồng bộ `main → develop` dùng merge commit. Hotfix từ `main` rồi đồng bộ về `develop`. `develop` mặc định, `main` ổn định. Mỗi PR ghi mục tiêu, cách kiểm thử và bằng chứng; không bắt buộc người khác Approve. Tác giả có quyền ghi được tự merge khi xử lý hết thảo luận, cập nhật với nhánh đích và cả bốn job CI đạt; áp dụng cả admin. Review chéo được khuyến khích. Thay đổi API/schema cần báo cả nhóm và cập nhật tài liệu trước khi merge.

## Checklist nghiệm thu

- [ ] Điền MSSV/lớp/giảng viên, xác nhận ngày khởi động và effort thực tế.
- [ ] Cả ba giải thích được 2PC, blocking và quyết định bền vững.
- [ ] Giữ 14 test backend đạt; bổ sung kiểm thử API/lifecycle/browser theo đặc tả.
- [ ] Chạy/dừng/restart đúng child, giữ DB; xử lý cổng bận không tác động tiến trình ngoài.
- [ ] Dashboard hoạt động khi Coordinator/participant crash.
- [ ] Double-click, retry và reload không tạo booking trùng.
- [ ] Hiển thị đúng stale/unknown, PREPARED, COMMIT chờ ACK và hoàn tất.
- [ ] Demo success, abort, retry, concurrent, crash/recovery; đối chiếu tồn/khóa sau phục hồi.
- [ ] Chrome/Edge trên máy thuyết trình đã chạy; lưu log/ảnh thật, không ghi dự đoán thành kết quả.
- [ ] Markdown, Word và bản Drive nhất quán về phạm vi, người làm, lịch, trạng thái.
- [ ] Gói sạch, không có DB thử/secret; leader và từng thành viên xác nhận phần sở hữu.
