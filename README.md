# Crypto Trend MVP

[![CI](https://github.com/bilalozok/crypto-trend-mvp/actions/workflows/ci.yml/badge.svg)](https://github.com/bilalozok/crypto-trend-mvp/actions/workflows/ci.yml)

Basit bir **FastAPI + PostgreSQL/SQLite** tabanlı kripto mum verisi (candles) servisi.
Amaç: veri çekme, saklama, son kayıtları listeleme ve test/lint/CI disiplinini oturtmak.

---

## Özellikler

- FastAPI REST API
- SQLAlchemy ile PostgreSQL/SQLite persistency
- Candle modelinde `UniqueConstraint` ile mükerrer kayıt koruması
- Fetch işlemlerinde mevcut mumları güncelleme ve mükerrer kayıtları önleme
- Listeleme endpoint’inde:
  - `limit`
  - `offset`
  - `sort`
  - filtreleme parametreleri
- Pytest + httpx ile testler
- Ruff + Black ile kod kalitesi
- GitHub Actions CI (test + lint)
- pre-commit hook’ları

---

## Proje Yapısı

```text
.
├── app/
│   ├── db/
│   ├── models/
│   ├── routers/
│   └── main.py
├── tests/
├── .github/workflows/ci.yml
├── Makefile
├── requirements.txt
├── requirements-dev.txt
└── .pre-commit-config.yaml
```

---

## Gereksinimler

- Python 3.11+ (lokalde 3.12 de çalışır)
- pip
- (Opsiyonel) make

---

## Kurulum

```bash
git clone https://github.com/bilalozok/crypto-trend-mvp.git
cd crypto-trend-mvp

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -r requirements-dev.txt
```

---

## Ortam Değişkenleri

`.env.example` dosyasını referans al:

```bash
cp .env.example .env
```

Örnek:

```env
DATABASE_URL=sqlite:///./crypto_trend.db
```

> Not: Uygulama `DATABASE_URL` üzerinden bağlanır.

---

## Uygulamayı Çalıştırma

```bash
make run
```

Uygulama varsayılan olarak:

- API: `http://127.0.0.1:8010`
- Swagger UI: `http://127.0.0.1:8010/docs`
- ReDoc: `http://127.0.0.1:8010/redoc`

---

## API Endpointleri

### Health / Root

- `GET /`

### Veri Çekme

- `POST /candles/fetch/{symbol}`

Örnek:

```bash
curl -X POST "http://127.0.0.1:8010/candles/fetch/BTCUSDT"
```

### Son Mumları Listeleme

- `GET /candles/latest`

Örnek:

```bash
curl "http://127.0.0.1:8010/candles/latest?symbol=BTCUSDT&limit=20&offset=0&sort=desc"
```

### Trend özeti

- `GET /signals/trend?symbol=BTCUSDT&interval=1h&short_period=5&long_period=20`
- Önce `POST /candles/fetch/BTCUSDT?interval=1h&limit=100` ile veri doldurun.
- Yalnızca kapanmış mumların kapanış fiyatları kullanılır. SMA(5), SMA(20) üzerinde ise
  `up`, altında ise `down`, eşitse `flat` döner. Bu bir ortalama karşılaştırmasıdır.
- `ready`: Hesaplama tamamlandı. `stale=true` ise en son beklenen kapanmış mum eksiktir.
- `insufficient_data`: Uzun periyot için yeterli kapanmış mum yoktur.
- `missing_data`: Hesaplama penceresinde mumlar arasında boşluk vardır.
- `invalid_data`: Kapanış fiyatı sonlu ve pozitif değildir.
- `stale`, en son kayıtlı mumun kapanışından en az bir interval geçtiğinde true olur.
- Yanıttaki tarihlerin tamamı UTC'dir; veri çekilmez ve veritabanına yazılmaz.
- Desteklenen interval'ler: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w.
  Takvim ayı gibi değişken uzunluklu interval'ler desteklenmez.
- short_period < long_period olmalıdır; long_period en fazla 500 olabilir.

---

## Geliştirme Komutları

### Test

```bash
make test
```

### Lint

```bash
make lint
```

### Format

```bash
make format
```

### Toplu Kontrol (lint + test)

```bash
make check
```

---

## pre-commit

Kurulum:

```bash
make precommit-install
```

Tüm dosyalarda çalıştırma:

```bash
make precommit-run
```

Aktif hook’lar:

- `end-of-file-fixer`
- `trailing-whitespace`
- `check-yaml`
- `ruff`
- `ruff-format`
- `black`

---

## CI

GitHub Actions pipeline:

- **test job**: `pytest`
- **lint job**: `ruff` + `black --check`

Dosya: `.github/workflows/ci.yml`

---

## Sık Karşılaşılan Durumlar

### Port doluysa

```bash
lsof -ti:8000,8010 | xargs kill -9
```

### Black check fail olursa

```bash
make format
make check
```

---

## Roadmap

- README örneklerini genişletme
- Deploy (Railway/Render) opsiyonu
- Fetch endpoint’i için timeout/connection error negatif testleri
- pre-commit ve kalite kapılarını daha da sıkılaştırma
- Pydantic v2 `model_config` geçiş temizliği

---

## Lisans

Bu proje öğrenme/deneme amaçlı MVP çalışmasıdır.

## Otomatik mum güncelleme (Railway Cron)

API servisini çalışır bırakın. Aynı repodan ayrı bir `candle-worker` servisi oluşturun.
Worker her çalışmada veri çeker, mevcut mumları günceller ve kapanır; zamanlama Railway'dedir.

| Ayar | Değer |
| --- | --- |
| Start Command | `python -m app.workers.scheduler` |
| Cron Schedule | `*/5 * * * *` |
| Restart Policy | Never |
| Healthcheck | Boş |
| Public domain | Gerekmiyor |

Worker Variables:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
FETCH_SYMBOLS=BTCUSDT
FETCH_INTERVALS=1h
FETCH_LIMIT=100
```

Railway, DATABASE_URL referansını çözümler. Worker Postgres URL'sini psycopg 3'e
uygun biçime dönüştürür. DATABASE_URL eksikse iş durur; varsayılan SQLite'a yazılmaz.
Postgres servis adı farklıysa referansı o servis adıyla seçin.

- Cron UTC'de her 5 dakikada çalışır; kesin dakika garantisi yoktur.
- Önceki iş bitmediyse Railway sonraki çalışmayı atlar.
- Her iş en fazla 5 sembol/interval çiftini sırasıyla işler.
- FETCH_LIMIT 1–100 arasındadır; OKX fallback'i de en fazla 100 mum döndürür.
- Açık mumlar güncellenir; trend endpoint'i sadece kapanmış mumları kullanır.
- Upsert, API ile worker'ın eşzamanlı yazmalarında mükerrer kayıtları önler.
- İlk çalışmada tablo hazır olmalıdır; worker migration çalıştırmaz.
- Log: `fetch_complete symbol=BTCUSDT interval=1h candles=100`.
- Bir çift başarısızsa diğerleri denenir ve süreç 1 koduyla kapanır. Hatalı ayarda
  çıkış kodu 2'dir. Ayrıntılı bağlantı hataları şifre sızdırmamak için loglanmaz.
- Ağ hatalarında aynı çalışma içinde yeniden deneme döngüsü yoktur; sonraki cron
  çalışması tekrar dener. Son 100 mumdan eski boşluklar ayrıca backfill gerektirir.

PostgreSQL'e özel eşzamanlı upsert ve transaction rollback testleri
`Worker PostgreSQL` CI işinde PostgreSQL 18 ile çalışır.
`TEST_POSTGRES_URL` yalnızca bu test veritabanı için kullanılır; üretim URL'sini vermeyin.

## Binance Spot USDT kataloğu (ilk aşama)

Yeni piyasa sorguları yalnızca `https://data-api.binance.vision` adresinden Binance
Spot verisi okur; OKX fallback'i yoktur. API anahtarı gerekmez.

- `GET /market/binance/symbols?limit=100&offset=0&min_quote_volume=0`
  Aktif (`TRADING`), Spot işlemlere açık USDT paritelerini listeler. Katalog 24 saatlik
  USDT işlem hacmine göre azalan sıralanır. `total` filtrelenmiş toplam parite sayısıdır.
  Sayfalama için `offset` kullanılır. Hacim bir sinyal veya yükseliş olasılığı değildir.
- Katalog/hacim snapshot'ı işlem başına 5 dakika önbelleğe alınır; `as_of` alındığı
  zamandır. Stablecoin ve diğer USDT spot pariteleri şimdilik ayrıca elenmez.
- `GET /market/binance/candles/preview?symbol=BTCUSDT&limit=25`
  Binance'den 15m mumları okur; kapanmamış mumu dışarıda bırakır.
  `limit` sağlayıcıdan istenen mum sayısıdır; açık mum elendiği için sonuç daha kısa olabilir.
  Yanıtta `exchange=binance`, `market=spot`, `stored=false` bulunur.
- Bu iki endpoint veritabanına yazmaz. Mevcut `candles` tablosundaki eski Binance/OKX
  kayıtları bu yanıtlarda kullanılmaz.
- Mevcut `/candles/fetch`, `/candles/latest`, `/signals/trend` ve eski worker henüz
  eski veri akışıdır. Onları Binance kaynaklı kabul etmeyin.
- Binance erişim engeli veya rate limit durumunda 503; hatalı sağlayıcı verisinde
  502 döner. Başka borsadan veriyle doldurulmaz.
- Sonraki aşama: kaynakları ayrılmış tablolar, 15m geçmiş veri doldurma ve tüm
  katalog için toplu worker. Bu ilk paket tüm pariteleri otomatik toplamayı etkinleştirmez.

Kaynak: https://developers.binance.com/en/docs/products/spot/faqs/market_data_only

## Binance Spot 15m collection

The isolated `binance_spot_symbols` and `binance_spot_candles` tables preserve
Binance provenance. Existing `candles` data and the legacy trend endpoint remain
separate. Apply `alembic upgrade head` before deploying this version; PostgreSQL
tables are managed by migrations, rather than application startup.

Switch the Railway candle-worker start command to
`python -m app.workers.market_worker`. Keep the five-minute cron schedule and
restart policy Never. Use the same PostgreSQL DATABASE_URL as the API.
Optional settings: MARKET_BUDGET_SECONDS=180, MARKET_CONCURRENCY=4,
MARKET_HISTORY_LIMIT=500 (initial closed candles per symbol).
The worker targets every active Binance Spot USDT pair, prioritizing symbols with
the oldest attempt. The time budget stops new submissions; in-flight requests
may finish afterward. Deferred symbols resume on subsequent runs. A run does
not guarantee that every pair was refreshed. Rate-limit and access errors stop
new submissions; failures are recorded without exposing credentials.

Initial history is limited to the configured candle count. Subsequent requests
overlap stored candles and advance forward. Internal historical gaps are
reported, not automatically repaired. Stablecoin candidates are marked using
an explicit starting list, but are still collected.

Read collection progress at `GET /market/binance/coverage`, with pagination and
`candles_required` (default 200). It reports history size, freshness, gaps and
last errors. Read stored Binance candles at
`GET /market/binance/candles?symbol=BTCUSDT&limit=100`.
The preview endpoint remains read-only. Formation analysis will use the new
tables in a subsequent change.

## Experimental formation analysis
GET /analysis/binance/formations?symbol=BTCUSDT uses the latest 200 closed,
contiguous, current Binance 15m candles. Non-ready statuses suppress patterns:
insufficient_data, missing_data, invalid_data and stale_data.
Unknown/inactive symbols return 404. No external requests or database writes occur.

price_patterns_v1 detects double bottoms/tops and ascending/descending triangles.
Strict pivots require three candles on each side. Double extrema must be adjacent,
6–80 bars apart, with an intervening move of at least twice the tolerance.
Triangles require three horizontal touches and three advancing opposite extrema.
Anchors must be within 40 bars. Tolerance = max(0.3% last close, 0.5 ATR14);
confirmation/invalidation buffer = max(0.1% last close, 0.2 ATR14).
Breakouts use closes, not wicks. Output includes levels, timestamps, reasons and
forming/confirmed/invalidated/not_detected states.

These experimental geometric rules are not calibrated probabilities or order
instructions. Irregular patterns may be missed and overlapping patterns may
appear. States are recomputed, not persisted. Ranking, similarity and volume
confirmation will be separate additions.

## Paginated formation scan
GET /analysis/binance/formations/scan?direction=up&state=confirmed&limit=50
scans a page of active symbols, ordered by recorded 24h quote volume, then symbol.
Optional offset, min_quote_volume and include_stablecoins filters apply to the
symbol universe before pagination. Default stablecoin exclusion uses the explicit
starting list, not exhaustive asset classification. Limits: 1–100 symbols/page.
state=all includes forming, confirmed and invalidated; not_detected is omitted.
direction=all includes both directions. An empty matches list does not imply an
empty market: follow next_offset until null to scan the remaining symbol pages.

quality_counts reports ready and suppressed data statuses for each scanned page.
matched_symbols counts coins, not patterns. No probability ranking is computed.
A window query reads at most 200 closed candles per selected coin into memory;
there is no network fetch, write, or per-symbol database query.
Separate page requests are live reads, not an immutable snapshot. Catalogue
refreshes can move volume-ranked pages; deduplicate symbols when combining pages.

## Confirmation age and current breakout position
Formation results now include last_close, last_candle_close_time,
confirmation_age_bars, distance_from_breakout_pct, breakout_position,
breakout_holding and confirmation_threshold. Age counts closed 15m bars since
the first detected confirmation (zero for the confirming candle). Distance is
signed relative to the geometric breakout level, not the buffered threshold.
Position is above/below/within_buffer using the current confirmation buffer.
Holding means the last closed price is beyond the buffered breakout boundary
in the pattern direction. It does not assert continuous holding or a retest.
These fields do not redefine the historical confirmed/invalidated status.

Scan filters: max_confirmation_age_bars=4 (inclusive) and breakout_holding=true.
An age filter excludes patterns with no confirmation; holding alone does not restrict
the historical pattern status. Combine state=confirmed for current confirmed candidates.
Defaults preserve the previous scan behavior. Example:
 /analysis/binance/formations/scan?direction=up&state=confirmed&max_confirmation_age_bars=4&breakout_holding=true
Rules use current ATR-based buffers and the rolling window; levels and ages
can change on recomputation. This is not a persisted signal history.

## Breakout volume support
Every formation result exposes confirmation_volume (Binance base-asset volume),
prior_volume_average, volume_ratio, volume_reference_bars=20, volume_supported
and volume_support_threshold=1.5. The reference is the 20 candles strictly before
the first price-confirmation candle. Neither that candle nor subsequent candles
enter its average. No confirmation, insufficient reference history or a zero
reference average yields a null ratio, not a zero or an infinite score.
volume_supported means ratio >= 1.5, an experimental threshold rather than a
calibrated prediction. It does not change historical price-confirmation status.

Optional scan filter min_volume_ratio=1.5 excludes missing ratios and keeps
ratios at or above the requested threshold. Default remains unfiltered.
Combine with state=confirmed, max_confirmation_age_bars=4 and
breakout_holding=true. Ratios are within-symbol comparisons; raw coin volumes
must not be compared across different assets. Existing 24h quote-volume filters
use USDT turnover and are separate.

## Visual formation review
Open /analysis/binance/chart?symbol=BNBUSDT to inspect candles, volume, selected
pivot points, breakout/invalidation levels and the buffered confirmation threshold.
Select another pattern or enter another symbol. Hover to inspect candle values.
The vertical confirmation marker denotes its candle; all chart times are UTC.

GET /analysis/binance/chart-data?symbol=BNBUSDT returns candle data and analysis
from the same in-memory 200-candle window and cutoff. It does not fetch Binance
or write data. Non-ready status suppresses overlays; invalid data also suppresses
candles. Every pattern result now includes pivot_points (time, price, high/low).
The chart uses local SVG/JavaScript without external chart scripts or CDNs.
It is a review tool, not order entry or a backtest.

## Pivot availability correction
structure_available_at is the close of the third candle after the final selected
pivot. A strict pivot cannot be identified earlier. Price-confirmation evaluation
now starts at that close, preventing confirmation timestamps before the structure
is observable. A prior breakout is eligible only if price is still beyond the
buffered threshold at an observable close. Volume support and confirmation age
use this first eligible confirmation candle. The experimental geometric method
is otherwise unchanged.

This corrects pivot look-ahead timing, not every possible backtest bias. Tolerance
and confirmation buffers still use current-window ATR and last price. Historic
performance must use walk-forward recomputation of each past window.

## Head-and-shoulders patterns
head_and_shoulders (down) and inverse_head_and_shoulders (up) extend the same
experimental rules to six patterns. This first version only recognizes roughly
horizontal necklines: the two intervening extremes must agree within tolerance.
Sloping necklines are deliberately rejected; they are not flattened arbitrarily.
Three consecutive pivots define left shoulder, head and right shoulder.
The head must exceed both shoulders by at least 2 tolerances; shoulders must
agree within tolerance and stand at least 2 tolerances away from the neckline.
Each side spans 6–60 bars, their duration ratio is 0.5–2, total span <=100 bars,
and the right shoulder is within 40 bars. The neckline is the average of the two
intervening extremes. Invalidation uses the right shoulder plus the existing
directional buffer. Confirmation waits for right-shoulder pivot availability.
Chart labels identify shoulders, head and neckline points.
These geometric patterns do not yet enforce a preceding trend or target price.

## Converging sloped boundaries
symmetrical_triangle, rising_wedge and falling_wedge extend the registry to nine.
Three high pivots and three low pivots define ordinary least-squares boundaries.
Both line residuals must fit within tolerance; observed candles up to the last
pivot must remain within the channel (with tolerance). Boundaries must interleave,
span 12–100 bars, contract at least 20%, and retain width >2 tolerances at the last
pivot. Each slope must move at least max(tolerance, 0.5 ATR) over the span.
The apex must be after pivot availability and within 100 bars of the last pivot.
Triangles have opposing slopes; rising wedges have positive slopes with faster
rising support; falling wedges have negative slopes with faster falling resistance.

Forming symmetrical triangles have neutral direction and no single breakout level.
Their first buffered close above/below the channel determines direction.
Rising wedges expect down; falling wedges expect up. Opposite breakouts invalidate
these directional setups. Price confirmation starts only after pivot availability.
Before confirmation boundaries move with each candle; at confirmation breakout and
opposite invalidation levels freeze. A setup with no confirmation before its apex
is invalidated as expired. boundary_lines expose fitted segments and apex_time;
the chart plots them. Filters up/down exclude unconfirmed neutral triangles.
These are experimental geometry, not calibrated direction forecasts.

## Bull and bear flags
bull_flag and bear_flag extend the registry to eleven. Both require an explicit
preceding pole and three upper plus three lower touches of a countertrend channel.
Boundaries must have matching signs (down for bull, up for bear), be approximately
parallel (slope difference over the span <= tolerance), fit within tolerance, and
contain observed candles up to the last pivot. Span is 12–48 bars, anchor within
30 bars; width must exceed 2 tolerances and change by no more than 20%.
The first touch must be the pole-side extreme. The pole reference is the most
extreme close 8–30 bars before that touch. Its move must exceed both 3 ATR and
3 channel widths; retracement through the last pivot may consume at most 60%.
Confirmation follows pivot availability in the pole direction; opposing closes
invalidate. Before confirmation the flag expires after 60 bars from its start.
At confirmation, price and invalidation levels freeze like wedges.
pole_points, pole_move_pct and flag_retracement_ratio explain the preceding move;
the chart draws the pole in gold. These experimental thresholds need backtesting.

## Bull and bear pennants
bull_pennant and bear_pennant extend the registry to thirteen. They reuse the
converging opposing-slope triangle rules with a directional pole filter.
The compact triangle spans 12–48 bars with its last pivot within 30 bars.
Its first touch must be the pole-side extreme. As with flags, the extreme close
8–30 bars before that touch defines the pole; its move must exceed 3 ATR and
3 initial channel widths, and retracement through the last pivot is <=60%.
Generic triangle residual, containment, convergence and apex rules still apply.

A bull pennant requires upward confirmation; a bear pennant requires downward
confirmation. Opposite breakouts invalidate and do not get confirmed timestamps.
Timing, age, volume ratio and frozen confirmation levels use the shared rules.
pole_points, pole_move_pct and pennant_retracement_ratio explain the structure.
The existing chart draws the pole and converging boundaries. These experimental
continuation rules are not calibrated probabilities.

## Triple bottom and triple top
triple_bottom and triple_top extend the registry to fifteen.
Three consecutive strict pivots must agree within tolerance. Adjacent spacing
is 6–50 bars with a duration ratio 0.5–2, total span <=100 bars, and the last
touch within 40 bars. Both intervening reversals must move at least 2 tolerances
away from all three extrema. Bottom breakout uses the higher intervening high;
top breakout uses the lower intervening low, requiring the close to clear both.
Invalidation uses the outermost touch plus the existing directional buffer.
Confirmation waits for third-pivot availability and shares age/volume rules.
The chart labels all three extrema and both intervening reversals.
These geometric rules do not yet require an earlier trend or compute targets.


### Deneysel yatay dikdörtgenler

Yükseliş/düşüş dikdörtgenleri üç destek ve üç direnç temasını sırayla arar.
Her sınırdaki fiyat farkı mevcut toleransı aşamaz; kanal genişliği en az
dört tolerans olmalıdır. Ardışık temaslar 3–25, toplam yapı 15–100 mumdur;
son temas son 40 mum içinde olmalıdır. Yapıdan önceki 12 mumdaki yönlü
kapanış hareketi en az kanal genişliği kadar olmalıdır. Yapı içindeki
fitiller kanalın toleranslı sınırlarını aşamaz.
Üçüncü sağ komşu mum kapandıktan sonra yönlü kırılım ve karşı sınırda
geçersizleşme değerlendirilir. Mevcut hacim, teyit yaşı ve grafik alanları
kullanılır; eşikler deneysel kurallardır, başarı olasılığı değildir.


### Deneysel fincan-kulp ve ters fincan-kulp

Gövde 24–100 mumdur; iki kenar tolerans içinde yakın olmalıdır. Gövdenin
uç noktası genişliğin %30–70 bölgesinde, derinliği en az dört toleranstır.
Dip/tepe çevresindeki yedi kapanışın en az beşi derinliğin alt %35 bölgesinde
olmalıdır; iki çeyrek noktada ara yükseklik aranır. Bu geometrik yaklaşım
keskin V yapıları dışlamaya çalışır; eğri uydurma veya başarı olasılığı değildir.
Kulp 4–24 mum ve gövdenin en fazla yarısı uzunluğunda, derinliği gövdenin
%10–50'sidir. Kulp pivotunun üç sağ komşusu kapandıktan sonra iki kenarın
ötesindeki kapanış teyit olur; kulp uç sınırı geçersizleşme seviyesidir.
Ters yapı aynı kuralları aynalayarak kullanır. Mevcut hacim, teyit yaşı,
tarama ve grafik alanları korunur.


### Deneysel genişleyen üçgen

Üç üst ve üç alt pivot üzerinden mevcut çizgi uydurma yöntemi kullanılır.
Üst çizgi pozitif, alt çizgi negatif eğimli olmalı; yapı sonunda kanal
genişliği başlangıca göre en az %25 artmalıdır. İlk genişlik iki toleranstan
fazladır. Mevcut artık hata, fitillerin kanal içinde kalması, 12–100 mum gövde, son 40 mum
pivot ve asgari eğim kuralları korunur. Oluşurken yön nötrdür; son pivotun
üç sağ komşusu kapandıktan sonra ilk yönlü kapanış kırılımı teyit edilir.
Teyitte seviyeler sabitlenir; karşı sınır ihlali geçersizleşmedir. Gelecek
tepe kesişimi yoktur; teyitsiz yapı son pivottan 60 mum sonra sona erer.
Grafikte iki genişleyen çizgi ve pivotlar gösterilir. Başarı olasılığı değildir.


### Tek coin analiz raporu

`GET /analysis/binance/report?symbol=BTCUSDT` aynı 200 kapanmış 15m mumdan
20 formasyon sonucunu birleştirir. Yeni veri çekmez ve veri tabanına yazmaz.
Varsayılan `max_confirmation_age_bars=4`, `min_volume_ratio=1.5` parametreleri
raporda gösterilir. Güncel teyit, yaş sınırını geçen ve kırılım seviyesini
koruyan `confirmed` yapı anlamındadır; hacim desteği ayrıca değerlendirilir.
Her iki yönde güncel teyit varsa `conflicting`, yalnızca bir yönde varsa
`bullish_setup`/`bearish_setup`, teyit yoksa `waiting` döner. Güncel olmayan
veya kırılımı korunmayan teyitler özete yön vermez; gerekçeleri her formasyonda
gösterilir. Veri hazır değilse değerlendirme `unavailable` ve formasyonlar
boştur. Türkçe yorum, sayımlar, tüm tespit edilen yapılar ve grafik bağlantısı
döner. Sayımlar birbirinden bağımsız başarı kanıtları değildir; rapor fiyat
tahmini, kalibre edilmiş olasılık veya emir talimatı üretmez.


### Gözlenen formasyon değişim geçmişi

Önce `alembic upgrade head` ile `8c15c2026b01` uygulanmalıdır. İki yeni tablo
son gözlenen durumları ve değişim olaylarını tutar; mevcut mumlar korunur.
Worker başarılı veri toplamasından sonra yalnızca `ready` analizleri kaydeder.
İlk tespit `initial_observation`, sonraki kayıtlar `appeared`, `changed`,
`new_structure`, `disappeared` olabilir. İmza durum, yön, yapı başlangıcı,
son pivot, teyit zamanı ve kırılımın korunmasını içerir; her yaş/hacim veya
fiyat değişimi yeni olay değildir. Her coin/formasyon/kapanmış mum için
yalnızca ilk gözlem işlenir; aynı mumun sonradan veri düzeltmeleri ayrı olay
üretmez. PostgreSQL coin satırı kilidi eşzamanlı kayıtları sıralar; olay ve
son durum tek transaction ile yazılır. Eksik/eski veri durumu değiştirmez.
Atlanan mumlar için olay üretilmez; kayıt zamanları gerçek ilk oluşum zamanı
olduğu iddiası taşımaz. Metot sürümü olayda saklanır.
`GET /analysis/binance/history?symbol=PEOPLEUSDT&limit=50&offset=0`
son kayıtları veri zamanına göre yeninden eskiye döndürür; `pattern` isteğe
bağlı filtre olabilir. Endpoint salt okunurdur. İşçi history başarısızlığını
`formation_history_failed` olarak ayrıca loglar; başarılı mum toplama kaydı
korunur. Veri geçerliliği veya hata için sahte formasyon olayı yazılmaz.


### Açıklanabilir benzer coin analizi

`GET /analysis/binance/similar?symbol=PEOPLEUSDT` varsayılan son 48 adet
15m log getirisini (12 saat) karşılaştırır. Tüm seriler aynı kapanış zamanına
hizalanır ve mevcut 200 mum kalite kontrolünü geçmelidir. Sıfıra yakın
varyanslı seriler korelasyon için uygun değildir; eksik/eski veri dışlanır.
Adaylar aktif, stablecoin adayı olmayan USDT pariteleridir; referans hariçtir.
`candidate_limit=100`, `offset`, `limit=10`, `min_quote_volume=0`,
`min_correlation=0.3` ve `lookback_bars=48` ayarlanabilir. Her cevap yalnızca
aday sayfasındaki en benzer sonuçları verir; `next_offset` ile tüm sayfaları
taramak gerekir. Evren hacme göre sıralanır ve canlı yenilenebilir.
Skor fiyat korelasyonu (%70), güncel formasyon kümelerinin Jaccard benzerliği
(%20) ve son pencere içindeki gözlenmiş değişim türlerinin benzerliği (%10)
bileşenlerinden oluşur. Veri olmayan bileşen kullanılmaz, kalan ağırlıklar
yeniden normalize edilir ve cevapta gösterilir. İki boş formasyon kümesi eşleşme sayılmaz; yalnız birinin boş olması sıfır
benzerliktir. Değişim bileşeni için iki coinde de gözlenmiş değişim gerekir.
Oluşan yapılar ile en fazla dört mumluk, kırılımını koruyan teyitler güncel
formasyon kümesine girer. İlk tarihçe gözlemleri değişim sayılmaz. Değişim
kümeleri sıralama veya gecikmeli öncül/ardıl ilişki modellemez. Skor bir
geçmiş benzerlik ölçüsüdür; gelecekte aynı hareket veya yükseliş olasılığı
değildir. Endpoint salt okunurdur; kayıt veya dış borsa çağrısı yapmaz.


### Açıklanabilir yükseliş adayı sıralaması

`GET /analysis/binance/candidates` aktif, stablecoin adayı olmayan USDT
paritelerinde mevcut formasyon taramasını kullanır. Varsayılan en fazla
dört mumluk teyit, korunan kırılım ve en az 1.5x teyit hacmi gerekir.
Her coin için en yüksek puanlı tek yükseliş formasyonu esas alınır; aynı
fiyattan türeyen çok sayıda yapı ekstra puan getirmez. Güncel ve kırılımını
koruyan düşüş teyidi varsa coin dışlanır; `include_conflicting=true` seçilirse
30 puan ceza ve karşıt yapıların ayrıntıları gösterilir.
Puan bileşenleri: tazelik `35*(1-age/(max_age+1))`, hacim
`35*min(volume_ratio/max(3,2*min_volume_ratio),1)`, yakınlık
`30*max(0,1-abs(distance_from_breakout_pct)/3)`. Yakınlık bileşeni
kırılımdan fazla uzaklaşmış fiyatlara düşük puan verir. Ağırlıklar, üç yüzde
uzaklık ve hacim doygunluğu deneysel ürün kurallarıdır; başarı oranlarından
türetilmemiştir. Sonuç başarı/yükseliş olasılığı veya emir talimatı değildir.
`candidate_limit=100`, `offset`, `limit=10`, `max_confirmation_age_bars=4`,
`min_volume_ratio=1.5`, `min_quote_volume=0` ayarlanabilir. Sonuç yalnızca
aday sayfasına aittir; tüm evren için `next_offset` ile sayfalar birleştirilir.
`candle_close_time` tüm sayfalarda aynı olmalıdır. Yeni kayıt, migration
veya dış borsa çağrısı gerektirmez.


### Birleşik analiz ekranı

`/analysis/binance/dashboard?symbol=PEOPLEUSDT` coin seçimi, grafik, Türkçe
rapor, geçmiş ve tarama sonuçlarını bir araya getirir. Rapor ve geçmiş coin
seçildiğinde yüklenir. Benzer coinler ve yükseliş adayları düğme ile tüm
aday sayfalarını tarar; ekran genel sıralamayı birleştirir. Sayfalar arasında
mum veya evren değişirse sonuç yayımlanmaz, yeniden tarama istenir.
Kalite nedeniyle dışlanan coinler ve kısmi sonuç durumu gösterilir. Coin
değişiminde eski istekler iptal edilir. Bir taramadaki aynı coin kayıtları
tekilleştirilir. Katalog alınamazsa coin elle girilebilir. Grafik ayrı
panelde aynı coini açar; embed görünümünde coin değişimi ana ekrandan yapılır.
Otomatik yenileme, emir gönderme veya veri tabanına yazma yoktur. Skorlar
olasılık yüzdesi olarak gösterilmez.


### İleri yürütmeli tek coin sinyal testi

`GET /analysis/binance/backtest?symbol=BTCUSDT` kayıtlı son 500 kapanmış
mumu kullanır. Her adımda yalnızca o adımda bilinen 200 mum analiz edilir.
Varsayılan sekiz mum (iki saat) tutma süresi, her yönde 10 baz puan komisyon
ve 5 baz puan kaymadır. Yeni yükseliş teyidi ancak `confirmed_at` o adımın
kapanışıysa adaydır; hacim eşiği 1.5x, güncel karşıt teyit varsa dışlanır.
Aynı kapanıştaki çoklu yapı tek sinyale indirilir. Giriş sonraki mum açılışı,
çıkış N'inci mum kapanışıdır. Uygun ileri mumlar henüz yoksa sonuç beklemede
sayılır. Boşluklu/geçersiz ileri mumlarla getiri hesaplanmaz. İlk analiz 200 mum
kapandığında yapılır; daha önce oluşmuş teyitler test sinyali sayılmaz.
`history_limit=201..1000`, `horizon_bars=1..96`, `fee_bps=0..100`,
`slippage_bps=0..100`, `min_volume_ratio=0..1000` ayarlanabilir. Baz puan
%0.01'dir. Net getiri: çıkış kapanışı*(1-kayma)*(1-komisyon) /
[giriş açılışı*(1+kayma)*(1+komisyon)] - 1. En iyi/en kötü ara hareket
ham fitiller üzerinden giriş açılışına göre ölçülür. Sinyaller çakışabilir;
özet bağımsız sinyal getirileridir, portföy getirisi veya sermaye büyümesi
değildir. Kayıtlı kısa pencere, güncel aktif semboller, sabit maliyet varsayımı
ve küçük örneklem nedeniyle sonuç kalibre edilmiş gelecek olasılığı değildir.
Endpoint salt okunurdur; eski veri çekmez, emir veya kayıt üretmez.


### Manual Binance Spot historical collection

`python -m app.workers.history_backfill --days 30 --end 2026-10-05T01:00:00Z`
prints a plan without network requests or database writes. Add `--execute` to collect
into the existing PostgreSQL `binance_spot_candles` table. No migration is required.
The fixed default cohort is the 20 symbols from the initial non-stablecoin study;
`--symbols BTCUSDT,ETHUSDT` can override it (maximum 20). All symbols must be active
in the stored catalogue and outside the explicit stablecoin candidate list.

The cutoff is exclusive and must be a closed 15m boundary. Collection includes
200 warmup bars preceding the requested period: 30 days means 3,080 bars per symbol.
Pages are validated before atomic upsert, and each successful page is committed.
Repeated runs with the same cutoff are idempotent; incomplete history remains
reported as incomplete, including newly listed symbols and missing candles.
Worker health timestamps and observed formation events are not updated by backfill.
The usual live worker continues to refresh the latest bars.

This supplies historical data only. The current API backtest still caps its latest
history window at 1,000 bars; a dated evaluation runner is required to evaluate
the full collected period. A retrospective time split is not an unseen holdout
when those dates have already informed development.


### Fixed-date paired horizon study

`python -m app.workers.historical_study --output signal_study_30d.json`
reads existing PostgreSQL candles only, using the fixed 20-symbol backfill cohort.
Defaults: start 2026-09-05T01:00:00Z, split 2026-09-25T01:00:00Z,
end 2026-10-05T01:00:00Z. Flags `--start`, `--split`, `--end`, `--symbols`
can override these dates and cohort. An existing output file is never overwritten.

All required candles including 200 warmup bars are validated before evaluation.
The unchanged real replay engine selects signals once per period; 4/8/16-bar
outcomes use identical signal identities and fixed costs (10bps fee and 5bps
slippage per side). Signals without a full 16-bar forward window within their
period are excluded for every horizon and counted separately. No transaction
spans a network request; no remote requests or database writes occur.

The JSON contains per-period, per-horizon totals, pattern breakdowns and signal
records. Terminal summaries are signal-weighted, not averages of coin averages.
These are retrospective current-cohort statistics, not independent portfolio
trades, calibrated probabilities or unseen validation results.


### Observed forward signal tracking

Apply migration `9d15c2026c01` before enabling `FORWARD_TRACKING_ENABLED=true`
on the existing market worker. The feature defaults to disabled.
`GET /analysis/binance/forward?symbol=BTCUSDT&limit=20&offset=0` exposes immutable
observed snapshots, rule hashes, observation delays, scheduled hypothetical
entries and later 1/2/4h outcomes. No orders are sent.

Only new, volume-supported, non-conflicting upward confirmations on the latest
closed bar are recorded. Stablecoin candidates are excluded. A per-coin, per-rule
4h cooldown prevents overlapping entries. Rule configuration and a hash of the
engine source are saved so records from changed rules can be separated.

The hypothetical entry is the first 15m boundary strictly after observation;
it is not the opening price already elapsed when the worker recognized the
signal. Closed stored bars later provide outcomes, using fixed per-side costs
of 10bps fee plus 5bps slippage. Missing/invalid forward data is marked invalid
and never treated as a return; completed outcomes and signal snapshots are not
rewritten. Observation and measurement timestamps are separate.

The worker records observations only after this feature is enabled; backfill does
not generate historical forward signals. The current implementation settles when
the symbol is successfully refreshed; an inactive/unavailable symbol may remain
pending. These hypothetical per-signal statistics are not portfolio performance.


### Forward outcome dashboard

`GET /analysis/binance/forward/summary?days=7` reads observed outcomes for the
current rule hash only. Optional `rule_hash` selects one historical fingerprint;
`days` is bounded to 1–30. The response includes complete/pending/invalid counts
for 1/2/4h, signal-weighted net-return statistics, and the latest 10 signals.
Only finite, completed net returns enter statistics; empty means/rates are null.
The dashboard has a manual refresh panel for all coins, independent of the
selected chart coin. Periods may have different completed cohorts, so compare
sample counts before comparing horizons. No migration or worker changes required.

### Forward results by formation

The forward summary includes `pattern_groups`, grouped by the primary formation
name saved at observation. Each group reports signal and unique-symbol counts,
plus completed, pending, invalid, positive-net rate, mean and median net returns
for 1/2/4 hours. The dashboard displays these counts without ranking formations.
Groups share the selected rule fingerprint and observation window. Different
horizons can have different completed cohorts; small samples do not establish
future success probabilities. Signal capture rules and migrations are unchanged.

### Paired forward horizon comparison

`paired_comparison` uses only observed signals with valid completed net returns
for all three horizons. Each horizon therefore reports exactly the same signal
cohort. Pending or invalid outcomes exclude a signal from this comparison only;
the original summary and formation groups remain available. Empty paired cohorts
have null return statistics. This is a completed-cohort comparison, not a
portfolio simulation. No capture rules, fees, cooldowns or migrations change.

### Saved forward reports

Apply migration `ae15c2026d01` before deploying this feature. It only adds
`binance_forward_reports`; existing signals and their outcomes are preserved.
The dashboard creates reports for 1/7/30 days, lists saved metadata in pages,
opens stored summaries and downloads JSON containing every captured signal.
POST `/analysis/binance/forward/reports?days=7&request_id=<uuid>` creates a fixed
current-rule snapshot. Reusing the UUID returns the same report; different days
with that UUID are rejected. PostgreSQL uses REPEATABLE READ so summary and
signal payloads share a consistent database view while the worker updates.
Reports contain at most 10,000 signals; larger periods must be shortened.
GET `/analysis/binance/forward/reports?limit=20&offset=0` lists metadata only.
GET `/analysis/binance/forward/reports/{id}` opens a saved payload; append
`/download` to download JSON. No report update or deletion endpoint is exposed.
Pending outcomes stay pending in old reports. Reports from different times can
overlap and are not independent samples. Creating a report never changes signal
rules or the worker. These routes follow the existing public API access model.

### Dashboard categories and formation-first flow

The analysis dashboard separates coin analysis, formation scanning, candidates,
forward results and saved reports into keyboard-accessible tabs. Clicking a coin
in any scan opens its coin tab and chart. The current tab stays in the URL.
Formation scanning selects any of the 20 supported patterns, state and direction,
with optional volume and fresh/holding/volume-supported confirmation filters.
All eligible symbol pages are scanned; changed candle windows, changed universe
counts or failed pages stop the run without displaying a partial matching list.
GET `/analysis/binance/formations/scan` accepts optional `pattern=<formation_id>`;
unknown IDs return 422. Omitting it preserves the existing multi-pattern scan.
Existing and new tables include generated explanatory notes about evidence,
missing measurements, historical snapshots and what to inspect next. These notes
are descriptions, not personal saved notes or order instructions. They do not
change stored reports, signal capture rules, scores, or migrations. Personal
TL/USDT purchases and protected account access are a subsequent implementation.

### Private purchase ledger (TL / USDT)

Apply migration `bf15c2026e01` before deployment. It adds account/session/login-limit
and purchase tables, preserving all candles, signals and reports. Accounts are
provisioned only through `python -m app.workers.account_admin --username <name>`.
The CLI reads a 15–128 character password twice via hidden prompts, never arguments
or printed output. `--reset-password` replaces the password and revokes sessions.
Use the intended production PostgreSQL connection when provisioning accounts.
There is no public signup or password-reset endpoint.

Passwords use salted scrypt N=2^17, r=8, p=1; raw sessions are random, hashed in
storage, expire after 12 hours and use Secure/HttpOnly/SameSite=Strict host cookies.
Private writes require a session and CSRF header, and explicit foreign origins
are rejected. `PRIVATE_APP_ORIGIN` defaults to the existing Railway HTTPS origin;
set it to the exact HTTPS origin when using another domain. Database-backed login
budgets allow 6 attempts per username and 30 globally per 15 minutes, including
successful logins. One password verification per web process runs at a time.
Private responses have no-store cache headers. Existing public analysis/report
routes remain public; they never include personal purchase records.
References: OWASP Password Storage, Session Management and CSRF Prevention cheat
sheets at https://cheatsheetseries.owasp.org/ .

The Aldıklarım tab supports owner-scoped purchase creation/listing/deletion,
start-inclusive/end-exclusive Turkey-time date filters and pagination. All amounts
are validated finite decimal strings (up to 18 decimal places), preserved without
binary float conversion. Cost equals price times coin quantity plus fee in the
selected currency. Exact totals are kept; average unit cost is rounded to 18
places. Currency groups are separate, and selected-period purchases are not
presented as the entire portfolio. A repeated purchase UUID with identical fields
is idempotent. Personal notes are rendered as text and scoped to the account.

This release is an acquisition ledger: it does not subtract sales, convert TL to
USDT, fetch currency rates or claim current valuations or P/L. Market valuation
and range charts follow in the next step, using explicitly timestamped price/FX
sources. Explanatory notes now distinguish supportive evidence, weaker evidence
and observation steps; scores and signal rules are unchanged.

### Özel alışların değerlemesi ve fiyat aralığı

`Aldıklarım` sekmesinde **Değeri hesapla / Yenile**, üstteki tarih filtresine uyan
alışları son kapanmış 15m Binance Spot USDT fiyatıyla değerlendirir. Bu tarih
filtresi alış kayıtlarını seçer; geçmiş değerleme tarihi seçmez. Alış komisyonu
maliyete dahil, satış masrafları hariçtir. Satışlar düşülmediğinden kayıtlı alış
adetlerinin elde tutulduğu varsayılır; gerçek bakiye/portföy getirisi değildir.

TL için aynı kapanışa ait **BtcTurk USDT/TRY** kline fiyatı kullanılır.
Kaynak: https://docs.btcturk.com/docs/public-endpoints/get-kline-data/;
`graph-api.btcturk.com/v1/klines/history`, `symbol=USDTTRY`, `resolution=15`.
`from`/`to` saniyedir; dönen `t` açılış zamanı istenen 15m mumla birebir
örtüşmelidir. OHLC pozitif/sonlu/tutarlı olmalı; diziler eşit uzunlukta olmalıdır. Açılış zamanı tam eşleşen tek mum seçilir;
yanıtta gelen sonraki mum değerlemeye dahil edilmez. Eksik veya tekrarlanan
eşleşme reddedilir.
Coin fiyatı Binance Spot, kur ayrı BtcTurk piyasasıdır; kaynak ekranda gösterilir.
Binance TR bağlantısı Railway'de HTTP 451 döndüğü için bağımsız veri sağlayıcısı
kullanılır. Başarısız sorgu, eksik güncel coin fiyatı, pasif parite veya son
fiyat zamanından sonraki alış için değer null kalır. Herhangi bir grup
hesaplanamıyorsa o para biriminin toplam piyasa değeri ve farkı da null kalır.
TL ve USDT maliyetleri/toplamları karıştırılmaz; USDT banka USD/TL kuru değildir.
İki piyasadan türetilen TL değeri işlem yapılabilir teklif değildir. Dış servise
yalnızca USDT/TRY paritesi ve ortak mum zaman aralığı gönderilir.

`GET /account/purchases/valuation?start=...&end=...` mevcut özel hesap oturumunu
ister; en fazla 10.000 kayıt seçer. Fiyat/kur zamanı ve kaynak yanıtta gösterilir.
Tutarlar Decimal ile hesaplanır ve JSON'da string kalır; yüzdeler 8 ondalığa
yuvarlanır. TL kur sorguları 30 saniyelik kısa, süreç içi ve sınırlı önbellek
kullanır; hata halinde sonraki aralıkta yeniden denenir.

**Aralığı analiz et** ayrı başlangıç/bitiş ile (en fazla 31 gün) saklanan USDT
fiyatlarını ve o hesaba ait alış zamanlarını gösterir.
`GET /account/purchases/price-range?symbol=BTCUSDT&start=...&end=...` yalnızca
hesabın alış kaydı olan coinlerde çalışır. Aralık tam kapalı 15m mumlara
daraltılır. Eksik/geçersiz mum veya iki mumdan kısa aralıkta dönem değişimi ve
kesintisiz grafik gösterilmez. Otomatik geçmiş indirme yapılmaz; saklanan
geçmişin dışında aralık veri eksik olarak görünür. Grafik USDT cinsindedir; TL
alış fiyatı grafiğe fiyat seviyesi olarak taşınmaz. İşaretler yalnızca alış
zamanıdır. Kişisel tutarlar/notlar giriş gerektirir ve yanıtlar no-store kalır.

Sayı girişleri `1234,56`, `1234.56` veya Türkçe `1.234,56` kabul eder. Para
simgeleri ve belirsiz karma biçimler reddedilir; doğrulama hataları alan adıyla
gösterilir. Yeni migration, hesap veya worker ayarı gerekmez.


## Özel portföy teknik takip

`Portföy teknik takip` sekmesi giriş gerektirir. `GET /account/portfolio`
yalnızca hesabın alış kaydı olan benzersiz coinlerini, sayfa başına 10 adet
inceler. Satış kayıtları yoktur; liste eldeki net miktarı doğrulamaz. TL kuru
ve alış maliyeti bu akışta kullanılmaz. Kapanmış 200 adet 15m mumdan mevcut
formasyon motoru ile olumlu/olumsuz kanıtlar, hacim desteği, kırılım ve
geçersizlik seviyeleri sunulur. Orta (4h) ve uzun (1d) vade henüz desteklenmez;
15m sonucu diğer vadeler için kullanılmaz. Olasılık veya otomatik emir üretilmez.

Migration: `alembic upgrade head` (`c015c2026f01`).
`POST /account/portfolio/observations` CSRF ile bir sahip olunan coin için
Türkiye takvim günündeki ilk geçerli kullanıcı gözlemini saklar. Aynı gün
tekrar kayıt isteği eski gözlemi değiştirmez; eşzamanlı istekler tek kayıt
oluşturur. Eksik/eski veri kaydedilmez. Otomatik worker kaydı ve geçmişe dönük
doldurma yoktur. `GET /account/portfolio/history?symbol=BTCUSDT` son 30 kaydı
hesaba özel getirir. Günlük yön karşılaştırması önceki kayıtlı günle yapılır;
aynı yön fiyat seviyelerinin aynı kaldığı anlamına gelmez. Tüm hesap yanıtları
no-store'dur; çıkışta ve oturum bitişinde özel portföy görünümü temizlenir.


## Portföyde orta ve uzun vade

Migration `d115c2026g01`: 4h/1d mumları `portfolio_market_candles` ve
istek durumları `portfolio_market_feeds` içinde ayrı tutulur. Mevcut 15m
mum tablosu, ileriye dönük sinyal motoru ve kural hash'i değiştirilmez.
`portfolio_multiframe_v2` kendi vade görünüm sürümüdür.

Coin detayında **Bu coinin orta/uzun vade verilerini indir / Yenile** düğmesi
CSRF korumalı `POST /account/portfolio/refresh` çağrısı yapar. Yalnızca hesabın
alış kaydı olan aktif spot USDT coinleri için Binance Spot 4h ve 1d kline
verisi istenir. Kişisel maliyetler, miktarlar, notlar veya hesap bilgileri
kaynağa gönderilmez. GET teknik analiz isteği ağdan veri indirmez.

Her kaynak ayrı 201 mumla başlatılır; açık mum atılır, 200 kapanmış mum
analize yeterlidir. Yeni listelenen coinlerde 200 mum yoksa yetersiz geçmiş
etiketi görünür. UTC sınırları ve OHLCV doğrulanır; eksik/eski/bozuk veri yön
sinyali sayılmaz. Günlük mum UTC 00:00'da (Türkiye 03:00) kapanır. Teyit yaşı
ilgili vadenin mum adedidir; 4h için dört mum 16 saat, 1d için dört mum dört
gündür. Veri her vadede ayrı zamanda kapanır; zamanları tabloda gösterilir.

Mevcut motorun indeks başına fiyat/hacim kuralları ayrı OHLCV kaynaklarına
uygulanır; fiyatlar yeniden örneklenmez. Zaman damgaları kaynak vadesine geri
çevrilir. Ana 15m motorunun global değişkenleri değişmez; bu kuralların diğer
vadelerde başarı olasılığı kalibre edilmiş değildir. Vade uyumu hazır
vadelerdeki yönlerin karşılaştırmasıdır; eksik vadeler açıkça listelenir,
teyit adedi bağımsız kanıt sayılmaz.

Worker ortamında `PORTFOLIO_TIMEFRAMES_ENABLED=true` etkinleştirilirse aktif
hesapların alış coinleri (en fazla 500 benzersiz parite) her normal toplama
çevriminde güncellenir. Başarıyla alınmış kapanış için tekrar ağ isteği
atılmaz; başarısız/sürmekte olan istekler parite/vade başına bir dakika
sınırlıdır. Kaynak blok/rate limit durumunda ek istekler durdurulur. Günlük
özel gözlem düğmeyle saklanmaya devam eder; mevcut ilk günlük gözlem ve v1
kayıtları değiştirilmez. Eski ve yeni portföy yöntemleri günlük yön
karşılaştırmasında karıştırılmaz.


### Portföyün koşullu yorumları

Her vade kendi formasyonlarından olumlu/olumsuz kanıt, teyit hacmi eksikliği,
eski teyit, oluşan ve geçersizleşen yapı açıklamaları üretir. Güncel veya
oluşan yönlü yapılar için ilgili vade kapanışının teyit eşiği ve geometrik
geçersizlik fiyatı gösterilir. Geçersizleşmiş/eski teyitler aktif koşul
listesine alınmaz. Oluşan yapıların koşulu teyit adayıdır, kesin sinyal değildir.

Yeni alım incelemesi ve eldeki coin için risk incelemesi ayrı açıklanır.
Eksik/eski veri yorumlara katılmaz; adı açıkça belirtilir. Yorumlar olasılık,
emir, otomatik stop veya kişisel mali duruma göre öneri üretmez.
`portfolio_conditional_notes_v1` açıklama sürümüdür; geometrik motor ve
`portfolio_multiframe_v2` değişmez. Kayıtlı günlük gözlemler tekrar hesaplanmaz.


### Portföy saatli gözlemleri

Coin detayındaki güncel gözlem düğmesi, kapanmış mumlarla hesaplanan teknik
görünümü yeni saatli kayıt olarak saklar. Günlük ilk gözlemler değiştirilmez.
Son 30 saatli kayıt ve kayıttan önceki son günlük/saatli gözleme göre teyit,
hacim ve fiyat koşulu değişimleri gösterilir. Eksik veri ve farklı analiz
sürümleri yön değişimi olarak karşılaştırılmaz. Kayıtlar manueldir; emir üretilmez.
Deployment öncesinde `alembic upgrade head` gerekir (`e215c2026h01`).
`/account/portfolio/snapshots` GET/POST giriş ve kayıt sahipliği gerektirir; POST
için CSRF ve UUID `request_id` gerekir. Aynı isteğin tekrarı kaydı çoğaltmaz.


### Otomatik günlük portföy gözlemi (Türkiye 09:00)

Railway web ve candle-worker servislerinde `PORTFOLIO_DAILY_ENABLED=true`
ayarlanarak açılır; varsayılan kapalıdır. Mevcut worker Cron sıklığı korunur.
Aktif hesapların alış kaydı olan coinleri, 09:00 sonrasındaki başarılı veri
toplama çalışmasında bir kez kaydedilir. Gerçek gözlem zamanı ve planlanan
09:00 zamanı ayrıdır; kapanış verisi kullanılır, 09:00 fiyatı simüle edilmez.
Kısa vade verisi hazır değilse kayıt alınmaz ve aynı gün sonraki çalışmada
yeniden denenir. Eksik orta/uzun vadeler açıkça belirtilir; geçmiş günler
doldurulmaz. Yeni alışlar gün içinde sonraki çalışmada kapsama girer.
Günlük ilk manuel gözlem değiştirilmez. Otomatik gözlemler saatli kayıtlarda
ayrı kaynak etiketiyle görünür. Migration gerekmez; mevcut snapshot tablosu
ve hesap/istek benzersizliği kullanılır. Bayrağı false yaparak durdurulur.


### Kayıtlı aday taramaları

Adaylar sekmesindeki Piyasayı tara ve kaydet işlemi giriş/CSRF gerektirir.
Aktif stablecoin olmayan USDT evreni tek sunucu isteğinde taranır; en fazla
1000 coin desteklenir, sınır aşılırsa kısmi kayıt oluşturulmaz. PostgreSQL
REPEATABLE READ ile evren ve mum verisi aynı veritabanı görünümünden okunur.
Kapanmış 15m verisindeki mevcut yükseliş aday koşulu ve puanı korunur.
Tüm taranan coinlerin veri durumu ve dışlanma nedeni, tüm uygun adaylar ve
adayların ayrı 15m/4h/1d teknik görünümleri değişmez JSON olarak saklanır.
4h/1d için yalnızca mevcut depolanmış veri kullanılır; tarama dış API çağırmaz.
Eksik vade yön kanıtı sayılmaz. Yüksek vadeler bu aşamada puana dahil değildir.
Kayıtlı taramalar yalnızca hesap sahibine açıktır. Liste 20 kayıt/sayfadır; coin
geçmişi son 100 taramayı inceler. Evren dışı ve veri eksikliği başarısızlık
olarak etiketlenmez. Başarı oranı veya sonradan oluşan fiyat sonuçları henüz
hesaplanmaz. Mevcut herkese açık aday GET API değişmez. Deployment öncesinde
`alembic upgrade head` gerekir (`f315c2026i01`).


Kayıtlı aday taramasında Aç düğmesi yüklenme durumunu gösterir ve detay
bölümüne kaydırır. Sil düğmesi onay sonrası yalnızca hesap sahibinin
seçtiği taramayı kalıcı siler; o taramanın coin geçmişindeki gözlemi de
kalkar. Yeni taramalar ve portföy kayıtları etkilenmez. DELETE isteği
giriş ve CSRF gerektirir. Migration gerekmez.


### Kayıtlı aday sonuçları

Yeni taramaların hesaplama tamamlanma zamanı kaydedilir. Varsayımsal giriş
bu zamandan en az 15 dakika sonraki ilk 15m mum açılışıdır (15–30 dakika
tampon); 09:00 portföy programından bağımsızdır. Önceden kaydedilmiş ve
tamamlanma zamanı olmayan taramalar ileriye dönük teste alınmaz.
1/2/4/24 saatlik tam, ardışık ve kapanmış 15m mumlar kullanılır. Her yönde
10 bps komisyon ve 5 bps kayma uygulanır. GET salt okunurdur; Sonuçları
hesapla düğmesinin CSRF korumalı POST isteği tamamlanan sonuçları değişmez
olarak saklar. Bekleyen/eksik veriler ortalamalara katılmaz ve yeniden
denenebilir. Sonuçlar bu taramanın aday grubuna aittir; taramalar arasında
tekrarlanan adaylar bağımsız işlem veya portföy getirisi sayılmaz. Aynı
adaylarla karşılaştırma tüm dört süre tamamlanan alt örneklemi kullanır.
Tarama silindiğinde bağlı sonuçları silinir. Migration `0415c2026j01` gerekli.

### Otomatik aday sonuç takibi

Worker servisinde `CANDIDATE_OUTCOMES_ENABLED=true` ile etkinleştirilir;
varsayılan kapalıdır. Her piyasa toplama turundan sonra en fazla 10 uygun
tarama incelenir. Yoğun arşivlerde 15 dakikalık zaman dilimine göre dönen
partiler kullanılır; sonuçların görünmesi birden fazla worker turu sürebilir.
Yalnızca aktif hesapların yeni, en az 1 saatlik penceresi kapanmış ve
bütün sonuçları henüz saklanmamış taramaları seçilir. Eksik/geçersiz mumlar
sonraki turlarda yeniden denenir; worker geçmiş veri indirmez.
Tamamlanan sonuçlar değişmez. Eski giriş zamanı olmayan taramalar dışlanır.
Dashboard yenilemesi saklanan sonuçları gösterir; manuel hesaplama da kullanılabilir.
Migration gerekmez; mevcut 0415c2026j01 şeması kullanılır.

### Taramalar arası aday değerlendirmesi

Adaylar sekmesindeki değerlendirme son 7/30 günün hesaba özel taramalarını
birleştirir. Kural sürümleri ayrı tutulur. Her kural/coin için giriş zamanına
göre en erken kayıt seçilir; sonraki 24 saatteki tekrarlar dışlanır.
Tam 24 saat sınırındaki giriş dahil edilir. Bu seçim formasyon, getiri veya
sonucun tamamlanmasına göre değişmez. Sadece dört süresi de saklanmış adaylarla
1/2/4/24 saat karşılaştırması yapılır; tamamlanmamış sayısı ayrıca gösterilir.
GET /account/candidate-scans/study?days=7 hiçbir sonuç üretmez veya kaydetmez.
Sonuçlar gerçekleşmiş portföy getirisi veya gelecek başarı olasılığı değildir.
Seçilen dönemde 1000 taramadan fazlası varsa kısmi özet yerine açık hata verilir.
Migration gerekmez. Kayıt silme değerlendirme örneklemini değiştirebilir.

### Aday durum değişimi çizelgesi

Coin tarama geçmişiyle birlikte, son 100 kayıtlı taramadaki adaylığa giriş,
adaylığın korunması ve aday koşullarının ilk gözlenen kaybı gösterilir.
Veri hazır değilken aday kaybı üretilmez. Kural sürümü değişince karşılaştırma
yeniden başlar. Tarama aralıkları içindeki kesin kayıp zamanı bilinmez.
Kapanış fiyatı eski taramada saklanmadığından, taramanın veri kapanışına
tam eşleşen mevcut tarihsel 15m mumdan okunur; bulunamazsa boş gösterilir.
Bu fiyat değişmez tarama kaydının parçası değildir, sonradan veri düzeltmeleriyle
değişebilir. Çizelge satış emri veya sonuç değerlendirmesinde çıkış kuralı değildir.
Migration gerekmez.

### Otomatik 15m aday gözlemleri

1515c2026k01 migration sonrasında workerda CANDIDATE_OBSERVATIONS_ENABLED=true
ile etkinleştirilir (varsayılan kapalı). Yeni taramaların adayları varsayımsal
girişten itibaren 24 saat izlenir. Başarılı veri toplama sonrası yalnızca en son
kapanması beklenen 15m pencere gözlenir; geçmiş pencereler doldurulmaz.
Aynı tarama/coin/mumda ilk gözlem saklanır; tekrar çalıştırma değiştirmez.
Aktif hesaplar işlenir. Eski giriş zamanı olmayan taramalar dışlanır.
Analiz kaynağı taramanın kural hashinden farklıysa aday durumu değerlendirilmez.
Veri eksikliği aday kaybı değildir. Kapanış fiyatı gözlemde saklanır.
Kayıtlı tarama açıldığında son 200 gözlem gösterilir; toplam sayı ayrıca belirtilir.
Taramayı silmek gözlemlerini de siler. Gözlemler çıkış emri değildir; worker
gecikmesi ve eksik veri nedeniyle kesin değişim anını göstermez.

### Dashboard çalışma alanları

Piyasa analizi herkese açık coin/formasyon analizi, genel sonuçlar ve ortak
raporları kapsar. Özel takip tek giriş alanının altında portföy, aday takibi
ve alış kayıtlarına ayrılır. Adaylarda tarama/kayıtlar, sonuç/değerlendirme,
coin geçmişi alt sekmeleri vardır. Login eski tab bağlantılarını korur;
giriş yokken özel içerik gizlenir. Portföy listesi kısa özettir; yorumlar
coin ayrıntısındadır. Uzun geçmişler ve ikincil formlar açılır bölümlerdedir.
Tablolar sabit başlık/coin sütunu ve tam sürümü tıklayarak açılan kısa hash
gösterimi kullanır. Hesaplama ve kayıt kuralları değişmez; migration gerekmez.


### Dashboard okunabilirlik düzenlemesi

Oturum açıkken hesap alanı küçük bir durum satırıdır. Coin detayındaki vade yorumları kısa, orta ve uzun vade kartlarında okunur. Veri açıklamaları Nasıl okunur bölümündedir. Adaylarda seçilen taramanın sonuçları önce, taramalar arası değerlendirme ayrı açılır bölümde gösterilir. Hesaplanamayan sonuçların nedeni açılabilir; boş tablolar yerine açıklama görünür. Teknik fiyatlarda sekiz anlamlı basamak gösterilir; tam değer odaklanabilir değerin açıklamasında korunur. Hesaplamalar ve kayıt kuralları değişmez.


### Kullanıcı yönetimi ve teknik yön göstergeleri

Özel takip alanında Kullanıcı yönetimi yalnızca `bilalozok` oturumuna görünür. GET/POST `/account/admin/users` sunucuda da bu aktif hesabı zorunlu tutar; hesap oluşturma Origin ve CSRF kontrollerinden geçer. Yeni kullanıcı adları küçük harfe dönüştürülür, tekrar eden adlar reddedilir, parolalar 15–128 karakterdir ve mevcut scrypt yöntemiyle saklanır. Liste parolaları veya parola özetlerini içermez. Yönetici yetkisi başka kullanıcılara verilemez; yeni hesaplar normal özel hesaplardır. Migration gerekmez.

Portföy ve kayıtlı adayların vade göstergeleri yeşil Alımı değerlendir, kırmızı Satışı / riski değerlendir, sarı Teyit bekle / Çelişkili ve gri Veri hazır değil etiketleridir. Renkler mevcut hazır vadenin assessment alanından türetilir; oluşan veya geçersizleşmiş yapı işlem teyidi sayılmaz. Portföy genel yönü eksik veri varsa değerlendirilmez; zıt teyit varsa sarıdır. Kayıtlı adaylarda etiket tarama anındaki kaydedilmiş duruma aittir. Fiyat getirisi rengi teknik yön önerisi yerine geçmez.


### Worker kısmi başarı ve hata logları

Bir turda başarılı coinler varsa ve yalnızca tekil Binance veri alma hataları oluşmuşsa worker `outcome=partial` ve exit 0 ile tamamlanır. Hatalı pariteler silinmez; last_attempt_ms/last_error kaydı korunur ve sonraki planlı turda yeniden denenir. Başarısız mumlar yazılmaz ve geçerli veri sayılmaz. Tüm coinlerin başarısız olduğu, erişim/rate limit nedeniyle durdurulan veya veritabanı/takip işlemi hatası olan turlar exit 1 ile kapanır. Özet attempted/success/failed/deferred yanında outcome ve hard_failures içerir. Hata logları sabit reason etiketi, varsa HTTP durum kodu ve uygulama hata durum kodunu içerir; ham yanıt, URL veya kimlik bilgisi yazılmaz. Migration gerekli değildir.


### Yönetici parola işlemleri

Yeni kullanıcı ve parola değiştirme alanlarında Göster/Gizle yalnızca o anda girilen metnin görünürlüğünü değiştirir. Mevcut parolalar okunamaz; liste veya API yanıtları parolayı/özetini döndürmez. bilalozok POST `/account/admin/users/password` ile mevcut bir hesabın parolasını değiştirebilir. Origin, CSRF ve yönetici yetkisi zorunludur. Yeni scrypt özeti ve o kullanıcının bütün oturumlarının iptali aynı veritabanı işleminde kaydedilir. Olmayan kullanıcı için hesap oluşturulmaz; aktiflik ve alış kayıtları korunur. Yönetici kendi parolasını değiştirirse tekrar giriş yapması gerekir. İşlem sonrası parola alanları temizlenir ve maskelenir. Migration gerekmez.


### Aday takip durumu

Seçilen taramada GET `/account/candidate-scans/{scan_id}/tracking` yalnızca sahibi için saklanmış son gözlemleri ve tamamlanan sonuçları özetler. Eski kayıt, adaysız tarama, giriş bekleniyor, ilk kapanış bekleniyor, açık/bitmiş 24 saatlik pencere ayrılır. Her coin için son gözlenen aday durumu, kapanış ve gözlem zamanı gösterilir. Hazır olmayan veya farklı kural sürümlü kayıt aday kaybı sayılmaz. Dört horizonun saklanmış tamamlanan, süre bekleyen ve süresi dolmuş fakat henüz saklanmamış sayıları ayrı tutulur. Önizlemede hesaplanan ama saklanmayan sonuç tamamlanmış saklı kayıt sayılmaz. Worker etkinliği bu ekranla kesin doğrulanamaz; son beklenen kapanışta kayıt yokluğu belirtilir, geçmiş gözlemler üretilmez. Yenileme salt okunurdur; migration gerekmez.

### Adaylarda geçmiş ve güncel görünüm

Kayıtlı aday rozetleri tarama anını belirtir. Takip özeti ilk gözlem, son uygun durum değişimi, son kapanış ve gözlem sayısını gösterir; veri/kural uyumsuzluğu değişim sayılmaz ve karşılaştırmayı keser. Ayrıntılı otomatik gözlemler açılır bölümde tutulur.

Seçilen taramanın aday coininde 4h/1d hazırlama işlemi oturum, sahiplik ve CSRF kontrolüyle mevcut sınırlı veri yenileyiciyi kullanır. Yeni teknik değerlendirme ayrı gösterilir; tarama ve sonuç kayıtları değişmez. Bu işlem yalnızca seçilen coini yeniler; otomatik sürekli takip başlatmaz. Migration gerekmez.

Adayın güncel vade değerlendirmesinde izlenecek yapı ve takip adımı gösterilir. Ayrı USDT seviye tablosu yalnızca hazır vadelerin mevcut aktif/oluşan koşullarını kullanır; eski/geçersiz teyitleri aktif seviye olarak sunmaz. Analiz kuralları ve arşivler değiştirilmez.

### Otomatik aday vade verileri

Worker için `CANDIDATE_TIMEFRAMES_ENABLED=true` etkinleştirilince aktif kullanıcıların varsayımsal girişten itibaren 24 saatlik penceresi açık kayıtlı adaylarının 4h/1d verileri yenilenir. Varsayılan kapalıdır. Eski, giriş zamanı gelmemiş, penceresi bitmiş ve aktif olmayan pariteler alınmaz. Portföy ve aday listeleri birleştirilir; aynı coin bir turda bir kez yenilenir. Mevcut önbellek, dakika başına rezervasyon ve worker bütçesi korunur; bütçe nedeniyle ertelenen coin sonraki turda denenir. Kayıtlı taramalar/sonuçlar değiştirilmez. Yeni tarama, hazırlanmış güncel verileri kullanabilir; bütün piyasanın üç vade verilerinin hazır olması garanti edilmez.

Loglar: `candidate_timeframes_eligible symbols=N`, başarıda `candidate_timeframes_complete symbol=...`; hata mevcut `portfolio_timeframes_failed` kaydındadır. Migration gerekmez.

Aday takip durumunda dört coin özeti gösterilir: son gözlemde aday, koşulları yok, yeniden adaylık gözlendi, değerlendirilemedi/gözlem yok. Yeniden adaylık yalnızca uygun gözlemlerde false→true değişiminden çıkarılır; veri/kural uyumsuzluğu karşılaştırmayı ve yeniden adaylık durumunu sıfırlar. Sonraki uygun aday gözlemleri bu yeniden adaylık durumunu korur; koşullar kaybolunca durum değişir. Kartlar anlık al/sat önerisi değildir ve sonuç saklanma sayılarından ayrıdır.

Aday durum kartları tabloyu filtreler; aynı karta tekrar basmak veya Tüm adaylar düğmesi filtreyi kaldırır. Filtre aynı taramanın yenilenmesinde korunur, başka tarama açınca ve çıkışta sıfırlanır. Kartlar/sonuç sayıları tüm seçilen taramayı temsil eder; filtre yalnızca coin satırlarını daraltır.

Kayıtlı aday taraması listesi coin (yalnızca saklanan adaylar), Türkiye tarih/saat aralığı ve takip türüne göre veritabanında filtrelenir; filtreler sayfalama öncesinde uygulanır. Tarih oluşturulma zamanı içindir, başlangıç dahil/bitiş hariçtir. Kayıt sahipliği korunur. Takip başlangıcı olan/açık/bitmiş/eski sınıfları worker sağlık veya sonuç tamamlama göstergesi değildir. Arşiv değişmez; migration gerekmez.

Kayıtlı taramada 4h/1d hazırlama düğmesi coin adının hemen altında, sabit coin sütununda gösterilir. İzlenecek adım açıklaması açılır hücrede tutulur ve genişliği sınırlandırılır; uzun metin işlem düğmesini sağ kenara itmez.

Genel ileriye dönük sonuçlarda formasyon adına basmak seçilen formasyon/süre için özetin dönemi, kural sürümü ve gözlem zamanına uyan geçmiş coin sinyallerini sayfalı olarak açar. Sonuçlar son saklanan haliyle okunur; özetten sonra tamamlanmış olabilir. Güncel formasyon eşleşmesi değildir. Coin düğmesi mevcut güncel grafik akışını kullanır. Sinyaller tekil coinlerden farklı sayılabilir; bekleyen/geçersiz sonuçlar sıfır sayılmaz. Migration gerekmez.

### Tek mumluk erken oluşum uyarıları

Formasyon taramasındaki erken uyarı alanı mevcut değişmez formasyon geçmişini okur. Yapı `forming` durumunda ve `structure_available_at` gözlenen 15m kapanışına eşitse gösterilir. Güncel durum aynı yapıyı korumalıdır; eski, gelecekteki, geç kaydedilmiş ve geçersizleşmiş yapılar gösterilmez. Sonraki kapanışta uyarı sona erer. Yükseliş ve düşüş kapsanır. Otomatik ekran kontrolü isteğe bağlıdır ve görünür ekranda 30 saniyede bir çalışır; dış bildirim gönderilmez. Worker formasyon geçmişini toplamalıdır. Yeni migration veya worker değişkeni gerekmez. Üç sağ mumluk pivot kuralı korunur; hareketin başlangıcından önce bildirim veya 15 dakika içinde teslim garantisi verilmez.

### Kişisel özet dashboard

Özel takipte varsayılan Özet sekmesi giriş gerektirir. Kullanıcının alış coinlerini, son kayıtlı taramasını ve bu coinlere ait tek mumluk yükseliş/düşüş oluşumlarını gösterir. Portföy matrisi 10 coinlik sayfalara ayrılır; dikkat listesi gösterilen sayfaya aittir. Aday ve sonuç sayıları yalnızca son kayıtlı taramaya aittir. Erken uyarılar bütün alış coinleri ve son taramanın adaylarıyla eşleştirilir, görünür ekranda 30 saniyede bir hafif okuma ile güncellenir ve süresi dolunca kaldırılır. Piyasa verisi indirilmez, kayıt oluşturulmaz. Migration veya yeni worker ayarı gerekmez.

### Özet ekranında USDT alış getirileri

Komisyon dahil maliyet, son beklenen kapanmış 15m fiyatıyla varsayımsal değer ve gerçekleşmemiş kâr/zarar gösterilir. Satışlar düşülmez. Son 24 saat ve 7 gün farkı, bugünkü kâr/zarardan ilgili kapanıştaki kâr/zararı çıkarır; yeni alışların fiyat ve komisyonu dahil edilir. Grafik son sekiz günlük eş saatli kapanışı kullanır, o noktaya kadar alınmış coinleri değerlendirir. Eksik veya geçersiz kapanışlar doldurulmaz; eksik bileşen varsa toplam da boş kalır. TL alışları hariç tutulur. Veritabanı okunur, dış veri indirilmez; migration gerekmez.

### Uygulama genelinde giriş zorunluluğu

Ana adres oturumsuz kullanıcıyı `/login` ekranına yönlendirir; giriş sonrası kişisel Özet açılır. Tüm analiz, grafik, indirme, API ve dokümantasyon uçları aktif hesap/oturum gerektirir. Yalnızca giriş ekranı, giriş POST'u ve veri içermeyen `/health` kontrolü açıktır. Yazma işlemleri oturum yanında CSRF kontrolü gerektirir. Bütün HTTP yanıtları no-store döner. Mevcut HTTPS güvenli oturum çerezi, hesaplar ve parola sıfırlamada oturum iptali korunur. Yeni hesaplar bilalozok yönetimiyle oluşturulur, genel kayıt yoktur. Worker doğrudan veritabanından çalışır; yeni ayar ve migration gerekmez.

Kullanıcı oluşturma, listeleme, parola sıfırlama ve kalıcı silme yalnızca `bilalozok` hesabına açıktır. Silme kullanıcı adı yazılarak onaylanır; hesabın oturumları ve kişisel alış, tarama, sonuç ve gözlem kayıtları tek işlemde silinir. Ortak piyasa verileri korunur. `bilalozok` silinemez.

Kullanıcı yönetimi üst gezintide yalnızca bilalozok hesabının gördüğü Yönetim sekmesindedir. Özel takip içindeki yinelenen oturum/çıkış kutusu gösterilmez; çıkış üst panelden yapılır. Yönetim sekmesi klavyeyle gezilebilir; yetkisiz hesaplar admin adresinde kişisel Özete yönlendirilir. Sunucu yetki kontrolleri korunur.

Özette erken oluşumlar için Kişisel coinler/Piyasa geneli kapsamı seçilebilir. Kartlar ve liste aynı kapsama aittir; kapsam değişimi eski istek sonuçlarının yeni listeye yazılmasını engeller. Piyasa geneli mevcut formasyon geçmişini okur, yeni tarama veya Binance isteği başlatmaz. Giriş koruması ve tek mumluk süre korunur.

Dashboard kompakt analiz panelleri kullanır: özet grafikleri ana alanda, erken uyarılar ve takip sağ panelde, coin tabloları tam genişlikte gösterilir. Dar ekranlarda paneller alt alta geçer; mevcut veri ve işlemler korunur.

Özet ekranında takip sayaçları kompakt satırlarda, kâr/zarar değerleri yön rengiyle gösterilir. Coin raporu açılabilir takip açıklamaları içeren formasyon kartlarını kullanır; özetin uzun okuma notları açılır alandadır.

Özet tabloları grafiklerin bulunduğu sütunda kesintisiz devam eder; sağdaki takip panelinin yüksekliği tabloları aşağı itmez. Coin grafikleri kayıt sayısına göre doğal yüksekliğini korur.

Özet tek tam genişlikli akış kullanır: grafikler, yan yana uyarı/takip panelleri ve tablolar. Farklı sütun yüksekliklerinin oluşturduğu uzun boşluklar önlenir; dar ekranlarda paneller alt alta geçer.

Özetin erken oluşum listesi geniş sol panelde gösterilir. Sağda son tarama takibi ve altında dikkat isteyen vadeler yer alır; dar ekranlarda paneller alt alta geçer.

Coin analizindeki teknik gösterge katmanı RSI14 (Wilder), MACD12/26/9 (SMA ile başlatılan EMA), SMA50/EMA50 ve Bollinger20 ±2 nüfus standart sapmasını kapanmış 15m/4h/1d verilerden hesaplar. Güncel, kesintisiz 200 mum şarttır; son 100 nokta gösterilir. Aday puanları ve erken uyarı kuralları değişmez. 50 günlük ortalama yalnızca günlük vadededir. Gösterge sonuçları bu aşamada arşivlenmez; Fibonacci ve birleşik performans testi sonraki aşamalardır.

Fibonacci açıklayıcı katmanı son 200 kapanmış mumdaki üç sağ mumla doğrulanmış son karşıt dip/tepe çiftini kullanır. İki yön için %23,6/38,2/50/61,8/78,6 düzeltmeleri gösterilir (%50 aralık orta noktasıdır). Belirsiz çiftlerde seviye üretilmez; pivot ve doğrulanma zamanları görünür. Yeni çift seviyeleri değiştirebilir; geçmiş aday kayıtları ve puanlar değişmez.

Formasyon–gösterge uyum özeti aynı vadenin aynı 200 kapanmış mumundan üretilir. Oluşan yönlü yapılar ve güncel teyitler için trend (EMA50 konum/eğim), tek momentum grubu (RSI50/MACD histogramı) ve teyit hacmi destek/çelişki/nötr olarak açıklanır. Bollinger ve Fibonacci yön oyuna katılmaz; puan veya olasılık üretilmez. Henüz arşiv kaydı yapılmaz.

Yeni aday taramaları adayın 15m gösterge değerlerini, önceki mumunu, Fibonacci referanslarını ve ana formasyon uyum koşullarını tarama anında JSON içinde saklar. Mevcut toplu okunmuş 200 mum kullanılır; ek worker veya migration gerekmez. Eski kayıtlar doldurulmaz. Aday değerlendirmesinde aynı kural/gösterge sürümü ve formasyon içindeki trend/momentum alt grupları dört tamamlanmış sonuçla karşılaştırılır; mevcut sonuçtan bağımsız 24 saat çakışma elemesi korunur. Fark nedensel katkı veya olasılık değildir.

Aday taramasının kapsam özeti güncel veriyle değerlendirilen coin sayısını, aday sayısını ve eleme/veri sorunu nedenlerini ayrı gösterir. Sıfır aday taramanın başarısız olduğu anlamına gelmez; güncel veriyle hiç değerlendirilemeyen tarama ayrıca belirtilir. Tanı tablosu seçilen değişmez tarama kaydından okunur.

Kayıtlı aday taramalarını yenile ve daha fazla tarama düğmeleri kayıt listesini yükler; yükleme durumu ekranda gösterilir.

Gösterge sonuç karşılaştırması formasyon/kural/gösterge sürümü ve süre seçimiyle kartlarda ve sıfır merkezli grafiklerde gösterilir. Örtüşen alt gruplar toplanmaz; eksik sonuçlar grafikte sıfır sayılmaz.

Erken oluşum satırlarından aynı 15m kapanışının trend ve momentum bağlamı isteğe bağlı açılır. Gösterge raporu uyarı kapanışı/formasyon/yönüyle eşleşmezse destek yorumu gösterilmez. Aynı coin/kapanış cevabı uyarı süresince tekrar kullanılır; ek Binance çağrısı veya worker sıklığı değişikliği yapılmaz.

Erken uyarılarda süre mevcut hücrede güncellenir; aynı liste yeniden çizilmez. Veri/gösterge değişince yatay kaydırma konumu korunur. Gösterge bağlamı coin adının yanındadır.

### Erken oluşum sonuç ölçümü

`alembic upgrade head` ile `0910e2026l01` migration uygulanır. Sonra candle worker için
`EARLY_FORMATION_STUDY_ENABLED=true` etkinleştirilir (varsayılan kapalı). Mevcut 15m mumlar
kullanılır; Binance çağrısı eklenmez. Sadece hâlen tek mumluk pencerede olan yeni oluşumlar
ve aynı kapanışın gösterge bağlamı saklanır. Tekrarlar aynı anahtarla çoğaltılmaz.
1/2/4/24 saat ham fiyat hareketi ölçülür; düşüş beklentisi açığa satış getirisi sayılmaz.
Gösterge sürümü, formasyon ve yön ayrı karşılaştırılır. 24 saat çakışan aynı coin/sürüm
kayıtlarının en erkeni, sonuca bakılmadan tutulur. Eksik veriler sıfır sayılmaz.
Formasyon ekranında “Erken uyarıların ölçülen sonuçları” düğmesi salt okunur özeti gösterir.

Erken uyarı ölçüm ekranında formasyon, beklenen yön ve süre filtreleri bulunur. Kartlar tüm yüklenen dönemi özetler; filtreli son kayıt listesi yalnızca API yanıtındaki son 20 uyarıyı kapsar. Bekleyen sonuçlarda planlanan kapanış Türkiye saatiyle gösterilir; süre bekleme, worker/veri bekleme ve saklanmış geçersiz sonuç birbirinden ayrılır.

Erken uyarı ölçümleri formasyon, yön ve süre filtreleriyle sıfır merkezli grafiklerde gösterilir. Sürümler ayrı panellerde tutulur; dört geçerli sonucu tamamlanmamış kayıtlar sıfır hareket sayılmaz. Grafikler mevcut saklanmış veriyi kullanır.

Erken ölçüm grafiklerinde tamamlanan karşılaştırma örneği yoksa boş çubuklar yerine kısa durum kartı gösterilir. Geçerli sonuçlar yüklendiğinde grafik açılır; ayrıntılı tablo ve filtreler korunur.

Erken ölçüm kayıtlarında uyarı anında saklanan 15m gösterge değerleri ve formasyon uyumu açılabilir. Güncel grafik ayrı düğmeyle açılır; kayıtlar yeniden hesaplanmaz. Bu okuma katmanı ölçüm kural sürümünü değiştirmez.

Erken ölçüm veri sağlığı bölümü aktif coinlerin son kapanmış 15m mum güncelliğini ve seçilen dönemdeki en son 1000 ölçüm kaydının bekleme/geçersizlik durumunu gösterir. Kapanış sonrası dört mum toleransı ayrı sayılır; Binance isteği veya kayıt değişikliği yapılmaz. Son başarılı coin alımı, worker çalışmasının kesin kanıtı değildir.

Geçmiş erken ölçümler coin tam sembolü, formasyon tam adı, yön ve Türkiye tarihleriyle aranabilir. Varsayılan son 30 gün, sayfa boyutu 20 kayıttır. Sayfalama sabit gözlem üst sınırı kullanır; yeni kayıtları görmek için arama yenilenir. Saklanmış bağlamlar okunur, geçmiş veri üretilmez.

Erken ölçüm aramasında coin ve formasyon açılır listeden seçilir. Seçenekler tüm saklanmış ölçümlerden okunur, tekrarsızdır ve yenilenebilir; tüm coinler/formasyonlar seçeneği filtreyi kaldırır.

Uyarılar ve formasyonlar ekranı erken uyarılar, manuel tarama, ölçüm sonuçları, geçmiş arama ve veri sağlığı sekmelerine ayrılır. Açıklamalar ve ayrıntılı tablolar isteğe bağlı açılır; erken uyarı otomatik okuması yalnızca ilgili sekme görünürken çalışır.

Tüm ana çalışma ekranları amaca göre alt sekmelere ayrılır: coin grafik/gösterge/geçmiş, piyasa sonuçları, rapor listesi/ayrıntısı, kişisel özet, portföy listesi/ayrıntısı, aday tarama/takip, alış değerleme/kayıt ekleme ve yönetim. Bir coin veya rapor seçildiğinde ilgili ayrıntı sekmesi açılır; filtreler ve kayıtlar korunur.
