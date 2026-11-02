# Audit Pelaksanaan — Apa Yang Disahkan, Apa Yang Diubah

> Dokumen ini **bukan** analisis baharu. `KAJIAN-SISTEM.md` dan
> `BENCHMARK-ANALYSIS.md` sudah menyenaraikan kelemahan sistem. Yang tiada
> sebelum ini ialah rekod **pengesahan** (bukti setiap kecacatan benar-benar
> wujud, direproduksi) dan **pembetulan** (apa yang diubah, dan ujian apa yang
> menghalangnya daripada berulang).
>
> Setiap dakwaan di bawah disertakan cara menyemaknya sendiri.

Julat: `82183788` → `7bddbeb` · 27 fail · +2156 −274 · ujian 153 → 193

---

## 1. Penemuan utama: sistem tidak pernah berjalan

Punca sebenar "sangat bermasalah" bukan logik isyarat. Ia lebih asas.

```
$ git log --oneline 82183788 -- .github
(tiada output)
```

**Direktori `.github/` tidak pernah wujud dalam keseluruhan sejarah repo.**

README menerangkan penjadual GitHub Actions setiap 15 minit dan workflow CI.
Kedua-duanya tidak wujud. Maka:

- pengimbas tidak pernah dijalankan secara automatik walau sekali;
- `data/signals.json` mengandungi 51 bait — **sifar isyarat, selamanya**;
- `data/performance.json` semua null;
- namun `data/system-status.json` melaporkan `HEALTHY`, dan papan pemuka
  memaparkan **SYSTEM ONLINE** ke atas data berusia berjam-jam.

Ini menjadikan setiap dakwaan prestasi dalam README tidak boleh disahkan —
bukan kerana ia palsu, tetapi kerana **tiada apa-apa yang pernah menghasilkan
data untuk mengujinya**. Semua kecacatan lain adalah sekunder kepada ini.

**Pembetulan.** `.github/workflows/scanner.yml` (cron `*/15` +
`workflow_dispatch` + kumpulan concurrency + cuba-semula rebase pada push) dan
`.github/workflows/tests.yml` (pytest, `compileall`, pengesahan data,
pemeriksaan dokumentasi).

Satu nota jujur tertanam dalam workflow itu: peristiwa `schedule` GitHub
adalah *best-effort*. Dokumentasi GitHub menyatakan ia "may be delayed during
periods of high load", dan **tembakan yang terlewat tidak pernah dicuba
semula** — jika 09:00 tergelincir melepasi 10:00 ia digugurkan sahaja.
Laporan komuniti menunjukkan hanyutan 5–15 minit adalah rutin, kadangkala
lebih 30 minit. Jadual juga dilumpuhkan secara automatik selepas 60 hari repo
tidak aktif. Justeru `workflow_dispatch` dikekalkan sebagai laluan pemulihan,
dan kadens sebenar patut difahami sebagai *lebih kurang* 15 minit, bukan tepat.

---

## 2. Papan pemuka kini bercakap benar

Kecacatan paling merosakkan kepercayaan: cip status membaca nilai `health`
yang **tersimpan** dan tidak pernah bertanya bila data itu ditulis.

```
$ python3 -c "..."   # logik yang kini dijalankan pelayar
stored health : HEALTHY
data age      : 6.2 h
chip shown    : OFFLINE  -> 'SCANNER OFFLINE'
```

Pelayar kini menerbitkan kesihatan daripada **usia data jam-dinding** dan
menurunkan taraf nilai tersimpan: >45 minit → `DATA STALE`, >3 jam →
`SCANNER OFFLINE`, dengan usia dipaparkan pada cip itu sendiri.

Prinsipnya: sistem yang mati mesti kelihatan mati. Papan pemuka yang
memaparkan "ONLINE" ke atas data basi lebih teruk daripada papan pemuka yang
rosak, kerana ia menghalang pengguna daripada menyedari ada masalah.

---

## 3. "Mengapa sifar isyarat?" — kini boleh dijawab

### 3.1 Masalah: histogram penolakan tidak boleh diagregat

Sebab penolakan membenamkan nilai apungan langsung:

```
"relVol 0.83 < min 1.2"      "RSI 42.2 outside [45,74]"
"volatility out of band (0.087%)"
```

200 penilaian menghasilkan **~69 kunci unik**. Mustahil dijumlahkan, kardinaliti
tidak terbatas, dan ia mengubah `system-status.json` pada setiap imbasan
(churn git tanpa makna).

**Pembetulan.** `REJECT_STAGES` — set tertutup 23 kod. Setiap penolakan kini
`{"code", "detail"}`: kod untuk mesin, detail untuk manusia. Susunan tuple itu
*ialah* susunan get dinilai, jadi `lastScan.rejectFunnel` merupakan corong
sebenar — setiap peringkat hanya melihat apa yang terselamat daripada
peringkat sebelumnya.

### 3.2 Corong itu kini kelihatan

Panel baharu pada papan pemuka (§4, "Mengapa tiada isyarat?") memaparkan
Get / Ditolak / Terselamat. Daripada data terkini yang dikomit:

| Get | Ditolak | Terselamat |
|---|---:|---:|
| Penjajaran 4H/1H | 115 | 85 |
| Regim 4H bukan RANGING | 12 | 73 |
| ATR% dalam julat | 9 | 64 |
| ADX mantap/muncul | 17 | 47 |
| RSI dalam julat | 20 | 27 |
| Volum relatif ≥ min | 17 | 10 |

Sepuluh calon sampai ke get struktur/SMC; tiada satu pun menembusi sweep →
CHoCH/BOS → displacement → FVG/OB. **Sifar isyarat adalah tingkah laku yang
betul bagi tetapan ini** — bukan kerosakan. Perbezaan itu kini boleh dibuktikan,
bukan diandaikan.

### 3.3 Alat diagnosis luar talian

```
$ python -m scanner.diagnose --sensitivity
```

`scanner/diagnose.py` memainkan semula bahagian *get* model isyarat ke atas
`data/universe-snapshot.json` yang dikomit — tiada rangkaian, jadi ia berfungsi
dalam CI dan persekitaran tersekat. Ia juga menjawab soalan susulan yang
sebenar: *ambang mana yang paling menyekat?*

```
signalModel.minRelVolume   1.0            +7
signalModel.minRelVolume   1.1            +2
signalModel.minRelVolume   1.2  <- kini   +0
signalModel.minAdx15m      18             -3
signalModel.rsiLongMin     40             +2
```

Ini mengukuhkan diagnosis `minRelVolume`: median volum relatif alam semesta
ialah **0.97** manakala ambang ialah **1.2**, jadi get itu menolak majoriti
pasaran mengikut pembinaan.

**Ambang itu sengaja TIDAK diubah dalam audit ini.** Melonggarkannya akan
menghasilkan isyarat, dan isyarat akan kelihatan seperti kemajuan — tetapi
tanpa pengesahan luar sampel tiada bukti isyarat tambahan itu *lebih baik*.
Menala ambang sehingga output kelihatan memuaskan adalah definisi
*overfitting*. Alat itu membuat kos setiap ambang telus; keputusan kekal pada
manusia, selepas walk-forward.

---

## 4. Kebergantungan sumber tunggal yang menyamar sebagai failover

Konfigurasi menyenaraikan tiga "failover endpoints":

```
fapi.binance.com  ·  fapi1.binance.com  ·  fapi2.binance.com
```

Ketiga-tiganya milik Binance. Sekatan geografi HTTP 451 — mod kegagalan paling
mungkin bagi hos ini — mematikan **ketiga-tiganya serentak**. Ini bukan
failover; ia satu sumber dengan tiga ejaan.

**Pembetulan.** Penyesuai Bybit v5 yang menterjemah kepada bentuk respons
Binance yang sudah difahami enjin, ditambah `_by_dialect()` yang berjalan pada
peringkat *dialek*: bentuk permintaan mesti dipilih sebelum dihantar, jadi
panggilan berbentuk Binance tidak boleh gagal-alih ke Bybit dengan sendirinya.

Kontrak terjemahan (didokumen dalam kod): interval `{15m:"15", 1h:"60",
4h:"240", 1d:"D"}`; `category=linear`; baris kline tiba **terbaru dahulu**
tanpa `closeTime` → diterbitkan sebagai `openTime + interval − 1`;
`turnover24h` → `quoteVolume`; `price24hPcnt` (nisbah) → `priceChangePercent`
(peratus). Disahkan terhadap dokumentasi rasmi Bybit, termasuk had `limit`
1000 dan lilin terakhir yang mungkin belum tutup (dikendalikan oleh
`drop_incomplete` sedia ada).

Nota jujur dalam `config/strategy.json`: alam semesta Bybit **bertindih tetapi
tidak sama** dengan Binance. Failover mengekalkan sistem hidup; ia tidak
menjamin set simbol yang sama. Venue yang berkhidmat direkod dalam
`system-status.lastScan.apiStats.endpointUsed`.

### Dua pepijat yang ditemui hanya selepas ujian diperketat

Ujian stub pertama saya lulus, tetapi ia menggantikan pembantu klien sendiri —
terlalu tinggi arasnya. Memainkan semula sekatan geografi penuh pada lapisan
`urlopen` mendedahkan:

1. **Kebocoran merentas dialek.** `_by_dialect` menyemat dialek, tetapi `_get`
   masih mengitar *setiap* endpoint — jadi `/exchangeInfo` berbentuk Binance
   dihantar ke `api.bybit.com`, di mana ia hanya boleh 404. `_get` kini
   gagal-alih hanya antara hos yang bercakap dialek sama.
2. **Medan tiket Bybit tercicir.** Kadar funding, OI dan perubahan 24 jam
   tidak diterjemah, jadi ia terbaca sebagai hilang pada venue failover.

Pengajarannya lebih luas daripada dua pepijat itu: **ujian yang mengejek
terlalu dekat dengan kod yang diuji akan mengesahkan reka bentuk anda, bukan
tingkah laku anda.**

---

## 5. Kecacatan ketepatan lain yang disahkan dan dibetulkan

| # | Kecacatan | Bukti | Pembetulan |
|---|---|---|---|
| D8 | `scan_once()` menetapkan `_STATUS` hanya di dalam blok tulis → `--dry-run --json-output` membuang `NameError` | direproduksi | `_STATUS` peringkat modul |
| — | `htf_bias()` mengandungi `if bias == neutral: bias = neutral` — no-op | dibaca | susunan EMA berarah dengan ADX rendah kini diturunkan kepada `neutral` |
| §5.7 | Ambang ADX 15M digunakan untuk mengelas regim 4H **dan** bias 1D | dibaca | `minAdxByTimeframe` `{15m:—, 1h:18, 4h:20, 1d:20}` |
| §5.6 | FVG/OB tiada status mitigasi — gap yang telah diisi 50 bar lalu menjaringkan sama seperti yang segar | dibaca | penjejakan mitigasi/pembatalan bersebab; zon terpakai digugurkan; zon diuji menjaringkan 2.5 bukan 5 |
| D15 | `validate_signal()` membaca `sig["entryPrice"]` yang tiada dalam `REQUIRED_FIELDS` → `KeyError` pada isyarat belum tercetus | direproduksi | bacaan defensif; jatuh balik ke harga pencetus |
| D10 | `monteCarloNet` diisi daripada siri **kasar** apabila kos tidak dimodelkan | dibaca | medan `*Net` tanpa siri bersih ialah `null` |
| D6 | Pembina papan pemuka luar talian didokumen "berjalan setiap imbasan" tetapi tiada pemanggil; ia juga menulis ke checkout sebenar semasa ujian | direproduksi | disambung ke imbasan; laluan boleh disuntik |
| D14 | `_last_index()` tidak pernah dipanggil; `struct_direction_ok` ditetapkan tidak pernah dibaca | grep | dibuang |
| — | `exchange_info()` menerima sebarang dict → respians cacat terbaca "tiada simbol" | ditemui oleh ujian | pengawal bentuk; rantai gagal-alih |

---

## 6. Dokumentasi tidak boleh lagi hanyut secara senyap

README mendakwa 153 ujian ketika suite mengumpul 171. Bukan pembohongan —
hanya prosa yang lupa dikemas kini. Tetapi itulah cara dokumentasi mati.

`scanner/check_docs.py` (berjalan dalam CI) mengesahkan lima kelas dakwaan yang
boleh disemak secara mekanikal: fail workflow yang dirujuk wujud; dakwaan
kadens `*/15` disokong oleh baris cron sebenar; kiraan ujian yang dipetik
sepadan dengan `pytest --collect-only`; laluan lencana wujud; fail
`frontend/*.html` yang dirujuk wujud.

Ia menangkap kecacatan sebenar pada larian pertama. Ambangnya bertimbang rasa:
README dibenarkan *ketinggalan* sedikit (ujian ditambah, prosa belum
disegarkan) tetapi tidak boleh **melebih-lebihkan** suite, dan tidak boleh
hanyut melebihi 5%.

---

## 7. Perbandingan dengan sistem setara

`BENCHMARK-ANALYSIS.md` sudah memberi skor 8.2/10 dan menginventori 20 fungsi.
Yang ditambah audit ini ialah dua pembanding dengan pelajaran konkrit, dan satu
pemerhatian yang mengubah cara repo ini patut meletakkan dirinya.

**`joshyattridge/smart-money-concepts`** (Python, PyPI, aktif) — analog pustaka
terdekat. Menggabungkan gap bersebelahan, menghalang pertindihan BOS/CHoCH,
order block dipercepat numba. Preseden konkrit untuk pengendalian keadaan
FVG/OB yang §5.6 kini laksanakan.

**`Julian-dev28/hermes-trader`** (Hyperliquid, 500+ perps) — **11 get risiko
bebas** termasuk korelasi, cooldown, had kerugian harian dan sekatan berita;
saiz kedudukan risiko-sama ATR; keluar trailing dua fasa yang berterusan ke
cakera. Ini preseden terkuat untuk §5.8 (risiko korelasi portfolio) yang masih
tiada di sini — sistem ini boleh mengeluarkan 12 isyarat yang, tanpa disedari,
merupakan satu pertaruhan beta yang sama.

**`OfficialGIGA/crypto-signal-scanner`** — modul MarketPulse **gagal secara
senyap tanpa mematikan pengimbas**. Preseden degradasi yang baik.

### Pemerhatian yang paling penting

freqtrade — rujukan industri — menyediakan `lookahead-analysis` dan
`recursive-analysis`. Namun isu terbuka #11346 menunjukkan **kedua-duanya
melaporkan "No bias" pada strategi yang benar-benar membaca lilin 1 jam yang
belum tutup pada 14:15**.

Itulah tepat kelas pincang sempadan MTF yang enjin ini kawal melalui
`drop_incomplete` dan penghirisan bersebab dalam replay. Dengan kata lain:
**alat standard industri terlepas kelas pepijat yang seni bina repo ini cegah
secara reka bentuk.** Itu pembeza yang boleh dipertahankan — dan ia patut
diuji secara eksplisit pada sempadan 4H/1H→15M, bukan diandaikan.

---

## 8. Apa yang sengaja tidak dibuat

Kejujuran tentang skop penting sama seperti kerja itu sendiri.

- **Ambang tidak ditala.** Lihat §3.3. Melonggarkan `minRelVolume` akan
  menghasilkan isyarat dan kelihatan seperti kemajuan; tanpa pengesahan luar
  sampel ia hanyalah overfitting yang berpakaian kemajuan.
- **Pengesahan walk-forward belum dilaksanakan.** Replay masih satu laluan
  dalam-sampel. Amalan yang diterima bagi kripto intraday ialah tetingkap
  latihan 14–30 hari, ujian 3–7 hari, embargo 12–24 jam, dengan purging untuk
  label bertindih. Ini kerja besar dan patut dilakukan sebelum sebarang
  penalaan ambang, bukan selepas.
- **Risiko korelasi portfolio (§5.8)** kekal terbuka.
- **Bias kemandirian (§5.5)** kekal terbuka: alam semesta dibina daripada
  simbol yang *hidup hari ini*, jadi mana-mana ujian sejarah melebihkan
  prestasi dengan membuang yang telah disenarai keluar.
- **Failover tidak boleh diuji secara langsung** dari sandbox ini — kesemua 13
  API pasaran gagal jabat tangan TLS. Ia diliputi ujian pengangkutan stub;
  larian sebenar terhadap Bybit masih perlu disahkan sekali dalam persekitaran
  bersambung.

---

## 9. Cara mengesahkan sendiri

```bash
python -m pytest tests/ -q          # 193 lulus, luar talian, deterministik
python -m scanner.check_docs        # dakwaan README vs realiti
python -m scanner.diagnose --sensitivity   # corong + kos setiap ambang
python -m scanner.validate_data     # integriti artifak data
python -m scanner.main --dry-run --json-output   # imbasan tanpa tulisan
```

---

## 10. Penilaian

Sistem ini tidak "sangat bermasalah" dalam pemikiran perdagangannya. Model
konfluens berstruktur baik, pengasingan kos kasar/bersih lebih jujur daripada
kebanyakan projek setara, dan disiplin lilin-tertutup menyelesaikan kelas
pepijat yang terlepas oleh alat standard industri.

Ia bermasalah kerana **jurang antara apa yang didakwanya dan apa yang
dijalankannya adalah menyeluruh**: tiada automasi, sifar isyarat sepanjang
hayat, metrik null, dan papan pemuka yang melaporkan sihat sepanjang masa.
Jurang itu kini ditutup — automasi wujud, papan pemuka tidak lagi boleh
memaparkan data mati sebagai hidup, dan corong penolakan menjadikan senyapnya
enjin boleh diaudit.

Kerja yang tinggal bersifat kuantitatif, bukan kejuruteraan: pengesahan luar
sampel, risiko peringkat portfolio, dan hanya selepas itu — penalaran ambang.
