[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python Sürümü](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Lisans: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

FL Daily Edit, gerçek dünyadaki transferleri bir `EDIT00000000` kayıt dosyasına uygulayarak SP Football Life 2026 ve eFootball PES 2021 kadrolarını günceller.

> **Yeni oyuncu oluşturma şu anda tüm mutasyon yollarında devre dışıdır.
> `create` tanımları şema ve inceleme için yüklenebilir olmaya devam eder;
> ancak `--allow-create` yalnızca ayrılmış bir uyumluluk seçeneği olarak tutulur.
> `PlayerAppearance.bin` ayrılmış bir girdi olarak kalır; create tanımları
> `create_temporarily_unavailable` nedeniyle reddedilir.**
>
> Kayıt dosyasında zaten bulunan oyuncular için transferler ve incelenmiş güncellemeler
> desteklenir. Eksik oyuncular atlanır. Role dayalı overflow release varsayılan olarak
> etkindir; dolu kadroyu değiştirmemek için `--no-allow-overflow-release` kullanın.

> [!WARNING]
> **Beta bildirimi:** FL Daily Edit, depo verileri ve oluşturulan sürümler hâlâ test ediliyor. Her oyun/kayıt dosyası yapılandırmasında çalışmayabilir; bazı koşullar henüz desteklenmiyor.

## Uyumluluk

Dahil edilen temel sürüm **SP Football Life 2026** için hedeflenmiştir. Gereksinimler:

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

UML, daha eski FL26 sürümleri veya milli takım güncellemesi olmayan kurulumlarla uyumlu değildir. Kayıt dosyasını yükledikten sonra yeni bir Ana Lig veya Efsane Olun kariyeri başlatın.

[Dahil edilen temel](../../base/EDIT00000000), 22 Ağustos 2026 tarihli
[Gondowan's EDIT](https://www.reddit.com/r/SPFootballLife/comments/1vvh129/release_gondowans_edit_file_22082026_latest/) dosyasıdır.
Tüm ligler için 22/08/2026 son dakika transferlerini, 600'den fazla oyuncunun
puan değişikliklerini, birinci/ikinci lig yükselme ve düşme değişikliklerini,
boy ve mevki düzeltmelerini, isim ve forma numarası güncellemelerini, mevcut
teknik direktör değişikliklerini ve en iyi oyunculara göre sıralanmış otomatik
kadro dizilişlerini içerir. Yeni oyuncu oluşturmaz veya 3. ligden yükselen takımları eklemez.

## Windows yükleyici

Windows yükleyicisi, yeni başlayanlar için önerilen seçenektir. Yükleyici arayüzü şu anda yalnızca İngilizce olarak sunulmaktadır. Geçerli doğrulanmış indirmeler **yalnızca Football Life 2026 Update 2.2 + SmokePatch's National Squads Update içindir**. Vanilla eFootball PES 2021 algılanabilir, ancak eşleşen doğrulanmış bir temel yayımlanana kadar kurulum devre dışı kalır.

1. [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip) dosyasını indirin ve ayıklayın.
2. Oyunu kapatın.
3. **Fast** veya **Deep** seçeneğini belirleyin. Bunlar ayrı güncelleme kapsamı seçenekleridir ve her biri oluşturulma zamanını görüntüler.
4. Algılanan Football Life 2026 klasörünü onaylayın veya gerekirse **Browse** düğmesini kullanın.
5. **Download and install** seçeneğini belirleyin. Yükleyici indirmeyi doğrular, mevcut kaydınızı yedekler ve atomik olarak yenisiyle değiştirir.

**GUI aracılığıyla mevcut bir kaydı güncelleme:** Yükleyici, önceden derlenmiş bir
sürüm yüklemek yerine, kullanıcı tarafından seçilen standart düzendeki bir
`EDIT00000000` dosyasını da güncelleyebilir. **Update my local save** seçeneğini
belirleyin, algılanan bir konumu seçin veya **Browse** kullanın, **Fast** veya
**Deep** seçeneğini belirleyin ve inceledikten sonra **Apply update** düğmesine
basın. Sihirbaz değiştirmeden önce kaydı doğrular, aynı konumda bir yedek
oluşturur ve ilerlemeyi, sonucu veya tanılamayı görüntüler. Yerel uygunluk
SPFL/PES/UML etiketine bağlı değildir ve bu yol uzak bir ön derleme indirmez.
İsteğe bağlı bu harici SPFL katalogları mevcut olmadığında, yerel eşleştirici
seçilen kayda gömülü oyuncu ve takım adlarını kullanır ve paketlenmiş yerel
güncelleme yolunun bunlar olmadan da çalışmasını sağlar.

> [!WARNING]
> Yükleyici yürütülebilir dosyası imzasızdır; bu nedenle çalıştırdığınızda Windows SmartScreen bir uyarı gösterebilir. Devam etmeden önce indirilen `FLDailyEditInstaller.zip` dosyasını [en son sürümde](https://github.com/gvoze32/fldailyedit/releases/tag/latest) yayımlanan `FLDailyEditInstaller.zip.sha256` ile karşılaştırın.
> Windows yükleyiciyi Smart App Control aracılığıyla engellerse, **Settings → Privacy & security → Windows Security → App & browser control → Smart App Control settings** bölümünü açın ve **Off** konumuna getirin. Alternatif olarak, indirilen dosyaya sağ tıklayın, **Properties** menüsünü açın ve varsa **Unblock** onay kutusunu işaretleyin.

Yükleyici olmadan manuel kurulum için genel [Hızlı sürüm ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) veya [Kapsamlı sürüm ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip) dosyasını indirin. `EDIT00000000` dosyasını çıkartın, mevcut dosyanızı yedekleyin ve çıkartılan dosyayı şuraya kopyalayın:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

İsteğe bağlı bir çalıştırma veya özel bir kulüp listesi kullanmak için depoyu çatallayın (fork) ve Actions sekmesindeki **Run workflow** özelliğini kullanın.

## Neler Güncellenir

- Transferler, serbest bırakmalar, kiralamalar ve kiralıktan dönenler
- FotMob kadro verilerinden sağlanan uygun forma numaraları
- Mevcut FL26 kadrosuna göre doğrulanan oyuncu kimlikleri
- Kadro değişikliklerinden etkilenen dizilişler ve oyun planları
- Transfer raporları ve JSON Lines denetim günlükleri
- GitHub Actions aracılığıyla günlük olarak önceden derlenmiş kayıt dosyaları
- Açık Player Update komutlarıyla incelenen oyuncu oluşturma ve özellik düzeltmeleri

Güncelleyici, kadrodaki başka bir oyuncu tarafından kullanılan bir forma numarasının üzerine yazmaz. Ayrıca transferi uygulamadan önce oyuncunun mevcut kulübünü doğrular.

## Yol Haritası / Şimdilik tamamlandı

Mevcut yol haritasındaki tüm maddeler tamamlandı. Sıradaki faydalı fikri bekliyoruz.

## Güvenlik ve Sınırlamalar

- Yerel çalıştırmalar döngüsel yedekler oluşturur ve doğrulanmış atomik şifreleme kullanır.
- Kayıt dosyaları, kadro değişikliklerinden önce ve sonra doğrulanır.
- İşlem kilidi, iki örneğin aynı anda aynı çıktıya yazmasını engeller.
- Eksik FotMob anlık görüntüleri, bozuk bir kayıt oluşturmak yerine çalışmayı durdurur.
- Belirsiz oyuncu eşleşmeleri ve kaynak kulüp uyumsuzlukları atlanır.
- Dolu hedef kadrolarda role dayalı overflow release varsayılan olarak etkindir.
  İlk on bir ve maç kadrosu yedekleri korunur, en derindeki native reserve tercih edilir
  ve native aday varken created oyuncular korunur. Ability/OVR kullanılmaz;
  `--no-allow-overflow-release` ile kapatılabilir.
- Wikipedia, Sortitoutsi ve Transfermarkt tamamlayıcı kaynaklardır. Bunlardan birindeki kesinti, tam bir FotMob anlık görüntüsünü geçersiz kılmaz.

**Transfer Güncellemeleri ve Player Updates**

Bunlar ayrı iş akışlarıdır:

- `run`, kayıt dosyasında zaten bulunan oyuncular için transferleri işler. Bir hedef kulüp
  doluysa, role dayalı overflow adayı varsayılan olarak serbest bırakılır;
  `--no-allow-overflow-release` ile transfer atlanır.
- `players apply`, incelenmiş özellik değişikliklerini uygular. Mevcut oyuncular için
  `update` tanımları desteklenir.
- Yeni oyuncular için `create` tanımları yalnızca şema ve inceleme amacıyla
  yüklenebilir ve incelenebilir. `players apply` tüm create mutasyonlarını
  `create_temporarily_unavailable` ile reddeder; `--allow-create` ve
  `PlayerAppearance.bin` yalnızca ayrılmış uyumluluk girdileri olarak tutulur.
- Fast ve Deep senkronizasyon iş akışları `--no-allow-create` kullanır; yerel
  `players apply` de create kapalı aynı davranışı korur.
- Transfer komutları role dayalı overflow release özelliğini varsayılan olarak
  açar. Hedef kadro doluysa transferin girebilmesi için seçilen güvenli reserve
  serbest bırakılır; kapatmak için yalnızca
  `--no-allow-overflow-release` kullanın. Selector, native base oyuncusu varken
  ilk takım rollerini, maç günü yedeklerini, aktarılan/korunan ID'leri ve
  ayrılmış aralıktaki created oyuncuları korur.

## Yerel Kurulum

Yerel kurulum; macOS, Linux ve WSL üzerinden Windows üzerinde desteklenmektedir. Python 3.10 veya üstü gereklidir.

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
| `run` | Yalnızca doğrulanmış transferleri uygula |
| `players validate` | Tüm Player Update'leri orijinal temele karşı doğrula |
| `players apply` | İncelenen Player Update'leri açıkça bir kayda uygula |
| `base-audit` | Etkin Player Update'leri, hedefleri ve loan parent'ı base ile karşılaştırır |
| `base-refresh` | Yerel veya HTTPS base adayını doğrular ve isteğe bağlı yayımlar |
| `usage-import` | Çevrimdışı oyuncu kullanım CSV verisini release policy'ye ekler |
| `players apply --preflight` | İncelenen create hedeflerini ve güvenlik girdilerini yazmadan gösterir |
| `log` | Son uygulanan transferleri göster |
| `inspect` | Takımları, oyuncu sayılarını ve kayıt dosyası ofsetlerini incele |
| `validate` | Kadro kayıtlarını ve oyun planı eşlemelerini kontrol et |
| `repair` | Referans kayıtları kullanarak eski bir temeli onar |
| `audit` | Kayıt ve yerel Player/Team meta verilerini salt okunur denetle |
| `compare` | İki yerel CPK meta veri varyantını salt okunur karşılaştır |

`run` yalnızca transferleri işler: Player Update'leri asla yüklemez veya uygulamaz. Her iki iş akışını birleştirmek için önce bir çıktı kaydında transfer komutunu çalıştırın, ardından aynı dosyada `players apply --in-place` komutunu çalıştırın.

## Player Updates

İncelenen her Player Update, `players/` altında oyuncu başına şema-v2 formatında eksiksiz bir JSON dosyasıdır. Bir işlemi (`operation`: `create` veya `update`), bir yaşam döngüsünü (`active`, `integrated` veya `superseded`), `applies_to` içindeki kesin temel sürümleri, kararlı oyuncu kimliğini ve Pes Retro Stats UUID/profil kaynağını, kaynak gösterilen kanıtları ve incelenen PES verilerini kaydeder. Oluşturma güncellemeleri, önerilen eksiksiz bir oyuncu kaydı ve hedef kadro verilerini içerir. Mevcut oyuncu güncellemeleri, yalnızca doğrulanmış temelden farklı olan desteklenen değerleri içerir; her değişiklik birebir `from` ve `to` değerlerini kaydeder.

> **Yaşam döngüsü notu:** `superseded`, oyuncunun kariyer durumu değil Player Update durumudur. Güncellemenin seçilen temel sürüm için artık geçerli olmadığı anlamına gelir.
`create` kayıtları yalnızca inceleme için şema tarafından desteklenir. CLI ve
doğrudan API create mutasyonlarının tamamı devre dışıdır; `--allow-create`
ayrılmış uyumluluk seçeneği olarak tutulur ve
`create_temporarily_unavailable` döndürür. Transfer overflow release varsayılan
olarak açıktır; kapatmak için `--no-allow-overflow-release` kullanın. Eksik veya
geçersiz güvenlik meta verisi kayıt dosyasını değiştirmez.
Desteklenen güncelleme grupları yetenekler, mevki yetkinliği, oyun tarzı, oyuncu becerileri, COM tarzları, uyruk, fiziksel/temel ayarlar ve kayıtlı mevkidir.
- Oluşturulan GEN (OVR) inceleme değerleri, yayınlanan PES 2021 formülüne dayanan deterministik hesaplamalardır. Oyun çalışma zamanının bağımsız bir garantisi değil, bir eşitlik yardımcısıdır; önerilen yetenek değerleri yine de inceleme gerektirir.
- Önceki OVR model tanımlayıcısıyla oluşturulan oyuncu taslakları, doğrulamadan önce yeniden oluşturulmalıdır; v1'den v2'ye örtük bir geçiş yoktur.

### Kolay Issue Yolu

1. [Oyuncu güncelleme issue formunu](../../.github/ISSUE_TEMPLATE/player-update.yml) açın. `Player name` kısmını `Pes Retro Stats profile` ile birebir aynı girin, kanıt URL'lerini ekleyin ve `generate-player-draft` etiketini bekleyin.
2. Yapılandırılan iş akışı profili çeker ve şema-v2 `players/<player-slug>.json` önerisini içeren bir taslak PR açar. Profil verilerinden kaynak anlık görüntüsünü, kimliği, fiziksel ayarları, mevki verilerini, yetenekleri, oyun tarzını, oyuncu becerilerini ve COM tarzlarını türetir.
3. Yeni oluşturma için yalnızca kaynakta bulunmayan oyun içi değerler `draft.missing` içinde listelenir: kimlik ve oyuncu için PES ID'leri ve forma baskı adları, takım ID'si ve adı, uyruk ID'si, ten rengi ve iris rengi. Bir katkıcı veya bakımcı bunları tamamlamalıdır. Güncelleme için ise oluşturucu, doğrulanmış temelde oyuncuyu bulur ve yalnızca gerçek `from`/`to` farklarını üretir. PES 2021 tarafından desteklenmeyen kaynak mevkiler (örneğin `RWB`), yeniden eşlenmek yerine doğrudan çıkarılır; buna kayıtlı mevki değişikliği de dahildir.
4. Katkıcılar ve bakımcılar oluşturulan her değeri onaylanmamış bir öneri olarak inceler. CI testi yalnızca bir oyuncu JSON dosyası eklendiğinde veya değiştirildiğinde ve paylaşılan anlamsal doğrulayıcı başarılı olduğunda Player Update'i kabul eder.
5. PR'ın birleştirilmesi (merge) nihai insan onayıdır. JSON dosyasında ayrı bir `approved` işareti yoktur.

Oluşturulan her önerinin tam dosya doğrulamasından geçememesi beklenir. Oluşturulan kanıtları tam şema v2'ye dönüştürmek için yalnızca taslakta kullanılan `evidence.current_team`, `evidence.issue_number` ve `evidence.issue_url` alanlarını kaldırın; standart `evidence.profile_url`, incelenen `evidence.proof_urls` ve `evidence.effective_date` alanlarını koruyun; ve incelenmiş, boş olmayan bir `evidence.reason` ekleyin. Standart profil UUID'sini `identity.pes_retro_stats_id` olarak saklayın ve `pes` içinde yalnızca incelenen oynanış değerlerini tutun. Oluşturma işlemi için `draft.missing` içinde belirtilen tüm oyun içi alanları tamamlayın. Oluşturulan oyuncu PES ID'leri benzersiz ve en az `0x100000` (1.048.576) olmalıdır; öneri ayırıcı bu ayrılmış aralıkta kalır.
Ardından tam doğrulamadan önce, yalnızca inceleme amaçlı oluşturulan taslak meta verileri olan en üst düzey `source` ve `draft` nesnelerini kaldırın.

### Doğrudan Tek Dosyalı PR Yolu

Deneyimli katkıcılar taslak aşamasını atlayıp doğrudan tam bir `players/<player-slug>.json` dosyası ekleyen veya değiştiren bir PR açabilir. `identity` ve `evidence` içinde standart UUID/profil kaynağını, kaynak gösterilen kanıtları, incelenen PES değerlerini, beklenen güncelleme referans noktalarını, yaşam döngüsünü ve tam temel sürümü sağlayın; ardından inceleme istemeden önce `python run.py players validate` komutunu çalıştırın. Oluşturulan taslağın en üst düzey `source` veya `draft` meta verilerini dahil etmeyin. Bu PR'a başka kod veya dokümantasyon değişikliği dahil etmeyin.

Uygulama her zaman açık bir komuttur ve `data/base_manifest.json` içindeki tam sürümü gerektirir; sürüm uyuşmazlığı hedef kayıt dosyasının şifresi çözülmeden önce başarısız olur.

### Sürüm Yaşam Döngüsü

Resmi temel değiştiğinde `base/EDIT00000000` ve `data/base_manifest.json` dosyalarını birlikte güncelleyin. Geçmiş Player Update'leri `players/` altında saklayın; yalnızca sürüm değiştiği için onları silmeyin. `applies_to` listesi yeni sürümü içermeyen etkin bir Player Update devre dışı kalır: doğrulama `needs_review` bildirir ve uygulama işlemi onu atlar. İncelemeden sonra, Player Update hâlâ geçerliyse yeni sürümü ekleyin, resmi temel değişikliği içeriyorsa `integrated` olarak işaretleyin veya artık geçerli değilse `superseded` olarak belirleyin.

Yaygın `run` seçenekleri:

| Seçenek | Amaç |
|---|---|
| `--deep` | Yerel olarak dizine eklenen tüm FotMob kulüplerini getirir |
| `--club "Chelsea,Arsenal"` | Çalıştırmayı seçili kulüplerle sınırlar |
| `--window auto` | Bugüne kadar geçerli olan tüm tarihli transferleri yeniden oynatır |
| `--window summer` | En son 1 Haziran - 30 Eylül aralığını kullanır |
| `--window winter` | Seçilen yılın Ocak-Şubat aralığını kullanır |
| `--since YYYY-MM-DD` | Alt tarih sınırını manuel olarak belirler |
| `--dry-run` | Kayıt yazmadan değişiklikleri planlar |
| `--from-base` | `base/EDIT00000000` dosyasından başlar |
| `--fotmob-only` | Ek transfer kaynakları olmadan çalışır |
| `--release-policy PATH` | Kulübe göre korunan oyuncuları ve çevrimdışı kullanım sayaçlarını yükler |
| Forma numaraları | Güncel forma numaralarını her transfer çalışmasında varsayılan olarak senkronize eder |

`--from-base` olmadan, normal bir çalıştırma son doğrulanan çıktıdan devam eder. Bu, daha sonra zamanlanmış bir çalıştırma birikmiş geçmişi tekrar okuduğunda uygulanan transferlerin kaybolmasını önler.

## Transfer Kaynakları

FotMob ana transfer geçmişini ve kadro meta verilerini sağlar. Wikipedia sezon listeleri, SortitoutSI transfer bildirimleri ve doğrulanmış tarihli Transfermarkt kayıtları transfer yollarını tamamlar veya doğrular. Pes Retro Stats profilleri taslaklar için kaynaklı ancak onaylanmamış öneriler sağlar.

Farklı kaynaklardan gelen kayıtlar tarihleri, kimlikleri, alıntıları veya kanıt bağlantıları atılmadan uzlaştırılır. Tarihsiz, gelecekte geçerli olacak, çelişkili veya belirsiz olaylar kayıt dosyasını tek başına güncelleyemez.

Oyuncu eşleştirme, kaynak takım kadrosundan başlar ve hedef takım kadrosunu birim kuvvetli (idempotent) bir geri dönüş olarak kullanır. Mevki, uyruk ve yaş yalnızca bu bilgiler mevcut olduğunda dikkate alınır.

## Geliştirme

Test paketini çalıştırmak için:

```bash
pytest -v
```

Test paketi; kayıt ayrıştırma ve doğrulamayı, transfer uzlaştırmasını, kadro planlamasını, kiralama geçmişini, oyuncu eşleştirmesini, kadro sınırlarını, raporları, yedeklemeleri ve işlem kilitlerini kapsar.

## Lisans

FL Daily Edit, [MIT Lisansı](../../LICENSE) kapsamında sunulmaktadır.
