# Kajian Menyeluruh — Crypto 15-Minute High-Quality Signal Scanner

**Tarikh:** 30 Ogos 2026 · **Versi sistem:** v1.1.0 · **Cawangan:** `arena/01a051eb-crypto-signal-scanner`
**Skop:** audit kod, kajian sistem pembanding, analisis jurang, dan cadangan penambahbaikan

> **Nota kejujuran metodologi.** Semua dakwaan teknikal tentang *repo ini* disahkan dengan
> perintah yang saya jalankan sendiri (direkodkan dalam §1). Semua dakwaan tentang *sistem
> lain* diambil daripada sumber awam yang dipautkan. Panel pakar dalam §6 adalah **peranti
> analitik yang saya bentuk** — enam persona dengan kepakaran berbeza untuk memaksa pertimbangkan
> sistem ini dari sudut yang berlawanan. Ia bukan petikan daripada individu sebenar.

---

## 1. Log pengesahan — apa yang saya jalankan

| Pemeriksaan | Perintah | Keputusan |
|---|---|---|
| Ujian unit + integrasi | `pytest tests/ -q` | **120 passed in 0.36s** |
| Integriti data (sama seperti CI) | `python -m scanner.validate_data` | `data validation OK`, exit 0 |
| Kompilasi bait | `python -m compileall scanner tests` | OK |
| Sintaks JS | `node --check frontend/app.js` | OK |
| Pembina luar talian | `python -m scanner.build_offline` / `build_preview` | kedua-duanya berjaya, idempoten |
| Papan pemuka | `http.server 8080` | semua aset + 5 fail JSON → HTTP 200 |
| Laluan kegagalan imbasan | `python -m scanner.main --symbols BTCUSDT --dry-run` | exit **1**, data lama dikekalkan |
| Bukti aliasing (lihat §5.1) | skrip ad-hoc memanggil `update_outcomes` + `check_immutability` | `merged[0] is previous[0]` → **True** |
| Pengiraan kos (§5.2) | skrip ad-hoc atas `config/strategy.json` | jadual dalam §5.2 |

**Yang TIDAK dapat saya sahkan:** laluan gembira imbasan sebenar. Sandbox ini tiada laluan TLS
keluar ke Binance — ketiga-tiga endpoint failover mengembalikan
`TLS/SSL connection has been closed (EOF)`. Jadi pembinaan universe, penjanaan isyarat pada data
nyata, dan `scanner.replay` **tidak diverifikasi di sini**. Laluan kegagalan (semua endpoint mati)
disahkan dan berkelakuan seperti yang didokumenkan.

---

## 2. Anatomi sistem

```
Binance USDⓈ-M (fapi) ──failover──> spot-vision
        ↓
Top-100 universe (isipadu 24j, tapis stablecoin/leveraged)
        ↓
Kline 4H / 1H / 15M (+1D pilihan) ── drop_incomplete()
        ↓
indicators.py   EMA20/50/200 · RSI · ATR · ADX · RelVol · VWAP
structure.py    swing fractal k=2 · BOS/CHoCH · displacement · equal levels · sweep
smc.py          FVG 3-candle · Order Block
        ↓
analysis.py     bias 4H → bias 1H → frame 15M
risk.py         trigger = ekstrem candle ± 0.1·ATR
                stop    = struktur invalidasi ± 0.5·ATR
                TP      = aras likuiditi nyata terdekat dengan RR ≥ 2.5
scoring.py      100 mata (HTF 20 · struktur 20 · SMC 20 · momentum 15 · isipadu 10 · volatiliti 5 · RR 10)
        ↓
signals.py      tolak keras → dedupe → cooldown → maks 12 aktif
outcomes.py     WAITING_TRIGGER → TRIGGERED → WIN/LOSS/EXPIRED/AMBIGUOUS/CANCELLED
performance.py  winRate · profitFactor · expectancyR · Wilson 95% · Monte Carlo
        ↓
persist.py      tulis atomik → data/ + mirror frontend/data/
```

**Saiz:** 8,362 baris — 3,264 enjin · 1,509 ujian · 3,589 frontend (`.js`+`.html`+`.css`).
**Kebergantungan runtime: sifar.**

---

## 3. Kekuatan yang disahkan

Ini bukan senarai budi bicara — setiap satu saya sahkan dalam kod.

| # | Kekuatan | Bukti |
|---|---|---|
| S1 | **Sifar kebergantungan runtime** | `requirements.txt` hanya `pytest`; semua import enjin adalah stdlib. Tiada rantaian bekalan untuk diserang, tiada versi untuk rosak. |
| S2 | **Tiada lookahead secara pembinaan** | `indicators.py` hanya menyentuh indeks ≤ i; swing hanya sah selepas `k` bar (`structure.py`); `slice_frames()` dalam `replay.py` memotong `closeTime <= until_close_ms`. |
| S3 | **Kejujuran statistik luar biasa** | `AMBIGUOUS` tidak pernah dikira sebagai menang; resolusi 1-minit sebelum mengaku ambigu; `wilson_interval()` pada kadar menang; `disclaimer` menyatakan skor bukan kebarangkalian. Ini **jarang** dalam projek sebegini. |
| S4 | **Monte Carlo deterministik** | `random.Random(1337)` — boleh ulang, tidak menjejaskan determinisme enjin. |
| S5 | **Tulis atomik sebenar** | `tempfile.mkstemp` + `fsync` + `os.replace` — tiada fail JSON separuh tulis. |
| S6 | **Dasar kegagalan yang betul** | Endpoint failover berantai; imbasan gagal → kekalkan data papan pemuka lama, exit 1 (disahkan). Tidak pernah memalsukan isyarat. |
| S7 | **Semua boleh dikonfigurasi** | 40+ parameter dalam `config/strategy.json`; pemberat skor berjumlah tepat 100. |
| S8 | **120 ujian luar talian, 0.36s** | Vektor petunjuk kiraan tangan, setiap peraturan tolak keras, kitaran hayat penuh. |

**Penilaian:** sebagai *alat penyaringan yang jujur*, reka bentuk terasnya lebih baik daripada
kebanyakan projek sumber terbuka setara. Masalahnya bukan pada niat — ia pada **tiga lubang
yang menjadikan nombor yang dihasilkan tidak boleh dipercayai**, dan pada **ketiadaan lapisan
pengesahan luar sampel**.

---

## 4. Lima sistem pembanding

Saya pilih lima yang **paling dekat dari segi seni bina**, dan setiap satu mengajar pelajaran
berbeza. Bukan sekadar "projek popular".

### 4.1 `freqtrade/freqtrade` — penanda aras rangka kerja strategi kripto

> https://github.com/freqtrade/freqtrade · https://pypi.org/project/freqtrade/ · 30K+ bintang

Rangka kerja sumber terbuka paling matang untuk strategi kripto berarah. Mengapa ia relevan:
ia menyelesaikan **tiga masalah yang repo ini belum sentuh**.

| Ciri freqtrade | Status repo ini |
|---|---|
| Pemodelan yuran maker/taker + funding dalam backtest | **Tiada** (sifar rujukan `fee`/`commission`) |
| Simulasi slippage | **Tiada** (slippage hanya *dilapor*, tidak ditolak) |
| `freqtrade lookahead-analysis` — **CLI khusus untuk mengesan lookahead bias** | Tiada; hanya disahkan oleh pembinaan |
| `freqtrade recursive-analysis` — kesan formula rekursif | Tiada |
| `hyperopt` (pencarian parameter + loss function boleh pilih) | Tiada |
| Walk-forward / pengesahan luar sampel | Tiada |
| Mod `dry-run` (forward test berasingan dari live) | Tiada |
| Protections: max drawdown, stoploss guard, cooldown | Separa (`maxActiveSignalsTotal`, cooldown) |
| Persistence SQLite | JSON dalam git |

**Pelajaran:** freqtrade menjadikan *pengesanan lookahead* dan *pemodelan kos* sebagai
**subperintah CLI kelas pertama**, bukan dokumen. Repo ini ada sifat anti-lookahead yang baik
tetapi tiada alat untuk **membuktikan** ia kekal begitu apabila kod berubah.

### 4.2 `joshyattridge/smart-money-concepts` — rujukan kanonik SMC

> https://github.com/joshyattridge/smart-money-concepts · `pip install smartmoneyconcepts` · MIT

Implementasi Python paling dirujuk untuk konsep ICT/SMC. Perbandingan terus dengan
`scanner/structure.py` + `scanner/smc.py`:

| Konsep | `smartmoneyconcepts` | Repo ini |
|---|---|---|
| FVG | ✅ + **`MitigatedIndex`** (bila gap diisi) | ✅ tetapi **tiada status mitigasi** |
| Order Block | ✅ + `MitigatedIndex`, `close_mitigation`, `OBVolume`, `Percentage` | ✅ tetapi **tiada status mitigasi** |
| BOS / CHoCH | ✅ + `BrokenIndex` | ✅ + swing "consumed" (lebih ketat — **kelebihan repo ini**) |
| Liquidity / EQH-EQL | ✅ + **`Swept` index** | ✅ `find_liquidity_sweeps` |
| Premium / Discount zone | ✅ | ❌ |
| Retracements (%) | ✅ | ❌ |
| Previous High/Low per timeframe | ✅ (15m→1W) | ❌ |
| Kill zones | ❌ | ✅ (`sessions.py`) — **kelebihan repo ini** |

**Pelajaran paling tajam:** `FVG` dan `OrderBlock` dalam repo ini tiada medan status. Saya sahkan
dataclass mereka hanya ada `index, direction, bottom, top, ...` + `contains()`. Akibatnya FVG yang
sudah diisi 50 bar lalu **menjaringkan mata yang sama** dengan FVG segar dalam komponen
liquidity/SMC (5 mata setiap satu). Ini inflasi skor yang senyap.

### 4.3 `xxvw/SMC_ICT_Library` — konfluens + ML, seni bina hampir sama

> https://github.com/xxvw/SMC_ICT_Library · MQL5 + Python

Projek yang **paling mirip dari segi falsafah**: ada `ConfluenceDetector` yang menjaringkan semua
elemen SMC — sama seperti `scoring.py` repo ini. Tetapi ia pergi lebih jauh:

- `PremiumDiscount` (premium/discount/equilibrium) dan `OptimalTradeEntry` (Fib 0.618–0.786)
  — kedua-duanya tiada dalam repo ini
- Status OB sebagai **mesin keadaan**: `FRESH / TESTED / MITIGATED / BROKEN`
- `KillZone` Asian/London/New York/Overlap (repo ini ada, setara)
- **15 skrip latihan ML**: `fvg_fill_predictor` (XGBoost), `ob_quality_scorer` (LightGBM),
  `liquidity_sweep_predictor`, `mtf_confluence_scorer`, `sl_tp_optimizer`, `market_regime_detector`

**Pelajaran:** pemberat konfluens repo ini (20/20/20/15/10/5/10) adalah **tekaan pakar yang tidak
pernah diuji**. `xxvw` menjadikan setiap pemberat itu *boleh dipelajari dan diukur*. Repo ini boleh
mula dengan langkah lebih kecil: **ukur kadar mitigasi FVG/OB secara empirikal** sebelum memberi
mata.

### 4.4 `oager/walkforward-engine` — lapisan pengesahan yang tiada di sini

> https://github.com/oager/walkforward-engine · Python · kemaskini Ogos 2026

Enjin walk-forward bot-agnostik dengan: **out-of-sample survival gating**, Monte Carlo path risk,
dan backstop pemilihan-terlalu-lengkap — **PBO / CSCV** dan **trial-deflated Sharpe**.

Ini tepat mengisi lubang statistik terbesar repo ini. Rujukan teori di belakangnya:

- Bailey, Borwein, López de Prado & Zhu (2014), *Pseudo-Mathematics and Financial Charlatanism*,
  Notices of the AMS 61(5), 458–471 — https://www.ams.org/notices/201405/rnoti-p458.pdf
- Bailey et al. (2017), *The Probability of Backtest Overfitting*, J. Computational Finance 20(4)
  — dalam kertas itu, **~53% Sharpe OOS negatif walaupun semua Sharpe IS positif**
- Bailey & López de Prado (2014), *The Deflated Sharpe Ratio*, J. Portfolio Management 40(5), 94–107
- Implementasi Python PBO: https://github.com/esvhd/pypbo
- Walk-forward + **CPCV** (combinatorial purged cross-validation): https://github.com/titouannwtt/freqtrade-ultimate

**Pelajaran:** petikan kunci Bailey & López de Prado — *"maklumat paling penting yang tiada dalam
hampir semua backtest yang diterbitkan ialah bilangan percubaan yang dilakukan."* Repo ini ada
~40 tombol konfigurasi dan memecahkan prestasi mengikut **arah, kualiti, rejim, julat skor, dan
simbol** serentak. Setiap pecahan itu ialah ujian berganda. Tanpa pengiraan bilangan percubaan,
sebarang "A+ menang lebih kerap" yang muncul kemudian adalah **tidak boleh ditafsir**.

### 4.5 `naimkatiman/alpha-scanner` — lapisan produk yang tiada di sini

> https://github.com/naimkatiman/alpha-scanner · MIT · live demo di Railway

Penyaring isyarat konfluens pelbagai timeframe (M15/H1/H4/D1) — **produk**, bukan enjin. Ia ada:

- **Paper trading** akaun maya $10K dengan mod auto-trade
- **Accuracy tracking**: penjejakan TP/SL server-side dengan statistik awam
- **Leaderboard** kadar menang mengikut simbol (mingguan/bulanan/semua)
- Telegram alerts, webhook integration, RSS feed, PWA offline

**Pelajaran:** repo ini mengesan kitaran hayat isyarat dengan lebih **teliti** daripada
alpha-scanner (AMBIGUOUS, resolusi 1M, immutability), tetapi **tiada siapa boleh melihatnya**.
Tiada alert, tiada forward-test, tiada lejar ketepatan awam yang boleh diaudit orang luar.
Kekuatan teknikal yang tidak boleh dicapai orang = sifar impak.

### Rujukan tambahan

| Sumber | Relevans |
|---|---|
| https://github.com/RyanJHamby/stock-screener | **Seni bina ops yang sama** (GitHub Actions cron → commit JSON ke git). Ada `.github/workflows/daily_screening_git_storage.yml` yang sebenar + cache JSON dalam git (74% pengurangan panggilan API). Template terus untuk workflow yang hilang. |
| https://nautilustrader.io/ | `FillModel` / `FeeModel` / `LatencyModel` sebagai objek boleh suntik — model untuk pemodelan kos yang betul. |
| https://github.com/keithorange/CryptoSuperScreener | Penyaring pelbagai bursa via CCXT — jalan keluar dari kebergantungan Binance tunggal. |
| https://github.com/shner-elmo/TradingView-Screener | 3,000+ medan, campuran timeframe tanpa langganan — alternatif sumber data. |
| https://github.com/rajdeep7878/marketmind-open | Enjin backtest yang *sengaja* menangkap overfitting/lookahead/"fantasy costs"; rekod penyelidikannya sendiri: semua ditolak. Rujukan budaya pengesahan. |

---

## 5. Kelemahan — dengan bukti

### 🔴 5.1 KRITIKAL — Pengawal "no-repaint" tidak berfungsi dalam laluan live

Ini penemuan paling serius. README §6 dan docstring `outcomes.py` mendakwa:

> *"Immutability: entry, trigger, SL, TP are never modified (no repainting)."*
> `main.py:225` → `check_immutability(previous_signals, merged_previous)`

Masalahnya: `update_outcomes()` **mengubah suai dict input di tempat** dan memulangkan objek yang
sama. Saya buktikan:

```
status selepas update   : LOSS
merged[0] is previous[0]: True
previous[0]['status']   : LOSS   <- objek "asal" turut berubah
check_immutability()    : []     <- sentiasa kosong
```

Kedua-dua senarai mengandungi **objek yang sama**. `check_immutability` membandingkan setiap
isyarat dengan dirinya sendiri → **sentiasa memulangkan senarai kosong**. Pengawal itu adalah
no-op. Sebarang pelanggaran immutability pada masa depan akan **lulus secara senyap**.

**Mengapa 120 ujian lulus?** `tests/test_outcomes.py:127` membina `new = [dict(old[0])]` — salinan
cetek yang *berbeza identiti*. Ujian itu menguji `check_immutability` dengan betul, tetapi
**tidak menguji cara `main.py` memanggilnya**. Jurang antara unit test dan integration path.

**Pembetulan (~3 baris):** salin mendalam sebelum kemas kini.
```python
previous_signals = copy.deepcopy(previous_signals_payload.get("signals", []))
```
dan tambah ujian yang memanggil laluan sebenar `scan_once()`, bukan hanya fungsi utiliti.

### 🔴 5.2 KRITIKAL — Tiada pemodelan kos langsung

`grep -rni "fee|commission|funding_cost" scanner/` → **kosong**. `slippage` dalam
`performance.py` hanya **dilaporkan** (`avgPct`, `maxPct`) — ia **tidak pernah ditolak** daripada
`rMultiple`.

Kos Binance USDⓈ-M VIP0: **0.02% maker / 0.05% taker**
(https://www.bitdegree.org/crypto/tutorials/binance-fees). Isyarat ini adalah **stop-order** →
taker masuk + taker keluar = **0.10% notional setiap pusingan**.

Saya kira kos dalam unit R menggunakan parameter repo sendiri
(`minRr=2.5`, `stopBufferAtr=0.5`, `minAtrPercent=0.1`):

| Jarak stop (ATR) | ATR% | Risiko (%harga) | **Kos (unit R)** |
|---|---|---|---|
| 0.15 (minimum dibenarkan) | 0.10% | 0.015% | **6.67 R** ⚠️ |
| 0.50 | 0.30% | 0.150% | 0.67 R |
| **1.00** | **0.30%** | **0.300%** | **0.33 R** |
| 2.00 | 0.30% | 0.600% | 0.17 R |
| 3.50 (maksimum dibenarkan) | 0.30% | 1.050% | 0.10 R |

**Kesan kepada break-even win rate pada RR 2.5:**

| Kos | Break-even WR kasar | Break-even WR bersih | Anjakan |
|---|---|---|---|
| 0.00 R | 28.6% | 28.6% | — |
| 0.10 R | 28.6% | 31.4% | +2.9 pp |
| **0.33 R** | **28.6%** | **38.0%** | **+9.4 pp** |
| 0.50 R | 28.6% | 42.9% | +14.3 pp |

Pada kes tipikal (stop ~1 ATR, ATR% ~0.3%), **kos memakan satu pertiga daripada setiap R**.
Strategi yang kelihatan +0.2R jangkaan kasar adalah **−0.13R bersih**. Dan kes tepi paling buruk
(stop 0.15 ATR pada ATR% 0.1%, yang **dibenarkan** oleh `minAtrPercent=0.1`) kosnya **6.67 R** —
 mustahil untuk menang.

**Ini tidak kelihatan dalam mana-mana metrik semasa.** `expectancyR`, `profitFactor`, `sharpeR`,
Monte Carlo — semuanya dikira atas R kasar.

### 🔴 5.3 KRITIKAL — `rMultiple` LOSS di-hardcode `-1.0`, tidak simetri dengan WIN

`outcomes.py::_close()`:
```python
entry = sig["entryPrice"] or sig["triggerPrice"]
risk  = abs(entry - sig["stopLoss"])
if status == WIN:
    sig["rMultiple"] = round(abs(level - entry) / risk, 4)   # guna entry SEBENAR
else:
    sig["rMultiple"] = -1.0                                   # tetap, abaikan entry sebenar
```

WIN menggunakan risiko berasaskan entry sebenar; LOSS mengandaikan entry = trigger. Apabila
berlaku gap-fill (kod di atasnya *memang* menetapkan `entryPrice = c.open` bila candle buka
melampau trigger), dua-dua arah berat sebelah ke arah **optimis**:

| Harga open | Entry | LOSS dilapor repo | LOSS sebenar | Kurang lapor |
|---|---|---|---|---|
| 100.0 | 100.0 | −1.00 R | −1.00 R | — |
| 101.0 | 101.0 | −1.00 R | −1.20 R | 0.20 R |
| 102.0 | 102.0 | −1.00 R | −1.40 R | 0.40 R |
| 103.0 | 103.0 | −1.00 R | −1.60 R | 0.60 R |

Semakin teruk slippage, semakin **cantik** statistik yang dilaporkan. Ini arah yang paling
berbahaya untuk berat sebelah.

### 🟠 5.4 SEDERHANA — Tiada pengesahan luar sampel

`replay.py` ialah **satu laluan in-sample**. Tiada pembahagian train/test, tiada walk-forward,
tiada purge/embargo, tiada pengiraan bilangan percubaan. Dengan ~40 tombol konfigurasi dan 5
dimensi pecahan prestasi (§4.4), risiko data-snooping adalah **strukutural**, bukan hipotesis.

### 🟠 5.5 SEDERHANA — Bias kemandirian (survivorship) dalam universe

Universe dibina daripada **top-100 isipadu semasa** setiap imbasan. Untuk backtest, ini bermakna
kita hanya menguji syiling yang *masih* popular hari ini — yang terselamat. `replay.py` menerima
`--symbols` secara manual, jadi tiada mekanisme universe historis.

### 🟠 5.6 SEDERHANA — FVG / Order Block tiada status mitigasi

Disahkan: dataclass `FVG` dan `OrderBlock` tiada medan status. Zon yang sudah diisi penuh
menjaringkan mata penuh. Bandingkan `MitigatedIndex` dalam `smartmoneyconcepts` (§4.2).

### 🟠 5.7 SEDERHANA — Parameter ADX dikongsi merentas timeframe

`analysis.py` memanggil:
```python
market_regime(bias_4h, adx_4h, cfg.get("signalModel.minAdx15m"))   # 4H guna ambang 15M
htf_bias(c1, ..., cfg.get("signalModel.minAdx15m"))                # 1D  guna ambang 15M
```
Skala ADX berbeza mengikut timeframe. Ambang `minAdx15m=15` yang digunakan untuk mengklasifikasi
**rejim 4H** dan **bias 1D** adalah kesilapan konsep — rejim 4H akan diklasifikasi `RANGING`
terlalu kerap atau terlalu jarang.

### 🟠 5.8 SEDERHANA — Tiada kawalan risiko peringkat portfolio

`dedupe.maxActiveSignalsTotal = 12` mengira **bilangan**, bukan **pendedahan**. Altcoin kripto
sangat berkorelasi dengan BTC (ρ sering > 0.7). Dua belas isyarat LONG serentak pada alt yang
berbeza adalah **satu pertaruhan beta BTC bersaiz 12×**, bukan dua belas pertaruhan bebas. Monte
Carlo dalam `performance.py` juga membuat bootstrap R **secara bebas**, jadi ia meremehkan
drawdown ekor dengan ketara.

### 🟡 5.9 MINOR — Baris mati dalam `htf_bias`

```python
if adx_value is not None and adx_value < min_adx and bias == "neutral":
    bias = "neutral"          # menetapkan "neutral" apabila sudah "neutral"
```
No-op. Niat asalnya mungkin untuk menurunkan bias lemah kepada neutral; yang ditulis tidak
melakukan apa-apa.

### 🟡 5.10 MINOR — Jurang operasi & dokumentasi

| Isu | Bukti |
|---|---|
| **Tiada `.github/` langsung** | `git log --all --diff-filter=A --name-only \| grep '^.github/'` → **0**. README baris 79–81 dan 126 mendakwa workflow "included". Tiada penyelia 15-minit, tiada CI. |
| Badge tunjuk repo salah | Baris 3–4 → `Acekiller88/Final-Trading-Said`. URL badge juga minta `frontend/badge.json` sedangkan enjin menulis `frontend/data/badge.json` (`main.py:337`) → 404 walaupun nama repo dibetulkan. |
| Bilangan ujian salah | README baris 78, 93, 331 kata **103**; sebenar **120**. |
| `replay-report.json` dijejaki git | README §10 kata "git-ignored"; `git ls-files data/` menyenaraikannya. |
| Tiada `.gitignore` | `git check-ignore` tiada padanan; `.venv/`, `scanner/__pycache__/`, `tests/__pycache__/` kini untracked. |
| `persist.DATA_FILES` kod mati | grep: 1 kejadian (definisi sendiri); tertinggal `universe-snapshot.json` yang `app.js:68` ambil. |
| `build_offline`/`build_preview` tiada pemanggil | grep: hanya docstring sendiri. Dakwaan "rebuilt automatically on every scan" hanya benar bila §5.10 baris pertama dibaiki. |
| Tiada LICENSE | Tiada fail lesen → status guna semula tidak jelas. |
| Tiada lint / typecheck / coverage | Tiada ruff/mypy/pytest-cov dalam `requirements.txt`. |

---

## 6. Panel pakar

**Enam persona dibentuk untuk analisis ini** — setiap satu mewakili kegagalan yang berbeza.
Format: setiap pakar membuka dengan diagnosis, kemudian mereka berdebat, kemudian menumpu.

---

**🎓 Dr. Nadia — Penyelidik Kuantitatif (integriti backtest, statistik pemilihan)**

> "Saya mula dengan soalan yang membosankan: *berapa banyak percubaan yang telah dilakukan?*
> Tiada siapa boleh menjawab, kerana tiada log percubaan. Bailey dan López de Prado sudah
> tunjukkan bahawa tanpa nombor itu, Sharpe ratio tidak boleh ditafsir — titik. Sistem ini ada
> Wilson CI pada kadar menang, yang bagus, tetapi Wilson CI mengukur **ketidakpastian pensampelan**,
> bukan **bias pemilihan**. Ia menjawab soalan yang salah. Dan perhatikan apa yang berlaku pada
> `bySymbol`, `byRegime`, `byQuality`, `byScoreRange` — empat pecahan serentak pada sampel yang
> kini bersaiz **sifar**. Sebaik sahaja ada 30 isyarat, seseorang *akan* jumpa satu pecahan yang
> nampak bagus. Itu bukan penemuan, itu aritmetik."

**⚙️ Puan Farah — Mikrostruktur Pasaran & Pelaksanaan**

> "Saya tak peduli tentang statistik selagi kosnya sifar dalam model. Saya kira semula:
> stop-order masuk + keluar = 0.10% taker. Pada stop 1 ATR dengan ATR% 0.3%, itu **0.33 R setiap
> dagangan**. Break-even win rate naik dari 28.6% ke 38%. Sistem ini menjaringkan RR ≥ 2.5 sebagai
> kriteria kualiti — tetapi RR **kasar**. RR 2.5 kasar dengan kos 0.33R adalah 2.17R bersih. Dan
> kes tepi yang dibenarkan konfigurasi — stop 0.15 ATR pada ATR% 0.1% — kosnya 6.67R. Sistem itu
> akan menjana isyarat yang **mustahil dimenangi** dan menjaringkannya A+."
>
> "Satu lagi: funding. Sistem ini *mengambil* `lastFundingRate` dan memaparkannya, tetapi tidak
> pernah menolaknya. Pada pegangan maksimum 4 jam, anda mungkin melepasi satu penempatan funding
> 8-jam. Kecil, tetapi ia ada dan ia tidak dihitung."

**🎓 Dr. Nadia:** "Setuju, dan itu bermakna Monte Carlo dalam `performance.py` juga salah — ia
bootstrap R yang sudah salah."

---

**🕯️ Encik Rizal — Pengamal Price Action / SMC**

> "Dari sudut craft, enjin ini **lebih jujur daripada kebanyakan**. Ia tidak mendakwa melihat order
> institusi sebenar — docstring `smc.py` kata terus-terang 'price-action proxies'. Swing 2-bar
> fractal dengan pengesahan tertunda. BOS guna close, bukan wick. Sweep ada had 1× ATR supaya
> breakout tulen tidak dikira sebagai sweep. Itu semua betul dan ramai yang buat salah.
>
> Tetapi ada satu lubang yang ketara kepada sesiapa yang benar-benar berdagang konsep ini: **FVG dan
> OB tidak ada hayat**. Dalam praktik, FVG yang sudah diisi sepenuhnya tidak lagi relevan — ia
> *sudah* menjadi likuiditi yang diserap. Di sini, FVG dari 50 bar lalu yang sudah dilalui tiga
> kali masih menyumbang 5 mata. Sama untuk OB. `smartmoneyconcepts` dah selesai ini dengan
> `MitigatedIndex`. Ini bukan nitpick — ia **inflasi skor sistematik** yang akan membuatkan
> isyarat B+ kelihatan seperti A."

**⚙️ Puan Farah:** "Dan ia berinteraksi dengan masalah kos. Skor yang diinflasi → lebih banyak
isyarat → lebih banyak dagangan → lebih banyak yuran. Dua bug yang saling menguatkan."

---

**🔧 Cik Mei — Kejuruteraan Platform & Kebolehpercayaan**

> "Saya lihat perkara yang paling asas dahulu dan ia tidak ada: **tiada `.github/` langsung**.
> Sifar fail, pernah. README mendakwa ada `scanner.yml` cron `*/15` dan `tests.yml`. Kedua-duanya
> tidak wujud. Jadi: tiada penyelia, tiada CI, tiada commit data automatik, tiada pembinaan semula
> dashboard-offline. **Keseluruhan premis 'kos sifar' dalam README §1 bergantung pada automasi yang
> belum ditulis.**
>
> Dan badge — dua-duanya tunjuk ke repo lain (`Final-Trading-Said`), dan URL-nya minta path yang
> salah (`frontend/badge.json` vs `frontend/data/badge.json`). Jadi badge itu 404 walaupun nama
> repo dibetulkan.
>
> Perkara yang saya puji: tulis atomik dengan `fsync` + `os.replace` itu betul. Failover endpoint
> dengan mod degraded itu betul. Dasarkan data dalam git itu pilihan yang sah untuk skala ini —
> `RyanJHamby/stock-screener` buat benda sama dengan 3,800 syiling dan ada cache JSON yang
> mengurangkan panggilan API 74%. Tapi repo itu **ada workflow yang sebenar**."

**🎓 Dr. Nadia:** "Dan tanpa CI, pembetulan 5.1 boleh di-regress minggu depan tanpa sesiapa perasan."

---

**🛡️ Dr. Hakim — Risiko & Saiz Posisi**

> "`maxActiveSignalsTotal = 12` mengira **bilangan**, bukan **pendedahan**. Dalam kripto, altcoin
> berkorelasi tinggi dengan BTC. Dua belas LONG serentak pada dua belas alt yang 'berbeza' ialah
> **satu** pertaruhan beta BTC dengan saiz dua belas kali. Sistem ini tidak mempunyai sebarang
> kawalan korelasi.
>
> Kemudian lihat Monte Carlo: ia bootstrap R **secara bebas**. Itu andaian yang salah secara
> demonstrabel — apabila BTC jatuh, kesemua dua belas isyarat kalah serentak. Bootstrap bebas akan
> meremehkan `maxDDp95` dengan ketara. Nombor drawdown yang dipaparkan adalah **terlalu optimis
> secara sistematik**.
>
> Dan satu lagi: tiada model saiz posisi langsung. `rMultiple` adalah unit yang betul untuk
> dilaporkan, tetapi tiada siapa boleh menterjemahkannya kepada 'berapa % ekuiti' tanpa saiz."

**⚙️ Puan Farah:** "Setuju dengan korelasi. Dan untuk kes tepi 6.67R itu — saya akan jadikan ia
**tolak keras**, bukan sekadar skor rendah. `minAtrPercent` patut dinaikkan, atau
`minStopAtrMultiple` patut ditambah supaya stop terlalu ketat ditolak."

---

**📣 Encik Daniel — Produk, Kepercayaan & Penggunaan**

> "Saya ambil sudut yang berbeza. Sistem ini mengesan kitaran hayat isyarat dengan ketelitian yang
> **lebih baik** daripada `naimkatiman/alpha-scanner` — AMBIGUOUS, resolusi 1-minit, immutability.
> Tetapi tiada siapa boleh melihatnya. Tiada alert. Tiada Telegram. Tiada lejar ketepatan awam.
> Data semasa: **0 isyarat, 0 dagangan selesai**. Papan pemuka memaparkan keadaan kosong.
>
> Ironinya: kekuatan terbesar sistem ini — kejujuran statistik — adalah **aset produk** yang belum
> digunakan. Kebanyakan pemberi isyarat kripto menjual '94% tepat'. Sistem ini secara eksplisit
> menolak berbuat begitu. Itu pembezaan. Tetapi ia perlu **boleh dilihat**: lejar awam yang
> menunjukkan setiap isyarat, setiap keputusan, setiap penafian, dengan Wilson CI dipaparkan
> bersama saiz sampel. Itu adalah produk yang tiada orang lain dalam ruang ini buat dengan jujur."

**🎓 Dr. Nadia:** "Dengan syarat: jangan paparkan pecahan `bySymbol` sehingga ada saiz sampel
minimum. Atau paparkan dengan amaran yang tidak boleh dilepaskan."

---

### Titik pertikaian

**Encik Rizal** menolak cadangan untuk menambah ML scoring seperti `xxvw/SMC_ICT_Library`:

> "Tambah 15 model LightGBM/LSTM sekarang adalah cara terpantas untuk memusnahkan perkara baik
> dalam sistem ini. Anda akan menukar peraturan yang boleh dijelaskan kepada kotak hitam, dan
> dengan 0 isyarat sejarah anda tidak mempunyai data untuk melatih apa-apa pun."

**Dr. Nadia** bersetuju, dengan syarat:

> "Tetapi jangan jadikan 'boleh dijelaskan' sebagai alasan untuk tidak **mengukur**. Pemberat
> 20/20/20/15/10/5/10 adalah tekaan. Sebaik sahaja ada cukup sampel, jalankan regresi mudah:
> adakah komponen skor sebenarnya meramalkan hasil? Jika komponen volatiliti (5 mata) tidak
> mempunyai kuasa pembeza, buang atau berat semula. Itu bukan ML, itu **akauntabiliti asas**."

**Cik Mei** membantah keutamaan:

> "Semua ini menarik, tetapi **tiada satu pun boleh diuji** selagi tiada CI. Saya mahu workflow
> dahulu. Ia 30 minit kerja dan ia menjadikan setiap pembetulan lain selamat."

**Dr. Hakim** tidak bersetuju tentang urutan risiko:

> "CI melindungi kod. Ia tidak melindungi sesiapa daripada **nombor yang salah**. Bug 5.2 dan 5.3
> bermakna setiap metrik yang akan dipaparkan di papan pemuka awam adalah optimis. Jika kita
> hantar itu ke lejar awam Encik Daniel, kita memusnahkan kepercayaan yang kita cuba bina.
> Betulkan nombor dahulu, kemudian automasi, kemudian paparkan."

### Titik tumpuan panel

Panel mencapai persetujuan pada empat perkara:

1. **Nombor mesti betul sebelum ia dipaparkan.** Bug 5.1/5.2/5.3 menjadikan setiap metrik optimis.
   Tiada guna menghantar metrik yang salah ke lejar awam.
2. **CI dahulu, kemudian yang lain** — tetapi *selepas* tiga pembetulan kritikal, kerana CI yang
   hijau atas metrik yang salah lebih berbahaya daripada tiada CI.
3. **Tiada ML sehingga ada data.** 0 isyarat sejarah. Model akan belajar bunyi bising.
4. **Kejujuran statistik ialah produk, bukan kos.** Paparkan Wilson CI + saiz sampel + kos
   dimodelkan secara terbuka. Itu pembezaan yang tiada pesaing tawarkan.

---

## 7. Pelan tindakan berprioriti

### P0 — Betulkan sebelum sebarang nombor dipaparkan

> **STATUS: SELESAI (30 Ogos 2026).** Kelima-lima item P0 telah dilaksanakan
> dalam cawangan ini dan ditutup oleh 25 ujian baharu dalam `tests/test_costs.py`
> (`pytest tests/ -q` → **145 passed**). Nota pelaksanaan:
> - **P0-1** — `copy.deepcopy` sebelum `update_outcomes`; `check_immutability`
>   kini juga **gagal dengan kuat** (`"SAME OBJECT"`) jika diberi senarai
>   beralias, supaya kelas bug ini tidak boleh senyap lagi. `restore_immutable()`
>   memulihkan medan beku sahaja — kemajuan kitaran hayat yang sah dikekalkan
>   (perlakuan lama membuang keseluruhan rekod, yang akan menghidupkan semula
>   isyarat yang sudah ditutup).
> - **P0-2** — modul baharu `scanner/costs.py` (`CostModel`, `apply_costs`).
>   Kos dilapor sebagai `costR` + `rMultipleNet` pada setiap isyarat;
>   `performance.py` kini keluar **kasar dan bersih**. `rMultiple` kekal kasar
>   — kos tidak pernah ditolak secara senyap.
> - **P0-3** — `outcomes.r_multiple()` menggunakan satu penyebut
>   (`|trigger − stop|`) untuk kedua-dua arah.
> - **P0-4** — dilaksanakan sebagai **pintu kos** (`costs.maxCostR`), bukan
>   gandaan ATR tetap. Ini keputusan yang lebih tepat daripada cadangan asal:
>   masalah sebenar ialah `stop_atr × atr_pct`, jadi ambang dalam unit R
>   menyesuaikan diri dengan volatiliti pasangan. `risk.minStopAtrMultiple`
>   juga kini boleh dikonfigurasi (lalai 0.15 = kelakuan asal).
> - **P0-5** — **SELESAI.** `_monte_carlo()` kini *moving-block bootstrap*
>   (blok `round(n^⅓)`, had `n//2`), jadi kelompok kerugian berkorelasi
>   terselamat daripada kocokan. Pada buku 24 dagangan berkelompok:
>   `maxDDp95` bebas = 8.0R vs blok = **9.0R**. Laluan MC juga kini guna siri
>   **bersih** bila kos dimodelkan (`monteCarlo.basis`).
>
> **STATUS P1: P1-1, P1-2, P1-3 SELESAI.** `.github/workflows/tests.yml`
> (pytest 3.11+3.12 + validasi JSON) dan `scanner.yml` (cron `*/15`,
> `permissions: contents: write`, pytest sebelum imbasan, bina semula
> dashboard luar talian, commit hanya bila data berubah, pengawal
> anti-gelung pada commit sendiri). `.gitignore` ditambah,
> `data/replay-report.json` dinyahjejak, `LICENSE` (MIT) ditambah, dan README
> dibaiki (badge → repo + path betul, 103 → 153 ujian, `costs.py` dalam
> susun atur). **Jumlah ujian: 153.**

| # | Tindakan | Usaha | Kesan |
|---|---|---|---|
| P0-1 | **`copy.deepcopy(previous_signals)` sebelum `update_outcomes`** + ujian yang memanggil `scan_once()` sebenar | ~1 jam | Mengaktifkan semula pengawal no-repaint (§5.1) |
| P0-2 | **Model kos**: tolak yuran taker + funding daripada `rMultiple`; lapor `expectancyR` kasar **dan** bersih | ~3 jam | Menunjukkan break-even sebenar 38%, bukan 28.6% (§5.2) |
| P0-3 | **Simetrikan `rMultiple`**: LOSS guna risiko entry sebenar, bukan `-1.0` hardcode | ~1 jam | Membuang berat sebelah optimis yang membesar dengan slippage (§5.3) |
| P0-4 | **Tolak keras stop terlalu ketat**: tambah `minStopAtrMultiple` (~0.5) supaya kes 6.67R mustahil | ~30 min | Menutup kes tepi yang tidak boleh dimenangi |
| P0-5 | **Bootstrap Monte Carlo mengikut kelompok masa** (block bootstrap), bukan bebas | ~2 jam | `maxDDp95` yang jujur (§5.8) |

### P1 — Asas pengesahan & operasi

| # | Tindakan | Usaha | Kesan |
|---|---|---|---|
| P1-1 | **`.github/workflows/tests.yml` + `scanner.yml`** (cron `*/15`, `permissions: contents: write`, bina semula offline HTML). Template: `RyanJHamby/stock-screener` | ~2 jam | Menghidupkan premis README §1 (§5.10) |
| P1-2 | **`.gitignore`** + buang `data/replay-report.json` dari git + **LICENSE** | ~30 min | Kebersihan & kejelasan undang-undang |
| P1-3 | **Betulkan README**: badge (repo + path), 103→120, dakwaan workflow | ~30 min | Dokumentasi sepadan realiti |
| P1-4 | **Walk-forward + luar sampel** dalam `replay.py`: pembahagian masa, purge/embargo, log bilangan percubaan | ~1 hari | Satu-satunya jalan untuk tahu sama ada ada edge (§5.4) |
| P1-5 | **`lookahead-analysis`** gaya freqtrade: jalankan enjin dua kali dengan data masa hadapan diubah; output mesti identik | ~4 jam | *Membuktikan* anti-lookahead kekal benar apabila kod berubah |
| P1-6 | **Status mitigasi FVG/OB** (`MitigatedIndex`, `Swept`); zon terisi tidak menjaringkan mata | ~3 jam | Membuang inflasi skor (§5.6) |
| P1-7 | **Ambang ADX per-timeframe**: `minAdx4h`, `minAdx1d` berasingan dari `minAdx15m` | ~1 jam | Rejim 4H diklasifikasi dengan betul (§5.7) |
| P1-8 | **Kawal pendedahan, bukan bilangan**: had korelasi (maks N LONG serentak dalam kelompok korelasi yang sama) | ~4 jam | Mengeluarkan risiko tersembunyi 12× beta (§5.8) |

### P2 — Nilai & kematangan

| # | Tindakan | Usaha | Kesan |
|---|---|---|---|
| P2-1 | **Lejar ketepatan awam** + alert Telegram/Discord | ~1 hari | Kekuatan kejujuran menjadi boleh dilihat |
| P2-2 | **Mod paper-trading** berasingan dari data live (forward test) | ~1 hari | Bukti luar sampel yang berjalan sendiri |
| P2-3 | **Universe historis** untuk buang bias kemandirian | ~1 hari | Backtest yang boleh dipercayai (§5.5) |
| P2-4 | **PBO / deflated Sharpe** (`esvhd/pypbo`) sebaik ada percubaan berbilang | ~4 jam | Jawapan kepada "berapa besar kemungkinan ini nasib?" |
| P2-5 | **Premium/discount zone + OTE** (rujuk `smartmoneyconcepts`, `xxvw`) | ~4 jam | Menambah konteks SMC yang terbukti berguna |
| P2-6 | **Abastraksi bursa** via CCXT (buang kebergantungan Binance tunggal) | ~1 hari | Daya tahan terhadap geo-block 451 / perubahan API |
| P2-7 | **Kalibrasi pemberat skor secara empirikal** — hanya selepas ≥200 isyarat selesai | ~1 hari | Menggantikan tekaan dengan pengukuran |
| P2-8 | `ruff` + `mypy` + `pytest --cov` dalam CI | ~2 jam | Regressions ditangkap awal |

**Cadangan urutan:** P0-1 → P0-3 → P0-2 → P0-4 → P0-5 → **P1-1 (CI)** → P1-2/3 → P1-6/7 →
P1-4/5 → selebihnya.

Jumlah anggaran untuk P0 + P1: **~4 hari kerja**.

---

## 8. Kesimpulan

Sistem ini mempunyai **teras reka bentuk yang lebih baik daripada reputasi kategori ini** —
sifar kebergantungan, anti-lookahead secara pembinaan, kejujuran statistik yang jarang, tulis
atomik, dasar kegagalan yang betul, dan 120 ujian yang lulus dalam 0.36 saat.

Tetapi pada keadaan semasa, **nombor yang dihasilkannya tidak boleh dipercayai**, atas tiga
sebab yang saya buktikan:

1. Pengawal no-repaint adalah no-op (aliasing objek — `merged[0] is previous[0]` → `True`).
2. Kos transaksi tidak dimodelkan langsung; pada kes tipikal ia **0.33 R/dagangan**, menolak
   break-even win rate dari 28.6% ke 38.0%.
3. `rMultiple` LOSS di-hardcode `-1.0` sementara WIN guna entry sebenar — berat sebelah yang
   **membesar** apabila slippage memburuk.

Dan lapisan pengesahan luar sampel — walk-forward, PBO, deflated Sharpe — **tidak wujud**,
walaupun sistem ini ada ~40 tombol konfigurasi.

Berita baiknya: kesemua P0 adalah **~6 jam kerja**, dan kesemuanya adalah pembetulan kecil pada
kod yang sudah disusun dengan baik. Ini bukan projek yang perlu ditulis semula. Ia projek yang
perlu **dibuktikan**.

---

## 9. Rujukan

**Sistem pembanding**
1. freqtrade — https://github.com/freqtrade/freqtrade · https://pypi.org/project/freqtrade/
2. smart-money-concepts — https://github.com/joshyattridge/smart-money-concepts
3. SMC/ICT Library (MQL5) — https://github.com/xxvw/SMC_ICT_Library
4. Walk-forward engine — https://github.com/oager/walkforward-engine
5. Alpha Scanner — https://github.com/naimkatiman/alpha-scanner
6. Stock screener (Actions + git-JSON) — https://github.com/RyanJHamby/stock-screener
7. freqtrade-ultimate (CPCV walk-forward) — https://github.com/titouannwtt/freqtrade-ultimate
8. pypbo — https://github.com/esvhd/pypbo
9. NautilusTrader (FillModel/FeeModel) — https://nautilustrader.io/
10. CryptoSuperScreener — https://github.com/keithorange/CryptoSuperScreener
11. TradingView-Screener — https://github.com/shner-elmo/TradingView-Screener

**Statistik & integriti backtest**
12. Bailey, Borwein, López de Prado & Zhu (2014), *Pseudo-Mathematics and Financial Charlatanism*,
    Notices of the AMS 61(5), 458–471 — https://www.ams.org/notices/201405/rnoti-p458.pdf
13. Bailey et al. (2017), *The Probability of Backtest Overfitting*, J. Computational Finance 20(4)
    — https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
14. Bailey & López de Prado (2014), *The Deflated Sharpe Ratio*, J. Portfolio Management 40(5), 94–107
    — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
15. *The Dangers of Backtesting* — https://portfoliooptimizationbook.com/book/8.3-dangers-backtesting.html

**Kos pasaran**
16. Binance Futures fee schedule — https://www.bitdegree.org/crypto/tutorials/binance-fees
17. Binance futures maker/taker & funding — https://tradersunion.com/brokers/crypto/view/binance/futures-fees/
