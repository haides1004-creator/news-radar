from pathlib import Path
import re, sys

root = Path(__file__).resolve().parents[1]
manifest = (root / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
java = "\n".join(p.read_text(encoding="utf-8") for p in (root / "app/src/main/java").rglob("*.java"))

allowed_permissions = {"android.permission.INTERNET"}
permissions = set(re.findall(r'uses-permission android:name="([^"]+)"', manifest))
extra = permissions - allowed_permissions
checks = {
    "Only INTERNET permission": not extra and permissions == allowed_permissions,
    "No JavaScript native bridge": "addJavaScriptInterface" not in java,
    "WebView debugging disabled": "setWebContentsDebuggingEnabled(false)" in java,
    "File access disabled": "setAllowFileAccess(false)" in java,
    "Content access disabled": "setAllowContentAccess(false)" in java,
    "Cleartext traffic disabled": 'android:usesCleartextTraffic="false"' in manifest,
    "SSL errors are cancelled": "handler.cancel();" in java,
}

failed = False
for name, ok in checks.items():
    print(("PASS" if ok else "FAIL") + " - " + name)
    failed |= not ok

if extra:
    print("Unexpected permissions:", sorted(extra))

sys.exit(1 if failed else 0)
