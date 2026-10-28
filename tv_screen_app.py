# -*- coding: utf-8 -*-
"""
«شاشتي» — واجهة لشاشات سامسونج (Tizen) و LG (webOS).
ساعة، تاريخ هجري وميلادي، مواقيت الصلاة لكل الدول الإسلامية بطريقة حساب كل دولة،
الطقس لثلاثة أيام، وآية اليوم، فوق مناظر سعودية مرسومة تتلوّن مع أوقات الصلاة.

هذا الملف يبني التطبيق ويغلّفه:
  - LG: ملف .ipk جاهز للتثبيت (بدون أي أدوات خارجية)
  - Samsung: ملفات .wgt تحتاج توقيعها بشهادة سامسونج عبر Tizen Studio
"""

import io
import json
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
<link href="https://fonts.googleapis.com/css2?family=Readex+Pro:wght@300;500;700&display=swap" rel="stylesheet">
<style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0a1230;color:#fff;
  font-family:"Readex Pro","Samsung Sans Arabic","LG Smart UI","Noto Sans Arabic",Tahoma,sans-serif}
#sky{position:fixed;top:0;left:0;right:0;bottom:0;-webkit-transition:background 6s;transition:background 6s}
#sun{position:fixed;width:38vw;height:38vw;border-radius:50%;top:-10vw;left:-8vw;
  background:radial-gradient(circle,rgba(255,236,190,.35) 0%,rgba(255,236,190,0) 65%);opacity:0;-webkit-transition:opacity 6s;transition:opacity 6s}
#stars,.scene,#shade{position:fixed;top:0;left:0;right:0;bottom:0}
#stars svg,.scene svg{width:100%;height:100%;display:block}
#stars{-webkit-transition:opacity 6s;transition:opacity 6s}
.scene{opacity:0;-webkit-transition:opacity 5s;transition:opacity 5s}
.scene.on{opacity:1}
.f1,.f2,.f3,.sea,.lt,.st,.rf,.mr,.gl{-webkit-transition:fill 6s,opacity 6s;transition:fill 6s,opacity 6s}
#shade{top:auto;height:48%;background:linear-gradient(to bottom,rgba(0,0,0,0),rgba(0,0,0,.42))}
#place{position:fixed;bottom:2.4vh;left:0;right:0;text-align:center;font-size:1.15vw;opacity:.85;text-shadow:0 .1vw .6vw rgba(0,0,0,.5)}
#place span{border:1px solid rgba(255,255,255,.45);border-radius:1vw;padding:0 .7vw;margin-right:.7vw;font-size:.95vw}
#wrap{position:absolute;top:5vh;bottom:6vh;left:4.5vw;right:4.5vw;-webkit-transition:-webkit-transform 3s;transition:transform 3s}
.row{display:-webkit-box;display:-webkit-flex;display:flex;-webkit-box-pack:justify;-webkit-justify-content:space-between;justify-content:space-between}
.top{-webkit-align-items:flex-start;align-items:flex-start;height:62%}
.bottom{-webkit-align-items:flex-end;align-items:flex-end;height:38%}
#clock{font-size:14vw;font-weight:300;line-height:1;text-shadow:0 .5vw 2.5vw rgba(0,0,0,.25)}
#ampm{font-size:3vw;font-weight:500;margin-right:1vw;opacity:.85}
#hijri{font-size:3vw;font-weight:500;margin-top:1.4vw}
#greg{font-size:2vw;opacity:.85;margin-top:.3vw}
#cityName{font-size:1.5vw;opacity:.7;margin-top:1vw}
.panel{background:rgba(5,10,25,.28);border-radius:1.6vw;padding:1.2vw 1.8vw;min-width:24vw}
.p{font-size:2vw;padding:.42vw 0;border-bottom:1px solid rgba(255,255,255,.12)}
.p:last-child{border-bottom:0}
.p span{float:left}
.p.next{color:#ffd98a;font-weight:700}
#countdown{font-size:1.6vw;color:#ffd98a;margin-top:.9vw;text-align:center}
#wx{display:-webkit-box;display:-webkit-flex;display:flex}
.day{margin-left:3vw;text-align:center}
.day .t{display:block;font-size:2.8vw;font-weight:500;line-height:1.2}
.day .n{font-size:1.4vw;opacity:.85}
.day .d{display:block;font-size:1.3vw;opacity:.75}
#verse{max-width:48vw;font-size:2vw;line-height:1.9;text-align:right;text-shadow:0 .2vw 1vw rgba(0,0,0,.3)}
#verseSrc{display:block;font-size:1.3vw;opacity:.75;margin-top:.4vw}
#hint{position:fixed;top:1.2vh;left:0;right:0;text-align:center;font-size:1.2vw;opacity:.6;-webkit-transition:opacity 2s;transition:opacity 2s}
#picker{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(4,8,20,.88)}
#picker h2{font-size:2.4vw;font-weight:500;text-align:center;margin:5vh 0 2vh}
#list{width:34vw;height:72vh;margin:0 auto;overflow:hidden;position:relative}
#list div{font-size:1.9vw;padding:.7vw 1.4vw;border-radius:.8vw;border:.2vw solid transparent}
#list div.sel{border-color:#ffd98a;color:#ffd98a;background:rgba(255,217,138,.08)}
</style>
</head>
<body>
<style id="pal"></style>
<div id="sky"></div><div id="sun"></div><div id="stars"></div>
<div class="scene" id="sA"></div><div class="scene" id="sB"></div><div id="shade"></div>
<div id="wrap">
  <div class="row top">
    <div>
      <div><span id="clock">--:--</span><span id="ampm"></span></div>
      <div id="hijri"></div>
      <div id="greg"></div>
      <div id="cityName"></div>
    </div>
    <div class="panel"><div id="prayers"></div><div id="countdown"></div></div>
  </div>
  <div class="row bottom">
    <div id="wx"></div>
    <div id="verse"></div>
  </div>
</div>
<div id="place"></div>
<div id="hint">اضغط OK لتغيير الدولة والمدينة</div>
<div id="picker"><h2 id="pickTitle">اختر دولتك</h2><div id="list"></div></div>
<script>
var CFG = __CONFIG__;
var ORDER = ['Fajr','Sunrise','Dhuhr','Asr','Maghrib','Isha'];
var NAMES = {Fajr:'الفجر',Sunrise:'الشروق',Dhuhr:'الظهر',Asr:'العصر',Maghrib:'المغرب',Isha:'العشاء'};
var DAYS = ['الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت'];
var MONTHS = ['يناير','فبراير','مارس','أبريل','مايو','يونيو','يوليو','أغسطس','سبتمبر','أكتوبر','نوفمبر','ديسمبر'];
var SKY = {
  night:'linear-gradient(165deg,#070d24 0%,#132147 55%,#22355f 100%)',
  dawn:'linear-gradient(170deg,#24315f 0%,#6d5687 50%,#e09a76 100%)',
  day:'linear-gradient(170deg,#1b6894 0%,#4c98bd 50%,#d8b886 100%)',
  dusk:'linear-gradient(170deg,#34587f 0%,#c4864f 58%,#ebb86d 100%)',
  evening:'linear-gradient(170deg,#24183f 0%,#633555 52%,#b8694a 100%)'
};
var PAL = {
  night:   ['#1b2a4c','#121c37','#080d1d','#0c1630',.95,.9,.12,.25],
  dawn:    ['#7a6890','#4d4068','#261d3a','#4b4a74',.6,.3,.25,.45],
  day:     ['#9eabb6','#8b7058','#45322a','#2d7fa3',0,0,.3,.6],
  dusk:    ['#b08468','#7a4e3a','#33201a','#6a5a70',.35,0,.3,.55],
  evening: ['#7c4a68','#47284a','#1c1128','#2c2346',.85,.5,.2,.35]
};
var P = null, picking = false, pickMode = 'country', sel = 0, pickCountry = 0, curPhase = '', curScene = -1, layerA = true;

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
function t12(s){
  var m = mins(s), h = Math.floor(m / 60), mm = m % 60, h12 = h % 12 || 12;
  return ar(h12 + ':' + pad(mm));
}

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

function tick(){
  var d = new Date(), m = d.getHours() * 60 + d.getMinutes();
  var h12 = d.getHours() % 12 || 12;
  $('clock').innerHTML = ar(h12 + ':' + pad(d.getMinutes()));
  $('ampm').innerHTML = d.getHours() < 12 ? 'ص' : 'م';
  $('greg').innerHTML = DAYS[d.getDay()] + '، ' + ar(d.getDate()) + ' ' + MONTHS[d.getMonth()] + ' ' + ar(d.getFullYear());
  $('cityName').innerHTML = city()[0] + '، ' + country()[0];
  var ph = phase(m);
  if (ph !== curPhase){
    curPhase = ph;
    var c = PAL[ph];
    $('sky').style.background = SKY[ph];
    $('sun').style.opacity = (ph === 'day' || ph === 'dusk' || ph === 'dawn') ? '1' : '0';
    $('pal').innerHTML = '.f1{fill:' + c[0] + '}.f2{fill:' + c[1] + '}.f3{fill:' + c[2] + '}.sea{fill:' + c[3] + '}' +
      '.lt{fill:#ffcf7a;opacity:' + c[4] + '}.st{fill:#fff;opacity:' + c[5] + '}.rf{fill:#fff;opacity:' + c[6] + '}' +
      '.mr{opacity:' + c[7] + '}.gl{fill:#e9b85c;opacity:' + (c[4] * .6 + .4) + '}.cv{fill:#fff;opacity:.13}';
  }
  showScene();
  if (P && P.date !== dkey(d)) loadPrayers();
  if (!P) return;
  $('hijri').innerHTML = P.hijri;
  var n = nextPrayer(m), html = '', friday = d.getDay() === 5;
  for (var i = 0; i < ORDER.length; i++){
    var k = ORDER[i], name = (k === 'Dhuhr' && friday) ? 'الجمعة' : NAMES[k];
    html += '<div class="p' + (k === n.k ? ' next' : '') + '">' + name + '<span>' + t12(P.t[k]) + '</span></div>';
  }
  $('prayers').innerHTML = html;
  var hh = Math.floor(n.left / 60), mm = n.left % 60;
  var left = hh ? ar(hh) + ' س ' + ar(mm) + ' د' : ar(mm) + ' دقيقة';
  $('countdown').innerHTML = 'باقي على ' + ((n.k === 'Dhuhr' && friday) ? 'الجمعة' : NAMES[n.k]) + ' ' + left;
}

function loadPrayers(){
  var d = new Date(), c = city(), k = dkey(d), cached = sget('p'), lk = lkey();
  if (cached && cached.date === k && cached.loc === lk){ P = cached; tick(); return; }
  get('https://api.aladhan.com/v1/timings/' + k + '?latitude=' + c[1] + '&longitude=' + c[2] +
      '&method=' + country()[1] + '&school=' + country()[2], function(j){
    if (j && j.data && j.data.timings){
      var h = j.data.date.hijri, t = {};
      for (var i = 0; i < ORDER.length; i++) t[ORDER[i]] = String(j.data.timings[ORDER[i]]).substr(0, 5);
      P = {date: k, loc: lk, t: t, hijri: ar(parseInt(h.day, 10)) + ' ' + h.month.ar + ' ' + ar(h.year) + ' هـ'};
      sset('p', P);
    } else if (cached && cached.loc === lk){
      P = cached;
    }
    tick();
  });
}

function wxText(c){
  if (c === 0) return 'صحو';
  if (c <= 3) return 'غائم جزئياً';
  if (c === 45 || c === 48) return 'ضباب';
  if (c >= 51 && c <= 57) return 'رذاذ';
  if (c >= 61 && c <= 67) return 'أمطار';
  if (c >= 71 && c <= 77) return 'ثلوج';
  if (c >= 80 && c <= 82) return 'زخات مطر';
  if (c >= 95) return 'عواصف رعدية';
  return '';
}
function renderWx(w){
  if (!w || !w.daily) return;
  var html = '', d = new Date();
  for (var i = 0; i < 3 && i < w.daily.time.length; i++){
    var label = i === 0 ? 'اليوم' : DAYS[(d.getDay() + i) % 7];
    var temp = i === 0 && w.current ? Math.round(w.current.temperature_2m) : Math.round(w.daily.temperature_2m_max[i]);
    var code = i === 0 && w.current ? w.current.weather_code : w.daily.weather_code[i];
    html += '<div class="day"><span class="t" dir="ltr">' + ar(temp) + '°</span><span class="n">' + label + '</span>' +
            '<span class="d">' + wxText(code) + '</span><span class="d" dir="ltr">' + ar(Math.round(w.daily.temperature_2m_max[i])) + '° / ' + ar(Math.round(w.daily.temperature_2m_min[i])) + '°</span></div>';
  }
  $('wx').innerHTML = html;
}
function loadWeather(){
  var c = city(), cached = sget('w'), lk = lkey();
  if (cached && cached.loc === lk) renderWx(cached.data);
  get('https://api.open-meteo.com/v1/forecast?latitude=' + c[1] + '&longitude=' + c[2] +
      '&current=temperature_2m,weather_code&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=3',
      function(j){ if (j && j.daily){ sset('w', {loc: lk, data: j}); renderWx(j); } });
}

function loadVerse(){
  var day = Math.floor(Date.now() / 86400000), cached = sget('v');
  if (cached && cached.day === day){ showVerse(cached); return; }
  var n = ((day * 7919) % 6236) + 1, tries = 0;
  function attempt(){
    get('https://api.alquran.cloud/v1/ayah/' + n + '/quran-simple', function(j){
      if (j && j.data && j.data.text){
        if (j.data.text.length > 170 && tries < 6){ tries++; n = n % 6236 + 1; attempt(); return; }
        var v = {day: day, text: j.data.text, src: String(j.data.surah.name).replace(/[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]/g, '') + ' · ' + ar(j.data.numberInSurah)};
        sset('v', v); showVerse(v);
      } else if (cached){ showVerse(cached); }
    });
  }
  attempt();
}
function showVerse(v){ $('verse').innerHTML = '﴿' + v.text + '﴾<span id="verseSrc">' + v.src + '</span>'; }

function drift(){
  var x = Math.round((Math.random() - .5) * 20) / 10, y = Math.round((Math.random() - .5) * 20) / 10;
  var t = 'translate(' + x + 'vw,' + y + 'vh)';
  $('wrap').style.webkitTransform = t; $('wrap').style.transform = t;
}

function showScene(){
  var n = CFG.scenes.length, idx = Math.floor(Date.now() / (20 * 60000)) % n;
  if (idx === curScene) return;
  curScene = idx;
  var s = CFG.scenes[idx], show = layerA ? $('sA') : $('sB'), hide = layerA ? $('sB') : $('sA');
  show.innerHTML = s[3];
  show.className = 'scene on'; hide.className = 'scene';
  layerA = !layerA;
  $('place').innerHTML = s[0] + ' · ' + s[1] + '<span>' + s[2] + '</span>';
}

function pickItems(){ return pickMode === 'country' ? CFG.countries : CFG.countries[pickCountry][3]; }
function openPicker(){
  picking = true; pickMode = 'country'; sel = loc[0]; renderPicker();
  $('picker').style.display = 'block';
}
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
  P = null; $('prayers').innerHTML = ''; $('countdown').innerHTML = ''; $('wx').innerHTML = '';
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
  if (k === 13){ openPicker(); e.preventDefault(); }
  else if (back && k !== 8){ exitApp(); e.preventDefault(); }
});

$('stars').innerHTML = CFG.stars;
tick(); loadPrayers(); loadWeather(); loadVerse(); drift();
setInterval(tick, 10000);
setInterval(loadWeather, 30 * 60000);
setInterval(loadVerse, 60 * 60000);
setInterval(drift, 90000);
setTimeout(function(){ $('hint').style.opacity = '0'; }, 12000);
</script>
</body>
</html>
"""


def _loc(country=0, city=0):
    country, city = int(country), int(city)
    if not 0 <= country < len(COUNTRIES):
        country = 0
    if not 0 <= city < len(COUNTRIES[country][3]):
        city = 0
    return [country, city]


def page(country=0, city=0):
    cfg = {
        "loc": _loc(country, city),
        "countries": COUNTRIES,
        "scenes": tv_scenes.build(),
        "stars": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice">'
                  + tv_scenes.stars() + '</svg>'),
    }
    data = json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")
    return PAGE_TEMPLATE.replace("__CONFIG__", data)


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


def lg_ipk(country=0, city=0):
    appinfo = {
        "id": LG_APP_ID, "version": APP_VERSION, "vendor": "Shashati", "type": "web",
        "main": "index.html", "title": "شاشتي", "icon": "icon.png", "largeIcon": "largeIcon.png",
        "bgColor": "#0a1230", "disableBackHistoryAPI": True, "resolution": "1920x1080",
    }
    pkginfo = {"app": LG_APP_ID, "id": LG_APP_ID, "loc_name": "شاشتي",
               "package_format_version": 2, "vendor": "Shashati", "version": APP_VERSION}
    files = {
        "appinfo.json": json.dumps(appinfo, ensure_ascii=False, indent=1).encode("utf-8"),
        "index.html": page(country, city).encode("utf-8"),
        "icon.png": png_icon(80),
        "largeIcon.png": png_icon(130),
    }
    base = "./usr/palm/applications/" + LG_APP_ID
    entries = [("./usr", None), ("./usr/palm", None), ("./usr/palm/applications", None), (base, None)]
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
    <access origin="*" subdomains="true"/>
    <tizen:setting screen-orientation="landscape" context-menu="disable" background-support="disable" encryption="disable" install-location="auto" hwkey-event="enable"/>
</widget>
""" % (APP_VERSION, TIZEN_APP_ID, TIZEN_PKG_ID)


def tizen_files(country=0, city=0):
    return {
        "config.xml": TIZEN_CONFIG.encode("utf-8"),
        "index.html": page(country, city).encode("utf-8"),
        "icon.png": png_icon(117),
    }


def tizen_unsigned_zip(country=0, city=0):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in tizen_files(country, city).items():
            z.writestr(n, d)
    return buf.getvalue()
