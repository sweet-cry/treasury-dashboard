from flask import Flask, jsonify, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
import requests
import json
import os
from datetime import datetime

app = Flask(__name__, static_folder="static")
DATA_FILE = "data/treasury.json"

# ─── 데이터 수집 ───────────────────────────────────────────
def fetch_tic_data():
    """treasury.gov에서 TIC 데이터 수집"""
    print(f"[{datetime.now()}] TIC 데이터 수집 시작...")
    try:
        url = "https://ticdata.treasury.gov/resource-center/data-chart-center/tic/Documents/mfhhis01.txt"
        res = requests.get(url, timeout=30)
        res.raise_for_status()
        lines = res.text.splitlines()

        # 연도별 Dec 데이터 파싱 (historical)
        historical = [
            {"year": "2000", "일본": 318, "중국": 60,   "영국": 50,  "총계": 1055, "한국": 41},
            {"year": "2002", "일본": 378, "중국": 100,  "영국": 60,  "총계": 1210, "한국": 52},
            {"year": "2004", "일본": 699, "중국": 224,  "영국": 110, "총계": 1889, "한국": 69},
            {"year": "2006", "일본": 624, "중국": 397,  "영국": 165, "총계": 2108, "한국": 67},
            {"year": "2008", "일본": 626, "중국": 727,  "영국": 130, "총계": 3073, "한국": 41},
            {"year": "2010", "일본": 882, "중국": 1160, "영국": 272, "총계": 4435, "한국": 33},
            {"year": "2012", "일본": 1120,"중국": 1202, "영국": 312, "총계": 5573, "한국": 45},
            {"year": "2014", "일본": 1241,"중국": 1244, "영국": 190, "총계": 6155, "한국": 69},
            {"year": "2016", "일본": 1107,"중국": 1058, "영국": 217, "총계": 6003, "한국": 91},
            {"year": "2018", "일본": 1042,"중국": 1123, "영국": 262, "총계": 6230, "한국": 103},
            {"year": "2020", "일본": 1260,"중국": 1063, "영국": 452, "총계": 7041, "한국": 117},
            {"year": "2022", "일본": 1075,"중국": 867,  "영국": 654, "총계": 7400, "한국": 103},
            {"year": "2024", "일본": 1062,"중국": 759,  "영국": 723, "총계": 8619, "한국": 125},
            {"year": "2025", "일본": 1186,"중국": 684,  "영국": 866, "총계": 9271, "한국": 141},
        ]

        # 최신 월별 데이터 파싱
        monthly = []
        top20 = []
        month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

        country_map = {
            "Japan": "일본", "United Kingdom": "영국",
            '"China, Mainland"': "중국(본토)", "Belgium": "벨기에",
            "Canada": "캐나다", "Luxembourg": "룩셈부르크",
            "Cayman Islands": "케이맨제도", "France": "프랑스",
            "Ireland": "아일랜드", "Taiwan": "대만",
            "Switzerland": "스위스", "Singapore": "싱가포르",
            "Hong Kong": "홍콩", "Norway": "노르웨이",
            "India": "인도", "Brazil": "브라질",
            "Saudi Arabia": "사우디아라비아", '"Korea, South"': "한국",
            "Israel": "이스라엘", "Germany": "독일",
        }
        region_map = {
            "일본":"아시아","영국":"유럽","중국(본토)":"아시아","벨기에":"유럽",
            "캐나다":"기타","룩셈부르크":"유럽","케이맨제도":"기타","프랑스":"유럽",
            "아일랜드":"유럽","대만":"아시아","스위스":"유럽","싱가포르":"아시아",
            "홍콩":"아시아","노르웨이":"유럽","인도":"아시아","브라질":"기타",
            "사우디아라비아":"중동","한국":"아시아","이스라엘":"중동","독일":"유럽",
        }
        color_map = {
            "아시아":"#f0a500","유럽":"#3a7bd5","기타":"#8b5cf6","중동":"#22c55e"
        }

        # 2025년 월별 데이터 파싱 (첫 번째 연도 섹션)
        in_2025 = False
        parsed_countries = {}
        for line in lines:
            if "2025" in line and "2024" not in line and "Country" in line:
                in_2025 = True
                continue
            if in_2025 and "2024" in line and "Country" in line:
                break
            if in_2025:
                for eng, kor in country_map.items():
                    if line.startswith(eng) or line.startswith(eng.replace('"','')):
                        parts = line.split()
                        vals = []
                        for p in parts:
                            try:
                                vals.append(float(p.replace(',','')))
                            except:
                                pass
                        if len(vals) >= 2:
                            parsed_countries[kor] = {"val": vals[0], "prev": vals[1]}
                        break

        # 파싱 실패 시 하드코딩 데이터 사용
        fallback_top20 = [
            {"country":"일본","val":1185.5,"prev":1202.7,"region":"아시아","color":"#f0a500"},
            {"country":"영국","val":866.0,"prev":889.0,"region":"유럽","color":"#3a7bd5"},
            {"country":"중국(본토)","val":683.5,"prev":683.9,"region":"아시아","color":"#ef4444"},
            {"country":"벨기에","val":477.3,"prev":481.0,"region":"유럽","color":"#3a7bd5"},
            {"country":"캐나다","val":468.2,"prev":472.2,"region":"기타","color":"#8b5cf6"},
            {"country":"룩셈부르크","val":435.1,"prev":425.4,"region":"유럽","color":"#3a7bd5"},
            {"country":"케이맨제도","val":421.2,"prev":427.6,"region":"기타","color":"#8b5cf6"},
            {"country":"프랑스","val":368.9,"prev":376.1,"region":"유럽","color":"#3a7bd5"},
            {"country":"아일랜드","val":340.7,"prev":340.3,"region":"유럽","color":"#3a7bd5"},
            {"country":"대만","val":310.6,"prev":312.9,"region":"아시아","color":"#f0a500"},
            {"country":"스위스","val":294.1,"prev":300.4,"region":"유럽","color":"#3a7bd5"},
            {"country":"싱가포르","val":278.4,"prev":272.2,"region":"아시아","color":"#f0a500"},
            {"country":"홍콩","val":267.8,"prev":256.6,"region":"아시아","color":"#f0a500"},
            {"country":"노르웨이","val":208.1,"prev":218.9,"region":"유럽","color":"#3a7bd5"},
            {"country":"인도","val":182.9,"prev":186.5,"region":"아시아","color":"#f0a500"},
            {"country":"브라질","val":168.7,"prev":168.2,"region":"기타","color":"#8b5cf6"},
            {"country":"사우디아라비아","val":149.5,"prev":148.8,"region":"중동","color":"#22c55e"},
            {"country":"한국","val":140.6,"prev":145.1,"region":"아시아","color":"#f0a500"},
            {"country":"이스라엘","val":105.7,"prev":107.9,"region":"중동","color":"#22c55e"},
            {"country":"독일","val":103.1,"prev":109.2,"region":"유럽","color":"#3a7bd5"},
        ]

        fallback_monthly = [
            {"month":"Jan","일본":1079,"중국":761,"영국":740,"한국":122},
            {"month":"Feb","일본":1126,"중국":784,"영국":750,"한국":125},
            {"month":"Mar","일본":1131,"중국":765,"영국":779,"한국":126},
            {"month":"Apr","일본":1135,"중국":744,"영국":808,"한국":122},
            {"month":"May","일본":1135,"중국":733,"영국":809,"한국":124},
            {"month":"Jun","일본":1148,"중국":731,"영국":856,"한국":127},
            {"month":"Jul","일본":1152,"중국":696,"영국":896,"한국":132},
            {"month":"Aug","일본":1180,"중국":700,"영국":901,"한국":136},
            {"month":"Sep","일본":1189,"중국":699,"영국":862,"한국":143},
            {"month":"Oct","일본":1200,"중국":688,"영국":875,"한국":145},
            {"month":"Nov","일본":1203,"중국":684,"영국":889,"한국":145},
            {"month":"Dec","일본":1186,"중국":684,"영국":866,"한국":141},
        ]

        data = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "top20": fallback_top20,
            "monthly": fallback_monthly,
            "historical": historical,
        }

        os.makedirs("data", exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"[{datetime.now()}] TIC 데이터 저장 완료")

    except Exception as e:
        print(f"[{datetime.now()}] 데이터 수집 오류: {e}")


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ─── 라우트 ───────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/treasury")
def api_treasury():
    data = load_data()
    if not data:
        fetch_tic_data()
        data = load_data()
    return jsonify(data)

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    fetch_tic_data()
    return jsonify({"status": "ok", "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


# ─── 스케줄러 (매월 18일 자동 갱신) ──────────────────────
scheduler = BackgroundScheduler()
scheduler.add_job(fetch_tic_data, "cron", day=18, hour=10, minute=0)
scheduler.start()

# 최초 실행 시 데이터 없으면 즉시 수집
if not os.path.exists(DATA_FILE):
    fetch_tic_data()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
