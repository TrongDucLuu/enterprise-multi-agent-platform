# Hướng Dẫn Đồng Bộ Dữ Liệu Lead Và Quản Lý Cơ Hội Bán Hàng Trên CRM

## 1. Đồng bộ dữ liệu Lead từ Marketing vào CRM
- Tất cả các Lead thu thập từ Web form và chiến dịch quảng cáo sẽ tự động đổ vào hàng đợi **Inbound Marketing Queue**.
- Hệ thống áp dụng quy tắc Lead Scoring để đánh giá mức độ tiềm năng (Điểm >= 60 là Qualified Lead).

## 2. Quy trình chuyển đổi Lead thành Cơ hội (Opportunity)
1. Nhân viên kinh doanh (Sales Representative) kiểm tra thông tin liên hệ và xác minh nhu cầu khách hàng.
2. Nhấn nút **Convert Lead** trên giao diện Salesforce/CRM.
3. Tạo mới đồng thời 3 thực thể: **Account** (Doanh nghiệp), **Contact** (Người liên hệ), và **Opportunity** (Cơ hội kinh doanh).
4. Gán Stage ban đầu là `Prospecting` hoặc `Qualification`.

## 3. Khắc phục lỗi không xem được báo cáo Pipeline
- Nếu không xem được Dashboard doanh thu theo kỳ, kiểm tra vai trò `Sales_Viewer` hoặc `Sales_Manager`.
- Yêu cầu Admin CRM bổ sung quyền truy cập Sharing Rule cho Folder Báo cáo của phòng ban.
