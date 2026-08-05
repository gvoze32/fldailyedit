[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Bahasa Indonesia](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Bahasa_Indonesia-ce1126?style=flat-square)](README.id.md) [![Español LATAM](https://img.shields.io/badge/%F0%9F%87%B2%F0%9F%87%BD_Espa%C3%B1ol_LATAM-006847?style=flat-square)](README.es-419.md) [![Português (Brasil)](https://img.shields.io/badge/%F0%9F%87%A7%F0%9F%87%B7_Portugu%C3%AAs_%28Brasil%29-009c3b?style=flat-square)](README.pt-BR.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%AC_%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-ce1126?style=flat-square)](README.ar.md) [![简体中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh-CN.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md)

# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> [!WARNING]
> **Masalah yang Diketahui / Sedang Diperbaiki**: File save `EDIT00000000` yang dihasilkan saat ini masih mengalami bug/corrupt saat dibuka di dalam game (fallback ke default). Masalah integritas save file ini sedang dalam proses perbaikan.

FL Daily Edit memperbarui skuad SP Football Life 2026 dan eFootball PES 2021
dengan menerapkan transfer dunia nyata ke file save `EDIT00000000`.

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

## Unduh save terbaru

GitHub Actions menghasilkan save dan laporan transfer yang diperbarui setiap hari.

> [!NOTE]
> GitHub mewajibkan Anda masuk sebelum mengunduh artefak workflow.

1. Buka eksekusi terbaru yang berhasil dari
   [Deep Sync](https://github.com/gvoze32/fldailyedit/actions/workflows/sync-deep.yml)
   atau [Fast Sync](https://github.com/gvoze32/fldailyedit/actions/workflows/sync-fast.yml).
2. Unduh `updated-fl-save-and-reports.zip` dari bagian **Artifacts**.
3. Ekstrak `EDIT00000000`.
4. Cadangkan save Anda saat ini, lalu salin file yang telah diekstrak ke direktori
   yang sesuai:

| Gim | Direktori save di Windows |
|---|---|
| SP Football Life 2026 | `Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\` |
| eFootball PES 2021 | `Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\<user_id>\save\` |

Untuk menjalankannya sesuai permintaan atau menggunakan daftar klub khusus, fork
repositori ini dan gunakan **Run workflow** dari tab Actions.

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

## Roadmap / Sedang dikerjakan

Item berikut masih direncanakan dan belum diimplementasikan:

1. **Pulihkan integritas output** — selidiki dan perbaiki kerusakan pada `EDIT00000000` yang saat ini dihasilkan, lalu perkuat validasi dan pemeriksaan round-trip sebelum output dianggap telah diperbaiki.
2. **Installer/downloader GUI Windows** — sediakan `.exe` yang mudah digunakan pemula untuk memandu proses pengunduhan dan pemasangan option file, dengan pilihan **Fast** dan **Deep** yang jelas.
3. **Beberapa base tervalidasi** — dukung pembuatan option file yang sesuai dari base terpisah dan tervalidasi untuk **SP Football Life 2026**, **vanilla eFootball PES 2021**, dan **UML** setelah file base tersebut diberikan dan divalidasi.

## Keamanan dan keterbatasan

- Eksekusi lokal membuat cadangan bergilir serta menggunakan enkripsi atomik yang terverifikasi.
- Save divalidasi sebelum dan sesudah perubahan roster.
- Process lock mencegah dua proses menulis output yang sama secara bersamaan.
- Snapshot FotMob yang tidak lengkap membatalkan proses alih-alih menghasilkan save parsial.
- Kecocokan pemain yang ambigu, ketidakcocokan klub sumber, dan skuad tujuan yang
  penuh akan dilewati.
- Wikipedia, Sortitoutsi, dan Transfermarkt merupakan sumber tambahan. Gangguan
  pada salah satu sumber tersebut tidak membatalkan snapshot FotMob yang lengkap.
- `--allow-overflow-release` gagal secara tertutup karena katalog yang disertakan
  tidak memuat data posisi dan OVR lengkap untuk setiap pemain.

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
Kelompok pembaruan yang didukung adalah kemampuan, kecakapan posisi, gaya bermain,
keahlian pemain, gaya COM, kewarganegaraan, pengaturan fisik/dasar, dan posisi
terdaftar.

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
`draft.missing`. Setelah itu, hapus objek tingkat teratas `source` dan `draft`,
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
