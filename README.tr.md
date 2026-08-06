[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Bahasa Indonesia](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Bahasa_Indonesia-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python Sürümü](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Lisans: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

FL Daily Edit, gerçek dünyadaki transferleri bir `EDIT00000000` kayıt dosyasına uygulayarak SP Football Life 2026 ve eFootball PES 2021 kadrolarını günceller.

## Uyumluluk

Paketle gelen temel dosya **SP Football Life 2026** için tasarlanmıştır. Gereksinimler:

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

UML, daha eski FL26 sürümleri veya milli takım güncellemesi olmayan kurulumlarla uyumlu değildir. Kayıt dosyasını yükledikten sonra yeni bir Ana Lig (Master League) veya Efsane Olun (Become a Legend) kariyeri başlatılmalıdır.

[Dahil edilen temel](base/EDIT00000000), 27 Temmuz 2026 tarihli [Gondowan's Mid-Summer EDIT](https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/) dosyasıdır. 500'den fazla transfer, güncellenmiş reytingler, mevkiler, forma numaraları, kiralıktan dönenler, teknik direktörler, dizilişler ve küme düşme/yükselme değişikliklerini içerir. Yeni oyuncu oluşturmaz ve üçüncü ligden yükselen kulüpleri eklemez.

## Windows yükleyici

Windows yükleyici, yeni başlayanlar için önerilen seçenektir. Yükleyici arayüzü şu anda yalnızca İngilizce olarak sunulmaktadır. Güncel doğrulanmış indirmeler **yalnızca Football Life 2026 Update 2.2 + SmokePatch's National Squads Update** sürümünü hedefler. Vanilla eFootball PES 2021 algılanabilir, ancak eşleşen doğrulanmış bir temel yayımlanana kadar kurulum devre dışı kalır.

1. [FLDailyEditInstaller.exe](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.exe) dosyasını indirin.
2. Oyunu kapatın.
3. **Fast** veya **Deep** seçeneğini belirleyin. Bunlar ayrı güncelleme kapsamı seçenekleridir ve her biri oluşturulma zamanını gösterir.
4. Algılanan Football Life 2026 klasörünü doğrulayın veya gerekirse **Browse** seçeneğini kullanın.
5. **Download and install** seçeneğini belirleyin. Yükleyici indirmeyi doğrular, mevcut kayıt dosyasını yedekler ve atomik olarak değiştirir.

> [!WARNING]
> İlk yürütülebilir dosya imzasızdır; bu nedenle Windows SmartScreen bir uyarı gösterebilir. Devam etmeden önce indirilen dosyayı [en son sürümde](https://github.com/gvoze32/fldailyedit/releases/tag/latest) yayımlanan `FLDailyEditInstaller.exe.sha256` ile karşılaştırın.

Yükleyici olmadan elle kurulum için herkese açık [Fast sürüm ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) veya [Deep sürüm ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip) dosyasını indirin. `EDIT00000000` dosyasını çıkarın, mevcut kayıt dosyanızı yedekleyin ve çıkarılan dosyayı şuraya kopyalayın:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

İsteğe bağlı bir çalıştırma veya özel bir kulüp listesi için depoyu fork'layıp Actions sekmesinden **Run workflow** seçeneğini kullanabilirsiniz.

## Neleri günceller

- Transferler, serbest bırakmalar, kiralamalar ve kiralıktan dönüşler
- FotMob kadro verilerinden alınan güncel forma numaraları
- Mevcut FL26 kadrosuyla doğrulanan oyuncu kimlikleri
- Kadro değişikliklerinden etkilenen dizilişler ve oyun planları
- Transfer raporları ve JSON Lines denetim günlükleri
- GitHub Actions aracılığıyla günlük önceden derlenmiş kayıt dosyaları
- Açık Player Update komutlarıyla incelenen oyuncu oluşturmaları ve özellik düzeltmeleri

Güncelleyici, başka bir oyuncu tarafından kullanılan forma numarasının üzerine yazmaz. Ayrıca transfer uygulamadan önce oyuncunun mevcut kulübünü doğrular.

## Yol Haritası / Devam Eden Çalışmalar

Planlanan ve geliştirilmekte olan özellik:

1. **Arayüzde Yerel Güncelleme (ayrı çoklu temel dosya dağıtımının yerini alır)** — her yama için ayrı önceden derlenmiş temel dosyalar dağıtmak yerine, kullanıcıların kendi kayıt dosyaları üzerinde transfer sürecini çalıştırabilmesi için yükleyici arayüzüne yerel güncelleme modu eklenecektir (**SP Football Life 2026**, **vanilla eFootball PES 2021** ve **UML**).

## Güvenlik ve Sınırlamalar

- Yerel çalıştırmalar yedekleme oluşturur ve doğrulanmış atomik şifreleme kullanır.
- Kayıt dosyaları kadro değişikliklerinden önce ve sonra doğrulanır.
- İşlem kilidi, iki çalıştırmanın aynı anda aynı çıktıya yazmasını engeller.
- Eksik FotMob anlık görüntüleri, yarım kayıt oluşturmak yerine işlemi durdurur.
- Belirsiz oyuncu eşleşmeleri ve dolu kadrolar atlanır.
- Wikipedia, Sortitoutsi ve Transfermarkt tamamlayıcı kaynaklardır.
- `--allow-overflow-release` güvenli bir şekilde durur çünkü dahil edilen katalog her oyuncu için tam mevki ve GEN verisi içermez.

## Yerel Kurulum ve Çalıştırma

macOS, Linux ve WSL üzerinden Windows desteklenmektedir. Python 3.10 veya daha yeni bir sürüm gereklidir.

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

## Sık Kullanılan Komutlar

```bash
# Kayıt yazmadan değişiklikleri önizleme
python run.py run --dry-run --edit-file base/EDIT00000000

# Mevcut bir kayıt dosyasını doğrulama
python run.py validate --edit-file base/EDIT00000000

# Oyuncu güncellemelerini temel sürüme karşı doğrulama
python run.py players validate

# İncelenen oyuncu güncellemelerini çıktı dosyasına uygulama
python run.py players apply \
  --base-revision fl26-u2.2-national-squads \
  --edit-file output/EDIT00000000 \
  --in-place

# Bugüne kadar geçerli tüm transferleri uygulama
python run.py run --window auto

# Dahil edilen temel dosyadan yeniden derleme
python run.py run --from-base --window auto

# Belirli bir kayıt dosyasını doğrudan güncelleme
python run.py run --edit-file /path/to/EDIT00000000 --in-place

# Tüm çalıştırma seçeneklerini görüntüleme
python run.py run --help
```

| Komut | Amaç |
|---|---|
| `run` | Yalnızca doğrulanmış transferleri uygular |
| `players validate` | Tüm Player Update dosyalarını temel sürüme göre doğrular |
| `players apply` | İncelenen Player Update dosyalarını kayıt dosyasına uygular |
| `log` | Son uygulanan transferleri gösterir |
| `inspect` | Takımları, oyuncu sayılarını ve dosya ofsetlerini inceler |
| `validate` | Kadro kayıtlarını ve oyun planı eşlemelerini kontrol eder |
| `repair` | Referans kayıtları kullanarak eski bir temeli onarır |


`run` yalnızca transferleri yönetir; Player Update dosyalarını yüklemez veya uygulamaz. Her iki iş akışını birleştirmek için önce transfer komutunu çalıştırın, ardından aynı dosyada `players apply --in-place` komutunu yürütün.

## Oyuncu Güncellemeleri (Player Updates)

İncelenen her güncelleme, `players/` altında oyuncu başına bir şema-v2 JSON dosyasıdır. `operation` (`create` veya `update`), yaşam döngüsü (`active`, `upstreamed` veya `retired`), geçerli temel sürümler (`applies_to`), oyuncu kimliği, Pes Retro Stats profil UUID'si, kanıtlar ve incelenen PES verilerini kaydeder.

### Kolay Issue Yolu

1. [Oyuncu güncelleme issue formunu](.github/ISSUE_TEMPLATE/player-update.yml) açın. `Player name` kısmını `Pes Retro Stats` profiliyle birebir aynı girin, kanıt URL'lerini ekleyin ve `generate-player-draft` etiketini bekleyin.
2. Yapılandırılan iş akışı profili çeker ve şema-v2 `players/<player-slug>.json` önerisini içeren bir taslak PR açar.
3. Yeni oluşturma için yalnızca kaynakta bulunmayan oyun içi değerler `draft.missing` içinde listelenir. Güncelleme için ise sadece gerçek farklar üretilir.
4. Geliştiriciler öneriyi inceler. CI testi yalnızca bir oyuncu JSON dosyası eklendiğinde ve anlamsal doğrulayıcı başarılı olduğunda kabul eder.
5. PR'ın birleştirilmesi (merge) nihai onaydır.

### Doğrudan Tek Dosyalı PR Yolu

Deneyimli katkıcılar taslak aşamasını atlayıp doğrudan tamamlanmış bir `players/<player-slug>.json` dosyası ekleyen bir PR açabilir. Bilgileri ekledikten sonra inceleme istemeden önce `python run.py players validate` komutunu çalıştırın.

Uygulama her zaman açık bir komuttur ve `data/base_manifest.json` içindeki tam sürümü gerektirir.

### Sürüm Yaşam Döngüsü

Resmi temel değiştiğinde `base/EDIT00000000` ve `data/base_manifest.json` dosyalarını birlikte güncelleyin. Geçmiş güncellemeleri `players/` altında saklayın. İncelemeden sonra geçerliyse yeni sürümü ekleyin, resmi temele dahil edildiyse `upstreamed`, artık geçerli değilse `retired` olarak işaretleyin.

Yaygın `run` seçenekleri:

| Seçenek | Amaç |
|---|---|
| `--deep` | Yerel olarak dizine eklenen tüm FotMob kulüplerini çeker |
| `--club "Chelsea,Arsenal"` | Yalnızca seçili kulüplerle sınırlar |
| `--window auto` | Bugüne kadar geçerli olan tarihli transferleri oynatır |
| `--window summer` | En son 1 Haziran - 30 Eylül aralığını kullanır |
| `--window winter` | Seçilen yılın Ocak - Şubat aralığını kullanır |
| `--since YYYY-MM-DD` | Alt tarih sınırını manuel olarak belirler |
| `--dry-run` | Kayıt yazmadan değişiklikleri planlar |
| `--from-base` | `base/EDIT00000000` dosyasından başlar |
| `--fotmob-only` | Ek kaynaklar olmadan çalışır |

## Transfer Kaynakları

FotMob ana transfer geçmişini ve kadro meta verilerini sağlar. Wikipedia sezon listeleri, SortitoutSI transfer bildirimleri ve Transfermarkt kayıtları transferleri tamamlar veya doğrular. Pes Retro Stats profilleri taslaklar için kaynak sağlar.

## Geliştirme

Test paketini çalıştırmak için:

```bash
pytest -v
```

## Lisans

FL Daily Edit, [MIT Lisansı](LICENSE) kapsamında sunulmaktadır.
