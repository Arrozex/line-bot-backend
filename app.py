import os
import re
from flask import Flask, request, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

# LINE Bot 相關套件
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

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
# 2. 資料表模型
# ==========================================
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    line_user_id = db.Column(db.String(50), unique=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(50))
    enrollments = db.relationship('Enrollment', backref='user', lazy=True)

class Course(db.Model):
    __tablename__ = 'courses'
    id = db.Column(db.Integer, primary_key=True)
    course_name = db.Column(db.String(100), nullable=False)
    enrollments = db.relationship('Enrollment', backref='course', lazy=True)

class Enrollment(db.Model):
    __tablename__ = 'enrollments'
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(100), db.ForeignKey('users.email'))
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'))
    check_in_time = db.Column(db.DateTime, nullable=True)

# ==========================================
# 3. 健康檢查端點
# ==========================================
@app.route("/", methods=['GET'])
def health_check():
    return 'LINE Bot is running! 🤖', 200

# ==========================================
# 4. LINE Webhook 入口
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
# 5. 訊息處理邏輯
# ==========================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    line_id = event.source.user_id
    
    reply_text = ""

    # --- 邏輯 A: Email 綁定 ---
    if "@" in msg and "." in msg:
        user = User.query.filter_by(email=msg).first()
        
        if user:
            user.line_user_id = line_id
            db.session.commit()
            
            course_list = []
            for enrollment in user.enrollments:
                course_list.append(enrollment.course.course_name)
            
            courses_str = "\n".join(course_list) if course_list else "目前沒有報名課程"
            reply_text = f"哈囉 {user.name}！\n綁定成功 ✅\n\n您目前報名的課程有：\n{courses_str}"
        else:
            reply_text = "找不到這個 Email 耶 😅\n請確認您輸入的是報名時填寫的信箱。"

    # --- 邏輯 B: 簽到 ---
    elif msg == "簽到":
        user = User.query.filter_by(line_user_id=line_id).first()
        
        if user:
            updated_count = 0
            for enrollment in user.enrollments:
                if enrollment.check_in_time is None:
                    enrollment.check_in_time = datetime.now()
                    updated_count += 1
            
            db.session.commit()
            
            if updated_count > 0:
                reply_text = f"{user.name} 您好，已為您完成 {updated_count} 堂課程的簽到！📅"
            else:
                reply_text = "您目前沒有需要簽到的課程，或是都已經簽過囉！"
        else:
            reply_text = "您尚未綁定身分喔！\n請先輸入您的 Gmail 進行綁定。"

    # --- 邏輯 C: 其他訊息 ---
    else:
        reply_text = "請輸入您的 Gmail 來查詢課程與綁定帳號。\n或者輸入「簽到」來進行課程簽到。"

    # 回傳訊息
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
