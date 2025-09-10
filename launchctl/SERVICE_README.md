# 🎵 Music Genre Autoupdater Service

Автоматичний сервіс для оновлення років альбомів з MusicBrainz та Discogs APIs.

## 🚀 Швидкий старт

### Встановлення сервісу

```bash
./manage_service.sh install
```

### Перевірка статусу

```bash
./manage_service.sh status
```

### Перегляд логів

```bash
./manage_service.sh logs
```

## 📅 Розклад роботи

- **Коли:** Щодня о 2:00 ранку
- **Що:** Оновлення років альбомів для всієї музичної бібліотеки
- **Тривалість:** 3-6 годин (залежно від розміру бібліотеки)

## 📊 Моніторинг

### Логи розташовані в:

- **LaunchCtl логи:** `~/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs/launchctl/`
- **Основні логи:** `~/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs/main/`
- **Помилки:** `~/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/Python Scripts/Genres Autoupdater v2.0/error.log`
- **Аналітика:** `~/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/Python Scripts/Genres Autoupdater v2.0/analytics.log`

### Команди управління:

| Команда                         | Опис              |
| ------------------------------- | ----------------- |
| `./manage_service.sh install`   | Встановити сервіс |
| `./manage_service.sh uninstall` | Видалити сервіс   |
| `./manage_service.sh start`     | Запустити зараз   |
| `./manage_service.sh stop`      | Зупинити          |
| `./manage_service.sh status`    | Показати статус   |
| `./manage_service.sh logs`      | Показати логи     |
| `./manage_service.sh test`      | Тестовий запуск   |

## ⚙️ Конфігурація

### Основні налаштування в `my-config.yaml`:

```yaml
# Тестові артисти (порожній = вся бібліотека)
development:
  test_artists: [] # Для продакшн
  debug_mode: true

# API таймаути
applescript_timeouts:
  default: 3600 # 1 година для повної бібліотеки
  single_artist_fetch: 600 # 10 хвилин для одного артиста
  full_library_fetch: 3600 # 1 година для повної бібліотеки

# Batch обробка
year_retrieval:
  processing:
    batch_size: 25
    delay_between_batches: 20
```

## 🔧 Технічні деталі

### Системні вимоги:

- macOS з Music.app
- Python 3.12+ (через pyenv)
- Активні API ключі для Discogs та Last.fm
- Інтернет з'єднання

### Ресурси:

- **Пам'ять:** ~200MB під час роботи
- **CPU:** Low priority (nice=10)
- **Мережа:** ~1-2 API запити на секунду
- **Диск:** Логи та кеш ~50-100MB

### Безпека:

- API ключі зашифровані в конфігурації
- Валідація всіх вхідних даних
- Автоматичне очищення небезпечних символів
- Timeout захист для всіх операцій

## 🆘 Діагностика проблем

### Сервіс не запускається:

```bash
# Перевірити статус
./manage_service.sh status

# Подивитися помилки
./manage_service.sh logs

# Переінсталювати
./manage_service.sh uninstall
./manage_service.sh install
```

### Помилки в роботі:

```bash
# Перевірити основні логи
tail -f "~/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs/main/main.log"

# Перевірити помилки
tail -f error.log

# Тестовий запуск
./manage_service.sh test
```

### Часті проблеми:

| Проблема             | Рішення                                         |
| -------------------- | ----------------------------------------------- |
| AppleScript timeout  | Збільшити `applescript_timeouts` в конфігурації |
| API rate limits      | Зменшити `requests_per_second` в конфігурації   |
| Повна пам'ять        | Очистити кеш файли в logs директорії            |
| Music.app недоступна | Перезапустити Music.app                         |

## 📈 Оптимізація продуктивності

### Для великих бібліотек (>20K треків):

```yaml
# Збільшити batch розмір
year_retrieval:
  processing:
    batch_size: 50
    delay_between_batches: 15

# Збільшити кеш
caching:
  album_cache_max_entries: 100000
```

### Для швидкого інтернету:

```yaml
# Збільшити швидкість API запитів
year_retrieval:
  rate_limits:
    musicbrainz_requests_per_second: 2
    lastfm_requests_per_second: 10
```

## 🔄 Оновлення

1. Зупинити сервіс: `./manage_service.sh stop`
2. Оновити код
3. Перевірити конфігурацію
4. Запустити тест: `./manage_service.sh test`
5. Перезапустити: `./manage_service.sh start`

---

**Статус:** ✅ Готовий до продакшну  
**Тестування:** Пройдено успішно  
**Останнє оновлення:** 2025-08-28
