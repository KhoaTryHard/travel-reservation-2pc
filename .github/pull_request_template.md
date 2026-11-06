## Mục đích

<!-- Mô tả vấn đề và hành vi sau thay đổi. Tiêu đề PR theo Conventional Commits. -->

## Issue liên quan

<!-- Ví dụ: Closes #12. Ghi "Không có" nếu là công việc nhỏ không có issue. -->

## Cách kiểm thử và kết quả

<!-- Ghi lệnh đã chạy và kết quả thực tế; bổ sung log/demo crash hoặc recovery nếu liên quan. -->

```text
python -m unittest discover -s tests -v
```

## Ảnh hưởng và bước áp dụng

<!-- Nêu thay đổi RPC contract, SQLite schema, cấu hình hoặc tài liệu; ghi "Không có" nếu không áp dụng. -->

## Checklist

- [ ] PR đúng nhánh đích: công việc → develop; hotfix/phát hành → main; đồng bộ main → develop.
- [ ] Tên nhánh, commit và tiêu đề PR theo [quy trình GitHub](https://github.com/KhoaTryHard/travel-reservation-2pc/blob/develop/docs/GITHUB_WORKFLOW.md).
- [ ] Chọn squash cho nhánh công việc; merge commit cho develop → main hoặc main → develop.
- [ ] Nhánh đã cập nhật với nhánh đích; cả bốn job CI đạt.
- [ ] Kết quả kiểm thử/demo được ghi đúng thực tế.
- [ ] Tài liệu và hướng dẫn RPC/schema/cấu hình liên quan đã cập nhật.
- [ ] Không chứa secret, DB thử nghiệm hoặc thay đổi ngoài phạm vi.
- [ ] Có một thành viên khác phê duyệt và mọi thảo luận đã được xử lý trước merge.
