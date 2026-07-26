#!/usr/bin/env bash
# Build and publish only the static Чистовик shell to Yandex Object Storage.
# Student data and the local Hub are never part of this deployment.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENDPOINT="${YC_STORAGE_ENDPOINT:-https://storage.yandexcloud.net}"
: "${YC_WEB_BUCKET:?Set YC_WEB_BUCKET to the Object Storage bucket name}"
: "${AWS_ACCESS_KEY_ID:?Set AWS_ACCESS_KEY_ID to a Yandex Cloud static access key}"
: "${AWS_SECRET_ACCESS_KEY:?Set AWS_SECRET_ACCESS_KEY to the matching secret key}"

export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-ru-central1}"
export VITE_PAPERFLOW_UI_MODE=cloud
export VITE_PAPERFLOW_HUB_URLS="${VITE_PAPERFLOW_HUB_URLS:-https://127.0.0.1:17841,https://localhost:17841,http://127.0.0.1:17841,http://localhost:17841}"
export VITE_PAPERFLOW_ALLOWED_HUB_HOSTS="${VITE_PAPERFLOW_ALLOWED_HUB_HOSTS:-}"

cd "${ROOT}/frontend"
npm ci
npm run build

AWS=(aws --endpoint-url "${ENDPOINT}")

"${AWS[@]}" s3api put-bucket-website \
  --bucket "${YC_WEB_BUCKET}" \
  --website-configuration '{"IndexDocument":{"Suffix":"index.html"},"ErrorDocument":{"Key":"index.html"}}'

if [[ "${YC_MAKE_BUCKET_PUBLIC:-false}" == "true" ]]; then
  policy="$(mktemp)"
  trap 'rm -f "${policy}"' EXIT
  cat >"${policy}" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PaperFlowPublicWebAssets",
      "Effect": "Allow",
      "Principal": "*",
      "Action": ["s3:GetObject"],
      "Resource": ["arn:aws:s3:::${YC_WEB_BUCKET}/*"]
    }
  ]
}
JSON
  "${AWS[@]}" s3api put-bucket-policy --bucket "${YC_WEB_BUCKET}" --policy "file://${policy}"
fi

# Hashed Vite assets can be cached indefinitely. Shell/manifest/service worker
# remain short-lived so a security update reaches teachers immediately.
"${AWS[@]}" s3 sync dist "s3://${YC_WEB_BUCKET}" \
  --delete \
  --exclude "index.html" \
  --exclude "sw.js" \
  --exclude "manifest.webmanifest" \
  --exclude "chistovik-icon.svg" \
  --cache-control "public,max-age=31536000,immutable"

NO_CACHE="no-cache,no-store,must-revalidate"
"${AWS[@]}" s3 cp dist/index.html "s3://${YC_WEB_BUCKET}/index.html" \
  --content-type "text/html; charset=utf-8" --cache-control "${NO_CACHE}"
"${AWS[@]}" s3 cp dist/sw.js "s3://${YC_WEB_BUCKET}/sw.js" \
  --content-type "application/javascript; charset=utf-8" --cache-control "${NO_CACHE}"
"${AWS[@]}" s3 cp dist/manifest.webmanifest "s3://${YC_WEB_BUCKET}/manifest.webmanifest" \
  --content-type "application/manifest+json; charset=utf-8" --cache-control "${NO_CACHE}"
"${AWS[@]}" s3 cp dist/chistovik-icon.svg "s3://${YC_WEB_BUCKET}/chistovik-icon.svg" \
  --content-type "image/svg+xml" --cache-control "${NO_CACHE}"

cat <<EOF
Чистовик uploaded to bucket: ${YC_WEB_BUCKET}
Only static HTML/CSS/JS/PWA assets were published.
Configure a Yandex Cloud CDN/custom HTTPS domain in front of the bucket before production use.
EOF
