[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

Perbarui save `EDIT00000000` SP Football Life 2026 dan eFootball PES 2021
dengan transfer nyata yang terverifikasi serta pembaruan pemain yang telah ditinjau.

> **Beta:** Rilis dan kompatibilitas save masih dalam pengujian.
>
> **Pembuatan pemain baru untuk sementara dinonaktifkan.** Transfer dan
> pembaruan pemain yang sudah ada tetap didukung. Pemain yang tidak ditemukan
> atau ambigu akan dilewati. Roster tujuan yang penuh akan melepas pemain cadangan
> yang aman berdasarkan role; gunakan `--no-allow-overflow-release` untuk
> membiarkan roster penuh tanpa perubahan.

## Kompatibilitas

[Base save yang disertakan](../../base/EDIT00000000) memerlukan:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

Tidak kompatibel dengan UML, FL26 versi lama, atau instalasi tanpa pembaruan
skuad nasional. Mulai karier Master League atau Become a Legend baru setelah
memasangnya.

## Installer Windows

Installer adalah pilihan termudah:

1. Unduh dan ekstrak [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Tutup gim dan pilih **Fast** atau **Deep**.
3. Konfirmasi folder Football Life, lalu pilih **Download and install**.

Installer memverifikasi rilis, mencadangkan save saat ini, lalu menggantinya
secara atomik. Untuk memperbarui save yang sudah ada, pilih **Update my local
save**, pilih save, lalu pilih **Apply update**.

Installer belum ditandatangani. Verifikasi `FLDailyEditInstaller.zip` dengan
`FLDailyEditInstaller.zip.sha256` pada [rilis terbaru](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
sebelum menjalankannya; Windows SmartScreen mungkin menampilkan peringatan.

Untuk pemasangan manual, unduh [ZIP Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
atau [ZIP Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip).
Cadangkan save, ekstrak `EDIT00000000`, lalu salin ke:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Untuk daftar klub khusus atau proses on-demand, fork repositori dan gunakan
**Run workflow** pada tab Actions.

## Yang diperbarui

- Transfer, pelepasan, peminjaman, dan pengembalian pinjaman
- Nomor punggung, lineup, dan game plan yang terdampak perubahan roster
- Laporan transfer dan log audit
- Save siap pakai harian melalui GitHub Actions

Updater memeriksa klub pemain sebelum memindahkan pemain dan tidak menimpa nomor
punggung yang sudah dipakai anggota skuad lain.

Save PES21 yang bersih dapat menyisakan nomor punggung pada slot roster kosong.
Kondisi ini dilaporkan sebagai peringatan yang tidak memblokir dan tidak
menghentikan update lokal.

## Jalankan secara lokal

Didukung di macOS, Linux, dan Windows melalui WSL. Python 3.10 atau lebih baru
diperlukan.

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

`run` hanya menerapkan transfer. `players apply` adalah alur terpisah. Untuk
menggabungkan keduanya, jalankan transfer terlebih dahulu, lalu terapkan Player
Updates ke save yang sama. Gunakan `python run.py <command> --help` untuk tools
audit, perbandingan, logging, dan repair.

## Player Updates

Pembaruan yang telah ditinjau disimpan sebagai satu file JSON per pemain di
`players/`. Record `update` untuk pemain yang sudah ada dapat diterapkan. Record
`create` untuk pemain baru hanya untuk review dan saat ini ditolak oleh
`players apply` dengan `create_temporarily_unavailable`.

Untuk mengusulkan pembaruan:

1. Buka [formulir issue pembaruan pemain](../../.github/ISSUE_TEMPLATE/player-update.yml).
2. Masukkan nama pemain persis seperti pada profil Pes Retro Stats dan sertakan URL bukti.
3. Tinjau draft yang dihasilkan, jalankan `python run.py players validate`, lalu kirim satu file JSON pemain.

## Keamanan

- Save divalidasi sebelum dan sesudah perubahan.
- Eksekusi lokal membuat backup bergilir dan memakai enkripsi atomik terverifikasi.
- Process lock mencegah penulisan bersamaan ke output yang sama.
- Data sumber yang tidak lengkap membatalkan proses; kecocokan ambigu dilewati.
- FotMob adalah sumber utama. Sumber lain hanya melengkapi atau mengonfirmasi.

## Pengembangan

```bash
pytest -v
```

## Lisensi

FL Daily Edit tersedia di bawah [Lisensi MIT](../../LICENSE).
