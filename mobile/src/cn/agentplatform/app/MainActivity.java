package cn.agentplatform.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 智能体 Agent —— Android 客户端（WebView 壳）
 *
 * 设计要点：
 * 1. 全屏沉浸式，无地址栏，打开即进入平台首页，体验接近豆包等 App。
 * 2. 对话 / 办公 / 技能等能力全部由服务端 + 已接入的大模型提供；
 *    API Key 只存在服务端，不下发到手机，避免泄露。
 * 3. 长按底部「服务地址」可修改服务器地址，平台换域名后无需重装。
 * 4. 断网 / 服务不可达时显示友好错误页与「重试」「改地址」，不留白屏。
 * 5. 返回键优先回退网页历史，连续两次才退出，符合移动 App 习惯。
 */
public class MainActivity extends Activity {

    private static final String PREF = "agent_platform_pref";
    private static final String KEY_URL = "server_url";
    private static final String DEFAULT_URL =
            "https://your-domain.example/";  // TODO 改成你自己的平台地址

    private WebView web;
    private ProgressBar progress;
    private TextView addrBar;
    private String currentUrl;
    private long lastBackTime = 0L;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        currentUrl = getSharedPreferences(PREF, MODE_PRIVATE)
                .getString(KEY_URL, DEFAULT_URL);

        // ---------- 布局：顶部细进度条 + 全屏 WebView + 底部极细地址条 ----------
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setVisibility(View.GONE);
        root.addView(progress, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dip(3)));

        web = new WebView(this);
        root.addView(web, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        addrBar = new TextView(this);
        addrBar.setText("服务：" + shortHost(currentUrl) + "  ·  长按可修改");
        addrBar.setTextSize(10f);
        addrBar.setTextColor(Color.parseColor("#98a2b3"));
        addrBar.setGravity(Gravity.CENTER);
        addrBar.setPadding(0, dip(3), 0, dip(3));
        addrBar.setBackgroundColor(Color.parseColor("#fafbfc"));
        addrBar.setOnLongClickListener(new View.OnLongClickListener() {
            @Override public boolean onLongClick(View v) { showUrlDialog(); return true; }
        });
        root.addView(addrBar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        setContentView(root);

        // ---------- WebView 设置 ----------
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setBuiltInZoomControls(false);
        s.setSupportZoom(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setAllowFileAccess(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setUserAgentString(s.getUserAgentString() + " AgentPlatformAndroid/1.0");
        if (Build.VERSION.SDK_INT >= 21) {
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
            CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);
        }
        CookieManager.getInstance().setAcceptCookie(true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                if (url == null) return false;
                // 错误页「修改地址」按钮
                if (url.startsWith("agent://settings")) {
                    showUrlDialog();
                    return true;
                }
                if (url.startsWith("http://") || url.startsWith("https://")) {
                    // 站内一律留在 WebView 内，避免跳出浏览器
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                } catch (Exception ignored) {
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, int errorCode,
                                        String description, String failingUrl) {
                showErrorPage(description);
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                if (newProgress >= 100) {
                    progress.setVisibility(View.GONE);
                } else {
                    progress.setVisibility(View.VISIBLE);
                    progress.setProgress(newProgress);
                }
            }
        });

        // 遇到下载链接（如产物 .md / 代码文件）交给系统下载管理器
        web.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent,
                                        String contentDisposition, String mimeType,
                                        long contentLength) {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                } catch (Exception e) {
                    Toast.makeText(MainActivity.this, "请在浏览器中打开下载链接",
                            Toast.LENGTH_SHORT).show();
                }
            }
        });

        load(currentUrl);
    }

    private void load(String url) {
        currentUrl = url;
        addrBar.setText("服务：" + shortHost(url) + "  ·  长按可修改");
        web.loadUrl(url);
    }

    /** 断网或服务不可达时的友好页面，避免白屏 */
    private void showErrorPage(String reason) {
        String html = "<!doctype html><html><head><meta name='viewport' "
                + "content='width=device-width,initial-scale=1'><style>"
                + "body{margin:0;padding:48px 26px;font-family:-apple-system,sans-serif;"
                + "background:#fff;color:#344054;text-align:center}"
                + "h2{font-size:19px;margin:18px 0 8px}p{font-size:13px;line-height:1.8;"
                + "color:#667085}button{margin:18px 6px 0;padding:11px 22px;border:0;"
                + "border-radius:10px;background:#5925dc;color:#fff;font-size:14px}"
                + "button.sec{background:#f2f4f7;color:#344054}</style></head><body>"
                + "<div style='font-size:44px'>&#128225;</div>"
                + "<h2>暂时连不上服务</h2>"
                + "<p>请检查手机网络，或确认平台服务是否在线。<br>"
                + "如果平台换了地址，长按底部地址栏即可修改，无需重新安装。</p>"
                + "<button onclick=\"location.href='" + escapeJs(currentUrl) + "'\">重新加载</button>"
                + "<button class='sec' onclick=\"window.location.href='agent://settings'\">修改地址</button>"
                + "</body></html>";
        web.loadDataWithBaseURL(currentUrl, html, "text/html", "utf-8", null);
    }

    /** 长按地址栏修改服务器地址 */
    private void showUrlDialog() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(currentUrl);
        input.setSelection(input.getText().length());
        input.setPadding(dip(16), dip(10), dip(16), dip(10));

        new AlertDialog.Builder(this)
                .setTitle("服务地址")
                .setMessage("请输入平台的完整网址（含 http:// 或 https://）。\n"
                        + "平台换域名时改这里即可，不用重装 App。")
                .setView(input)
                .setPositiveButton("保存并打开", new DialogInterface.OnClickListener() {
                    @Override public void onClick(DialogInterface d, int w) {
                        String u = input.getText().toString().trim();
                        if (u.length() == 0) return;
                        if (!u.startsWith("http://") && !u.startsWith("https://")) {
                            u = "https://" + u;
                        }
                        getSharedPreferences(PREF, MODE_PRIVATE)
                                .edit().putString(KEY_URL, u).apply();
                        load(u);
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (web.canGoBack()) {
                web.goBack();
                return true;
            }
            long now = System.currentTimeMillis();
            if (now - lastBackTime < 2000) {
                finish();
            } else {
                lastBackTime = now;
                Toast.makeText(this, "再按一次返回键退出", Toast.LENGTH_SHORT).show();
            }
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.onResume();
    }

    @Override
    protected void onPause() {
        if (web != null) web.onPause();
        super.onPause();
    }

    private int dip(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private static String shortHost(String url) {
        try {
            Uri u = Uri.parse(url);
            return u.getHost() == null ? url : u.getHost();
        } catch (Exception e) {
            return url;
        }
    }

    private static String escapeJs(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "");
    }
}
