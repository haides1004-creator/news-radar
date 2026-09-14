package com.captain.adshield;

import android.app.Activity;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Build;
import android.view.Gravity;
import android.view.KeyEvent;
import android.webkit.CookieManager;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.ServiceWorkerClient;
import android.webkit.ServiceWorkerController;
import android.webkit.ServiceWorkerWebSettings;
import android.webkit.SslErrorHandler;
import android.net.http.SslError;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;
import android.view.inputmethod.EditorInfo;

import java.util.concurrent.atomic.AtomicInteger;

public final class MainActivity extends Activity {

    private final AdBlocker adBlocker = new AdBlocker();
    private final AtomicInteger blockedCount = new AtomicInteger(0);
    private volatile boolean blockerEnabled = true;

    private WebView webView;
    private EditText addressBar;
    private TextView statusText;
    private LinearLayout root;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        createAndAttachWebView();
        configureServiceWorkerFiltering();
        loadSecure("https://www.google.com/");
    }

    private void buildUi() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);

        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setOrientation(LinearLayout.HORIZONTAL);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(4), dp(4), dp(4), dp(4));

        Button back = smallButton("‹");
        Button forward = smallButton("›");
        Button reload = smallButton("↻");

        addressBar = new EditText(this);
        addressBar.setSingleLine(true);
        addressBar.setTextSize(14f);
        addressBar.setHint("주소 또는 검색어");
        addressBar.setImeOptions(EditorInfo.IME_ACTION_GO);
        LinearLayout.LayoutParams addressParams = new LinearLayout.LayoutParams(0, dp(46), 1f);
        addressParams.setMargins(dp(4), 0, dp(4), 0);
        addressBar.setLayoutParams(addressParams);

        Switch blockerSwitch = new Switch(this);
        blockerSwitch.setText("차단");
        blockerSwitch.setChecked(true);
        blockerSwitch.setOnCheckedChangeListener((buttonView, checked) -> {
            blockerEnabled = checked;
            blockedCount.set(0);
            updateStatus();
            if (webView != null) webView.reload();
        });

        toolbar.addView(back);
        toolbar.addView(forward);
        toolbar.addView(reload);
        toolbar.addView(addressBar);
        toolbar.addView(blockerSwitch);

        statusText = new TextView(this);
        statusText.setTextSize(12f);
        statusText.setPadding(dp(10), dp(3), dp(10), dp(3));
        statusText.setText("광고 차단 켜짐 · 차단 0개");

        root.addView(toolbar);
        root.addView(statusText, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));
        setContentView(root);

        back.setOnClickListener(v -> {
            if (webView != null && webView.canGoBack()) webView.goBack();
        });
        forward.setOnClickListener(v -> {
            if (webView != null && webView.canGoForward()) webView.goForward();
        });
        reload.setOnClickListener(v -> {
            if (webView != null) webView.reload();
        });
        addressBar.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_GO ||
                    (event != null && event.getKeyCode() == KeyEvent.KEYCODE_ENTER)) {
                navigateFromAddressBar();
                return true;
            }
            return false;
        });
    }

    private Button smallButton(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(20f);
        button.setAllCaps(false);
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setPadding(dp(8), 0, dp(8), 0);
        button.setLayoutParams(new LinearLayout.LayoutParams(dp(44), dp(46)));
        return button;
    }

    private void createAndAttachWebView() {
        webView = new WebView(this);
        LinearLayout.LayoutParams webParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1f
        );
        root.addView(webView, webParams);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setGeolocationEnabled(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSafeBrowsingEnabled(true);
        settings.setSupportMultipleWindows(false);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        settings.setMediaPlaybackRequiresUserGesture(true);

        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(webView, false);

        WebView.setWebContentsDebuggingEnabled(false);
        webView.setWebViewClient(new ShieldWebViewClient());
    }

    private void configureServiceWorkerFiltering() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            ServiceWorkerController controller = ServiceWorkerController.getInstance();
            ServiceWorkerWebSettings settings = controller.getServiceWorkerWebSettings();
            settings.setAllowFileAccess(false);
            settings.setAllowContentAccess(false);
            controller.setServiceWorkerClient(new ServiceWorkerClient() {
                @Override
                public WebResourceResponse shouldInterceptRequest(WebResourceRequest request) {
                    if (blockerEnabled && adBlocker.shouldBlock(request.getUrl())) {
                        blockedCount.incrementAndGet();
                        return adBlocker.blockedResponse();
                    }
                    return null;
                }
            });
        }
    }

    private void navigateFromAddressBar() {
        String input = addressBar.getText().toString().trim();
        if (input.isEmpty()) return;

        String target;
        if (input.startsWith("https://")) {
            target = input;
        } else if (input.startsWith("http://")) {
            Toast.makeText(this, "보안을 위해 HTTPS 주소만 허용합니다.", Toast.LENGTH_SHORT).show();
            return;
        } else if (input.contains("://")) {
            Toast.makeText(this, "지원하지 않는 주소 형식입니다.", Toast.LENGTH_SHORT).show();
            return;
        } else if (!input.contains(" ") && input.contains(".")) {
            target = "https://" + input;
        } else {
            target = "https://www.google.com/search?q=" + Uri.encode(input);
        }
        loadSecure(target);
    }

    private void loadSecure(String url) {
        Uri uri = Uri.parse(url);
        if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null) {
            Toast.makeText(this, "HTTPS 웹페이지만 열 수 있습니다.", Toast.LENGTH_SHORT).show();
            return;
        }
        blockedCount.set(0);
        updateStatus();
        webView.loadUrl(url);
    }

    private void updateStatus() {
        String state = blockerEnabled ? "광고 차단 켜짐" : "광고 차단 꺼짐";
        statusText.setText(state + " · 차단 " + blockedCount.get() + "개");
    }

    private void injectCleanupScript(String pageUrl) {
        if (!blockerEnabled || pageUrl == null) return;
        Uri uri = Uri.parse(pageUrl);
        String host = uri.getHost();
        if (host == null) return;

        String script =
                "(function(){" +
                "if(window.__adShieldTimer){clearInterval(window.__adShieldTimer);}" +
                "function clean(){" +
                "var s=['ins.adsbygoogle','[id^=\\\"google_ads_\\\"]','[id^=\\\"div-gpt-ad\\\"]','.ad-container','.advertisement','.video-ads','.ytp-ad-module','.ytp-ad-overlay-container','ytd-display-ad-renderer','ytd-promoted-sparkles-web-renderer','ytd-ad-slot-renderer','ytm-promoted-sparkles-web-renderer'];" +
                "s.forEach(function(q){document.querySelectorAll(q).forEach(function(e){e.style.setProperty('display','none','important');});});" +
                "if(location.hostname==='youtube.com'||location.hostname.endsWith('.youtube.com')){" +
                "document.querySelectorAll('.ytp-ad-skip-button,.ytp-skip-ad-button,.ytp-ad-skip-button-modern').forEach(function(b){try{b.click();}catch(e){}});" +
                "var p=document.querySelector('.html5-video-player.ad-showing');" +
                "if(p){var v=p.querySelector('video');if(v&&isFinite(v.duration)&&v.duration>0){try{v.currentTime=v.duration;}catch(e){}}}" +
                "}" +
                "}" +
                "clean();window.__adShieldTimer=setInterval(clean,900);" +
                "})();";
        webView.evaluateJavascript(script, null);
    }

    private final class ShieldWebViewClient extends WebViewClient {
        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            if (blockerEnabled && adBlocker.shouldBlock(request.getUrl())) {
                blockedCount.incrementAndGet();
                return adBlocker.blockedResponse();
            }
            return null;
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            String scheme = uri.getScheme();
            if ("https".equalsIgnoreCase(scheme)) return false;

            runOnUiThread(() -> Toast.makeText(
                    MainActivity.this,
                    "보안을 위해 HTTPS 외 링크는 차단했습니다.",
                    Toast.LENGTH_SHORT
            ).show());
            return true;
        }

        @Override
        public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
            super.onPageStarted(view, url, favicon);
            blockedCount.set(0);
            addressBar.setText(url);
            updateStatus();
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            super.onPageFinished(view, url);
            addressBar.setText(url);
            updateStatus();
            injectCleanupScript(url);
        }

        @Override
        public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            handler.cancel();
            Toast.makeText(MainActivity.this, "인증서 오류가 있어 연결을 차단했습니다.", Toast.LENGTH_LONG).show();
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            super.onReceivedError(view, request, error);
            if (request.isForMainFrame()) {
                runOnUiThread(() -> statusText.setText("페이지를 열지 못했습니다."));
            }
        }

        @Override
        public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
            runOnUiThread(() -> {
                Toast.makeText(MainActivity.this, "웹 엔진을 안전하게 다시 시작합니다.", Toast.LENGTH_SHORT).show();
                recreate();
            });
            return true;
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.setWebViewClient(null);
            webView.loadUrl("about:blank");
            webView.clearHistory();
            webView.removeAllViews();
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
