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
