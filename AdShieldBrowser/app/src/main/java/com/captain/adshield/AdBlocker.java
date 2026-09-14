package com.captain.adshield;

import android.net.Uri;
import android.webkit.WebResourceResponse;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

final class AdBlocker {

    private static final Set<String> BLOCKED_HOSTS = new HashSet<>(Arrays.asList(
            "2mdn.net",
            "adcolony.com",
            "adform.net",
            "adnxs.com",
            "adroll.com",
            "adsafeprotected.com",
            "adsrvr.org",
            "adservice.google.com",
            "amazon-adsystem.com",
            "app-measurement.com",
            "appsflyer.com",
            "bidswitch.net",
            "casalemedia.com",
            "chartbeat.com",
            "chartboost.com",
            "criteo.com",
            "criteo.net",
            "demdex.net",
            "doubleclick.net",
            "doubleverify.com",
            "flashtalking.com",
            "flurry.com",
            "google-analytics.com",
            "googleadservices.com",
            "googlesyndication.com",
            "googletagservices.com",
            "hotjar.com",
            "imrworldwide.com",
            "indexww.com",
            "lijit.com",
            "mathtag.com",
            "media.net",
            "moatads.com",
            "mookie1.com",
            "openx.net",
            "outbrain.com",
            "pubmatic.com",
            "quantserve.com",
            "rubiconproject.com",
            "scorecardresearch.com",
            "serving-sys.com",
            "sharethrough.com",
            "smartadserver.com",
            "smaato.net",
            "taboola.com",
            "tapjoy.com",
            "teads.tv",
            "unityads.unity3d.com",
            "yieldmo.com",
            "zedo.com",
            "adfit.co.kr",
            "ad.daum.net",
            "adcr.naver.com",
            "gfp.veta.naver.com",
            "siape.veta.naver.com"
    ));

    private static final String[] BLOCKED_URL_PARTS = {
            "/pagead/",
            "/pagead2/",
            "/adsystem/",
            "/adserver/",
            "/adview?",
            "/adclick?",
            "/api/stats/ads",
            "/ptracking",
            "facebook.com/tr?",
            "youtube.com/pagead/"
    };

    boolean shouldBlock(Uri uri) {
        if (uri == null) return false;
        String scheme = uri.getScheme();
        if (scheme == null || !(scheme.equalsIgnoreCase("https") || scheme.equalsIgnoreCase("http"))) {
            return false;
        }

        String host = uri.getHost();
        if (host != null) {
            host = host.toLowerCase(Locale.ROOT);
            for (String blocked : BLOCKED_HOSTS) {
                if (host.equals(blocked) || host.endsWith("." + blocked)) {
                    return true;
                }
            }
        }

        String full = uri.toString().toLowerCase(Locale.ROOT);
        for (String part : BLOCKED_URL_PARTS) {
            if (full.contains(part)) return true;
        }
        return false;
    }

    WebResourceResponse blockedResponse() {
        return new WebResourceResponse(
                "text/plain",
                StandardCharsets.UTF_8.name(),
                204,
                "No Content",
                Collections.emptyMap(),
                new ByteArrayInputStream(new byte[0])
        );
    }
}
