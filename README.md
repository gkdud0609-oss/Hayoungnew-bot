# 데일리 뉴스 클리핑 봇 설치 안내

- 매일 오후 5시: 전날 17:00 ~ 당일 17:00 기사를 6개 카테고리로 정리해 발송
- 텔레그램 봇에게 `뉴스`라고 보내면: 전날 17:00 ~ 현재 기사를 정리해 답장 (5분마다 확인하므로 5~15분 걸림)

## 1. 텔레그램 봇 만들기 (5분)

1. 텔레그램에서 **@BotFather** 검색 → 대화 시작 → `/newbot` 입력
2. 봇 이름(예: 대동 뉴스클리핑)과 아이디(예: daedong_news_bot, 반드시 `bot`으로 끝나야 함) 입력
3. `123456789:ABCdef...` 형태의 **봇 토큰**이 나오면 복사해 둡니다 → `TELEGRAM_BOT_TOKEN`
4. 방금 만든 봇을 검색해 들어가서 **아무 메시지나 하나 보냅니다** (예: 안녕)
5. 브라우저 주소창에 아래 주소를 입력합니다 (`<토큰>` 자리에 3번 토큰)
   `https://api.telegram.org/bot<토큰>/getUpdates`
6. 화면에서 `"chat":{"id":123456789` 부분의 숫자를 복사합니다 → `TELEGRAM_CHAT_ID`

> 팀 단톡방으로 받고 싶다면: 봇을 그룹에 초대 → 그룹에 메시지 하나 작성 → 5번 다시 확인.
> 그룹 ID는 `-100...` 처럼 마이너스로 시작합니다.

## 2. GitHub 저장소 만들고 파일 올리기

1. github.com 가입/로그인 → 오른쪽 위 **+** → **New repository**
2. 이름 예: `news-bot`, **Private** 선택 → Create repository
3. **Add file → Upload files** 에서 `news_bot.py`, `README.md` 업로드 → Commit changes
4. **Add file → Create new file** 클릭
   - 파일 이름 칸에 정확히 `.github/workflows/daily-news.yml` 입력 (슬래시를 치면 폴더가 자동 생성됨)
   - `daily-news.yml` 내용을 그대로 붙여넣기 → Commit changes

## 3. ★ API 키 넣기 (GitHub Secrets)

저장소 **Settings → Secrets and variables → Actions → New repository secret** 에서 4개를 하나씩 등록합니다.
**Name은 아래와 글자 하나까지 똑같이** 입력하세요.

| Name | Secret에 넣을 값 |
|---|---|
| `NAVER_CLIENT_ID` | 네이버 개발자센터 → 내 애플리케이션의 Client ID |
| `NAVER_CLIENT_SECRET` | 같은 곳의 Client Secret |
| `TELEGRAM_BOT_TOKEN` | 1-3번 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 1-6번 숫자 |

키는 코드 파일에 직접 적지 않습니다. Secrets에 넣으면 암호화되어 본인 외에는 볼 수 없습니다.

## 3-1. 저장소를 공개(Public)로 바꾸기

5분마다 텔레그램을 확인하려면 GitHub 무료 사용량이 많이 필요해서, 저장소를 **Public**으로 바꿔야 무료로 쓸 수 있습니다.
코드만 공개되고 API 키(Secrets)는 공개되지 않습니다.

**Settings → General → 맨 아래 Danger Zone → Change visibility → Change to public** → 확인 문구 입력

## 4. 바로 테스트하기

1. 저장소 상단 **Actions** 탭 → (처음이면 "I understand… enable" 버튼 클릭)
2. 왼쪽 **데일리 뉴스 클리핑** → 오른쪽 **Run workflow** → mode 선택(daily = 정기 발송 / command = '뉴스' 답장) → Run workflow
3. 1분쯤 뒤 텔레그램으로 메시지가 오면 성공 ✅
4. 네이버 오류가 있으면 텔레그램 메시지에 원인과 해결 방법이 함께 표시됩니다
   - `HTTP 401` → 네이버 Client ID / Secret 값 오류 (Secrets 다시 입력)
   - `HTTP 403` → 네이버 내 애플리케이션 > API 설정 > 사용 API에 '검색' 추가
   - 텔레그램이 아예 안 오면: Actions 실행 기록 → `뉴스 수집 및 텔레그램 발송` 로그 확인 (`chat not found` = chat ID 오류)
5. 봇에게 `뉴스`라고 보내고 5~15분 기다려 답장이 오는지 확인

이후로는 **매일 오후 5시에 자동 발송**됩니다. PC를 꺼 둬도 됩니다.

## 5. 알아두면 좋은 점

- GitHub 예약 실행은 서버 사정에 따라 **수 분~수십 분 늦게** 도착할 수 있습니다.
  수집 기간은 실행 시각과 관계없이 항상 '전날 17:00 ~ 당일 17:00'로 고정되므로, 늦게 도착해도 내용은 같습니다.
- 5분마다 확인은 GitHub 사정에 따라 가끔 건너뛰어질 수 있습니다. 답이 늦으면 한 번 더 `뉴스`를 보내 주세요.
- 봇은 Secrets에 등록한 chat ID의 메시지에만 답합니다.
- 저장소에 60일 동안 아무 변경이 없으면 GitHub이 예약 실행을 멈출 수 있습니다. 그럴 땐 Actions 탭에서 다시 Enable 하면 됩니다.
- 네이버 검색 API는 하루 25,000회까지 무료이고, 한 번 정리할 때 30~150회 정도 씁니다.

## 6. 설정 바꾸기 (`news_bot.py` 상단)

| 항목 | 설명 |
|---|---|
| `CATEGORIES` | 카테고리 이름·검색어·카테고리당 최대 기사 수(`max`) |
| `CUTOFF_HOUR` | 수집 기준 시각 (기본 17 = 오후 5시) |
| `COMMAND_WORDS` | 봇에게 보낼 요청 단어 (기본 `뉴스`, `/news`) |
| `TITLE_MUST_CONTAIN_KEYWORD` | 제목에 검색어가 있는 기사만 (기본 True) |
| `STRICT_KEYWORDS` | 앞 글자가 붙으면 다른 단어로 보는 검색어 (구보다 → '연구보다' 제외 등) |
| `EXCLUDE_WORDS` | 검색어별 제외어 (대동 → 대동맥, 대동강 등) |
| `SHOW_DESCRIPTION` | 기사 요약 한 줄 같이 보내기 |
| `MEDIA_NAMES` | 매체명 표시용 도메인 목록 (없으면 도메인 주소로 표시) |

GitHub 웹에서 파일을 열고 연필(✏️) 아이콘을 눌러 수정 → Commit 하면 다음 실행부터 반영됩니다.
