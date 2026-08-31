# Hướng Dẫn Khắc Phục Lỗi Tạo Purchase Order Trong Hệ Thống ERP SAP S/4HANA

## 1. Mô tả sự cố thường gặp
Khi nhân viên mua hàng thao tác trên SAP GUI qua giao dịch **ME21N** hoặc **ME51N** (Purchase Requisition), hệ thống hiển thị thông báo lỗi:
- `M8-147: Authorization check failed for transaction ME21N.`
- `M3-018: Field Plant/Storage Location is mandatory but missing.`

## 2. Nguyên nhân kỹ thuật
- Người dùng chưa được gán vai trò `Z_PROC_PURCHASER` trong phân hệ SAP MM (Material Management).
- Hồ sơ nhân viên chưa liên kết đúng Mã Công ty (Company Code) hoặc Purchasing Organization `VN01`.

## 3. Quy trình khắc phục chuẩn (Standard Operating Procedure)
1. **Bước 1**: Kiểm tra tài khoản người dùng trên SAP transaction `SU01D` hoặc báo cáo phân quyền `SU53`.
2. **Bước 2**: Yêu cầu phê duyệt từ Trưởng bộ phận Mua hàng qua ticket hệ thống.
3. **Bước 3**: Admin SAP Basis thực hiện gán Role `Z_PROC_PURCHASER` và Plant Authorization tương ứng.
4. **Bước 4**: Yêu cầu người dùng đăng xuất hoàn toàn khỏi SAP GUI (`/nend`), sau đó đăng nhập lại để làm mới User Buffer.
