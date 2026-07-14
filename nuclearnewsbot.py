import os
import feedparser
import urllib.parse
import time, calendar, json
import requests
from google import genai
from google.genai import types

# ===== 설정 (금고에서 꺼내오기) =====
GEMINI_KEY = os.environ["GEMINI_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

keywords_en = ["nuclear power", "small modular reactor", "uranium", "U.S. nuclear", "U.S. NRC", "Nuclear Supply Chain", "Nuclear Financing", "Westinghouse", "AP1000", "Nuclear Construction", "Large PWR", "PWR Reactor"]
HOURS = 12   # 1시간=1, 하루=24, 일주일=168

# ===== 1) 뉴스 가져오기 (+ 최근 뉴스만 거르기) =====
feeds = []
for kw in keywords_en:
    feeds.append(f"https://news.google.com/rss/search?q={urllib.parse.quote(kw)}&hl=en-US&gl=US&ceid=US:en")

now = time.time()
cutoff = HOURS * 3600

articles = []
seen_links = set()
for url in feeds:
    feed = feedparser.parse(url)
    for entry in feed.entries:
        if not getattr(entry, "published_parsed", None):
            continue
        age = now - calendar.timegm(entry.published_parsed)
        if age > cutoff:
            continue
        if entry.link not in seen_links:
            seen_links.add(entry.link)
            articles.append({"title": entry.title, "link": entry.link, "published": entry.published})

print(f"최근 {HOURS}시간 이내 기사 {len(articles)}개를 찾았어요.")

# ===== 텔레그램 전송 함수 =====
def send_to_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True})

if not articles:
    send_to_telegram(f"최근 {HOURS}시간 이내 원전·에너지 뉴스가 없습니다.")
    print("뉴스 없음 — 알림 전송 후 종료.")
    raise SystemExit

# ===== 2) Gemini에게 정렬·요약 =====
client = genai.Client(api_key=GEMINI_KEY)

articles = articles[:60]   # 최신 60개만 AI에 전달

news_text = ""
for i, a in enumerate(articles, start=1):
    news_text += f"{i}. {a['title']}\n"

prompt = f"""당신은 원자력·에너지 섹터를 담당하는 투자 애널리스트입니다.
대규칙 1: 아래 뉴스 제목 목록에서 투자 판단에 중요한 순서대로 상위 10개를 골라 정렬하세요.

중요도 기준 (프롬프트 결과에서에 우선도 몇에 해당하는지를 굳이 언급할 필요는 없음):
[우선도 1] 미국의 대형원전 신규 설치/사업 진행 (자금조달, 인허가, 부지확보)
[우선도 2] 한국 EPC 기업의 미국/해외 대형원전 EPC 참여
[우선도 3] 유럽 내 대형원전/SMR 사업 진행 관련
[우선도 4] 미국 내 SMR 기술선들 (Holtec, X-Energy, Nuscale Power, Oklo 등)의 사업 진행 관련 소식

대규칙 2: 내용이 비슷한 뉴스가 여럿 있는 경우는 하나만 요약하기

결과를 아래 형식의 JSON 배열로만 출력하세요. 다른 말은 절대 넣지 마세요.
[
  {{"index": 뉴스목록_번호, "summary": "한 줄 요약", "point": "투자 포인트 한 줄"}}
]

뉴스 목록:
{news_text}
"""

def ask_gemini(user_prompt):
    last_error = None
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            return resp.text
        except Exception as e:
            last_error = e
            time.sleep(120)   # 5초 쉬었다 다시 시도
    raise last_error

def clean_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return text.strip()

raw = ask_gemini(prompt)
try:
    results = json.loads(clean_json(raw))
except json.JSONDecodeError:
    fix_prompt = "아래 내용을 올바른 JSON 배열 하나로만 다시 출력하세요. 설명·군더더기 없이 JSON만:\n\n" + raw
    raw2 = ask_gemini(fix_prompt)
    results = json.loads(clean_json(raw2))

# ===== 3) 결과를 텔레그램 메시지로 조립해서 전송 =====
message = f"📰 원전·에너지 투자 뉴스 (최근 {HOURS}시간)\n\n"
for rank, r in enumerate(results, start=1):
    idx = r["index"] - 1
    if idx < 0 or idx >= len(articles):
        continue
    a = articles[idx]
    message += f"[{rank}위] {a['title']}\n"
    message += f"· 요약: {r['summary']}\n"
    message += f"· 투자 포인트: {r['point']}\n"
    message += f"· 링크: {a['link']}\n\n"

send_to_telegram(message)
print("텔레그램으로 전송 완료!")
