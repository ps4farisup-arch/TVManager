# -*- coding: utf-8 -*-
"""نقطة تشغيل البرنامج داخل تطبيق الأندرويد."""
import os


def start():
    os.environ["TVM_ANDROID"] = "1"
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except Exception:
        pass
    import tv_manager
    return tv_manager.start_background()
