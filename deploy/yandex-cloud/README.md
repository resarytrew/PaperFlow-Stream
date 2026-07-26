# PaperFlow Web в Yandex Cloud

Этот deployment публикует только статические файлы `frontend/dist` в Object
Storage. В bucket не попадают база Hub, изображения работ, классы, ученики,
ответы, OCR, оценки, backup или diagnostics.

## Требования

- Node.js 22;
- AWS CLI;
- Yandex Cloud static access key с минимальными правами на один web-bucket;
- Object Storage bucket;
- для production — CDN и собственный HTTPS-домен.

## Ручной deploy

```bash
export YC_WEB_BUCKET=paperflow-web
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

# Только для первоначальной настройки публичного static bucket:
export YC_MAKE_BUCKET_PUBLIC=true

bash deploy/yandex-cloud/deploy-web.sh
```

После первого deploy верни `YC_MAKE_BUCKET_PUBLIC=false`. Лучше ограничить
service account операциями с конкретным bucket, а не выдавать ему права на весь
каталог облака.

## GitHub Actions

Workflow `.github/workflows/deploy-web-yandex.yml` использует secrets:

```text
YC_WEB_BUCKET
YC_STATIC_ACCESS_KEY
YC_STATIC_SECRET_KEY
```

Он собирает frontend в cloud mode и запускает тот же shell script. Deploy не
требует доступа к PaperFlow Hub и не может прочитать локальные данные учителя.

## Production-настройка

1. Подключить CDN и HTTPS-домен, например `https://app.paperflow.ru`.
2. Установить короткий cache для `index.html`, `sw.js` и manifest.
3. Установить immutable cache для `/assets/*`.
4. Передать точный Origin в installer Hub:

```text
PaperFlowHubSetup-0.3.0.exe /WebUrl=https://app.paperflow.ru
```

5. Не добавлять в облачный frontend ключи Yandex Vision, service-account keys или
   любые API, принимающие ученические payload.

## Privacy boundary

Допустимый cloud control plane описан в `backend/app/cloud/contracts.py` и
принимает только технические метаданные версии/лицензии. Любой будущий cloud API
должен использовать эти строгие DTO или аналогичные модели с `extra="forbid"`.
