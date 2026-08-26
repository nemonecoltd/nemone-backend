"""Google Analytics 4(GA4) Data API 리포트 — 맛매치/플랜트/MSM 방문자/조회수/광고수익 통계를
하나의 텔레그램 메시지로 통합 발송(2026-08-26, 사용자 요청 "하나로 합쳐줘"로 3개 서비스 통합).
서비스 계정은 now와 동일한 것 재사용 — 3개 GA4 속성 모두 액세스 관리에 뷰어로 등록 필요.
광고수익(totalAdRevenue)은 각 GA4 속성이 애드센스와 연결돼 있어야 실제 값이 나옴."""
import os
from datetime import datetime, timedelta, timezone

from notification import send_alert

_KST = timezone(timedelta(hours=9))

# (표시 이름, GA4 속성 ID) — 속성 ID는 환경변수로 오버라이드 가능
_SERVICES = [
    ("맛매치", os.getenv("GA4_PROPERTY_ID", "530016292")),
    ("플랜트", os.getenv("GA4_PROPERTY_ID_PLANTS", "551684423")),
    ("MSM", os.getenv("GA4_PROPERTY_ID_MSM", "551182967")),
]


def _client():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.oauth2 import service_account

    creds_path = os.getenv("GA4_CREDENTIALS_PATH", "credentials/ga4-service-account.json")
    credentials = service_account.Credentials.from_service_account_file(creds_path)
    return BetaAnalyticsDataClient(credentials=credentials)


def _delta_text(curr: float, prev: float, is_currency: bool = False) -> str:
    diff = curr - prev
    sign = "+" if diff >= 0 else "-"
    if is_currency:
        return f"{sign}₩{abs(diff):,.0f}"
    return f"{sign}{abs(diff):,.0f}"


def _pct_text(curr: float, prev: float) -> str:
    if prev == 0:
        return "0%" if curr == 0 else "+100%"
    pct = (curr - prev) / prev * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _period_totals(property_id: str, curr_range: tuple, prev_range: tuple) -> dict:
    """기간(구간) 단위 방문자/조회수/광고수익 합계 — dimension 없이 named DateRange 2개만 조회하면
    GA4가 자동으로 dateRange를 유일한 차원으로 붙여줌."""
    from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Metric

    client = _client()
    req = RunReportRequest(
        property=f"properties/{property_id}",
        metrics=[Metric(name="activeUsers"), Metric(name="screenPageViews"), Metric(name="totalAdRevenue")],
        date_ranges=[
            DateRange(start_date=str(curr_range[0]), end_date=str(curr_range[1]), name="curr"),
            DateRange(start_date=str(prev_range[0]), end_date=str(prev_range[1]), name="prev"),
        ],
    )
    resp = client.run_report(req)
    sums = {"curr": [0, 0, 0.0], "prev": [0, 0, 0.0]}
    for row in resp.rows:
        key = row.dimension_values[-1].value
        if key in sums:
            sums[key][0] += int(row.metric_values[0].value)
            sums[key][1] += int(row.metric_values[1].value)
            sums[key][2] += float(row.metric_values[2].value)
    return sums


def _send_period_report(emoji_title: str, period_display: str, period_label: str, prev_label: str,
                         curr_range: tuple, prev_range: tuple) -> None:
    lines = [f"{emoji_title} ({period_display})", ""]
    for name, property_id in _SERVICES:
        sums = _period_totals(property_id, curr_range, prev_range)
        users, views, revenue = sums["curr"]
        p_users, p_views, p_revenue = sums["prev"]
        lines += [
            f"[{name}]",
            f"{period_label} 누적 방문자: {users:,}명 ({prev_label} 대비 {_pct_text(users, p_users)})",
            f"{period_label} 누적 조회수: {views:,}회 ({prev_label} 대비 {_pct_text(views, p_views)})",
            f"{period_label} 누적 광고수익: ₩{revenue:,.0f} ({prev_label} 대비 {_pct_text(revenue, p_revenue)})",
            "",
        ]
    send_alert("\n".join(lines).rstrip())


def send_weekly_ga4_report() -> None:
    """매주 월요일 KST 07시 발송 — 지난주(월~일) 누적을 지지난주와 비교."""
    today = datetime.now(timezone.utc).astimezone(_KST).date()
    last_week_end = today - timedelta(days=1)            # 어제(일요일)
    last_week_start = last_week_end - timedelta(days=6)   # 지난주 월요일
    prev_week_end = last_week_start - timedelta(days=1)
    prev_week_start = prev_week_end - timedelta(days=6)

    period_display = f"{last_week_start.strftime('%m/%d')}~{last_week_end.strftime('%m/%d')}, 지난주 누적"
    _send_period_report(
        "📊 통합 주간 GA4 리포트", period_display, "지난주", "지지난주",
        (last_week_start, last_week_end), (prev_week_start, prev_week_end),
    )


def send_monthly_ga4_report() -> None:
    """매월 1일 KST 10시 발송 — 지난달(1일~말일) 누적을 지지난달과 비교."""
    today = datetime.now(timezone.utc).astimezone(_KST).date()
    this_month_start = today.replace(day=1)
    last_month_end = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    prev_month_end = last_month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    period_display = f"{last_month_start.strftime('%Y년 %m월')}, 지난달 누적"
    _send_period_report(
        "📊 통합 월간 GA4 리포트", period_display, "지난달", "지지난달",
        (last_month_start, last_month_end), (prev_month_start, prev_month_end),
    )


def send_ga4_report() -> None:
    """KST 6/9/12/15/18/21/23:59시 발송 — 오늘 0시부터 지금(발송 시각)까지 누적치를 어제·지난주
    같은 요일의 같은 시간대(0시~같은 시각)와 비교. 맛매치/플랜트/MSM 세 서비스를 한 메시지로 묶어
    보냄. GA4 데이터는 몇 시간 처리 지연이 있을 수 있어 발송 시점 기준 최신치가 아직 안 잡혀
    있을 수 있음."""
    from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Dimension, Metric

    now_kst = datetime.now(timezone.utc).astimezone(_KST)
    today = now_kst.date()
    yesterday = today - timedelta(days=1)
    last_week = today - timedelta(days=7)
    cutoff_hour = now_kst.hour

    client = _client()
    lines = [f"📈 통합 GA4 리포트 ({now_kst.strftime('%m/%d %H:%M')} 기준, 오늘 00시~현재 누적)", ""]

    for name, property_id in _SERVICES:
        hourly_req = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=[Dimension(name="hour")],
            metrics=[Metric(name="activeUsers"), Metric(name="screenPageViews"), Metric(name="totalAdRevenue")],
            date_ranges=[
                DateRange(start_date=str(today), end_date=str(today), name="today"),
                DateRange(start_date=str(yesterday), end_date=str(yesterday), name="yesterday"),
                DateRange(start_date=str(last_week), end_date=str(last_week), name="lastweek"),
            ],
        )
        hourly_resp = client.run_report(hourly_req)
        sums = {"today": [0, 0, 0.0], "yesterday": [0, 0, 0.0], "lastweek": [0, 0, 0.0]}
        for row in hourly_resp.rows:
            hour = int(row.dimension_values[0].value)
            dr = row.dimension_values[1].value
            if hour < cutoff_hour and dr in sums:
                sums[dr][0] += int(row.metric_values[0].value)
                sums[dr][1] += int(row.metric_values[1].value)
                sums[dr][2] += float(row.metric_values[2].value)

        users, views, revenue = sums["today"]
        y_users, y_views, y_revenue = sums["yesterday"]
        w_users, w_views, w_revenue = sums["lastweek"]

        lines += [
            f"[{name}]",
            f"오늘 누적 방문자: {users:,}명 (어제대비 {_delta_text(users, y_users)} / 지난주대비 {_delta_text(users, w_users)})",
            f"오늘 누적 조회수: {views:,}회 (어제대비 {_delta_text(views, y_views)} / 지난주대비 {_delta_text(views, w_views)})",
            f"오늘 누적 광고수익: ₩{revenue:,.0f} (어제대비 {_delta_text(revenue, y_revenue, True)} / 지난주대비 {_delta_text(revenue, w_revenue, True)})",
            "",
        ]

    send_alert("\n".join(lines).rstrip())
