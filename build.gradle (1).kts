plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

android {
    namespace = "sa.tvmanager.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "sa.tvmanager.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 14
        versionName = "1.4"
        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

chaquopy {
    defaultConfig {
        // لازم يكون Python بنفس الإصدار مثبّت على جهاز البناء (للـ pip فقط)
        version = "3.11"
        // على GitHub يُضبط مسار البايثون تلقائياً، وفي Android Studio يدوّر عليه في PATH
        System.getenv("BUILD_PYTHON")?.let { buildPython(it) }
        pip {
            install("paramiko")
            install("certifi")
            install("adb-shell")
            install("rsa")
            install("pyasn1")
        }
    }
}
