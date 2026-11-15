# -*- coding: utf-8 -*-
"""
대동 홍보팀 뉴스 클리핑 봇
- 네이버 뉴스 검색 API로 기사 수집 → 카테고리별 정리(중복 제거) → 텔레그램 발송

모드
  python news_bot.py daily   : 정기 발송 (월~금 17:00, 일 14:00 / 직전 발송 시각 ~ 이번 발송 시각 기사)
  python news_bot.py poll    : 텔레그램 확인 → '뉴스'면 (전날 17:00 ~ 현재 기사) 답장,
                              '1-1, 2-3*' 같은 번호면 마지막으로 보낸 목록에서 골라 보고용 양식으로 정리해 답장
  python news_bot.py daily --dry-run : 텔레그램 발송 없이 화면에만 출력
"""
import os
import re
import sys
import json
import html
import difflib
import time
import subprocess
import shutil
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

# =====================================================================
# ★★★ API 키 넣는 곳 ★★★
# 키를 이 파일에 직접 적지 마세요!
# GitHub 저장소 > Settings > Secrets and variables > Actions 에
# 아래 4개 이름 그대로 등록하면 자동으로 읽어옵니다.
# =====================================================================
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "").strip()          # NAVER API HUB Client ID
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip()  # NAVER API HUB Client Secret
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()    # 텔레그램 봇 토큰
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()        # 받을 사람 chat ID
# 번호 선택 시 AI 요약용 (둘 중 하나만 있으면 됨, 구독 토큰 우선)
CLAUDE_CODE_OAUTH_TOKEN = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()  # Claude 구독 토큰 (claude setup-token)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()              # Claude API 키 (유료, 선택)

# =====================================================================
# 검색어 설정 (여기만 고치면 검색어를 바꿀 수 있어요)
# max = 카테고리당 최대 기사 수
# =====================================================================
CATEGORIES = [
    {
        "name": "자사뉴스",
        "keywords": ["대동", "대동기어", "대동모빌리티", "대동금속", "하이드로텍",
                     "제주대동", "대동애그테크", "대동에이아이랩", "대동로보틱스"],
        "max": 10,
    },
    {
        "name": "경쟁사",
        "keywords": ["TYM", "LS엠트론", "존디어", "구보다", "얀마", "HD건설기계", "두산밥캣"],
        "max": 10,
    },
    {
        "name": "시장 동향",
        "keywords": ["농기계", "트랙터", "건설기계"],
        "max": 10,
        "max_general": 3,
    },
    {
        "name": "스마트농업&기후위기",
        "keywords": ["스마트농업", "스마트팜", "스마트온실", "기후위기"],
        "max": 10,
        "max_general": 3,
    },
    {
        "name": "농업정책",
        "keywords": ["농식품부", "농진청"],
        "max": 10,
        "max_general": 3,
    },
    {
        "name": "로봇&모빌리티",
        "keywords": ["로봇", "농업로봇", "전기스쿠터", "전기이륜차"],
        "max": 10,
        "max_general": 3,
    },
    {
        "name": "AI",
        "keywords": ["AI", "피지컬AI", "생성형 AI", "인공지능"],
        "max": 10,
        "max_general": 3,
    },
]

# max_general = 대동그룹 사업과 직접 관련 없는 일반 기사는 최대 몇 건까지 보여줄지
#   (관련 기사를 먼저 채우고, 남는 자리에 일반 기사를 이 개수까지만 넣음 / 없으면 제한 없음)

# 대동그룹이 하고 있는 일과 관련된 단어 (제목·요약에 있으면 '관련 기사'로 우선 노출)
BUSINESS_WORDS = [
    "대동", "카이오티", "KIOTI", "농업", "농기계", "농촌", "농가", "농민", "영농", "농작업",
    "트랙터", "콤바인", "이앙기", "스마트팜", "스마트농업", "정밀농업", "디지털농업", "애그테크",
    "작물", "재배", "수확", "과수", "방제", "드론", "자율주행", "무인", "로봇", "피지컬",
    "휴머노이드", "모빌리티", "전기스쿠터", "이륜차", "건설기계", "농식품", "식량", "탄소중립",
]
# 이 검색어로 찾은 기사는 그 자체로 대동 사업과 관련 있다고 봄
CORE_KEYWORDS = {"피지컬AI", "농업로봇", "전기스쿠터", "전기이륜차", "스마트농업", "스마트팜",
                 "스마트온실", "농기계", "트랙터", "농식품부", "농진청"}

# 정기 발송 시각 (요일: 0=월 … 6=일 → 시각). 토요일은 발송 없음.
# 수집 기간 = 직전 발송 시각 ~ 이번 발송 시각
#   월: 일 14:00 ~ 월 17:00 / 화~금: 전날 17:00 ~ 당일 17:00 / 일: 금 17:00 ~ 일 14:00
SEND_SCHEDULE = {0: 17, 1: 17, 2: 17, 3: 17, 4: 17, 6: 14}

# '뉴스' 요청 시: 전날 이 시각부터 현재까지
CUTOFF_HOUR = 17

# 텔레그램에서 이 단어를 보내면 뉴스를 보내 줍니다
COMMAND_WORDS = {"뉴스", "/news"}

# 한 번 실행될 때 텔레그램을 계속 지켜보는 시간(분). 이 시간 동안 보낸 메시지는 몇 초 안에 처리
# (5분마다 새 실행이 대기하므로 끝나면 바로 이어서 지켜봄)
LISTEN_MINUTES = 25

# ---- 보고용 정리 양식 (번호를 보내면 이 양식으로 정리) ----
REPORT_TITLE = "[{month}월 {day}일 대동 Daily News]"
REPORT_GREETING = "안녕하세요? 부회장님."
REPORT_INTRO = "{day}일 주요 뉴스 전달드리며 금일은 다음의 뉴스가 눈에 띕니다."
REPORT_INTRO_PLAIN = "{day}일 주요 뉴스 전달드립니다."   # 별표(*) 기사가 없을 때
# 보고서 날짜: 이 시각(시) 이후에 만든 정리본은 '다음 날' 날짜로 씀 (저녁에 정리 → 다음 날 아침 보고)
#   12 = 낮 12시 이후면 다음 날 / 0 = 항상 다음 날 / 24 = 항상 당일
REPORT_NEXT_DAY_FROM_HOUR = 12
# 카테고리 → 보고서 섹션 이름을 다르게 쓰고 싶을 때만 적기 (비어 있으면 카테고리 이름 그대로)
#   예) "경쟁사": "경쟁사 및 시장 동향", "시장 동향": "경쟁사 및 시장 동향"  → 두 섹션을 하나로 합침
REPORT_SECTIONS = {}

# AI 요약 설정 (지시문은 clipping_prompt.txt 파일에서 읽음 → 이 파일만 고치면 요약 스타일이 바뀜)
CLAUDE_MODEL = "claude-sonnet-5-5"   # (API 키 방식일 때)
CLAUDE_CODE_MODEL = "sonnet"         # (구독 토큰 방식일 때) sonnet = 품질·속도 균형 / haiku = 더 빠름
SUMMARY_WORKERS = 5                  # 동시에 요약할 기사 수
PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clipping_prompt.txt")
# 별표(*) 없이 번호만 보냈을 때 '눈에 띄는 뉴스'로 올릴 개수 (0 = 별표 붙인 기사만 올림)
DEFAULT_HIGHLIGHTS = 0
# 마지막으로 보낸 뉴스 목록 저장 파일 (번호 → 기사 찾기용, GitHub이 자동 저장)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LAST_NEWS_FILE = os.path.join(BASE_DIR, "last_news.json")
# 마지막으로 보낸 보고용 정리본 (○ 줄 수정·피드백 반영 후 다시 보내기용)
LAST_REPORT_FILE = os.path.join(BASE_DIR, "last_report.json")
# 텔레그램으로 받은 요약 피드백 (다음 요약부터 바로 반영)
FEEDBACK_FILE = os.path.join(BASE_DIR, "style_feedback.txt")

# 제목에 검색어가 있는 기사만 남기기 (True 권장: 'AI', '로봇'처럼 넓은 검색어의 잡음 제거)
TITLE_MUST_CONTAIN_KEYWORD = True

# 앞 글자가 한글/영문이면 다른 단어로 보고 제외하는 검색어
#  예) '구보다' → '연구보다', '요구보다' 제외 / '얀마' → '미얀마' 제외 / '대동' → '현대동…' 제외
STRICT_KEYWORDS = {"대동", "구보다", "얀마", "TYM", "AI"}

# 검색어별 제외어 (제목에 이 단어가 있으면 버림)
EXCLUDE_WORDS = {
    "대동": ["대동맥", "대동강", "대동여지도", "대동단결", "대동제", "대동병원",
             "대동소이", "대동세상", "대동계", "대동법", "대동문", "대동초", "대동중",
             "대동고", "대동역", "대동동", "대동하", "대동한", "대동했", "대동해",
             "대동할", "대동된", "대동돼", "대동시켜", "대동천", "대동면", "대동리"],
}

# '대동'처럼 뜻이 여러 개인 검색어는 제목·요약에 아래 단어 중 하나가 있어야 대동그룹 기사로 인정
CONTEXT_REQUIRED = {
    "대동": ["대동그룹", "대동 그룹", "대동공업", "농기계", "트랙터", "콤바인", "이앙기", "농업",
             "농촌", "농가", "스마트팜", "스마트농업", "정밀농업", "애그테크", "농식품",
             "카이오티", "KIOTI", "Kioti", "원유현", "김준식", "김형철", "대동커넥트", "커넥트",
             "드론", "로봇", "모빌리티", "전기스쿠터", "이륜차", "기어", "금속", "하이드로텍"],
}

# 같은 기사로 볼 제목 유사도 (0~1, 낮을수록 더 많이 합침)
SIMILARITY_THRESHOLD = 0.6     # 문장 순서까지 비슷한 정도
OVERLAP_THRESHOLD = 0.45       # 들어간 글자 조각이 겹치는 정도 (같은 보도자료를 다르게 쓴 제목)

# 기사 요약(네이버 description)도 함께 보낼지
SHOW_DESCRIPTION = False

# 주요 매체 도메인 → 매체명 (없으면 도메인으로 표시)
MEDIA_NAMES = {
    "yna.co.kr": "연합뉴스", "newsis.com": "뉴시스", "news1.kr": "뉴스1",
    "chosun.com": "조선일보", "joongang.co.kr": "중앙일보", "donga.com": "동아일보",
    "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "hankookilbo.com": "한국일보",
    "kmib.co.kr": "국민일보", "seoul.co.kr": "서울신문", "segye.com": "세계일보",
    "munhwa.com": "문화일보", "mk.co.kr": "매일경제", "hankyung.com": "한국경제",
    "sedaily.com": "서울경제", "mt.co.kr": "머니투데이", "edaily.co.kr": "이데일리",
    "asiae.co.kr": "아시아경제", "fnnews.com": "파이낸셜뉴스", "heraldcorp.com": "헤럴드경제",
    "etnews.com": "전자신문", "zdnet.co.kr": "지디넷코리아", "ddaily.co.kr": "디지털데일리",
    "dt.co.kr": "디지털타임스", "inews24.com": "아이뉴스24", "bloter.net": "블로터",
    "aitimes.com": "AI타임스", "nongmin.com": "농민신문", "agrinet.co.kr": "한국농어민신문",
    "aflnews.co.kr": "농수축산신문", "farminsight.net": "팜인사이트", "ikpnews.net": "한국농정신문",
    "newspim.com": "뉴스핌", "ajunews.com": "아주경제", "businesspost.co.kr": "비즈니스포스트",
    "thebell.co.kr": "더벨", "biz.chosun.com": "조선비즈", "dailian.co.kr": "데일리안",
    "nocutnews.co.kr": "노컷뉴스", "ytn.co.kr": "YTN", "kbs.co.kr": "KBS", "imbc.com": "MBC",
    "sbs.co.kr": "SBS", "jtbc.co.kr": "JTBC", "mbn.co.kr": "MBN", "yonhapnewstv.co.kr": "연합뉴스TV",
    "imaeil.com": "매일신문", "idaegu.com": "대구신문", "yeongnam.com": "영남일보",
    "kyongbuk.co.kr": "경북일보", "busan.com": "부산일보", "kookje.co.kr": "국제신문",
    "jejunews.com": "제주일보", "ihalla.com": "한라일보", "jemin.com": "제민일보",
    "energy-news.co.kr": "에너지신문", "hellot.net": "헬로티", "irobotnews.com": "로봇신문",
    "motorgraph.com": "모터그래프", "ebn.co.kr": "EBN", "smarttoday.co.kr": "스마트투데이",
    "worktoday.co.kr": "워크투데이", "gukjenews.com": "국제뉴스",
}

KST = timezone(timedelta(hours=9))
# NAVER API HUB (네이버 클라우드 플랫폼) 뉴스 검색 주소
NAVER_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
MAX_PAGES = 5         # 검색어당 최대 500건까지 확인 (평소)
MAX_PAGES_RANGE = 10  # 날짜 지정 검색 때 (네이버 API는 검색어당 최신 약 1,000건까지만 제공)
MAX_RANGE_DAYS = 31   # 날짜 지정 검색 최대 기간
SEARCH_DAYS = 7       # '…찾아줘' 검색에서 날짜를 말하지 않으면 최근 며칠 기사에서 찾을지
SEARCH_DEFAULT_COUNT = 5
SEARCH_MAX_COUNT = 20
DAYS = "월화수목금토일"


# ---------------------------------------------------------------------
# 수집 기간
# ---------------------------------------------------------------------
def at_cutoff(d):
    return datetime(d.year, d.month, d.day, CUTOFF_HOUR, 0, tzinfo=KST)


def send_slots(now, days=10):
    """최근 발송 시각 목록 (최신순)"""
    slots = []
    for back in range(days):
        d = now - timedelta(days=back)
        hour = SEND_SCHEDULE.get(d.weekday())
        if hour is not None:
            t = datetime(d.year, d.month, d.day, hour, 0, tzinfo=KST)
            if t <= now:
                slots.append(t)
    return slots


def daily_window(now):
    """정기 발송: 직전 발송 시각 ~ 이번 발송 시각 (GitHub 실행이 늦어져도 구간은 고정)"""
    slots = send_slots(now)
    return slots[1], slots[0]


def command_window(now):
    """'뉴스' 요청: 전날 17:00 ~ 현재"""
    return at_cutoff(now - timedelta(days=1)), now


# ---------------------------------------------------------------------
# 네이버 수집
# ---------------------------------------------------------------------
class NaverError(Exception):
    pass


def naver_search(query, start=1):
    params = urllib.parse.urlencode({"query": query, "display": 100, "start": start, "sort": "date", "format": "json"})
    req = urllib.request.Request(f"{NAVER_URL}?{params}")
    req.add_header("X-NCP-APIGW-API-KEY-ID", NAVER_CLIENT_ID)
    req.add_header("X-NCP-APIGW-API-KEY", NAVER_CLIENT_SECRET)
    for attempt in range(3):
        try:
            time.sleep(0.1)  # 너무 빠르게 연속 호출하지 않도록
            with urllib.request.urlopen(req, timeout=15) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))  # 잠시 쉬었다 재시도
                continue
            raise_naver_error(e)


def raise_naver_error(e):
    raw = e.read().decode("utf-8", errors="ignore")
    detail = raw
    try:
        body = json.loads(raw)
        err = body.get("error", body) if isinstance(body, dict) else {}
        detail = " ".join(str(err.get(k, "")) for k in ("errorCode", "message", "errorMessage")).strip() or raw
    except Exception:
        pass
    detail = re.sub(r"\s+", " ", detail)[:150]
    raise NaverError(f"HTTP {e.code} {detail}".strip())


def clean(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()


def collect(keyword, since, until, pages=None):
    """검색어 하나로 since ~ until 사이 기사 수집 (네이버는 최신순으로 줌)"""
    items = []
    for page in range(pages or MAX_PAGES):
        data = naver_search(keyword, start=1 + page * 100)
        batch = data.get("items", [])
        reached_old = False
        for it in batch:
            try:
                pub = parsedate_to_datetime(it["pubDate"]).astimezone(KST)
            except Exception:
                continue
            if pub >= until:
                continue
            if pub < since:
                reached_old = True
                continue
            items.append({
                "title": clean(it.get("title")),
                "desc": clean(it.get("description")),
                "url": it.get("originallink") or it.get("link"),
                "naver": it.get("link"),
                "pub": pub,
            })
        if reached_old or len(batch) < 100:
            break
    return items


# ---------------------------------------------------------------------
# 필터
# ---------------------------------------------------------------------
def keyword_pattern(kw):
    # '생성형 AI' ↔ '생성형AI', '피지컬AI' ↔ '피지컬 AI' 모두 찾도록 띄어쓰기 무시
    k = re.sub(r"(?<=[가-힣])(?=[A-Za-z])|(?<=[A-Za-z])(?=[가-힣])", " ", kw)
    k = r"\s*".join(re.escape(part) for part in k.split())
    if kw not in STRICT_KEYWORDS:
        return re.compile(k)
    if re.fullmatch(r"[A-Za-z0-9]+", kw):
        return re.compile(rf"(?<![A-Za-z]){k}(?![A-Za-z])")
    return re.compile(rf"(?<![가-힣A-Za-z]){k}")


def title_matches(title, kw, desc=None):
    if any(w in title for w in EXCLUDE_WORDS.get(kw, [])):
        return False
    if desc is not None and kw in CONTEXT_REQUIRED:
        text = f"{title} {desc}"
        if not any(w in text for w in CONTEXT_REQUIRED[kw]):
            return False
    if not TITLE_MUST_CONTAIN_KEYWORD:
        return True
    return bool(keyword_pattern(kw).search(title))


def norm(title):
    title = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", title)   # [단독], (종합) 등 제거
    return re.sub(r"[^0-9A-Za-z가-힣]", "", title)


def bigrams(text):
    return {text[i:i + 2] for i in range(len(text) - 1)} or {text}


def is_duplicate(article, accepted, seen_urls):
    if article["url"] in seen_urls or article["naver"] in seen_urls:
        return True
    n = norm(article["title"])
    grams = bigrams(n)
    for a in accepted:
        if n == a["_norm"]:
            return True
        overlap = len(grams & a["_grams"]) / max(1, min(len(grams), len(a["_grams"])))
        if overlap >= OVERLAP_THRESHOLD + 0.2:   # 짧은 제목이 긴 제목에 거의 다 포함
            return True
        jaccard = len(grams & a["_grams"]) / max(1, len(grams | a["_grams"]))
        if jaccard >= OVERLAP_THRESHOLD:
            return True
        if difflib.SequenceMatcher(None, n, a["_norm"]).ratio() >= SIMILARITY_THRESHOLD:
            return True
    return False


def media_name(url):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    for domain, name in MEDIA_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or "-"


def is_relevant(article):
    if article["kw"] in CORE_KEYWORDS:
        return True
    text = f"{article['title']} {article['desc']}"
    for w in BUSINESS_WORDS:
        if w in article["kw"] or article["kw"] in w:
            continue  # 검색어 자체는 관련성 판단에서 제외 (예: '로봇' 기사에서 '로봇')
        if w in text:
            return True
    return False


def gather(since, until, pages=None):
    """전체 카테고리 수집 → (결과, 실패 검색어, 첫 오류 내용, 전체 검색어 수)"""
    accepted, seen_urls, failed, results = [], set(), [], []
    first_error = ""
    total = 0
    # 위 카테고리부터 처리 → 겹치는 기사는 앞 카테고리(자사뉴스)에 남음
    for cat in CATEGORIES:
        candidates = []
        for kw in cat["keywords"]:
            total += 1
            try:
                found = collect(kw, since, until, pages)
                matched = [a for a in found if title_matches(a["title"], kw, a["desc"])]
                candidates += matched
                print(f"{kw}: 기간 내 {len(found)}건 / 제목 일치 {len(matched)}건")
            except Exception as e:
                print(f"[실패] {kw}: {e}", file=sys.stderr)
                failed.append(kw)
                first_error = first_error or str(e)

        for a in candidates:
            # 여러 검색어에 걸리면 가장 구체적인(긴) 검색어로 표시
            matched = [k for k in cat["keywords"] if title_matches(a["title"], k)]
            a["kw"] = (max(matched, key=lambda k: (len(k.replace(" ", "")), k in CORE_KEYWORDS))
                       if matched else cat["keywords"][0])
            a["relevant"] = is_relevant(a)
        # 대동 사업 관련 기사 먼저, 그 안에서는 최신순
        candidates.sort(key=lambda a: (a["relevant"], a["pub"]), reverse=True)
        picked, general = [], 0
        max_general = cat.get("max_general")
        for a in candidates:
            if len(picked) >= cat["max"]:
                break
            if not a["relevant"] and max_general is not None and general >= max_general:
                continue
            if is_duplicate(a, accepted, seen_urls):
                continue
            general += 0 if a["relevant"] else 1
            a["_norm"] = norm(a["title"])
            a["_grams"] = bigrams(a["_norm"])
            picked.append(a)
            accepted.append(a)
            seen_urls.update({a["url"], a["naver"]})
        picked.sort(key=lambda a: (a["relevant"], a["pub"]), reverse=True)
        results.append((cat, picked))
    return results, failed, first_error, total


# ---------------------------------------------------------------------
# 메시지
# ---------------------------------------------------------------------
def fmt(dt):
    return f"{dt:%m.%d}({DAYS[dt.weekday()]}) {dt:%H:%M}"


def error_hint(err):
    if "401" in err:
        return "네이버 클라우드 NAVER API HUB의 Client ID / Client Secret 값을 GitHub Secrets에 넣었는지 확인해 주세요."
    if "403" in err:
        return "NAVER API HUB > Application에서 '검색' API가 선택되어 있는지 확인해 주세요."
    if "429" in err:
        return "네이버 API 하루 호출 한도를 넘었어요. 내일 다시 시도돼요."
    return "잠시 후 다시 시도해 주세요."


def build_message(title, since, until_label, results, failed, first_error, total):
    lines = [f"<b>{title}</b>", f"{fmt(since)} ~ {until_label}", ""]

    if failed and len(failed) == total:
        lines += [
            "⚠️ 네이버 뉴스를 하나도 가져오지 못했어요.",
            f"원인: {html.escape(first_error)}",
            f"해결: {html.escape(error_hint(first_error))}",
        ]
        return "\n".join(lines)

    for i, (cat, arts) in enumerate(results, 1):
        lines.append(f"<b>{i}. {html.escape(cat['name'])}</b> ({len(arts)}건)")
        if not arts:
            lines.append("- 관련 기사 없음")
        for j, a in enumerate(arts, 1):
            t = html.escape(a["title"])
            url = html.escape(a["url"] or "", quote=True)
            lines.append(
                f"<b>{i}-{j}</b> [{html.escape(a['kw'])}] <a href=\"{url}\">{t}</a>"
                f" - {html.escape(media_name(a['url']))} {a['pub']:%m/%d %H:%M}"
            )
            if SHOW_DESCRIPTION and a["desc"]:
                lines.append(f"   {html.escape(a['desc'][:80])}…")
        lines.append("")
    if failed:
        lines.append(f"⚠️ 수집 실패 검색어: {html.escape(', '.join(failed))}")
        lines.append(f"원인: {html.escape(first_error)}")
        lines.append("")
    lines.append("✏️ 보고용으로 정리하려면 번호를 보내 주세요. 예) 1-1*, 4-2*, 2-1, 3-3")
    lines.append("   * 붙인 기사 = 맨 위 '★ 눈에 띄는 뉴스'로 / 나머지는 카테고리별로 정리")
    return "\n".join(lines).strip()


def split_message(text, limit=3800):
    """텔레그램 1회 전송 한도(4096자)에 맞게 줄 단위로 나누기"""
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


# ---------------------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------------------
def telegram(method, params, tries=5):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    wait = int(params.get("timeout", 0) or 0) + 30   # 길게 기다리는 요청은 그만큼 여유
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=wait) as res:
                body = json.loads(res.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code in (409, 429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(3 * (attempt + 1))   # 409 = 다른 실행이 아직 지켜보는 중 → 잠시 후 재시도
                continue
            raise RuntimeError(f"텔레그램 오류 HTTP {e.code}: {e.read().decode(errors='ignore')}")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            # 일시적인 네트워크 끊김 → 잠시 쉬었다 다시 시도
            print(f"[텔레그램 재시도 {attempt + 1}/{tries}] {e}", file=sys.stderr)
            if attempt == tries - 1:
                raise
            time.sleep(3 * (attempt + 1))
    if not body.get("ok"):
        raise RuntimeError(f"텔레그램 오류: {body}")
    return body.get("result")


def send_telegram(text, dry_run=False, plain=False):
    if dry_run:
        print(text)
        return
    for chunk in split_message(text):
        params = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "disable_web_page_preview": "true"}
        if not plain:
            params["parse_mode"] = "HTML"
        telegram("sendMessage", params)


# ---------------------------------------------------------------------
# 번호 목록 저장 / 보고용 정리
# ---------------------------------------------------------------------
IN_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[git {' '.join(args[:2])}] {r.stderr.strip()[:200]}", file=sys.stderr)
    return r.returncode == 0


def push_files(*paths, message="봇 데이터 저장"):
    """저장한 파일을 바로 저장소에 올림 → 다른 실행(정기 발송/답장)과 내용을 맞춤"""
    if not IN_GITHUB:
        return
    git("config", "user.name", "news-bot")
    git("config", "user.email", "news-bot@users.noreply.github.com")
    git("add", *[os.path.basename(p) for p in paths])
    if git("commit", "-q", "-m", message):
        git("pull", "--rebase", "-X", "theirs", "-q")
        git("push", "-q")


def push_last_news():
    push_files(LAST_NEWS_FILE, message="최근 뉴스 목록 저장")


def pull_last_news():
    """다른 실행이 저장한 최신 목록 가져오기"""
    if IN_GITHUB:
        git("pull", "--rebase", "-X", "theirs", "-q")


def save_last_news(results):
    items = {}
    for i, (cat, arts) in enumerate(results, 1):
        for j, a in enumerate(arts, 1):
            items[f"{i}-{j}"] = {
                "cat": cat["name"], "title": a["title"], "desc": a["desc"],
                "url": a["url"], "naver": a["naver"], "pub": a["pub"].isoformat(),
            }
    with open(LAST_NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump({"saved": datetime.now(KST).isoformat(), "items": items}, f, ensure_ascii=False, indent=1)
    push_last_news()


SELECTION_RE = re.compile(r"^\s*\d+\s*-\s*\d+\s*\*?(\s*[,，/·\s]\s*\d+\s*-\s*\d+\s*\*?)*\s*$")


def parse_selection(text):
    """'1-1*, 2-3 3-1' → [('1-1', True), ('2-3', False), ('3-1', False)]"""
    picks = []
    for m in re.finditer(r"(\d+)\s*-\s*(\d+)\s*(\*?)", text):
        no = f"{int(m.group(1))}-{int(m.group(2))}"
        if no not in [p[0] for p in picks]:
            picks.append((no, bool(m.group(3))))
    return picks


def display_width(text):
    return sum(2 if ord(c) > 0x2E80 else 1 for c in text)


def article_link(it):
    naver = it.get("naver") or ""
    return naver if "naver.com" in naver else (it.get("url") or naver)


def summary_line(it):
    if it.get("ai_summary"):
        return it["ai_summary"].lstrip("☞").strip()
    desc = re.sub(r"\s*(\.\.\.|…)\s*$", "", it.get("desc") or "").strip()
    return desc or it["title"]


def headline_line(it):
    return (it.get("ai_headline") or it["title"]).lstrip("○").strip()


# ---------------------------------------------------------------------
# 기사 본문 가져오기 + AI 요약
# ---------------------------------------------------------------------
class _TextGrabber(HTMLParser):
    """네이버 뉴스 본문(id=dic_area) 또는 일반 기사 <p> 문단 텍스트 수집"""
    SKIP = {"script", "style", "noscript", "iframe", "figcaption", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0          # dic_area 안쪽 깊이
        self.skip = 0
        self.in_p = 0
        self.body, self.paras, self.meta = [], [], ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("property") in ("og:description",) and not self.meta:
            self.meta = a.get("content") or ""
        if tag in self.SKIP:
            self.skip += 1
        if self.depth:
            self.depth += 1
        elif a.get("id") in ("dic_area", "newsct_article", "articleBodyContents", "article-view-content-div"):
            self.depth = 1
        if tag == "p":
            self.in_p += 1
        if tag == "br" and self.depth:
            self.body.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if self.depth:
            self.depth -= 1
        if tag == "p" and self.in_p:
            self.in_p -= 1

    def handle_data(self, data):
        if self.skip:
            return
        if self.depth:
            self.body.append(data)
        elif self.in_p and len(data.strip()) > 20:
            self.paras.append(data.strip())


def fetch_article_text(it, limit=6000):
    """기사 본문 텍스트 (실패하면 네이버 요약문)"""
    for url in [it.get("naver"), it.get("url")]:
        if not url:
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (news clipping bot)"})
            with urllib.request.urlopen(req, timeout=15) as res:
                raw = res.read()
                charset = res.headers.get_content_charset()
            if not charset:
                m = re.search(rb'charset=["\']?([\w-]+)', raw[:3000])
                charset = m.group(1).decode() if m else "utf-8"
            page = raw.decode(charset, errors="ignore")
            g = _TextGrabber()
            g.feed(page)
            text = re.sub(r"\s+", " ", "".join(g.body)).strip() or " ".join(g.paras) or g.meta
            if len(text) > 200:
                return text[:limit]
        except Exception as e:
            print(f"[본문 실패] {url}: {e}", file=sys.stderr)
    return it.get("desc") or ""


def load_feedback():
    try:
        with open(FEEDBACK_FILE, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    except OSError:
        return []


def save_feedback(lines):
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    push_files(FEEDBACK_FILE, message="요약 피드백 저장")


def load_prompt():
    try:
        with open(PROMPT_FILE, encoding="utf-8") as f:
            prompt = f.read().strip()
    except OSError:
        prompt = "기사를 ☞로 시작하는 한 단락 클리핑으로 압축 요약하고, ○로 시작하는 한 줄 요약도 작성하세요."
    fb = load_feedback()
    if fb:
        prompt += ("\n\n[추가 요청사항 — 담당자가 텔레그램으로 준 피드백. 위 규칙과 겹치면 이것을 우선 적용]\n"
                   + "\n".join(f"- {l}" for l in fb))
    return prompt


def build_batch_prompt(targets, items):
    parts = [load_prompt(), "", "아래 기사를 위 규칙에 맞게 정리해 주세요.",
             "도구를 사용하지 말고, 다른 말 없이 아래 JSON 형식으로만 답하세요.",
             '{"번호": {"headline": "○ 한 줄 요약", "summary": "☞요약 단락"}, ...}', ""]
    for no in targets:
        it = items[no]
        parts += [f"### 번호 {no}", f"기사 제목: {it['title']}", "기사 본문:", fetch_article_text(it), ""]
    return "\n".join(parts)


def claude_cli():
    path = shutil.which("claude")   # 워크플로가 미리 설치해 둔 Claude Code
    return [path] if path else ["npx", "-y", "@anthropic-ai/claude-code"]


def summarize_one(no, items, env):
    """기사 1건 요약 → (번호, 결과 dict 또는 None, 오류)"""
    try:
        proc = subprocess.run(
            claude_cli() + ["-p", "--output-format", "text", "--model", CLAUDE_CODE_MODEL, "--max-turns", "1"],
            input=build_batch_prompt([no], items), capture_output=True, text=True, timeout=300, env=env,
        )
        out = proc.stdout.strip()
        if proc.returncode != 0 or not out:
            err = (proc.stderr or out or "응답 없음").strip().splitlines()
            print(f"[구독 요약 실패 {no}] {proc.stderr[-300:]}", file=sys.stderr)
            return no, None, (err[-1] if err else "실패")[:100]
        m = re.search(r"\{.*\}", out, re.S)
        data = json.loads(m.group(0)) if m else {}
        if "summary" in data:
            return no, data, ""
        for k, v in data.items():
            if isinstance(v, dict) and v.get("summary"):
                return no, v, ""
        return no, None, "형식 오류"
    except Exception as e:
        print(f"[구독 요약 실패 {no}] {e}", file=sys.stderr)
        return no, None, str(e)[:100]


def claude_ask(prompt, timeout=180):
    """Claude 구독으로 짧은 질문 → 답 텍스트 (실패 시 None)"""
    if not CLAUDE_CODE_OAUTH_TOKEN:
        return None
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = CLAUDE_CODE_OAUTH_TOKEN
    try:
        proc = subprocess.run(
            claude_cli() + ["-p", "--output-format", "text", "--model", CLAUDE_CODE_MODEL, "--max-turns", "1"],
            input=prompt, capture_output=True, text=True, timeout=timeout, env=env,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        print(f"[Claude 실패] {proc.stderr[-300:]}", file=sys.stderr)
    except Exception as e:
        print(f"[Claude 실패] {e}", file=sys.stderr)
    return None


def claude_json(prompt, timeout=180):
    text = claude_ask(prompt, timeout)
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def subscription_summarize(targets, items):
    """Claude 구독(Claude Code)으로 기사별 동시 요약. 결과 {번호: {...}}, 실패 시 '_error' 포함"""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}  # 구독 토큰을 쓰도록
    env["CLAUDE_CODE_OAUTH_TOKEN"] = CLAUDE_CODE_OAUTH_TOKEN
    result, errors = {}, []
    with ThreadPoolExecutor(max_workers=SUMMARY_WORKERS) as pool:
        for no, data, err in pool.map(lambda n: summarize_one(n, items, env), targets):
            if data:
                result[no] = data
            else:
                errors.append(err)
    if errors:
        result["_error"] = errors[0]
    return result


def ai_summarize(it):
    """Claude로 ☞ 요약 + ○ 한 줄 요약 생성. 실패하면 None"""
    if not ANTHROPIC_API_KEY:
        return None
    body = fetch_article_text(it)
    user = (
        f"기사 제목: {it['title']}\n"
        f"기사 본문:\n{body}\n\n"
        "위 기사를 규칙에 맞게 정리해 주세요. 다른 말 없이 아래 JSON 형식으로만 답하세요.\n"
        '{"headline": "○ 한 줄 요약", "summary": "☞요약 단락"}'
    )
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 1000,
        "system": load_prompt(),
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload, headers={
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as res:
                data = json.loads(res.read().decode())
            text = "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text")
            m = re.search(r"\{.*\}", text, re.S)
            result = json.loads(m.group(0)) if m else {"summary": text}
            return {"summary": (result.get("summary") or "").strip(),
                    "headline": (result.get("headline") or "").strip()}
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="ignore")[:200]
            print(f"[AI 요약 실패] HTTP {e.code} {msg}", file=sys.stderr)
            if e.code in (429, 500, 529) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return {"error": f"HTTP {e.code}"}
        except Exception as e:
            print(f"[AI 요약 실패] {e}", file=sys.stderr)
            return {"error": str(e)[:80]}
    return None


def report_date(now):
    return now + timedelta(days=1) if now.hour >= REPORT_NEXT_DAY_FROM_HOUR else now


def build_report(picks, items, now, headlines=None):
    found = [(no, star, items[no]) for no, star in picks if no in items]
    missing = [no for no, _ in picks if no not in items]
    if not found:
        return None, missing

    stars = [f for f in found if f[1]]
    highlights = stars if stars else found[:DEFAULT_HIGHLIGHTS] if DEFAULT_HIGHLIGHTS else []
    hl_nos = {f[0] for f in highlights}
    rest = [f for f in found if f[0] not in hl_nos]

    def block(it):
        return [f"☞{summary_line(it)}", "", f"원문: {it['title']}", article_link(it), ""]

    intro = REPORT_INTRO if (highlights or headlines) else REPORT_INTRO_PLAIN
    rd = report_date(now)
    lines = [
        REPORT_TITLE.format(month=rd.month, day=rd.day), "",
        REPORT_GREETING,
        intro.format(month=rd.month, day=rd.day), "",
    ]
    if headlines:   # 담당자가 직접 적어 준 ○ 줄
        lines += [f"○ {h.lstrip('○').strip()}" for h in headlines]
        lines += [""]
    elif highlights:
        lines += [f"○ {headline_line(it)}" for _, _, it in highlights]
        lines += [""]
    if highlights:
        lines += ["★ 눈에 띄는 뉴스 ★", ""]
        for _, _, it in highlights:
            lines += block(it)

    # 나머지는 카테고리 순서대로 섹션을 나눠서
    order = [REPORT_SECTIONS.get(c["name"], c["name"]) for c in CATEGORIES]
    sections = {}
    for _, _, it in rest:
        sec = REPORT_SECTIONS.get(it["cat"], it["cat"])
        sections.setdefault(sec, []).append(it)
    for sec in sorted(sections, key=lambda x: order.index(x) if x in order else 99):
        header = f"■ {sec}"
        if lines[-1] != "" or len(lines) > 6:   # 인사말 바로 뒤가 아니면 섹션 사이 한 줄 더 띄움
            lines.append("")
        lines += [header + "-" * max(10, 50 - display_width(header)), ""]
        for it in sections[sec]:
            lines += block(it)
    return "\n".join(lines).strip(), missing


def summarize_items(targets, items, notes):
    """고른 기사들에 AI 요약을 붙임 (실패한 건 네이버 요약문 사용)"""
    if not targets:
        return
    if CLAUDE_CODE_OAUTH_TOKEN:
        send_telegram(f"✏️ {len(targets)}건을 정리하고 있어요. 잠시만 기다려 주세요…")
        res = subscription_summarize(targets, items)
        errors = []
        for no in targets:
            r = res.get(no) or {}
            if r.get("summary"):
                items[no]["ai_summary"], items[no]["ai_headline"] = r["summary"], r.get("headline", "")
            else:
                errors.append(no)
        if errors:
            why = f" (원인: {res['_error']})" if res.get("_error") else ""
            notes.append("⚠️ AI 요약에 실패해 네이버 요약문으로 대신한 기사: " + ", ".join(errors) + why)
    elif ANTHROPIC_API_KEY:
        send_telegram(f"✏️ {len(targets)}건을 정리하고 있어요. 잠시만 기다려 주세요…")
        errors = []
        for no in targets:
            r = ai_summarize(items[no])
            if r and r.get("summary"):
                items[no]["ai_summary"], items[no]["ai_headline"] = r["summary"], r["headline"]
            else:
                errors.append(f"{no}({(r or {}).get('error', '실패')})")
        if errors:
            notes.append("⚠️ AI 요약에 실패해 네이버 요약문으로 대신한 기사: " + ", ".join(errors))
    else:
        notes.append("ℹ️ CLAUDE_CODE_OAUTH_TOKEN(구독 토큰)이 없어 네이버 요약문으로 정리했어요.")


def save_last_report(picks, items, headlines=None):
    used = {no: items[no] for no, _ in picks if no in items}
    with open(LAST_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump({"picks": picks, "items": used, "headlines": headlines or []},
                  f, ensure_ascii=False, indent=1)
    push_files(LAST_REPORT_FILE, message="최근 정리본 저장")


def load_last_report():
    pull_last_news()
    try:
        with open(LAST_REPORT_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return [tuple(p) for p in d["picks"]], d["items"], d.get("headlines") or []
    except (OSError, ValueError, KeyError):
        return None


def send_report(report, notes):
    send_telegram(report, plain=True)  # 복사해서 바로 쓸 수 있게 서식 없이 보냄
    if notes:
        send_telegram(html.escape("\n".join(notes), quote=False))


def run_selection(text):
    pull_last_news()
    if not os.path.exists(LAST_NEWS_FILE):
        send_telegram("아직 저장된 뉴스 목록이 없어요. 먼저 '뉴스'라고 보내서 목록을 받아 주세요.")
        return
    with open(LAST_NEWS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", {})
    picks = parse_selection(text)
    notes = []
    summarize_items([no for no, _ in picks if no in items], items, notes)
    report, missing = build_report(picks, items, datetime.now(KST))
    if report is None:
        send_telegram(f"목록에 없는 번호예요: {html.escape(', '.join(missing))}\n가장 최근에 받은 뉴스 목록의 번호로 보내 주세요.")
        return
    if missing:
        notes.append(f"⚠️ 목록에 없어서 뺀 번호: {', '.join(missing)}")
    notes.append("💬 ○ 줄을 바꾸려면 '- 내용'처럼 줄마다 -로 시작해서 보내 주세요. 요약 스타일은 '피드백: …'으로 알려 주세요.")
    save_last_report(picks, items)
    send_report(report, notes)


def run_headlines(text):
    """담당자가 보낸 ○ 줄로 바꿔서 마지막 정리본 다시 보내기"""
    last = load_last_report()
    if not last:
        send_telegram("바꿀 정리본이 없어요. 먼저 번호를 보내서 정리본을 받아 주세요.")
        return
    picks, items, _ = last
    heads = headline_lines(text)
    report, _ = build_report(picks, items, datetime.now(KST), headlines=heads)
    save_last_report(picks, items, heads)
    send_report(report, [])


def run_feedback(texts):
    """'피드백: …' 저장 → 마지막 정리본을 새 기준으로 다시 요약해서 보내기"""
    pull_last_news()
    fb = load_feedback()
    added = []
    for t in texts:
        body = re.sub(r"^피드백\s*[:：]?\s*", "", t).strip()
        if body:
            fb.append(body)
            added.append(body)
    save_feedback(fb)
    send_telegram("✅ 피드백을 저장했어요. 앞으로 요약에 계속 반영돼요.\n"
                  + "\n".join(f"• {html.escape(a)}" for a in added))
    last = load_last_report()
    if not last:
        return
    picks, items, heads = last
    notes = ["🔁 피드백을 반영해 마지막 정리본을 다시 썼어요."]
    summarize_items(list(items), items, notes)
    report, _ = build_report(picks, items, datetime.now(KST), headlines=heads or None)
    save_last_report(picks, items, heads)
    send_report(report, notes)


def run_feedback_admin(text):
    """'피드백 목록' / '피드백 삭제 2' / '피드백 초기화'"""
    pull_last_news()
    fb = load_feedback()
    m = re.match(r"피드백\s*삭제\s*(\d+)", text)
    if m:
        i = int(m.group(1)) - 1
        if 0 <= i < len(fb):
            removed = fb.pop(i)
            save_feedback(fb)
            send_telegram(f"🗑 삭제했어요: {html.escape(removed)}")
        else:
            send_telegram("그 번호의 피드백이 없어요. '피드백 목록'으로 번호를 확인해 주세요.")
        return
    if "초기화" in text:
        save_feedback([])
        send_telegram("🗑 저장된 피드백을 모두 지웠어요.")
        return
    if not fb:
        send_telegram("저장된 피드백이 없어요. '피드백: 첫 덩어리는 더 짧게'처럼 보내 주세요.")
    else:
        send_telegram("📋 저장된 피드백\n" + "\n".join(f"{i}. {html.escape(l)}" for i, l in enumerate(fb, 1))
                      + "\n\n지우려면 '피드백 삭제 번호'를 보내 주세요.")


# ---------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------
def run_daily(dry_run=False):
    now = datetime.now(KST)
    since, until = daily_window(now)
    results, failed, err, total = gather(since, until)
    msg = build_message("📰 데일리 뉴스 클리핑", since, fmt(until), results, failed, err, total)
    save_last_news(results)
    send_telegram(msg, dry_run)
    print("정기 발송 완료")


DATE_TOKEN_RE = re.compile(
    r"(?:(\d{4}|\d{2})\s*[-./년]\s*)?(\d{1,2})\s*(?:[-/.]|월)\s*(\d{1,2})\s*일?"   # 11/10, 11월 10일, 2026-11-10, 26/11/10
    r"|(?<![\d/.])(\d{1,2})\s*일"                                          # 12일 (앞 날짜와 같은 달)
)


def parse_date_range(text, now):
    """'뉴스 11/10~11/12', '뉴스 11월 10일부터 12일까지', '뉴스 11/14' → (시작, 끝) / 없으면 None"""
    dates, last_month, last_year = [], None, None
    for m in DATE_TOKEN_RE.finditer(text):
        try:
            if m.group(2):
                year = int(m.group(1)) if m.group(1) else None
                if year is not None and year < 100:
                    year += 2000   # 26 → 2026
                month, day = int(m.group(2)), int(m.group(3))
            elif last_month:
                year, month, day = last_year, last_month, int(m.group(4))
            else:
                continue
            y = year or now.year
            d = datetime(y, month, day, tzinfo=KST)
            if not year and d.date() > now.date():
                d = d.replace(year=y - 1)   # 연도 없이 미래 날짜면 작년으로
            dates.append(d)
            last_month, last_year = month, d.year
        except ValueError:
            continue
    if not dates:
        return None
    start, end = min(dates[:2]), max(dates[:2])
    return start, min(end + timedelta(days=1), now)   # 끝 날짜는 그날 24:00까지 (미래는 현재까지)


def run_range(text, dry_run=False):
    now = datetime.now(KST)
    rng = parse_date_range(text, now)
    if not rng:
        send_telegram("날짜를 못 알아들었어요. 예) 뉴스 11/10~11/12, 뉴스 11월 14일")
        return
    since, until = rng
    if until <= since:
        send_telegram("아직 오지 않은 날짜예요. 오늘이나 지난 날짜로 보내 주세요.")
        return
    if (until - since).days > MAX_RANGE_DAYS:
        send_telegram(f"한 번에 최대 {MAX_RANGE_DAYS}일까지 찾을 수 있어요. 기간을 나눠서 보내 주세요.")
        return
    send_telegram(f"📅 {fmt(since)} ~ {fmt(until)} 뉴스를 찾고 있어요. 잠시만 기다려 주세요…")
    results, failed, err, total = gather(since, until, pages=MAX_PAGES_RANGE)
    msg = build_message("📅 기간 지정 뉴스", since, fmt(until), results, failed, err, total)
    if (now - since).days >= 3:
        msg += "\n\nℹ️ 'AI·로봇'처럼 기사가 많은 검색어는 네이버 API가 최근 약 1,000건까지만 줘서, 며칠 지난 기간은 일부 빠질 수 있어요."
    save_last_news(results)
    send_telegram(msg, dry_run)
    print("기간 지정 답장 완료")


SEARCH_TRIGGER_RE = re.compile(r"찾아|^검색")
SEARCH_FILLER_RE = re.compile(
    r"관련된|관련|에\s*대한|에\s*대해|에\s*관한|내용의|내용이|내용|뉴스들?|기사들?|"
    r"찾아서|찾아줘|찾아\s*줘|찾아봐|찾아|알려줘|알려\s*줘|알려|보여줘|^검색|좀|"
    r"몇\s*[개건]\s*만?|\d+\s*[개건]\s*만?|있는지|있나|있으면|해줘|부탁해?|요$"
)


def parse_search(text, now):
    """→ (주제 문장, 개수, 시작, 끝)"""
    m = re.search(r"(\d+)\s*[개건]", text)
    n = min(int(m.group(1)), SEARCH_MAX_COUNT) if m else SEARCH_DEFAULT_COUNT
    rng = parse_date_range(text, now) if DATE_TOKEN_RE.search(text) else None
    since, until = rng if rng else (now - timedelta(days=SEARCH_DAYS), now)
    topic = DATE_TOKEN_RE.sub(" ", text)
    topic = re.sub(r"부터|까지|~", " ", topic)
    topic = SEARCH_FILLER_RE.sub(" ", topic)
    topic = re.sub(r"\s+", " ", topic).strip(" ,.?!")
    return topic, n, since, until


def run_search(text):
    now = datetime.now(KST)
    topic, n, since, until = parse_search(text, now)
    if not topic:
        send_telegram("무엇에 관한 뉴스를 찾을지 알려 주세요. 예) 휴머노이드 액추에이터 관련 뉴스 5개 찾아줘")
        return
    send_telegram(f"🔍 '{html.escape(topic)}' 뉴스를 찾고 있어요 ({fmt(since)} ~ {fmt(until)}, {n}건)…")

    # 1) 검색어 만들기 (Claude가 있으면 요청 문장을 네이버 검색어로 바꿈)
    queries = [topic]
    q = claude_json(
        "다음 요청에 맞는 기사를 네이버 뉴스에서 찾으려고 합니다. "
        "검색에 쓸 짧은 검색어(2~4단어 명사구) 1~3개를 만들어 주세요. "
        '도구를 쓰지 말고 다른 말 없이 JSON으로만 답하세요: {"queries": ["검색어1", "검색어2"]}\n\n'
        f"요청: {text}", timeout=120)
    if q and isinstance(q.get("queries"), list) and q["queries"]:
        queries = [str(x).strip() for x in q["queries"][:3] if str(x).strip()] or queries

    # 2) 기사 모으기 (중복 제거)
    cands, seen = [], set()
    for kw in queries:
        try:
            for a in collect(kw, since, until, pages=2):
                if a["url"] in seen or is_duplicate(a, cands, set()):
                    continue
                seen.add(a["url"])
                a["kw"] = kw
                a["_norm"] = norm(a["title"])
                a["_grams"] = bigrams(a["_norm"])
                cands.append(a)
        except Exception as e:
            print(f"[검색 실패] {kw}: {e}", file=sys.stderr)
    print(f"검색어 {queries} → 후보 {len(cands)}건")
    if not cands:
        send_telegram(f"'{html.escape(topic)}' 관련 기사를 찾지 못했어요. 다른 표현이나 더 긴 기간으로 다시 보내 주세요.")
        return
    cands.sort(key=lambda a: a["pub"], reverse=True)
    cands = cands[:60]

    # 3) 요청에 가장 맞는 기사 고르기
    picked = cands[:n]
    if len(cands) > n:
        listing = "\n".join(f"[{i}] {a['title']} — {a['desc'][:120]}" for i, a in enumerate(cands))
        r = claude_json(
            f"아래 기사 목록에서 다음 요청에 가장 잘 맞는 기사를 최대 {n}개 골라, 잘 맞는 순서대로 번호를 주세요. "
            "요청과 관련 없는 기사는 넣지 마세요. "
            '도구를 쓰지 말고 다른 말 없이 JSON으로만 답하세요: {"picks": [3, 0, 7]}\n\n'
            f"요청: {text}\n\n기사 목록:\n{listing}", timeout=180)
        if r and isinstance(r.get("picks"), list):
            idx = [i for i in r["picks"] if isinstance(i, int) and 0 <= i < len(cands)]
            if idx:
                picked = [cands[i] for i in dict.fromkeys(idx)][:n]
    for a in picked:
        a["relevant"] = True

    cat = {"name": f"{topic} 관련", "keywords": queries}
    results = [(cat, picked)]
    msg = build_message(f"🔍 '{html.escape(topic)}' 검색 결과", since, fmt(until), results, [], "", len(queries))
    save_last_news(results)
    send_telegram(msg)


def run_command(dry_run=False):
    now = datetime.now(KST)
    since, until = command_window(now)
    results, failed, err, total = gather(since, until)
    msg = build_message("🔎 요청하신 뉴스", since, f"현재({fmt(until)})", results, failed, err, total)
    save_last_news(results)
    send_telegram(msg, dry_run)
    print("요청 답장 완료")


# ○ 줄로 인식할 줄머리: ○, -, •, ·, 'ㅇ ' (휴대폰에서 치기 쉬운 기호)
HEADLINE_RE = re.compile(r"^\s*(?:○|-|•|·|ㅇ\s)\s*(\S.*)$")


def headline_lines(text):
    return [m.group(1).strip() for l in text.splitlines() if (m := HEADLINE_RE.match(l))]


def handle_updates(updates):
    wants_news = wants_help = False
    selection = headlines = admin = range_req = search_req = None
    feedbacks = []
    for u in updates:
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != TELEGRAM_CHAT_ID:
            continue  # 등록된 내 채팅이 아니면 무시
        text = (msg.get("text") or "").strip()
        as_numbers = re.sub(r"[‐‑‒–—―−－~]", "-", text)  # 휴대폰이 바꾼 다른 모양의 '-'도 번호로 인식
        if text in COMMAND_WORDS:
            wants_news = True
        elif SEARCH_TRIGGER_RE.search(text) and not text.startswith("피드백"):
            search_req = text   # 예) 휴머노이드 관련 뉴스 5개 찾아줘
        elif "뉴스" in text and DATE_TOKEN_RE.search(text):
            range_req = text   # 예) 뉴스 11/10~11/12
        elif SELECTION_RE.match(as_numbers):
            selection = as_numbers  # 여러 번 보냈으면 마지막 것
        elif re.match(r"피드백\s*(목록|삭제|초기화)", text):
            admin = text
        elif text.startswith("피드백"):
            feedbacks.append(text)
        elif headline_lines(text):
            headlines = text
        elif text:
            wants_help = True
    if wants_news or selection or feedbacks or headlines or admin or range_req or search_req:
        try:   # 받자마자 '입력 중…' 표시
            telegram("sendChatAction", {"chat_id": TELEGRAM_CHAT_ID, "action": "typing"}, tries=1)
        except Exception:
            pass
    print(f"메시지 확인: 뉴스요청={wants_news} / 번호={selection} / ○줄={bool(headlines)} "
          f"/ 피드백={len(feedbacks)} / 기타={wants_help}")

    if admin:
        run_feedback_admin(admin)
    if range_req:
        run_range(range_req)
    if search_req:
        run_search(search_req)
    if selection:
        run_selection(selection)
    if feedbacks:
        run_feedback(feedbacks)
    if headlines:
        run_headlines(headlines)
    if wants_news:
        send_telegram("🔎 뉴스를 모으고 있어요. 잠시만 기다려 주세요…")
        run_command()
    elif wants_help and not (selection or feedbacks or headlines or admin or range_req or search_req):
        send_telegram("• '뉴스' → 전날 오후 5시부터 지금까지의 기사 목록\n"
                      "• '뉴스 11/10~11/12', '뉴스 11월 14일' → 그 날짜의 기사 목록\n"
                      "• '휴머노이드 관련 뉴스 5개 찾아줘' → 주제에 맞는 기사를 찾아 목록으로 (날짜도 같이 쓸 수 있어요)\n"
                      "• 번호(예: 1-1*, 4-2, 2-1) → 마지막 목록에서 골라 보고용 양식으로 정리\n"
                      "   * 붙인 기사는 맨 위 '★ 눈에 띄는 뉴스'로 올라가요\n"
                      "• - 로 시작하는 줄(○도 가능) → 마지막 정리본의 ○ 줄을 그 내용으로 바꿔서 다시 보내기\n"
                      "• 피드백: 내용 → 요약 스타일에 계속 반영 + 마지막 정리본 다시 쓰기\n"
                      "• 피드백 목록 / 피드백 삭제 2 / 피드백 초기화")


def run_poll():
    """LISTEN_MINUTES 동안 텔레그램을 지켜보다가 메시지가 오면 바로 처리"""
    deadline = time.time() + LISTEN_MINUTES * 60
    offset = None
    print(f"{LISTEN_MINUTES}분 동안 텔레그램 메시지를 기다립니다")
    while True:
        remaining = int(deadline - time.time())
        if remaining <= 5:
            break
        params = {"timeout": min(50, remaining), "allowed_updates": '["message"]'}
        if offset:
            params["offset"] = offset   # 이전 메시지는 처리 완료(읽음) 표시
        try:
            updates = telegram("getUpdates", params)
        except Exception as e:
            print(f"[확인 실패] {e}", file=sys.stderr)
            time.sleep(10)
            continue
        if not updates:
            continue
        offset = max(u["update_id"] for u in updates) + 1
        try:
            handle_updates(updates)
        except Exception as e:
            # 처리 중 오류가 나도 멈추지 않고 알린 뒤 계속 지켜봄
            print(f"[처리 실패] {e}", file=sys.stderr)
            try:
                send_telegram(html.escape(f"⚠️ 요청을 처리하다 오류가 났어요: {str(e)[:150]}\n"
                                          "같은 내용을 한 번 더 보내 주세요."))
            except Exception:
                pass
    if offset:
        telegram("getUpdates", {"offset": offset, "timeout": 0, "limit": 1})
    print("대기 종료")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = (args[0] if args else os.environ.get("MODE", "daily")).strip() or "daily"
    dry_run = "--dry-run" in sys.argv

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        if not dry_run:
            sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 설정되지 않았어요.")

    if mode == "poll":
        run_poll()
        return

    if not (NAVER_CLIENT_ID and NAVER_CLIENT_SECRET):
        msg = "⚠️ NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 GitHub Secrets에 없어요."
        send_telegram(msg, dry_run)
        sys.exit(msg)

    if mode == "command":
        run_command(dry_run)
    else:
        run_daily(dry_run)


if __name__ == "__main__":
    main()
