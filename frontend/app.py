"""app.py의 내용->화면을 띄우는 역할
   실제 데이터는 app.js가 FastAPI 백엔드(127.0.0.1:8000)에서 직접 받아옵니다.
"""
from flask import Flask, render_template

app = Flask(__name__)


@app.route("/")
def index():
    # templates/index.html 을 브라우저로 보냄
    return render_template("index.html")


if __name__ == "__main__":
    # 포트 5001: FastAPI 백엔드가 CORS로 허용하는 포트라 실시간 연결이 됩니다.
    app.run(host="127.0.0.1", port=5001, debug=True)
