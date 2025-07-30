import os
import requests
import json
from datetime import datetime, timedelta
import time
import statistics
from dotenv import load_dotenv

# --- КОНФИГУРАЦИЯ ---

# Загружаем переменные окружения из .env файла
load_dotenv()

# API ключи
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
CRYPTO_PANIC_API_KEY = os.getenv("CRYPTO_PANIC_API_KEY")

# Монеты для отслеживания (тикер: [репозиторий на GitHub, ключевые слова для поиска])
TARGET_ASSETS = {
    "DOT": ["polkadot-sdk/polkadot", "Polkadot"],
    "SOL": ["solana-labs/solana", "Solana"],
    "ATOM": ["cosmos/cosmos-sdk", "Cosmos ATOM"],
    "RNDR": ["RenderToken/rndr-app", "Render Token"],
}

# Параметры анализа
HISTORY_FILE = "synapse_history.json" # Файл для хранения исторических данных
HOURS_TO_ANALYZE = 24 # Анализируем активность за последние 24 часа
ANOMALY_STD_DEV_THRESHOLD = 2.0 # Порог для аномалии (во сколько раз ст.отклонение должно быть превышено)
CONVERGENCE_THRESHOLD = 2 # Сколько аномалий нужно для "сигнала конвергенции"

# --- ИНСТРУМЕНТЫ API ---

def get_github_commits(repo: str) -> int:
    """Получает количество коммитов в репозитории за последние 24 часа."""
    since_date = (datetime.utcnow() - timedelta(hours=HOURS_TO_ANALYZE)).isoformat()
    url = f"https://api.github.com/repos/{repo}/commits"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    params = {"since": since_date, "per_page": 100}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        # API возвращает до 100 результатов на страницу, для простоты считаем первую страницу
        return len(response.json())
    except requests.RequestException as e:
        print(f"  [Error] GitHub API request failed for {repo}: {e}")
        return 0

def get_news_mentions(query: str) -> int:
    """Получает количество упоминаний в новостях с CryptoPanic."""
    if not CRYPTO_PANIC_API_KEY:
        print("  [Warning] CRYPTO_PANIC_API_KEY not set. Skipping news analysis.")
        return 0
    url = f"https://cryptopanic.com/api/v1/posts/?auth_token={CRYPTO_PANIC_API_KEY}&search={query}&public=true"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        posts = response.json().get("results", [])
        # Фильтруем посты за последние 24 часа
        now = datetime.utcnow()
        recent_posts = [
            p for p in posts 
            if now - datetime.fromisoformat(p['created_at'].replace('Z', '')) < timedelta(hours=HOURS_TO_ANALYZE)
        ]
        return len(recent_posts)
    except requests.RequestException as e:
        print(f"  [Error] CryptoPanic API request failed for {query}: {e}")
        return 0

def get_reddit_mentions(query: str, subreddit: str = "cryptocurrency") -> int:
    """Получает количество упоминаний на Reddit (упрощенная версия через поиск)."""
    # Внимание: это очень упрощенный метод. Для серьезного анализа лучше использовать PRAW и Pushshift API.
    url = f"https://www.reddit.com/r/{subreddit}/search.json"
    headers = {'User-agent': 'ChainSynapse Bot 0.1'}
    params = {'q': query, 'sort': 'new', 't': 'day', 'restrict_sr': 'on'}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        posts = response.json()['data']['children']
        return len(posts)
    except (requests.RequestException, KeyError) as e:
        print(f"  [Error] Reddit search failed for {query}: {e}")
        return 0

# --- ЛОГИКА АНАЛИЗА ---

def load_history() -> dict:
    """Загружает историю из JSON файла."""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_history(history: dict):
    """Сохраняет историю в JSON файл."""
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)

def analyze_anomaly(data_points: list, current_value: int) -> tuple[bool, float, float]:
    """Анализирует, является ли текущее значение аномалией."""
    if len(data_points) < 5: # Нужно достаточно данных для статистики
        return False, 0.0, 0.0
    
    mean = statistics.mean(data_points)
    stdev = statistics.stdev(data_points)
    
    if stdev == 0: # Избегаем деления на ноль
        return False, mean, stdev

    is_anomaly = current_value > (mean + ANOMALY_STD_DEV_THRESHOLD * stdev)
    return is_anomaly, mean, stdev

def run_analysis():
    """Главный цикл анализа."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] --- Starting ChainSynapse Analysis ---")
    history = load_history()
    convergence_alerts = []

    for ticker, (repo, query) in TARGET_ASSETS.items():
        print(f"[INFO] Analyzing {query} ({ticker})...")
        
        if ticker not in history:
            history[ticker] = {"github": [], "news": [], "reddit": []}

        # 1. Сбор свежих данных
        github_commits = get_github_commits(repo)
        news_mentions = get_news_mentions(query)
        reddit_mentions = get_reddit_mentions(query)
        time.sleep(2) # Небольшая задержка, чтобы не превышать лимиты API

        # 2. Анализ аномалий
        sources = {
            "GitHub": (github_commits, history[ticker]["github"]),
            "News": (news_mentions, history[ticker]["news"]),
            "Reddit": (reddit_mentions, history[ticker]["reddit"]),
        }
        
        anomalies_found = []
        for name, (current, past_data) in sources.items():
            is_anomaly, mean, stdev = analyze_anomaly(past_data, current)
            status = "-> ANOMALY DETECTED!" if is_anomaly else "-> Normal"
            print(f"  [{name}] Activity in last {HOURS_TO_ANALYZE}h: {current} (Baseline avg: {mean:.1f}, StDev: {stdev:.1f}) {status}")
            if is_anomaly:
                anomalies_found.append(name)
        
        # 3. Проверка на конвергенцию
        if len(anomalies_found) >= CONVERGENCE_THRESHOLD:
            convergence_alerts.append((query, ticker, anomalies_found))

        # 4. Обновление истории
        history[ticker]["github"].append(github_commits)
        history[ticker]["news"].append(news_mentions)
        history[ticker]["reddit"].append(reddit_mentions)
        # Ограничиваем историю, чтобы она не росла бесконечно
        for key in history[ticker]:
            history[ticker][key] = history[ticker][key][-30:]

    # 5. Сохранение истории
    save_history(history)

    # 6. Вывод отчета о конвергенции
    if convergence_alerts:
        print("\n" + "-"*50)
        for query, ticker, platforms in convergence_alerts:
            print(f"!!! 🧠🔗 CONVERGENCE ALERT: {query} ({ticker}) 🔗🧠 !!!")
            print(f"Anomalies detected on {len(platforms)} platforms: {platforms}")
            print("This could indicate a significant organic event. DYOR!")
        print("-" * 50)
    
    print("\n--- Analysis Complete ---")


if __name__ == "__main__":
    run_analysis()
