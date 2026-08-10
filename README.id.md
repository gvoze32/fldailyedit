[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

FL Daily Edit memperbarui skuad SP Football Life 2026 dan eFootball PES 2021
dengan menerapkan transfer dunia nyata ke file save `EDIT00000000`.

> **Batasan saat ini — pembuatan pemain baru sementara dinonaktifkan karena
> kami sedang memperbaiki dan memverifikasi masalah save/appearance.**
>
> Transfer pemain yang sudah ada di save dan pembaruan pemain yang sudah ditinjau
> tetap didukung. Pemain yang belum ada akan dilewati, dan roster tujuan yang
> penuh secara default dilewati tanpa melepas pemain lama.

## Kompatibilitas

Base yang disertakan ditujukan untuk **SP Football Life 2026**. Base ini memerlukan:

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

Base ini tidak kompatibel dengan UML, versi FL26 yang lebih lama, atau instalasi
tanpa pembaruan skuad nasional. Mulailah karier Master League atau Become a Legend
baru setelah memasang save ini.

[Base yang disertakan](base/EDIT00000000) adalah
[Gondowan's Mid-Summer EDIT](https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/),
bertanggal 27 Juli 2026. Base ini mencakup lebih dari 500 transfer, pembaruan rating,
posisi, nomor skuad, pemain yang kembali dari peminjaman, manajer, susunan pemain,
serta perubahan promosi atau degradasi. Base ini tidak membuat pemain atau
menambahkan klub promosi dari divisi ketiga.

## Installer Windows

Installer Windows adalah pilihan yang disarankan untuk pemula. Antarmuka installer saat ini hanya tersedia dalam bahasa Inggris. Unduhan tervalidasi saat ini hanya mendukung **Football Life 2026 Update 2.2 + SmokePatch's National Squads Update**. Deteksi untuk vanilla eFootball PES 2021 sudah tersedia, tetapi pemasangan tetap dinonaktifkan hingga base tervalidasi yang sesuai diterbitkan.

1. Unduh [FLDailyEditInstaller.exe](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.exe).
2. Tutup gim.
3. Pilih **Fast** atau **Deep**. Keduanya adalah pilihan cakupan pembaruan yang terpisah, dan masing-masing menampilkan waktu pembuatannya.
4. Konfirmasikan folder Football Life 2026 yang terdeteksi, atau gunakan **Browse** bila perlu.
5. Pilih **Download and install**. Installer memverifikasi unduhan, mencadangkan save saat ini, lalu menggantinya secara atomik.

**Memperbarui save yang ada melalui GUI:** Installer juga dapat memperbarui
`EDIT00000000` ber-layout umum yang dipilih pengguna, bukan memasang rilis
prebuilt. Pilih **Update my local save**, pilih lokasi yang terdeteksi atau
gunakan **Browse**, pilih **Fast** atau **Deep**, lalu tinjau dan pilih
**Apply update**. Wizard memvalidasi save sebelum perubahan, membuat backup di
tempat, dan menampilkan progres, hasil, atau diagnostik. Kelayakan lokal tidak
bergantung pada label SPFL/PES/UML, dan jalur ini tidak mengunduh rilis remote
prebuilt.
Saat katalog SPFL eksternal opsional tersebut tidak tersedia, pencocokan lokal
beralih ke nama pemain dan klub yang tersimpan di save terpilih, sehingga jalur
pembaruan lokal dalam paket dapat berjalan tanpa katalog tersebut.


> [!WARNING]
> Executable awal belum ditandatangani, sehingga Windows SmartScreen mungkin menampilkan peringatan. Sebelum melanjutkan, bandingkan file unduhan dengan `FLDailyEditInstaller.exe.sha256` yang diterbitkan pada [rilis terbaru](https://github.com/gvoze32/fldailyedit/releases/tag/latest).
> Jika Windows memblokir installer melalui Smart App Control, buka **Settings → Privacy & security → Windows Security → App & browser control → Smart App Control settings**, lalu ubah ke **Off**. Alternatifnya, klik kanan file yang diunduh, buka **Properties**, lalu centang **Unblock** jika tersedia.

Untuk pemasangan manual tanpa installer, unduh [ZIP rilis Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) atau [ZIP rilis Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip) yang bersifat publik. Ekstrak `EDIT00000000`, cadangkan save Anda saat ini, lalu salin file tersebut ke:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Untuk menjalankannya sesuai permintaan atau menggunakan daftar klub khusus, fork repositori ini dan gunakan **Run workflow** dari tab Actions.

## Yang diperbarui

- Transfer, pelepasan, peminjaman, dan pemain yang kembali dari peminjaman
- Nomor skuad yang tersedia berdasarkan data skuad FotMob
- Identitas pemain yang diperiksa terhadap roster FL26 saat ini
- Susunan pemain dan game plan yang terdampak oleh perubahan roster
- Laporan transfer dan log audit JSON Lines
- Save siap pakai harian melalui GitHub Actions
- Pembuatan pemain dan koreksi atribut yang telah ditinjau melalui perintah Pembaruan Pemain secara eksplisit

Updater tidak menimpa nomor seragam yang sudah digunakan oleh anggota skuad lain.
Updater juga memeriksa klub pemain saat ini sebelum menerapkan perpindahan.

## Roadmap / Selesai untuk saat ini

Semua item roadmap saat ini telah selesai. Kami menunggu ide berguna berikutnya.

## Keamanan dan keterbatasan

- Eksekusi lokal membuat cadangan bergilir serta menggunakan enkripsi atomik yang terverifikasi.
- Save divalidasi sebelum dan sesudah perubahan roster.
- Process lock mencegah dua proses menulis output yang sama secara bersamaan.
- Snapshot FotMob yang tidak lengkap membatalkan proses alih-alih menghasilkan save parsial.
- Kecocokan pemain yang ambigu dan ketidakcocokan klub sumber akan dilewati.
- Roster tujuan yang penuh secara default akan dilewati; updater transfer tidak
  pernah melepas pemain lama secara otomatis.
- `--allow-overflow-release` adalah opsi eksplisit khusus transfer. Opsi ini
  membutuhkan metadata posisi dan OVR yang lengkap dan dapat melepas kandidat
  yang aman untuk menyediakan slot. Jika metadata tidak lengkap, proses berhenti
  secara fail-closed.
- Wikipedia, Sortitoutsi, dan Transfermarkt merupakan sumber tambahan. Gangguan
  pada salah satu sumber tersebut tidak membatalkan snapshot FotMob yang lengkap.

**Transfer dan Player Updates adalah alur yang berbeda**

- `run` memproses transfer pemain yang sudah ada di save. Jika klub tujuan penuh,
  transfer tersebut dilewati; transfer aman lainnya dalam run yang sama tetap
  dapat diterapkan.
- `players apply` menerapkan perubahan atribut yang sudah ditinjau. Spec
  `update` untuk pemain yang sudah ada tetap didukung.
- Spec `create` pemain baru tetap dapat dimuat dan ditinjau, tetapi sementara
  dinonaktifkan setelah pengujian keamanan appearance/save. Penerapannya
  menghasilkan `create_temporarily_unavailable` dan save tidak berubah sedikit pun.

## Jalankan secara lokal

Penyiapan lokal didukung di macOS, Linux, dan Windows melalui WSL. Python 3.10
atau yang lebih baru diperlukan.

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

## Perintah umum

```bash
# Preview changes without writing a save
python run.py run --dry-run --edit-file base/EDIT00000000

# Validate an existing save
python run.py validate --edit-file base/EDIT00000000

# Validate one-file-per-player updates against the pristine base revision
python run.py players validate

# Apply reviewed Player Updates explicitly to an existing output save
python run.py players apply \
  --base-revision fl26-u2.2-national-squads \
  --edit-file output/EDIT00000000 \
  --in-place

# Apply all effective transfers available through today
python run.py run --window auto

# Rebuild from the bundled base
python run.py run --from-base --window auto

# Update a specific save in place
python run.py run --edit-file /path/to/EDIT00000000 --in-place

# Show every run option
python run.py run --help
```

| Perintah | Kegunaan |
|---|---|
| `run` | Hanya menerapkan transfer yang terverifikasi |
| `players validate` | Memvalidasi semua Pembaruan Pemain terhadap base asli |
| `players apply` | Menerapkan Pembaruan Pemain yang telah ditinjau secara eksplisit ke satu save |
| `log` | Menampilkan transfer yang baru diterapkan |
| `inspect` | Memeriksa tim, jumlah pemain, dan offset save |
| `validate` | Memeriksa pendaftaran roster dan pemetaan game plan |
| `repair` | Memperbaiki base lama menggunakan save referensi |

`run` hanya menangani transfer: perintah ini tidak pernah memuat atau menerapkan
Pembaruan Pemain. Untuk menggabungkan kedua alur kerja, pertama-tama jalankan
perintah transfer terhadap save output, lalu jalankan
`players apply --in-place` terhadap save yang sama.

## Pembaruan Pemain

Setiap Pembaruan Pemain yang telah ditinjau berupa satu file JSON schema-version-2
yang lengkap per pemain di dalam `players/`. File tersebut mencatat `operation`
(`create` atau `update`), lifecycle (`active`, `upstreamed`, atau `retired`), revisi
base `applies_to` yang tepat, identitas pemain yang stabil beserta provenance
UUID/profil Pes Retro Stats, bukti yang dikutip, dan data PES yang telah ditinjau.
Pembaruan create berisi usulan record pemain lengkap dan data roster tujuan.
Pembaruan untuk pemain yang sudah ada hanya berisi nilai yang didukung dan berbeda
dari base terverifikasi; setiap perubahan mencatat nilai literal `from` dan `to`.
Record `create` tetap didukung oleh schema untuk peninjauan dan pengaktifan
kembali di masa depan. Saat ini hanya record `update` pemain yang sudah ada yang
mengubah save; penerapan `create` selesai dengan
`create_temporarily_unavailable` tanpa mengubah save.
Kelompok pembaruan yang didukung adalah kemampuan, kecakapan posisi, gaya bermain,
keahlian pemain, gaya COM, kewarganegaraan, pengaturan fisik/dasar, dan posisi
terdaftar.
- Nilai tinjauan OVR yang dihasilkan adalah kalkulasi deterministik berdasarkan
  formula PES 2021 yang dipublikasikan. Nilai ini membantu parity, bukan jaminan
  independen bahwa runtime game memakai hasil yang sama; ability tetap perlu ditinjau.
- Draf pemain yang dibuat dengan pengidentifikasi model OVR sebelumnya harus
  dibuat ulang sebelum validasi; tidak ada migrasi v1-ke-v2 implisit.

### Jalur issue sederhana

1. Buka [formulir issue pembaruan pemain](.github/ISSUE_TEMPLATE/player-update.yml).
   Masukkan `Player name` persis seperti yang ditampilkan pada satu `Pes Retro Stats
   profile` kanonis, berikan URL bukti, lalu tunggu maintainer menerapkan label
   `generate-player-draft` yang tepat.
2. Workflow generator yang dikonfigurasi mengambil profil tersebut dan membuka
   draft PR yang berisi satu usulan schema-version-2
   `players/<player-slug>.json`. Workflow ini memperoleh snapshot sumber,
   identitas, pengaturan fisik, data posisi, kemampuan, gaya bermain, keahlian
   pemain, dan gaya COM dari profil tersebut.
3. Untuk create, hanya nilai lokal gim yang tidak tersedia dari sumber yang tetap
   tercantum di `draft.missing`: ID PES dan nama cetak untuk identitas dan pemain,
   ID dan nama tim, ID kewarganegaraan, warna kulit, dan warna iris. Kontributor
   atau maintainer harus melengkapinya. Untuk update, generator mencari pemain
   di base terverifikasi dan hanya menghasilkan perbedaan `from`/`to` yang nyata.
   Posisi sumber yang tidak didukung PES 2021, seperti `RWB`, dihilangkan dan
   bukan dipetakan ulang, termasuk dari perubahan posisi terdaftar.
4. Kontributor dan maintainer meninjau setiap nilai yang dihasilkan sebagai usulan
   yang belum disetujui. CI hanya menerima Pembaruan Pemain jika PR menambahkan
   atau mengubah tepat satu path JSON pemain kanonis dan validator semantik
   bersama berhasil.
5. Penggabungan PR tetap menjadi status persetujuan manusia. Tidak ada flag
   `approved` terpisah di dalam file JSON.

Setiap usulan yang dihasilkan diperkirakan gagal pada validasi file lengkap. Untuk
mengubah bukti yang dihasilkan menjadi schema v2 lengkap, hapus field khusus draft
`evidence.current_team`, `evidence.issue_number`, dan `evidence.issue_url`;
pertahankan `evidence.profile_url` kanonis, `evidence.proof_urls` yang telah
ditinjau, dan `evidence.effective_date`; lalu tambahkan `evidence.reason` yang telah
ditinjau dan tidak kosong. Simpan UUID profil kanonis sebagai
`identity.pes_retro_stats_id` dan hanya nilai gameplay yang telah ditinjau di
`pes`. Untuk create, lengkapi juga setiap field lokal gim yang disebutkan oleh
`draft.missing`. ID PES pemain yang dibuat harus unik dan setidaknya `0x100000` (1.048.576);
alokator proposal tetap berada dalam rentang yang dicadangkan tersebut.
Setelah itu, hapus objek tingkat teratas `source` dan `draft`,
yang merupakan metadata draft hasil generator dan hanya digunakan untuk peninjauan,
sebelum validasi lengkap.

### Jalur PR satu file langsung

Kontributor tingkat lanjut dapat melewati draft yang dihasilkan dari issue dan
langsung membuka PR yang menambahkan atau mengubah tepat satu file lengkap
`players/<player-slug>.json`. Berikan provenance UUID/profil kanonis di dalam
`identity` dan `evidence`, bukti yang dikutip, nilai PES yang telah ditinjau,
baseline update yang diharapkan, lifecycle, dan revisi base yang tepat, lalu
jalankan `python run.py players validate` sebelum meminta peninjauan. Jangan
sertakan metadata `source` atau `draft` tingkat teratas milik draft yang
dihasilkan. Jangan sertakan perubahan kode atau dokumentasi lain di PR tersebut.

Penerapan selalu dilakukan melalui perintah eksplisit dan memerlukan revisi yang
tepat dari `data/base_manifest.json`; ketidakcocokan revisi menyebabkan kegagalan
sebelum save target didekripsi.

### Siklus hidup revisi

Saat base resmi berubah, perbarui `base/EDIT00000000` dan
`data/base_manifest.json` secara bersamaan. Simpan riwayat Pembaruan Pemain di
`players/`; jangan menghapusnya hanya karena revisi berubah. Pembaruan Pemain
aktif yang daftar `applies_to`-nya tidak memuat revisi baru menjadi tidak aktif:
validasi melaporkan `needs_review` dan penerapan akan melewatinya. Setelah ditinjau,
tambahkan revisi baru hanya jika Pembaruan Pemain masih berlaku, tandai sebagai
`upstreamed` ketika base resmi sudah menyertakan perubahannya, atau tandai sebagai
`retired` ketika perubahan tersebut tidak lagi berlaku.

Opsi `run` yang umum:

| Opsi | Kegunaan |
|---|---|
| `--deep` | Mengambil setiap klub FotMob yang diindeks secara lokal |
| `--club "Chelsea,Arsenal"` | Membatasi proses ke klub yang dipilih |
| `--window auto` | Memutar ulang semua transfer bertanggal yang tersedia hingga hari ini |
| `--window summer` | Menggunakan rentang terbaru 1 Juni–30 September |
| `--window winter` | Menggunakan rentang Januari–Februari pada tahun yang dipilih |
| `--since YYYY-MM-DD` | Menetapkan batas bawah tanggal secara manual |
| `--dry-run` | Merencanakan perubahan tanpa menulis save |
| `--from-base` | Memulai dari `base/EDIT00000000` |
| `--fotmob-only` | Berjalan tanpa sumber transfer tambahan |

Tanpa `--from-base`, proses normal dilanjutkan dari output terverifikasi terakhir.
Hal ini mencegah hilangnya transfer ketika proses terjadwal berikutnya membaca
kembali riwayat kumulatif.

## Sumber transfer

FotMob menyediakan riwayat transfer dan metadata skuad utama. Daftar musiman
Wikipedia, pengajuan transfer SortitoutSI yang diaktifkan, dan record Transfermarkt
bertanggal yang terverifikasi melengkapi atau mengonfirmasi rute transfer. Profil
Pes Retro Stats menyediakan usulan berbasis sumber yang belum disetujui untuk
draft Pembaruan Pemain.

Record dari sumber yang berbeda direkonsiliasi tanpa membuang tanggal, ID, kutipan,
atau tautan buktinya. Peristiwa tanpa tanggal, berlaku pada masa mendatang,
bertentangan, atau ambigu tidak dapat memperbarui save dengan sendirinya.

Pencocokan pemain dimulai dari roster sumber dan menggunakan roster tujuan sebagai
fallback idempoten. Posisi, kewarganegaraan, dan usia hanya dipertimbangkan jika
informasi tersebut tersedia.

## Pengembangan

Jalankan test suite dengan:

```bash
pytest -v
```

Suite tersebut mencakup parsing dan validasi save, rekonsiliasi transfer,
perencanaan roster, riwayat peminjaman, pencocokan pemain, batas skuad, pelaporan,
cadangan, dan process locking.

## Lisensi

FL Daily Edit tersedia di bawah [Lisensi MIT](LICENSE).
