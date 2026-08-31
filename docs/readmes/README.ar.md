[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![إصدار Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![ترخيص MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

يحدّث ملفات الحفظ `EDIT00000000` الخاصة بـ SP Football Life 2026 وeFootball
PES 2021 باستخدام انتقالات حقيقية موثّقة وتحديثات لاعبين تمت مراجعتها.

> **إصدار تجريبي:** ما زالت الإصدارات وتوافق ملفات الحفظ قيد الاختبار.
>
> **إنشاء لاعبين جدد متوقف مؤقتًا.** ما زالت انتقالات اللاعبين الموجودين
> وتحديثاتهم المراجعة مدعومة. يتم تجاهل اللاعبين غير الموجودين أو غير الواضحين.
> عند امتلاء قائمة النادي المستهدف، يتم افتراضيًا تحرير لاعب احتياطي آمن حسب
> دوره؛ استخدم `--no-allow-overflow-release` لإبقاء القائمة دون تغيير.

## التوافق

تتطلب [قاعدة الحفظ المرفقة](../../base/EDIT00000000):

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

لا تتوافق مع UML أو إصدارات FL26 الأقدم أو التثبيتات التي لا تحتوي على تحديث
المنتخبات الوطنية. ابدأ مسيرة جديدة في Master League أو Become a Legend بعد
التثبيت.

## مُثبّت Windows

المُثبّت هو الخيار الأسهل:

1. نزّل واستخرج [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. أغلق اللعبة واختر **Fast** أو **Deep**.
3. أكّد مجلد Football Life ثم اختر **Download and install**.

يتحقق المُثبّت من الإصدار، وينشئ نسخة احتياطية، ويستبدل الملف بطريقة ذرّية.
لتحديث ملف حفظ موجود، اختر **Update my local save**، وحدد الملف، ثم اختر
**Apply update**.

المُثبّت غير موقّع. تحقّق من `FLDailyEditInstaller.zip` باستخدام الملف
`FLDailyEditInstaller.zip.sha256` المنشور في [أحدث إصدار](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
قبل تشغيله؛ قد يعرض Windows SmartScreen تحذيرًا.

للتثبيت اليدوي، نزّل [Fast ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
أو [Deep ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip).
أنشئ نسخة احتياطية، واستخرج `EDIT00000000`، ثم انسخه إلى:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

للتشغيل عند الطلب أو استخدام قائمة أندية مخصصة، أنشئ fork للمستودع واستخدم
**Run workflow** من تبويب Actions.

## ما الذي يتم تحديثه

- الانتقالات، وتحرير اللاعبين، والإعارات والعودة منها
- أرقام القمصان والتشكيلات وخطط اللعب المتأثرة بتغييرات القوائم
- تقارير الانتقالات وسجلات التدقيق
- ملفات حفظ جاهزة يومية عبر GitHub Actions

يتحقق البرنامج من نادي اللاعب الحالي ولا يستبدل رقم قميص مستخدمًا بالفعل.

قد تحتفظ ملفات PES21 النظيفة بأرقام قمصان في خانات قوائم فارغة. تُعرض هذه
كتحذيرات لا تمنع العملية ولا تمنع التحديث المحلي.

## التشغيل محليًا

مدعوم على macOS وLinux وWindows عبر WSL. يلزم Python 3.10 أو إصدار أحدث.

```bash
git clone https://github.com/gvoze32/fldailyedit.git
cd fldailyedit

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cd vendor/pesXdecrypter
make
cd ../..
```

## الأوامر الشائعة

```bash
# Preview transfers without writing a save
python run.py run --dry-run --edit-file base/EDIT00000000

# Apply all available transfers
python run.py run --window auto

# Rebuild from the bundled base
python run.py run --from-base --window auto

# Update a specific save in place
python run.py run --edit-file /path/to/EDIT00000000 --in-place

# Validate a save
python run.py validate --edit-file /path/to/EDIT00000000

# Validate Player Updates
python run.py players validate

# Apply reviewed Player Updates
python run.py players apply \
  --base-revision fl26-u2.2-national-squads \
  --edit-file /path/to/EDIT00000000 \
  --in-place

# Show command options
python run.py run --help
```

ينفّذ `run` الانتقالات فقط، بينما تمثل `players apply` عملية منفصلة. لاستخدامهما
معًا، شغّل الانتقالات أولًا ثم طبّق Player Updates على ملف الحفظ نفسه. استخدم
`python run.py <command> --help` لأدوات التدقيق والمقارنة والسجل والإصلاح.

## تحديثات اللاعبين

تُحفظ التحديثات المراجعة في `players/`، بملف JSON واحد لكل لاعب. يمكن تطبيق
سجلات `update` للاعبين الموجودين. أما سجلات `create` للاعبين الجدد فهي للمراجعة
فقط، ويرفضها `players apply` حاليًا بالخطأ
`create_temporarily_unavailable`.

لإرسال تحديث:

1. افتح [نموذج issue لتحديث اللاعب](../../.github/ISSUE_TEMPLATE/player-update.yml).
2. اكتب الاسم كما يظهر تمامًا في ملف Pes Retro Stats وأضف روابط الإثبات.
3. راجع المسودة، وشغّل `python run.py players validate`، وأرسل ملف JSON واحدًا للاعب.

## الأمان

- يتم التحقق من ملفات الحفظ قبل التغييرات وبعدها.
- ينشئ التشغيل المحلي نسخًا احتياطية دورية ويستخدم تشفيرًا ذريًا موثّقًا.
- يمنع قفل العملية الكتابة المتزامنة إلى المخرج نفسه.
- توقف البيانات غير المكتملة التشغيل، ويتم تجاهل المطابقات غير الواضحة.
- FotMob هو المصدر الأساسي؛ والمصادر الأخرى تكمل البيانات أو تؤكدها فقط.

## التطوير

```bash
pytest -v
```

## الترخيص

يتوفر FL Daily Edit بموجب [ترخيص MIT](../../LICENSE).
