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

# Supplying the trusted upload keystore makes its self-signed certificate valid
# for this verification. Strict mode is essential: ordinary jarsigner verification
# exits successfully even when unsigned files have been added to a signed archive.
# The explicit alias also rejects entries signed with another certificate.
jarsigner -verify -strict \
  -keystore "$keystore" \
  -storepass:env ZITCH_UPLOAD_STORE_PASSWORD \
  "$aab" "$ZITCH_UPLOAD_KEY_ALIAS" >/dev/null

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
