import os
import json
from datetime import datetime

STATS_FILE = os.path.join('data', 'stats.json')


def load_stats():
    if not os.path.exists(STATS_FILE):
        return {'total_visits': 0, 'last_visit_date': '', 'today_visits': 0}
    with open(STATS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_stats(stats):
    with open(STATS_FILE, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def record_visit():
    """접속 1회를 기록하고 갱신된 통계를 반환한다. 로그인 재시도마다가
    아니라, 실제 접속(전화 연결) 당 한 번만 호출해야 한다."""
    stats = load_stats()
    today = datetime.now().strftime('%Y-%m-%d')
    if stats.get('last_visit_date') != today:
        stats['today_visits'] = 0
        stats['last_visit_date'] = today
    stats['today_visits'] = stats.get('today_visits', 0) + 1
    stats['total_visits'] = stats.get('total_visits', 0) + 1
    save_stats(stats)
    return stats
