[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python Sürümü](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Lisans: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

SP Football Life 2026 ve eFootball PES 2021 `EDIT00000000` kayıtlarını,
doğrulanmış gerçek transferler ve incelenmiş oyuncu güncellemeleriyle yeniler.

> **Beta:** Sürümler ve kayıt uyumluluğu hâlâ test ediliyor.
>
> **Yeni oyuncu oluşturma şimdilik devre dışı.** Mevcut oyuncular için transferler
> ve incelenmiş güncellemeler desteklenir. Bulunamayan veya belirsiz oyuncular
> atlanır. Dolu hedef kadrolarda varsayılan olarak role göre güvenli bir yedek
> çıkarılır; kadroyu değiştirmemek için `--no-allow-overflow-release` kullanın.

## Uyumluluk

[Birlikte gelen temel kayıt](../../base/EDIT00000000) şunları gerektirir:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

UML, eski FL26 sürümleri veya milli takım güncellemesi olmayan kurulumlarla
uyumlu değildir. Kurulumdan sonra yeni bir Master League veya Become a Legend
kariyeri başlatın.

## Windows yükleyici

Yükleyici en kolay seçenektir:

1. [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip) dosyasını indirin ve çıkarın.
2. Oyunu kapatıp **Fast** veya **Deep** seçin.
3. Football Life klasörünü doğrulayıp **Download and install** seçeneğine tıklayın.

Yükleyici sürümü doğrular, mevcut kaydı yedekler ve atomik olarak değiştirir.
Mevcut kaydı güncellemek için **Update my local save** seçeneğini seçin, kaydı
belirtin ve **Apply update** düğmesine basın.

Yükleyici imzasızdır. Çalıştırmadan önce `FLDailyEditInstaller.zip` dosyasını
[son sürümdeki](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
`FLDailyEditInstaller.zip.sha256` ile doğrulayın; Windows SmartScreen uyarı
verebilir.

Elle kurulum için [Fast ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
veya [Deep ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip)
indirin. Kaydınızı yedekleyin, `EDIT00000000` dosyasını çıkarın ve şuraya kopyalayın:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

İsteğe bağlı çalıştırma veya özel kulüp listesi için depoyu fork edin ve Actions
sekmesinden **Run workflow** seçeneğini kullanın.

## Neler güncellenir

- Transferler, serbest bırakmalar, kiralıklar ve kiralıktan dönenler
- Kadro değişikliklerinden etkilenen forma numaraları, dizilişler ve oyun planları
- Transfer raporları ve denetim günlükleri
- GitHub Actions üzerinden günlük hazır kayıtlar

Güncelleyici oyuncunun mevcut kulübünü kontrol eder ve başka bir oyuncunun kullandığı
forma numarasını değiştirmez.

## Yerel kurulum

macOS, Linux ve WSL üzerinden Windows desteklenir. Python 3.10 veya daha yeni bir
sürüm gerekir.

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

## Sık kullanılan komutlar

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

`run` yalnızca transfer uygular. `players apply` ayrı bir akıştır. İkisini birlikte
kullanmak için önce transferi çalıştırın, sonra aynı kayda Player Updates uygulayın.
Denetim, karşılaştırma, günlük ve onarım araçları için
`python run.py <command> --help` kullanın.

## Oyuncu güncellemeleri

İncelenmiş güncellemeler `players/` altında oyuncu başına bir JSON dosyasında
saklanır. Mevcut oyuncuların `update` kayıtları uygulanabilir. Yeni oyuncuların
`create` kayıtları yalnızca inceleme içindir ve `players apply` tarafından
`create_temporarily_unavailable` hatasıyla reddedilir.

Güncelleme önermek için:

1. [Oyuncu güncelleme issue formunu](../../.github/ISSUE_TEMPLATE/player-update.yml) açın.
2. Adı Pes Retro Stats profilinde göründüğü şekilde yazın ve kanıt URL'leri ekleyin.
3. Oluşturulan taslağı inceleyin, `python run.py players validate` çalıştırın ve tek bir oyuncu JSON dosyası gönderin.

## Güvenlik

- Kayıtlar değişikliklerden önce ve sonra doğrulanır.
- Yerel çalıştırmalar döngüsel yedek oluşturur ve doğrulanmış atomik şifreleme kullanır.
- Bir kilit aynı çıktıya eşzamanlı yazmayı engeller.
- Eksik kaynak verisi çalıştırmayı durdurur; belirsiz eşleşmeler atlanır.
- FotMob ana kaynaktır; diğer kaynaklar yalnızca tamamlar veya doğrular.

## Geliştirme

```bash
pytest -v
```

## Lisans

FL Daily Edit [MIT Lisansı](../../LICENSE) ile sunulur.
