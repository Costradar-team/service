import json
from flask import Flask, jsonify, render_template
 
app = Flask(__name__)
 
 
@app.route("/")#사람이 읽는 페이지
def home():
    # templates/dashboard.html 을 찾아서 브라우저에 보여준다
    return render_template("dashboard.html")
 
 
@app.route("/api/prices")#기계가 보는 데이터
def api_prices():
    # mock_data.json 파일을 열어서 그대로 내려준다.
    # (2단계에서는 이 부분을 requests.get(진짜API주소).json() 으로 교체)
    with open("mock_data.json", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)
 
 
if __name__ == "__main__":
    app.run(debug=True)
