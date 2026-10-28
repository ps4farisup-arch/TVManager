# -*- coding: utf-8 -*-
"""
«شاشتي» — واجهة لشاشات سامسونج (Tizen) و LG (webOS).
ساعة، تاريخ هجري وميلادي، مواقيت الصلاة لكل الدول الإسلامية بطريقة حساب كل دولة،
الطقس لثلاثة أيام، وآية اليوم، فوق مناظر سعودية مرسومة تتلوّن مع أوقات الصلاة.

هذا الملف يبني التطبيق ويغلّفه:
  - LG: ملف .ipk جاهز للتثبيت (بدون أي أدوات خارجية)
  - Samsung: ملفات .wgt تحتاج توقيعها بشهادة سامسونج عبر Tizen Studio
"""

import base64
import io
import json
import os
import struct
import tarfile
import time
import zipfile
import zlib

APP_VERSION = "1.0.0"
LG_APP_ID = "sa.shashati.app"
TIZEN_PKG_ID = "ShAshati01"          # لازم 10 حروف/أرقام
TIZEN_APP_ID = TIZEN_PKG_ID + ".Shashati"

import tv_scenes

# [الدولة، طريقة الحساب في Aladhan، المذهب للعصر (0 جمهور، 1 حنفي)، المدن]
COUNTRIES = [
    ["السعودية", 4, 0, [
        ["الرياض", 24.7136, 46.6753], ["جدة", 21.5433, 39.1728], ["مكة المكرمة", 21.3891, 39.8579],
        ["المدينة المنورة", 24.5247, 39.5692], ["الدمام", 26.4207, 50.0888], ["الخبر", 26.2172, 50.1971],
        ["الأحساء", 25.3833, 49.5833], ["الجبيل", 27.0174, 49.6225], ["الطائف", 21.2703, 40.4158],
        ["تبوك", 28.3835, 36.5662], ["بريدة", 26.3260, 43.9750], ["عنيزة", 26.0840, 43.9930],
        ["حائل", 27.5114, 41.6900], ["أبها", 18.2164, 42.5053], ["خميس مشيط", 18.3000, 42.7333],
        ["جازان", 16.8892, 42.5511], ["نجران", 17.4924, 44.1277], ["الباحة", 20.0129, 41.4677],
        ["ينبع", 24.0895, 38.0618], ["عرعر", 30.9753, 41.0381], ["سكاكا", 29.9697, 40.2064],
        ["القريات", 31.3318, 37.3428], ["حفر الباطن", 28.4328, 45.9708], ["الخرج", 24.1556, 47.3120]]],
    ["الإمارات", 16, 0, [["أبوظبي", 24.4539, 54.3773], ["دبي", 25.2048, 55.2708], ["الشارقة", 25.3463, 55.4209],
                         ["العين", 24.2075, 55.7447], ["رأس الخيمة", 25.8007, 55.9762], ["الفجيرة", 25.1288, 56.3265]]],
    ["الكويت", 9, 0, [["مدينة الكويت", 29.3759, 47.9774], ["الجهراء", 29.3375, 47.6581], ["الأحمدي", 29.0769, 48.0838]]],
    ["قطر", 10, 0, [["الدوحة", 25.2854, 51.5310], ["الوكرة", 25.1659, 51.5976], ["الخور", 25.6804, 51.4969]]],
    ["البحرين", 8, 0, [["المنامة", 26.2285, 50.5860], ["المحرق", 26.2572, 50.6119]]],
    ["عُمان", 8, 0, [["مسقط", 23.5880, 58.3829], ["صلالة", 17.0151, 54.0924], ["صحار", 24.3474, 56.7090], ["نزوى", 22.9333, 57.5333]]],
    ["اليمن", 3, 0, [["صنعاء", 15.3694, 44.1910], ["عدن", 12.7855, 45.0187], ["تعز", 13.5795, 44.0209], ["المكلا", 14.5425, 49.1242]]],
    ["الأردن", 23, 0, [["عمّان", 31.9454, 35.9284], ["الزرقاء", 32.0728, 36.0880], ["إربد", 32.5556, 35.8500], ["العقبة", 29.5321, 35.0063]]],
    ["فلسطين", 5, 0, [["القدس", 31.7683, 35.2137], ["غزة", 31.5017, 34.4668], ["نابلس", 32.2211, 35.2544],
                      ["الخليل", 31.5326, 35.0998], ["رام الله", 31.9038, 35.2034]]],
    ["لبنان", 5, 0, [["بيروت", 33.8938, 35.5018], ["طرابلس", 34.4367, 35.8497], ["صيدا", 33.5571, 35.3729]]],
    ["سوريا", 3, 0, [["دمشق", 33.5138, 36.2765], ["حلب", 36.2021, 37.1343], ["حمص", 34.7324, 36.7137], ["اللاذقية", 35.5317, 35.7900]]],
    ["العراق", 3, 0, [["بغداد", 33.3152, 44.3661], ["البصرة", 30.5085, 47.7804], ["الموصل", 36.3350, 43.1189],
                     ["أربيل", 36.1911, 44.0092], ["النجف", 32.0259, 44.3462]]],
    ["مصر", 5, 0, [["القاهرة", 30.0444, 31.2357], ["الإسكندرية", 31.2001, 29.9187], ["الجيزة", 30.0131, 31.2089],
                   ["المنصورة", 31.0409, 31.3785], ["أسوان", 24.0889, 32.8998]]],
    ["السودان", 5, 0, [["الخرطوم", 15.5007, 32.5599], ["أم درمان", 15.6445, 32.4777], ["بورتسودان", 19.6158, 37.2164]]],
    ["ليبيا", 5, 0, [["طرابلس", 32.8872, 13.1913], ["بنغازي", 32.1167, 20.0667], ["مصراتة", 32.3754, 15.0925]]],
    ["تونس", 18, 0, [["تونس", 36.8065, 10.1815], ["صفاقس", 34.7406, 10.7603], ["سوسة", 35.8256, 10.6360]]],
    ["الجزائر", 19, 0, [["الجزائر", 36.7538, 3.0588], ["وهران", 35.6971, -0.6308], ["قسنطينة", 36.3650, 6.6147]]],
    ["المغرب", 21, 0, [["الرباط", 34.0209, -6.8416], ["الدار البيضاء", 33.5731, -7.5898], ["مراكش", 31.6295, -7.9811],
                       ["فاس", 34.0181, -5.0078], ["طنجة", 35.7595, -5.8340]]],
    ["موريتانيا", 3, 0, [["نواكشوط", 18.0735, -15.9582], ["نواذيبو", 20.9310, -17.0347]]],
    ["الصومال", 3, 0, [["مقديشو", 2.0469, 45.3182], ["هرجيسا", 9.5600, 44.0650]]],
    ["جيبوتي", 3, 0, [["جيبوتي", 11.5721, 43.1456]]],
    ["جزر القمر", 3, 0, [["موروني", -11.7172, 43.2473]]],
    ["تركيا", 13, 0, [["إسطنبول", 41.0082, 28.9784], ["أنقرة", 39.9334, 32.8597], ["إزمير", 38.4237, 27.1428], ["بورصة", 40.1885, 29.0610]]],
    ["إيران", 7, 0, [["طهران", 35.6892, 51.3890], ["مشهد", 36.2605, 59.6168], ["أصفهان", 32.6546, 51.6680], ["تبريز", 38.0800, 46.2919]]],
    ["باكستان", 1, 1, [["إسلام آباد", 33.6844, 73.0479], ["كراتشي", 24.8607, 67.0011], ["لاهور", 31.5204, 74.3587], ["بيشاور", 34.0151, 71.5249]]],
    ["أفغانستان", 1, 1, [["كابل", 34.5553, 69.2075], ["قندهار", 31.6289, 65.7372], ["هرات", 34.3529, 62.2040]]],
    ["بنغلاديش", 1, 1, [["دكا", 23.8103, 90.4125], ["شيتاغونغ", 22.3569, 91.7832]]],
    ["ماليزيا", 17, 0, [["كوالالمبور", 3.1390, 101.6869], ["جورج تاون", 5.4141, 100.3288], ["جوهور بهرو", 1.4927, 103.7414]]],
    ["إندونيسيا", 20, 0, [["جاكرتا", -6.2088, 106.8456], ["سورابايا", -7.2575, 112.7521], ["باندونغ", -6.9175, 107.6191],
                          ["ميدان", 3.5952, 98.6722], ["باندا آتشيه", 5.5483, 95.3238]]],
    ["بروناي", 11, 0, [["بندر سري بكاوان", 4.9031, 114.9398]]],
    ["المالديف", 3, 0, [["ماليه", 4.1755, 73.5093]]],
    ["أذربيجان", 3, 0, [["باكو", 40.4093, 49.8671]]],
    ["كازاخستان", 3, 1, [["أستانا", 51.1694, 71.4491], ["ألماتي", 43.2220, 76.8512]]],
    ["أوزبكستان", 3, 1, [["طشقند", 41.2995, 69.2401], ["سمرقند", 39.6542, 66.9597], ["بخارى", 39.7747, 64.4286]]],
    ["تركمانستان", 3, 1, [["عشق آباد", 37.9601, 58.3261]]],
    ["قيرغيزستان", 3, 1, [["بيشكيك", 42.8746, 74.5698]]],
    ["طاجيكستان", 3, 1, [["دوشنبه", 38.5598, 68.7870]]],
    ["ألبانيا", 3, 0, [["تيرانا", 41.3275, 19.8187]]],
    ["نيجيريا", 3, 0, [["أبوجا", 9.0765, 7.3986], ["لاغوس", 6.5244, 3.3792], ["كانو", 12.0022, 8.5920]]],
    ["السنغال", 3, 0, [["داكار", 14.7167, -17.4677], ["طوبى", 14.8667, -15.8833]]],
    ["مالي", 3, 0, [["باماكو", 12.6392, -8.0029], ["تمبكتو", 16.7666, -3.0026]]],
    ["النيجر", 3, 0, [["نيامي", 13.5116, 2.1254]]],
    ["تشاد", 3, 0, [["نجامينا", 12.1348, 15.0557]]],
    ["بوركينا فاسو", 3, 0, [["واغادوغو", 12.3714, -1.5197]]],
    ["غينيا", 3, 0, [["كوناكري", 9.6412, -13.5784]]],
    ["غامبيا", 3, 0, [["بانجول", 13.4549, -16.5790]]],
    ["غينيا بيساو", 3, 0, [["بيساو", 11.8817, -15.6177]]],
    ["سيراليون", 3, 0, [["فريتاون", 8.4657, -13.2317]]],
    ["ساحل العاج", 3, 0, [["أبيدجان", 5.3600, -4.0083]]],
    ["الكاميرون", 3, 0, [["ياوندي", 3.8480, 11.5021]]],
    ["الغابون", 3, 0, [["ليبرفيل", 0.4162, 9.4673]]],
    ["بنين", 3, 0, [["كوتونو", 6.3703, 2.3912]]],
    ["توغو", 3, 0, [["لومي", 6.1725, 1.2314]]],
    ["أوغندا", 3, 0, [["كمبالا", 0.3476, 32.5825]]],
    ["موزمبيق", 3, 0, [["مابوتو", -25.9692, 32.5732]]],
    ["سورينام", 3, 0, [["باراماريبو", 5.8520, -55.2038]]],
    ["غيانا", 3, 0, [["جورج تاون", 6.8013, -58.1551]]],
]

PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>شاشتي</title>
<style>
__FONTS__
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#05070f;color:#f4efe6;
  font-family:"ShUI","IBM Plex Sans Arabic","LG Smart UI","Samsung Sans Arabic","Noto Sans Arabic",Tahoma,sans-serif;
  -webkit-font-smoothing:antialiased}
.layer,#sky,#stars,#scrim{position:fixed;top:0;left:0;right:0;bottom:0}
#sky{-webkit-transition:background 6s;transition:background 6s}
.layer{opacity:0;-webkit-transition:opacity 2.5s;transition:opacity 2.5s;background-size:cover;background-position:center}
.layer.on{opacity:1}
.layer svg,#stars svg{width:100%;height:100%;display:block}
#stars{-webkit-transition:opacity 4s;transition:opacity 4s}
.f1,.f2,.f3,.sea,.lt,.st,.rf,.mr,.gl{-webkit-transition:fill 6s,opacity 6s;transition:fill 6s,opacity 6s}
#scrim{background:linear-gradient(to bottom,rgba(4,5,10,.62) 0%,rgba(4,5,10,.18) 26%,rgba(4,5,10,.08) 50%,rgba(4,5,10,.35) 70%,rgba(4,5,10,.82) 100%)}
.gold{color:#d9b46a}
.muted{color:rgba(244,239,230,.68)}
#ui{position:absolute;top:4.6vh;bottom:4.2vh;left:4vw;right:4vw;-webkit-transition:-webkit-transform 3s;transition:transform 3s;
  text-shadow:0 .1vw .8vw rgba(0,0,0,.45)}

/* أعلى الشاشة */
.top{position:absolute;top:0;left:0;right:0;height:16vh}
.date{position:absolute;right:0;top:0;min-width:30vw;text-align:right;padding-bottom:.9vw;border-bottom:1px solid rgba(217,180,106,.55)}
.date .h{font-size:2.15vw;font-weight:600;line-height:1.25}
.date .g{font-size:1.25vw;margin-top:.35vw}
.wx{position:absolute;left:0;top:0;min-width:20vw;text-align:left;padding-bottom:.9vw;border-bottom:1px solid rgba(217,180,106,.55)}
.wx .now{font-size:2.15vw;font-weight:600;line-height:1.25;direction:rtl;text-align:left}
.wx .now b{font-weight:600;margin-left:.8vw}
.wx .sub{font-size:1.25vw;margin-top:.35vw;text-align:left}
.days{margin-top:.8vw;font-size:1.15vw;text-align:left}
.days span{margin-right:1.6vw}

/* الوسط */
.clockbox{position:absolute;right:0;top:27vh;text-align:right}
#clock{display:inline-block;direction:ltr;font-size:12.5vw;font-weight:600;line-height:1;letter-spacing:-.01em;white-space:nowrap}
#clock i{font-style:normal;color:#d9b46a;margin:0 .3vw}
#ampm{display:inline-block;font-size:2.6vw;font-weight:600;color:#d9b46a;vertical-align:top;margin-right:.9vw;position:relative;top:1.4vw}
#tz{font-size:1.25vw;margin-top:.6vw}
#tz:before{content:"";display:inline-block;width:.5vw;height:.5vw;border-radius:50%;background:#d9b46a;margin-left:.6vw;vertical-align:middle}
.card{position:absolute;left:0;top:33vh;width:44vw;background:rgba(14,12,9,.55);border:1px solid rgba(255,255,255,.1);border-radius:1vw;padding:1.3vw 1.7vw 1.2vw;text-shadow:none}
.card .lbl{font-size:1.15vw;color:#d9b46a;padding-bottom:.6vw;border-bottom:1px solid rgba(217,180,106,.4);margin-bottom:.9vw}
#verse{font-family:"ShQ","Amiri","Noto Naskh Arabic","Traditional Arabic",serif;font-size:2.05vw;line-height:1.85}
#verseSrc{font-size:1.15vw;color:#d9b46a;margin-top:.6vw}

/* أسفل الشاشة */
.bottom{position:absolute;left:0;right:0;bottom:0;border-top:1px solid rgba(217,180,106,.55);padding-top:1.5vw;height:15vh}
.next{position:absolute;right:0;top:1.5vw;text-align:right}
.next .lbl{font-size:1.15vw;color:#d9b46a}
.next .nm{font-size:2.6vw;font-weight:600;line-height:1.3}
.next .left{font-size:1.2vw}
.times{position:absolute;left:50%;top:1.9vw;white-space:nowrap;-webkit-transform:translateX(-50%);transform:translateX(-50%)}
.t{display:inline-block;margin:0 1vw;text-align:center;opacity:.82;vertical-align:top}
.t span{display:block;font-size:1.05vw;color:rgba(244,239,230,.7)}
.t b{display:block;font-size:1.65vw;font-weight:500;margin-top:.15vw}
.t.on{opacity:1}
.t.on span,.t.on b{color:#d9b46a}
.place{position:absolute;left:0;top:1.5vw;text-align:left}
.place .lbl{font-size:1.15vw;color:#d9b46a;direction:rtl}
.place .nm{font-size:1.75vw;font-weight:600;line-height:1.4;direction:rtl}
.place .nav{font-size:1.05vw;direction:rtl}

#hint{position:fixed;top:1.4vh;left:0;right:0;text-align:center;font-size:1vw;opacity:.7;-webkit-transition:opacity 2s;transition:opacity 2s}
#picker{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(5,6,10,.9)}
#picker h2{font-size:2.2vw;font-weight:600;text-align:center;margin:6vh 0 2.5vh;color:#d9b46a}
#list{width:32vw;height:70vh;margin:0 auto;overflow:hidden;position:relative}
#list div{font-size:1.8vw;padding:.65vw 1.4vw;border-radius:.7vw;border:.15vw solid transparent}
#list div.sel{border-color:#d9b46a;color:#d9b46a;background:rgba(217,180,106,.08)}
</style>
</head>
<body>
<style id="pal"></style>
<div id="sky"></div><div id="stars"></div>
<div class="layer" id="lA"></div><div class="layer" id="lB"></div>
<div id="scrim"></div>
<div id="ui">
  <div class="top">
    <div class="date"><div class="h" id="hijri">&nbsp;</div><div class="g muted" id="greg"></div></div>
    <div class="wx"><div class="now" id="wxNow">&nbsp;</div><div class="sub muted" id="wxSub"></div><div class="days" id="wxDays"></div></div>
  </div>
  <div class="clockbox"><div dir="ltr" style="text-align:right"><span id="ampm"></span><span id="clock"></span></div><div id="tz" class="muted"></div></div>
  <div class="card" id="card" style="display:none"><div class="lbl">آية اليوم</div><div id="verse"></div><div id="verseSrc"></div></div>
  <div class="bottom">
    <div class="next"><div class="lbl">الصلاة القادمة</div><div class="nm" id="nextName">&nbsp;</div><div class="left muted" id="nextLeft"></div></div>
    <div class="times" id="times"></div>
    <div class="place"><div class="lbl" id="placeTag"></div><div class="nm" id="placeName"></div><div class="nav muted">◀ ▶ لتغيير الخلفية</div></div>
  </div>
</div>
<video id="keep" muted loop playsinline autoplay style="position:fixed;left:0;bottom:0;width:2px;height:2px;opacity:.01;pointer-events:none"></video>
<div id="hint">اضغط OK لتغيير الدولة والمدينة</div>
<div id="picker"><h2 id="pickTitle">اختر دولتك</h2><div id="list"></div></div>
<script src="$WEBAPIS/webapis/webapis.js"></script>
<script>
var CFG = __CONFIG__;
var ORDER = ['Fajr','Sunrise','Dhuhr','Asr','Maghrib','Isha'];
var NAMES = {Fajr:'الفجر',Sunrise:'الشروق',Dhuhr:'الظهر',Asr:'العصر',Maghrib:'المغرب',Isha:'العشاء'};
var DAYS = ['الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت'];
var MONTHS = ['يناير','فبراير','مارس','أبريل','مايو','يونيو','يوليو','أغسطس','سبتمبر','أكتوبر','نوفمبر','ديسمبر'];
var SKY = {
  night:'linear-gradient(170deg,#04060d 0%,#0b1224 55%,#16203a 100%)',
  dawn:'linear-gradient(170deg,#141a2e 0%,#4a3c55 55%,#a8735a 100%)',
  day:'linear-gradient(170deg,#23506d 0%,#6c97ae 55%,#cdb894 100%)',
  dusk:'linear-gradient(170deg,#24304a 0%,#8a5e42 58%,#c8925a 100%)',
  evening:'linear-gradient(170deg,#140f1f 0%,#3e2638 55%,#7c4638 100%)'
};
var PAL = {
  night:   ['#141f38','#0d1428','#05080f','#08101f',.95,.85,.10,.22],
  dawn:    ['#5a4c66','#3a2f4c','#1a1426','#3a3a58',.6,.25,.2,.4],
  day:     ['#8e9aa3','#6f5b49','#33261f','#2b6a86',0,0,.25,.5],
  dusk:    ['#8f6a55','#5c3b2d','#241612','#4e4456',.35,0,.25,.45],
  evening: ['#5c3a50','#352035','#140c1c','#211a34',.85,.45,.18,.3]
};
var P = null, W = null, picking = false, pickMode = 'country', sel = 0, pickCountry = 0, curPhase = '';
var BG = [], bgIdx = 0, bgShown = -1, lastSwitch = 0, layerA = true;

function $(id){ return document.getElementById(id); }
function pad(n){ return n < 10 ? '0' + n : '' + n; }
function ar(s){ return String(s).replace(/[0-9]/g, function(d){ return '٠١٢٣٤٥٦٧٨٩'.charAt(+d); }); }
function sget(k){ try { return JSON.parse(localStorage.getItem('sh_' + k)); } catch(e){ return null; } }
function sset(k, v){ try { localStorage.setItem('sh_' + k, JSON.stringify(v)); } catch(e){} }
function get(url, cb){
  var x = new XMLHttpRequest(), done = false;
  function fin(v){ if (!done){ done = true; cb(v); } }
  x.onreadystatechange = function(){
    if (x.readyState === 4){
      if (x.status === 200){ try { fin(JSON.parse(x.responseText)); } catch(e){ fin(null); } } else fin(null);
    }
  };
  try { x.open('GET', url, true); x.send(); } catch(e){ fin(null); }
  setTimeout(function(){ fin(null); }, 20000);
}

var loc = sget('loc');
if (!loc || !CFG.countries[loc[0]] || !CFG.countries[loc[0]][3][loc[1]]) loc = CFG.loc;
function country(){ return CFG.countries[loc[0]]; }
function city(){ return country()[3][loc[1]]; }
function lkey(){ return loc[0] + '_' + loc[1]; }
function dkey(d){ return pad(d.getDate()) + '-' + pad(d.getMonth() + 1) + '-' + d.getFullYear(); }
function mins(s){ var a = s.split(':'); return (+a[0]) * 60 + (+a[1]); }
function t12(s, withAmpm){
  var m = mins(s), h = Math.floor(m / 60), h12 = h % 12 || 12;
  return ar(h12 + ':' + pad(m % 60)) + (withAmpm ? (h < 12 ? ' ص' : ' م') : '');
}
function pname(k, d){ return (k === 'Dhuhr' && d.getDay() === 5) ? 'الجمعة' : NAMES[k]; }

function phase(m){
  if (!P){ var h = Math.floor(m / 60); return h < 5 || h >= 19 ? 'night' : (h < 7 ? 'dawn' : (h < 15 ? 'day' : (h < 18 ? 'dusk' : 'evening'))); }
  var f = mins(P.t.Fajr), s = mins(P.t.Sunrise), a = mins(P.t.Asr), g = mins(P.t.Maghrib), i = mins(P.t.Isha);
  if (m < f || m >= i + 30) return 'night';
  if (m < s + 40) return 'dawn';
  if (m < a) return 'day';
  if (m < g) return 'dusk';
  return 'evening';
}
function nextPrayer(m){
  var list = ['Fajr','Dhuhr','Asr','Maghrib','Isha'];
  for (var i = 0; i < list.length; i++){ var t = mins(P.t[list[i]]); if (t > m) return {k: list[i], left: t - m}; }
  return {k: 'Fajr', left: mins(P.t.Fajr) + 1440 - m};
}
function leftText(n){
  var h = Math.floor(n / 60), m = n % 60, out = [];
  if (h) out.push(h === 1 ? 'ساعة' : (h === 2 ? 'ساعتين' : ar(h) + (h <= 10 ? ' ساعات' : ' ساعة')));
  if (m) out.push(m === 1 ? 'دقيقة' : (m === 2 ? 'دقيقتين' : ar(m) + (m <= 10 ? ' دقائق' : ' دقيقة')));
  return out.length ? 'بعد ' + out.join(' و') : 'الآن';
}

function applyPhase(ph){
  if (ph === curPhase) return;
  curPhase = ph;
  var c = PAL[ph];
  $('sky').style.background = SKY[ph];
  $('stars').style.opacity = c[5] ? '1' : '0';
  $('pal').innerHTML = '.f1{fill:' + c[0] + '}.f2{fill:' + c[1] + '}.f3{fill:' + c[2] + '}.sea{fill:' + c[3] + '}' +
    '.lt{fill:#ffcf7a;opacity:' + c[4] + '}.st{fill:#fff;opacity:' + c[5] + '}.rf{fill:#fff;opacity:' + c[6] + '}' +
    '.mr{opacity:' + c[7] + '}.gl{fill:#d9b46a;opacity:' + (c[4] * .6 + .4) + '}.cv{fill:#fff;opacity:.12}';
}

function tick(){
  var d = new Date(), m = d.getHours() * 60 + d.getMinutes(), h12 = d.getHours() % 12 || 12;
  $('clock').innerHTML = ar(h12) + '<i>:</i>' + ar(pad(d.getMinutes()));
  $('ampm').innerHTML = d.getHours() < 12 ? 'ص' : 'م';
  $('greg').innerHTML = ar(d.getDate()) + ' ' + MONTHS[d.getMonth()] + ' ' + ar(d.getFullYear());
  $('tz').innerHTML = 'بتوقيت ' + city()[0];
  applyPhase(phase(m));
  if (Date.now() - lastSwitch >= 5 * 60000) setBg(bgIdx + 1);
  if (P && P.date !== dkey(d)) loadPrayers();
  if (!P){ $('hijri').innerHTML = DAYS[d.getDay()]; return; }
  $('hijri').innerHTML = DAYS[d.getDay()] + '، ' + P.hijri;
  var n = nextPrayer(m), html = '';
  for (var i = 0; i < ORDER.length; i++){
    var k = ORDER[i];
    html += '<div class="t' + (k === n.k ? ' on' : '') + '"><span>' + pname(k, d) + '</span><b>' + t12(P.t[k]) + '</b></div>';
  }
  $('times').innerHTML = html;
  $('nextName').innerHTML = pname(n.k, d) + ' ' + t12(P.t[n.k], true);
  $('nextLeft').innerHTML = leftText(n.left);
}

function loadPrayers(){
  var d = new Date(), c = city(), k = dkey(d), cached = sget('p'), lk = lkey();
  if (cached && cached.date === k && cached.loc === lk){ P = cached; tick(); return; }
  get('https://api.aladhan.com/v1/timings/' + k + '?latitude=' + c[1] + '&longitude=' + c[2] +
      '&method=' + country()[1] + '&school=' + country()[2], function(j){
    if (j && j.data && j.data.timings){
      var h = j.data.date.hijri, t = {};
      for (var i = 0; i < ORDER.length; i++) t[ORDER[i]] = String(j.data.timings[ORDER[i]]).substr(0, 5);
      P = {date: k, loc: lk, t: t, hijri: ar(parseInt(h.day, 10)) + ' ' + h.month.ar.replace(/[\u064B-\u065F]/g, '') + ' ' + ar(h.year) + ' هـ'};
      sset('p', P);
    } else if (cached && cached.loc === lk){
      P = cached;
    }
    tick();
  });
}

function wxText(c){
  if (c === 0) return 'صحو';
  if (c <= 2) return 'غائم جزئياً';
  if (c === 3) return 'غائم';
  if (c === 45 || c === 48) return 'ضباب';
  if (c >= 51 && c <= 57) return 'رذاذ';
  if (c >= 61 && c <= 67) return 'أمطار';
  if (c >= 71 && c <= 77) return 'ثلوج';
  if (c >= 80 && c <= 82) return 'زخات مطر';
  if (c >= 95) return 'عواصف رعدية';
  return '';
}
function deg(v){ return '<span dir="ltr">' + ar(Math.round(v)) + '°</span>'; }
function renderWx(w){
  if (!w || !w.daily) return;
  var d = new Date(), now = w.current ? w.current.temperature_2m : w.daily.temperature_2m_max[0];
  var code = w.current ? w.current.weather_code : w.daily.weather_code[0];
  $('wxNow').innerHTML = deg(now) + ' <b class="gold">' + wxText(code) + '</b>';
  $('wxSub').innerHTML = city()[0] + ' · ' + deg(w.daily.temperature_2m_max[0]) + ' / ' + deg(w.daily.temperature_2m_min[0]);
  var html = '';
  for (var i = 1; i < 3 && i < w.daily.time.length; i++){
    html += '<span><span class="muted">' + DAYS[(d.getDay() + i) % 7] + '</span> ' + deg(w.daily.temperature_2m_max[i]) + '</span>';
  }
  $('wxDays').innerHTML = html;
}
function loadWeather(){
  var c = city(), cached = sget('w'), lk = lkey();
  if (cached && cached.loc === lk) renderWx(cached.data);
  get('https://api.open-meteo.com/v1/forecast?latitude=' + c[1] + '&longitude=' + c[2] +
      '&current=temperature_2m,weather_code&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=3',
      function(j){ if (j && j.daily){ sset('w', {loc: lk, data: j}); renderWx(j); } });
}

function loadVerse(){
  var day = Math.floor(Date.now() / 86400000), cached = sget('v2');
  if (cached && cached.day === day){ showVerse(cached); return; }
  var n = ((day * 7919) % 6236) + 1, tries = 0;
  function attempt(){
    get('https://api.alquran.cloud/v1/ayah/' + n + '/quran-simple', function(j){
      if (j && j.data && j.data.text){
        if (j.data.text.length > 115 && tries < 12){ tries++; n = n % 6236 + 1; attempt(); return; }
        var v = {day: day, text: j.data.text,
                 src: String(j.data.surah.name).replace(/[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]/g, '') + ' · الآية ' + ar(j.data.numberInSurah)};
        sset('v2', v); showVerse(v);
      } else if (cached){ showVerse(cached); }
    });
  }
  attempt();
}
function showVerse(v){ $('verse').innerHTML = '﴿' + v.text + '﴾'; $('verseSrc').innerHTML = v.src; $('card').style.display = 'block'; }

function buildBg(){
  var i;
  if (CFG.photos && CFG.photos.length){
    for (i = 0; i < CFG.photos.length; i++) BG.push({photo: CFG.photos[i], name: 'صورة ' + ar(i + 1) + ' من ' + ar(CFG.photos.length), tag: 'صوري'});
  }
  if (!BG.length || CFG.mix){
    for (i = 0; i < CFG.scenes.length; i++) BG.push({scene: CFG.scenes[i][3], name: CFG.scenes[i][0] + ' · ' + CFG.scenes[i][1], tag: CFG.scenes[i][2]});
  }
  var saved = sget('bg');
  bgIdx = (saved !== null && saved < BG.length) ? saved : 0;
}
function setBg(i){
  bgIdx = ((i % BG.length) + BG.length) % BG.length;
  lastSwitch = Date.now();
  sset('bg', bgIdx);
  if (bgIdx === bgShown) return;
  bgShown = bgIdx;
  var b = BG[bgIdx], show = layerA ? $('lA') : $('lB'), hide = layerA ? $('lB') : $('lA');
  if (b.photo){ show.innerHTML = ''; show.style.backgroundImage = 'url("' + b.photo + '")'; }
  else { show.style.backgroundImage = 'none'; show.innerHTML = b.scene; }
  show.className = 'layer on'; hide.className = 'layer';
  layerA = !layerA;
  $('placeTag').innerHTML = b.tag;
  $('placeName').innerHTML = b.name;
}

/* منع توفير الطاقة وشاشة التوقف طالما التطبيق مفتوح */
var lunaRefs = [];
function luna(uri, params, cb){
  try {
    var b = new PalmServiceBridge();
    b.onservicecallback = function(m){ try { if (cb) cb(JSON.parse(m)); } catch(e){} };
    b.call(uri, JSON.stringify(params));
    lunaRefs.push(b); if (lunaRefs.length > 20) lunaRefs.shift();
    return b;
  } catch(e){ return null; }
}
var lgSaverHooked = false;
function keepAwake(){
  try { if (window.webapis && webapis.appcommon) webapis.appcommon.setScreenSaver(webapis.appcommon.AppCommonScreenSaverState.SCREEN_SAVER_OFF); } catch(e){}
  try { if (window.tizen && tizen.power) tizen.power.request('SCREEN', 'SCREEN_NORMAL'); } catch(e){}
  if (window.PalmServiceBridge && !lgSaverHooked){
    lgSaverHooked = true;
    luna('luna://com.webos.service.tvpower/power/registerScreenSaverRequest', {subscribe: true, clientName: 'sa.shashati.app'}, function(r){
      if (r && r.timestamp !== undefined){
        luna('luna://com.webos.service.tvpower/power/responseScreenSaverRequest', {clientName: 'sa.shashati.app', ack: false, timestamp: r.timestamp});
      }
    });
  }
  var v = $('keep');
  if (CFG.keep && v){
    if (!v.src) v.src = CFG.keep;
    try { var pr = v.play(); if (pr && pr['catch']) pr['catch'](function(){}); } catch(e){}
  }
}

function drift(){
  var x = Math.round((Math.random() - .5) * 12) / 10, y = Math.round((Math.random() - .5) * 12) / 10;
  var t = 'translate(' + x + 'vw,' + y + 'vh)';
  $('ui').style.webkitTransform = t; $('ui').style.transform = t;
}

function pickItems(){ return pickMode === 'country' ? CFG.countries : CFG.countries[pickCountry][3]; }
function openPicker(){ picking = true; pickMode = 'country'; sel = loc[0]; renderPicker(); $('picker').style.display = 'block'; }
function renderPicker(){
  var items = pickItems(), html = '';
  for (var i = 0; i < items.length; i++) html += '<div id="c' + i + '">' + items[i][0] + '</div>';
  $('list').innerHTML = html;
  $('pickTitle').innerHTML = pickMode === 'country' ? 'اختر دولتك' : 'اختر مدينتك في ' + CFG.countries[pickCountry][0];
  paintPicker();
}
function paintPicker(){
  var items = pickItems();
  for (var i = 0; i < items.length; i++) $('c' + i).className = i === sel ? 'sel' : '';
  var el = $('c' + sel), box = $('list');
  box.scrollTop = Math.max(0, el.offsetTop - box.clientHeight / 2 + el.offsetHeight / 2);
}
function closePicker(){ picking = false; $('picker').style.display = 'none'; }
function choose(){
  if (pickMode === 'country'){
    pickCountry = sel; pickMode = 'city'; sel = pickCountry === loc[0] ? loc[1] : 0;
    if (CFG.countries[pickCountry][3].length > 1){ renderPicker(); return; }
  }
  loc = [pickCountry, sel]; sset('loc', loc); closePicker();
  P = null; $('times').innerHTML = ''; $('nextName').innerHTML = '&nbsp;'; $('nextLeft').innerHTML = '';
  $('wxNow').innerHTML = '&nbsp;'; $('wxSub').innerHTML = ''; $('wxDays').innerHTML = '';
  loadPrayers(); loadWeather(); tick();
}
function exitApp(){
  try { if (window.tizen){ tizen.application.getCurrentApplication().exit(); return; } } catch(e){}
  try { if (window.webOS && webOS.platformBack){ webOS.platformBack(); return; } } catch(e){}
  window.close();
}
document.addEventListener('keydown', function(e){
  var k = e.keyCode, back = (k === 10009 || k === 461 || k === 27 || k === 8);
  if (picking){
    var len = pickItems().length;
    if (k === 38){ sel = (sel - 1 + len) % len; paintPicker(); }
    else if (k === 40){ sel = (sel + 1) % len; paintPicker(); }
    else if (k === 13) choose();
    else if (back){ if (pickMode === 'city'){ pickMode = 'country'; sel = pickCountry; renderPicker(); } else closePicker(); }
    e.preventDefault(); return;
  }
  if (k === 39 || k === 40){ setBg(bgIdx + 1); e.preventDefault(); }
  else if (k === 37 || k === 38){ setBg(bgIdx - 1); e.preventDefault(); }
  else if (k === 13){ openPicker(); e.preventDefault(); }
  else if (back && k !== 8){ exitApp(); e.preventDefault(); }
});

$('stars').innerHTML = CFG.stars;
buildBg(); setBg(bgIdx);
tick(); loadPrayers(); loadWeather(); loadVerse(); drift(); keepAwake();
setInterval(keepAwake, 5 * 60000);
setInterval(tick, 10000);
setInterval(loadWeather, 30 * 60000);
setInterval(loadVerse, 60 * 60000);
setInterval(drift, 120000);
setTimeout(function(){ $('hint').style.opacity = '0'; }, 12000);
</script>
</body>
</html>
"""


FONT_SOURCES = {
    # الاسم في الصفحة: (ملف التخزين، الوزن، الروابط)
    "ShUI-400": ("plex-ar-400.woff2", "ShUI", 400, [
        "https://cdn.jsdelivr.net/npm/@fontsource/ibm-plex-sans-arabic/files/ibm-plex-sans-arabic-arabic-400-normal.woff2",
        "https://cdn.jsdelivr.net/fontsource/fonts/ibm-plex-sans-arabic@latest/arabic-400-normal.woff2"]),
    "ShUI-600": ("plex-ar-600.woff2", "ShUI", 600, [
        "https://cdn.jsdelivr.net/npm/@fontsource/ibm-plex-sans-arabic/files/ibm-plex-sans-arabic-arabic-600-normal.woff2",
        "https://cdn.jsdelivr.net/fontsource/fonts/ibm-plex-sans-arabic@latest/arabic-600-normal.woff2"]),
    "ShUI-L400": ("plex-la-400.woff2", "ShUI", 400, [
        "https://cdn.jsdelivr.net/npm/@fontsource/ibm-plex-sans-arabic/files/ibm-plex-sans-arabic-latin-400-normal.woff2",
        "https://cdn.jsdelivr.net/fontsource/fonts/ibm-plex-sans-arabic@latest/latin-400-normal.woff2"]),
    "ShQ-400": ("amiri-ar-400.woff2", "ShQ", 400, [
        "https://cdn.jsdelivr.net/npm/@fontsource/amiri/files/amiri-arabic-400-normal.woff2",
        "https://cdn.jsdelivr.net/fontsource/fonts/amiri@latest/arabic-400-normal.woff2"]),
}
FONT_DIR = os.path.join(os.path.expanduser("~"), ".tv_manager_fonts")


def ensure_fonts(log=None):
    """يحمّل الخطوط مرة وحدة من الإنترنت ويخزنها على الكمبيوتر (الشاشة ما تحتاج إنترنت للخط)."""
    import urllib.request
    os.makedirs(FONT_DIR, exist_ok=True)
    got = {}
    for key, (fname, family, weight, urls) in FONT_SOURCES.items():
        path = os.path.join(FONT_DIR, fname)
        if not os.path.isfile(path) or os.path.getsize(path) < 2000:
            for u in urls:
                try:
                    req = urllib.request.Request(u, headers={"User-Agent": "TVManager/1.0"})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        data = r.read()
                    if len(data) > 2000 and data[:4] == b"wOF2":
                        with open(path, "wb") as f:
                            f.write(data)
                        break
                except Exception:
                    continue
        if os.path.isfile(path) and os.path.getsize(path) > 2000:
            got[key] = path
        elif log is not None:
            log.append("ما قدرت أحمّل الخط %s، بيستخدم خط الشاشة بداله." % family)
    return got


def font_css(log=None):
    import base64
    css = []
    for key, path in ensure_fonts(log).items():
        _, family, weight, _ = FONT_SOURCES[key]
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        css.append("@font-face{font-family:'%s';font-weight:%d;font-style:normal;font-display:block;"
                   "src:url(data:font/woff2;base64,%s) format('woff2')}" % (family, weight, b64))
    return "\n".join(css)


KEEP_MP4_B64 = "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"


PHOTO_DIR = os.path.join(os.path.expanduser("~"), ".tv_manager_photos")


def photo_names():
    try:
        names = [n for n in os.listdir(PHOTO_DIR) if n.lower().endswith(".jpg")]
    except OSError:
        return []
    return sorted(names, key=lambda n: (len(n), n))


def _loc(country=0, city=0):
    country, city = int(country), int(city)
    if not 0 <= country < len(COUNTRIES):
        country = 0
    if not 0 <= city < len(COUNTRIES[country][3]):
        city = 0
    return [country, city]


def page(country=0, city=0, photo_base="photos/", photo_suffix="", log=None, keep_src="keep.mp4"):
    cfg = {
        "loc": _loc(country, city),
        "countries": COUNTRIES,
        "scenes": tv_scenes.build(),
        "stars": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice">'
                  + tv_scenes.stars() + '</svg>'),
        "photos": [photo_base + n + photo_suffix for n in photo_names()],
        "mix": False,
        "keep": keep_src,
    }
    data = json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")
    return PAGE_TEMPLATE.replace("__FONTS__", font_css(log)).replace("__CONFIG__", data)


def _photo_files():
    out = {}
    for n in photo_names():
        with open(os.path.join(PHOTO_DIR, n), "rb") as f:
            out["photos/" + n] = f.read()
    return out


# ---------------------------------------------------------------- الأيقونة (PNG بدون مكتبات)
def png_icon(size):
    """أيقونة: سماء ليلية بتدرج وهلال ذهبي."""
    rows = []
    cx, cy, r = size * 0.47, size * 0.5, size * 0.3
    ox, oy, r2 = cx + size * 0.12, cy - size * 0.07, size * 0.26
    top, bot = (16, 30, 70), (190, 110, 80)
    for y in range(size):
        t = y / max(1, size - 1)
        bg = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        row = bytearray(b"\x00")
        for x in range(size):
            in_moon = (x - cx) ** 2 + (y - cy) ** 2 <= r * r and (x - ox) ** 2 + (y - oy) ** 2 > r2 * r2
            row += bytes((255, 214, 130, 255)) if in_moon else bytes(bg + (255,))
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


# ---------------------------------------------------------------- LG: ملف ipk
def _tar_gz(entries):
    buf = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.USTAR_FORMAT) as tar:
        for path, data in entries:
            info = tarfile.TarInfo(path)
            info.mtime, info.uid, info.gid, info.uname, info.gname = now, 0, 0, "root", "root"
            if data is None:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(info)
            else:
                info.size, info.mode = len(data), 0o644
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _ar(members):
    out = bytearray(b"!<arch>\n")
    now = int(time.time())
    for name, data in members:
        hdr = (name.ljust(16) + str(now).ljust(12) + "0".ljust(6) + "0".ljust(6)
               + "100644".ljust(8) + str(len(data)).ljust(10) + "`\n").encode("ascii")
        out += hdr + data
        if len(data) % 2:
            out += b"\n"
    return bytes(out)


def lg_ipk(country=0, city=0, log=None):
    appinfo = {
        "id": LG_APP_ID, "version": APP_VERSION, "vendor": "Shashati", "type": "web",
        "main": "index.html", "title": "شاشتي", "icon": "icon.png", "largeIcon": "largeIcon.png",
        "bgColor": "#0a1230", "disableBackHistoryAPI": True, "resolution": "1920x1080",
    }
    pkginfo = {"app": LG_APP_ID, "id": LG_APP_ID, "loc_name": "شاشتي",
               "package_format_version": 2, "vendor": "Shashati", "version": APP_VERSION}
    files = {
        "appinfo.json": json.dumps(appinfo, ensure_ascii=False, indent=1).encode("utf-8"),
        "index.html": page(country, city, log=log).encode("utf-8"),
        "icon.png": png_icon(80),
        "largeIcon.png": png_icon(130),
    }
    files.update(_photo_files())
    files["keep.mp4"] = base64.b64decode(KEEP_MP4_B64)
    base = "./usr/palm/applications/" + LG_APP_ID
    entries = [("./usr", None), ("./usr/palm", None), ("./usr/palm/applications", None), (base, None)]
    if any(n.startswith("photos/") for n in files):
        entries.append((base + "/photos", None))
    entries += [(base + "/" + n, d) for n, d in files.items()]
    entries += [("./usr/palm/packages", None), ("./usr/palm/packages/" + LG_APP_ID, None),
                ("./usr/palm/packages/" + LG_APP_ID + "/packageinfo.json",
                 json.dumps(pkginfo, ensure_ascii=False).encode("utf-8"))]
    size_kb = max(1, sum(len(d) for _, d in entries if d) // 1024)
    control = ("Package: %s\nVersion: %s\nSection: misc\nPriority: optional\nArchitecture: all\n"
               "Installed-Size: %d\nMaintainer: Shashati <nomail@example.com>\n"
               "Description: Shashati Saudi screen\nwebOS-Package-Format-Version: 2\n"
               "webOS-Packager-Version: 2.0.0\n" % (LG_APP_ID, APP_VERSION, size_kb)).encode("ascii")
    return _ar([
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", _tar_gz([("./control", control)])),
        ("data.tar.gz", _tar_gz(entries)),
    ])


# ---------------------------------------------------------------- Samsung: ملفات wgt
TIZEN_CONFIG = """<?xml version="1.0" encoding="UTF-8"?>
<widget xmlns="http://www.w3.org/ns/widgets" xmlns:tizen="http://tizen.org/ns/widgets" id="http://shashati.sa/app" version="%s" viewmodes="maximized">
    <tizen:application id="%s" package="%s" required_version="2.3"/>
    <content src="index.html"/>
    <feature name="http://tizen.org/feature/screen.size.normal.1080.1920"/>
    <icon src="icon.png"/>
    <name>شاشتي</name>
    <tizen:profile name="tv-samsung"/>
    <tizen:privilege name="http://tizen.org/privilege/internet"/>
    <tizen:privilege name="http://tizen.org/privilege/power"/>
    <access origin="*" subdomains="true"/>
    <tizen:setting screen-orientation="landscape" context-menu="disable" background-support="disable" encryption="disable" install-location="auto" hwkey-event="enable"/>
</widget>
""" % (APP_VERSION, TIZEN_APP_ID, TIZEN_PKG_ID)


def tizen_files(country=0, city=0, log=None):
    files = {
        "config.xml": TIZEN_CONFIG.encode("utf-8"),
        "index.html": page(country, city, log=log).encode("utf-8"),
        "icon.png": png_icon(117),
    }
    files.update(_photo_files())
    files["keep.mp4"] = base64.b64decode(KEEP_MP4_B64)
    return files


def tizen_unsigned_zip(country=0, city=0):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in tizen_files(country, city).items():
            z.writestr(n, d)
    return buf.getvalue()
