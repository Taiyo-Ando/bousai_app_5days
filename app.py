from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import os
import urllib.request
import urllib.parse
import math
import secrets
import re
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# 気象庁の市町村コード（青森市）
AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)
FORECAST_URL = (
    f"https://www.jma.go.jp/bosai/forecast/data/forecast/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')
OPINIONS_FILE = os.path.join(APP_DIR, 'data', 'opinion_history.json')
UPLOAD_DIR = os.path.join(APP_DIR, 'static', 'uploads')
ALLOWED_FACILITIES = {
    'wheelchair', 'multipurpose_toilet', 'visual_hearing_support', 'pets_allowed'
}
FACILITY_LABELS = {
    'wheelchair': '車いす対応',
    'multipurpose_toilet': '多目的トイレ',
    'visual_hearing_support': '視覚・聴覚障害者対応設備',
    'pets_allowed': 'ペット可能'
}
ALLOWED_REGIONS = {'A地区', 'B地区', 'C地区'}
ALLOWED_CONGESTION = {'○', '△', '✕'}

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])
opinions = load_json(OPINIONS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except OSError:
        return False
    return True


def save_shelters():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(shelters, f, ensure_ascii=False, indent=2)


def save_opinions():
    with open(OPINIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(opinions, f, ensure_ascii=False, indent=2)


def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def valid_csrf():
    return bool(request.form.get('csrf_token')) and secrets.compare_digest(
        request.form.get('csrf_token'), session.get('csrf_token', '')
    )


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': csrf_token}
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]


def geocode_address(address):
    """住所を緯度・経度に変換する（見つからなければ None）。"""
    query = urllib.parse.urlencode({
        'q': address,
        'format': 'jsonv2',
        'limit': 1,
        'countrycodes': 'jp'
    })
    request = urllib.request.Request(
        f'https://nominatim.openstreetmap.org/search?{query}',
        headers={'User-Agent': 'bousai-app/1.0'}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            locations = json.loads(response.read())
        if not locations:
            return None
        return float(locations[0]['lat']), float(locations[0]['lon'])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def distance_km(latitude, longitude, target_latitude, target_longitude):
    """2地点間の距離をキロメートルで返す。"""
    earth_radius_km = 6371
    latitude_1, latitude_2 = math.radians(latitude), math.radians(target_latitude)
    delta_latitude = math.radians(target_latitude - latitude)
    delta_longitude = math.radians(target_longitude - longitude)
    value = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_1) * math.cos(latitude_2)
        * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * earth_radius_km * math.asin(math.sqrt(value))


def shelters_by_distance(address=None, location=None):
    """住所または現在地の座標を基準に、避難所を近い順に返す。"""
    if location is None:
        location = geocode_address(address)
    if location is None:
        return None

    latitude, longitude = location
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None

    results = []
    for shelter in shelters:
        shelter_with_distance = dict(shelter)
        try:
            shelter_with_distance['distance_km'] = round(
                distance_km(
                    latitude,
                    longitude,
                    float(shelter['latitude']),
                    float(shelter['longitude'])
                ),
                1
            )
        except (KeyError, TypeError, ValueError):
            # 位置情報のない既存施設も一覧には残し、距離不明として最後に表示する。
            shelter_with_distance['distance_km'] = None
        results.append(shelter_with_distance)
    return sorted(
        results,
        key=lambda shelter: (
            shelter['distance_km'] is None,
            shelter['distance_km'] if shelter['distance_km'] is not None else 0
        )
    )


SEARCH_FEATURES = {
    'wheelchair': '車いす対応',
    'multipurpose_toilet': '多目的トイレ',
    'sensory_support': '視覚・聴覚障害者向け施設',
    'pets_allowed': 'ペット可'
}


def shelters_by_suitability(address, requested_features, location=None):
    """条件を満たす避難所を優先し、同点なら距離順に返す。"""
    results = shelters_by_distance(address, location)
    if results is None:
        return None

    requested_features = [
        feature for feature in requested_features
        if feature in SEARCH_FEATURES
    ]
    for shelter in results:
        shelter['matched_features'] = sum(
            bool(shelter.get(feature)) for feature in requested_features
        )
        shelter['requested_features_count'] = len(requested_features)
    return sorted(
        results,
        key=lambda shelter: (
            -shelter['matched_features'],
            shelter['distance_km'] is None,
            shelter['distance_km'] if shelter['distance_km'] is not None else 0
        )
    )


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        result = {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

        try:
            result["forecast"] = get_weather_forecast()
        except Exception:
            result["forecast"] = {"periods": [], "error": True}

        return result

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


def get_weather_forecast():
    """気象庁の予報から、青森市周辺の今日の天気と気温を取得する"""
    with urllib.request.urlopen(url=FORECAST_URL, timeout=10) as res:
        forecast_data = json.loads(res.read())

    today = datetime.now(JST).date().isoformat()
    weather_series = forecast_data[0]["timeSeries"][0]
    weather_area = next(
        area for area in weather_series["areas"]
        if area.get("area", {}).get("code") == "020010"
    )
    periods = []
    for timestamp, weather, code in zip(
        weather_series["timeDefines"],
        weather_area.get("weathers", []),
        weather_area.get("weatherCodes", [])
    ):
        if timestamp[:10] == today:
            periods.append({
                "time": timestamp[11:13] + "時",
                "weather": weather.replace("　", " "),
                "code": code
            })

    temperature_series = forecast_data[0]["timeSeries"][2]
    temperature_area = next(
        area for area in temperature_series["areas"]
        if area.get("area", {}).get("code") == "31312"
    )
    temperatures = [
        int(value) for value in temperature_area.get("temps", [])
        if value not in (None, "")
    ]

    precipitation_series = next((series for series in forecast_data[0].get("timeSeries", [])
                                 if series.get("areas") and "pops" in series["areas"][0]), {})
    precipitation_area = precipitation_series.get("areas", [{}])[0]
    wind_series = next((series for series in forecast_data[0].get("timeSeries", [])
                        if series.get("areas") and "winds" in series["areas"][0]), {})
    wind_area = wind_series.get("areas", [{}])[0]
    return {
        "periods": periods,
        "max_temperature": max(temperatures) if temperatures else None,
        "min_temperature": min(temperatures) if temperatures else None,
        "precipitation": precipitation_area.get("pops", []),
        "wind": wind_area.get("winds", ["不明"])[0] if wind_area.get("winds") else "不明"
    }


def get_disaster_information():
    """青森市で現在発表・継続中の気象警報・注意報だけを抽出する。"""
    weather = get_weather_warnings()
    if weather.get('error'):
        return None
    return [{
        'type': '気象',
        'datetime': weather.get('report_time', '不明'),
        'region': weather.get('area_name', AREA_NAME),
        'content': warning.get('name', '気象警報・注意報'),
        'status': warning.get('status', '不明'),
        'url': f'https://www.jma.go.jp/bosai/warning/#area_type=class20s&area_code={AREA_CODE}'
    } for warning in weather.get('warnings', []) if isinstance(warning, dict)]


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = [i for i in instructions if i.get('target') == '住民']
    map_shelters = [
        shelter for shelter in shelters
        if shelter.get('latitude') is not None and shelter.get('longitude') is not None
    ]
    return render_template(
        'index.html',
        resident_notices=resident_notices,
        map_shelters=map_shelters,
        shelters=shelters
    )


@app.route('/opinions', methods=['GET', 'POST'])
def opinions_box():
    message = None
    error = None
    opinion_value = ''
    if request.method == 'POST':
        opinion_value = request.form.get('opinion', '').strip()
        if not valid_csrf():
            error = '不正なリクエストです。ページを再読み込みしてください。'
        elif not opinion_value:
            error = '意見を入力してください。'
        elif len(opinion_value) > 2000:
            error = '意見は2000文字以内で入力してください。'
        else:
            opinion = {
                'id': max((item.get('id', 0) for item in opinions), default=0) + 1,
                'opinion': opinion_value,
                'created_at': get_japan_time()
            }
            opinions.insert(0, opinion)
            try:
                save_opinions()
            except OSError:
                opinions.pop(0)
                error = '意見を送信できませんでした。'
            else:
                return redirect(url_for('opinions_box', sent='1'))
    elif request.args.get('sent') == '1':
        message = 'ご意見を送信しました。ありがとうございます。'

    sorted_opinions = sorted(
        opinions,
        key=lambda item: item.get('created_at', ''),
        reverse=True
    )
    return render_template(
        'opinions.html',
        opinions=sorted_opinions,
        message=message,
        error=error,
        opinion_value=opinion_value
    )

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        if not valid_csrf():
            return render_template('shelter_register.html', shelters=shelters, error=True, message='不正なリクエストです。ページを再読み込みしてください。')
        name = request.form.get('name', '').strip()
        postal_code = re.sub(r'\D', '', request.form.get('postal_code', ''))
        address = request.form.get('address', '').strip()
        capacity = request.form.get('capacity', '').strip()
        if not name or len(postal_code) != 7 or not address or not re.fullmatch(r'[0-9]+', capacity):
            return render_template('shelter_register.html', shelters=shelters, error=True, message='避難所名、7桁の郵便番号、住所、収容人数を正しく入力してください。')

        location = geocode_address(address)
        facilities = [value for value in request.form.getlist('facilities') if value in ALLOWED_FACILITIES]
        image = request.files.get('building_image')
        building_image = request.form.get('building_image_url', '').strip()
        saved_upload = None
        if image and image.filename:
            allowed_extensions = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
            allowed_mime_types = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
            extension = image.filename.rsplit('.', 1)[-1].lower() if '.' in image.filename else ''
            if extension not in allowed_extensions or image.mimetype not in allowed_mime_types:
                return render_template('shelter_register.html', shelters=shelters, error=True, message='画像ファイルの形式が正しくありません。')
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            saved_upload = f"{secrets.token_hex(16)}.{extension}"
            image.save(os.path.join(UPLOAD_DIR, secure_filename(saved_upload)))
            building_image = url_for('static', filename=f'uploads/{saved_upload}')

        next_id = max((shelter.get('id', 0) for shelter in shelters), default=0) + 1
        shelters.append({
            'id': next_id,
            'name': name,
            'postal_code': postal_code,
            'address': address,
            'capacity': capacity,
            'building_image': building_image,
            'facilities': facilities,
            'latitude': location[0] if location else None,
            'longitude': location[1] if location else None
        })
        try:
            save_shelters()
        except OSError:
            shelters.pop()
            if saved_upload:
                try:
                    os.remove(os.path.join(UPLOAD_DIR, saved_upload))
                except OSError:
                    pass
            return render_template('shelter_register.html', shelters=shelters, error=True, message='避難所の登録に失敗しました。')

        return render_template('shelter_register.html', shelters=shelters, success=True, message='登録が完了しました')

    return render_template('shelter_register.html', shelters=shelters)

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    map_shelters = [
        shelter for shelter in shelters
        if shelter.get('latitude') is not None and shelter.get('longitude') is not None
    ]
    return render_template('shelter_search.html', map_shelters=map_shelters)

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=shelters)


# 避難所の地図・外観ページ
@app.route('/shelter/<int:shelter_id>')
def shelter_detail(shelter_id):
    shelter = next((item for item in shelters if item.get('id') == shelter_id), None)
    if shelter is None:
        return '避難所が見つかりませんでした', 404
    has_coordinates = all(
        shelter.get(key) is not None for key in ('latitude', 'longitude')
    )
    return render_template(
        'shelter_detail.html',
        shelter=shelter,
        has_coordinates=has_coordinates
    )


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/shelter_status', methods=['POST'])
@login_required
def shelter_status():
    if not valid_csrf():
        return render_template('shelter_register.html', shelters=shelters, error=True, message='不正なリクエストです。ページを再読み込みしてください。')
    name = request.form.get('status_name', '').strip()
    congestion = request.form.get('congestion', '')
    remaining = request.form.get('remaining_capacity', '').strip()
    if not any(shelter.get('name') == name for shelter in shelters):
        return render_template('shelter_register.html', shelters=shelters, error=True, message='該当する避難所が見つかりません。')
    if congestion not in ALLOWED_CONGESTION or not re.fullmatch(r'[0-9]+', remaining):
        return render_template('shelter_register.html', shelters=shelters, error=True, message='混雑状況と残収容数を正しく入力してください。')
    shelter = next(shelter for shelter in shelters if shelter.get('name') == name)
    previous = {key: shelter.get(key) for key in ('congestion', 'remaining_capacity', 'status_note')}
    shelter.update({'congestion': congestion, 'remaining_capacity': remaining,
                    'status_note': request.form.get('status_note', '').strip()})
    try:
        save_shelters()
    except OSError:
        for key, value in previous.items():
            if value is None:
                shelter.pop(key, None)
            else:
                shelter[key] = value
        return render_template('shelter_register.html', shelters=shelters, error=True, message='避難所状況の登録に失敗しました。')
    return render_template('shelter_register.html', shelters=shelters, success=True, message='登録が完了しました')


@app.route('/board', methods=['GET', 'POST'])
@login_required
def board():
    if request.method == 'POST':
        if not valid_csrf():
            return render_template('board.html', instructions=instructions, shelters=shelters, error='不正なリクエストです。')
        action = request.form.get('action')
        if action == 'send':
            regions = [region for region in request.form.getlist('regions') if region in ALLOWED_REGIONS]
            content = request.form.get('content', '').strip()
            if not regions:
                return render_template('board.html', instructions=instructions, shelters=shelters, error='送信先の地域を選択してください。')
            if not content:
                return render_template('board.html', instructions=instructions, shelters=shelters, error='発信内容を入力してください。')
            now = get_japan_time()
            instruction = {'id': max((item.get('id', 0) for item in instructions), default=0) + 1,
                           'target': '住民', 'content': content, 'regions': regions,
                           'status': '発表', 'created_at': now, 'updated_at': now}
            instructions.append(instruction)
            if not save_instructions():
                instructions.pop()
                return render_template('board.html', instructions=instructions, shelters=shelters, error='指示を送信できませんでした。')
            return redirect(url_for('board'))
        if action == 'release':
            try:
                instruction_id = int(request.form.get('instruction_id', ''))
            except ValueError:
                instruction_id = None
            instruction = next((item for item in instructions if item.get('id') == instruction_id), None)
            if instruction and instruction.get('status') != '解除':
                previous = instruction.copy()
                instruction['status'] = '解除'
                instruction['updated_at'] = get_japan_time()
                if not save_instructions():
                    instruction.clear()
                    instruction.update(previous)
                    return render_template('board.html', instructions=instructions, shelters=shelters, error='指示を解除できませんでした。')
            return redirect(url_for('board'))
    resident_instructions = sorted(
        (i for i in instructions if i.get('target') == '住民'),
        key=lambda item: (item.get('created_at', ''), item.get('id', 0)),
        reverse=True
    )
    return render_template('board.html', instructions=resident_instructions, shelters=shelters)

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    address = request.args.get('address', '').strip()
    requested_features = request.args.getlist('features')
    location = None
    latitude_value = request.args.get('latitude', '').strip()
    longitude_value = request.args.get('longitude', '').strip()
    coordinate_error = None
    if latitude_value or longitude_value:
        try:
            latitude = float(latitude_value)
            longitude = float(longitude_value)
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError
            location = (latitude, longitude)
        except (TypeError, ValueError):
            coordinate_error = '現在地の座標が正しくありません。住所を入力して検索してください。'

    if coordinate_error:
        return render_template(
            'search_results.html',
            results=[],
            address=address,
            location=None,
            requested_features=requested_features,
            feature_labels=SEARCH_FEATURES,
            error=coordinate_error
        )

    if not address and location is None:
        return redirect(url_for('shelter_search'))

    results = shelters_by_suitability(address, requested_features, location)
    if results is None:
        return render_template(
            'search_results.html',
            results=[],
            address=address,
            location=location,
            requested_features=requested_features,
            error='入力された住所を確認できませんでした。'
        )
    error = None
    if not results and shelters:
        error = '避難所の位置情報が登録されていません。管理者に登録内容の更新を依頼してください。'
    return render_template(
        'search_results.html',
        results=results,
        address=address,
        location=location,
        requested_features=requested_features,
        feature_labels=SEARCH_FEATURES,
        error=error
    )

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())


@app.route('/api/disasters')
def api_disasters():
    disasters = get_disaster_information()
    if disasters is None:
        return jsonify({'error': '災害情報を取得できませんでした'}), 502
    return jsonify({'disasters': disasters})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
