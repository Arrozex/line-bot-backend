import os
import re
from flask import Flask, request, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import date, datetime, timedelta

# LINE Bot 相關套件
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, QuickReply, QuickReplyButton, MessageAction

app = Flask(__name__)

# ==========================================
# 1. 設定區 (資料庫 + LINE Bot)
# ==========================================

# 資料庫連線
connection_string = os.environ.get('DATABASE_URL')
if connection_string and connection_string.startswith("postgres://"):
    connection_string = connection_string.replace("postgres://", "postgresql://", 1)

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

app.config['SQLALCHEMY_DATABASE_URI'] = connection_string
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ==========================================
# 2. 資料表模型 (符合實際 PostgreSQL Schema)
# ==========================================
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.BigInteger, primary_key=True)
    line_user_id = db.Column(db.Text, unique=True)
    email = db.Column(db.Text, unique=True, nullable=False)
    name = db.Column(db.Text)
    identity = db.Column(db.Text)  # 新增：身份/科系
    status = db.Column(db.Text, default='free')  # 新增：狀態機
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    
    # 新增關聯，讓 user.enrollments 可用
    enrollments = db.relationship('Enrollment', backref='user', lazy=True, foreign_keys='Enrollment.user_email', primaryjoin='User.email == Enrollment.user_email')

class Course(db.Model):
    __tablename__ = 'courses'
    id = db.Column(db.BigInteger, primary_key=True)
    course_name = db.Column(db.Text, nullable=False)
    course_date = db.Column(db.Date)
    weekday = db.Column(db.Integer)  # 0~6 代表星期
    start_time = db.Column(db.Time)
    end_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    enrollments = db.relationship('Enrollment', backref='course', lazy=True)

class Enrollment(db.Model):
    __tablename__ = 'enrollments'
    id = db.Column(db.BigInteger, primary_key=True)
    user_email = db.Column(db.Text, db.ForeignKey('users.email', ondelete='CASCADE', onupdate='CASCADE'))
    course_id = db.Column(db.BigInteger, db.ForeignKey('courses.id', ondelete='CASCADE'))
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)

# ==========================================
# 3. 輔助函數：發送 Quick Reply
# ==========================================
def send_quick_reply(reply_token, text, button_labels):
    """
    發送帶有 Quick Reply 按鈕的訊息
    
    Args:
        reply_token: LINE reply token
        text: 要顯示的訊息文字
        button_labels: 按鈕文字列表，例如 ["是的，我是", "我只是路過的"]
    """
    quick_reply_buttons = [
        QuickReplyButton(action=MessageAction(label=label, text=label))
        for label in button_labels
    ]
    
    messages = TextSendMessage(
        text=text,
        quick_reply=QuickReply(items=quick_reply_buttons)
    )
    
    line_bot_api.reply_message(reply_token, messages)

# ==========================================
# 4. 健康檢查端點
# ==========================================
@app.route("/", methods=['GET'])
def health_check():
    return 'LINE Bot is running! 🤖', 200

# ==========================================
# 5. LINE Webhook 入口
# ==========================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# ==========================================
# 6. 訊息處理邏輯 (狀態機)
# ==========================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    line_id = event.source.user_id
    
    # 查詢使用者
    user = User.query.filter_by(line_user_id=line_id).first()
    
    reply_text = ""

    # ==========================================
    # 第一層：觸發綁定流程
    # ==========================================
    if msg == "綁定資料":
        if user and user.email and "@" in user.email and not user.email.endswith("@temp"):
            # 已經綁定過了
            reply_text = "您已經綁定過了喔！若要修改資料請輸入「修改資料」。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        else:
            # 還沒綁定，開始綁定流程
            if not user:
                # 建立新使用者，暫時用假 Email
                user = User(
                    line_user_id=line_id, 
                    email=f"{line_id}@temp", 
                    status='check_identity'
                )
                db.session.add(user)
            else:
                user.status = 'check_identity'
            
            db.session.commit()

            # 發送 Quick Reply 按鈕
            send_quick_reply(
                event.reply_token,
                "👋 歡迎使用！請問您是否為「護理相關人員」或「本計畫學員」？",
                ["是的，我是", "我只是路過的"]
            )
        return  # 結束這次回應

    # ==========================================
    # 第二層：狀態機處理
    # ==========================================
    
    # --- 狀態 1: 檢查身分階段 ---
    if user and user.status == 'check_identity':
        if msg == "是的，我是":
            user.status = 'wait_email'  # 通過驗證，下一步問 Email
            db.session.commit()
            reply_text = "太好了！🎉\n\n接下來請輸入您的 「Email」 以進行綁定：\n(我們將會寄送課程資訊給您)"
        
        elif msg == "我只是路過的":
            # 重置狀態，刪除暫存使用者
            db.session.delete(user) 
            db.session.commit()
            reply_text = "沒問題！您依舊可以透過「近期課程」指令了解最新課程資訊哦。😊"
        
        else:
            # 使用者沒按按鈕，自己亂打字
            send_quick_reply(
                event.reply_token,
                "請點選下方的按鈕來確認您的身分喔！👇",
                ["是的，我是", "我只是路過的"]
            )
            return

    # --- 狀態 2: 等待 Email 階段 ---
    elif user and user.status == 'wait_email':
        if "@" in msg and "." in msg:
            # 檢查 Email 是否重複
            check_email = User.query.filter_by(email=msg).first()
            if check_email and check_email.id != user.id:
                reply_text = "這個 Email 已經有人使用囉！請換一個。"
            else:
                user.email = msg  # 更新真正的 Email
                user.status = 'wait_name'
                db.session.commit()
                reply_text = "收到！📧\n接下來，請輸入您於報名系統填入的 「真實姓名」："
        else:
            reply_text = "Email 格式看起來不太對喔，請再檢查一下"

    # --- 狀態 3: 等待姓名階段 ---
    elif user and user.status == 'wait_name':
        user.name = msg
        user.status = 'wait_dept'
        db.session.commit()
        reply_text = f"你好，{msg}！\n最後一步，請輸入您的 「服務單位」 或 「科系」："

    # --- 狀態 4: 等待科系/單位階段 ---
    elif user and user.status == 'wait_dept':
        user.identity = msg
        user.status = 'free'  # 綁定完成，狀態自由
        db.session.commit()
        reply_text = (
            "🎉 恭喜！綁定完成！\n\n"
            "您可以輸入指令，開始使用以下功能：1.「近期課表」2.「已選課程」3.「我的資料」"
        )

    # ==========================================
    # 第三層：功能指令 (已完成綁定的使用者)
    # ==========================================
    
    # --- 近期課程 (所有人都可以查看) ---
    elif msg == "近期課程":
        today = (datetime.utcnow() + timedelta(hours=8)).date()

        courses = Course.query.filter(
            (Course.end_date >= today) | (Course.end_date == None)
        ).order_by(Course.weekday, Course.start_time).all()

        if not courses:
            reply_text = "目前沒有即將進行的課程喔！😅"
        else:
            reply_text = "📋 近期課程一覽：\n----------------------\n"
            days_map = ["一", "二", "三", "四", "五", "六", "日"]
            
            for c in courses:
                day_str = days_map[c.weekday] if c.weekday is not None else "待定"
                time_str = c.start_time.strftime('%H:%M') if c.start_time else "待定"
                
                # 顯示課程名稱與時間
                reply_text += f"🔹 {c.course_name}\n   (週{day_str} {time_str})\n"
                
                if c.end_date:
                    reply_text += f"   ~ 至 {c.end_date} 截止\n"

            google_cal_link = "https://calendar.google.com/..."
            reply_text += f"\n📅 查看完整行事曆：\n{google_cal_link}"

    # --- 預設情況: 其他訊息 ---
    else:
        if not user:
            reply_text = "歡迎！請先輸入「綁定資料」來註冊您的帳號。"

        # --- 我的資料 ---
        elif msg == "我的資料":
            reply_text = (
                f"您的綁定資料：\n\n"
                f"姓名: {user.name or '未設定'}\n"
                f"Email: {user.email}\n"
                f"身分: {user.identity or '未設定'}"
            )

        # --- 已選課程 ---
        elif msg == "已選課程":
            enrollments = Enrollment.query.filter_by(user_email=user.email).all()
            
            if not enrollments:
                reply_text = "您目前還沒有選修任何課程喔！📚"
            else:
                all_courses = [e.course for e in enrollments]
                all_courses.sort(key=lambda x: (x.weekday or 999, x.start_time or datetime.min.time()))

                days_map = ["一", "二", "三", "四", "五", "六", "日"]
                reply_text = "🗓️ 您的課表：\n"
                
                current_weekday_index = -1 
                
                for c in all_courses:
                    # 如果換了一天，就印出分隔線和星期幾
                    if c.weekday != current_weekday_index:
                        weekday_str = days_map[c.weekday] if c.weekday is not None else "待定"
                        reply_text += f"\n【週{weekday_str}】\n"
                        current_weekday_index = c.weekday
                    
                    time_str = c.start_time.strftime('%H:%M') if c.start_time else "待定"
                    reply_text += f"   {time_str} {c.course_name}\n"
                    
        # --- 幫助 ---
        elif msg == "幫助":
            reply_text = "指令清單：1.「近期課程」2.「已選課程」3.「我的資料」"
        
        # --- 其他未知指令 ---
        else:
            reply_text = "您可以輸入「幫助」查看可使用的指令哦！"

    # 回傳訊息
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
