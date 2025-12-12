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

# 資料庫連線 (請確認這裡是你剛剛測試成功的 Supabase 網址)
connection_string = "postgresql://postgres:jhmc8653eee7@db.abhwicdwbxjdlfholdnb.supabase.co:5432/postgres"
app.config['SQLALCHEMY_DATABASE_URI'] = connection_string
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# LINE Bot 設定 (請填入你的 Token 與 Secret)
line_bot_api = LineBotApi('FuWgutKbXWdLQQkQox1SZ1+dbNMRh7cQgBoeF+sAfq32UK/Djcs9QksAA4U/zDHLQNSbleXfp4R6v5A6ed/bG+TesYLBN1ij8x3eOpRDc2Lt4IklhbGLCziWs8zMFElvhKnEGHuODeADfNJ7n+0NiwdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('d508be9c1ed17e4ba44374d15ccaa3e1')

# ==========================================
# 2. 資料表模型 (跟之前一樣)
# ==========================================
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    line_user_id = db.Column(db.String(50), unique=True) # 綁定後會有值
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
# 3. LINE Webhook 入口 (LINE 伺服器會呼叫這裡)
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
# 4. 訊息處理邏輯 (機器人的大腦)
# ==========================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip() # 使用者傳來的訊息
    line_id = event.source.user_id   # 使用者的 LINE ID
    
    reply_text = ""

    # --- 邏輯 A: 如果使用者輸入的是 Email ---
    # (簡單判斷是否包含 @ 和 .)
    if "@" in msg and "." in msg:
        # 去資料庫找這個 Email
        user = User.query.filter_by(email=msg).first()
        
        if user:
            # 找到人！進行綁定 (把 LINE ID 存進去)
            user.line_user_id = line_id
            db.session.commit()
            
            # 查詢他修了什麼課
            course_list = []
            for enrollment in user.enrollments:
                course_list.append(enrollment.course.course_name)
            
            courses_str = "\n".join(course_list)
            reply_text = f"哈囉 {user.name}！\n綁定成功 ✅\n\n您目前報名的課程有：\n{courses_str}"
        else:
            reply_text = "找不到這個 Email 耶 😅\n請確認您輸入的是報名時填寫的信箱。"

    # --- 邏輯 B: 如果使用者輸入「簽到」 ---
    elif msg == "簽到":
        # 先確認這個 LINE ID 是誰
        user = User.query.filter_by(line_user_id=line_id).first()
        
        if user:
            # 這裡示範「只要有報名就全部簽到」，未來可以改成「只簽到當天的課」
            updated_count = 0
            for enrollment in user.enrollments:
                if enrollment.check_in_time is None: # 如果還沒簽過
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

    # 回傳訊息給使用者
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)