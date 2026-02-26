# Image Storage Server (FastAPI)

Статический сервер хранения изображений:
- `POST` и `DELETE` защищены статичным токеном (`Bearer`).
- `GET /images/...` открыт без авторизации.
- При загрузке изображение автоматически конвертируется в `WEBP`.
- После загрузки API делает редирект на публичный URL сохраненного файла.

## Требования

- Python `3.10+`

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Конфигурация

Переменные окружения:

- `STORAGE_API_TOKEN` - токен для `POST/DELETE` (по умолчанию: `change-me-token`)
- `STORAGE_DIR` - путь до постоянного хранилища файлов (по умолчанию: `data/images`)
- `STORAGE_WEBP_QUALITY` - качество WEBP (`0..100`, по умолчанию: `80`)
- `STORAGE_WEBP_METHOD` - метод кодирования WEBP (`0..6`, по умолчанию: `2`, меньше = быстрее)
- `STORAGE_MAX_IMAGE_SIDE` - ограничение максимальной стороны в пикселях (по умолчанию: `0`, без ограничения)
- `STORAGE_MAX_CONVERT_CONCURRENCY` - число одновременных конвертаций (по умолчанию: `cpu_count/2`, минимум `1`)

Пример:

```bash
export STORAGE_API_TOKEN="super-secret-token"
export STORAGE_DIR="./data/images"
export STORAGE_WEBP_QUALITY="80"
export STORAGE_WEBP_METHOD="2"
export STORAGE_MAX_IMAGE_SIDE="0"
export STORAGE_MAX_CONVERT_CONCURRENCY="4"
```

## Запуск

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Документация OpenAPI:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## API

### Загрузка изображения (с авторизацией)

```bash
curl -i -X POST "http://localhost:8000/api/images" \
  -H "Authorization: Bearer super-secret-token" \
  -F "file=@./photo.jpg"
```

Ожидаемый ответ: `303 See Other` и заголовок `Location: /images/<uuid>.webp`

### Удаление изображения (с авторизацией)

```bash
curl -i -X DELETE "http://localhost:8000/api/images/<uuid>.webp" \
  -H "Authorization: Bearer super-secret-token"
```

Ожидаемый ответ: `204 No Content`

### Публичная выдача файла (без авторизации)

```bash
curl -I "http://localhost:8000/images/<uuid>.webp"
```

Ожидаемый ответ: `200 OK` (если файл существует)

## Тесты

```bash
pytest
```
