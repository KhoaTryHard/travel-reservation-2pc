# Kế hoạch nhóm trong 14 ngày

Vai trò tạm: SV1 là leader và coordinator, SV2 là Resource Managers, SV3 là client và chaos. Thay bằng họ tên/MSSV khi nhận việc. Ngày 1 là ngày nhóm chốt khởi động; nếu bắt đầu 05/11/2026 thì Ngày 14 là 18/11/2026. Đây là kế hoạch đề xuất, không phải lịch sử làm việc đã diễn ra. Mã nguồn bàn giao là baseline đã kiểm thử, nhóm cần đọc, kiểm chứng, nhận quyền sở hữu và hoàn thiện hồ sơ.

## Lịch thực hiện

| Ngày | SV1 | SV2 | SV3 | Cổng kiểm tra |
|---|---|---|---|---|
| 1 | Chốt phạm vi, backlog | Mô hình tồn và booking | Môi trường, kịch bản demo | Đồng thuận phạm vi và DoD |
| 2 | State machine và RPC | Schema và khóa | Contract client và test | API v1 được cả nhóm duyệt |
| 3 | BEGIN và token | PREPARE và tentative writes | CLI begin/status | Test từng module |
| 4 | END và biểu quyết | COMMIT/ABORT idempotent | CLI booking/load | Luồng thành công và hết chỗ |
| 5 | Quyết định bền vững | Nhật ký và khóa bền vững | Chaos delay/crash | Log được lưu trước phản hồi |
| 6 | Recovery coordinator | Restart participant | Test crash tự động | Tích hợp lần 1 |
| 7 | Review code và sửa lỗi | Kiểm tra tồn và rollback | Smoke test toàn hệ thống | M1 đạt happy path và abort |
| 8 | Replay và ACK | Tombstone thông điệp muộn | Test timeout và retry | Không trừ tồn hai lần |
| 9 | Test trước/sau quyết định | Kiểm tra khóa sau crash | Load đồng thời | M2 đạt ma trận lỗi |
| 10 | Rà soát bất biến | Review persistence | Chạy toàn bộ test | Đóng băng tính năng |
| 11 | Viết phân tích, kiến trúc | Viết dữ liệu và recovery | Viết test, hướng dẫn chạy | Báo cáo bản 1 |
| 12 | Ghép và rà báo cáo | Kiểm tra code sạch | Chạy trên máy demo | M3 bản ứng viên bàn giao |
| 13 | Điều phối diễn tập | Demo participant recovery | Demo chaos và retry | Sửa lỗi, dự phòng |
| 14 | Chốt Word và đóng gói | Kiểm tra mã nguồn cuối | Test lại và ghi bằng chứng | M4 nghiệm thu, nộp |

## WBS và ước lượng effort

| Mã | Hạng mục | Chủ trì | Effort giờ | Hạn | Điều kiện Done |
|---|---|---|---|---|---|
| W01 | Phạm vi và giao thức | SV1 | 6 | D2 | API, trạng thái được SV2/SV3 duyệt |
| W02 | Coordinator và idempotency | SV1 | 12 | D5 | begin/end/abort, token conflict đúng |
| W03 | Recovery và ACK | SV1 | 10 | D9 | Crash trước/sau quyết định đúng |
| W04 | Quản lý, tích hợp, báo cáo | SV1 | 14 | D14 | Mốc duyệt, báo cáo và release đầy đủ |
| W05 | Schema và khóa | SV2 | 10 | D3 | Không oversell; khóa tồn tại sau restart |
| W06 | Prepare commit abort | SV2 | 14 | D6 | Replay không trừ tồn trùng |
| W07 | Recovery và late message | SV2 | 10 | D9 | PREPARED chờ; tombstone chặn prepare muộn |
| W08 | Tài liệu, review, demo | SV2 | 8 | D14 | Giải thích được dữ liệu, log và rollback |
| W09 | RPC client và retry | SV3 | 10 | D4 | Token ổn định, báo outcome unknown |
| W10 | Chaos injector | SV3 | 10 | D6 | Crash thật và timeout có thể tái lập |
| W11 | Kiểm thử tích hợp | SV3 | 14 | D10 | 14 test đạt, lưu log thực tế |
| W12 | Đóng gói và diễn tập | SV3 | 8 | D14 | Máy sạch chạy theo README |

Tổng 126 giờ công, mỗi người 42 giờ/14 ngày, trung bình 3 giờ/ngày. Đây là ước lượng, không phải số giờ đã thực hiện. D13–D14 có thời gian sửa lỗi và chốt hồ sơ; không nhận tính năng mới sau D10.

## Quản lý tiến độ

Bảng công việc dùng Backlog → Ready → Doing → Review → Done; lỗi chặn dùng nhãn Blocked. Mỗi thẻ có mã WBS, người làm, người review, ngày hạn, effort còn lại, điều kiện nghiệm thu và link commit/test. Mỗi người tối đa 1 thẻ Doing. Cập nhật mỗi ngày trước 20:00 giờ Việt Nam; họp 15 phút lúc 20:00 để nêu việc xong, việc tiếp theo và blocker. Đây là lịch sinh hoạt đề xuất, chưa tạo sự kiện hoặc automation.

Leader kiểm tra tiến độ bằng task được nghiệm thu, không dựa vào số dòng code. Công thức: tổng effort kế hoạch của task Done / 126 × 100%. Chỉ ghi Done khi code đã review và có test/bằng chứng; không điền % giả định. Baseline kỹ thuật đã có 14 test đạt, nhưng mức hoàn thành và số giờ của từng thành viên chưa được xác nhận.

Nếu task chậm hơn 1 ngày hoặc blocker quá 4 giờ làm việc, báo leader ngay. Leader ưu tiên sửa correctness trước giao diện/tính năng bổ sung; điều chuyển việc test/tài liệu mà vẫn giữ chủ sở hữu module. Sau D10 chỉ sửa lỗi và tài liệu. Không cắt log, idempotency, recovery hoặc kiểm thử crash để kịp hạn.

Nhánh gợi ý: feature/coordinator, feature/resource-managers, feature/client-chaos; main luôn chạy được. Mỗi PR có mục tiêu, cách kiểm thử, log và 1 người duyệt. SV2 review SV1, SV3 review SV2, SV1 review SV3. Thay đổi API/schema phải báo cả nhóm và cập nhật README trước khi merge.

## Checklist nghiệm thu

- [ ] Nhóm điền tên, MSSV, lớp, giảng viên và ngày bắt đầu thật.
- [ ] Cả ba sinh viên giải thích được 2PC, blocking và ranh giới commit.
- [ ] Chạy 14 test trên máy thuyết trình, đính kèm log riêng.
- [ ] Demo đủ success, abort, retry, concurrent, timeout, crash/recovery.
- [ ] Đối chiếu tồn kho và khóa của cả hai DB sau recovery.
- [ ] Word thống nhất với code; không ghi kết quả thử nghiệm chưa chạy.
- [ ] ZIP không có dữ liệu cá nhân, DB thử hoặc thư viện môi trường.
- [ ] Leader duyệt bản nộp cuối và thành viên xác nhận phần mình.
