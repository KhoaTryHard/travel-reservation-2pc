# Bàn giao phần của Khánh (SV3) — Dashboard web, kiểm thử trình duyệt, hướng dẫn demo

Ngày cập nhật: 14/11/2026. Phạm vi: W09 (dashboard tiếng Việt), W10 (kiểm thử trình duyệt/demo), W11 (hướng dẫn, đóng gói).
Mọi con số dưới đây là kết quả đã chạy thật trong tài liệu này hoặc được ghi rõ là chưa chạy.

## Đã làm

| Tệp | Nội dung |
|---|---|
| `web/index.html`, `web/style.css`, `web/app.js` | Dashboard HTML/CSS/JavaScript thuần, tiếng Việt, nền sáng, nhãn chữ đi cùng màu, thao tác bàn phím |
| `run_dashboard.py` | Một lệnh phục vụ UI + API ở `http://127.0.0.1:8080`; nếu có `lifecycle.py` thì tự chạy ba dịch vụ và dọn đúng child khi thoát (giữ DB) |
| `dashboard_api.py` (sửa nhỏ) | Thêm tham số tùy chọn `static_dir` cho `create_http_server`: chỉ phục vụ `.html/.css/.js`, chặn `..`, tệp ẩn, symlink thoát; Host/Origin vẫn kiểm tra; thêm CSP. Cần Khoa review vì là tệp của Khoa |
| `tests/test_dashboard_ui.py` | 6 test cho phục vụ tệp tĩnh và hợp đồng id giữa HTML/JS (chạy trong CI) |
| `tests/browser_check.py` | Kịch bản trình duyệt thật (Playwright), tự dựng cụm trên cổng riêng + DB tạm, chụp ảnh, ghi biên bản |
| `docs/evidence/browser-20261114/` | Biên bản + ảnh của lần chạy ngày 14/11 (xem giới hạn bên dưới) |

### Hành vi UI theo đặc tả
- Ba thẻ dịch vụ: phân biệt tiến trình còn sống với health lỗi; nút khóa khi đang xử lý hoặc chưa nối bộ quản lý; cổng bận hiển thị lỗi 409.
- Tồn kho: tồn / đang giữ (PREPARED) / chưa giữ = max(tồn − giữ, 0); khóa hiện “Tạm khóa — chờ quyết định”; node lỗi hiện “Dữ liệu cũ” kèm giờ đọc cuối, chưa có snapshot thì “Không xác định”, không bao giờ thay bằng 0.
- Đặt combo: lưu token/payload vào localStorage **trước khi gửi**; khóa nút gửi lặp; không dùng lại token với payload khác; mất phản hồi → “Chưa xác định kết quả”, không tự kết luận ABORT; “Gửi lại” dùng đúng yêu cầu đã lưu (kể cả sau reload/nhiều tab); “Kiểm tra” chỉ kết luận khi đọc được Coordinator.
- Bảng giao dịch có lọc, cập nhật theo khóa nên polling không làm mất focus/lựa chọn; chi tiết giao dịch hiển thị payload, ACK, reservation, khóa, events theo seq từng node.
- COMMITTED chưa đủ ACK hiện “đang chờ xác nhận”.
- Gây lỗi theo vai trò (whitelist như API), tải 1–50 booking / 1–8 worker có tiến độ, kết quả từng yêu cầu và đối chiếu tồn trước/sau.

## Cách chạy

```powershell
python run_dashboard.py            # hoặc: py -3 run_dashboard.py
```
Mở `http://127.0.0.1:8080`. Trên máy có nhiều Python (MSYS2, XAMPP…) hãy dùng `py -3`, vì `python` có thể trỏ nhầm.
Tùy chọn: `--port`, `--base-port` (Coordinator; Airline = +1, Hotel = +2), `--data-dir`, `--log-dir`, `--no-autostart`.

**Phụ thuộc:** `lifecycle.py` của Hữu. Trên `develop` hiện tại PR #36 đã bị revert (commit `546c998`), tệp nằm ở commit `32e0e67` / nhánh `feat/13-lifecycle-manager`. Khi thiếu nó, dashboard vẫn chạy nhưng các nút Chạy/Dừng/Khởi động lại bị khóa và có banner giải thích.

## Kết quả kiểm thử đã chạy (14/11/2026)

- `python -m unittest discover -s tests`: **48/48 đạt** trên Linux, Python 3.12.3 (không gồm `tests/test_lifecycle.py` của Hữu vì tệp đó chưa nằm trên `develop`).
- `python tests/browser_check.py`: **17/17 ca PASS** với Chromium 153 headless (Playwright 1.63) trên Linux. Các ca: A00–A05, A08, A09, A11–A15, A17 (xem `docs/evidence/browser-20261114/report.md`), dùng `lifecycle.py` từ commit `32e0e67`.

## Chưa làm / chưa có bằng chứng — phải tự làm

1. **Chạy trên Chrome và Edge ở máy thuyết trình** (M3/A17): `python tests/browser_check.py --browser chrome` rồi `--browser msedge`; lưu `report.md` và ảnh vào issue #8/#9. Lần chạy ở trên không phải Chrome/Edge và không phải Windows.
2. Diễn tập bằng tay 12–15 phút theo bảng bên dưới; nhìn trực tiếp độ rõ khi chiếu (cỡ chữ, tương phản). Ảnh chụp tự động không thay cho việc này.
3. `lifecycle.py` thật phải được merge lại vào `develop` (Hữu); `dashboard_api.py` cần Khoa review thay đổi `static_dir`.
4. Chưa thêm bước chạy trình duyệt vào CI (cần Playwright). Chưa kiểm thử khi dashboard bị restart giữa chừng (token phiên đổi): UI có xử lý 403 `invalid_session` bằng cách lấy token mới rồi gửi lại cùng nội dung, nhưng ca này chưa chạy.
5. Chưa cập nhật bản Word/Drive: báo cáo vẫn ghi dashboard “chưa triển khai”. Chỉ sửa sau khi nhóm chấp nhận các bằng chứng trên, và ghi đúng giới hạn.

## Kịch bản demo cho SV3 (khớp đặc tả mục 6)

| Bước | Thao tác | Quan sát |
|---|---|---|
| 1 | Mở dashboard | Ba thẻ “Đang chạy”, tồn 5/5 |
| 2 | Đặt combo (hotel DEFAULT) | COMMITTED, đủ ACK, tồn 4/4 |
| 3 | Reload trang → Gửi lại | Cùng tx, tồn vẫn 4 |
| 4 | Hotel = MISSING, đặt | ABORTED, không còn khóa, tồn 4 |
| 5 | Arm Coordinator `after_votes` crash → đặt → chờ → Khởi động lại Coordinator → Gửi lại | “Chưa xác định”, hai RM PREPARED + khóa; sau restart ABORTED, khóa được giải phóng |
| 6 | Arm `after_decision` crash → đặt → Khởi động lại → Gửi lại | COMMITTED đủ ACK, tồn 4→3 đúng một lần |
| 7 | Tải 10/4 | Không bán vượt; dòng đối chiếu báo “Khớp” khi đủ ACK |

Lưu ý khi diễn tập: chuẩn bị DB mới trước buổi demo (dừng cả ba dịch vụ, đổi tên `data/` thành `data_backup/`); không reset giữa crash và recovery; không chạy `run_demo.py` cùng lúc (trùng cổng).

## Mẫu ghi biên bản thủ công

```text
Ca Axx / gate / thời điểm Asia/Saigon:
Commit / branch / CI:
OS / Python / browser+version / máy:
Owner thao tác / owner đối chiếu:
Token / tx / run_id:
Các bước, HTTP, state, ACK, tồn và khóa sau:
Ảnh / log:
Kết quả: Pass / Fail / Chưa chạy — lý do:
```
