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
