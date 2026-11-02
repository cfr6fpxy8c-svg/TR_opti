# Kajian Perbandingan Sistem & Pelan Naik Taraf

**Projek:** Crypto 15-Minute High-Quality Signal Scanner
**Tarikh kajian:** 2026-08-30
**Skop:** Analisis sistem seumpama (open-source), perbandingan fungsi, dan cadangan penambaikan — dalam rangkungan kekangan **kos sifar (RM0/bulan)**, arkitektur serverless (GitHub Actions + Cloudflare Pages), dan etika *"analysis-only, no trade execution"*.

---

## 1. Ringkasan Eksekutif

Sistem ini berada dalam **niche yang jarang didiami**: scanner isyarat SMC/confluens bermutu tinggi yang (a) berjalan atas infrastruktur kos sifar tanpa pelayan, (b) deterministik dengan jaminan no-lookahead/no-repaint yang diuji, dan (c) jujur secara statistik (win rate berasaskan sampel, skor BUKAN probabiliti).

Tiada satu pun sistem pembanding yang menggabungkan ketiga-tiga ciri itu. Namun kajian ini mengenal pasti **lapan gap utama** berbanding ekosistem terbaik — yang paling ketara: **notifikasi (Telegram)**, **carta candlestick pada kad isyarat**, **konteks derivatives (funding/OI/CVD)**, dan **automasi pengoptimuman parameter (walk-forward)**. Semua boleh ditambah kekal kos sifar.

**Kedudukan keseluruhan: 8.2/10** untuk kategorinya — kekuatan kejuruteraan & integriti statistik di atas purata ekosistem; kelemahan utama pada distribusi maklumat (alerts) dan visualisasi.

---

## 2. Inventori Fungsi Sistem Semasa

| # | Fungsi | Status |
|---|---|---|
| 1 | Universe top-100 dinamik (kedudukan volum 24j, penapis leveraged-token/stable) | ✅ |
| 2 | Multi-timeframe 4H→1H→15M (makro→arah→pelaksanaan) | ✅ |
| 3 | Enjin indikator: EMA 20/50/200, RSI, ADX(+DI), ATR, RelVol, VWAP (Wilder-matematik betul) | ✅ |
| 4 | Struktur pasaran: swing fractal, HH/HL/LH/LL, equal highs/lows, BOS, CHoCH, displacement | ✅ |
| 5 | SMC: liquidity sweep (dengan cap ATR), FVG 3-candle, order block deterministik | ✅ |
| 6 | Model isyarat LONG/SHORT konfluens penuh + 13 peraturan hard-rejection | ✅ |
| 7 | Skor konfluens 100-mata (7 komponen berpemberat) + taraf A+/A/B+ | ✅ |
| 8 | Model risiko: trigger stop-order, SL berasaskan struktur + penampan ATR, TP likuiditi sebenar, RR≥2.5 | ✅ |
| 9 | Kitaran hidup penuh: WAITING→TRIGGERED→WIN/LOSS/EXPIRED/AMBIGUOUS/CANCELLED | ✅ |
| 10 | Penyelesaian lilin kabur (AMBIGUOUS) dengan data 1-minit | ✅ |
| 11 | Enjin prestasi: win rate jujur, profit factor, streak, pecahan pelbagai dimensi | ✅ |
| 12 | Garisan masa terjadual vs sebenar + jitter + kesegaran data | ✅ |
| 13 | Dasar kegagalan API: failover 3-endpoint, mod terdegradsi, tiada data simulasi | ✅ |
| 14 | Validasi integriti data sebelum publikasi + penguatkuasaan immutability (anti-repaint) | ✅ |
| 15 | Mod replay/backtest kausal + semakan determinisme | ✅ |
| 16 | Dedupe: cooldown simbol, hash setup, had isyarat aktif | ✅ |
| 17 | Dashboard gelap 8-seksi, auto-refresh 60s + harga langsung dalam-pelayar 20s | ✅ |
| 18 | Sejarah boleh cari/tapis (arah/taraf/hasil/julat tarikh) | ✅ |
| 19 | Bundel offline satu-fail (data terbenam) | ✅ |
| 20 | Automasi GitHub Actions */15 + commit-hanya-jika-berubah + Pages deploy | ✅ |

**Gap fungsi (dikenal pasti melalui perbandingan):** notifikasi luar (Telegram/Discord), carta candlestick per isyarat, jadual screener universe penuh, konteks derivatives (funding/OI/long-short), penapis sesi/kill-zone, bias 1D, divergensi RSI, zon premium/discount, kalibrasi empirikal skor, optimizer parameter/walk-forward, Monte Carlo, eksport CSV.

---

## 3. Lanskap Sistem Pembanding

### Kategori A — Bot perdagangan penuh (benchmark kejuruteraan)

**[freqtrade](https://github.com/freqtrade/freqtrade)** — bot kripto open-source paling popular (~48–49k bintang GitHub). Paip penuh: backtest → dry-run (paper) → live, merentas 10+ exchange via CCXT. Ciri relevan: **Hyperopt** (pengoptimuman parameter secara ML/Bayesian), **FreqAI** (model ML adaptif), **FreqUI** (dashboard web), integrasi Telegram, **analisis lookahead-bias terbina dalam** (`lookahead-analysis`, `recursive-analysis`), **Edge positioning** (saiz posisi berdasarkan win-rate/RR historis), pelaporan drawdown/Sharpe terperinci, dan pengiraan yuran exchange realistik dalam backtest.

> **Pembelajaran utama untuk kita:** disiplin *lookahead-analysis* (kita ada, dalam bentuk ujian), **hyperopt/walk-forward** (kita tiada), dan **metrik risk-adjusted** (kita separuh).

### Kategori B — Scanner isyarat & alert (persaingan langsung)

- **[OfficialGIGA/crypto-signal-scanner](https://github.com/OfficialGIGA/crypto-signal-scanner)** — scanner 5-minit untuk 10+ pair (Kraken) dengan konvergensi isyarat (3+ isyarat serentak), modul **MarketPulse** (BTC dominance, korelasi silang-pair, deteksi dekorelasi), **alert Telegram pintar** (tetingkap dedupe, had kadar 20/hari, taraf keseveritan), rangkuman AI lokal (Ollama), dashboard FastAPI + SQLite. Kelemahan vs kita: pasangan tetap (bukan top-100 dinamik), tiada SMC sebenar, tiada penjejakan hasil WIN/LOSS berasaskan candle.
- **[foxclerec/binance-screener](https://github.com/foxclerec/binance-screener)** — screener Flask masa nyata dengan panel admin, caching, rate-limit; tapi perlu VPS + Postgres + Redis (bercanggah dengan kos sifar kita).
- **[aleks-ent/pump-dump-crypto-screener](https://github.com/aleks-ent/pump-dump-crypto-screener)** — monorepo TS dengan pipeline fetch→scan→persist→**alert Telegram**, dan yang menarik: **Pump Event Reviewer** — antara muka penlabelan manuall untuk menghasilkan dataset human-labeled bagi menilai semula kualiti deteksi. Konsep "human-in-the-loop review" ini boleh dipinjam untuk sistem kita.

### Kategori C — Pustaka SMC / price-action (persaingan logik teras)

- **[SavviBrax/smartmoneyconcepts-py](https://github.com/SavviBrax/smartmoneyconcepts-py)** — pakej Python untuk FVG, highs/lows, BOS/CHoCH (`close_break=True`), OB, liquidity. Kita sudah setara + lebih ketat (cap ATR pada sweep, pengesahan swing, kekangan umur).
- **[AkhileshSelvan/smc-mcp](https://github.com/AkhileshSelvan/smc-mcp)** — analisis SMC untuk ejen AI (MCP), tanpa lookahead ("swings only used once fractal fully formed" — prinsip sama dengan kita), dengan **mitigation state pada order block** (OB ditanda telah dilawat/hancur — kita belum jeja keadaan mitigasi), dan roadmap premium/discount. Data crypto/forex/saham tanpa kunci API.
- **[Prasad1612/smart-money-concept / SMC-Screener](https://github.com/Prasad1612/SMC-Screener)** — tambah **EQH/EQL** (ada pada kita), **zon Premium/Discount (equilibrium range)** (tiada pada kita), visualisasi matplotlib, output CSV/Google Sheets.
- **[GeneralTradingSarl/Smart-Money-Concepts](https://github.com/GeneralTradingSarl/Smart-Money-Concepts)** (MT5) — turut menonjolkan premium/discount + zon likuiditi visual.

### Kategori D — Dashboard & visualisasi

- **[TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts)** — pustaka carta rasmi TradingView, open-source Apache-2.0, ~40KB, canvas 60fps, candlestick/line/histogram, plugin system; wajib kredit atribusi TradingView. Ini penyelesaian standard industri untuk carta dalam dashboard statik — digunakan bersama [446 indikator komuniti](https://github.com/deepentropy/lightweight-charts-indicators) dan [68 alat lukisan](https://github.com/deepentropy/lightweight-charts-drawing).
- **[20wiz/crypto-trading-dashboard](https://github.com/20wiz/crypto-trading-dashboard)** (Streamlit + backtest interaktif), **[akshada2712/Real-time-Crypto-Analysis](https://github.com/akshada2712/Real-time-Crypto-Analysis)** (LSTM ramalan — kita sengaja TIDAK menuntut sebarang ramalan).

### Kategori E — Analitik derivatives / macro

- **[TradeBobbyTerminal](https://github.com/SoCloseSociety/TradeBobbyTerminal)** — terminal swa-hos gaya Bloomberg: **scanner makro ICT/SMC + orderflow crypto langsung (funding, open interest, liquidation, CVD, order book)** dari Binance, tanpa kunci API.
- **[beomsun0829/Open_Interest_Telegram_Alerts](https://github.com/beomsun0829/Open_Interest_Telegram_Alerts)** — alert OI & nisbah long/short setiap 5 minit.
- **[funding-rate-heatmap](https://github.com/StephanAkkerman/funding-rate-heatmap)** & raksi funding-arb scanner lain — heatmap kadar pembiayaan merentas exchange.

> **Pembelajaran utama:** isyarat harga sahaja adalah " separuh gambar" untuk pasaran perpetual — funding & OI adalah konteks institusi sebenar yang *percuma* pada API awam Binance futures.

---

## 4. Matriks Perbandingan Fungsi

✅ penuh · 🟡 separa · ❌ tiada

| Fungsi | **Kita** | freqtrade | GIGA scanner | smc-mcp | TradeBobby |
|---|---|---|---|---|---|
| Universe top-100 dinamik | ✅ | 🟡 (pairlist) | ❌ (tetap) | ❌ | ❌ |
| Multi-timeframe 4H/1H/15M | ✅ | ✅ | 🟡 (1h/4h/1d) | ❌ (roadmap) | ✅ |
| Logik SMC (BOS/CHoCH/FVG/OB/sweep) | ✅ | ❌ | ❌ | ✅ | ✅ |
| Skor konfluens + taraf kualiti | ✅ | ❌ | 🟡 (convergence count) | ❌ | ❌ |
| Hard rejection rules eksplisit | ✅ | 🟡 | 🟡 | ❌ | ❌ |
| Outcome engine (WIN/LOSS/AMBIG/EXPIRED) berasaskan candle | ✅ | ✅ | ❌ | ❌ | ❌ |
| Penyelesaian AMBIGUOUS dengan data 1m | ✅ | ❌ | ❌ | ❌ | ❌ |
| No-lookahead diuji + anti-repaint (immutability) | ✅ | ✅ | ❌ | 🟡 | ❌ |
| Win rate jujur (sampel dipaparkan) | ✅ | ✅ | ❌ | n/a | ❌ |
| Replay/backtest kausal | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Notifikasi Telegram/Discord** | ❌ | ✅ | ✅ | n/a | 🟡 |
| **Carta candlestick per isyarat** | ❌ | 🟡 (plot) | ❌ | ❌ | ✅ |
| **Jadual screener universe penuh** | ❌ | 🟡 | 🟡 | ❌ | ✅ |
| **Konteks derivatives (funding/OI/CVD/liquidation)** | ❌ | 🟡 | 🟡 (dominance/korelasi) | ❌ | ✅ |
| **Penapis sesi / kill-zone (ICT)** | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Optimizer parameter (hyperopt/walk-forward)** | ❌ | ✅ | ❌ | ❌ | ❌ |
| Metrik risk-adjusted (Sharpe/DD/expectancy) | 🟡 | ✅ | ❌ | ❌ | ❌ |
| Divergensi RSI | ❌ | ✅ | ❌ | ❌ | ❌ |
| Premium/discount zones | ❌ | ❌ | ❌ | ❌ (roadmap) | ✅ |
| ML / AI | ❌ (sengaja) | ✅ (FreqAI) | 🟡 (LLM ringkasan) | ❌ | ❌ |
| Kos siri bulanan | **RM0** | RM0 (perlu VPS utk live) | RM0+VPS? | RM0 | RM0+VPS |
| Pelayan diperlukan | **Tiada** | Ya (live) | Ya | Tidak | Ya |

**Kesimpulan matriks:** kekuatan unik kita ialah *kualiti isyarat + integritas hasil + kos sifar tanpa pelayan*. Kekuatan pembanding yang kita lacks: *distribusi (alerts), visual (charts/screener), konteks derivatives, dan automasi optimizer*.

---

## 5. Analisis Mendalam

### 5.1 Kekuatan yang perlu dikekalkan (unique selling points)

1. **Integritas deterministik** — replay `--determinism-check`, ujian no-lookahead (sama falsafah dengan `lookahead-analysis` freqtrade), immutability diuatkuasakan dalam kod bukan dokumen.
2. **Kejujuran statistik** — win rate tidak pernah termasuk AMBIGUOUS/EXPIRED; saiz sampel dipaparkan; skor tidak dituntut sebagai probabiliti. Kebanyakan scanner pembanding menuntut "high-probability signals" tanpa asas — kita tidak.
3. **Kualiti berbanding kuantiti** — 13 hard-rejection + skor ≥80; log menolak setup dengan alasan. GIGA scanner mengejar konvergensi≥3; freqtrade mengejar optimasi profit. Kita mengejar *ketepatan definisi setup*.
4. **Arkitektur kos sifar sejati** — tiada VPS/DB/Redis (berbeza dengan binance-screener Flask); GitHub Actions sebagai cron + compute; JSON-in-git sebagai DB; Cloudflare Pages/Pages sebagai CDN.
5. **Ketahanan data** — rantaian failover 3-endpoint dengan penurunan taraf (degraded) yang jujur dipaparkan.

### 5.2 Gap (diurut mengikut kesan kepada pengguna)

| # | Gap | Bukti ekosistem | Kesan |
|---|---|---|---|
| 1 | Tiada notifikasi luar — pengguna mesti buka dashboard | Semua scanner saingan ada Telegram; freqtrade ada Telegram control penuh | **Tinggi** — isyarat WAITING_TRIGGER 3-jam boleh terlepas |
| 2 | Tiada carta candlestick pada kad isyarat — pengguna tak nampak struktur (sweep/CHoCH/FVG) secara visual | Lightweight Charts 40KB, Apache-2.0; TradeBobby penuh carta | **Tinggi** — kebolehpercayaan & auditabiliti isyarat |
| 3 | Hanya breadth + top-10 volum dipapar; 100 simbol yang diimbas hilang selepas setiap scan | Screener table standard dalam semua scanner | **Sederhana-Tinggi** |
| 4 | Tiada konteks derivatives (funding/OI/long-short/liquidation) untuk pasaran perpetual | TradeBobby, funding-heatmap, OI alerts | **Sederhana-Tinggi** — funding ekstrem adalah penapis arah yang kuat |
| 5 | Tiada optimizer parameter / walk-forward / sensitivity | Hyperopt freqtrade | **Sederhana** — parameter sekarang "sensible defaults" belum divalidasi secara sistematis |
| 6 | Metrik prestasi tiada expectancy/max-drawdown/Sharpe-rasio-R | Laporan freqtrade | **Sederhana** |
| 7 | Tiada bias 1D / penapis sesi (kill-zone Asia-London-NY) | TradeBobby (ICT macro) | **Sederhana** — konsep teras ICT yang logik dengan SMC |
| 8 | Tiada divergensi RSI / premium-discount | messified indicator; pustaka SMC | **Rendah-Sederhana** |

### 5.3 Kekangan reka bentuk yang mesti dihormati

- **GitHub Actions:** ~700 request API per scan cukup, tetapi tambahan endpoint per-simbol mesti di belanjakan; storage git tidak boleh membesar tanpa had (simpanan lilin mesti dipadatkan & dibersihkan).
- **15-minit kadence & tiada websocket** — semua ciri mesti berfungsi secara batch.
- **Etika analisis-sahaja** — tiada pelaksanaan order; cadatan "position sizing calculator" hanya paparan.
- **Kos sifar** — tiada perkhidmatan berbayar; Telegram Bot API & Binance derivatives endpoints adalah percuma.

---

## 6. Cadangan Naik Taraf (Backlog Berkeutamaan)

### FAZA 1 — Quick wins (kos sifar, usaha kecil, nilai segera)

**F1. Notifikasi Telegram/Discord** *(usaha: S — paling bernilai)*
Enjin tulis `data/notifications.json` (peristiwa: isyarat baharu, TRIGGERED, WIN/LOSS) apabila berlaku; langkah workflow menghantar melalui Bot API menggunakan `secrets.TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (curl sahaja, tiada pelayan). Discord webhook lebih mudah lagi (satu POST). Mesej format: SYMBOL arah taraf skor, trigger/TP/SL/RR + pautan dashboard. *Menutup Gap #1 sepenuhnya.*

**F2. Jadual screener universe penuh** *(usaha: S-M)*
Simpan `data/universe-snapshot.json` — **ditulis-ganti setiap scan** (tiada pertumbuhan git): simbol, harga, bias 4H/1H, regime, RSI, ADX, ATR%, relVol, jarak ke swing terdekat. Tab baharu dashboard: jadual boleh isih + tapis (e.g. "semua strong_bullish 4H dengan relVol>1.5"). Data ini *sudah dikira* oleh enjin tetapi dibuang hari ini.

**F3. Metrik prestasi lanjutan** *(usaha: S)*
Tambah pada `performance.py`: **expectancy (R)**, **max drawdown (R, peak-to-trough)**, **Sharpe-rasio-R (min R / sisihan R)**, **slippage purata trigger-vs-fill** (data gap-open sudah sedia dalam outcome engine). Papar sampel saiz seperti biasa.

**F4. Lencana status README + uptime** *(usaha: S)*
Lencana Actions + lencana "last scan" dijana daripada `system-status.json` (JSON-to-badge via endpoint percuma atau SVG dikomit setiap scan). Kepercayaan sistem naik.

**F5. Eksport CSV + PWA manifest** *(usaha: S)*
Butang "Export CSV" pada jadual sejarah (jana dalam pelayar daripada JSON — tiada backend). `manifest.webmanifest` + ikon supaya dashboard boleh "dipasang" di telefon.

### FAZA 2 — Pendalaman analitis (usaha sederhana)

**F6. Konteks derivatives sebagai konfluens** *(usaha: M — nilai analitik tertinggi)*
Endpoint awam percuma Binance futures:
- `/fapi/v1/premiumIndex` — **funding rate SEMUA simbol dalam SATU panggilan** (murah dari segi bajet request);
- `/futures/data/openInterestHist` & `topLongShortAccountRatio` — per-simbol (contoh: sampel top-30 sahaja setiap scan).
Gunakan sebagai: (a) panel makro dashboard (heatmap funding seluruh universe — seperti funding-rate-heatmap), (b) **tag konteks pada kad isyarat** (funding ekstrem melawan arah = amaran), (c) komponen pemarkahan konfigurabel (`scoring.weights.derivatives`, kurangkan berat komponen lain secara berkadar). Peraturan keras boleh ditambah: *tolak LONG jika funding > +0.10% / 8j (pasaran terlebih long)* — contoh, boleh dimatikan melalui konfigurasi.

**F7. Penapis sesi / kill-zone (ICT)** *(usaha: S-M)*
Konfigurasi tetingkap masa UTC (Asia range, London open, NY open — lalai sesuai kripto 24/7 tetap boleh ditetapkan). Isyarat dalam kill-zone aktif dapat bonus pemarkahan kecil; dipaparkan pada kad. Deterministik sepenuhnya (masa candle tersedia).

**F8. Bias 1D sebagai gerbang tambahan** *(usaha: S)*
Tambah bingkai `1d` pada `analysis.py` (bajet: +100 request — masih < 700 dengan penjimatan: 1d hanya perlu 220 lilin). Bias harian bertentangan = penolakan keras baharu atau pengurangan pemarkahan HTF.

**F9. Divergensi RSI (pivot-based, deterministik)** *(usaha: M)*
Divergensi biasa+tersembunyi pada 15M/1H menggunakan pivot swing yang sedia ada (tiada lookahead — pivot mesti disahkan). Komponen momentum baharu / penapis pembalikan.

**F10. Zon Premium/Discount (equilibrium)** *(usaha: M)*
Daripada swing range terkini: kira 50% equilibrium; paparkan pada kad + carta; penapis pilihan: masuk LONG hanya dalam discount. Standard dalam pustaka SMC pembanding.

**F11. Kalibrasi empirikal skor (bukan probabiliti!)** *(usaha: M — mengukuhkan kejujuran statistik)*
Selepas N hasil terkumpul per tongkol skor (80–84/85–89/90–100), terbitkan *win rate historis empirikal per tongkol dengan sela keyakinan Wilson* pada metodologi dashboard. Ini menukar "skor bukan probabiliti" kepada "skor + rekod prestasi historis per kumpulan (n=…)" — lebih jujur dan lebih berguna daripada mana-mana scanner pembanding.

**F12. Keadaan mitigasi OB/FVG + watchlist** *(usaha: M)*
Jeja sama ada OB/FVG telah dilawati (mitigated) — seperti `smc-mcp` — supaya isyarat tidak bergantung pada zon yang telah dimakan. `data/watchlist.json` boleh edit pengguna: amaran apabila harga hampir paras watchlist (melalui notifikasi F1).

### FAZA 3 — Kematangan kuantitatif (usaha besar, lokal sahaja)

**F13. Optimizer parameter + walk-forward** *(usaha: L)*
CLI lokal: `python -m scanner.optimize --grid config/grids.json --symbols ... --walk-forward` — sweep merentas subset parameter (minScore, minRr, swingLookback, displacement multiple, dll), replay setiap kombinasi, dan laporkan jadual hasil + **kestabilan antara-fold** (varians metrik merentas blok masa). Amaran overfit terbina dalam: hanya terima parameter yang kekal positif pada fold out-of-sample. Ini versi ringkas Hyperopt freqtrade, sesuai batch/CI.

**F14. Monte Carlo pada hasil replay** *(usaha: M)*
Bootstrap R-multiple terkumpul → taburan drawdown & ekuiti (p5/p50/p95) dipapar pada dashboard prestasi. Memberi konteks statistik kepada profit factor.

**F15. Sumber data pelbagai-exchange (ccxt) untuk mod replay lokal** *(usaha: M)*
Pengesahan silang data + kelewatan (data Byzantine fault tolerance). Lokal sahaja — tindakan tetap pada API awam Binance.

**F16. Pemeriksa "reviewer" isyarat (human-in-the-loop)** *(usaha: M)*
Diilhamkan oleh pump-dump-screener: tab pilihan untuk menanda isyarat "valid setup / noise / misclassified" dengan nota — disimpan dalam `data/reviews.json`; kekal dalam skop analisis, membantu tuning berkala.

### Perkara yang sengaja TIDAK dicadangkan

- ❌ **Pelaksanaan dagangan / order** — melanggar objektif analisis-sahaja dan meningkatkan risiko+kompleksiti.
- ❌ **Model ML ramalan harga** — menuntut sesuatu yang tidak boleh dijamin; FreqAI bagus untuk freqtrade tapi bercanggah dengan falsafah kejujuran sistem ini.
- ❌ **Apa-apa perkhidmatan berbayar** (VPS, DB terurus, data feed premium, cron service).
- ❌ **Websocket / real-time sub-minit** — bercanggah dengan arkitektur batch kos-sifar; nilai sebenar rendah untuk isyarat 15M.

### Susunan keutamaan disyorkan

| Kedudukan | Cadangan | Usaha | Kesan |
|---|---|---|---|
| 1 | F1 Telegram/Discord alerts | S | ★★★★★ |
| 2 | F2 Jadual screener universe | S-M | ★★★★☆ |
| 3 | F3 Metrik expectancy/DD/Sharpe | S | ★★★★☆ |
| 4 | F6 Konteks funding/OI | M | ★★★★☆ |
| 5 | F5 CSV + PWA | S | ★★★☆☆ |
| 6 | F4 Lencana status | S | ★★★☆☆ |
| 7 | F8 Bias 1D | S | ★★★☆☆ |
| 8 | F7 Kill-zone filter | S-M | ★★★☆☆ |
| 9 | F11 Kalibrasi skor empirikal | M | ★★★☆☆ |
| 10 | F3b Slippage + F10 Premium/discount | M | ★★★☆☆ |
| 11 | F9 Divergensi RSI | M | ★★☆☆☆ |
| 12 | F13 Walk-forward optimizer | L | ★★★★☆ (jangka panjang) |

---

## 7. Rujukan

- freqtrade — https://github.com/freqtrade/freqtrade · https://freebacktesting.com/code-based/freqtrade
- OfficialGIGA/crypto-signal-scanner — https://github.com/OfficialGIGA/crypto-signal-scanner
- SavviBrax/smartmoneyconcepts-py — https://github.com/SavviBrax/smartmoneyconcepts-py
- AkhileshSelvan/smc-mcp — https://github.com/AkhileshSelvan/smc-mcp
- Prasad1612/SMC-Screener & smart-money-concept — https://github.com/Prasad1612/SMC-Screener
- GeneralTradingSarl/Smart-Money-Concepts (MT5) — https://github.com/GeneralTradingSarl/Smart-Money-Concepts
- TradingView Lightweight Charts — https://github.com/tradingview/lightweight-charts
- deepentropy/lightweight-charts-indicators — https://github.com/deepentropy/lightweight-charts-indicators
- SoCloseSociety/TradeBobbyTerminal — https://github.com/SoCloseSociety/TradeBobbyTerminal
- beomsun0829/Open_Interest_Telegram_Alerts — https://github.com/beomsun0829/Open_Interest_Telegram_Alerts
- StephanAkkerman/funding-rate-heatmap — https://github.com/StephanAkkerman/funding-rate-heatmap
- aleks-ent/pump-dump-crypto-screener — https://github.com/aleks-ent/pump-dump-crypto-screener
- foxclerec/binance-screener — https://github.com/foxclerec/binance-screener
- 20wiz/crypto-trading-dashboard — https://github.com/20wiz/crypto-trading-dashboard
- messified/tradingview-crypto-indicator — https://github.com/messified/tradingview-crypto-indicator

*Dokumen ini adalah analisis kejuruteraan/product, bukan nasihat kewangan. Sistem kekal sebagai alat analisis sahaja.*
