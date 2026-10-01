#!/usr/bin/env bash
# Verify that a release AAB is validly signed by the configured Play upload key.
set -o errexit
set -o nounset
set -o pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <release.aab> <upload.keystore>" >&2
  exit 2
fi

aab=$1
keystore=$2

for command in jarsigner keytool openssl; do
  command -v "$command" >/dev/null || {
    echo "Missing required command: $command" >&2
    exit 2
  }
done

: "${ZITCH_UPLOAD_STORE_PASSWORD:?ZITCH_UPLOAD_STORE_PASSWORD is required}"
: "${ZITCH_UPLOAD_KEY_ALIAS:?ZITCH_UPLOAD_KEY_ALIAS is required}"
test -s "$aab" || { echo "AAB does not exist or is empty: $aab" >&2; exit 1; }
test -s "$keystore" || { echo "Keystore does not exist or is empty: $keystore" >&2; exit 1; }

# Verify the archive's cryptographic signatures. Do not use `-strict` here:
# Android upload keys are normally self-signed, and jarsigner's strict mode treats
# an otherwise valid self-signed certificate chain as a non-zero signer error.
# The certificate extraction and exact fingerprint comparison below make an
# unsigned archive or an archive signed by any other key fail closed.
jarsigner -verify "$aab" >/dev/null

artifact_fingerprint=$(
  keytool -printcert -jarfile "$aab" -rfc \
    | openssl x509 -noout -fingerprint -sha256 \
    | sed 's/^.*=//; s/://g'
)
upload_fingerprint=$(
  keytool -exportcert -rfc \
    -keystore "$keystore" \
    -storepass:env ZITCH_UPLOAD_STORE_PASSWORD \
    -alias "$ZITCH_UPLOAD_KEY_ALIAS" \
    | openssl x509 -noout -fingerprint -sha256 \
    | sed 's/^.*=//; s/://g'
)

if [ -z "$artifact_fingerprint" ] || [ -z "$upload_fingerprint" ]; then
  echo "Could not read both SHA-256 signing-certificate fingerprints" >&2
  exit 1
fi
if [ "$artifact_fingerprint" != "$upload_fingerprint" ]; then
  echo "AAB signer does not match the configured upload-key certificate" >&2
  exit 1
fi

echo "AAB signature is valid and matches the configured upload key."
