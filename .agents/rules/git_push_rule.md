# Git Push Rule
Khi làm việc trong dự án này, sau khi hoàn thành việc sửa đổi hoặc thêm mới code (đã fix xong lỗi hoặc hoàn thành yêu cầu), AI PHẢI tự động đề xuất chạy các lệnh Git để đẩy code lên kho chứa (repository).

Quy trình chuẩn:
1. `git add .`
2. `git commit -m "mô tả thay đổi"`
3. `git push`

(AI sẽ sử dụng công cụ `run_command` để thực hiện hoặc đề xuất các lệnh này để đảm bảo code luôn được cập nhật lên server/Render nhanh chóng).
