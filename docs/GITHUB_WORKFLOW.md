# Quy trình làm việc trên GitHub

Tài liệu này áp dụng cho tất cả thành viên cộng tác trong dự án **HTPT — Đặt chuyến du lịch với Two Phase Commit** (`travel-reservation-2pc`).

## 1. Các nhánh chính

- `main`: phiên bản ổn định, sẵn sàng demo hoặc bàn giao. Chỉ cập nhật qua pull request đáp ứng các điều kiện merge.
- `develop`: nhánh tích hợp công việc đã hoàn thành, điểm bắt đầu cho tính năng mới và nhánh mặc định trên GitHub.

Luồng thông thường: `nhánh công việc → develop → main`.

Không commit trực tiếp, force-push hoặc xóa `main`, `develop`. Bảo vệ nhánh áp dụng cho cả admin: cần pull request, giải quyết hết thảo luận, CI đạt và nhánh công việc cập nhật với nhánh đích. Không bắt buộc người khác Approve; tác giả có quyền ghi được tự merge PR khi đủ các điều kiện này. Review chéo là hoạt động khuyến khích.

## 2. Đặt tên nhánh

Dùng dạng `<type>/<issue>-<mo-ta-ngan>`; viết thường, dùng dấu gạch ngang, không dấu và không chứa khoảng trắng. Có thể bỏ số issue với công việc nhỏ, ví dụ `docs/github-workflow`.

- `feat/` — tính năng mới, ví dụ `feat/12-coordinator-recovery`.
- `fix/` — sửa lỗi, ví dụ `fix/18-duplicate-booking`.
- `docs/` — tài liệu, ví dụ `docs/2pc-demo-guide`.
- `refactor/` — tái cấu trúc không đổi hành vi bên ngoài.
- `test/` — bổ sung hoặc chỉnh sửa kiểm thử.
- `chore/` — bảo trì, cấu hình hoặc công cụ.
- `hotfix/` — sửa lỗi khẩn cấp trên bản demo hoặc bàn giao; cần thống nhất với leader.

Tạo nhánh công việc từ `develop`:

```bash
git switch develop
git pull --ff-only origin develop
git switch -c feat/12-coordinator-recovery
```

Riêng `hotfix` tạo từ `main`. Nhánh do Codex tạo có thể dùng tiền tố `codex/`, ví dụ `codex/setup-github-workflow`; vẫn tuân theo cùng quy tắc PR, review và CI.

## 3. Commit

Theo Conventional Commits; phần scope là tùy chọn:

```text
<type>(<scope>): <mô tả ngắn ở dạng mệnh lệnh>
```

Ví dụ:

```text
feat(coordinator): replay durable decisions after restart
fix(participant): prevent duplicate inventory updates
docs: add GitHub collaboration workflow
refactor(rpc): extract request validation
test(recovery): cover lost commit acknowledgement
ci: run system tests on Linux and Windows
```

Dùng các type phù hợp như `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build`, `ci`, `perf`, `revert`. Mỗi commit tập trung vào một thay đổi có ý nghĩa. Thêm phần thân hoặc `BREAKING CHANGE:` khi cần giải thích thay đổi lớn hoặc không tương thích ngược.

Tên nhánh và Conventional Commits được kiểm tra qua checklist và review; chưa có bot hoặc hook bắt buộc riêng.

## 4. Pull request

1. Cập nhật nhánh công việc từ nhánh đích trước khi merge; xử lý xung đột và chạy lại kiểm thử phù hợp. Nhánh đích thông thường là `develop`.
2. Mở PR từ nhánh công việc vào `develop`. PR phát hành đi từ `develop` vào `main`. PR hotfix đi vào `main`; sau khi merge, mở PR `main → develop` để đồng bộ bản sửa.
3. Tiêu đề PR theo Conventional Commits, ví dụ `feat(coordinator): add recovery replay` hoặc `docs: add GitHub workflow`.
4. Dùng mẫu PR để mô tả mục đích, liên kết issue, lệnh kiểm thử và kết quả. Nêu rõ thay đổi RPC contract, SQLite schema, cấu hình hoặc bước nâng cấp dữ liệu nếu có.
5. Giữ PR nhỏ, chỉ giải quyết một mục tiêu. Không đưa secret, dữ liệu cá nhân, SQLite DB thử nghiệm hoặc file sinh tự động không cần thiết vào PR.
6. Tác giả tự kiểm tra thay đổi và xử lý góp ý nếu có. Khuyến khích review chéo để kiểm tra tính đúng đắn, bất biến 2PC và khả năng bảo trì; không yêu cầu Approve hoặc review lại làm điều kiện merge. Tác giả có quyền ghi được tự merge sau khi đủ CI và các điều kiện còn lại.
7. Giải quyết hết thảo luận và chờ đủ bốn job CI đạt: Ubuntu/Windows với Python 3.10/3.13. Nhánh phải cập nhật với nhánh đích; không bỏ qua yêu cầu, kể cả admin.
8. Dùng **Squash and merge** cho PR từ nhánh công việc, đặt tiêu đề squash theo Conventional Commits. Dùng **Create a merge commit** cho PR `develop → main` và `main → develop` để giữ quan hệ lịch sử hai nhánh dài hạn. Nhánh công việc được tự động xóa sau merge; hai nhánh chính được bảo vệ khỏi xóa.

Chạy cùng lệnh CI ở máy cá nhân, không cần cài thêm thư viện:

```bash
python -m unittest discover -s tests -v
```

CI chạy khi push hoặc mở/cập nhật PR vào `main`, `develop`; khi PR được mở lại hoặc đồng bộ bằng commit mới; và khi chạy thủ công qua Actions sau khi workflow đã có trên nhánh mặc định. Mỗi job có giới hạn 10 phút, token chỉ có quyền đọc mã nguồn. Trên Windows có thể dùng `py -3` thay cho `python`.

## 5. Đồng bộ và giải quyết xung đột

- Thường xuyên cập nhật nhánh công việc từ `develop` để phát hiện xung đột sớm. Nhánh hotfix cập nhật từ `main`.
- Ưu tiên `git pull --ff-only` trên các nhánh dùng chung; không rebase hoặc force-push nhánh của người khác.
- Để cập nhật nhánh công việc mà giữ lịch sử chung, chạy `git fetch origin` rồi `git merge origin/develop` trên nhánh đó; với hotfix dùng `origin/main`.
- Khi có xung đột, trao đổi với tác giả phần thay đổi liên quan, giải quyết có chủ đích rồi chạy lại các kiểm tra phù hợp trước khi cập nhật PR.
- Thay đổi giao thức hoặc dữ liệu cần báo cả nhóm và cập nhật README. Đặc biệt kiểm tra idempotency, khóa PREPARED, quyết định bền vững và replay sau crash.

## 6. Phát hành và bàn giao

- Leader hoặc người được ủy quyền mở PR `develop → main` khi nhóm đã chốt phạm vi demo hoặc bàn giao.
- Kiểm tra trực tiếp bản ứng viên trên `develop`: toàn bộ CI đạt, các PR trong phạm vi đáp ứng điều kiện merge, demo phù hợp đã chạy và ghi chú phát hành đã chuẩn bị. Kiểm thử ngay trên `develop` thay cho một nhánh môi trường trung gian.
- PR phát hành không yêu cầu người khác Approve; vẫn phải đủ các điều kiện bảo vệ `main`. Chọn **Create a merge commit** khi merge.
- Sau phát hành, nếu `main` có thay đổi chưa có trên `develop`, mở PR `main → develop`, cũng dùng merge commit. Bắt buộc đồng bộ sau hotfix để không mất bản sửa trong lần phát hành sau.
- Ghi nhận lỗi khi kiểm thử bằng issue và liên kết PR sửa lỗi. Ghi rõ commit đã kiểm thử và bằng chứng thực tế; không ghi kết quả chưa chạy.

## 7. Issue và phối hợp

- Dùng mẫu **Báo lỗi** hoặc **Đề xuất công việc**; mô tả bối cảnh, kết quả mong đợi và tiêu chí hoàn thành. Công việc nhỏ có thể không cần issue riêng.
- Gán người phụ trách và nhãn phù hợp nếu dự án đã cấu hình nhãn.
- Với lỗi 2PC, kèm bước tái hiện, lệnh chạy, hệ điều hành/Python, trạng thái giao dịch, log và thời điểm crash nếu có; loại bỏ dữ liệu nhạy cảm.
- Trao đổi quyết định ảnh hưởng nhiều thành viên trên issue hoặc PR để lưu lịch sử. Vai trò và vòng review của nhóm được mô tả trong [kế hoạch nhóm](plan.md).
- Không đưa token xác thực, mật khẩu hoặc dữ liệu nhạy cảm vào issue, commit hay PR. Nếu phát hiện đã lộ, báo leader và thu hồi thông tin xác thực.

## 8. Checklist trước khi merge

- [ ] PR đúng nhánh đích và liên kết issue liên quan nếu có.
- [ ] Tên nhánh, tiêu đề PR và commit tuân theo quy ước.
- [ ] Đã chọn đúng phương thức merge cho nhánh công việc hoặc hai nhánh chính.
- [ ] Tác giả đã tự kiểm tra thay đổi, xử lý góp ý nếu có và giải quyết hết thảo luận; không bắt buộc người khác Approve.
- [ ] Nhánh đã cập nhật với nhánh đích và cả bốn job CI đều đạt.
- [ ] Kiểm thử/demo phù hợp với thay đổi đã chạy và ghi kết quả thực tế.
- [ ] Tài liệu, RPC contract, cấu hình hoặc hướng dẫn nâng cấp dữ liệu liên quan đã được cập nhật.
- [ ] Không có secret, DB thử nghiệm, file không cần thiết hoặc thay đổi ngoài phạm vi.
