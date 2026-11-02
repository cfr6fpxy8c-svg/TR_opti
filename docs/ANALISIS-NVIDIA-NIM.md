# Analisis NVIDIA NIM (build.nvidia.com) — adakah sesuai untuk sistem ini?

**Tarikh:** 30 Ogos 2026 · **Katalog:** 100 model (disahkan dari halaman itu sendiri, 5 halaman × 24)

---

## 1. Adakah API percuma itu benar?

**Ya — tetapi hadnya bukan seperti yang kebanyakan artikel katakan.**

| Perkara | Fakta | Sumber |
|---|---|---|
| Kunci API percuma | Ya. Sertai NVIDIA Developer Program (emel sahaja, **tiada kad kredit**), dapat kunci `nvapi-…` | build.nvidia.com/settings/api-keys |
| Base URL | `https://integrate.api.nvidia.com/v1` — **OpenAI-compatible** | disahkan merentas semua sumber + dokumen NVIDIA |
| Senarai model hidup | `GET https://integrate.api.nvidia.com/v1/models` | **guna ini**, bukan artikel |
| **Sistem kredit** | **SUDAH DIMANSUHKAN.** Diganti dengan had kadar. | staf NVIDIA (`sophwats`), forum rasmi, Sept 2025 |
| Had kadar | **Berbeza mengikut model dan TIDAK diterbitkan.** Anda nampak had anda sendiri di penjuru kanan atas build.nvidia.com | staf NVIDIA, forum rasmi |
| "~40 RPM", "1,000 kredit" | Angka pihak ketiga. 40 RPM mungkin masih betul secara am; **1,000 kredit sudah lapuk** | artikel blog — jangan reka bentuk atasnya |
| Tempoh "trial" | **Tidak dihadkan masa.** NVIDIA maksudkan: prototyping, research, development, testing, learning | staf NVIDIA, forum rasmi |

**Petikan autoritatif** (forum rasmi NVIDIA, `sophwats`, 10 Sept 2025):

> *"We no longer use a credit-based system for build.nvidia.com. As you noted, this has been
> replaced by rate limits for trial usage. The rate limits vary for each model, and we do not
> publish those. However, you can see your maximum rate limit in the top right of
> build.nvidia.com."*

> *"The trial period is not limited by a time period. We use 'trial' to mean any use for
> prototyping, research, development, testing, learning, etc."*

### ⚠️ Tangkapan undang-undang yang penting

NVIDIA mentakrifkan **production** sebagai penggunaan melampaui development/testing/research/
evaluation — **termasuk melayani pengguna akhir sebenar dan urus niaga perniagaan** — dan ia
memerlukan lesen **NVIDIA AI Enterprise**, bukan kunci percuma.

Papan pemuka awam yang orang lihat dan bertindak atas isyaratnya **berada di kawasan kelabu ini**.
Bacaan paling selamat: kunci percuma sesuai untuk **lapisan kajian/pengesahan dalaman**, bukan
untuk perkhidmatan isyarat awam. Ini keputusan anda, tetapi anda patut memutuskannya sedar,
bukan secara kebetulan.

---

## 2. Soalan yang lebih penting: adakah AI menyelesaikan masalah sebenar sistem ini?

Saya perlu terus terang di sini, kerana jawapannya tidak selesa.

Lima jurang terbesar daripada audit (`docs/KAJIAN-SISTEM.md` §5):

| Jurang | Adakah LLM menyelesaikannya? |
|---|---|
| Tiada pengesahan luar sampel / walk-forward | **Tidak.** Ini perlukan data dan statistik, bukan penalaran. |
| Tiada kesedaran acara (token unlock, CPI, FOMC) | **Tidak sendirian.** LLM ada *knowledge cutoff* dan mereka angka. Perlukan **sumber data** (feed kalendar/berita) dahulu; LLM hanya meringkaskannya. |
| FVG / Order Block tiada status mitigasi | **Tidak.** Ini aritmetik atas candle. |
| Kos transaksi tidak dimodelkan | **Tidak.** (Sudah dibaiki dalam P0-2.) |
| Tiada kawalan risiko korelasi portfolio | **Tidak.** Ini matriks korelasi. |

**Kesimpulan jujur: LLM tidak menyelesaikan satu pun daripada lima jurang teratas.**

Yang LLM *boleh* tambah ialah kategori nilai yang berbeza — dan ia nyata, tetapi jangan
terkeliru antara keduanya. Alat pengesahan sebenar ialah **P1-4 (walk-forward)**, yang
langsung tidak perlukan AI.

---

## 3. Di mana LLM benar-benar menambah nilai

### 🥇 A. Penyemak adversarial (nilai tertinggi)

Beri model **konteks penuh** isyarat — kedua-dua arah, sebab penolakan, baris screener,
bias 4H/1H/1D, funding, sesi, RR, `costR` — dan minta: *"hujahkan menentang dagangan ini;
cari sebarang percanggahan dalaman."*

**Mengapa ini berharga:** enjin ini menolak berdasarkan **peraturan yang disenaraikan**. Ia
tidak boleh menangkap *kombinasi* bendera merah yang anda tidak fikirkan untuk senaraikan:

> 4H bullish + 1D bearish + funding +0.09% (crowd long melampau) + 30 minit sebelum
> penutupan London + relVol 3.5 (mungkin blow-off) + kos 0.22R

Tiada satu pun daripada itu melanggar peraturan keras. Bersama-sama, ia setup yang saya
tidak mahu ambil. **Inilah yang LLM boleh nampak dan peraturan tidak.**

### 🥈 B. Naratif untuk lejar awam

Tukar JSON isyarat kepada 3 ayat bahasa biasa. Ini terus menyokong strategi
"kejujuran sebagai produk" — pembezaan yang tiada pesaing tawarkan. Model kecil memadai.

### 🥉 C. Pembantu kajian atas output backtest

Baca pecatan `performance.json`, cadangkan hipotesis untuk diuji. **Manusia dalam gelung
sahaja** — tidak pernah auto-apply.

---

## 4. Tiga bahaya teknikal yang mesti direka bentuk lebih awal

### 🚨 4.1 Pencemaran lookahead dalam backtest — yang paling berbahaya

Jika anda menyemak isyarat **sejarah** dengan LLM yang data latihannya merangkumi apa yang
berlaku *selepas* tarikh isyarat itu, model tersebut **tahu** hasilnya. Sebarang backtest
yang memasukkan penilaian LLM adalah **tercemar** — dan ia akan kelihatan *bagus*, sebab
model itu meniru hindsight.

**Peraturan:** jalankan penasihat **hanya secara live/ke hadapan**. Jangan sekali-kali dalam
`replay.py`. (Alternatif — guna model dengan knowledge cutoff sebelum tempoh backtest —
rapuh dan tidak berbaloi.)

### 🚨 4.2 Determinisme pecah

`replay.py --determinism-check` membuktikan output byte-identical. Panggilan LLM tidak
deterministik, jadi ia memecahkan jaminan itu.

**Penyelesaian:** penasihat berjalan sebagai **laluan berasingan selepas** penjanaan isyarat,
menulis ke medan berasingan (`advisor.*`), dan `--determinism-check` mengecualikan medan itu.

### 🚨 4.3 Matematik had kadar

| Skop | Panggilan/hari | Purata/min | Bawah 40 RPM? |
|---|---|---|---|
| Semak isyarat yang lulus sahaja (≤12/imbasan) | ≤ 1,152 | 0.8 | ✅ selesa |
| **Batch 1 panggilan/imbasan** (disyorkan) | **96** | **0.07** | ✅ sangat selesa |
| Semak semua 100 baris screener | 9,600 | 6.7 | ⚠️ meletup dalam bursts |

**Syor: batch semua isyarat satu imbasan ke dalam SATU panggilan.** Lebih murah, lebih cepat,
dan model boleh membandingkan isyarat antara satu sama lain (yang mustahil jika dipanggil
berasingan).

---

## 5. Prinsip seni bina yang tidak boleh dikompromi

> ### LLM tidak boleh memasuki laluan penjanaan isyarat atau skor 100 mata.

Sebab:

1. **Kebolehauditan** — pembezaan sistem ini ialah setiap mata boleh dijejak ke peraturan.
   Pendapat LLM tidak boleh.
2. **Determinisme** — lihat §4.2.
3. **Boleh ulang** — `performance.json` yang diterbitkan mesti boleh dihasilkan semula.
4. **Kejujuran** — README sudah berjanji skor bukan kebarangkalian. Menambah "AI yakin 78%"
   akan memungkiri janji itu.

**Reka bentuk yang betul:** penasihat adalah **lapisan nasihat luar jalur** yang menulis medan
berlabel jelas, dan **tidak boleh** mengubah `score`, `status`, `quality`, atau `rMultiple`.

```
enjin (deterministik)  →  signals.json  →  [score, status, ...]  ← TIDAK DISENTUH
                                  ↓
                        penasihat (NIM, pilihan)
                                  ↓
                        advisor: { model, verdict, flags[], rationale, at }
                                  ↓
                        papan pemuka: paparkan sebagai NASIHAT, bukan skor
```

---

## 6. Cadangan model

> ⚠️ **ID model berubah.** Sahkan dengan `GET https://integrate.api.nvidia.com/v1/models`
> sebelum hardcode. Keluarga di bawah stabil; versi tepatnya mungkin berbeza.

| Tugas | Keluarga model | Sebab |
|---|---|---|
| **A. Penyemak adversarial** | **Model penalaran**: `nvidia/llama-3.1-nemotron-ultra-253b-v1`, atau `deepseek-ai/deepseek-r1` | Satu-satunya tugas yang berbaloi dibelanjakan kualiti. Perlu menimbang banyak faktor serentak dan mencari percanggahan. |
| **B. Naratif** | Kecil/laju: `nvidia/llama-3.3-nemotron-super-49b-v1.5`, `qwen/qwen3-235b-a22b` (~17B aktif) | Tugas isipadu; bar kualiti rendah; latensi penting |
| **C. Pembantu kajian** | Konteks panjang: Nemotron 3 Super (1M ctx) atau `deepseek-ai/deepseek-v4-pro` | Perlu memuat seluruh pecatan prestasi + konfigurasi |
| **Konteks acara (masa depan)** | `nvidia/nemotron-retriever-*` (embedding 1B) + feed berita | Untuk RAG atas berita — **bukan** LLM sahaja |
| **Keselamatan kandungan** | Model Nemotron content-safety (2M panggilan/30 hari) | Hanya jika papan pemuka awam menerima input pengguna |

### Syor permulaan

**Mula dengan TEPAT satu model, satu tugas: penyemak adversarial (A), batch 1 panggilan
setiap imbasan.**

- ~96 panggilan/hari — jauh di bawah mana-mana had yang munasabah
- Satu-satunya tempat penalaran LLM menambah sesuatu yang peraturan tidak boleh
- Boleh dimatikan sepenuhnya tanpa menjejaskan enjin
- Boleh diukur: jejak sama ada `advisor.veto` meramalkan hasil buruk, **ke hadapan**

Jangan mula dengan lima model. Anda tidak akan tahu yang mana membantu.

---

## 7. Bentuk implementasi (muat dengan reka bentuk stdlib-sahaja)

Enjin ini tiada kebergantungan runtime, jadi panggilan HTTP guna `urllib` — sama seperti
`market_data.py`. Tiada `openai`, tiada `requests`.

### `config/strategy.json`

```jsonc
"advisor": {
  "enabled": false,                 // MATI secara lalai
  "provider": "nvidia-nim",
  "baseUrl": "https://integrate.api.nvidia.com/v1",
  "model": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
  "apiKeyEnv": "NVIDIA_API_KEY",    // baca dari env, JANGAN dari fail
  "timeoutSeconds": 30,
  "maxRetries": 2,
  "batchPerScan": true,             // 1 panggilan untuk semua isyarat
  "temperature": 0.2,               // rendah: kita mahu konsisten, bukan kreatif
  "seed": 1337,                     // cuba deterministik (tidak dijamin semua model)
  "advisoryOnly": true,             // keras: tidak boleh diubah ke false
  "inReplay": false,                // keras: halang lookahead (§4.1)
  "maxSignalsPerCall": 12
}
```

### Invarian yang mesti diuji

```python
# penasihat TIDAK BOLEH menyentuh medan ini — ujian mesti mematuhinya
PROTECTED = {"score", "quality", "components", "status", "outcome",
             "rMultiple", "rMultipleNet", "triggerPrice", "stopLoss", "takeProfit"}
```

Ujian yang perlu ditulis (semua boleh diuji **luar talian** dengan transport palsu, sama
seperti `FakeClient` sedia ada — jadi CI tidak perlu kunci API):

1. `advisor` gagal (timeout/429/JSON rosak) → isyarat **tidak berubah**, imbasan **terus exit 0**
2. `advisor` cuba menulis `score` → **ditolak**, dicatat sebagai ralat
3. Tiada `NVIDIA_API_KEY` dalam env → penasihat **dilangkau secara senyap**, tiada panggilan
4. `inReplay=True` + `advisor.enabled=True` → **ralat konfigurasi**, bukan diam
5. `advisoryOnly` tidak boleh dimatikan → pengesahan konfigurasi gagal
6. JSON respons tidak mengikut skema → ditolak, bukan dijangka betul

---

## 8. Keputusan

**Guna NIM? Ya — tetapi untuk satu tugas sahaja, sebagai penasihat luar jalur.**

| | |
|---|---|
| Berbaloi? | **Ya**, untuk penyemakan adversarial + naratif lejar |
| Model | Satu model penalaran (Nemotron Ultra 253B / DeepSeek R1), batch 1 panggilan/imbasan |
| Kos | RM0, ~96 panggilan/hari |
| Boleh validate signal? | **Tidak dalam erti kata statistik.** Ia boleh *menyemak*, bukan *mengesahkan*. Pengesahan sebenar = walk-forward (P1-4), yang tidak perlukan AI. |
| Risiko utama | Pencemaran lookahead dalam backtest (§4.1) dan takrif "production" NVIDIA (§1) |
| Keutamaan vs P1-4/P1-6 | **Selepas keduanya.** AI tidak menggantikan pengesahan luar sampel. |

**Urutan yang saya syorkan:** P1-4 (walk-forward) → P1-6 (mitigasi FVG/OB) → penasihat NIM.

Sebab: jika anda tambah penasihat AI sebelum ada pengesahan luar sampel, anda tidak akan
pernah tahu sama ada penasihat itu membantu atau hanya menambah bunyi bising yang
kedengarannya meyakinkan.

---

## 9. Rujukan

1. Katalog model — https://build.nvidia.com/models (100 model, disahkan 30 Ogos 2026)
2. Penjanaan kunci — https://build.nvidia.com/settings/api-keys
3. Forum rasmi NVIDIA, pengesahan sistem kredit dimansuhkan + takrif trial —
   https://forums.developer.nvidia.com/t/request-more-4-000-credits-option-on-build-nvidia-com/344567
4. Dokumen NeMo (base URL + `NVIDIA_API_KEY`) —
   https://docs.nvidia.com/nemo/datadesigner/concepts/models/default-model-settings
5. Rujukan OpenAI-compatibility + format `chat/completions` —
   https://www.promptfoo.dev/docs/providers/nvidia/
6. Panduan kunci percuma (nota: angka kreditnya sudah lapuk) —
   https://decodethefuture.org/en/how-to-get-nvidia-api-key-free/
