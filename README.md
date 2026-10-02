# Crypto Trend MVP

[![CI](https://github.com/bilalozok/crypto-trend-mvp/actions/workflows/ci.yml/badge.svg)](https://github.com/bilalozok/crypto-trend-mvp/actions/workflows/ci.yml)

Basit bir **FastAPI + SQLite** tabanlı kripto mum verisi (candles) servisi.
Amaç: veri çekme, saklama, son kayıtları listeleme ve test/lint/CI disiplinini oturtmak.

---

## Özellikler

- FastAPI REST API
- SQLAlchemy ile SQLite persistency
- Candle modelinde `UniqueConstraint` ile mükerrer kayıt koruması
- Fetch işlemlerinde idempotent davranış (`skipped_existing`)
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
- `GET /candles/latest_raw`

Örnek:

```bash
curl "http://127.0.0.1:8010/candles/latest?symbol=BTCUSDT&limit=20&offset=0&sort=desc"
```

### DB Debug

- `GET /debug/db`

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
