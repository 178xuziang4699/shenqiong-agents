#!/usr/bin/env bash
# =============================================================================
# 智能体 Agent · Android APK 一键构建（纯命令行，不依赖 Android Studio / Gradle）
#
# 产物：mobile/AgentPlatform.apk（同时复制到 app/static/download/ 供在线下载）
# 用法：bash mobile/build_apk.sh [服务地址]
#   不带参数使用内置默认地址；带参数可把新地址固化进 App 后重新打包：
#     bash mobile/build_apk.sh "https://your-domain/"
#
# 依赖（Ubuntu/Debian）：
#   apt install -y aapt android-sdk-platform-23 dalvik-exchange zipalign apksigner
#   JDK（javac / keytool / jarsigner）
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

ANDROID_JAR="${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-23/android.jar}"
DX="${DX:-/usr/lib/android-sdk/build-tools/debian/dx}"
AAPT="${AAPT:-aapt}"
ZIPALIGN="${ZIPALIGN:-zipalign}"
APKSIGNER="${APKSIGNER:-apksigner}"

BUILD="$HERE/.build"
OUT="$HERE/out"
PKG="cn.agentplatform.app"
APK_NAME="AgentPlatform.apk"
KEYSTORE="$HERE/agent.keystore"
STOREPASS="${AP_KEYSTORE_STOREPASS:-changeit}"
KEYPASS="${AP_KEYSTORE_KEYPASS:-changeit}"
ALIAS="agentkey"

# 可选：把服务地址固化进 App（重新打包时用）
if [ "${1:-}" != "" ]; then
  python3 - "$1" <<'PY'
import re, sys
from pathlib import Path
url = sys.argv[1].strip()
p = Path("src/cn/agentplatform/app/MainActivity.java")
s = p.read_text(encoding="utf-8")
s = re.sub(r'private static final String DEFAULT_URL =\s*\n?\s*"[^"]*";',
           'private static final String DEFAULT_URL =\n            "%s";' % url.replace('"', '\\"'), s)
p.write_text(s, encoding="utf-8")
print("已把默认服务地址更新为：", url)
PY
fi

echo "==> [0/8] 检查工具链"
for t in "$AAPT" "$ZIPALIGN" "$APKSIGNER" javac keytool; do
  command -v "$t" >/dev/null 2>&1 || { echo "缺少命令：$t"; exit 1; }
done
[ -f "$ANDROID_JAR" ] || { echo "缺少 android.jar：$ANDROID_JAR"; exit 1; }
[ -f "$DX" ] || { echo "缺少 dx：$DX"; exit 1; }

echo "==> [1/8] 生成图标与清理构建目录"
rm -rf "$BUILD" "$OUT"
mkdir -p "$BUILD/gen" "$BUILD/classes" "$BUILD/dex" "$OUT"
python3 make_icon.py

echo "==> [2/8] aapt 打包资源 + 生成 R.java + 未签名 APK"
"$AAPT" package -f -m \
  -J "$BUILD/gen" \
  -M AndroidManifest.xml \
  -S res \
  -I "$ANDROID_JAR" \
  -F "$BUILD/unsigned.apk" \
  --auto-add-overlay

echo "==> [3/8] javac 编译 Java 源码（目标 Java 8 字节码，兼容 dx）"
javac -source 8 -target 8 -encoding UTF-8 -nowarn \
  -bootclasspath "$ANDROID_JAR" \
  -d "$BUILD/classes" \
  $(find "$BUILD/gen" src -name "*.java") 2>&1 | grep -v "bootstrap class path" || true

echo "==> [4/8] dx 生成 classes.dex"
"$DX" --dex --min-sdk-version=21 \
  --output="$BUILD/dex/classes.dex" \
  "$BUILD/classes"

echo "==> [5/8] 把 classes.dex 加入 APK"
(cd "$BUILD/dex" && "$AAPT" add "$BUILD/unsigned.apk" classes.dex >/dev/null)

echo "==> [6/8] zipalign 对齐"
"$ZIPALIGN" -f -p 4 "$BUILD/unsigned.apk" "$BUILD/aligned.apk"

echo "==> [7/8] 生成签名密钥并签名（v1 + v2）"
if [ ! -f "$KEYSTORE" ]; then
  keytool -genkeypair -v \
    -keystore "$KEYSTORE" -alias "$ALIAS" \
    -keyalg RSA -keysize 2048 -validity 10950 \
    -storepass "$STOREPASS" -keypass "$KEYPASS" \
    -dname "CN=Agent Platform, OU=Agent, O=AgentPlatform, L=CN, ST=CN, C=CN" \
    >/dev/null 2>&1
fi
"$APKSIGNER" sign \
  --ks "$KEYSTORE" --ks-key-alias "$ALIAS" \
  --ks-pass "pass:$STOREPASS" --key-pass "pass:$KEYPASS" \
  --v1-signing-enabled true --v2-signing-enabled true \
  --min-sdk-version 21 \
  --out "$OUT/$APK_NAME" "$BUILD/aligned.apk"

echo "==> [8/8] 校验并发布到下载目录"
"$APKSIGNER" verify --verbose "$OUT/$APK_NAME" | head -12
"$AAPT" dump badging "$OUT/$APK_NAME" | grep -E "^package:|application-label|launchable-activity|sdkVersion|targetSdkVersion" | head -8

DOWNLOAD_DIR="$HERE/../app/static/download"
mkdir -p "$DOWNLOAD_DIR"
cp "$OUT/$APK_NAME" "$DOWNLOAD_DIR/$APK_NAME"
SIZE=$(stat -c %s "$OUT/$APK_NAME")
echo ""
echo "✅ 构建完成：$OUT/$APK_NAME"
echo "   大小：$SIZE 字节 ($(echo "scale=1; $SIZE/1024/1024" | bc) MB)"
echo "   下载副本：$DOWNLOAD_DIR/$APK_NAME"
