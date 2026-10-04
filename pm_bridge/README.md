# PM Assistant Bridge

## Cửa sổ Windows và khay hệ thống

Mở EXE mới (hoặc `Chay_Ung_Dung.bat` khi chạy source) để hiện cửa sổ nhỏ.
Cửa sổ hiển thị trạng thái đồng bộ, chế độ tự động, kết nối server (kiểm tra mỗi 30 giây),
và kết quả gần nhất. Có nút Đồng bộ ngay, Bật/Tạm dừng tự động và Mở website.
Nút X thu xuống khay hệ thống. Nhấp đúp biểu tượng để mở lại; menu chuột phải có Thoát.
Thoát sẽ chờ chu kỳ đang chạy hoàn tất trước khi đóng ứng dụng.
Mở EXE lần nữa sẽ đưa cửa sổ đang chạy lên. Nếu bản Web cũ chiếm cổng 5555,
cần thoát bản cũ trước. Settings được dùng chung với website.

Ứng dụng Windows có dashboard tại http://127.0.0.1:5555.

## Bản hiện tại

- Đồng bộ nguyên file mọi định dạng trong nhiều thư mục và thư mục con.
- Giữ cấu trúc đường dẫn tương đối, tách nguồn bằng source_id và máy bằng client_id.
- SHA-256 phát hiện thay đổi; stream dữ liệu từ snapshot trên đĩa, không parse Excel tại client.
- File lỗi sẽ thử lại ở chu kỳ sau; chưa hỗ trợ resume từng phần hay hàng đợi lưu mọi phiên bản offline.
- Chỉ upload file mới/thay đổi; chưa lan truyền thao tác xóa/đổi tên sang server.
- Loại thư mục download và vùng dữ liệu ứng dụng khỏi upload để tránh vòng lặp.
- Outlook Classic: Inbox mặc định, tối đa 500 email/chu kỳ trong số ngày đã chọn.
  Chỉ gửi nội dung email và metadata đính kèm, chưa gửi binary đính kèm.
- Download báo cáo theo polling, file tạm rồi đổi tên; tránh ghi đè, retry xác nhận.
- Settings, lịch sử và nhật ký SQLite. Không cần cài SQLite server.

## Chạy

Chạy `Chay_Giao_Dien_Web.bat`, hoặc bản EXE trong thư mục `releases` mới nhất.
Copy **cả thư mục PMAssistant_Bridge**, gồm `_internal`, sang Windows x64.
Điền URL/API key/client ID, các thư mục nguồn (mỗi dòng một đường dẫn), thư mục download.
Bấm Lưu cấu hình rồi Sync Ngay; bật Tự động để chạy theo chu kỳ.
Lần đầu mặc định tắt tự động để người dùng chọn nguồn và server trước.
Đóng tab trình duyệt không dừng ứng dụng; dừng đúng PMAssistant_Bridge.exe trong Task Manager.
Máy ngủ/tắt hoặc đăng xuất thì ứng dụng không tiếp tục sync.
Outlook COM yêu cầu Outlook Classic có profile đã đăng nhập; New Outlook không hỗ trợ COM này.
Chính sách máy doanh nghiệp vẫn có thể hạn chế thực thi hoặc kết nối.

EXE lưu dữ liệu ở `%LOCALAPPDATA%\PMAssistantBridge`:
- `bridge_state.db`: settings và trạng thái.
- `bridge.log`: lỗi chi tiết, tự xoay vòng.

Chạy source dùng dữ liệu trong `pm_bridge`; có thể đổi bằng `PM_BRIDGE_DATA_DIR`.
Settings từ bản EXE cũ không tự chuyển: cấu hình lại trên web, hoặc sao lưu rồi chuyển database khi ứng dụng đã tắt.

## Reference Hub

`py -3.11 pm_bridge/server_hub_demo/server.py`

Mặc định lắng nghe localhost:8000. Triển khai máy chủ thực bằng ASGI và HTTPS.
Đặt `BRIDGE_API_KEY` giống client và `PM_HUB_DATA_DIR` nếu muốn đổi vùng lưu.
Khóa mặc định chỉ dành cho thử nghiệm local.

API:
- `PUT /api/v1/sync/files?source_id=...&relative_path=...&sha256=...`: raw body,
  header `Content-Type: application/octet-stream`; response phải trả `sha256` đã nhận.
- `POST /api/v1/sync/emails`: client_id, count, emails.
- `GET /api/v1/reports/pending?client_id=...`: danh sách id, filename, sha256.
- `GET /api/v1/reports/download/{id}`: binary.
- `POST /api/v1/reports/confirm`: report_id, client_id.

Tất cả API sync dùng `Authorization: Bearer <key>` và `X-Client-ID`.
Reference hub lưu vào `data/<SHA256(client_id)[:24]>/uploads/<source_id>/<relative_path>`.
Backend đặt file báo cáo vào `data/<SHA256(client_id)[:24]>/reports/`.
Ghi file tạm `.part` rồi đổi tên khi hoàn tất để tránh client đọc báo cáo đang tạo.
Reference hub có SQLite lưu email/xác nhận; chưa có engine phân tích dự án.
Khóa dùng chung cho demo; triển khai nhiều người cần ánh xạ credential riêng tới từng client.

## Kiểm tra và đóng gói

`py -3.11 -m unittest pm_bridge.test_regression -v`

`py -3.11 pm_bridge/build_exe.py`

Build tạo thư mục phiên bản mới trong `releases`, không xóa dữ liệu/bản đang chạy.
`excel_reader.py` và `server_client.py` là module cũ, không dùng trong luồng đồng bộ file hiện tại.
Giao diện dùng CSS và SVG nội bộ, không phụ thuộc CDN. Có các trang Tổng quan,
Thư mục đồng bộ, Báo cáo, Nhật ký và Cài đặt; hỗ trợ màn hình nhỏ.
