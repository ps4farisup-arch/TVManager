package sa.tvmanager.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

public class MainActivity extends Activity {
    private static final int FILE_REQUEST = 7;
    private WebView web;
    private WifiManager.MulticastLock multicastLock;
    private ValueCallback<Uri[]> fileCallback;

    private static final String LOADING =
        "<html dir='rtl'><body style='margin:0;background:#0B1222;color:#F4EFE6;font-family:sans-serif;"
        + "display:flex;align-items:center;justify-content:center;height:100vh'>"
        + "<div style='text-align:center'><h2 style='color:#D9B46A'>مدير الشاشات</h2><p>جارٍ التشغيل…</p></div></body></html>";

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // بدونها أندرويد يتجاهل ردود البحث الجماعي عن الشاشات (SSDP)
        WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
        if (wifi != null) {
            multicastLock = wifi.createMulticastLock("tvmanager");
            multicastLock.setReferenceCounted(false);
            multicastLock.acquire();
        }

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0B1222"));
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                if ("127.0.0.1".equals(u.getHost())) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception ignored) { }
                return true;
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent i = params.createIntent();
                if (params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) {
                    i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                }
                try {
                    startActivityForResult(i, FILE_REQUEST);
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });

        web.loadDataWithBaseURL(null, LOADING, "text/html", "utf-8", null);

        new Thread(() -> {
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                final String url = Python.getInstance().getModule("android_entry").callAttr("start").toString();
                runOnUiThread(() -> web.loadUrl(url));
            } catch (Throwable t) {
                final String msg = String.valueOf(t.getMessage()).replace("<", "&lt;");
                runOnUiThread(() -> web.loadDataWithBaseURL(null,
                    LOADING.replace("جارٍ التشغيل…", "تعذّر التشغيل:<br><small dir='ltr'>" + msg + "</small>"),
                    "text/html", "utf-8", null));
            }
        }).start();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != FILE_REQUEST || fileCallback == null) return;
        Uri[] result = null;
        if (resultCode == RESULT_OK && data != null) {
            ClipData clip = data.getClipData();
            if (clip != null) {
                result = new Uri[clip.getItemCount()];
                for (int i = 0; i < clip.getItemCount(); i++) result[i] = clip.getItemAt(i).getUri();
            } else if (data.getData() != null) {
                result = new Uri[]{data.getData()};
            }
        }
        fileCallback.onReceiveValue(result);
        fileCallback = null;
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (multicastLock != null && multicastLock.isHeld()) multicastLock.release();
        super.onDestroy();
    }
}
