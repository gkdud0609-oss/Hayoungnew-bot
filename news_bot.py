# -*- coding: utf-8 -*-
"""
대동 홍보팀 뉴스 클리핑 봇
- 네이버 뉴스 검색 API로 기사 수집 → 카테고리별 정리(중복 제거) → 텔레그램 발송

모드
  python news_bot.py daily   : 매일 정기 발송 (전날 17:00 ~ 당일 17:00 기사)
  python news_bot.py poll    : 텔레그램 확인 → '뉴스' 메시지가 있으면 (전날 17:00 ~ 현재 기사) 답장
  python news_bot.py daily --dry-run : 텔레그램 발송 없이 화면에만 출력
"""
import os
import re
import sys
import json
import html
import difflib
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

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

# =====================================================================
# 검색어 설정 (여기만 고치면 검색어를 바꿀 수 있어요)
# max = 카테고리당 최대 기사 수
# =====================================================================
CATEGORIES = [
    {
        "name": "자사뉴스",
        "keywords": ["대동", "대동기어", "대동모빌리티", "대동금속", "하이드로텍",
                     "제주대동", "대동애그테크", "대동에이아이랩", "대동로보틱스"],
        "max": 30,
    },
    {
        "name": "경쟁사 및 시장 동향",
        "keywords": ["TYM", "LS엠트론", "존디어", "구보다", "얀마", "농기계",
                     "트랙터", "건설기계", "HD건설기계", "두산밥캣"],
        "max": 15,
    },
    {
        "name": "스마트농업&기후위기",
        "keywords": ["스마트농업", "스마트팜", "스마트온실", "기후위기"],
        "max": 10,
    },
    {
        "name": "농업정책",
        "keywords": ["농식품부", "농진청"],
        "max": 10,
    },
    {
        "name": "로봇&모빌리티",
        "keywords": ["로봇", "농업로봇", "전기스쿠터", "전기이륜차"],
        "max": 10,
    },
    {
        "name": "AI",
        "keywords": ["AI", "인공지능"],
        "max": 10,
    },
]

# 수집 기준 시각 (17 = 오후 5시). 전날 이 시각부터 모읍니다.
CUTOFF_HOUR = 17

# 텔레그램에서 이 단어를 보내면 뉴스를 보내 줍니다
COMMAND_WORDS = {"뉴스", "/news"}

# 제목에 검색어가 있는 기사만 남기기 (True 권장: 'AI', '로봇'처럼 넓은 검색어의 잡음 제거)
TITLE_MUST_CONTAIN_KEYWORD = True

# 앞 글자가 한글/영문이면 다른 단어로 보고 제외하는 검색어
#  예) '구보다' → '연구보다', '요구보다' 제외 / '얀마' → '미얀마' 제외 / '대동' → '현대동…' 제외
STRICT_KEYWORDS = {"대동", "구보다", "얀마", "TYM", "AI"}

# 검색어별 제외어 (제목에 이 단어가 있으면 버림)
EXCLUDE_WORDS = {
    "대동": ["대동맥", "대동강", "대동여지도", "대동단결", "대동제", "대동병원",
             "대동소이", "대동세상", "대동계", "대동법", "대동문", "대동초", "대동중",
             "대동고", "대동역", "대동동"],
}

# 같은 기사로 볼 제목 유사도 (0~1, 높을수록 엄격)
SIMILARITY_THRESHOLD = 0.8

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
MAX_PAGES = 5  # 검색어당 최대 500건까지 확인
DAYS = "월화수목금토일"


# ---------------------------------------------------------------------
# 수집 기간
# ---------------------------------------------------------------------
def at_cutoff(d):
    return datetime(d.year, d.month, d.day, CUTOFF_HOUR, 0, tzinfo=KST)


def daily_window(now):
    """정기 발송: 전날 17:00 ~ 당일 17:00 (17시 이전에 수동 실행하면 하루 전 구간)"""
    end = at_cutoff(now)
    if now < end:
        end -= timedelta(days=1)
    return end - timedelta(days=1), end


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
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
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


def collect(keyword, since, until):
    """검색어 하나로 since ~ until 사이 기사 수집 (네이버는 최신순으로 줌)"""
    items = []
    for page in range(MAX_PAGES):
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
    k = re.escape(kw)
    if kw not in STRICT_KEYWORDS:
        return re.compile(k)
    if re.fullmatch(r"[A-Za-z0-9]+", kw):
        return re.compile(rf"(?<![A-Za-z]){k}(?![A-Za-z])")
    return re.compile(rf"(?<![가-힣A-Za-z]){k}")


def title_matches(title, kw):
    if any(w in title for w in EXCLUDE_WORDS.get(kw, [])):
        return False
    if not TITLE_MUST_CONTAIN_KEYWORD:
        return True
    return bool(keyword_pattern(kw).search(title))


def norm(title):
    title = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", title)   # [단독], (종합) 등 제거
    return re.sub(r"[^0-9A-Za-z가-힣]", "", title)


def is_duplicate(article, accepted, seen_urls):
    if article["url"] in seen_urls or article["naver"] in seen_urls:
        return True
    n = norm(article["title"])
    for a in accepted:
        if n == a["_norm"] or difflib.SequenceMatcher(None, n, a["_norm"]).ratio() >= SIMILARITY_THRESHOLD:
            return True
    return False


def media_name(url):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    for domain, name in MEDIA_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or "-"


def gather(since, until):
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
                for a in collect(kw, since, until):
                    if title_matches(a["title"], kw):
                        candidates.append(a)
            except Exception as e:
                print(f"[실패] {kw}: {e}", file=sys.stderr)
                failed.append(kw)
                first_error = first_error or str(e)

        candidates.sort(key=lambda a: a["pub"], reverse=True)
        picked = []
        for a in candidates:
            if len(picked) >= cat["max"]:
                break
            if is_duplicate(a, accepted, seen_urls):
                continue
            # 여러 검색어에 걸리면 가장 구체적인(긴) 검색어로 표시
            matched = [k for k in cat["keywords"] if title_matches(a["title"], k)]
            a["kw"] = max(matched, key=len) if matched else cat["keywords"][0]
            a["_norm"] = norm(a["title"])
            picked.append(a)
            accepted.append(a)
            seen_urls.update({a["url"], a["naver"]})
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
        for a in arts:
            t = html.escape(a["title"])
            url = html.escape(a["url"] or "", quote=True)
            lines.append(
                f"• [{html.escape(a['kw'])}] <a href=\"{url}\">{t}</a>"
                f" - {html.escape(media_name(a['url']))} {a['pub']:%m/%d %H:%M}"
            )
            if SHOW_DESCRIPTION and a["desc"]:
                lines.append(f"   {html.escape(a['desc'][:80])}…")
        lines.append("")
    if failed:
        lines.append(f"⚠️ 수집 실패 검색어: {html.escape(', '.join(failed))}")
        lines.append(f"원인: {html.escape(first_error)}")
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
def telegram(method, params):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as res:
            body = json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"텔레그램 오류 HTTP {e.code}: {e.read().decode(errors='ignore')}")
    if not body.get("ok"):
        raise RuntimeError(f"텔레그램 오류: {body}")
    return body.get("result")


def send_telegram(text, dry_run=False):
    if dry_run:
        print(text)
        return
    for chunk in split_message(text):
        telegram("sendMessage", {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })


# ---------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------
def run_daily(dry_run=False):
    now = datetime.now(KST)
    since, until = daily_window(now)
    results, failed, err, total = gather(since, until)
    msg = build_message("📰 데일리 뉴스 클리핑", since, fmt(until), results, failed, err, total)
    send_telegram(msg, dry_run)
    print("정기 발송 완료")


def run_command(dry_run=False):
    now = datetime.now(KST)
    since, until = command_window(now)
    results, failed, err, total = gather(since, until)
    msg = build_message("🔎 요청하신 뉴스", since, f"현재({fmt(until)})", results, failed, err, total)
    send_telegram(msg, dry_run)
    print("요청 답장 완료")


def run_poll():
    """텔레그램에 쌓인 메시지 확인 → 내 채팅에서 '뉴스'가 왔으면 답장"""
    updates = telegram("getUpdates", {"timeout": 0, "allowed_updates": '["message"]'})
    if not updates:
        print("새 메시지 없음")
        return

    # 확인한 메시지는 바로 '읽음' 처리 → 다음 확인 때 중복 답장 방지
    last_id = max(u["update_id"] for u in updates)
    telegram("getUpdates", {"offset": last_id + 1, "timeout": 0, "limit": 1})

    wants_news = wants_help = False
    for u in updates:
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != TELEGRAM_CHAT_ID:
            continue  # 등록된 내 채팅이 아니면 무시
        text = (msg.get("text") or "").strip()
        if text in COMMAND_WORDS:
            wants_news = True
        elif text:
            wants_help = True

    if wants_news:
        send_telegram("🔎 뉴스를 모으고 있어요. 잠시만 기다려 주세요…")
        run_command()
    elif wants_help:
        send_telegram("'뉴스'라고 보내 주시면 전날 오후 5시부터 지금까지의 기사를 정리해 드려요.")
    else:
        print("처리할 메시지 없음")


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
