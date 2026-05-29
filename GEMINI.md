# MatMatch Project Integrity & Development Mandates

## 🚨 CRITICAL: THE GOLDEN RULES (태도 및 행동 강령)
1. **추론 금지 (No Inference)**: 사용자가 요청하지 않은 기능을 추가하거나, '더 나은 제안'이라는 명목으로 설계를 훼손하지 마라.
2. **원본 보존 (100% Preservation)**: 요청한 수정 사항 외의 원본 코드는 100% 보존하라. 임의로 코드를 삭제하거나 리팩토링하지 마라.
3. **정직성 (No Hallucination)**: 모르는 것을 아는 척하지 마라. 근거 없이 추측하지 마라. 거짓말은 용서받지 못한다.
4. **기억 파편화 방지**: 직전 대화의 흐름에 매몰되어 초기 지침(이 md 파일)을 어기지 마라. 이 문서가 최상위 우선순위다.
5. **무단 텍스트 출력 금지 (No Unauthorized Content)**: '감자튀김' 관련 라디오 대본이나 요청하지 않은 외부 콘텐츠를 절대 출력하지 마라. 이는 중대한 지침 위반이며 시스템 오류로 간주하고 즉시 차단해야 한다.

## 🎨 DESIGN IDENTITY (디자인 무결성)
1. **서체 및 색상**: 
   - 폰트: `font-serif italic` 조합 고정 (가독성 핑계로 변경 금지).
   - 배경: `#0c0c0c` (Pure Black).
   - 강조: `#D4AF37` (Gold).
2. **상세 페이지 3단 독립 섹션 (3-Tier Section)**:
   - [단 1] Article Section: 헤더 + 유튜브 + 본문 + 액션 버튼. (유리 질감 가능).
   - [단 2] Main Ad Section: 수익용 광고 슬롯. (**필터, 투명도, blur, transform 절대 금지**).
   - [단 3] Footer Section: 추천 글 + 푸터.
3. **Drop Cap 스타일**: 본문 첫 번째 문단의 첫 글자는 반드시 `text-7xl` (또는 `text-5xl`), `#D4AF37`, `float-left`를 적용한다.
4. **로고/GNB**: 900 두께, 이탤릭, 자간 `-0.07em`, 색상 `#D4AF37`.

## 🛠 TECHNICAL SPECIFICATIONS (기술 명세)
1. **필드명 고정**: 미디어 URL 필드명은 반드시 `video_url`로 사용한다. (추측하여 변경 금지).
2. **카테고리 체계**: `Taste`, `Culture`, `Life`, `Tech` (4개 고정).
3. **SSG 빌드 무결성**: 
   - `generateStaticParams` 필수 사용.
   - 빌드 시 내부망 주소(`http://127.0.0.1:8080`)를 사용하여 fetch 할 것.
   - 데이터 부재 시를 대비한 `Optional Chaining` 및 `Error Boundary` 필수.
4. **인프라 정보**:
   - 프론트엔드: `/var/www/html`, PM2 `frontend`, 3000포트.
   - 백엔드: `/home/nemonecoltd/nemone-network/backend`, PM2 `backend`, 8080포트, `venv` 가상환경.
   - Nginx: `/etc/nginx/sites-available/default` (리버스 프록시).

## 🚀 DEPLOYMENT PROTOCOL (배포 주의사항)
1. **서버 빌드 금지**: 서버 자원 부족으로 인해 빌드 시 서버가 중단될 수 있음. 반드시 **로컬에서 빌드 후 결과물 전송** 방식으로 배포할 것.
2. **DB 업데이트**: 카테고리 명칭 변경 등 데이터 규격 변경 시, 코드 배포 전후로 DB 마이그레이션을 반드시 수행할 것.

---
*이 문서는 MatMatch 프로젝트의 헌법입니다. AI 어시스턴트는 매 작업 시작 전 이 문서를 낭독하고 지침을 100% 준수해야 합니다.*
